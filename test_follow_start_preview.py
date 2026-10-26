"""Startup lifecycle regression: no camera, cloud, sockets or real flight."""
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT/'modules/ai'),str(ROOT/'modules/follow')]
from drone_sdk.follow_integration import FollowIntegration
from drone_sdk.contract import ControlResult
from r3_follow.service import FollowService
from r3_follow.mock_port import MockPort
from r3_follow.core import Observation
from program_runner import validate


class LivePort(MockPort):
    def __init__(self):
        super().__init__(); self.seq=0; self.reason='not_found'
    def get_frame(self):
        self.seq+=1
        self.publish(self.reason,self.seq,time.time())
        return super().get_frame()


class Detector:
    def detect(self, reason, frame_id, acquired_at, source):
        found=not reason
        return Observation(frame_id,acquired_at,source,640,480,0,found,
            320 if found else None,240 if found else None,.25 if found else None,
            ((240,160),(400,160),(400,320),(240,320)) if found else (),reason)


class PreviewLifecycle(unittest.TestCase):
    def setUp(self):
        self.guard=patch('socket.socket',side_effect=AssertionError('No network'))
        self.guard.start();self.addCleanup(self.guard.stop)
        self.port=LivePort()
        self.service=FollowService(self.port,tracker=Detector(),log_directory=False)
        class Bridge:
            service=self.service
            def is_following(inner):return self.service.is_following()
            def get_follow_status(inner):return self.service.get_follow_status()
            def start_follow(inner):return self.convert(self.service.start_follow())
            def stop_follow(inner):return self.convert(self.service.stop_follow())
        self.bridge=Bridge()
        self.integration=FollowIntegration(lambda:'follow' if self.port.lease else 'none')
        def ensure():
            self.integration._bridge=self.bridge;self.integration._port=self.port
            self.service.start_background()
            return self.bridge
        self.integration._ensure_bridge=ensure
        self.addCleanup(self.integration.stop)
        self.addCleanup(self.service.close)

    def convert(self,result):
        return ControlResult(result.ok,'follow' if self.port.lease else 'none',result.reason)

    def wait(self,condition):
        deadline=time.monotonic()+1
        while not condition():
            self.assertLess(time.monotonic(),deadline)
            time.sleep(.01)

    def test_preview_without_provider_reads_shared_frames_and_never_takes_control(self):
        self.port.reason=''
        self.assertTrue(self.integration.prepare_preview().ok)
        self.wait(lambda:self.integration.status().get('target_visible'))
        self.assertIsNone(self.port.lease)
        self.assertFalse(self.integration.is_following())
        self.assertFalse(any(e['event']=='grant' for e in self.port.events))

    def test_waits_past_initial_miss_and_budget_expiry_for_valid_target(self):
        for reason in ('not_found','detection_budget_exceeded'):
            with self.subTest(reason=reason):
                self.port.reason=reason
                self.integration.prepare_preview()
                self.wait(lambda:self.service.controller.observation is not None and
                          self.service.controller.observation.reason == reason)
                timer=threading.Timer(.15,lambda:setattr(self.port,'reason',''))
                timer.start()
                result=self.integration.start(timeout=.6)
                timer.join()
                self.assertTrue(result.ok,result)
                self.assertEqual(self.port.lease.owner,'follow')
                self.service.stop_follow()

    def test_failed_start_keeps_preview_but_new_target_does_not_auto_start(self):
        self.integration.prepare_preview()
        result=self.integration.start(timeout=.12)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason,'follow_target_not_found')
        self.assertFalse(self.service.closed)
        self.port.reason=''
        self.wait(lambda:self.integration.status().get('target_visible'))
        self.assertIsNone(self.port.lease)
        self.assertTrue(self.integration.status()['recognition_running'])
        self.assertTrue(self.integration.start(timeout=.5).ok)

    def test_explicit_stop_cancels_pending_start_and_closes_preview(self):
        self.integration.prepare_preview()
        box={}
        thread=threading.Thread(target=lambda:box.update(result=self.integration.start(.8)))
        thread.start();time.sleep(.06);self.integration.stop()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertFalse(box['result'].ok)
        self.assertEqual(box['result'].reason,'follow_start_cancelled')
        self.assertIsNone(self.port.lease)
        self.assertFalse(self.integration.status()['recognition_running'])

    def test_new_voice_turn_cancels_waiting_start(self):
        self.integration.prepare_preview()
        started=time.monotonic()
        result=self.integration.start(.8,cancelled=lambda:time.monotonic()-started>.06)
        self.assertEqual(result.reason,'follow_start_cancelled')
        self.assertIsNone(self.port.lease)
        self.assertFalse(self.service.closed)

    def test_duplicate_never_starts(self):
        self.port.reason='ambiguous_duplicate_id'
        self.integration.prepare_preview()
        result=self.integration.start(.4)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason,'ambiguous_duplicate_id')
        self.assertIsNone(self.port.lease)


class ProgramDeclarations(unittest.TestCase):
    def test_exact_logged_import_forms_are_normalized_without_execution(self):
        for code in ('from drone import *\ntakeoff()',
                     'from drone import takeoff\ntakeoff()',
                     'import drone\nstart_follow()'):
            self.assertIn(validate(code),('takeoff','start_follow'))

    def test_dynamic_nested_other_imports_and_multiple_calls_remain_rejected(self):
        for code in ('import os\ntakeoff()', 'from drone import takeoff as f\nf()',
                     'from drone import *\ntakeoff()\nland()',
                     'from drone import *\nif True:\n takeoff()',
                     'from drone import *\nmove_left(x)'):
            with self.subTest(code=code),self.assertRaises(ValueError):validate(code)


class SharedSdkPreview(unittest.TestCase):
    def test_same_publish_frame_path_as_real_entry_detects_on_ground_without_motion(self):
        from drone_sdk import controller as ctl, _driver as drv
        from drone_sdk.contract import FrameResult
        from r3_follow.vision import synthetic_frame
        self.assertTrue(drv.SIMULATED)
        self.assertEqual(ctl.get_status().flight_state,'landed')
        ctl.stop_follow();time.sleep(.06);ctl.set_frame_provider(None)
        self.addCleanup(ctl.stop_follow)
        with patch('socket.socket',side_effect=AssertionError('No network')), \
             patch.dict(os.environ,{'R3_FOLLOW_CONFIG':str(ROOT/'modules/follow/configs/aircraft_A_ID0_motion_follow.json')}):
            self.assertTrue(ctl._follow.prepare_preview().ok)
            deadline=time.monotonic()+1
            frame=synthetic_frame(x=.5,y=.5,size=.25)
            seq=0
            while not ctl._follow.status().get('target_visible'):
                self.assertLess(time.monotonic(),deadline)
                seq+=1;ctl.publish_frame(FrameResult(frame,seq,time.time(),False));time.sleep(.02)
            self.assertEqual(ctl.get_status().control_owner,'none')
            self.assertFalse(ctl.is_following())
            self.assertEqual(drv.snapshot()['velocity'],(0,0,0,0))


if __name__=='__main__':unittest.main(verbosity=2)
