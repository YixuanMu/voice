# A 股实时语音监控

Python 行情监控服务、React + TypeScript 本地 Dashboard，以及 Windows 中文语音播报。默认使用可复现的 5,000 只模拟股票，无需通达信账号。

## Windows 10 启动

1. 安装 64 位 Python 3.11 或更高版本并启用 Python Launcher（`py`）。安装带 npm 的 Node.js 22.12 或更高版本。
2. 下载或克隆仓库。双击 `start.bat`。首次启动需要联网安装依赖，随后浏览器打开 `http://127.0.0.1:8765`。关闭脚本窗口或按 Ctrl+C 停止。
3. 点击 **测试中文语音**，在 **播报记录** 查看播放结果。Windows 需要可供桌面 SAPI 使用的中文语音。可在 Windows 设置的“时间和语言 → 语音”添加中文语音，或在 Windows PowerShell 执行以下命令检查可用语音。

   ```powershell
   Add-Type -AssemblyName System.Speech
   (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() |
     ForEach-Object { $_.VoiceInfo.Culture.Name + ' ' + $_.VoiceInfo.Name }
   ```

## 监控与统计

- 当前涨跌停、涨跌家数、涨停开板和回封的独立股票数及事件次数。1、3、5、10 分钟窗口分别统计首次进入状态的股票数、重复进入次数和总进入次数。
- 1、3 分钟成交额与之前数个同长度窗口的平均成交速率比较。监控急涨、急跌及放量上涨。历史数据不足时只计算有足够样本的指标。
- 板块按覆盖成分股等权计算三分钟涨跌，监控同向拉升和急跌、近期触板和整体放量。记录领涨股先达到阈值后跟涨的时间顺序。重叠板块用异动股票集合做同批次去重。
- 检测引擎使用行情时间。启动、午休、断线、长间隔后的首条样本用作基线。开板和回封使用连续采样的涨停价状态变化，即使一只股票在启动前已涨停。首次和重复的分类从本进程开始观察，重启前的日内触板历史无法补齐。两次采样之间的快速开板和回封可能漏检。
- 播报按优先级和自选股加权，近时消息合并，连续异常只提醒一次，新增异常受冷却和队列限额约束。暂停清理等待播报的任务，正在播放的短句会正常结束。
- Dashboard 显示数据来源、连接状态、覆盖率、窗口统计、情绪曲线、排行榜和事件／播报记录。低覆盖时在界面标注，暂停全市场汇总播报。

## 设置、回放和诊断

修改 `config/config.yaml` 的默认值（使用 YAML 兼容的 JSON 写法）。Dashboard 可即时调整检测阈值、语速、音量、冷却时间、自选股等。界面改动优先加载，保存在 `var/settings.json`。关闭程序后移走该文件可重读默认值。记录保存在本地 SQLite，滚动保留最多 5,000 条，界面显示当前数据源最近 100 条。

在项目根目录执行以下命令。

```bat
.venv\Scripts\python.exe -m scripts.diagnose --source mock
.venv\Scripts\python.exe -m scripts.generate_mock var/replay.jsonl --count 5000 --frames 60
run_tests.bat
```

将 `.env.example` 复制为 `.env`，设置 `VOICE_SOURCE=replay` 和 `VOICE_REPLAY=var/replay.jsonl` 后重启可回放。每行是一批报价，包含 `quotes`、`expected` 和 `sectors`。模拟和回放使用加速的行情时钟，均有显式标签。模拟每天循环生成可复现的涨跌停、涨停开板、回封和板块异动。

通达信实盘需要 SDK、数据服务 Key 和行情权限，步骤及验收项见 [通达信行情接入与验收](docs/data-sources.md)。

## 项目布局

| 目录 | 职责 |
| --- | --- |
| `backend/` | 行情适配、缓存与检测、事件调度、HTTP 和 WebSocket |
| `frontend/` | React 界面和构建配置 |
| `config/` | 默认检测与播报参数 |
| `scripts/` | 语音播放、行情诊断、模拟录制 |
| `tests/` | 模拟全市场、行情连续性、语音调度、SDK 数据映射、WebSocket 与参数测试 |
| `docs/` | 数据源配置和验收说明 |
