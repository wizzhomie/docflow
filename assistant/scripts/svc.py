#!/usr/bin/env python3
"""Deployment and process supervision for the DocFlow assistant.

The demo runs inside a container with no systemd and no Docker daemon, so this script is
the deployment tool. It owns exactly two processes — `llama-server` and the operator — and
knows how to start them, adopt ones that are already running, wait until they answer,
restart them and keep a supervisor that puts a crashed one back up.

    scripts/deploy.sh status     # pids, readiness, the real /api/health
    scripts/deploy.sh deploy     # start what is down, then run the supervisor
    scripts/deploy.sh start      # start what is down (no supervisor)
    scripts/deploy.sh restart    # stop everything, start again, supervisor on
    scripts/deploy.sh stop
    scripts/deploy.sh logs [model|operator|supervisor] [--lines 50]

Exit code is 0 only when both components are alive and answering, so the command can be
used in a health check.

Processes are identified by their real argv, never by a substring of the command line: a
`grep llama-server` in someone's shell must not be mistaken for the model.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from docflow_operator.config import get_settings  # noqa: E402

RUN_DIR = ROOT / '.run'
LOG_DIR = RUN_DIR / 'logs'
PID_DIR = RUN_DIR / 'pids'
SUPERVISOR = 'supervisor'


# --- what the two processes look like --------------------------------------

def _is_model(argv: list[str]) -> bool:
    return bool(argv) and Path(argv[0]).name == 'llama-server'


def _is_operator(argv: list[str]) -> bool:
    return 'uvicorn' in argv and any(arg.startswith('docflow_operator.server:app') for arg in argv)


def _is_supervisor(argv: list[str]) -> bool:
    return 'supervise' in argv and any(Path(arg).name == 'svc.py' for arg in argv)


@dataclass(frozen=True)
class Component:
    name: str
    script: str
    liveness_url: str
    ready_timeout: float
    matches: object


def components(settings) -> list[Component]:
    return [
        Component('model', 'scripts/start_model.sh', f'{settings.model_url}/health', 120.0, _is_model),
        # Liveness is the API itself, not /api/health: the operator must not be restarted
        # just because DocFlow or the model is briefly down.
        Component('operator', 'scripts/start_operator.sh',
                  f'http://127.0.0.1:{settings.port}/api/config', 60.0, _is_operator),
    ]


# --- small helpers ----------------------------------------------------------

def _mkdirs() -> None:
    for directory in (RUN_DIR, LOG_DIR, PID_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def _pid_path(name: str) -> Path:
    return PID_DIR / f'{name}.pid'


def _log_path(name: str) -> Path:
    return LOG_DIR / f'{name}.log'


def _log(message: str) -> None:
    print(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {message}', flush=True)


def _argv(pid: int) -> list[str]:
    try:
        raw = Path(f'/proc/{pid}/cmdline').read_bytes()
    except OSError:
        return []
    return [part.decode('utf-8', 'replace') for part in raw.split(b'\0') if part]


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_pid(name: str) -> int | None:
    try:
        return int(_pid_path(name).read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return None


def _write_pid(name: str, pid: int) -> None:
    _mkdirs()
    _pid_path(name).write_text(f'{pid}\n', encoding='utf-8')


def _clear_pid(name: str) -> None:
    try:
        _pid_path(name).unlink()
    except OSError:
        pass


def _managed_pid(name: str, matches) -> int | None:
    """The pid from the pid file, but only if that process is still the one we started."""
    pid = _read_pid(name)
    if pid and _alive(pid) and matches(_argv(pid)):
        return pid
    if pid:
        _clear_pid(name)  # stale file from a previous container life
    return None


def _find_pid(matches) -> int | None:
    """Find a process we started in an earlier session, before pid files existed."""
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        if matches(_argv(pid)):
            return pid
    return None


def _url_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status < 500
    except (urllib.error.URLError, OSError):
        return False


def _url_json(url: str, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _tail(path: Path, lines: int = 8) -> str:
    try:
        content = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return ''
    return '\n'.join(content[-lines:])


def _wait_ready(component: Component, timeout: float | None = None) -> bool:
    deadline = time.monotonic() + (component.ready_timeout if timeout is None else timeout)
    while time.monotonic() < deadline:
        if _url_ok(component.liveness_url):
            return True
        time.sleep(0.5)
    return False


# --- starting and stopping --------------------------------------------------

def _spawn(component: Component) -> int:
    _mkdirs()
    log = _log_path(component.name).open('ab')
    try:
        process = subprocess.Popen(
            ['bash', component.script],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, env=os.environ.copy(),
        )
    finally:
        log.close()
    _write_pid(component.name, process.pid)
    return process.pid


def _stop(component: Component, timeout: float = 10.0) -> bool:
    pid = _managed_pid(component.name, component.matches) or _find_pid(component.matches)
    if not pid:
        _clear_pid(component.name)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        _clear_pid(component.name)
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.2)
    if _alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    _clear_pid(component.name)
    return True


def _start(component: Component) -> bool:
    pid = _managed_pid(component.name, component.matches) or _find_pid(component.matches)
    if pid and _alive(pid):
        if _wait_ready(component, timeout=10.0):
            if not _pid_path(component.name).exists():
                _write_pid(component.name, pid)
            return True
        _log(f'{component.name}: process {pid} exists but does not answer, restarting it')
        _stop(component)
    _log(f'{component.name}: starting')
    _spawn(component)
    if _wait_ready(component):
        _log(f'{component.name}: ready')
        return True
    _log(f'{component.name}: did not become ready in {int(component.ready_timeout)}s; '
         f'last log lines:\n{_tail(_log_path(component.name))}')
    return False


def stop_all() -> None:
    settings = get_settings()
    if _stop_supervisor():
        _log('supervisor: stopped')
    for component in reversed(components(settings)):
        if _stop(component):
            _log(f'{component.name}: stopped')


def start_all() -> bool:
    settings = get_settings()
    for component in components(settings):
        if not _start(component):
            return False
    return True


# --- supervisor -------------------------------------------------------------

def _supervisor_pid() -> int | None:
    return _managed_pid(SUPERVISOR, _is_supervisor) or _find_pid(_is_supervisor)


def _stop_supervisor() -> bool:
    pid = _supervisor_pid()
    if not pid:
        _clear_pid(SUPERVISOR)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        _clear_pid(SUPERVISOR)
        return False
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.2)
    _clear_pid(SUPERVISOR)
    return True


def _sleep(seconds: float, stopping: dict) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not stopping['value']:
        time.sleep(0.2)


def supervise() -> int:
    """Keep both components alive until asked to stop."""
    _mkdirs()
    _write_pid(SUPERVISOR, os.getpid())
    stopping = {'value': False}

    def handle(signum, frame):  # noqa: ARG001 - signal handlers get two arguments
        stopping['value'] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    settings = get_settings()
    managed = components(settings)
    failures = {component.name: 0 for component in managed}
    _log(f'supervisor started (pid {os.getpid()}), watching: '
         + ', '.join(component.name for component in managed))

    while not stopping['value']:
        for component in managed:
            if stopping['value']:
                break
            pid = _managed_pid(component.name, component.matches) or _find_pid(component.matches)
            if pid and _alive(pid) and _url_ok(component.liveness_url):
                failures[component.name] = 0
                continue
            failures[component.name] += 1
            delay = min(2 * failures[component.name], 30)
            _log(f'{component.name}: down (attempt {failures[component.name]}), restarting in {delay}s')
            _sleep(delay, stopping)
            if stopping['value']:
                break
            _stop(component)
            if not _start(component):
                _log(f'{component.name}: restart failed; the supervisor will try again')
        _sleep(4, stopping)

    _clear_pid(SUPERVISOR)
    _log('supervisor stopped')
    return 0


def ensure_supervisor() -> int | None:
    pid = _supervisor_pid()
    if pid:
        return pid
    _mkdirs()
    log = _log_path(SUPERVISOR).open('ab')
    try:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), 'supervise'],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, env=os.environ.copy(),
        )
    finally:
        log.close()
    _write_pid(SUPERVISOR, process.pid)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if _alive(process.pid):
            return process.pid
        time.sleep(0.2)
    return None


# --- commands ---------------------------------------------------------------

def status() -> int:
    settings = get_settings()
    rows = []
    ok = True
    for component in components(settings):
        pid = _managed_pid(component.name, component.matches) or _find_pid(component.matches)
        alive = _alive(pid)
        ready = _url_ok(component.liveness_url) if alive else False
        ok = ok and alive and ready
        rows.append((component.name, pid, alive, ready))
    supervisor = _supervisor_pid()
    print(f'{"component":10s} {"pid":>8s}  {"alive":5s}  {"ready":5s}')
    for name, pid, alive, ready in rows:
        print(f'{name:10s} {pid or "-":>8}  {"yes" if alive else "no":5s}  {"yes" if ready else "no":5s}')
    print(f'{SUPERVISOR:10s} {supervisor or "-":>8}  {"yes" if supervisor else "no":5s}  '
          f'{"yes" if supervisor else "-":5s}')
    health = _url_json(f'http://127.0.0.1:{settings.port}/api/health', timeout=15)
    if health:
        print(f'health: ok={health.get("ok")} '
              f'docflow={health.get("docflow", {}).get("ok")} model={health.get("model", {}).get("ok")}')
    else:
        print('health: operator /api/health did not answer')
    return 0 if ok else 1


def deploy() -> int:
    if not start_all():
        return 1
    if ensure_supervisor():
        _log('supervisor is running')
    else:
        _log('supervisor did not start; the service will not survive a crash')
    return status()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Deploy and supervise the DocFlow assistant.')
    parser.add_argument('command', choices=['status', 'start', 'stop', 'restart', 'deploy',
                                            'supervise', 'logs'])
    parser.add_argument('component', nargs='?', help='for logs: model, operator or supervisor')
    parser.add_argument('--lines', type=int, default=40, help='for logs: how many lines')
    args = parser.parse_args(argv)

    if args.command == 'status':
        return status()
    if args.command == 'start':
        return 0 if start_all() else 1
    if args.command == 'stop':
        stop_all()
        return 0
    if args.command == 'restart':
        stop_all()
        if not start_all():
            return 1
        ensure_supervisor()
        return status()
    if args.command == 'deploy':
        return deploy()
    if args.command == 'supervise':
        return supervise()
    if args.command == 'logs':
        name = args.component or SUPERVISOR
        print(_tail(_log_path(name), max(1, args.lines)))
        return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
