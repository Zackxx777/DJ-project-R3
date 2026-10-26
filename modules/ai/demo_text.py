"""固定指令分派；默认模拟，实机入口显式注入 UnifiedDrone，不执行任意代码。"""
import json
from drone_adapter import MockDrone

HELP = '可输入：查看电量、查看状态、起飞、跟着我、前进/后退/左移/右移/升高/降低20厘米、向左转/向右转（默认90度）、向左转45度、停止跟随、停在这里、降落、退出'


class Demo:
    def __init__(self, drone=None, *, lateral_frame='aircraft'):
        if lateral_frame not in ('aircraft', 'facing_pilot'):
            raise ValueError('unknown voice lateral frame')
        self.lateral_frame = lateral_frame
        self.drone = drone or MockDrone()

    def describe(self):
        """状态口语化：TTS 要念人话，不能把 status 字典原样念出去。"""
        status = self.drone.get_status()
        parts = ['当前电量 %s%%' % status.get('battery')]
        height = status.get('height_cm')
        if type(height) is int:
            parts.append('高度 %d 厘米' % height)
        parts.append('正在飞行' if status.get('flying') else '未起飞，在地面')
        if status.get('following'):
            parts.append('跟随已开启')
        parts.append('链路正常' if status.get('connected', True) else '链路异常')
        return '，'.join(parts) + '。'

    def handle(self, text):
        self.last_result = {'ok': False, 'status': 'failed'}
        text = text.strip()
        drone = self.drone
        prefix = '[模拟] ' if drone.get_status().get('simulated', True) else '[真机] '
        if text == '查看电量':
            self.last_result = {'ok': True, 'status': 'completed'}
            return prefix + '当前电量为%d%%。' % drone.get_status()['battery']
        if text == '查看状态':
            self.last_result = {'ok': True, 'status': 'completed'}
            return prefix + self.describe()
        if text == '跟着我':
            result = drone.start_follow()
            self.last_result = result
            if result['ok']:
                return prefix + '已开始跟随'
            # 带上真实原因（地面状态通常是"尚未起飞"），别只说"请查看状态"
            return prefix + '无法开始跟随：' + str(result.get('message') or '原因未知')
        if text == '停止跟随':
            self.last_result = drone.stop_follow()
            return prefix + self.last_result['message']
        import re
        move=re.fullmatch(r'(前进|后退|左移|右移|升高|降低)(\d+)厘米',text)
        if move and 20<=int(move.group(2))<=100:
            distance=int(move.group(2))
            text=move.group(1)+'30厘米'
        else: distance=30
        rotation = re.fullmatch(r'(?:(?:向|往)\s*)?(左|右)\s*转(?:\s*(\d+)\s*度)?', text)
        degrees = int(rotation.group(2) or 90) if rotation else 0
        if rotation and not 1 <= degrees <= 180:
            return '旋转角度必须是1至180度整数，未执行动作。'
        if rotation:
            degrees *= -1 if rotation.group(1) == '左' else 1
            text = '旋转'
        requested_degrees = degrees
        facing = self.lateral_frame == 'facing_pilot'
        if facing:
            # Convert the voice convention once, just as for lateral movement.
            # SDK rotation and automatic follow yaw keep aircraft coordinates.
            degrees = -degrees
        actions = {
            '起飞': lambda: drone.takeoff('model'),
            '降落': lambda: drone.land('model'),
            '停在这里': lambda: drone.stop_motion('model'),
            '前进30厘米': lambda: drone.move_forward('model', distance),
            '后退30厘米': lambda: drone.move_back('model', distance),
            '左移30厘米': lambda: (drone.move_right if facing else drone.move_left)('model', distance),
            '右移30厘米': lambda: (drone.move_left if facing else drone.move_right)('model', distance),
            '升高30厘米': lambda: drone.move_up('model', distance),
            '降低30厘米': lambda: drone.move_down('model', distance),
            '旋转': lambda: drone.rotate('model', degrees),
        }
        if text not in actions:
            # 未知/听写错误的指令：绝不执行动作，并且必须让 TTS 能播报这句反问。
            # 这里只给一句短的——help 列表在启动横幅里已经打过了，念出来太啰嗦。
            return '抱歉，我没听清，请再说一遍；未识别固定指令，没有执行动作。'
        temporary_move = text in ('前进30厘米', '后退30厘米', '左移30厘米', '右移30厘米', '升高30厘米', '降低30厘米')
        was_following = drone.is_following()
        owns_follow = drone.get_status().get('owner') == 'follow'
        was_following = was_following or owns_follow
        ticket = None
        ticket_handoff = hasattr(drone, 'pause_follow_for_action')
        if text in ('降落', '停在这里'):
            # Also invalidate a paused action's ticket when following=False.
            result = drone.stop_follow()
            if not result['ok']:
                return prefix + '停止跟随未确认，未继续执行动作'
        if was_following or owns_follow:
            result = (drone.pause_follow_for_action() if ticket_handoff and
                      temporary_move else drone.stop_follow())
            ticket = result.get('ticket')
            if not result['ok']:
                return prefix + '无法暂停跟随，取消动作'
        result = drone.request_control('model')
        if not result['ok']:
            if ticket is not None:
                drone.finish_follow_action(ticket, 'failed')
            return prefix + result['message'] + '；未执行动作'
        result = None
        try:
            result = actions[text]()
            self.last_result = result
            message = result['message']
            if facing and text.startswith(('左移', '右移')):
                message = '按你面对机头时的%s侧：%s' % (text[0], message)
            if facing and text == '旋转':
                actual_direction = '右转' if degrees > 0 else '左转'
                voice_direction = '右转' if requested_degrees > 0 else '左转'
                message = '按你面对机头时的方向：' + message.replace(actual_direction, voice_direction, 1)
            if not result['ok']:
                stopped = drone.stop_motion('model')
                message += '；' + stopped['message'] + '；不恢复跟随'
        except Exception as exc:
            result = {'ok': False}
            stopped = drone.stop_motion('model')
            message = '执行异常：' + str(exc) + '；' + stopped['message'] + '；不恢复跟随'
        finally:
            released = drone.release_control('model')
        if text == '旋转':
            # Looking away from the marker is intentional. Do not have follow
            # immediately undo the rotation; keep recognition available.
            if hasattr(drone, 'prepare_follow_preview'):
                drone.prepare_follow_preview()
            if was_following:
                message += '；转向后跟随已停止，需要重新说跟随我'
        if result['ok'] and was_following and temporary_move:
            if not released['ok'] or (ticket_handoff and result.get('status') != 'completed'):
                if ticket is not None:
                    drone.finish_follow_action(ticket, 'failed')
                return prefix + message + '；动作完成或控制权释放未确认，不恢复跟随'
            axis = ('depth' if text.startswith(('前进', '后退')) else
                    'horizontal' if text.startswith(('左移', '右移')) else 'vertical')
            if ticket_handoff and hasattr(drone, 'finish_follow_adjustment'):
                resumed = drone.finish_follow_adjustment(ticket, 'completed', axis)
            else:
                resumed = (drone.finish_follow_action(ticket, 'completed') if ticket_handoff
                           else drone.start_follow())
            self.last_result = dict(result, follow_resumed=bool(resumed['ok']))
            message += '；' + ('已恢复跟随' if resumed['ok'] else '未恢复跟随：' + resumed['message'])
        elif ticket is not None:
            drone.finish_follow_action(ticket, 'failed')
        return prefix + message

if __name__ == '__main__':
    print('AI随行小伙伴 · 接口流程演示 v0.1')
    print('固定文字指令，无大模型、无语音、无真实无人机连接。')
    print(HELP)
    demo = Demo()
    while True:
        try:
            text = input('你：')
        except (EOFError, KeyboardInterrupt):
            break
        if text.strip() == '退出':
            break
        print('程序：' + demo.handle(text))
