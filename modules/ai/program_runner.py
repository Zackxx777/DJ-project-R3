"""Dispatch allowlisted calls without eval/exec; the real entry injects its guarded SDK."""
import ast
from demo_text import Demo
from drone_adapter import MockDrone

COMMANDS = {'get_status': '查看状态', 'get_battery': '查看电量',
            'takeoff': '起飞', 'land': '降落', 'start_follow': '跟着我',
            'stop_follow': '停止跟随', 'stop_motion': '停在这里',
            'move_forward': '前进30厘米', 'move_up': '升高30厘米',
            'move_down': '降低30厘米', 'move_back': '后退30厘米',
            'move_left': '左移30厘米', 'move_right': '右移30厘米', 'rotate': '右转30度',
            'turn_left': '左转90度', 'turn_right': '右转90度'}

# 允许带"厘米"参数的接口（20~100cm，与 move_forward 同一限制）
DISTANCE_COMMANDS = ('move_forward', 'move_back', 'move_left', 'move_right', 'move_up', 'move_down')
MOVEMENT_LABELS = dict(zip(DISTANCE_COMMANDS, ('前进', '后退', '左移', '右移', '升高', '降低')))

def _call_node(code):
    """取出"唯一那个 drone.xxx() 调用"。

    模型常会带 import / print / 赋值 / 注释，这些**不会被本执行器执行**（我们只读调用名
    和字面量参数，从不 exec 代码），所以直接忽略；真正要守的边界是：
      - 只能有**一个** drone.* 调用
      - 名字必须在白名单里
      - 参数只能是字面量（移动类 20~100 的整数），且不允许关键字参数
    这样既不会因为 `import time` 把合法起飞判死，也不会让多步程序蒙混过关。
    """
    tree = ast.parse(code)
    # Some voice-model tool calls omit the documented drone prefix. Accept
    # only a whole-program single whitelist call, never evaluate a Python name.
    bare = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id in COMMANDS]
    if bare:
        def proxy_import(node):
            # These declarations are discarded, NEVER imported/executed.
            if isinstance(node, ast.Import):
                return all(n.name == 'drone' and n.asname is None for n in node.names)
            if isinstance(node, ast.ImportFrom):
                return (node.module == 'drone' and node.level == 0 and
                        all(n.name in (*COMMANDS, '*') and n.asname is None for n in node.names))
            return False
        body = [node for node in tree.body if not proxy_import(node)]
        if (len(bare) != 1 or len(body) != 1
                or not isinstance(body[0], ast.Expr)
                or body[0].value is not bare[0]):
            raise ValueError('简写接口必须是单独一条调用，不允许组合或嵌套')
        call = bare[0]
        return ast.Call(func=ast.Attribute(value=ast.Name(id='drone', ctx=ast.Load()),
                        attr=call.func.id, ctx=ast.Load()), args=call.args, keywords=call.keywords)
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and isinstance(node.func.value, ast.Name)
             and node.func.value.id == 'drone']
    if not calls:
        raise ValueError('未找到允许的接口；例如起飞应写 drone.takeoff()，跟随应写 drone.start_follow()')
    if len(calls) > 1:
        raise ValueError('一次只允许调用一个接口')
    call = calls[0]
    # 必须是"裸调用"语句：x = drone.takeoff() 这种赋值形式一律拒绝（四组回归用例也这么要求）
    if not any(isinstance(node, ast.Expr) and node.value is call
               for node in ast.walk(tree)):
        raise ValueError('只允许直接调用接口')
    return call


def validate(code):
    if not isinstance(code, str) or len(code) > 2000:
        raise ValueError('程序为空或过长')
    call = _call_node(code)
    if not (call.func.attr in COMMANDS and not call.keywords):
        raise ValueError('接口不在允许列表')
    name = call.func.attr
    if name in DISTANCE_COMMANDS:
        if not (len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
                and type(call.args[0].value) is int and 20 <= call.args[0].value <= 100):
            raise ValueError('移动类接口只接受 20~100 厘米的整数参数')
    elif name in ('rotate', 'turn_left', 'turn_right'):
        rotation_degrees(call)
    elif call.args:
        raise ValueError('此接口不接受参数')
    return name


