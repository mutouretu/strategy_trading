from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from ..domain import StrategyStatus
from ..infrastructure.sqlite_store import SQLiteStore


class StrategySupervisor:
    """Starts one shared scheduler process for every strategy.

    Configuration is locked before spawning. A spawn failure never unlocks it.
    """

    def __init__(
        self,
        store: SQLiteStore,
        log_dir: str | Path = "runtime/logs",
        pid_path: str | Path | None = None,
    ) -> None:
        self.store = store
        # Capture every process-management path at construction time.  A
        # scheduler is detached and may inherit a different working directory;
        # relative PID/DB paths would then point at different files and allow a
        # second scheduler to start against the same database.
        self.db_path = store.path.expanduser().resolve()
        self.log_dir = Path(log_dir).expanduser().resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.pid_path = (
            Path(pid_path).expanduser().resolve()
            if pid_path is not None
            else self.log_dir.parent / "scheduler.pid"
        )
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self._scheduler: subprocess.Popen | None = None
        self._spawn_lock = threading.Lock()

    def start(self, strategy_id: str) -> int:
        config = self.store.get_strategy(strategy_id)
        if config is None or config.archived:
            raise KeyError(strategy_id)
        if self.is_running(strategy_id):
            return self._scheduler_pid()

        self.store.mark_started(strategy_id)
        log_path = self.log_dir / "scheduler.log"
        command = [
            sys.executable,
            "-m",
            "gridtrader.runtime.scheduler",
            "--db",
            str(self.db_path),
            "--pid-file",
            str(self.pid_path),
        ]
        try:
            pid = self._ensure_scheduler(command, log_path)
        except Exception:
            self.store.set_status(strategy_id, StrategyStatus.ERROR)
            raise
        self.store.heartbeat(strategy_id, "scheduler-starting", pid)
        return pid

    def stop(self, strategy_id: str) -> None:
        if self.store.get_strategy(strategy_id) is None:
            raise KeyError(strategy_id)
        # Stopping one strategy only changes its state. The shared scheduler and
        # every other strategy remain untouched.
        self.store.mark_runtime_stopped(strategy_id)
        self.store.set_status(strategy_id, StrategyStatus.STOPPED)

    def is_running(self, strategy_id: str) -> bool:
        config = self.store.get_strategy(strategy_id)
        if config is None or config.status not in {
            StrategyStatus.STARTING,
            StrategyStatus.RUNNING,
            StrategyStatus.ERROR,
        }:
            return False
        return self._scheduler_pid(required=False) > 0

    def _ensure_scheduler(self, command: list[str], log_path: Path) -> int:
        with self._spawn_lock:
            if self._scheduler is not None and self._scheduler.poll() is None:
                return int(self._scheduler.pid)
            existing_pid = self._scheduler_pid(required=False)
            if existing_pid:
                return existing_pid
            with log_path.open("a", encoding="utf-8") as log_file:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            self.pid_path.write_text(str(process.pid), encoding="utf-8")
            self._scheduler = process
            return int(process.pid)

    def _scheduler_pid(self, required: bool = True) -> int:
        if self._scheduler is not None and self._scheduler.poll() is None:
            return int(self._scheduler.pid)
        try:
            pid = int(self.pid_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            if required:
                raise RuntimeError("scheduler is not running")
            return 0
        if self._pid_alive(pid):
            return pid
        try:
            self.pid_path.unlink()
        except FileNotFoundError:
            pass
        if required:
            raise RuntimeError("scheduler is not running")
        return 0

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except ProcessLookupError:
            return False
