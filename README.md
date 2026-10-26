# AI 随行小伙伴 · 三组联调仓库

基于二组交付的 `v4.1-bluetooth-ai-integrated`，整合标记跟随、豆包语音控制和动作交接。运行环境统一为 **Python 3.11**。

**当前交付为 2026-10-27 联调版，已修改二组 SDK 和四组语音模块的部分代码。** 具体范围、接口约定和复测步骤见 [给二四组的交接说明](docs/HANDOFF_20261027.md)。请按整版比较合并，不要直接用旧版 `drone_sdk/` 或 `modules/ai/` 覆盖。

## 当前改进

- 优化标记识别耗时、启动等待和短暂漏检恢复；显示检测状态、有效目标及停止原因。
- 默认配置采用横移与转向结合的跟随，并启用带高度时效检查的上下跟随。
- 调整追赶响应、提前减速和近距离前进限制；过期或丢失目标时归零。
- 补齐语音六方向移动和旋转；跟随中平移/升降可暂停跟随、完成动作、按新画面更新基准，再按条件恢复。
- 修复悬停后控制权未释放导致必须落地重飞才能跟随的问题。
- 当前语音入口的左右以“用户面对机头”为约定；左右转默认 90°，仍支持指定角度。底层 SDK 与自动跟随坐标没有反转。

用户在 10 月 26 日完成了多轮实机测试，并反馈跟随已有改善。最新微调仍需按交接说明复测；未给出实测速度、制动距离或全场景验收结论。标记识别不等于豆包看图问答，后者的独立视觉模型尚未配置。

## 安装

在 VS Code 中打开本仓库，在 PowerShell 终端执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_r3_voice.ps1 -CheckOnly -RealDrone
```

`requirements.txt` 已补入音频重采样依赖 `soxr`，无需为豆包模式安装本地 Whisper 模型。VS Code 解释器选择 `.venv\Scripts\python.exe`。`-CheckOnly` 只检查依赖、跟随配置和启动参数，不连接云端、音频流或无人机。

## 启动豆包与跟随

先列出当前电脑的音频设备：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_r3_voice.ps1 -ListDevices
```

选择实际耳机的输入/输出编号。下面的 `1`、`5` **仅为示例，必须替换为自己电脑的编号**：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_r3_voice.ps1 -RealDrone -InputDevice 1 -OutputDevice 5
```

按本机隐藏提示输入 `VOLC_API_KEY`；也支持继承进程已有的同名环境变量。密钥不写入仓库或命令参数。电脑通过 TELLO Wi-Fi 连接飞机，同时通过手机 USB 网络共享或有线网络访问豆包。只运行一个控制程序。

- 此入口选择 `aircraft_A_ID0_motion_follow.json`（`DICT_4X4_50`，ID 0）。B/C 配置和旧 A 配置保留，但不等于已验证三机同时飞行。
- `-RealDrone` 允许响应用户的起飞请求，不再要求额外的起飞解锁；启动程序不会自行起飞。起飞电量阈值为 30%。
- 说“起飞”后尝试补高到接近 120 cm，再悬停；标记清晰可见后另说“跟着我”。可用 `-NoAutoClimb` 关闭补高。
- 语音“向左转”“向右转”默认 90°；“向左转 45 度”保留指定角度。完成旋转后需要重新说“跟着我”。
- `Ctrl+Shift+Alt+A` 请求零速度悬停，`Ctrl+Shift+Alt+L` 请求降落；悬停不是停桨。先落地再退出终端。
- 不加 `-RealDrone` 时仅测试云端语音与模拟飞机。

当前现场验证使用 **无 Docker 时的本地白名单执行器**。安装了 Docker 时原入口会选择另一条沙箱执行路径；不能把这次实测结果直接当作沙箱路径的验收。详见交接说明。

## 发布前离线验证

2026-10-27，Python 3.11.9：跟随测试 **656 项**、语音测试 **106 项**、跨模块专项 **122 项**，均通过。这是各测试命令收集到的执行数，包含继承/集成覆盖，不能视作 884 个互不重复场景；本轮验证没有实飞。

复现命令、专项文件与尚需实测的内容见 [交接说明](docs/HANDOFF_20261027.md)。旧的 [10 月 26 日验证记录](docs/R3_VALIDATION_20261026.md) 是早期版本记录，不代表当前源码仍与二四组原版一致。

## 来源与旧版保留

上游：[LenLanxi/DJ-project](https://github.com/LenLanxi/DJ-project)，提交 `9a7d2a17c4634b0c26bcab82a48410021031e714`。本仓库初始提交 `727f2af` 保存原版源码快照，`aeb9787` 保存早期三组制动集成版；此前发出的本地压缩包未覆盖。来源见 [UPSTREAM.json](UPSTREAM.json)，原 README 见 [README_UPSTREAM.md](README_UPSTREAM.md)。

开源 PID 原文件及许可证保留在 `modules/follow/r3_follow/_vendor/`。密钥、虚拟环境、本机音频配置及原始实飞/录音日志不随公开仓库上传。