def rotation_degrees(call):
    if call.func.attr in ('turn_left', 'turn_right'):
        if len(call.args) > 1:
            raise ValueError('左右转只接受一个可选角度，省略时为90度')
        value = ast.literal_eval(call.args[0]) if call.args else 90
        if type(value) is not int or not 1 <= value <= 180:
            raise ValueError('左右转只接受1至180的整数度数')
        return -value if call.func.attr == 'turn_left' else value
    if len(call.args) != 1:
        raise ValueError('旋转需要一个角度参数')
    value = ast.literal_eval(call.args[0])
    if type(value) is not int or not 1 <= abs(value) <= 180:
        raise ValueError('旋转只接受-180至180的非零整数度数；正数右转，负数左转')
    return value

class Runner:
    def __init__(self, demo=None, *, allow_simulated_sdk=False, allow_programs=False):
        self.demo = demo or Demo()
        self.allow_programs=allow_programs
        allowed = type(self.demo.drone) is MockDrone
        if allow_simulated_sdk:
            from drone_sdk.ai_bridge import UnifiedDrone
            from drone_sdk import controller
            allowed = type(self.demo.drone) is UnifiedDrone and controller.SIMULATED is True
        if not allowed:
            raise ValueError('此版本执行器仅允许本地模拟器')

    def run_sandbox(self, code):
        # Parse for syntax only; generated programs execute exclusively inside Docker.
        try:
            if not isinstance(code,str) or not code.strip(): raise ValueError('empty_program')
            ast.parse(code)
        except (SyntaxError,ValueError,TypeError):
            return {'accepted':False,'ok':False,'status':'failed','result':'invalid_program'}
        import asyncio
        from backends import MockBackend,SDKBackend
        from gateway import Gateway
        from sandbox_runner import SandboxRunner
        from pathlib import Path
        drone=self.demo.drone
        if type(drone) is MockDrone:
            backend=MockBackend()
            backend.flying=drone.flying
        else:
            backend=SDKBackend(Path(__file__).resolve().parents[2])
        gateway=Gateway(backend,getattr(self,'structured_vision',None))
        result=asyncio.run(SandboxRunner(gateway).run(code))
        if type(drone) is MockDrone:
            drone.flying=backend.flying
        return {'accepted':True,'ok':result['ok'],'status':result['status'],
                'simulated':backend.simulated,'result':str(result),'execution':result,
                'state':drone.get_status()}

    def run(self, code):
        try:
            name = validate(code)
        except (SyntaxError, ValueError, TypeError, RecursionError) as exc:
            if not self.allow_programs:
                return {'accepted': False, 'result': '程序检查未通过，未执行动作',
                        'reason': str(exc), 'code': str(code)[:200]}
            return self.run_sandbox(code)
        if name in DISTANCE_COMMANDS:
            distance = _call_node(code).args[0].value
            command = '%s%d厘米' % (MOVEMENT_LABELS[name], distance)
        elif name in ('rotate', 'turn_left', 'turn_right'):
            degrees = rotation_degrees(_call_node(code))
            command = '%s%d度' % ('右转' if degrees > 0 else '左转', abs(degrees))
        else:
            command = COMMANDS[name]
        message = self.demo.handle(command)
        receipt = getattr(self.demo, 'last_result', {})
        return {'accepted': True, 'ok': receipt.get('ok') is True,
                'status': receipt.get('status', 'completed' if receipt.get('ok') else 'failed'),
                'follow_resumed': receipt.get('follow_resumed'),
                'simulated': self.demo.drone.get_status().get('simulated', True),
                'result': message,
                'state': self.demo.drone.get_status()}

    def run_guarded(self, code, cancelled):
        from contextlib import nullcontext
        if cancelled():
            return {'accepted': False, 'ok': False, 'status': 'cancelled', 'reason': 'stale_turn'}
        drone = self.demo.drone
        guard = drone.turn_guard(cancelled) if hasattr(drone, 'turn_guard') else nullcontext()
        with guard:
            return self.run(code)
