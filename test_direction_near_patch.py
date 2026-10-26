"""Direction, close-target and altitude regressions. No hardware or cloud I/O."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT/'modules/ai'), str(ROOT/'modules/follow')]
from program_runner import Runner
from demo_text import Demo
from drone_adapter import MockDrone
from r3_follow.core import Observation, FollowController
from r3_follow.config import FollowConfig, load_config
from r3_follow.service import FollowService
from r3_follow.mock_port import MockPort


def obs(seq, t, size=.2, x=320, y=240):
    half=size*640/2
    return Observation(seq,t,'shared_sdk',640,480,0,True,x,y,size,
        ((x-half,y-half),(x+half,y-half),(x+half,y+half),(x-half,y+half)),'')


def controller(size=.2, limit=.2, rate=2.):
    c=FollowController(control_profile='openpose_pid',distance_mode='relative_size',
        steering_mode='adaptive',pid_tuning='responsive_v1',vertical_follow=True,
        require_geometry=True,max_request_rate=rate,reference_size_limit=limit)
    c.observe(obs(0,10,size));assert c.calibrate(10)[0];assert c.start(10)[0]
    return c


class FacingVoice(unittest.TestCase):
    def test_hud_successful_handoff_is_running_and_failure_is_stopped(self):
        from follow_hud import follow_status_text
        self.assertEqual(follow_status_text({'event':'handoff_finish','status':'completed'})[0], 'follow: RUNNING')
        self.assertIn('STOPPED',follow_status_text({'event':'handoff_finish','status':'failed'})[0])

    def test_user_left_right_converted_exactly_once_and_distance_preserved(self):
        for command,actual in [('left','right'),('right','left')]:
            for following in [False,True]:
                r=Runner(Demo(MockDrone(),lateral_frame='facing_pilot'))
                r.run('takeoff()')
                if following:r.run('start_follow()')
                d=r.demo.drone
                with patch.object(d,'move_'+actual,wraps=getattr(d,'move_'+actual)) as call:
                    result=r.run('drone.move_%s(50)'%command)
                call.assert_called_once_with('model',50)
                self.assertTrue(result['ok']);self.assertIn('面对机头',result['result'])
                self.assertEqual(d.following,following)

    def test_default_sdk_and_other_directions_not_inverted(self):
        for frame,names in [('aircraft',('left','right')),('facing_pilot',('up','down','forward','back'))]:
            for name in names:
                r=Runner(Demo(MockDrone(),lateral_frame=frame));r.run('takeoff()')
                d=r.demo.drone
                with patch.object(d,'move_'+name,wraps=getattr(d,'move_'+name)) as call:
                    self.assertTrue(r.run('drone.move_%s(20)'%name)['ok'])
                call.assert_called_once_with('model',20)

    def test_rotation_stops_follow_and_does_not_autoresume(self):
        for degree in [-180,-30,30,180]:
            r=Runner();r.run('takeoff()');r.run('start_follow()');d=r.demo.drone
            with patch.object(d,'rotate',wraps=d.rotate) as call:
                result=r.run('drone.rotate(%d)'%degree)
            self.assertTrue(result['ok']);self.assertFalse(d.following)
            call.assert_called_once_with('model',degree)
            self.assertIsNone(d.owner)

    def test_rotation_parameters_rejected_without_movement(self):
        r=Runner();r.run('takeoff()')
        with patch.object(r.demo.drone,'rotate') as call:
            for value in ['0','181','-181','True','3.2','20+10','x','get_status()']:
                self.assertFalse(r.run('drone.rotate(%s)'%value)['accepted'],value)
        call.assert_not_called()

    def test_failed_rotation_does_not_claim_success_or_resume(self):
        r=Runner();r.run('takeoff()');r.run('start_follow()');r.demo.drone.fail_next_move=True
        result=r.run('rotate(180)')
        self.assertFalse(result['ok']);self.assertFalse(r.demo.drone.following)


class CloseTarget(unittest.TestCase):
    def test_close_start_reference_and_depth_rebase_are_capped(self):
        c=controller(.451137)
        self.assertEqual(c.reference_size,.2)
        for i in range(1,10):
            c.observe(obs(i,10+i*.1,.40));request=c.tick(10+i*.1)
            self.assertLessEqual(request.forward,0.)
        self.assertLess(request.forward,0.)
        c.pause();c.observe(obs(11,11.1,.35));self.assertTrue(c.rebase_axis('depth',11.1)[0])
        self.assertEqual(c.reference_size,.2)

    def test_logged_lead_reversal_cannot_command_forward_inside_reference(self):
        # Logged failure: negative current error, positive estimated rate
        # changes -3 RC PID into +6 RC. Cover with/without acceleration limit.
        for rate in [None,2.]:
            c=controller(.14395,None,rate)
            for i,error in enumerate([-.11+j*.006 for j in range(18)],1):
                t=10+i*.03;c.observe(obs(i,t,.14395/(1+error)))
                request=c.tick(t+.08)
                self.assertLessEqual(request.forward,0.,(error,request))

    def test_forward_history_is_cut_immediately_on_large_close_frame(self):
        c=controller()
        for i in range(1,8):
            t=10+i*.1;c.observe(obs(i,t,.13));request=c.tick(t)
        self.assertGreater(request.forward,0.)
        c.observe(obs(8,10.8,.25));self.assertLessEqual(c.tick(10.8).forward,0.)

    def test_far_chase_identical_with_and_without_reference_cap(self):
        a,b=controller(.15,.2),controller(.15,None)
        for i in range(1,60):
            t=10+i*.04;size=.15/(1+.008*i)
            a.observe(obs(i,t,size));b.observe(obs(i,t,size))
            self.assertEqual(a.tick(t+.1),b.tick(t+.1))
        self.assertGreater(a.last_request.forward,0.)

    def test_follow_horizontal_and_vertical_signs_not_voice_mirrored(self):
        for x,y,right,up in [(370,200,1,1),(270,280,-1,-1)]:
            c=controller()
            for i in range(1,8):
                t=10+i*.1;c.observe(obs(i,t,.2,x,y));r=c.tick(t)
            self.assertGreater(r.right*right,0.);self.assertGreater(r.up*up,0.)

    def test_config_rejects_bad_limit(self):
        for value in [True,0,.31,float('nan'),'0.2']:
            with self.assertRaises(ValueError):
                FollowConfig(control_profile='openpose_pid',distance_mode='relative_size',reference_size_limit=value)
        config=load_config(ROOT/'modules/follow/configs/aircraft_A_ID0_motion_follow.json')
        self.assertEqual(config.reference_size_limit,.2);self.assertTrue(config.vertical_follow)


class VerticalSending(unittest.TestCase):
    def test_actual_sender_up_down_and_height_limits(self):
        for y,height,fresh,expected in [(200,120,True,1),(280,120,True,-1),
                (200,180,True,0),(280,70,True,0),(200,120,False,0)]:
            now=[10.];p=MockPort(lambda:now[0]);p.height_cm=height;p.height_fresh=fresh
            s=FollowService(p,monotonic=lambda:now[0],wall_clock=lambda:1800000000+now[0],
                log_directory=False,control_profile='openpose_pid',vertical_follow=True,
                distance_mode='relative_size',reference_size_limit=.2)
            try:
                s.controller.observe(obs(0,10));s.controller.calibrate(10)
                # The service normally sets frame_ready after capture.
                s.frame_ready=True;s.last_frame_id=0
                self.assertTrue(s.start_follow().ok)
                for i in range(1,8):
                    now[0]=10+i*.1;s.controller.observe(obs(i,now[0],.2,y=y));s.control_once()
                ud=s.last_channels[2]
                if expected:self.assertGreater(ud*expected,0)
                else:self.assertEqual(ud,0)
                state=s.get_follow_status()
                self.assertTrue(state['vertical_follow'])
            finally:s.close()


if __name__=='__main__':unittest.main()
