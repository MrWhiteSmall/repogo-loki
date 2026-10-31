# 从自动练习走到自己查询

## 系统与位置

|系统|入口|本期状态|
|---|---|---|
|macOS Apple Silicon|`bash start.sh`，Python3.9+|已实测完整核心流程|
|macOS Intel|同上，下载 darwin-amd64|未实测|
|Linux x64 / ARM64|同上，下载 linux-amd64 / arm64|未实测；没有把本机测试冒充 shenji 测试|
|Windows x64|`start.cmd` / `py -3 run.py` / `start.ps1`|已整理未实测，包含退出信号处理但未验收|
|Windows ARM64|无原生映射|本入口未适配；不承诺x64仿真|

全部路径相对 `run.py` 所在目录，不要求固定磁盘位置。`.runtime` 包含官方程序ZIP、解压程序、`loki-local.yaml`、数据目录`data`、`loki.log`和查询结果`results`。启动器默认只用127.0.0.1的HTTP18740和gRPC18741，配置不含认证，勿改成公网或内网共享地址。查询并行度和写入速率有小型练习配置，但没有设置进程内存/CPU硬上限。

## 自动启动、停止与重跑

`bash start.sh` 完成整套练习后停止；不是启动后常驻的网页服务。用 `python3 run.py --port 18750` 改为连续两端口18750/18751。Windows用`py -3 run.py --port 18750`。

正常情况下退出由脚本负责；运行中可 Ctrl+C，脚本会请求 Loki 正常退出。强制杀进程可能损坏当轮验收，因此脚本遇到不能正常退出会报失败。重跑不会清空历史数据，只覆盖本轮结果文件，并用新run标记隔离查询。请先复制需要保留的结果再重跑。

## 手动启动服务

先成功完成一次练习，以生成并校验下载程序和配置。然后在交付根目录选择对应命令，不要同时运行自动练习和手动实例。

Mac Apple Silicon：
```bash
cd .runtime
./loki-darwin-arm64 -config.file=loki-local.yaml
```
Mac Intel把程序名换为`loki-darwin-amd64`；Linux x64为`loki-linux-amd64`、ARM64为`loki-linux-arm64`。Windows PowerShell：
```powershell
Set-Location .runtime
.\loki-windows-amd64.exe -config.file=loki-local.yaml
```

这些非Mac ARM64手动命令来自固定发行包命名与原程序参数，未实测。出现启动错误先看当前终端。默认地址`http://127.0.0.1:18740/ready`显示`ready`只代表服务就绪，不代表写入或查询已经成功；根路径没有网页也属正常。本练习没有图形看板。

## 自己发一次查询

服务保持运行，在另一个终端回到交付根目录，复制下列Python命令。它查询最近一小时所有练习失败日志，不限定run；若反复跑过会看到多个轮次：

```python
# 保存为 query-local.py，再用 python3 query-local.py（Windows: py -3 query-local.py）
import json, time, urllib.parse, urllib.request
expression = '{app="repogo_orders",env="practice",phase="before"} | json | level="error"'
params = {"query": expression, "start": str(time.time_ns()-3600*10**9), "end": str(time.time_ns()), "limit": "100", "direction": "forward"}
url = "http://127.0.0.1:18740/loki/api/v1/query_range?" + urllib.parse.urlencode(params)
with urllib.request.urlopen(url, timeout=20) as response:
    print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
```

若要只看最近一轮，将`expression`换为`summary.json`里`scenarios.before.query`的完整字符串。LogQL是Loki的查询语言：花括号先选择“哪个应用、环境和阶段”；`| json`把每行JSON读成字段；`| level="error"`只保留失败记录。再加上`run="本轮标记"`就能避开历史轮次。

归类统计用`sum by (reason) (count_over_time(筛选表达式[10m]))`：先在十分钟窗口内计数，再合并相同原因。完整可复制的实际表达式在`summary.json`的`metric_query`。窗口是时间限制，不是“全部历史”的同义词；超过窗口会为空。程序不自动采集真实应用的文件；接入实际日志要另配官方支持的采集方案（当前上游指向Alloy），不把本包称为完整生产日志平台。

## 停止、备份与升级

手动服务在它自己的终端按Ctrl+C，等进程确实退出再备份`.runtime/data`，同时保存`loki-local.yaml`、当前版本和结果。日志可能包含业务隐私；本练习只有虚构样例，接真实数据前自行做脱敏、访问控制和留存规划。

本期固定3.7.9，下载归档SHA256来自官方发行记录并核对官方校验文件。不要只改VERSION跳过摘要校验；新版本需重新读配置与迁移说明、重新验证核心流程。恢复备份时应在停止状态使用相同版本和配置，先在副本测试，勿覆盖唯一数据。`data`和原生日志不进入Gitee及会员附件。
