from __future__ import annotations

import argparse
import fcntl
import os
import signal
import time
import uuid
from decimal import Decimal
from pathlib import Path

from .binance import BinanceFuturesExchange
from .config import binance_base_url, binance_credentials, load_environment
from .domain import Mode, StrategyStatus
from .engine import TradingEngine
from .exchange import Exchange
from .position_coordinator import PositionCoordinator
from .snapshot_exchange import SnapshotExchange
from .store import SQLiteStore


ACTIVE_STATUSES = {
    StrategyStatus.STARTING,
    StrategyStatus.RUNNING,
    StrategyStatus.ERROR,
}


class StrategyScheduler:
    """Runs every strategy as a lightweight state machine in one process."""

    def __init__(
        self,
        store: SQLiteStore,
        exchange: Exchange,
        *,
        clock=time.monotonic,
        pid: int | None = None,
        reconcile_interval_sec: float = 60.0,
        position_settlement_grace_sec: float = 0.0,
    ) -> None:
        self.store = store
        self.exchange = SnapshotExchange(exchange)
        self.clock = clock
        self.pid = os.getpid() if pid is None else pid
        self.run_id = uuid.uuid4().hex
        self.engines: dict[str, TradingEngine] = {}
        self.next_due: dict[str, float] = {}
        self.reconcile_interval_sec = max(5.0, reconcile_interval_sec)
        self.position_settlement_grace_sec = max(
            0.0,
            position_settlement_grace_sec,
        )
        self.next_reconcile_at = 0.0
        self.last_reconcile_error = ""

    def run_once(self, now: float | None = None) -> int:
        current_time = self.clock() if now is None else now
        configs = {
            config.strategy_id: config
            for config in self.store.list_strategies()
            if config.has_started and config.status in ACTIVE_STATUSES
        }

        for strategy_id in set(self.engines) - set(configs):
            self.engines.pop(strategy_id, None)
            self.next_due.pop(strategy_id, None)

        if not configs:
            self.next_reconcile_at = current_time + self.reconcile_interval_sec
            return 0

        pending_action_ids = self.store.pending_cell_action_strategy_ids()
        due = [
            config
            for strategy_id, config in configs.items()
            if strategy_id in pending_action_ids
            or strategy_id not in self.next_due
            or current_time >= self.next_due[strategy_id]
        ]
        reconcile_due = current_time >= self.next_reconcile_at
        if not due and not reconcile_due:
            return 0

        open_qty_before = {
            (cell.strategy_id, cell.cell_id): cell.open_qty
            for cell in self.store.list_all_cells()
        }

        # Price and open-order responses are shared by every strategy with the
        # same symbol during this cycle.
        self.exchange.begin_cycle()
        processed = 0
        cycle_had_errors = False
        due_ids = {config.strategy_id for config in due}
        for config in sorted(due, key=lambda item: (item.symbol, item.strategy_id)):
            strategy_remains_active = True
            engine = self.engines.get(config.strategy_id)
            if engine is None:
                engine = TradingEngine(
                    self.store,
                    self.exchange,
                    config.strategy_id,
                    run_id=f"{self.run_id}-{config.strategy_id}",
                )
                self.engines[config.strategy_id] = engine
            try:
                if not self._strategy_is_active(config.strategy_id):
                    strategy_remains_active = False
                    continue
                engine.process_cell_actions()
                engine.tick()
                if not self._strategy_is_active(config.strategy_id):
                    strategy_remains_active = False
            except Exception as exc:
                # API stop/delete can race a scheduler cycle that already took
                # its configuration snapshot.  A disappeared or inactive
                # strategy is a lifecycle change, not an engine failure; never
                # let the stale cycle terminate the shared scheduler.
                if not self._strategy_is_active(config.strategy_id):
                    strategy_remains_active = False
                    continue
                cycle_had_errors = True
                try:
                    self.store.heartbeat_if_active(
                        config.strategy_id,
                        engine.run_id,
                        self.pid,
                        last_error=str(exc),
                    )
                    self.store.set_status_if_active(
                        config.strategy_id,
                        StrategyStatus.ERROR,
                    )
                except KeyError:
                    strategy_remains_active = False
            finally:
                if strategy_remains_active:
                    self.next_due[config.strategy_id] = current_time + config.poll_interval_sec
                else:
                    self.engines.pop(config.strategy_id, None)
                    self.next_due.pop(config.strategy_id, None)
                processed += 1

        if reconcile_due:
            for config in sorted(configs.values(), key=lambda item: (item.symbol, item.strategy_id)):
                if config.strategy_id in due_ids:
                    continue
                engine = self.engines.get(config.strategy_id)
                if engine is None:
                    engine = TradingEngine(
                        self.store,
                        self.exchange,
                        config.strategy_id,
                        run_id=f"{self.run_id}-{config.strategy_id}",
                    )
                    self.engines[config.strategy_id] = engine
                try:
                    if not self._strategy_is_active(config.strategy_id):
                        self.engines.pop(config.strategy_id, None)
                        self.next_due.pop(config.strategy_id, None)
                        continue
                    engine.sync_orders_only()
                    if not self._strategy_is_active(config.strategy_id):
                        self.engines.pop(config.strategy_id, None)
                        self.next_due.pop(config.strategy_id, None)
                except Exception as exc:
                    if not self._strategy_is_active(config.strategy_id):
                        self.engines.pop(config.strategy_id, None)
                        self.next_due.pop(config.strategy_id, None)
                        continue
                    cycle_had_errors = True
                    try:
                        self.store.heartbeat_if_active(
                            config.strategy_id,
                            engine.run_id,
                            self.pid,
                            last_error=str(exc),
                        )
                        self.store.set_status_if_active(
                            config.strategy_id,
                            StrategyStatus.ERROR,
                        )
                    except KeyError:
                        self.engines.pop(config.strategy_id, None)
                        self.next_due.pop(config.strategy_id, None)
            if cycle_had_errors:
                self.last_reconcile_error = "order synchronization failed; position rewrite skipped"
            else:
                try:
                    coordinator = PositionCoordinator(
                        self.store,
                        self.exchange,
                        self.run_id,
                        settlement_grace_sec=self.position_settlement_grace_sec,
                    )
                    protected_shortage_pools: set[tuple[str, str]] = set()
                    for cell in self.store.list_all_cells():
                        config = configs.get(cell.strategy_id)
                        if config is None:
                            continue
                        before = open_qty_before.get(
                            (cell.strategy_id, cell.cell_id),
                            Decimal("0"),
                        )
                        if cell.open_qty > before:
                            protected_shortage_pools.add(
                                (
                                    config.symbol,
                                    "LONG" if config.mode == Mode.LONG else "SHORT",
                                )
                            )
                    result = coordinator.reconcile(
                        self.engines,
                        protected_shortage_pools,
                    )
                    for strategy_id in result.rescan_strategy_ids:
                        self.next_due[strategy_id] = current_time
                    self.last_reconcile_error = ""
                except Exception as exc:
                    # Trading loops remain isolated from a failed account snapshot;
                    # the next audit retries without rewriting Cell ownership.
                    self.last_reconcile_error = str(exc)
            self.next_reconcile_at = current_time + self.reconcile_interval_sec
        return processed

    def _strategy_is_active(self, strategy_id: str) -> bool:
        config = self.store.get_strategy(strategy_id)
        return bool(
            config is not None
            and config.has_started
            and config.status in ACTIVE_STATUSES
        )

    def seconds_until_next_cycle(self, maximum: float = 1.0) -> float:
        if not self.next_due:
            return maximum
        delay = min(self.next_due.values()) - self.clock()
        return max(0.05, min(maximum, delay))


