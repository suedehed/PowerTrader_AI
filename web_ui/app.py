from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, Optional
import sys

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

DEFAULT_SETTINGS = {
    "main_neural_dir": r"C:\PowerTrader_AI",
    "coins": ["BTC", "ETH", "XRP", "BNB", "DOGE"],
    "default_timeframe": "1hour",
    "timeframes": [
        "1min",
        "5min",
        "15min",
        "30min",
        "1hour",
        "2hour",
        "4hour",
        "8hour",
        "12hour",
        "1day",
        "1week",
    ],
    "candles_limit": 120,
    "ui_refresh_seconds": 1.0,
    "chart_refresh_seconds": 10.0,
    "hub_data_dir": "",
    "script_neural_runner2": "pt_thinker.py",
    "script_neural_trainer": "pt_trainer.py",
    "script_trader": "pt_trader.py",
    "auto_start_scripts": False,
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_FILE = PROJECT_ROOT / "gui_settings.json"


@dataclass
class ProcInfo:
    name: str
    script_name: str
    proc: Optional["subprocess.Popen[str]"] = None


def _safe_read_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_settings() -> dict:
    data = DEFAULT_SETTINGS.copy()
    on_disk = _safe_read_json(SETTINGS_FILE)
    if isinstance(on_disk, dict):
        data.update(on_disk)
    return data


class ProcessManager:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.project_dir = PROJECT_ROOT
        hub_dir = self.settings.get("hub_data_dir") or str(self.project_dir / "hub_data")
        self.hub_dir = Path(hub_dir).resolve()
        self.hub_dir.mkdir(parents=True, exist_ok=True)
        self.runner_ready_path = self.hub_dir / "runner_ready.json"
        self._runner_ready_pending = False
        self._lock = threading.Lock()

        self.proc_neural = ProcInfo("Neural Runner", self.settings["script_neural_runner2"])
        self.proc_trader = ProcInfo("Trader", self.settings["script_trader"])

        self._runner_logs: Deque[str] = deque(maxlen=400)
        self._trader_logs: Deque[str] = deque(maxlen=400)

        self._runner_q: "queue.Queue[str]" = queue.Queue()
        self._trader_q: "queue.Queue[str]" = queue.Queue()
        self._log_thread = threading.Thread(target=self._drain_log_queues, daemon=True)
        self._log_thread.start()

    def _drain_log_queues(self) -> None:
        while True:
            try:
                line = self._runner_q.get(timeout=0.2)
                self._runner_logs.append(line)
            except queue.Empty:
                pass
            try:
                line = self._trader_q.get(timeout=0.2)
                self._trader_logs.append(line)
            except queue.Empty:
                pass

    def _reader_thread(self, proc: "subprocess.Popen[str]", q: "queue.Queue[str]", prefix: str) -> None:
        try:
            while True:
                line = proc.stdout.readline() if proc.stdout else ""
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue
                q.put(f"{prefix}{line.rstrip()}")
        except Exception:
            pass
        finally:
            q.put(f"{prefix}[process exited]")

    def _start_process(self, info: ProcInfo, q: "queue.Queue[str]", prefix: str) -> None:
        import subprocess

        if info.proc and info.proc.poll() is None:
            return
        script_path = self.project_dir / info.script_name
        if not script_path.is_file():
            raise FileNotFoundError(f"Missing script: {script_path}")

        env = os.environ.copy()
        env["POWERTRADER_HUB_DIR"] = str(self.hub_dir)

        info.proc = subprocess.Popen(
            [sys.executable, "-u", str(script_path)],
            cwd=str(self.project_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        t = threading.Thread(target=self._reader_thread, args=(info.proc, q, prefix), daemon=True)
        t.start()

    def _stop_process(self, info: ProcInfo) -> None:
        if not info.proc or info.proc.poll() is not None:
            return
        try:
            info.proc.terminate()
        except Exception:
            pass

    def _read_runner_ready(self) -> Dict[str, object]:
        try:
            if self.runner_ready_path.is_file():
                with self.runner_ready_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {"ready": False}

    def _poll_runner_ready_then_start_trader(self) -> None:
        while True:
            with self._lock:
                if not self._runner_ready_pending:
                    return
                runner_proc = self.proc_neural.proc
            if not runner_proc or runner_proc.poll() is not None:
                with self._lock:
                    self._runner_ready_pending = False
                return
            st = self._read_runner_ready()
            if bool(st.get("ready", False)):
                with self._lock:
                    self._runner_ready_pending = False
                self._start_process(self.proc_trader, self._trader_q, "[TRADER] ")
                return
            time.sleep(0.25)

    def start_all(self) -> None:
        with self._lock:
            try:
                with self.runner_ready_path.open("w", encoding="utf-8") as f:
                    json.dump({"timestamp": time.time(), "ready": False, "stage": "starting"}, f)
            except Exception:
                pass
            self._start_process(self.proc_neural, self._runner_q, "[RUNNER] ")
            self._runner_ready_pending = True
        threading.Thread(target=self._poll_runner_ready_then_start_trader, daemon=True).start()

    def stop_all(self) -> None:
        with self._lock:
            self._runner_ready_pending = False
            self._stop_process(self.proc_trader)
            self._stop_process(self.proc_neural)

    def status(self) -> dict:
        runner_running = bool(self.proc_neural.proc and self.proc_neural.proc.poll() is None)
        trader_running = bool(self.proc_trader.proc and self.proc_trader.proc.poll() is None)
        return {
            "runner": {
                "running": runner_running,
                "pid": self.proc_neural.proc.pid if runner_running else None,
            },
            "trader": {
                "running": trader_running,
                "pid": self.proc_trader.proc.pid if trader_running else None,
            },
            "runner_ready": self._read_runner_ready(),
            "auto_start_pending": self._runner_ready_pending,
        }

    def logs(self) -> dict:
        return {
            "runner": list(self._runner_logs),
            "trader": list(self._trader_logs),
        }


app = FastAPI()
manager = ProcessManager()

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def api_status() -> JSONResponse:
    return JSONResponse(manager.status())


@app.post("/api/start")
def api_start() -> JSONResponse:
    manager.start_all()
    return JSONResponse({"status": "starting"})


@app.post("/api/stop")
def api_stop() -> JSONResponse:
    manager.stop_all()
    return JSONResponse({"status": "stopping"})


@app.get("/api/logs")
def api_logs() -> JSONResponse:
    return JSONResponse(manager.logs())
