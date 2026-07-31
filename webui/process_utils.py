"""Process discovery and termination scoped to one project root."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

try:
    import psutil
except ImportError:  # Linux deployments can still use procfs during upgrades.
    psutil = None

try:
    from secure_files import atomic_write_text
except ImportError:  # running from webui/
    import sys

    ROOT = Path(__file__).resolve().parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from secure_files import atomic_write_text


def _cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
        return [part.decode(errors="replace") for part in raw.split(b"\0") if part]
    except (OSError, ValueError):
        pass
    if psutil is None:
        return []
    try:
        return psutil.Process(int(pid)).cmdline()
    except (psutil.Error, ValueError):
        return []


def _cwd(pid: int) -> Path | None:
    try:
        return Path(os.readlink(f"/proc/{int(pid)}/cwd")).resolve()
    except (OSError, ValueError):
        pass
    if psutil is None:
        return None
    try:
        return Path(psutil.Process(int(pid)).cwd()).resolve()
    except (psutil.Error, OSError, ValueError):
        return None


def _process_ids() -> list[int]:
    proc_root = Path("/proc")
    if proc_root.is_dir():
        try:
            return [int(entry.name) for entry in proc_root.iterdir() if entry.name.isdigit()]
        except OSError:
            pass
    if psutil is None:
        return []
    try:
        return psutil.pids()
    except (psutil.Error, OSError):
        return []


def _resolved_arg(arg: str, cwd: Path) -> Path | None:
    if not arg or arg.startswith("-"):
        return None
    candidate = Path(arg)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        return candidate.resolve()
    except OSError:
        return None


def process_matches(
    pid: int,
    root: str | os.PathLike[str],
    script_names: tuple[str, ...] | list[str],
) -> bool:
    project_root = Path(root).resolve()
    process_cwd = _cwd(pid)
    if process_cwd != project_root:
        return False
    expected = {(project_root / name).resolve() for name in script_names}
    return any(
        _resolved_arg(arg, process_cwd) in expected
        for arg in _cmdline(pid)
    )


def find_managed_processes(
    root: str | os.PathLike[str],
    script_names: tuple[str, ...] | list[str],
) -> list[dict]:
    found = []
    for pid in _process_ids():
        if not process_matches(pid, root, script_names):
            continue
        cmdline = _cmdline(pid)
        etime = ""
        try:
            pgid = os.getpgid(pid)
        except OSError:
            pgid = None
        try:
            result = subprocess.run(
                ["ps", "-o", "etime=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            etime = result.stdout.strip()
        except Exception:
            pass
        found.append(
            {
                "pid": pid,
                "pgid": pgid,
                "etime": etime,
                "cmd": " ".join(cmdline)[:240],
            }
        )
    return sorted(found, key=lambda item: item["pid"])


def write_pid_file(
    path: str | os.PathLike[str],
    pid: int,
) -> None:
    atomic_write_text(path, f"{int(pid)}\n")


def read_verified_pid_file(
    path: str | os.PathLike[str],
    root: str | os.PathLike[str],
    script_names: tuple[str, ...] | list[str],
) -> int | None:
    try:
        pid = int(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if process_matches(pid, root, script_names) else None


def terminate_managed_processes(
    root: str | os.PathLike[str],
    script_names: tuple[str, ...] | list[str],
    *,
    grace_seconds: float = 2.0,
) -> list[int]:
    processes = find_managed_processes(root, script_names)
    pids = {item["pid"] for item in processes}
    if not pids:
        return []

    groups: set[int] = set()
    direct: set[int] = set()
    for pid in pids:
        try:
            pgid = os.getpgid(pid)
        except OSError:
            continue
        if pgid == pid:
            groups.add(pgid)
        else:
            direct.add(pid)

    for pgid in groups:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            pass
    for pid in direct:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        if not find_managed_processes(root, script_names):
            break
        time.sleep(0.1)

    remaining = find_managed_processes(root, script_names)
    for item in remaining:
        pid = item["pid"]
        try:
            pgid = os.getpgid(pid)
            if pgid == pid:
                os.killpg(pgid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return sorted(pids)
