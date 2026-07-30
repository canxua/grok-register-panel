#!/usr/bin/env python3
"""Grok register batch live monitor — bind Tailscale, control + blacklist panel."""
from __future__ import annotations

import json
import ipaddress
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from secure_files import atomic_write_json, ensure_private_dir

try:
    from webui.blacklist_store import read_blacklist as read_blacklist_state
    from webui.process_utils import (
        find_managed_processes,
        terminate_managed_processes,
        write_pid_file,
    )
    from webui.recovery_ops import recovery_status, start_recovery, stop_recovery
    from webui.security_utils import (
        check_token_optional_read,
        expected_token,
        mask_email,
        redact_log_line,
        redact_proxy,
    )
except ImportError:  # running as script from webui/
    from blacklist_store import read_blacklist as read_blacklist_state  # type: ignore
    from process_utils import (  # type: ignore
        find_managed_processes,
        terminate_managed_processes,
        write_pid_file,
    )
    from recovery_ops import recovery_status, start_recovery, stop_recovery  # type: ignore
    from security_utils import (  # type: ignore
        check_token_optional_read,
        expected_token,
        mask_email,
        redact_log_line,
        redact_proxy,
    )
LOG_DIR = ROOT / "log"
CPA_DIR = Path(os.environ.get("CPA_AUTH_DIR", str(ROOT / "cpa_auth")))
ASSET_DIR = Path(__file__).resolve().parent / "assets"
FONT_ASSETS = {
    "/assets/geist.woff2": ASSET_DIR / "geist-latin-wght-normal.woff2",
    "/assets/geist-mono.woff2": ASSET_DIR / "geist-mono-latin-wght-normal.woff2",
}
MONITOR_TOKEN_ENV = "MONITOR_TOKEN"
PANEL_INCLUDE_TAIL = os.environ.get("PANEL_INCLUDE_TAIL", "0").strip() in ("1", "true", "yes")

