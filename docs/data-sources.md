# 通达信行情接入与验收

应用默认使用模拟源。实盘模式使用通达信官方 Python SDK 的批量快照轮询。以下是文档能力和接入方式，需使用自己的数据服务 Key 在交易时段核验。

| 能力 | 官方接口 | 本项目用法 |
| --- | --- | --- |
| 沪深京 A 股 | `get_stock_list("5", list_type=1)` | 使用沪、深、京后缀，统计三市请求股票数 |
| 实时价格与成交额 | `get_market_snapshot_batch` | 分批请求现价、昨收、刷新时刻及累计成交额。文档样例对应万元，适配器转成元 |
| 涨跌停价格 | `get_more_info_batch` | 同日 `HqDate`、`ZTPrice`、`DTPrice`。缺字段降低覆盖率。双侧为零表示无有效价格限制 |
| 行业和概念板块 | `get_sector_list`、`get_stock_list_in_sector` | 扫描可获取板块并与 A 股池取交集，也可配置经过核验的本地板块映射 |
| 实时订阅 | `subscribe`、`unsubscribe` | 诊断工具支持单只样本测试。实时应用使用批量快照，全市场订阅容量需要另行向供应商和实盘测试确认 |
| 交易日历 | `get_trading_dates` | 用官方日历过滤假日，按北京时间切分上午和下午时段 |

[官方 SDK 说明](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114.html) · [快照及扩展数据字段](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlir5r0t9hgc.html) · [实时订阅](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbidnohpjn4/mindoc-1hlsf0u13l0d4.html) · [板块成分接口](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1ctuhttn72svo/mindoc-1h10r92mchgug.html)

## 配置与诊断

1. 完成 README 的模拟模式安装。使用相同 Python 环境安装 SDK。
   ```bat
   .venv\Scripts\python.exe -m pip install tdxaidata -i https://pypi.tuna.tsinghua.edu.cn/simple
   ```
2. 在通达信会员中心申请对应权限的**数据服务 Key**，按 SDK 文档填写其 `TdxAiData.ini`，避免将 Key 写入项目文件。SDK 无需打开通达信终端。可用权限、收费和调用限制以账号实际权限为准。
3. 在交易时段运行诊断。
   ```bat
   .venv\Scripts\python.exe -m scripts.diagnose --source tdx --rounds 3 --sectors --subscribe
   ```
   记录沪深京代码数量、有效板块数量、涨跌停字段覆盖、每次扫盘时间、45 秒内新鲜样本数和 P50/P95 行情延迟。单只订阅成功只说明样本收到回调。收盘后刷新时间和覆盖率不代表盘中性能。
4. 实际覆盖和延迟符合需要后，将 `.env` 的 `VOICE_SOURCE` 设为 `tdx`。可配置 `VOICE_TDX_CHUNK` 和 `VOICE_TDX_INTERVAL`。后者是两轮扫描之间的等待秒数，实际频率还受单轮耗时影响。
5. 可选 `VOICE_TDX_SECTORS=var/tdx-sectors.json`，指向形如 `{"板块名称":["600000.SH","000001.SZ"]}` 的 UTF-8 JSON，适用于 SDK 板块权限不足或需要只监控部分板块的场景。

SDK 快照只有时分秒，因此适配器要求扩展数据报告同一交易日，避免把前一日价格当成今天。无可用记录或权限异常触发退避重连。停牌优先使用当日停牌元数据与零成交确认，过期或无快照股票降低覆盖率。真实接口的成交额单位、无涨跌幅样本、停牌状态、板块映射、权限、全市场可用容量和延迟都需要盘中对照通达信验证。本仓库的 SDK 自动化测试使用文档结构的桩数据。