def run_scheduler_forever(scheduler: StrategyScheduler, stop_requested=lambda: False) -> None:
    while not stop_requested():
        scheduler.run_once()
        time.sleep(scheduler.seconds_until_next_cycle())


def main() -> int:
    load_environment()
    parser = argparse.ArgumentParser(description="Run all grid strategies in one scheduler process")
    parser.add_argument("--db", required=True)
    parser.add_argument("--pid-file", default="runtime/scheduler.pid")
    parser.add_argument("--base-url", default=binance_base_url())
    args = parser.parse_args()

    api_key, api_secret = binance_credentials(required=True)

    stopping = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    pid_path = Path(args.pid_file)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    with pid_path.open("a+", encoding="utf-8") as pid_file:
        try:
            fcntl.flock(pid_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        pid_file.seek(0)
        pid_file.truncate()
        pid_file.write(str(os.getpid()))
        pid_file.flush()

        store = SQLiteStore(args.db)
        exchange = BinanceFuturesExchange(api_key, api_secret, args.base_url)
        scheduler = StrategyScheduler(
            store,
            exchange,
            reconcile_interval_sec=float(
                os.getenv("GRID_POSITION_RECONCILE_INTERVAL_SEC", "60")
            ),
            position_settlement_grace_sec=float(
                os.getenv("GRID_POSITION_SETTLEMENT_GRACE_SEC", "15")
            ),
        )
        run_scheduler_forever(scheduler, lambda: stopping)
    try:
        pid_path.unlink()
    except FileNotFoundError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