BASE_FILE = LOG_DIR / "batch1000.base"
ORCH_PID = LOG_DIR / "orch100.pid"
BATCH_PID = LOG_DIR / "batch100.pid"
CONTROL_FILE = LOG_DIR / "monitor_control.json"
STATS_CACHE = LOG_DIR / "monitor_stats.json"
BIND_HOST = os.environ.get("MONITOR_HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("MONITOR_PORT", "8787"))
VENV_PY = ROOT / ".venv/bin/python"
ORCH_SCRIPT = ROOT / "run_until_100.py"
CONTROL_LOCK = threading.RLock()
START_LOCK = threading.Lock()
MAX_REQUEST_BODY = 64 * 1024

RE_OK = re.compile(r"\[\+\] 注册成功")
RE_FAIL = re.compile(r"\[-\] 失败")
RE_DOMAIN = re.compile(r"\[-\] 域名拒绝")
RE_SKIP = re.compile(r"\[-\] 卡住跳过")
RE_BOT0 = re.compile(r"botFlagSource=0")
RE_BOT1 = re.compile(r"botFlagSource=1")
RE_EMAIL_OK = re.compile(r"\[\+\] 注册成功(?:（[^）]*）)?:\s*(\S+)")
RE_FAIL_KIND = re.compile(r"\[-\] 失败 \[([^\]]+)\]:\s*(.*)")
RE_WORKER = re.compile(r"\[W(\d+)\]")
RE_BATCH = re.compile(r"\[batch\] count=(\d+) workers=(\d+)")
RE_START = re.compile(r"终端模式启动，目标数量:\s*(\d+)\s*\|\s*并发:\s*(\d+)")
RE_END = re.compile(r"任务结束。成功\s*(\d+)\s*\|\s*失败\s*(\d+)")
RE_ADDED_BL = re.compile(r"ADDED blacklist AS(\d+)")
RE_LOOKUP_FAIL = re.compile(r"lookup fail", re.I)
RE_ANALYZE_ERR = re.compile(r"analyze error", re.I)


def _read_json(path: Path, default=None):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        pass
    return default if default is not None else {}


def _write_json(path: Path, data: dict):
    atomic_write_json(path, data)


def load_control() -> dict:
    with CONTROL_LOCK:
        c = _read_json(CONTROL_FILE, {})
        c.setdefault("workers", 1)
        c.setdefault("risk_pause", 10)
        c.setdefault("batch_count", 1)
        c.setdefault("add_count", 1)  # 再跑 N 个
        c.setdefault("mode", "batch")  # orch | batch
        return c


def save_control(updates: dict) -> dict:
    allowed = {
        "workers",
        "risk_pause",
        "batch_count",
        "add_count",
        "mode",
        "base_cpa",
        "target_cpa",
    }
    with CONTROL_LOCK:
        c = load_control()
        c.update({key: value for key, value in (updates or {}).items() if key in allowed})
        try:
            c["workers"] = max(1, min(24, int(c.get("workers", 1))))
        except Exception:
            c["workers"] = 1
        try:
            c["risk_pause"] = max(1, min(50, int(c.get("risk_pause", 10))))
        except Exception:
            c["risk_pause"] = 10
        try:
            c["batch_count"] = max(1, min(200, int(c.get("batch_count", 1))))
        except Exception:
            c["batch_count"] = 1
        try:
            c["add_count"] = max(1, min(500, int(c.get("add_count", 1))))
        except Exception:
            c["add_count"] = 1
        c["mode"] = c.get("mode") if c.get("mode") in ("orch", "batch") else "batch"
        for key in ("base_cpa", "target_cpa"):
            if c.get(key) is None or str(c.get(key)).strip() == "":
                c.pop(key, None)
                continue
            try:
                c[key] = max(0, int(c[key]))
            except Exception:
                c.pop(key, None)
        c["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_json(CONTROL_FILE, c)
        return c


def discover_log():
    env = os.environ.get("BATCH_LOG")
    if env and Path(env).is_file():
        return Path(env)
    cands = sorted(LOG_DIR.glob("batch*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    cands = [p for p in cands if "sticky" not in p.name and "rotate" not in p.name]
    return cands[0] if cands else None


def read_base():
    """Prefer control.base_cpa; fall back to batch1000.base file if present."""
    try:
        c = load_control()
        if c.get("base_cpa") is not None and str(c.get("base_cpa")).strip() != "":
            return int(c["base_cpa"])
    except Exception:
        pass
    try:
        return int(BASE_FILE.read_text().strip())
    except Exception:
        return 0


def process_running():
    """Detect orch and/or batch workers."""
    info = {
        "running": False,
        "pid": None,
        "etime": None,
        "cmd": None,
        "orch_running": False,
        "orch_pid": None,
        "orch_etime": None,
        "batch_running": False,
        "batch_pid": None,
        "batch_etime": None,
    }
    orch = find_managed_processes(ROOT, ("run_until_100.py",))
    batch = find_managed_processes(ROOT, ("run_batch_headless.py",))

    def primary(items):
        if not items:
            return None
        return next((item for item in items if item.get("pgid") == item.get("pid")), items[0])

    orch_item = primary(orch)
    batch_item = primary(batch)
    if orch_item:
        info["orch_running"] = True
        info["orch_pid"] = orch_item["pid"]
        info["orch_etime"] = orch_item.get("etime")
        info["running"] = True
        info["pid"] = orch_item["pid"]
        info["etime"] = orch_item.get("etime")
        info["cmd"] = orch_item.get("cmd")
    if batch_item:
        info["batch_running"] = True
        info["batch_pid"] = batch_item["pid"]
        info["batch_etime"] = batch_item.get("etime")
        if not info["running"]:
            info["running"] = True
            info["pid"] = batch_item["pid"]
            info["etime"] = batch_item.get("etime")
            info["cmd"] = batch_item.get("cmd")
    return info


def parse_log(path, max_tail=400_000):
    if not path or not path.is_file():
        return {"error": "no log"}
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_tail:
            f.seek(size - max_tail)
            f.readline()
        text = f.read().decode("utf-8", errors="replace")

    lines = text.splitlines()
    ok = fail = domain = skip = bot0 = bot1 = 0
    count = workers = None
    ended = None
    recent_ok = []
    recent_fail = []
    fail_kinds = {}
    worker_ok = {}
    worker_fail = {}

    for line in lines:
        m = RE_BATCH.search(line) or RE_START.search(line)
        if m:
            count, workers = int(m.group(1)), int(m.group(2))
        m = RE_END.search(line)
        if m:
            ended = {"success": int(m.group(1)), "fail": int(m.group(2))}

        if RE_OK.search(line):
            ok += 1
            em = RE_EMAIL_OK.search(line)
            email = em.group(1) if em else ""
            wm = RE_WORKER.search(line)
            w = f"W{wm.group(1)}" if wm else "?"
            worker_ok[w] = worker_ok.get(w, 0) + 1
            ts = line[1:9] if line.startswith("[") else ""
            recent_ok.append({"t": ts, "w": w, "email": mask_email(email)})
        if RE_FAIL.search(line):
            fail += 1
            fm = RE_FAIL_KIND.search(line)
            kind = fm.group(1) if fm else "其它"
            msg = fm.group(2) if fm else line[-120:]
            if "inputs=none" in msg:
                kind = "空页UI"
            if "Turnstile" in msg or "Turnstile" in kind:
                kind = "资料页Turnstile" if "Turnstile" in msg else kind
            fail_kinds[kind] = fail_kinds.get(kind, 0) + 1
            wm = RE_WORKER.search(line)
            w = f"W{wm.group(1)}" if wm else "?"
            worker_fail[w] = worker_fail.get(w, 0) + 1
            ts = line[1:9] if line.startswith("[") else ""
            recent_fail.append({"t": ts, "w": w, "kind": kind, "msg": redact_log_line(msg[:160])})
        if RE_DOMAIN.search(line):
            domain += 1
        if RE_SKIP.search(line):
            skip += 1
        if RE_BOT0.search(line):
            bot0 += 1
        if RE_BOT1.search(line):
            bot1 += 1

    last_lines = lines[-40:]
    if size > max_tail:
        def gcount(pat):
            r = subprocess.run(["grep", "-c", pat, str(path)], capture_output=True, text=True)
            try:
                return int(r.stdout.strip() or 0)
            except Exception:
                return 0

        ok = gcount("注册成功")
        fail = gcount(r"\[-\] 失败")
        bot0 = gcount("botFlagSource=0")
        bot1 = gcount("botFlagSource=1")

    return {
        "log": path.name,
        "log_name": path.name,
        "log_size": size,
        "mtime": path.stat().st_mtime,
        "count_target": count,
        "workers": workers,
        "ok": ok,
        "fail": fail,
        "domain": domain,
        "skip": skip,
        "bot0": bot0,
        "bot1": bot1,
        "ended": ended,
        "fail_kinds": fail_kinds,
        "worker_ok": worker_ok,
        "worker_fail": worker_fail,
        "recent_ok": recent_ok[-25:][::-1],
        "recent_fail": recent_fail[-25:][::-1],
        "tail": [redact_log_line(line) for line in last_lines],
    }


def cpa_count():
    try:
        return sum(1 for p in CPA_DIR.iterdir() if p.is_file() and p.name.startswith("xai-"))
    except Exception:
        try:
            return sum(1 for _ in CPA_DIR.iterdir() if _.is_file())
        except Exception:
            return 0


def read_blacklist():
    return read_blacklist_state()


def blacklist_update_errors():
    """Count blacklist expansion / ASN lookup errors from orch logs."""
    added = []
    lookup_fails = 0
    analyze_errors = 0
    hit_pause = 0
    try:
        logs = sorted(LOG_DIR.glob("orch100*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]
        logs += sorted(LOG_DIR.glob("orch100-stdout.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:1]
        seen = set()
        for path in logs:
            if str(path) in seen or not path.is_file():
                continue
            seen.add(str(path))
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for line in text.splitlines():
                m = RE_ADDED_BL.search(line)
                if m:
                    added.append({"asn": int(m.group(1)), "line": line[-120:], "log": path.name})
                if RE_LOOKUP_FAIL.search(line):
                    lookup_fails += 1
                if RE_ANALYZE_ERR.search(line):
                    analyze_errors += 1
                if "pause+blacklist" in line or "HIT" in line and "注册风控" in line:
                    hit_pause += 1
    except Exception:
        pass
    # unique recent added (last 30)
    uniq = []
    seen_a = set()
    for a in reversed(added):
        if a["asn"] in seen_a:
            continue
        seen_a.add(a["asn"])
        uniq.append(a)
        if len(uniq) >= 30:
            break
    uniq.reverse()
    return {
        "lookup_fail_count": lookup_fails,
        "analyze_error_count": analyze_errors,
        "error_count": lookup_fails + analyze_errors,
        "hit_pause_count": hit_pause,
        "recent_added": uniq[-15:],
        "added_total": len(added),
    }


def success_stats():
    """Aggregate success stats: CPA + jsonl + time-window rates + latest batch."""
    from datetime import datetime, timezone, timedelta

    cpa = cpa_count()
    configured_base = read_base()
    base_stale = configured_base < 0 or configured_base > cpa
    base = cpa if base_stale else configured_base
    jsonl_ok = 0
    jsonl_risk = 0
    jsonl_fail = 0
    by_day = {}
    results = LOG_DIR / "register_results.jsonl"

    # windows in hours -> counters
    windows_h = (1, 3, 12)
    now = datetime.now(timezone.utc)
    win = {
        h: {"ok": 0, "fail": 0, "risk": 0, "total": 0, "since": (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        for h in windows_h
    }

    def _parse_ts(ts: str):
        if not ts:
            return None
        s = str(ts).strip()
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    try:
        if results.exists():
            size = results.stat().st_size
            # last 8MB covers 12h under high volume
            with results.open("rb") as f:
                if size > 8_000_000:
                    f.seek(size - 8_000_000)
                    f.readline()
                for line in f:
                    try:
                        o = json.loads(line.decode("utf-8", errors="replace"))
                    except Exception:
                        continue
                    st = o.get("status")
                    day = (o.get("ts") or "")[:10]
                    if day:
                        by_day.setdefault(day, {"ok": 0, "risk": 0, "fail": 0})
                    if st == "ok":
                        jsonl_ok += 1
                        if day:
                            by_day[day]["ok"] += 1
                    elif st == "risk":
                        jsonl_risk += 1
                        if day:
                            by_day[day]["risk"] += 1
                    elif st:
                        jsonl_fail += 1
                        if day:
                            by_day[day]["fail"] += 1

                    dt = _parse_ts(o.get("ts") or "")
                    if not dt:
                        continue
                    age = now - dt
                    for h in windows_h:
                        if age <= timedelta(hours=h):
                            bucket = win[h]
                            if st == "ok":
                                bucket["ok"] += 1
                            elif st == "risk":
                                bucket["risk"] += 1
                            elif st:
                                bucket["fail"] += 1
                            if st in ("ok", "risk", "fail", "sso_timeout", "browser", "other"):
                                bucket["total"] += 1
                            elif st:
                                bucket["total"] += 1
    except Exception:
        pass

    # normalize window rates
    rates = {}
    for h, b in win.items():
        # total attempts that finished with a status
        total = int(b["ok"]) + int(b["fail"]) + int(b["risk"])
        ok = int(b["ok"])
        rate = round(100.0 * ok / total, 1) if total else None
        rates[f"{h}h"] = {
            "hours": h,
            "ok": ok,
            "fail": int(b["fail"]),
            "risk": int(b["risk"]),
            "total": total,
            "success_rate": rate,
            "since": b["since"],
        }

    log = discover_log()
    parsed = parse_log(log) if log else {}
    batch_ok = parsed.get("ok") or 0
    batch_fail = parsed.get("fail") or 0
    data = {
        "cpa": cpa,
        "base_cpa": base,
        "base_cpa_stale": base_stale,
        "cpa_delta": cpa - base,
        "jsonl_ok": jsonl_ok,
        "jsonl_risk": jsonl_risk,
        "jsonl_fail": jsonl_fail,
        "batch_ok": batch_ok,
        "batch_fail": batch_fail,
        "batch_log": parsed.get("log_name"),
        "by_day": by_day,
        "rates": rates,
        "refreshed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        _write_json(STATS_CACHE, data)
    except Exception:
        pass
    return data




def _parse_etime(s):
    if not s:
        return None
    s = s.strip()
    try:
        days = 0
        if "-" in s:
            d, s = s.split("-", 1)
            days = int(d)
        parts = [int(x) for x in s.split(":")]
        if len(parts) == 3:
            h, m, sec = parts
        elif len(parts) == 2:
            h = 0
            m, sec = parts
        else:
            return None
        return days * 86400 + h * 3600 + m * 60 + sec
    except Exception:
        return None


def kill_all():
    """Stop only orchestrator and batch processes under this project root."""
    killed = terminate_managed_processes(
        ROOT,
        ("run_until_100.py", "run_batch_headless.py"),
    )
    return {"ok": True, "killed": killed}


def _runtime_prerequisite_error() -> str | None:
    if not VENV_PY.is_file():
        return f"missing runtime python: {VENV_PY}"
    if not (ROOT / "config.json").is_file():
        return f"missing config: {ROOT / 'config.json'}"
    return None


def _start_orch_unlocked():
    proc = process_running()
    if proc.get("orch_running") or proc.get("batch_running"):
        return {"ok": False, "error": "already running", "process": proc}
    if find_managed_processes(ROOT, ("sso_to_auth_json.py",)):
        return {"ok": False, "error": "account recovery is running"}
    prerequisite_error = _runtime_prerequisite_error()
    if prerequisite_error:
        return {"ok": False, "error": prerequisite_error}
    c = load_control()
    now = cpa_count()
    add_count = c.get("add_count")
    try:
        add_count = int(add_count) if add_count is not None else 0
    except Exception:
        add_count = 0
    target = c.get("target_cpa")
    try:
        target = int(target) if target is not None else None
    except Exception:
        target = None
    if add_count > 0:
        c["base_cpa"] = now
        c["target_cpa"] = now + add_count
    elif target is None or target <= now:
        n = int(c.get("batch_count") or 1)
        c["add_count"] = n
        c["base_cpa"] = now
        c["target_cpa"] = now + n
        add_count = n
    c = save_control(c)
    need = int(c.get("target_cpa") or 0) - now
    ensure_private_dir(LOG_DIR)
    stdout_path = LOG_DIR / "orch100-stdout.log"
    fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    stdout = os.fdopen(fd, "a", encoding="utf-8")
    stdout.write(
        f"\n--- monitor start {time.strftime('%Y-%m-%dT%H:%M:%SZ')} "
        f"workers={c.get('workers')} cpa={now} target={c.get('target_cpa')} need={need} ---\n"
    )
    stdout.flush()
    try:
        p = subprocess.Popen(
            [str(VENV_PY), "-u", str(ORCH_SCRIPT)],
            cwd=str(ROOT),
            stdout=stdout,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    finally:
        stdout.close()
    write_pid_file(ORCH_PID, p.pid)
    return {
        "ok": True,
        "pid": p.pid,
        "mode": "orch",
        "workers": c.get("workers"),
        "cpa_now": now,
        "target_cpa": c.get("target_cpa"),
        "need": need,
        "add_count": add_count or c.get("add_count"),
        "control": c,
        "message": f"已启动 orch pid={p.pid} 目标 CPA {c.get('target_cpa')} (再跑 {need})",
    }


def start_orch():
    with START_LOCK:
        return _start_orch_unlocked()



def _start_batch_only_unlocked():
    proc = process_running()
    if proc.get("batch_running") or proc.get("orch_running"):
        return {"ok": False, "error": "already running", "process": proc}
    if find_managed_processes(ROOT, ("sso_to_auth_json.py",)):
        return {"ok": False, "error": "account recovery is running"}
    prerequisite_error = _runtime_prerequisite_error()
    if prerequisite_error:
        return {"ok": False, "error": prerequisite_error}
    c = load_control()
    workers = int(c.get("workers") or 1)
    count = int(c.get("batch_count") or 1)
    now = cpa_count()
    c["base_cpa"] = now
    c["target_cpa"] = now + count
    c = save_control(c)
    logname = LOG_DIR / f"batch-orch-{time.strftime('%Y%m%d-%H%M%S')}-n{count}.log"
    ensure_private_dir(LOG_DIR)
    fd = os.open(logname, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    fout = os.fdopen(fd, "w", encoding="utf-8")
    try:
        p = subprocess.Popen(
            [
                "xvfb-run", "-a", "-s", "-screen 0 1920x1080x24",
                str(VENV_PY), "-u", str(ROOT / "run_batch_headless.py"),
                str(count), str(workers),
            ],
            cwd=str(ROOT),
            stdout=fout,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        fout.close()
    write_pid_file(BATCH_PID, p.pid)
    return {
        "ok": True,
        "pid": p.pid,
        "mode": "batch",
        "workers": workers,
        "count": count,
        "log": logname.name,
    }


def start_batch_only():
    with START_LOCK:
        return _start_batch_only_unlocked()


def snapshot():
    log = discover_log()
    parsed = parse_log(log) if log else {"error": "no log"}
    cpa = cpa_count()
    configured_base = read_base()
    base_stale = configured_base < 0 or configured_base > cpa
    base = cpa if base_stale else configured_base
    proc = process_running()
    control = load_control()
    bl = read_blacklist()
    bl_err = blacklist_update_errors()
    try:
        rates = success_stats().get("rates") or {}
    except Exception:
        rates = {}
    target = parsed.get("count_target") or control.get("batch_count") or 1
    ok = parsed.get("ok") or 0
    fail = parsed.get("fail") or 0
    done = ok + fail
    pct = round(100.0 * ok / target, 2) if target else 0
    eta = None
    rate_per_min = None
    etime = proc.get("etime") or proc.get("batch_etime") or ""
    secs = _parse_etime(etime)
    if secs and ok > 0:
        rate_per_min = round(ok / (secs / 60.0), 2)
        remain = max(target - ok, 0)
        if rate_per_min > 0:
            eta_min = remain / rate_per_min
            eta = f"{int(eta_min)}m" if eta_min < 120 else f"{eta_min/60:.1f}h"
    workers_show = parsed.get("workers") or control.get("workers")
    return {
        "ts": time.time(),
        "ts_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_cpa": base,
        "base_cpa_stale": base_stale,
        "cpa": cpa,
        "cpa_delta": cpa - base,
        "process": proc,
        "control": control,
        "target": target,
        "done_attempts": done,
        "progress_pct": pct,
        "success_rate": round(100.0 * ok / done, 1) if done else None,
        "rate_per_min": rate_per_min,
        "eta": eta,
        "blacklist": {
            "count": bl.get("count"),
            "asns": bl.get("asns"),
            "items": bl.get("items"),
            "isp_keywords": bl.get("isp_keywords"),
            "mtime_human": bl.get("mtime_human"),
            "ok": bl.get("ok"),
            "error": bl.get("error"),
            "errors": bl.get("errors"),
        },
        "blacklist_update": bl_err,
        "rates": rates,
        **{k: v for k, v in parsed.items() if k != "tail"},
        "workers": workers_show,
        "tail": (parsed.get("tail") or []) if PANEL_INCLUDE_TAIL else ["(raw log tail disabled; set PANEL_INCLUDE_TAIL=1)"],
    }


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="theme-color" content="#f3f4f1" id="theme-color"/>
<title>GrokRegister</title>
<script>
  (function () {
    const key = "GROK_REGISTER_THEME";
    let theme = "";
    try { theme = localStorage.getItem(key) || ""; } catch (e) {}
    if (theme !== "light" && theme !== "dark") {
      theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    document.documentElement.dataset.theme = theme;
    document.getElementById("theme-color").content = theme === "dark" ? "#171815" : "#f3f4f1";
  })();
</script>
<style>
  @font-face {
    font-family: "Geist";
    src: url("/assets/geist.woff2") format("woff2");
    font-style: normal;
    font-weight: 100 900;
    font-display: swap;
  }
  @font-face {
    font-family: "Geist Mono";
    src: url("/assets/geist-mono.woff2") format("woff2");
    font-style: normal;
    font-weight: 100 900;
    font-display: swap;
  }
  :root {
    color-scheme: light;
    --bg: #f3f4f1;
    --surface: #e9eae6;
    --surface-raised: #f8f9f6;
    --surface-soft: #eff0ec;
    --surface-deep: #d9dad5;
    --border: rgba(21, 22, 19, .16);
    --border-strong: rgba(21, 22, 19, .46);
    --text: #151613;
    --text-secondary: #383a35;
    --muted: #696b64;
    --placeholder: #85877f;
    --ok: #237a57;
    --fail: #b83f3f;
    --warn: #8a6400;
    --accent: #b93b28;
    --accent-hover: #9f2f1f;
    --accent-ink: #f8f9f6;
    --focus: #b93b28;
    --button: #f8f9f6;
    --button-hover: #e1e2dd;
    --hover-border: rgba(21, 22, 19, .46);
    --focus-shadow: rgba(185, 59, 40, .16);
    --primary-bg: #151613;
    --primary-text: #f8f9f6;
    --primary-hover: #2e302b;
    --danger-border: rgba(184, 63, 63, .45);
    --danger-hover-bg: rgba(184, 63, 63, .08);
    --danger-hover-border: rgba(184, 63, 63, .72);
    --header: rgba(243, 244, 241, .88);
    --progress-track: #d9dad5;
    --row-hover: rgba(21, 22, 19, .035);
    --tail-bg: #151613;
    --tail-text: #d3d5ce;
    --grid-line: rgba(21, 22, 19, .055);
  }
  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
  html {
    background: var(--bg);
    transition: background-color 180ms ease, color 180ms ease;
  }
  body {
    margin: 0;
    min-height: 100dvh;
    background-color: var(--bg);
    background-image:
      linear-gradient(to right, var(--grid-line) 1px, transparent 1px),
      linear-gradient(to bottom, var(--grid-line) 1px, transparent 1px);
    background-size: 40px 40px;
    background-attachment: fixed;
    color: var(--text);
    font-family: "Geist", "Noto Sans CJK SC", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
    font-size: 14px;
    line-height: 1.45;
    letter-spacing: 0;
    transition: background-color 180ms ease, color 180ms ease;
  }
  ::selection { background: var(--accent); color: var(--accent-ink); }
  header {
    position: sticky;
    top: 0;
    z-index: 10;
    border-bottom: 1px solid var(--border);
    background: var(--header);
    backdrop-filter: blur(18px) saturate(118%);
    -webkit-backdrop-filter: blur(18px) saturate(118%);
    transition: background-color 180ms ease, border-color 180ms ease;
  }
  .topbar {
    width: min(calc(100% - 64px), 1480px);
    height: 68px;
    margin: 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
  }
  .brand { min-width: 0; }
  h1 {
    margin: 0;
    color: var(--text);
    font-size: 17px;
    line-height: 1.2;
    font-weight: 800;
  }
  h1::after {
    content: "";
    width: 5px;
    height: 5px;
    display: inline-block;
    margin-left: 5px;
    background: var(--accent);
    transition: background-color 180ms ease;
  }
  .page-heading {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 20px;
    margin: 2px 0 20px;
  }
  .page-heading > div { min-width: 0; }
  .page-title { margin: 0; color: var(--text); font-size: 28px; line-height: 1.18; font-weight: 680; }
  .brand-subtitle {
    margin-top: 7px;
    color: var(--muted);
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .status-cluster {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    flex-wrap: nowrap;
  }
  .badge {
    min-height: 28px;
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 4px 10px;
    border: 1px solid var(--border);
    border-radius: 2px;
    background: transparent;
    color: var(--text-secondary);
    font-size: 12px;
    font-weight: 560;
    white-space: nowrap;
  }
  .dot { width: 7px; height: 7px; flex: 0 0 auto; border-radius: 50%; background: var(--muted); }
  .dot.on { background: var(--ok); }
  .dot.done { background: var(--ok); }
  .dot.off { background: var(--muted); }
  main { width: min(calc(100% - 64px), 1480px); margin: 0 auto; padding: 28px 0 48px; }
  .panel-gap { margin-top: 14px; }
  .card {
    min-width: 0;
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: 0;
    padding: 16px;
    transition: background-color 180ms ease, border-color 180ms ease, color 180ms ease, transform 180ms cubic-bezier(.16, 1, .3, 1);
  }
  @media (hover: hover) {
    .card:hover { border-color: var(--border-strong); transform: translateY(-2px); }
  }
  .panel { margin-top: 14px; }
  .panel.no-margin { margin-top: 0; }
  .panel h2, .card h2 {
    margin: 0;
    color: var(--text);
    font-size: 13px;
    font-weight: 620;
  }
  .ok { color: var(--ok); } .fail { color: var(--fail); } .warn { color: var(--warn); } .accent { color: var(--accent); }
  .section-head {
    min-height: 32px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-bottom: 14px;
  }
  .section-meta { color: var(--muted); font-size: 12px; text-align: right; }
  .control-grid {
    display: grid;
    grid-template-columns: minmax(220px, 1.6fr) minmax(150px, .9fr) repeat(4, minmax(100px, .55fr)) minmax(258px, auto);
    gap: 12px;
    align-items: end;
  }
  .control-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .control-panel { padding: 12px 16px; }
  .control-panel .section-head { min-height: 24px; margin-bottom: 8px; }
  .control-panel .msg:empty { display: none; }
  .field { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .field label { color: var(--muted); font-size: 12px; font-weight: 560; }
  input, select, button { font: inherit; letter-spacing: 0; }
  input, select {
    width: 100%;
    min-height: 38px;
    border: 1px solid var(--border-strong);
    border-radius: 2px;
    background: var(--surface-soft);
    color: var(--text);
    padding: 8px 10px;
    outline: none;
  }
  input::placeholder { color: var(--placeholder); opacity: 1; }
  input:hover, select:hover { border-color: var(--hover-border); }
  input:focus, select:focus { border-color: var(--focus); box-shadow: 0 0 0 3px var(--focus-shadow); }
  button {
    min-height: 38px;
    border: 1px solid var(--border-strong);
    border-radius: 2px;
    background: var(--button);
    color: var(--text);
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: background-color 180ms ease, border-color 180ms ease, color 180ms ease, transform 180ms cubic-bezier(.16, 1, .3, 1);
    white-space: nowrap;
  }
  button:hover { background: var(--button-hover); border-color: var(--hover-border); }
  button:active { transform: translateY(2px); }
  button:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
  button.primary { background: var(--primary-bg); border-color: var(--primary-bg); color: var(--primary-text); }
  button.primary:hover { background: var(--primary-hover); border-color: var(--primary-hover); }
  button.danger { background: transparent; border-color: var(--danger-border); color: var(--fail); }
  button.danger:hover { background: var(--danger-hover-bg); border-color: var(--danger-hover-border); }
  button:disabled { opacity: .42; cursor: not-allowed; transform: none; }
  button.view-switch {
    min-width: 68px;
    min-height: 30px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0 9px;
    border-color: var(--border);
    background: var(--surface-soft);
    color: var(--text-secondary);
    font-size: 11px;
    font-weight: 620;
    line-height: 1;
  }
  button.view-switch:hover { border-color: var(--hover-border); color: var(--text); }
  button.view-switch[data-active="true"] {
    border-color: var(--accent);
    background: var(--accent);
    color: var(--accent-ink);
  }
  .theme-switch {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 2px;
    border: 1px solid var(--border);
    border-radius: 2px;
    background: var(--surface-soft);
  }
  button.theme-option {
    min-height: 24px;
    padding: 3px 8px;
    border: 0;
    border-radius: 1px;
    background: transparent;
    color: var(--muted);
    font-size: 11px;
    font-weight: 560;
    line-height: 1;
  }
  button.theme-option:hover { border: 0; background: var(--button-hover); color: var(--text); }
  button.theme-option[aria-pressed="true"] { background: var(--accent); color: var(--accent-ink); }
  .metric-grid {
    display: grid;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 1px;
    overflow: hidden;
    border: 1px solid var(--border);
    border-radius: 0;
    background: var(--border);
  }
  #kpis { margin-top: 10px; }
  .metric {
    min-width: 0;
    padding: 10px 14px;
    background: var(--surface);
    transition: background-color 180ms ease, color 180ms ease;
  }
  .metric:hover { background: var(--surface-raised); }
  .metric .label { color: var(--muted); font-size: 11px; }
  .metric .value {
    margin-top: 4px;
    font-size: 23px;
    line-height: 1.05;
    font-weight: 730;
    font-variant-numeric: tabular-nums;
    overflow-wrap: anywhere;
  }
  .metric .sub { min-height: 16px; margin-top: 4px; color: var(--muted); font-size: 11px; }
  .rate-panel { margin-top: 10px; padding: 12px 16px 14px; }
  .rate-panel .section-head { min-height: 24px; margin-bottom: 8px; }
  .rate-panel .section-meta { font-size: 11px; }
  .rate-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    border: 1px solid var(--border);
    border-radius: 0;
    overflow: hidden;
  }
  .rate-item { min-width: 0; padding: 10px 12px; background: var(--surface-soft); transition: background-color 180ms ease; }
  .rate-item + .rate-item { border-left: 1px solid var(--border); }
  .rate-top { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
  .rate-label { color: var(--text-secondary); font-size: 12px; }
  .rate-total { color: var(--muted); font-size: 11px; white-space: nowrap; }
  .rate-value { margin-top: 4px; font-size: 23px; line-height: 1; font-weight: 730; font-variant-numeric: tabular-nums; }
  .rate-breakdown { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 6px; color: var(--muted); font-size: 11px; }
  .progress-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 10px; }
  .bar-wrap { height: 8px; overflow: hidden; border-radius: 1px; background: var(--progress-track); }
  .bar { height: 100%; width: 0%; background: var(--accent); transition: width 420ms cubic-bezier(.16, 1, .3, 1), background-color 180ms ease; }
  .progress-sub { margin-top: 9px; color: var(--muted); font-size: 12px; }
  .two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
  .three { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr) minmax(0, .95fr); gap: 14px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 9px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
  th { color: var(--muted); font-weight: 620; font-size: 11px; }
  td { color: var(--text-secondary); }
  tbody tr:last-child td { border-bottom: 0; }
  tr:hover td { background: var(--row-hover); }
  .table-scroll { width: 100%; overflow: auto; }
  .mono { font-family: "Geist Mono", "SFMono-Regular", Consolas, monospace; font-size: 12px; }
  .tail {
    max-height: 360px;
    overflow: auto;
    border: 1px solid var(--border);
    border-radius: 2px;
    background: var(--tail-bg);
    padding: 12px;
    color: var(--tail-text);
    font-size: 11.5px;
    line-height: 1.55;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .chips { display: flex; flex-wrap: wrap; gap: 7px; }
  .card > h2 + .chips { margin-top: 14px; }
  .chip {
    min-width: 84px;
    border: 1px solid var(--border);
    border-radius: 2px;
    background: var(--surface-soft);
    padding: 8px 9px;
  }
  .chip b { display: block; margin-top: 2px; font-size: 17px; font-variant-numeric: tabular-nums; }
  .chip span { color: var(--muted); font-size: 11px; }
  .bl-list {
    max-height: 260px;
    overflow: auto;
    border: 1px solid var(--border);
    border-radius: 2px;
    background: var(--surface-soft);
  }
  .bl-list table { font-size: 12px; }
  .msg { font-size: 12px; color: var(--muted); min-height: 18px; margin-top: 8px; }
  .msg.err { color: var(--fail); } .msg.ok { color: var(--ok); }
  .button-group { display: flex; align-items: center; justify-content: flex-end; gap: 7px; flex-wrap: wrap; }
  .recovery-layout { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
  .recovery-layout .chips { flex: 1 1 auto; }
  .recovery-actions { flex: 0 0 auto; }
  body.help-view-open { overflow: hidden; }
  body.help-view-open #dashboard-view > :not(#help-view) { display: none; }
  .help-view {
    position: fixed;
    inset: 68px 0 0;
    z-index: 8;
    overflow-y: auto;
    overscroll-behavior: contain;
    background-color: var(--bg);
    background-image:
      linear-gradient(to right, var(--grid-line) 1px, transparent 1px),
      linear-gradient(to bottom, var(--grid-line) 1px, transparent 1px);
    background-size: 40px 40px;
  }
  .help-view[hidden] { display: none; }
  .help-view-inner {
    width: min(calc(100% - 64px), 1120px);
    margin: 0 auto;
    padding: 28px 0 48px;
  }
  .help-view-heading {
    margin-bottom: 20px;
    padding-bottom: 20px;
    border-bottom: 1px solid var(--border);
  }
  .help-view-subtitle {
    margin: 7px 0 0;
    color: var(--muted);
    font-size: 12px;
  }
  .help-body { min-width: 0; }
  .help-toolbar {
    min-height: 42px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 16px;
  }
  .help-tabs {
    display: inline-flex;
    gap: 2px;
    padding: 2px;
    border: 1px solid var(--border);
    border-radius: 2px;
    background: var(--surface-soft);
  }
  button.help-tab {
    min-height: 28px;
    padding: 5px 10px;
    border: 0;
    border-radius: 1px;
    background: transparent;
    color: var(--muted);
    font-size: 12px;
  }
  button.help-tab:hover { border: 0; color: var(--text); }
  button.help-tab[aria-selected="true"] { background: var(--accent); color: var(--accent-ink); }
  .help-guide-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 1px;
    border: 1px solid var(--border);
    background: var(--border);
  }
  .help-guide-item { min-width: 0; min-height: 132px; padding: 14px; background: var(--surface-soft); }
  .help-guide-item h3 { margin: 0; color: var(--text); font-size: 13px; font-weight: 650; }
  .help-guide-item p { margin: 9px 0 0; color: var(--text-secondary); font-size: 12px; line-height: 1.65; }
  .help-guide-item code, .faq-answer code {
    color: var(--accent);
    font-family: "Geist Mono", monospace;
    font-size: .94em;
    overflow-wrap: anywhere;
  }
  .help-note {
    margin: 14px 0 0;
    padding-top: 12px;
    border-top: 1px solid var(--border);
    color: var(--muted);
    font-size: 11px;
    line-height: 1.6;
  }
  .faq-tools {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 4px;
  }
  #faq-search { min-height: 36px; max-width: 360px; }
  .faq-count { flex: 0 0 auto; color: var(--muted); font-size: 11px; }
  .faq-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 24px; }
  .faq-item { min-width: 0; border-top: 1px solid var(--border); }
  .faq-item summary {
    padding: 13px 2px;
    color: var(--text);
    font-size: 12px;
    font-weight: 620;
    line-height: 1.45;
    cursor: pointer;
  }
  .faq-item summary::marker { color: var(--accent); }
  .faq-item[open] summary { color: var(--accent); }
  .faq-answer { padding: 0 18px 14px; color: var(--text-secondary); font-size: 12px; line-height: 1.65; }
  .faq-empty { margin: 16px 0 2px; color: var(--muted); font-size: 12px; }
  footer { margin-top: 16px; color: var(--muted); font-size: 11px; overflow-wrap: anywhere; }
  main > :not(.help-view) {
    animation: panel-enter 520ms cubic-bezier(.16, 1, .3, 1) both;
  }
  main > :nth-child(2) { animation-delay: 45ms; }
  main > :nth-child(3) { animation-delay: 90ms; }
  main > :nth-child(4) { animation-delay: 135ms; }
  main > :nth-child(5) { animation-delay: 180ms; }
  main > :nth-child(6) { animation-delay: 225ms; }
  main > :nth-child(7) { animation-delay: 270ms; }
  main > :nth-child(8) { animation-delay: 315ms; }
  main > :nth-child(9) { animation-delay: 360ms; }
  main > :nth-child(n + 10) { animation-delay: 405ms; }
  @keyframes panel-enter {
    from { opacity: 0; transform: translateY(12px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @media (min-width: 1121px) {
    .control-panel .control-grid { gap: 10px; }
    .control-panel .field { gap: 4px; }
    .control-panel .field label { font-size: 11px; }
    .control-panel .control-actions { gap: 6px; }
    .control-panel input,
    .control-panel select,
    .control-panel .control-actions button {
      min-height: 34px;
      padding-block: 6px;
    }
  }
  @media (max-width: 1120px) {
    .control-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .field-token { grid-column: span 2; }
    .control-actions { grid-column: 1 / -1; padding-top: 14px; border-top: 1px solid var(--border); }
    .metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .three { grid-template-columns: minmax(0, 1fr); }
    .help-guide-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  @media (max-width: 760px) {
    .topbar { width: calc(100% - 32px); height: 60px; align-items: center; flex-direction: row; gap: 10px; }
    .brand { width: auto; }
    .status-cluster { width: auto; justify-content: flex-end; margin-left: auto; }
    #clock, #sync-label { display: none; }
    main { width: calc(100% - 24px); padding: 20px 0 34px; }
    .page-heading { margin-bottom: 16px; }
    .page-title { font-size: 22px; }
    .card { padding: 14px; }
    .control-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .field-token, .field-mode { grid-column: 1 / -1; }
    .control-actions { justify-content: stretch; }
    .control-actions button { flex: 1 1 0; padding-inline: 8px; }
    .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .metric { padding: 14px; }
    .metric .value { font-size: 23px; }
    .rate-grid, .two { grid-template-columns: minmax(0, 1fr); }
    .rate-item + .rate-item { border-left: 0; border-top: 1px solid var(--border); }
    .section-head { align-items: flex-start; }
    .section-meta { max-width: 48%; }
    .help-view { inset-block-start: 60px; }
    .help-view-inner { width: calc(100% - 24px); padding: 20px 0 34px; }
    .help-view-heading { margin-bottom: 16px; padding-bottom: 16px; }
    .help-toolbar, .faq-tools { align-items: stretch; flex-direction: column; }
    .help-toolbar { min-height: 0; }
    .help-tabs { width: 100%; }
    button.help-tab { flex: 1 1 0; }
    .help-guide-grid, .faq-grid { grid-template-columns: 1fr; }
    #faq-search { max-width: none; }
    .recovery-layout { align-items: stretch; flex-direction: column; }
    .recovery-actions { justify-content: stretch; }
    .recovery-actions button { flex: 1 1 0; }
  }
  @media (max-width: 420px) {
    h1 { font-size: 15px; }
    .badge { font-size: 11px; }
    .run-status { width: 30px; min-width: 30px; justify-content: center; padding-inline: 0; }
    #run-label { display: none; }
    .card { padding: 13px; }
    .control-actions { flex-wrap: wrap; }
    .control-actions button { flex-basis: calc(50% - 4px); }
    .control-actions button:last-child { flex-basis: 100%; }
    .metric .sub { font-size: 11px; }
    .button-group { justify-content: flex-start; }
  }
  @media (max-width: 340px) {
    .run-status { display: none; }
  }
  html[data-theme="dark"] {
      color-scheme: dark;
      --bg: #171815;
      --surface: #20211e;
      --surface-raised: #242622;
      --surface-soft: #1d1e1b;
      --surface-deep: #30322d;
      --border: rgba(240, 241, 237, .16);
      --border-strong: rgba(240, 241, 237, .42);
      --text: #f0f1ed;
      --text-secondary: #d3d5ce;
      --muted: #a5a79f;
      --placeholder: #777971;
      --ok: #69c493;
      --fail: #f27c71;
      --warn: #d7ae58;
      --accent: #f06449;
      --accent-hover: #ff7a60;
      --accent-ink: #171815;
      --focus: #f06449;
      --button: #242622;
      --button-hover: #30322d;
      --hover-border: rgba(240, 241, 237, .42);
      --focus-shadow: rgba(240, 100, 73, .18);
      --primary-bg: #f0f1ed;
      --primary-text: #171815;
      --primary-hover: #d3d5ce;
      --danger-border: rgba(242, 124, 113, .48);
      --danger-hover-bg: rgba(242, 124, 113, .09);
      --danger-hover-border: rgba(242, 124, 113, .75);
      --header: rgba(23, 24, 21, .88);
      --progress-track: #30322d;
      --row-hover: rgba(240, 241, 237, .035);
      --tail-bg: #11120f;
      --tail-text: #d3d5ce;
      --grid-line: rgba(240, 241, 237, .045);
  }
  @media (prefers-reduced-motion: reduce) {
    html { scroll-behavior: auto; }
    *, *::before, *::after {
      animation-duration: .01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: .01ms !important;
    }
    .card:hover { transform: none; }
  }
</style>
</head>
<body>
<header>
  <div class="topbar">
    <div class="brand">
      <h1>GrokRegister</h1>
    </div>
    <div class="status-cluster">
      <button type="button" class="view-switch" id="help-view-toggle" aria-label="打开问题和使用" title="问题和使用" aria-controls="help-view" aria-expanded="false" data-active="false" onclick="toggleAppView()">
        <span id="help-view-label" aria-hidden="true">问题和使用</span>
      </button>
      <div class="theme-switch" role="group" aria-label="界面主题">
        <button type="button" class="theme-option" data-theme-choice="light" aria-pressed="false" onclick="setTheme('light')">浅色</button>
        <button type="button" class="theme-option" data-theme-choice="dark" aria-pressed="false" onclick="setTheme('dark')">深色</button>
      </div>
      <span class="badge run-status" id="run-status" aria-label="任务状态：加载中" aria-live="polite" aria-atomic="true"><span class="dot" id="run-dot"></span><span id="run-label">加载中</span></span>
      <span class="badge mono" id="clock">--</span>
      <span class="badge" id="sync-label">实时更新</span>
    </div>
  </div>
</header>
<main id="dashboard-view" aria-label="注册控制台">
  <div class="page-heading">
    <div>
      <div class="page-title">注册控制台</div>
      <div class="brand-subtitle mono" id="logname">--</div>
    </div>
  </div>
  <section class="card control-panel">
    <div class="section-head">
      <h2>任务控制</h2>
      <span class="section-meta mono" id="ctrl-status"></span>
    </div>
    <div class="control-grid">
      <div class="field field-token">
        <label for="monitor-token">访问令牌</label>
        <input id="monitor-token" type="password" autocomplete="off" placeholder="MONITOR_TOKEN" onchange="getToken(); refresh(); refreshRecovery()" onblur="getToken()"/>
      </div>
      <div class="field field-mode">
        <label for="mode">运行模式</label>
        <select id="mode">
          <option value="orch">持续编排</option>
          <option value="batch" selected>单批运行</option>
        </select>
      </div>
      <div class="field"><label for="workers-input">并发数</label>
        <input type="number" id="workers-input" min="1" max="24" value="1"/>
      </div>
      <div class="field"><label for="batch_count">单批数量</label>
        <input type="number" id="batch_count" min="1" max="200" value="1"/>
      </div>
      <div class="field"><label for="add_count">追加目标</label>
        <input type="number" id="add_count" min="1" max="500" value="1" title="每次启动从当前 CPA 再注册 N 个"/>
      </div>
      <div class="field"><label for="risk_pause">风控阈值</label>
        <input type="number" id="risk_pause" min="1" max="50" value="10"/>
      </div>
      <div class="control-actions">
        <button class="primary" id="btn-start" onclick="doStart()">启动任务</button>
        <button class="danger" id="btn-stop" onclick="doStop()">停止任务</button>
        <button onclick="saveCtrl()">保存设置</button>
      </div>
    </div>
    <div class="msg" id="ctrl-msg" role="status" aria-live="polite"></div>
  </section>

  <section class="help-view" id="help-view" aria-labelledby="help-view-title" hidden>
    <div class="help-view-inner">
      <div class="help-view-heading">
        <div class="page-title" id="help-view-title">使用帮助</div>
        <p class="help-view-subtitle">运行方法与故障排查</p>
      </div>
      <div class="help-body" id="help-body">
      <div class="help-toolbar">
        <div class="help-tabs" role="tablist" aria-label="帮助内容" onkeydown="handleHelpTabKey(event)">
          <button type="button" class="help-tab" id="help-tab-guide" role="tab" aria-selected="true" aria-controls="help-guide" data-help-tab="guide" onclick="setHelpTab('guide')">快速使用</button>
          <button type="button" class="help-tab" id="help-tab-faq" role="tab" aria-selected="false" aria-controls="help-faq" data-help-tab="faq" tabindex="-1" onclick="setHelpTab('faq')">常见问题</button>
        </div>
      </div>

      <div id="help-guide" role="tabpanel" aria-labelledby="help-tab-guide">
        <div class="help-guide-grid">
          <div class="help-guide-item">
            <h3>准备环境</h3>
            <p>确认 Camoufox 引擎已安装、邮箱服务可用、CPA auth 目录可写。直连可用时不必额外配置代理。</p>
          </div>
          <div class="help-guide-item">
            <h3>选择模式</h3>
            <p><code>持续编排</code>按追加目标多轮运行；<code>单批运行</code>只执行单批数量。首次建议并发 2-3。</p>
          </div>
          <div class="help-guide-item">
            <h3>保存并启动</h3>
            <p>输入当前面板令牌，先保存设置再启动。追加目标表示从现有 CPA 数量继续增加多少。</p>
          </div>
          <div class="help-guide-item">
            <h3>观察结果</h3>
            <p>优先看注册风控、时段成功率和日志尾部。连续风控时先换出口或邮箱域名，不要继续提高并发。</p>
          </div>
        </div>
        <p class="help-note">停止任务会结束当前编排和批处理进程。重置黑名单会恢复基线规则，不等于清空所有风控判断。</p>
      </div>

      <div id="help-faq" role="tabpanel" aria-labelledby="help-tab-faq" hidden>
        <div class="faq-tools">
          <label class="sr-only" for="faq-search">搜索常见问题</label>
          <input id="faq-search" type="search" placeholder="搜索错误码或现象" autocomplete="off" oninput="filterFaq(this.value)"/>
          <span class="faq-count mono" id="faq-count">12 项</span>
        </div>
        <div class="faq-grid" id="faq-grid">
          <details class="faq-item" data-faq-item data-search="令牌 token unauthorized 401 保存设置 启动">
            <summary>提示访问令牌不匹配或 401</summary>
            <div class="faq-answer">重新输入当前面板令牌并保存。令牌只保存在当前浏览器的 localStorage 中，换端口、设备或浏览器后需要重新输入。</div>
          </details>
          <details class="faq-item" data-faq-item data-search="启动 立即结束 目标 cpa add_count 追加目标">
            <summary>点击启动后立即结束</summary>
            <div class="faq-answer">通常是 CPA 已达到旧目标。提高“追加目标”后再启动；持续编排会以当前 CPA 为基线增加 N，单批运行只执行“单批数量”。</div>
          </details>
          <details class="faq-item" data-faq-item data-search="风控 policy deny registration risk botFlagSource ip 邮箱 域名">
            <summary>出现 policy=deny 或注册风控</summary>
            <div class="faq-answer">该账号已被注册风控拒绝，不要反复重转同一 SSO。先更换质量更好的出口并给 IP 冷却时间，邮箱优先使用稳定的子域名，并发先保持 2-3。</div>
          </details>
          <details class="faq-item" data-faq-item data-search="卡住 浏览器 启动失败 turnstile 资料页 空页 并发 camoufox">
            <summary>注册卡在验证码、资料页或浏览器启动</summary>
            <div class="faq-answer">先从失败分类和日志尾部确认具体阶段。连续浏览器启动失败时降低并发，并检查是否执行过 <code>camoufox fetch</code>；资料页失败也可能是 Turnstile 未通过。</div>
          </details>
          <details class="faq-item" data-faq-item data-search="cpa 没新增 invalid_grant access denied 503 auth unavailable oauth 入库 目录 管理密钥">
            <summary>CPA 没新增，或出现 invalid_grant / 503</summary>
            <div class="faq-answer">先检查 <code>cpa_auto_add</code>、auth 目录、远程 CPA 地址和管理密钥。<code>invalid_grant Access denied</code> 表示 OAuth 交换被拒；503 表示 CPA 当前没有可用 xAI auth。</div>
          </details>
          <details class="faq-item" data-faq-item data-search="permission denied access chat endpoint referrer grok build base_url oauth">
            <summary>调用模型提示 permission-denied</summary>
            <div class="faq-answer">常见原因是 token 缺少 <code>referrer=grok-build</code>，或 <code>base_url</code> 指向了 <code>api.x.ai</code>。使用项目的 Authorization Code + PKCE 流程重新生成，并指向 Build 通道。</div>
          </details>
          <details class="faq-item" data-faq-item data-search="出口 ip 代理 无法解析 流量 住宅 链式 dialer">
            <summary>无法解析出口 IP，或代理流量消耗很高</summary>
            <div class="faq-answer">先单独测试代理端口是否可用。住宅代理可能同时计算上下行流量，实际每 GB 产出没有固定值；降低并发并避免重复失败重试。链式代理应在代理客户端配置。</div>
          </details>
          <details class="faq-item" data-faq-item data-search="邮箱 api 401 超时 cloudflare workers key auth_mode proxy">
            <summary>邮箱 API 返回 401 或请求超时</summary>
            <div class="faq-answer">401 先检查对应邮箱服务的 key 和 <code>auth_mode</code>。访问 workers.dev 超时时，在配置中显式填写代理，不要只依赖桌面进程可能无法继承的 HTTP_PROXY 环境变量。</div>
          </details>
          <details class="faq-item" data-faq-item data-search="黑名单 asn 清除 重置 baseline 风控 出口">
            <summary>黑名单有什么作用，可以清除吗</summary>
            <div class="faq-answer">黑名单用于避开持续触发风控的出口 ASN。面板“重置”会恢复基线熔断规则；不清楚影响时不要清空全部规则，重复命中通常说明出口质量需要调整。</div>
          </details>
          <details class="faq-item" data-faq-item data-search="accounts txt sso 导入 cpa json sub2api 转换">
            <summary>已有 accounts 文本怎么导入 CPA 或 sub2api</summary>
            <div class="faq-answer">控制台的“账号补录”可处理待补录队列，也可扫描全部 accounts 文本；已存在 CPA 的账号会跳过，成功项会从待补录队列移除。面板不直接导入 sub2api，需要按目标系统的数据结构另行转换。</div>
          </details>
          <details class="faq-item" data-faq-item data-search="搜索 模型 grok build 4.5 能力 api">
            <summary>注册成功但搜索或某个模型不可用</summary>
            <div class="faq-answer">注册成功不代表所有上游能力都会开放。确认请求走 Grok Build 通道；搜索和具体模型可用性仍可能随账号状态和上游策略变化。</div>
          </details>
          <details class="faq-item" data-faq-item data-search="体验额度 429 quota rate limit 免费 余额">
            <summary>体验额度有多少，出现 429 怎么办</summary>
            <div class="faq-answer">体验额度由上游按账号分配，面板无法推算准确余额。429 通常表示额度耗尽或触发速率限制，需要等待恢复或更换仍有可用额度的 auth。</div>
          </details>
        </div>
        <p class="faq-empty" id="faq-empty" hidden>没有匹配的问题，请换一个错误码或现象关键词。</p>
      </div>
      </div>
    </div>
  </section>

  <section class="metric-grid panel-gap" id="kpis" aria-label="核心指标"></section>

  <section class="card panel rate-panel">
    <div class="section-head">
      <h2>时段成功率</h2>
      <span class="section-meta mono" id="rates-updated">register_results.jsonl</span>
    </div>
    <div class="rate-grid" id="rate-kpis"></div>
  </section>

  <section class="card panel">
    <div class="progress-head">
      <h2>当前批次</h2>
      <div class="mono" id="prog-text">--</div>
    </div>
    <div class="bar-wrap"><div class="bar" id="bar"></div></div>
    <div class="progress-sub" id="prog-sub"></div>
  </section>

  <section class="card panel recovery-panel" aria-labelledby="recovery-title">
    <div class="section-head">
      <h2 id="recovery-title">账号补录</h2>
      <span class="section-meta mono" id="recovery-status">等待检查</span>
    </div>
    <div class="recovery-layout">
      <div class="chips" id="recovery-kpis"></div>
      <div class="button-group recovery-actions">
        <button id="recovery-pending" onclick="startRecovery('pending')">补录待处理</button>
        <button id="recovery-accounts" onclick="startRecovery('accounts')">扫描全部账号</button>
        <button class="danger" id="recovery-stop" onclick="stopRecovery()">停止补录</button>
      </div>
    </div>
    <div class="msg" id="recovery-msg" role="status" aria-live="polite"></div>
  </section>

  <div class="three panel-gap">
    <div class="card">
      <div class="section-head">
        <h2>成功统计</h2>
        <button onclick="refreshStats()">刷新</button>
      </div>
      <div class="chips" id="stats-chips"></div>
      <div class="msg" id="stats-msg" role="status" aria-live="polite"></div>
      <div class="table-scroll">
        <table><thead><tr><th>日期</th><th>成功</th><th>风控</th><th>失败</th></tr></thead>
        <tbody id="stats-day"></tbody></table>
      </div>
    </div>
    <div class="card">
      <div class="section-head">
        <h2>黑名单</h2>
        <div class="button-group">
          <button onclick="refreshBlacklist()">刷新</button>
          <button class="danger" onclick="resetBlacklist('baseline')">重置</button>
        </div>
      </div>
      <div class="chips" id="bl-kpis"></div>
      <div class="msg" id="bl-msg" role="status" aria-live="polite"></div>
      <div class="bl-list" style="margin-top:10px">
        <table><thead><tr><th>ASN</th><th>备注</th></tr></thead><tbody id="bl-body"></tbody></table>
      </div>
    </div>
    <div class="card">
      <div class="section-head"><h2>黑名单更新记录</h2></div>
      <div class="chips" id="bl-err-chips"></div>
      <div class="table-scroll">
        <table><thead><tr><th>新增 ASN</th><th>来源</th></tr></thead>
        <tbody id="bl-added"></tbody></table>
      </div>
    </div>
  </div>

  <div class="two panel-gap">
    <div class="card"><h2>Worker 成功 / 失败</h2><div class="chips" id="workers-stats"></div></div>
    <div class="card"><h2>失败分类</h2><div class="chips" id="fails"></div></div>
  </div>
  <div class="two panel-gap">
    <div class="card">
      <div class="section-head"><h2>最近成功</h2></div>
      <div class="table-scroll"><table><thead><tr><th>时间</th><th>W</th><th>邮箱</th></tr></thead><tbody id="ok-body"></tbody></table></div>
    </div>
    <div class="card">
      <div class="section-head"><h2>最近失败</h2></div>
      <div class="table-scroll"><table><thead><tr><th>时间</th><th>W</th><th>类型</th><th>摘要</th></tr></thead><tbody id="fail-body"></tbody></table></div>
    </div>
  </div>
  <section class="card panel">
    <div class="section-head"><h2>日志尾部</h2></div>
    <div class="tail mono" id="tail"></div>
  </section>
  <footer id="footer"></footer>
</main>
<script>
let last = null;
const THEME_KEY = "GROK_REGISTER_THEME";
const APP_VIEW_KEY = "GROK_REGISTER_APP_VIEW";
const HELP_TAB_KEY = "GROK_REGISTER_HELP_TAB";
function syncThemeButtons() {
  const theme = document.documentElement.dataset.theme || "light";
  document.querySelectorAll("[data-theme-choice]").forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.themeChoice === theme));
  });
  const color = document.getElementById("theme-color");
  if (color) color.content = theme === "dark" ? "#171815" : "#f3f4f1";
}
function setTheme(theme) {
  if (theme !== "light" && theme !== "dark") return;
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
  syncThemeButtons();
}
function setAppView(view, options = {}) {
  if (view !== "dashboard" && view !== "help") return;
  const dashboard = document.getElementById("dashboard-view");
  const help = document.getElementById("help-view");
  const toggle = document.getElementById("help-view-toggle");
  const label = document.getElementById("help-view-label");
  if (!dashboard || !help || !toggle || !label) return;
  const isHelp = view === "help";
  const dashboardChildren = Array.from(dashboard.children).filter(element => element !== help);
  dashboardChildren.forEach(element => {
    element.inert = isHelp;
    if (isHelp) element.setAttribute("aria-hidden", "true");
    else element.removeAttribute("aria-hidden");
  });
  help.hidden = !isHelp;
  help.inert = !isHelp;
  document.body.classList.toggle("help-view-open", isHelp);
  toggle.dataset.active = String(isHelp);
  toggle.setAttribute("aria-expanded", String(isHelp));
  toggle.setAttribute("aria-label", isHelp ? "返回控制台" : "打开问题和使用");
  toggle.title = isHelp ? "返回控制台" : "问题和使用";
  label.textContent = isHelp ? "返回控制台" : "问题和使用";
  if (options.persist !== false) {
    try { localStorage.setItem(APP_VIEW_KEY, view); } catch (e) {}
  }
  if (options.focus) {
    requestAnimationFrame(() => {
      const target = isHelp ? document.querySelector('[data-help-tab][aria-selected="true"]') : toggle;
      if (target) target.focus();
    });
  }
}
function toggleAppView() {
  const isHelp = document.body.classList.contains("help-view-open");
  setAppView(isHelp ? "dashboard" : "help", { focus: true });
}
function setHelpTab(name) {
  if (name !== "guide" && name !== "faq") return;
  document.querySelectorAll("[data-help-tab]").forEach(button => {
    const selected = button.dataset.helpTab === name;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  const guide = document.getElementById("help-guide");
  const faq = document.getElementById("help-faq");
  if (guide) guide.hidden = name !== "guide";
  if (faq) faq.hidden = name !== "faq";
  try { localStorage.setItem(HELP_TAB_KEY, name); } catch (e) {}
}
function handleHelpTabKey(event) {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  const tabs = Array.from(document.querySelectorAll("[data-help-tab]"));
  const current = tabs.indexOf(document.activeElement);
  if (current < 0) return;
  event.preventDefault();
  const next = event.key === "ArrowRight" ? (current + 1) % tabs.length : (current - 1 + tabs.length) % tabs.length;
  tabs[next].focus();
  setHelpTab(tabs[next].dataset.helpTab);
}
function filterFaq(value) {
  const query = String(value || "").trim().toLocaleLowerCase();
  const items = Array.from(document.querySelectorAll("[data-faq-item]"));
  const matches = [];
  items.forEach(item => {
    const haystack = ((item.dataset.search || "") + " " + item.textContent).toLocaleLowerCase();
    const matched = !query || haystack.includes(query);
    item.hidden = !matched;
    if (matched) matches.push(item);
  });
  if (query && matches.length === 1) matches[0].open = true;
  const count = document.getElementById("faq-count");
  const empty = document.getElementById("faq-empty");
  if (count) count.textContent = matches.length + " 项";
  if (empty) empty.hidden = matches.length > 0;
}
function showHelpFor(query) {
  setAppView("help", { focus: false });
  setHelpTab("faq");
  const search = document.getElementById("faq-search");
  if (search) search.value = query || "";
  filterFaq(query || "");
  if (search) requestAnimationFrame(() => search.focus());
}
function initHelp() {
  let view = "dashboard";
  let tab = "guide";
  try {
    view = localStorage.getItem(APP_VIEW_KEY) || "dashboard";
    tab = localStorage.getItem(HELP_TAB_KEY) || "guide";
  } catch (e) {}
  setHelpTab(tab);
  filterFaq("");
  setAppView(view, { persist: false, focus: false });
}
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && document.body.classList.contains("help-view-open")) {
    setAppView("dashboard", { focus: true });
  }
});
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}
function setMsg(id, text, cls) {
  const el = document.getElementById(id);
  el.textContent = text || "";
  el.className = "msg" + (cls ? " " + cls : "");
}
function getToken() {
  const el = document.getElementById("monitor-token");
  const fromInput = el ? (el.value || "").trim() : "";
  const tok = (fromInput || window.MONITOR_TOKEN || localStorage.getItem("MONITOR_TOKEN") || "").trim();
  if (fromInput) try { localStorage.setItem("MONITOR_TOKEN", fromInput); } catch (e) {}
  return tok;
}
function loadTokenField() {
  const el = document.getElementById("monitor-token");
  if (!el) return;
  if (!el.value) {
    try { el.value = localStorage.getItem("MONITOR_TOKEN") || window.MONITOR_TOKEN || ""; } catch (e) {}
  }
}
async function api(path, opts) {
  opts = Object.assign({}, opts || {});
  const authHelp = opts.authHelp !== false;
  delete opts.authHelp;
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  const tok = getToken();
  if (tok) headers["Authorization"] = "Bearer " + tok;
  const r = await fetch(path, Object.assign({}, opts, { headers }));
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    if (r.status === 401) {
      if (authHelp) showHelpFor("令牌");
      throw new Error("访问令牌不匹配，请重新输入当前面板令牌");
    }
    throw new Error(j.error || r.statusText || "request failed");
  }
  if (j && j.ok === false) throw new Error(j.error || j.message || "request failed");
  return j;
}
async function refresh() {
  try {
    const d = await api("/api/status?_=" + Date.now(), { authHelp: false });
    last = d;
    render(d);
  } catch (e) {
    const message = String(e.message || e);
    document.getElementById("clock").textContent = message.includes("令牌") ? "需要令牌" : "连接异常";
    const sync = document.getElementById("sync-label");
    if (sync) {
      sync.textContent = message.includes("令牌") ? "等待令牌" : "更新失败";
      sync.className = "badge fail";
    }
    if (message.includes("令牌")) setMsg("ctrl-msg", message, "err");
  }
}
function fillControl(d) {
  const c = d.control || {};
  if (document.activeElement && ["workers-input","batch_count","add_count","risk_pause","mode"].includes(document.activeElement.id)) return;
  if (c.workers != null) document.getElementById("workers-input").value = c.workers;
  if (c.batch_count != null) document.getElementById("batch_count").value = c.batch_count;
  if (c.add_count != null && document.getElementById("add_count")) document.getElementById("add_count").value = c.add_count;
  if (c.risk_pause != null) document.getElementById("risk_pause").value = c.risk_pause;
  if (c.mode) document.getElementById("mode").value = c.mode;
}
function controlBody() {
  return {
    workers: Number(document.getElementById("workers-input").value || 1),
    batch_count: Number(document.getElementById("batch_count").value || 1),
    add_count: Number((document.getElementById("add_count") || {}).value || 1),
    risk_pause: Number(document.getElementById("risk_pause").value || 10),
    mode: document.getElementById("mode").value || "batch",
  };
}
async function saveCtrl() {
  try {
    const j = await api("/api/control", { method: "POST", body: JSON.stringify(controlBody()) });
    setMsg("ctrl-msg", "设置已保存，并发数 " + j.workers, "ok");
  } catch (e) { setMsg("ctrl-msg", String(e.message || e), "err"); }
}
async function doStart() {
  document.getElementById("btn-start").disabled = true;
  setMsg("ctrl-msg", "正在启动…", "");
  try {
    await api("/api/control", { method: "POST", body: JSON.stringify(controlBody()) });
    const j = await api("/api/start", { method: "POST", body: JSON.stringify(controlBody()) });
    if (j.ok === false) throw new Error(j.error || "start failed");
    const msg = j.message || ("已启动，进程 " + (j.pid || "?") + "，模式 " + (j.mode || ""));
    setMsg("ctrl-msg", msg + (j.need != null ? "，剩余 " + j.need : ""), "ok");
    setTimeout(refresh, 1000);
    setTimeout(refresh, 3000);
  } catch (e) { setMsg("ctrl-msg", String(e.message || e), "err"); }
  document.getElementById("btn-start").disabled = false;
}
async function doStop() {
  document.getElementById("btn-stop").disabled = true;
  try {
    const j = await api("/api/stop", { method: "POST", body: "{}" });
    setMsg("ctrl-msg", "已停止 killed=" + JSON.stringify(j.killed || []), "ok");
    setTimeout(refresh, 800);
  } catch (e) { setMsg("ctrl-msg", String(e.message || e), "err"); }
  document.getElementById("btn-stop").disabled = false;
}
async function resetBlacklist(mode) {
  mode = mode || "baseline";
  if (!confirm(mode === "empty" ? "清空全部黑名单？" : "重置为基线熔断？")) return;
  try {
    const j = await api("/api/blacklist/reset", { method: "POST", body: JSON.stringify({ mode }) });
    setMsg("bl-msg", j.message || "已重置", "ok");
    setTimeout(refresh, 500);
  } catch (e) { setMsg("bl-msg", String(e.message || e), "err"); }
}
async function refreshBlacklist() {
  try {
    const j = await api("/api/blacklist?_=" + Date.now());
    renderBlacklist(j, last && last.blacklist_update);
    setMsg("bl-msg", "已刷新 / " + (j.mtime_human || "") + " / " + (j.count || 0) + " ASN", "ok");
  } catch (e) { setMsg("bl-msg", String(e.message || e), "err"); }
}
async function refreshStats(authHelp = true) {
  try {
    const j = await api("/api/stats?_=" + Date.now(), { authHelp });
    renderStats(j);
    setMsg("stats-msg", "统计已刷新 " + (j.refreshed_at || ""), "ok");
  } catch (e) { setMsg("stats-msg", String(e.message || e), "err"); }
}
function renderRecovery(data) {
  data = data || {};
  const report = data.last_report || {};
  document.getElementById("recovery-kpis").innerHTML = [
    ["待处理", data.pending_count ?? 0, (data.pending_count || 0) > 0 ? "warn" : "ok"],
    ["账号记录", data.account_record_count ?? 0, ""],
    ["可补录", data.recoverable_count ?? 0, (data.recoverable_count || 0) > 0 ? "accent" : "ok"],
    ["上次成功", report.success_count ?? "--", "ok"],
    ["上次失败", report.failure_count ?? "--", (report.failure_count || 0) > 0 ? "fail" : ""],
  ].map(([label, value, cls]) => `<div class="chip"><span>${esc(label)}</span><b class="${cls}">${esc(value)}</b></div>`).join("");
  document.getElementById("recovery-status").textContent = data.running ? ("补录中 #" + (data.pid || "?")) : "空闲";
  document.getElementById("recovery-pending").disabled = !!data.running || !(data.pending_count > 0);
  document.getElementById("recovery-accounts").disabled = !!data.running || !(data.recoverable_count > 0);
  document.getElementById("recovery-stop").disabled = !data.running;
}
async function refreshRecovery() {
  try {
    const data = await api("/api/recovery?_=" + Date.now(), { authHelp: false });
    renderRecovery(data);
  } catch (e) {
    const message = String(e.message || e);
    document.getElementById("recovery-status").textContent = message.includes("令牌") ? "等待令牌" : "检查失败";
  }
}
async function startRecovery(scope) {
  if (scope === "accounts" && !confirm("扫描全部账号文本并补录缺失 CPA？此操作可能持续较长时间。")) return;
  setMsg("recovery-msg", "正在启动补录…", "");
  try {
    const data = await api("/api/recovery/start", { method: "POST", body: JSON.stringify({ scope }) });
    setMsg("recovery-msg", "补录已启动，共 " + (data.input_count || 0) + " 条", "ok");
    await refreshRecovery();
  } catch (e) { setMsg("recovery-msg", String(e.message || e), "err"); }
}
async function stopRecovery() {
  try {
    const data = await api("/api/recovery/stop", { method: "POST", body: "{}" });
    setMsg("recovery-msg", "补录已停止，结束进程 " + JSON.stringify(data.killed || []), "ok");
    await refreshRecovery();
  } catch (e) { setMsg("recovery-msg", String(e.message || e), "err"); }
}
function renderBlacklist(bl, upd) {
  bl = bl || {};
  upd = upd || {};
  document.getElementById("bl-kpis").innerHTML = [
    ["ASN 数", bl.count ?? 0, "accent"],
    ["ISP 关键字", (bl.isp_keywords || []).length, ""],
    ["解析错误", (bl.errors || []).length, (bl.errors || []).length ? "fail" : "ok"],
  ].map(([l,v,c]) => `<div class="chip"><span>${esc(l)}</span><b class="${c}">${esc(v)}</b></div>`).join("");
  document.getElementById("bl-body").innerHTML = (bl.items || []).map(i =>
    `<tr><td class="mono">AS${esc(i.asn)}</td><td>${esc(i.note || "")}</td></tr>`
  ).join("") || '<tr><td colspan="2" style="color:var(--muted)">空</td></tr>';
  document.getElementById("bl-err-chips").innerHTML = [
    ["更新错误合计", upd.error_count ?? 0, (upd.error_count ? "fail" : "ok")],
    ["lookup 失败", upd.lookup_fail_count ?? 0, "warn"],
    ["analyze 错误", upd.analyze_error_count ?? 0, "warn"],
    ["暂停扩黑次数", upd.hit_pause_count ?? 0, ""],
    ["历史新增记录", upd.added_total ?? 0, "accent"],
  ].map(([l,v,c]) => `<div class="chip"><span>${esc(l)}</span><b class="${c}">${esc(v)}</b></div>`).join("");
  document.getElementById("bl-added").innerHTML = (upd.recent_added || []).slice().reverse().map(a =>
    `<tr><td class="mono">AS${esc(a.asn)}</td><td class="mono">${esc(a.log || "")}</td></tr>`
  ).join("") || '<tr><td colspan="2" style="color:var(--muted)">暂无自动新增</td></tr>';
}

function rateCls(r) {
  if (r == null) return "";
  if (r >= 70) return "ok";
  if (r >= 40) return "warn";
  return "fail";
}
function renderRates(rates) {
  rates = rates || {};
  const order = ["1h", "3h", "12h"];
  const labels = { "1h": "近 1 小时", "3h": "近 3 小时", "12h": "近 12 小时" };
  const cards = order.map(k => {
    const b = rates[k] || {};
    const r = b.success_rate;
    const val = r == null ? "--" : (r + "%");
    return `<div class="rate-item">
      <div class="rate-top">
        <span class="rate-label">${esc(labels[k] || k)}</span>
        <span class="rate-total">${b.total ?? 0} 次</span>
      </div>
      <div class="rate-value ${rateCls(r)}">${esc(val)}</div>
      <div class="rate-breakdown">
        <span class="ok">成功 ${b.ok ?? 0}</span>
        <span class="fail">失败 ${b.fail ?? 0}</span>
        <span class="warn">风控 ${b.risk ?? 0}</span>
      </div>
    </div>`;
  });
  const el = document.getElementById("rate-kpis");
  if (el) el.innerHTML = cards.join("");
}

function renderStats(s) {
  s = s || {};
  if (s.rates) renderRates(s.rates);
  document.getElementById("stats-chips").innerHTML = [
    ["CPA", s.cpa ?? "--", "accent"],
    ["CPA 变化", s.cpa_delta ?? "--", "ok"],
    ["本批成功", s.batch_ok ?? 0, "ok"],
    ["本批失败", s.batch_fail ?? 0, "fail"],
    ["jsonl ok", s.jsonl_ok ?? 0, "ok"],
    ["jsonl risk", s.jsonl_risk ?? 0, "warn"],
  ].map(([l,v,c]) => `<div class="chip"><span>${esc(l)}</span><b class="${c}">${esc(v)}</b></div>`).join("");
  const days = Object.entries(s.by_day || {}).sort((a,b) => b[0].localeCompare(a[0])).slice(0, 10);
  document.getElementById("stats-day").innerHTML = days.length ? days.map(([d, v]) =>
    `<tr><td class="mono">${esc(d)}</td><td class="ok">${v.ok||0}</td><td class="warn">${v.risk||0}</td><td class="fail">${v.fail||0}</td></tr>`
  ).join("") : '<tr><td colspan="4" style="color:var(--muted)">无 jsonl 数据</td></tr>';
}
function render(d) {
  document.getElementById("clock").textContent = d.ts_human || "--";
  document.getElementById("logname").textContent =
    (d.log_name || d.log || "--") + (d.process && d.process.etime ? " / 用时 " + d.process.etime : "");
  const on = !!(d.process && d.process.running);
  document.getElementById("run-dot").className = "dot " + (on ? "on" : (d.ended ? "done" : "off"));
  let runLabel = "已停止";
  if (d.process && d.process.orch_running) runLabel = "编排运行 #" + d.process.orch_pid;
  else if (d.process && d.process.batch_running) runLabel = "单批运行 #" + d.process.batch_pid;
  else if (d.ended) runLabel = "已完成";
  document.getElementById("run-label").textContent = runLabel;
  document.getElementById("run-status").setAttribute("aria-label", "任务状态：" + runLabel);
  const sync = document.getElementById("sync-label");
  if (sync) {
    sync.textContent = "实时更新";
    sync.className = "badge";
  }
  document.getElementById("ctrl-status").textContent = on ? "运行中" : "空闲";
  document.getElementById("btn-start").disabled = on;
  document.getElementById("btn-stop").disabled = !on;
  fillControl(d);

  const kpis = [
    ["本批成功", d.ok ?? 0, "ok", "目标 " + (d.target ?? "--")],
    ["本批失败", d.fail ?? 0, "fail", d.success_rate != null ? "成功率 " + d.success_rate + "%" : "暂无数据"],
    ["CPA 总量", d.cpa ?? "--", "accent", "较基线 " + (d.cpa_delta != null ? ((Number(d.cpa_delta) >= 0 ? "+" : "") + d.cpa_delta) : "--")],
    ["正常 / 风控", (d.bot0 ?? 0) + " / " + (d.bot1 ?? 0), (d.bot1 ?? 0) > 0 ? "warn" : "ok", "注册结果采样"],
    ["黑名单 ASN", (d.blacklist && d.blacklist.count) ?? "--", "accent", "更新错误 " + ((d.blacklist_update && d.blacklist_update.error_count) ?? 0)],
    ["预计完成", d.ended ? "已完成" : (d.eta || "--"), "", "并发 " + (d.workers ?? "--") + (d.rate_per_min != null ? " / " + d.rate_per_min + " 每分钟" : "")],
  ];
  document.getElementById("kpis").innerHTML = kpis.map(([label, val, cls, sub]) =>
    `<div class="metric"><div class="label">${esc(label)}</div><div class="value ${cls}">${esc(val)}</div><div class="sub">${esc(sub)}</div></div>`
  ).join("");
  renderRates(d.rates || {});
  const ru = document.getElementById("rates-updated");
  if (ru && d.ts_human) ru.textContent = "数据更新 " + d.ts_human;

  const pct = Math.min(100, Number(d.progress_pct) || 0);
  document.getElementById("bar").style.width = pct + "%";
  document.getElementById("prog-text").textContent = (d.ok ?? 0) + " / " + (d.target ?? 0) + " (" + pct + "%)";
  document.getElementById("prog-sub").textContent =
    "尝试 " + (d.done_attempts ?? 0) + " / " + (on ? "进程运行中" : "未运行")
    + (d.ended ? " / 结束：成功 " + d.ended.success + "，失败 " + d.ended.fail : "");

  renderBlacklist(d.blacklist, d.blacklist_update);
  // light stats from snapshot
  renderStats({
    cpa: d.cpa, cpa_delta: d.cpa_delta, base_cpa: d.base_cpa,
    batch_ok: d.ok, batch_fail: d.fail,
    jsonl_ok: "--", jsonl_risk: "--",
    by_day: {}, refreshed_at: d.ts_human,
  });

  const wset = new Set([...(Object.keys(d.worker_ok || {})), ...(Object.keys(d.worker_fail || {}))]);
  const ws = [...wset].sort((a, b) => parseInt(a.slice(1)) - parseInt(b.slice(1)));
  document.getElementById("workers-stats").innerHTML = ws.length ? ws.map(w =>
    `<div class="chip"><span>${esc(w)}</span><b><span class="ok">${d.worker_ok && d.worker_ok[w] || 0}</span> <span style="color:var(--muted)">/</span> <span class="fail">${d.worker_fail && d.worker_fail[w] || 0}</span></b></div>`
  ).join("") : '<span style="color:var(--muted)">暂无</span>';
  const fk = Object.entries(d.fail_kinds || {}).sort((a, b) => b[1] - a[1]);
  document.getElementById("fails").innerHTML = fk.length ? fk.map(([k, v]) =>
    `<div class="chip"><span>${esc(k)}</span><b class="fail">${v}</b></div>`
  ).join("") : '<span style="color:var(--muted)">暂无失败</span>';
  document.getElementById("ok-body").innerHTML = (d.recent_ok || []).map(r =>
    `<tr><td class="mono">${esc(r.t)}</td><td>${esc(r.w)}</td><td class="mono">${esc(r.email)}</td></tr>`
  ).join("") || '<tr><td colspan="3" style="color:var(--muted)">暂无记录</td></tr>';
  document.getElementById("fail-body").innerHTML = (d.recent_fail || []).map(r =>
    `<tr><td class="mono">${esc(r.t)}</td><td>${esc(r.w)}</td><td>${esc(r.kind)}</td><td class="mono">${esc(r.msg)}</td></tr>`
  ).join("") || '<tr><td colspan="4" style="color:var(--muted)">暂无记录</td></tr>';
  document.getElementById("tail").textContent = (d.tail || []).join("\n");
  document.getElementById("footer").textContent =
    "服务 " + location.host + " / 日志 " + (d.log || "") + " / 2 秒轮询 / "
    + (d.log_size ? (d.log_size / 1024).toFixed(0) + " KB" : "0 KB")
    + " / 黑名单 " + ((d.blacklist && d.blacklist.count) || 0) + " ASN";
}
syncThemeButtons();
initHelp();
loadTokenField();
refresh();
setInterval(refresh, 2000);
// full stats once on load
refreshStats(false);
refreshRecovery();
setInterval(refreshRecovery, 5000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "GrokRegister"
    sys_version = ""

    def version_string(self):
        return self.server_version

    def log_message(self, fmt, *args):
        msg = args[0] if args else ""
        if "/api/status" in str(msg):
            return
        super().log_message(fmt, *args)

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'none'; img-src 'self' data:; "
            "font-src 'self'; connect-src 'self'; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
        )
        # No wildcard CORS — panel is same-origin. Optional explicit origin via env.
        allow = str(os.environ.get("MONITOR_CORS_ORIGIN", "") or "").strip()
        if allow and allow != "*":
            self.send_header("Access-Control-Allow-Origin", allow)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def _auth_header(self) -> str:
        return (
            self.headers.get("Authorization")
            or self.headers.get("X-Monitor-Token")
            or ""
        )

    def _require_write(self) -> bool:
        if check_token_optional_read(self._auth_header(), write=True):
            return True
        self._json(401, {"ok": False, "error": "unauthorized: set MONITOR_TOKEN and pass Authorization: Bearer <token>"})
        return False

    def _require_read(self) -> bool:
        if check_token_optional_read(self._auth_header(), write=False):
            return True
        self._json(401, {"ok": False, "error": "unauthorized: enter the current monitor token"})
        return False

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if n <= 0:
            return {}
        if n > MAX_REQUEST_BODY:
            raise OverflowError("request body too large")
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception as exc:
            raise ValueError("invalid JSON body") from exc
        if not isinstance(body, dict):
            raise ValueError("JSON body must be an object")
        return body

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if u.path in FONT_ASSETS:
            path = FONT_ASSETS[u.path]
            if path.is_file():
                self._send(200, path.read_bytes(), "font/woff2")
            else:
                self._send(404, b"not found", "text/plain")
            return
        if u.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        if u.path == "/api/health":
            self._json(200, {"ok": True})
            return
        if u.path in ("/api/status", "/api/blacklist", "/api/stats", "/api/control", "/api/recovery"):
            if not self._require_read():
                return
        if u.path == "/api/status":
            try:
                self._json(200, snapshot())
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if u.path == "/api/blacklist":
            try:
                bl = read_blacklist()
                bl["update"] = blacklist_update_errors()
                self._json(200, bl)
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if u.path == "/api/stats":
            try:
                self._json(200, success_stats())
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if u.path == "/api/control":
            self._json(200, load_control())
            return
        if u.path == "/api/recovery":
            try:
                self._json(200, recovery_status())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        u = urlparse(self.path)
        # All POST endpoints require MONITOR_TOKEN
        if not self._require_write():
            return
        try:
            body = self._read_body()
        except OverflowError as exc:
            self._json(413, {"ok": False, "error": str(exc)})
            return
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        if u.path == "/api/control":
            try:
                self._json(200, save_control(body))
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if u.path == "/api/start":
            try:
                if body:
                    save_control(body)
                mode = (body or {}).get("mode") or load_control().get("mode") or "batch"
                if mode == "batch":
                    self._json(200, start_batch_only())
                else:
                    self._json(200, start_orch())
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if u.path == "/api/stop":
            try:
                self._json(200, kill_all())
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if u.path == "/api/recovery/start":
            try:
                with START_LOCK:
                    result = start_recovery((body or {}).get("scope") or "pending")
                self._json(200 if result.get("ok") else 409, result)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if u.path == "/api/recovery/stop":
            try:
                self._json(200, stop_recovery())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if u.path == "/api/blacklist/refresh":
            try:
                bl = read_blacklist()
                bl["update"] = blacklist_update_errors()
                self._json(200, bl)
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if u.path == "/api/blacklist/reset":
            try:
                from webui.blacklist_ops import reset_blacklist as _reset_bl
            except ImportError:
                try:
                    from blacklist_ops import reset_blacklist as _reset_bl  # type: ignore
                except ImportError:
                    _reset_bl = None
            if _reset_bl is None:
                self._json(501, {"ok": False, "error": "blacklist_ops unavailable"})
                return
            try:
                mode = (body or {}).get("mode") or "baseline"
                self._json(200, _reset_bl(mode))
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if u.path == "/api/stats/refresh":
            try:
                self._json(200, success_stats())
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        self._send(404, b"not found", "text/plain")


def main():
    host = BIND_HOST
    tok = expected_token()
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.strip().lower() == "localhost"
    if not tok and not loopback:
        raise SystemExit(
            "MONITOR_TOKEN is required when MONITOR_HOST is not loopback"
        )
    ThreadingHTTPServer.allow_reuse_address = True
    try:
        httpd = ThreadingHTTPServer((host, BIND_PORT), Handler)
    except OSError as e1:
        raise SystemExit(
            f"cannot bind {BIND_HOST}:{BIND_PORT} ({e1}); "
            "set MONITOR_HOST/MONITOR_PORT (no 0.0.0.0 fallback)"
        )
    if not tok:
        print(
            "[monitor] WARNING: MONITOR_TOKEN unset — write APIs (start/stop/control) will return 401",
            flush=True,
        )
    print(f"[monitor] http://{host}:{BIND_PORT}/  (bound {host}:{BIND_PORT})", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
