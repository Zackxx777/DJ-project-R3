# AI 随行小伙伴 · 三组开发仓库

基于二组整机代码，维护三组的识别、跟随控制和制动改动。统一使用 **Python 3.11**。

当前基线是 [LenLanxi/DJ-project](https://github.com/LenLanxi/DJ-project) 的 `v4.1-bluetooth-ai-integrated`，提交 `9a7d2a17c4634b0c26bcab82a48410021031e714`。这是独立仓库：第一条提交保存上游源码快照，后续提交记录三组改动。原始历史、贡献者记录在上游仓库，来源见 [UPSTREAM.json](UPSTREAM.json)。

## 包含的内容

- 二组 SDK、四组 AI 模块和原有启动脚本一并保留；`drone_sdk/`、`modules/ai/` 的 66 个文件与上述上游版本逐字节一致。
- 三组版本 `r3-9a7d2a1-braking1`：标记识别、开源 PID 适配、上下跟随、识别与发送线程、丢失归零、控制权交接和提前减速。
- 三机分别绑定 `DICT_4X4_50` 的 ID 0、1、2，配置和标记图均在 `modules/follow/`。
- 保留 v4.1 SDK 校验记录。开源 PID 原文件及许可证保留，Git 固定其换行格式，避免 Windows 检出后哈希失配。

这里只修改三组模块及本仓库说明。后续二组、四组更新需先比较再同步，不能用旧交付包直接覆盖。

## 验证进度与限制

2026-10-26：Python 3.11.9 下 **612 项独立测试通过**，三个标记配置检查通过；实际 SDK、三组后台和四组 Gateway 的离线交接已运行。见 [验证记录](docs/R3_VALIDATION_20261026.md)。

**这不是实飞验收。** 本次使用合成画面和模拟飞行，没有打开摄像头、麦克风或连接无人机。豆包实时语音、真实网络、制动距离、三机同时飞行仍需验证。

已知限制：目标暂时丢失、跟随处于恢复等待时，四组普通运动请求可能收到 `control_busy: busy`。本次已记录，未修改二组、四组代码解决该行为。

## 安装与离线检查

在仓库根目录 PowerShell 中运行。原有 Python 3.14 可以保留。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 -B modules\follow\verify_r3_changes.py --output logs\r3_validation
.\.venv\Scripts\python.exe -X utf8 -B modules\follow\check_follow_config.py --config modules\follow\configs\aircraft_A_ID0.json --config modules\follow\configs\aircraft_B_ID1.json --config modules\follow\configs\aircraft_C_ID2.json --expected-ids 0 1 2
.\.venv\Scripts\python.exe -X utf8 -B modules\follow\check_team_handoff.py --root . --output logs\r3_handoff
```

完整语音入口的额外依赖见 `requirements-stt.txt`；豆包需要单独取得有效密钥。`.env.example` 只是模板，真实密钥只放本机 `.env`，不要提交。原二组说明保存在 [README_UPSTREAM.md](README_UPSTREAM.md)，其中历史测试成绩和操作说明不代表本次已再次验证。

## 启用本次跟随配置

在启动整机程序的**同一个终端**里设置，例如飞机 A：

```powershell
$env:R3_FOLLOW_CONFIG = (Resolve-Path .\modules\follow\configs\aircraft_A_ID0.json).Path
```

飞机 B、C 分别换成 `aircraft_B_ID1.json`、`aircraft_C_ID2.json`。没有设置时，整机入口可能回到旧配置，不能据此判断已启用本次 PID。应由整机入口调用 `controller.start_follow()`；直接创建控制器的旧演示不能替代后台服务验证。

| 飞机 | 标记 ID | 前后上限 | 转向上限 | 上下上限 |
|---|---:|---:|---:|---:|
| A | 0 | 40 RC | 50 RC | 20 RC |
| B | 1 | 30 RC | 20 RC | 20 RC |
| C | 2 | 30 RC | 20 RC | 20 RC |

RC 是控制杆量，**不是厘米/秒**。当前按标记相对大小保持距离，尚无实测米数换算。默认 `yaw` 模式用转向对准目标，左右平移请求为零；`offline_candidates/` 中的混合和平移配置供对比，未完成实飞验证。上下跟随依赖新鲜、可信的高度数据，缺失时保持升降请求为零。

## 后续维护

1. 新修改放在 `modules/follow/`，通过离线检查后再提交。
2. `origin` 指向本仓库；本机 `upstream` 仅用于读取二组更新，推送已禁用。
3. 同步上游时检查差异及 SDK 校验记录，重跑三组测试与交接检查。
4. 不提交虚拟环境、密钥、现场录像、录音及运行日志；旧压缩包继续保留。

许可证与来源沿用相应原文件；开源依赖来源和哈希见 [PROVENANCE.json](modules/follow/r3_follow/_vendor/PROVENANCE.json)，未为整机项目另行声明新许可证。
