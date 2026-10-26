"""Hover -> explicit follow, through the real SDK with simulated transport.

No aircraft, microphone or external network is used by these tests.
"""
import asyncio
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'modules/ai'), str(ROOT / 'modules/follow')]
from drone_sdk import controller as ctl, _driver as drv
from drone_sdk.ai_bridge import UnifiedDrone
from drone_sdk.contract import FrameResult
from r3_follow.vision import synthetic_frame
from demo_text import Demo
from program_runner import Runner
from volc_dialog import VolcDuplex
from backends import SDKBackend
from gateway import Gateway


class HoverResume(unittest.TestCase):
    def setUp(self):
        self.assertTrue(drv.SIMULATED)
        # Windows asyncio needs a local socketpair for its wakeup pipe. Build
        # that first; application network operations are forbidden during tests.
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.close_loop)
        self.guard = patch.multiple('socket.socket', **{
            name: Mock(side_effect=AssertionError('No network'))
            for name in ('connect', 'connect_ex', 'sendto', 'bind')})
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.env = patch.dict(os.environ, {'R3_FOLLOW_CONFIG': str(
            ROOT / 'modules/follow/configs/aircraft_A_ID0_motion_follow.json')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.reset()
        self.addCleanup(self.reset)
        self.seq = 0
        self.image = synthetic_frame(x=.5, y=.5, size=.20)
        self.visible = True
        ctl.set_frame_provider(self.frame)
        self.drone = UnifiedDrone()
        self.runner = Runner(Demo(self.drone), allow_simulated_sdk=True)
        self.assertTrue(self.runner.run('drone.takeoff()')['ok'])

    def close_loop(self):
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.close()

    def reset(self):
        drv.set_simulated_delay(0)
        ctl.stop_follow()
        for worker in ctl._follow._retired_workers:
            worker.join(1)
        ctl.set_frame_provider(None)
        ctl.land('model', priority=True)
        time.sleep(.06)
        ctl.get_status()
        ctl.release_control('model')

    def frame(self):
        self.seq += 1
        image = self.image if self.visible else self.image * 0 + 255
        return FrameResult(image, self.seq, time.time(), False)

    def assert_hover(self):
        self.assertEqual(ctl.get_status().flight_state, 'flying')
        self.assertEqual(ctl.get_status().control_owner, 'none')
        self.assertFalse(ctl.is_following())
        self.assertEqual(drv.snapshot()['velocity'], (0, 0, 0, 0))

    def start_follow(self):
        result = self.runner.run('drone.start_follow()')
        self.assertTrue(result['ok'], result)
        self.assertTrue(ctl.is_following())
        self.assertEqual(ctl.get_status().control_owner, 'follow')

    def test_voice_and_hotkey_hover_can_restart_follow_repeatedly_in_air(self):
        dialog = VolcDuplex(self.runner, audio=None, instructions='', api_key='test-key')
        with patch.object(drv, 'execute', wraps=drv.execute) as execute:
            # First stop before follow reproduces the 23:01 log. Later stops
            # interrupt active following, through the same hotkey/tool route.
            for cycle in range(4):
                stopped = self.loop.run_until_complete(dialog.emergency(False))
                self.assertTrue(stopped['ok'], stopped)
                self.assertIn('尚未确认', stopped['message'])
                self.assert_hover()
                time.sleep(.08)
                self.assert_hover()  # Seeing the marker alone must not resume.
                result = self.loop.run_until_complete(dialog._dispatch(
                    'run_program', {'code': 'drone.start_follow()'}))
                self.assertTrue(result['ok'], (cycle, result))
                self.assertTrue(ctl.is_following())
            execute.assert_not_called()  # No land/takeoff or discrete moves.

    def test_sdk_backend_interrupt_also_allows_explicit_follow_restart(self):
        self.start_follow()
        backend = SDKBackend(ROOT)
        self.assertTrue(backend.emergency(False)['ok'])
        self.assert_hover()
        self.assertTrue(backend.start_follow()['ok'])
        self.assertTrue(ctl.is_following())

    def test_normal_program_hover_still_releases_via_runner(self):
        self.start_follow()
        result = self.runner.run('drone.stop_motion()')
        self.assertTrue(result['ok'], result)
        self.assert_hover()
        self.start_follow()

    def test_gateway_finish_after_hover_does_not_clean_up_new_follow(self):
        gateway = Gateway(SDKBackend(ROOT))
        async def run():
            gateway.begin()
            await gateway.dispatch('move_up', [20], {})
            self.assertTrue(gateway.acquired)
            result = await gateway.interrupt(False)
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['control_released'])
            self.start_follow()
            grant = ctl.get_status().control_epoch
            await gateway.finish(False)
            self.assertTrue(ctl.is_following())
            self.assertEqual(ctl.get_status().control_owner, 'follow')
            self.assertEqual(ctl.get_status().control_epoch, grant)
        self.loop.run_until_complete(run())

    def test_new_manual_move_after_hover_can_then_return_to_follow(self):
        self.assertTrue(self.drone.emergency(False)['ok'])
        result = self.runner.run('drone.move_up(20)')
        self.assertTrue(result['ok'], result)
        self.assertEqual(ctl.get_status().control_owner, 'none')
        self.start_follow()

    def test_pre_stop_grant_cannot_move_or_release_a_new_model_grant(self):
        self.assertTrue(self.drone.request_control('model')['ok'])
        self.assertTrue(self.drone.emergency(False)['ok'])
        new = UnifiedDrone()
        self.assertTrue(new.request_control('model')['ok'])
        with patch.object(drv, 'execute', wraps=drv.execute) as execute:
            self.assertFalse(self.drone.move_forward('model', 20)['ok'])
            self.assertFalse(self.drone.release_control('model')['ok'])
            execute.assert_not_called()
        self.assertEqual(ctl.get_status().control_owner, 'model')
        self.assertTrue(new.move_up('model', 20)['ok'])
        self.assertTrue(new.release_control('model')['ok'])

    def test_late_action_and_finally_cannot_stop_or_release_new_owner(self):
        for new_owner in ('model', 'follow'):
            with self.subTest(new_owner=new_owner):
                entered, finish = threading.Event(), threading.Event()
                result = {}
                def delayed(action, param=None):
                    entered.set()
                    self.assertTrue(finish.wait(2))
                    return True
                def old_turn():
                    result.update(self.runner.run('drone.move_forward(20)'))
                with patch.object(drv, 'execute', side_effect=delayed):
                    thread = threading.Thread(target=old_turn)
                    thread.start()
                    try:
                        self.assertTrue(entered.wait(1))
                        self.assertTrue(self.drone.emergency(False)['ok'])
                        grant = ctl.request_control(new_owner)
                        self.assertTrue(grant.ok, grant)
                        self.assertTrue(ctl.update_velocity(new_owner, 0, 6, 0, 0,
                            control_epoch=grant.control_epoch,
                            command_epoch=grant.command_epoch).ok)
                    finally:
                        finish.set()
                        thread.join(2)
                    self.assertFalse(thread.is_alive())
                self.assertFalse(result['ok'], result)
                state = ctl.get_status()
                self.assertEqual(state.control_owner, new_owner)
                self.assertEqual(state.control_epoch, grant.control_epoch)
                self.assertEqual(drv.snapshot()['velocity'], (0, 6, 0, 0))
                self.assertTrue(ctl.release_control(new_owner,
                    control_epoch=grant.control_epoch).ok)

    def test_zero_send_failure_does_not_release_control_as_success(self):
        self.assertTrue(self.drone.request_control('model')['ok'])
        with patch.object(drv, 'set_velocity', return_value=False):
            result = self.drone.emergency(False)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['message'], 'stop_send_failed')
        self.assertEqual(ctl.request_control('follow').reason, 'busy')
        self.assertTrue(self.drone.emergency(False)['ok'])
        self.assert_hover()
        self.start_follow()

    def test_pending_transport_still_blocks_new_follow_grant(self):
        with patch.object(drv, 'action_pending', return_value=True):
            self.assertTrue(self.drone.emergency(False)['ok'])
            blocked = ctl.request_control('follow')
            self.assertFalse(blocked.ok)
            self.assertEqual(blocked.reason, 'action_result_pending')
        self.start_follow()

    def test_hover_during_landing_cannot_cancel_landing_or_start_follow(self):
        self.assertTrue(self.drone.emergency(True)['ok'])
        with patch.object(ctl, '_landing_deadline', time.monotonic() + 10):
            self.assertTrue(self.drone.emergency(False)['ok'])
            self.assertEqual(ctl.get_status().flight_state, 'landing')
            blocked = ctl.request_control('follow')
            self.assertFalse(blocked.ok)
            self.assertEqual(blocked.reason, 'landing_in_progress')

    def test_stop_cancels_waiting_follow_start_but_next_request_can_start(self):
        self.visible = False
        result = {}
        thread = threading.Thread(target=lambda: result.update(self.drone.start_follow()))
        thread.start()
        try:
            deadline = time.monotonic() + 1
            while not ctl._follow._starting:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.005)
            self.assertTrue(self.drone.emergency(False)['ok'])
        finally:
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(result['ok'], result)
        self.visible = True
        self.assert_hover()
        self.start_follow()


if __name__ == '__main__':
    unittest.main(verbosity=2)
