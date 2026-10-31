#!/usr/bin/env python3
"""RepoGo original local Loki exercise. No account or third-party service."""
import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".runtime"
VERSION = "3.7.9"
ASSETS = {
    ("Darwin", "arm64"): ("loki-darwin-arm64.zip", "48acd15626d60ce357ac094c79d30eb9cc8be150b3b97e1360762506c3cd05a8"),
    ("Darwin", "x86_64"): ("loki-darwin-amd64.zip", "4f03d13ea194a81da1fbd82d053944da029769bd13216fda92419c6473416ef2"),
    ("Linux", "aarch64"): ("loki-linux-arm64.zip", "f46a623ff261d8579d09dcba545d48e14500bcfbc26caa59628ae465b3b67983"),
    ("Linux", "x86_64"): ("loki-linux-amd64.zip", "17a5fae28ce733b3560c27c76620920b169bd2aea9473947b733b511f92e81dd"),
    ("Windows", "amd64"): ("loki-windows-amd64.exe.zip", "b045b833e8df55b68af103952a0fad7b2c74f2e1fc82987722d3f53bb432580b"),
}


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request(base, path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(base + path, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as res:
        raw = res.read()
        return json.loads(raw) if raw.startswith(b"{") else raw.decode()


def binary():
    arch = platform.machine().lower()
    if arch == "amd64" and platform.system() != "Windows":
        arch = "x86_64"
    key = (platform.system(), arch)
    if key not in ASSETS:
        raise RuntimeError("此入口仅整理macOS arm64/x64、Linux arm64/x64、Windows x64；当前系统未适配")
    name, expected = ASSETS[key]
    archive = STATE / name
    if not archive.exists():
        print("从Grafana官方固定发行版下载：" + name, flush=True)
        temp = archive.with_suffix(".download")
        try:
            with urllib.request.urlopen("https://github.com/grafana/loki/releases/download/v" + VERSION + "/" + name, timeout=180) as res, temp.open("wb") as f:
                while True:
                    chunk = res.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            temp.replace(archive)
        finally:
            temp.unlink(missing_ok=True)
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError("官方下载包校验失败；请备份并移走.runtime下对应zip后重试")
    name_in_zip = name[:-4]
    target = STATE / name_in_zip
    # Re-extract the exact executable from the verified archive on each run.
    with zipfile.ZipFile(archive) as z:
        if z.namelist() != [name_in_zip]:
            raise RuntimeError("发行包内文件列表与固定版本不符")
        target.write_bytes(z.read(name_in_zip))
    target.chmod(0o755)
    return target, actual


def configure(port):
    store = (STATE / "data").resolve().as_posix()
    # Original local-only single-process config using documented filesystem/TSDB.
    cfg = f"""auth_enabled: false
server:
  http_listen_address: 127.0.0.1
  http_listen_port: {port}
  grpc_listen_address: 127.0.0.1
  grpc_listen_port: {port + 1}
  log_level: warn
common:
  instance_addr: 127.0.0.1
  path_prefix: {json.dumps(store)}
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
  storage:
    filesystem:
      chunks_directory: {json.dumps(store + '/chunks')}
      rules_directory: {json.dumps(store + '/rules')}
schema_config:
  configs:
    - from: 2020-10-24
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
ingester:
  wal:
    enabled: true
    dir: {json.dumps(store + '/wal')}
limits_config:
  ingestion_rate_mb: 2
  ingestion_burst_size_mb: 4
  max_query_parallelism: 2
  split_queries_by_interval: 0
analytics:
  reporting_enabled: false
"""
    path = STATE / "loki-local.yaml"
    path.write_text(cfg, encoding="utf-8")
    return path


def start(exe, config, base, log):
    # Refuse to operate on someone else's listener; no accidental shared instance.
    import socket
    port = urllib.parse.urlparse(base).port
    for candidate in (port, port + 1):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", candidate)) == 0:
                raise RuntimeError(f"端口{candidate}已占用；改用 --port 18750 等空闲连续两端口")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen([str(exe), "-config.file=" + str(config)], cwd=STATE, stdout=log, stderr=subprocess.STDOUT, creationflags=creationflags)
    deadline = time.monotonic() + 75
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("Loki启动退出，查看.runtime/loki.log")
            try:
                if request(base, "/ready").strip() == "ready":
                    return proc
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.5)
        raise RuntimeError("Loki75秒仍未就绪，查看.runtime/loki.log")
    except BaseException:
        stop(proc)
        raise


def stop(proc):
    if proc is None or proc.poll() is not None:
        return
    # Loki handles SIGINT/SIGTERM as graceful shutdown on supported desktop OS.
    if os.name == "nt":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=45)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        raise RuntimeError("Loki未能优雅退出；已停止，本轮持久化不能记通过")


