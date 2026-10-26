"""Voice turn viewpoint/default-angle tests; no aircraft or cloud connection."""
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'modules/ai'))
from demo_text import Demo
from drone_adapter import MockDrone
from program_runner import Runner


class VoiceTurns(unittest.TestCase):
    def runner(self, frame='facing_pilot', following=False):
        runner = Runner(Demo(MockDrone(), lateral_frame=frame))
        self.assertTrue(runner.run('takeoff()')['ok'])
        if following:
            self.assertTrue(runner.run('start_follow()')['ok'])
        return runner

    def test_default_ninety_for_left_and_right_in_both_views(self):
        for frame, sign in (('facing_pilot', -1), ('aircraft', 1)):
            for name, angle in (('turn_left', -90), ('turn_right', 90)):
                for following in (False, True):
                    with self.subTest(frame=frame, name=name, following=following):
                        runner = self.runner(frame, following)
                        drone = runner.demo.drone
                        with patch.object(drone, 'rotate', wraps=drone.rotate) as rotate:
                            result = runner.run('drone.%s()' % name)
                        rotate.assert_called_once_with('model', sign * angle)
                        self.assertTrue(result['ok'], result)
                        self.assertIn('90', result['result'])
                        self.assertFalse(drone.following)
                        self.assertIsNone(drone.owner)

    def test_named_angles_and_original_signed_rotate_are_preserved(self):
        for angle in (1, 30, 45, 90, 180):
            for name, logical in (('turn_left', -angle), ('turn_right', angle),
                                  ('rotate', -angle), ('rotate', angle)):
                with self.subTest(angle=angle, name=name, logical=logical):
                    runner = self.runner()
                    param = logical if name == 'rotate' else angle
                    drone = runner.demo.drone
                    with patch.object(drone, 'rotate', wraps=drone.rotate) as rotate:
                        result = runner.run('%s(%d)' % (name, param))
                    rotate.assert_called_once_with('model', -logical)
                    self.assertTrue(result['ok'], result)
                    requested = '左转' if logical < 0 else '右转'
                    opposite = '右转' if logical < 0 else '左转'
                    self.assertIn(requested, result['result'])
                    self.assertNotIn(opposite, result['result'])
                    self.assertIn(str(angle), result['result'])

    def test_fixed_text_accepts_default_and_explicit_turns(self):
        for text, expected in (('向左转', 90), ('向右转', -90),
                               ('左转', 90), ('右转', -90),
                               ('往左转', 90), ('往右转', -90),
                               ('向左转45度', 45), ('向右转 30 度', -30),
                               ('左转180度', 180), ('右转1度', -1)):
            with self.subTest(text=text):
                runner = self.runner()
                drone = runner.demo.drone
                with patch.object(drone, 'rotate', wraps=drone.rotate) as rotate:
                    runner.demo.handle(text)
                rotate.assert_called_once_with('model', expected)
                self.assertTrue(runner.demo.last_result['ok'])

    def test_invalid_angles_never_fall_back_to_default_or_move(self):
        runner = self.runner()
        with patch.object(runner.demo.drone, 'rotate') as rotate:
            for name in ('turn_left', 'turn_right'):
                for argument in ('0', '-1', '181', 'True', 'None', '"45"', '3.2',
                                 '20+10', 'x', 'get_status()', '30,45'):
                    code = 'drone.%s(%s)' % (name, argument)
                    self.assertFalse(runner.run(code)['accepted'], code)
            for text in ('向左转0度', '向右转181度', '向左转-20度'):
                runner.demo.handle(text)
                self.assertFalse(runner.demo.last_result['ok'], text)
        rotate.assert_not_called()

    def test_failed_turn_keeps_follow_stopped_and_reports_failure(self):
        runner = self.runner(following=True)
        runner.demo.drone.fail_next_move = True
        result = runner.run('turn_left()')
        self.assertFalse(result['ok'], result)
        self.assertNotIn('完成', result['result'])
        self.assertFalse(runner.demo.drone.following)
        self.assertIsNone(runner.demo.drone.owner)

    def test_cancelled_turn_does_not_execute_new_alias(self):
        runner = self.runner()
        with patch.object(runner.demo.drone, 'rotate') as rotate:
            result = runner.run_guarded('turn_right()', lambda: True)
        self.assertFalse(result['ok'])
        rotate.assert_not_called()


class ActualSdkRoute(unittest.TestCase):
    def test_voice_maps_once_and_direct_sdk_rotation_keeps_original_sign(self):
        from drone_sdk import controller as ctl, _driver as drv
        from drone_sdk.ai_bridge import UnifiedDrone
        self.assertTrue(drv.SIMULATED)
        def cleanup():
            ctl.stop_follow()
            for worker in ctl._follow._retired_workers:
                worker.join(1)
            ctl.land('model', priority=True)
            time.sleep(.06)
            ctl.get_status()
            ctl.release_control('model')
        cleanup()
        self.addCleanup(cleanup)
        drone = UnifiedDrone()
        runner = Runner(Demo(drone, lateral_frame='facing_pilot'), allow_simulated_sdk=True)
        with patch('socket.socket', side_effect=AssertionError('No network')), \
             patch.object(drone, 'prepare_follow_preview'):
            self.assertTrue(runner.run('takeoff()')['ok'])
            for code, expected, wording in (('turn_left()', 90, '左转 90'),
                ('turn_right()', -90, '右转 90'), ('turn_left(45)', 45, '左转 45'),
                ('turn_right(30)', -30, '右转 30'), ('rotate(-45)', 45, '左转 45')):
                with patch.object(drv, 'execute', wraps=drv.execute) as execute:
                    result = runner.run(code)
                execute.assert_called_once_with('rotate', expected)
                self.assertTrue(result['ok'], result)
                self.assertIn(wording, result['result'])
                self.assertIn('面对机头', result['result'])
                self.assertEqual(ctl.get_status().control_owner, 'none')
            self.assertTrue(drone.request_control('model')['ok'])
            with patch.object(drv, 'execute', wraps=drv.execute) as execute:
                self.assertTrue(drone.rotate('model', -45)['ok'])
            execute.assert_called_once_with('rotate', -45)
            self.assertTrue(drone.release_control('model')['ok'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
