from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Service:
    name: str
    module: str
    port: int


SERVICES = [
    Service("orchestrator", "net_razor_orchestrator.main:app", 8010),
    Service("x-api", "x_api.main:app", 8011),
    Service("hn-api", "hn_api.main:app", 8012),
    Service("yt-api", "yt_api.main:app", 8013),
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _start_service(service: Service, *, host: str, reload: bool) -> subprocess.Popen:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        service.module,
        "--host",
        host,
        "--port",
        str(service.port),
    ]
    if reload:
        command.append("--reload")

    print(f"starting {service.name} on http://{host}:{service.port}", flush=True)
    return subprocess.Popen(command, cwd=_repo_root())


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def run_services(*, host: str, reload: bool) -> int:
    processes: list[subprocess.Popen] = []
    stopping = False

    def handle_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    previous_sigint = signal.signal(signal.SIGINT, handle_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, handle_stop)
    try:
        processes = [_start_service(service, host=host, reload=reload) for service in SERVICES]
        print("all services started; press Ctrl-C to stop", flush=True)

        while not stopping:
            for service, process in zip(SERVICES, processes, strict=True):
                return_code = process.poll()
                if return_code is not None:
                    print(
                        f"{service.name} exited with code {return_code}; stopping services",
                        flush=True,
                    )
                    stopping = True
                    break
            time.sleep(0.5)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        for process in processes:
            _stop_process(process)
        print("services stopped", flush=True)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run all Net-Razor HTTP services locally.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run_services(host=args.host, reload=args.reload))