def query(base, expression, start_ns, end_ns, metric=False):
    params = {"query": expression, "time": str(end_ns)} if metric else {"query": expression, "start": str(start_ns), "end": str(end_ns), "limit": "100", "direction": "forward"}
    result = request(base, "/loki/api/v1/" + ("query" if metric else "query_range") + "?" + urllib.parse.urlencode(params))
    if result.get("status") != "success":
        raise RuntimeError("Loki查询未成功")
    return result


def lines(result):
    return [json.loads(v[1]) for stream in result["data"]["result"] for v in stream["values"]]


def demo(args):
    STATE.mkdir(exist_ok=True)
    results = STATE / "results"
    results.mkdir(exist_ok=True)
    exe, digest = binary()
    cfg = configure(args.port)
    base = f"http://127.0.0.1:{args.port}"
    run = uuid.uuid4().hex[:12]
    begin = time.time_ns() - 20_000_000_000
    proc = None
    log = (STATE / "loki.log").open("ab")
    snapshots = {}
    original_rows = {}
    try:
        proc = start(exe, cfg, base, log)
        for phase in ("before", "after"):
            samples = [json.loads(s) for s in (ROOT / "samples" / (phase + ".jsonl")).read_text(encoding="utf-8").splitlines() if s.strip()]
            original_rows[phase] = samples
            origin = time.time_ns() - 10_000_000_000
            values = [[str(origin + n * 1_000_000), json.dumps(dict(row, run=run), ensure_ascii=False)] for n, row in enumerate(samples)]
            request(base, "/loki/api/v1/push", {"streams": [{"stream": {"app": "repogo_orders", "env": "practice", "phase": phase}, "values": values}]})
            selector = '{app="repogo_orders",env="practice",phase="' + phase + '"} | json | run="' + run + '"'
            error_query = selector + ' | level="error"'
            metric_query = 'sum by (reason) (count_over_time(' + error_query + '[10m]))'
            end = time.time_ns()
            all_rows = query(base, selector, begin, end)
            failed = query(base, error_query, begin, end)
            counts = query(base, metric_query, begin, end, metric=True)
            for name, result in (("all", all_rows), ("errors", failed), ("counts", counts)):
                write_json(results / (phase + "-" + name + ".json"), result)
            actual_errors = sorted(r["order"] for r in lines(failed))
            expected_errors = sorted(r["order"] for r in samples if r["level"] == "error")
            expected_reasons = {}
            for r in samples:
                if r["level"] == "error":
                    expected_reasons[r["reason"]] = expected_reasons.get(r["reason"], 0) + 1
            actual_reasons = {r["metric"]["reason"]: int(float(r["value"][1])) for r in counts["data"]["result"]}
            if len(lines(all_rows)) != len(samples) or actual_errors != expected_errors or actual_reasons != expected_reasons:
                raise RuntimeError("真实查询与输入日志不一致；本轮不计通过")
            snapshots[phase] = {"total": len(samples), "error_orders": actual_errors, "reasons": actual_reasons, "query": error_query, "metric_query": metric_query, "selector": selector}
            print(phase + "：" + json.dumps(snapshots[phase], ensure_ascii=False), flush=True)
        stop(proc)
        proc = None
        proc = start(exe, cfg, base, log)
        for phase, expected in snapshots.items():
            readback = query(base, expected["selector"], begin, time.time_ns())
            write_json(results / (phase + "-after-restart.json"), readback)
            actual_rows = [{k: v for k, v in row.items() if k != "run"} for row in lines(readback)]
            canonical = lambda rows: sorted(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows)
            if canonical(actual_rows) != canonical(original_rows[phase]):
                raise RuntimeError("重启后日志内容不一致；持久化未通过")
        stop(proc)
        proc = None
        summary = {"passed": True, "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "version": VERSION, "platform": platform.platform(), "archive_sha256": digest, "base_url": base, "run": run, "scenarios": snapshots, "restart_readback": {"before": 6, "after": 6}, "process_stopped": True, "note": "原创假订单日志；after是修改样例后新写入的一组记录，未改写历史日志，也未修复真实支付系统"}
        write_json(results / "summary.json", summary)
        print("通过：失败原因2+1→1；退出并重启后两组各6条仍可查询。服务已停止。结果：" + str(results), flush=True)
    finally:
        stop(proc)
        log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RepoGo Loki 中文本地日志练习")
    parser.add_argument("--port", type=int, default=18740, help="仅本机HTTP端口；另使用相邻gRPC端口")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65534:
        parser.error("端口应在1024—65534之间")
    try:
        demo(args)
    except (Exception, KeyboardInterrupt) as exc:
        print("未完成：" + str(exc), file=sys.stderr)
        sys.exit(1)
