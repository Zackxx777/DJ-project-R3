"""Offline motion/voice/follow handoff regressions; no drone or cloud connection."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT/'modules/ai'), str(ROOT/'modules/follow')]
from program_runner import Runner
from demo_text import Demo
from drone_adapter import MockDrone
from volc_dialog import VolcDuplex
from drone_sdk.telemetry import install_receiver_stamp, read_height, STAMP
from r3_follow.core import Observation, FollowController, State
from r3_follow.mock_port import MockPort
from r3_follow.service import FollowService
from r3_follow.session_log import SessionLog


def observation(seq, stamp, x=320, y=240, size=.25, found=True, reason=''):
    half = size*640/2
    corners = ((x-half,y-half),(x+half,y-half),(x+half,y+half),(x-half,y+half)) if found else ()
    return Observation(seq,stamp,'shared_sdk',640,480,0,found,x,y,size,corners,reason)


class Routes(unittest.TestCase):
    def test_six_routes_preserve_distance_and_handoff(self):
        for name in ('forward','back','left','right','up','down'):
            for follow in (False,True):
                with self.subTest(name=name,follow=follow):
                    r=Runner();r.run('drone.takeoff()')
                    if follow:r.run('drone.start_follow()')
                    method='move_'+name
                    with patch.object(r.demo.drone,method,wraps=getattr(r.demo.drone,method)) as move:
                        result=r.run('drone.%s(20)'%method)
                    move.assert_called_once_with('model',20)
                    self.assertTrue(result['ok']); self.assertEqual(result['status'],'completed')
                    self.assertEqual(r.demo.drone.following,follow)

    def test_failed_move_never_resumes_or_claims_success(self):
        r=Runner();r.run('drone.takeoff()');r.run('drone.start_follow()')
        r.demo.drone.fail_next_move=True
        result=r.run('drone.move_up(20)')
        self.assertTrue(result['accepted']);self.assertFalse(result['ok'])
        self.assertFalse(r.demo.drone.following)

    def test_bare_call_and_invalid_programs(self):
        r=Runner();self.assertTrue(r.run('takeoff()')['ok'])
        self.assertTrue(r.run('move_left(20)')['ok'])
        for code in ('drone.move_up(19)','drone.move_up(True)','drone.move_up(101)',
                     'takeoff();land()','drone.move_left(20);drone.move_up(20)',
                     'x=drone.move_up(20)','move_up(x)'):
            self.assertFalse(r.run(code)['accepted'],code)

    def test_cancelled_turn_never_executes(self):
        r=Runner()
        with patch.object(r.demo,'handle') as handle:
            self.assertFalse(r.run_guarded('drone.takeoff()',lambda:True)['ok'])
        handle.assert_not_called()


class Telemetry(unittest.TestCase):
    def test_stamp_changes_only_on_packet_not_poll(self):
        class Vendor:
            @staticmethod
            def parse_state(data):return {'h':120,'tof':118} if data else {}
        with patch('drone_sdk.telemetry.time.time',return_value=100.):
            install_receiver_stamp(Vendor)
            parser=Vendor.parse_state;install_receiver_stamp(Vendor)
            self.assertIs(parser,Vendor.parse_state)
            packet=Vendor.parse_state('h:120;')
        tello=SimpleNamespace(get_current_state=lambda:packet)
        self.assertEqual(read_height(tello,True,now=100.1),(120,100.,'h'))
        self.assertEqual(read_height(tello,True,now=100.4),(120,100.,'h'))
        self.assertIsNone(read_height(tello,True,now=100.6)[1])
        self.assertEqual(Vendor.parse_state(''),{})

    def test_tof_and_invalid_height_and_future(self):
        packet={STAMP:100.,'h':0,'tof':125}
        drone=SimpleNamespace(get_current_state=lambda:packet)
        self.assertEqual(read_height(drone,True,now=100.1),(125,100.,'tof'))
        for changes in ({'tof':65535},{'tof':True},{STAMP:101.}):
            p=dict(packet,**changes)
            self.assertIsNone(read_height(SimpleNamespace(get_current_state=lambda:p),True,now=100.1)[1])

    def test_controller_exports_real_receipt_not_query_time(self):
        from drone_sdk import controller as ctl, _driver as drv
        h=dict(battery=80,height_cm=120,connected=True,link_ok=True,height_timestamp=time.time()-.1)
        with patch.object(drv,'SIMULATED',False),patch.object(drv,'health',return_value=h):
            result=ctl.get_status()
        self.assertEqual(result.height_timestamp,h['height_timestamp'])
        self.assertFalse(result.simulated)


class Rebase(unittest.TestCase):
    def controller(self):
        c=FollowController(control_profile='openpose_pid',pid_tuning='responsive_v1',
            steering_mode='adaptive',vertical_follow=True,distance_mode='relative_size',
            recovery_window=30,require_geometry=True)
        c.observe(observation(1,10));self.assertTrue(c.calibrate(10)[0]);c.start(10)
        return c

    def test_rebase_updates_only_requested_axis(self):
        for axis in ('horizontal','vertical','depth'):
            c=self.controller();c.pause();c.observe(observation(2,10.1,384,192,.30))
            self.assertTrue(c.rebase_axis(axis,10.1)[0]);c.start(10.1)
            self.assertEqual(c.target_x_fraction,.6 if axis=='horizontal' else .5)
            self.assertEqual(c.target_y_fraction,.4 if axis=='vertical' else .5)
            self.assertEqual(c.reference_size,.30 if axis=='depth' else .25)
            for seq in range(3,10):
                stamp=10+seq*.1;c.observe(observation(seq,stamp,384,192,.30));req=c.tick(stamp)
            if axis=='horizontal':self.assertEqual((req.right,req.yaw),(0.,0.))
            if axis=='vertical':self.assertEqual(req.up,0.)
            if axis=='depth':self.assertEqual(req.forward,0.)

    def test_rebase_rejects_edge_stale_and_active(self):
        c=self.controller();self.assertFalse(c.rebase_axis('horizontal',10)[0])
        c.pause();self.assertFalse(c.rebase_axis('horizontal',11)[0])
        c.observe(observation(2,11,620));self.assertFalse(c.rebase_axis('horizontal',11)[0])

    def test_short_miss_requires_three_new_consistent_frames(self):
        c=self.controller()
        c.observe(observation(2,10.03,found=False,reason='not_found'))
        req=c.tick(10.03);self.assertEqual(req.state,State.RECOVERING)
        self.assertFalse(any((req.right,req.forward,req.up,req.yaw)))
        for seq,stamp in ((3,10.06),(4,10.09)):
            c.observe(observation(seq,stamp));self.assertEqual(c.tick(stamp).state,State.RECOVERING)
        c.observe(observation(5,10.12));self.assertEqual(c.tick(10.12).state,State.TRACKING)
        self.assertFalse(c.distance_guard.blocked)

    def test_long_loss_jump_and_duplicate_do_not_fast_resume(self):
        for reason,first,x in (('not_found',10.3,320),('not_found',10.06,500),
                               ('ambiguous_duplicate_id',10.06,320)):
            c=self.controller();c.observe(observation(2,10.03,found=False,reason=reason));c.tick(10.03)
            for seq in range(3,6):
                stamp=first+(seq-3)*.03;c.observe(observation(seq,stamp,x));req=c.tick(stamp)
            self.assertNotEqual(req.state,State.TRACKING)
            self.assertFalse(any((req.right,req.forward,req.up,req.yaw)))


class HandoffService(unittest.TestCase):
    def setUp(self):
        self.now=10.; self.seq=0
        class Detector:
            def detect(_,frame,frame_id,acquired_at,source):
                return observation(frame_id,acquired_at,*frame)
        self.port=MockPort(lambda:self.now)
        self.service=FollowService(self.port,tracker=Detector(),monotonic=lambda:self.now,
            wall_clock=lambda:1800000000+self.now,log_directory=False,
            control_profile='openpose_pid',steering_mode='adaptive',pid_tuning='responsive_v1',
            vertical_follow=True,distance_mode='relative_size')
        self.addCleanup(self.service.close)
        self.frame();self.assertTrue(self.service.calibrate_reference().ok)
        self.assertTrue(self.service.start_follow().ok)

    def frame(self,x=320,y=240,size=.25):
        self.now+=.1;self.seq+=1
        self.port.publish((x,y,size),self.seq,1800000000+self.now)
        self.assertTrue(self.service.capture_once())

    def test_waits_for_post_action_frame_then_resumes(self):
        s=self.service;result,ticket=s.pause_for_action();self.assertTrue(result.ok)
        done=1800000000+self.now
        result=s.finish_action(ticket,'completed',adjust_axis='horizontal',not_before=done)
        self.assertEqual(result.reason,'awaiting_post_action_frame');self.assertIsNone(self.port.lease)
        self.frame(400)
        result=s.finish_action(ticket,'completed',adjust_axis='horizontal',not_before=done)
        self.assertTrue(result.ok,result.reason);self.assertEqual(s.controller.target_x_fraction,.625)

    def test_explicit_stop_invalidates_pending_adjustment(self):
        s=self.service;_,ticket=s.pause_for_action();done=1800000000+self.now
        s.stop_follow();self.frame(400)
        result=s.finish_action(ticket,'completed',adjust_axis='horizontal',not_before=done)
        self.assertFalse(result.ok);self.assertIsNone(self.port.lease)

    def test_stale_height_blocks_vertical_resume(self):
        s=self.service;_,ticket=s.pause_for_action();done=1800000000+self.now
        self.frame(y=200);self.port.height_fresh=False
        result=s.finish_action(ticket,'completed',adjust_axis='vertical',not_before=done)
        self.assertEqual(result.reason,'height_unknown_or_stale');self.assertIsNone(self.port.lease)


class AudioConcurrency(unittest.IsolatedAsyncioTestCase):
    async def test_blocking_action_does_not_block_interrupt_or_allow_old_queue(self):
        started=threading.Event();release=threading.Event();calls=[]
        class LocalRunner:
            def run(self,code):
                calls.append(code);started.set();release.wait(2);return {'ok':True}
        runner=LocalRunner();emergency=Mock(return_value={'ok':True})
        runner.demo=SimpleNamespace(drone=SimpleNamespace(emergency=emergency))
        dialog=VolcDuplex(runner,None,instructions='',api_key='offline-test')
        first=asyncio.create_task(dialog._dispatch('run_program',{'code':'first'}))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait,1))
            queued=asyncio.create_task(dialog._dispatch('run_program',{'code':'obsolete'}))
            await asyncio.sleep(.02)
            result=await asyncio.wait_for(dialog._dispatch('interrupt',{'land':True}),.5)
            self.assertTrue(result['ok']);emergency.assert_called_once_with(True)
            release.set();await first
            self.assertEqual((await queued)['status'],'cancelled');self.assertEqual(calls,['first'])
        finally:
            release.set();await first


class LogRotation(unittest.TestCase):
    def test_rotates_and_retains_all_motion_results(self):
        with tempfile.TemporaryDirectory() as folder:
            log=SessionLog(folder,{},max_bytes=1000,max_segments=20)
            for i in range(50):
                log.submit(dict(event='motion_request',time=i*.02,channels=[i,0,0,0],status='accepted',
                                openpose_control={'padding':'x'*400}))
            log.close();self.assertTrue(log.finished.wait(2));self.assertIsNone(log.error)
            self.assertGreater(len(log.paths),1)
            rows=[json.loads(line) for p in log.paths for line in p.read_text().splitlines()]
            motions=[r for r in rows if r['event']=='motion_request']
            self.assertEqual([r['channels'][0] for r in motions],list(range(50)))
            self.assertTrue(rows[-1]['complete']);self.assertTrue(log.status()['flush_complete'])


class ActualSdkAdjustment(unittest.TestCase):
    def test_six_axis_actions_through_real_integration_with_simulated_transport(self):
        from drone_sdk import controller as ctl, _driver as drv
        from drone_sdk.ai_bridge import UnifiedDrone
        from drone_sdk.contract import FrameResult
        from r3_follow.vision import synthetic_frame
        self.assertTrue(drv.SIMULATED)
        seq=0; position=dict(x=.5,y=.5,size=.25)
        lock=threading.Lock()
        def frame():
            nonlocal seq
            with lock:
                seq+=1
                return FrameResult(synthetic_frame(**position),seq,time.time(),False)
        execute=drv.execute
        def move(action,param=None):
            result=execute(action,param)
            if result and action in ('left','right','up','down','forward','back'):
                with lock:
                    if action in ('left','right'):position['x']+= .05 if action=='left' else -.05
                    if action in ('up','down'):position['y']+= .05 if action=='up' else -.05
                    if action in ('forward','back'):position['size']+= .02 if action=='forward' else -.02
            return result
        def cleanup():
            ctl.stop_follow();time.sleep(.15);ctl.set_frame_provider(None)
            ctl.land('model',priority=True);time.sleep(.08)
            ctl.get_status();ctl.release_control('model')
        cleanup()
        self.addCleanup(cleanup)
        with patch.dict(os.environ,{'R3_FOLLOW_CONFIG':str(ROOT/'modules/follow/configs/aircraft_A_ID0_motion_follow.json')}), \
             patch.object(drv,'execute',side_effect=move), \
             patch('socket.socket',side_effect=AssertionError('No real network in SDK test')):
            ctl.set_frame_provider(frame)
            runner=Runner(Demo(UnifiedDrone()),allow_simulated_sdk=True)
            self.assertTrue(runner.run('drone.takeoff()')['ok'])
            self.assertTrue(runner.run('drone.start_follow()')['ok'])
            for direction in ('left','right','up','down','forward','back'):
                result=runner.run('drone.move_%s(20)'%direction)
                self.assertTrue(result['ok'],result)
                self.assertTrue(result['follow_resumed'],result)
                self.assertEqual(ctl.get_status().control_owner,'follow')
            runner.demo.drone.emergency(False)
            self.assertFalse(ctl.is_following())
            self.assertEqual(drv.snapshot()['velocity'],(0,0,0,0))


if __name__=='__main__':
    unittest.main(verbosity=2)
