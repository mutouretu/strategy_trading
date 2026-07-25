from __future__ import annotations

import argparse
import fcntl
import os
import signal
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from ..infrastructure.binance import BinanceCoinMExchange, BinanceFuturesExchange
from ..shared.config import (
    binance_base_url,
    binance_coinm_base_url,
    binance_credentials,
    load_environment,
)
from ..domain import FuturesMarket, Mode, StrategyStatus
from ..application.engine import TradingEngine
from ..ports.exchange import Exchange
from ..application.position_coordinator import PositionCoordinator
from ..infrastructure.snapshot_exchange import SnapshotExchange
from ..infrastructure.sqlite_store import SQLiteStore


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
        exchange: Exchange | Mapping[FuturesMarket | str, Exchange],
        *,
        clock=time.monotonic,
        wall_clock=time.time,
        pid: int | None = None,
        reconcile_interval_sec: float = 60.0,
        position_settlement_grace_sec: float = 0.0,
        gap_threshold_sec: float = 5.0,
        audit_heartbeat_interval_sec: float = 30.0,
    ) -> None:
        self.store = store
        if isinstance(exchange, Mapping):
            self.exchanges = {
                FuturesMarket(market): SnapshotExchange(adapter)
                for market, adapter in exchange.items()
            }
        else:
            market = FuturesMarket(
                getattr(exchange, "market_type", FuturesMarket.USDM)
            )
            self.exchanges = {market: SnapshotExchange(exchange)}
        # Compatibility for diagnostics that expect the original attribute.
        self.exchange = next(iter(self.exchanges.values()))
        self.clock = clock
        self.wall_clock = wall_clock
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
        self.gap_threshold_sec = max(2.0, float(gap_threshold_sec))
        self.audit_heartbeat_interval_sec = max(
            5.0,
            float(audit_heartbeat_interval_sec),
        )
        self._last_loop_wall = float(self.wall_clock())
        self._last_audit_heartbeat_wall = self._last_loop_wall
        self.store.record_scheduler_run_start(
            self.run_id,
            self.pid,
            observed_at=self._utc_from_epoch(self._last_loop_wall),
        )

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
        for market in {config.market_type for config in configs.values()}:
            exchange = self.exchanges.get(market)
            if exchange is not None:
                exchange.begin_cycle()
        processed = 0
        cycle_had_errors = False
        due_ids = {config.strategy_id for config in due}
        for config in sorted(due, key=lambda item: (item.symbol, item.strategy_id)):
            strategy_remains_active = True
            engine = self.engines.get(config.strategy_id)
            if engine is None:
                engine = TradingEngine(
                    self.store,
                    self._exchange_for(config.market_type),
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
                self._record_strategy_recovery(config)
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
                self._record_strategy_failure(config, exc)
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
                        self._exchange_for(config.market_type),
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
                    self._record_strategy_recovery(config)
                    if not self._strategy_is_active(config.strategy_id):
                        self.engines.pop(config.strategy_id, None)
                        self.next_due.pop(config.strategy_id, None)
                except Exception as exc:
                    if not self._strategy_is_active(config.strategy_id):
                        self.engines.pop(config.strategy_id, None)
                        self.next_due.pop(config.strategy_id, None)
                        continue
                    cycle_had_errors = True
                    self._record_strategy_failure(config, exc)
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
                    for market in sorted(
                        {config.market_type for config in configs.values()},
                        key=lambda item: item.value,
                    ):
                        coordinator = PositionCoordinator(
                            self.store,
                            self._exchange_for(market),
                            self.run_id,
                            settlement_grace_sec=self.position_settlement_grace_sec,
                            market_type=market,
                        )
                        protected_shortage_pools: set[tuple[str, str]] = set()
                        for cell in self.store.list_all_cells():
                            config = configs.get(cell.strategy_id)
                            if config is None or config.market_type != market:
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
                    self.store.record_scheduler_recovery(
                        "position-reconcile",
                        self.run_id,
                    )
                    self.last_reconcile_error = ""
                except Exception as exc:
                    # Trading loops remain isolated from a failed account snapshot;
                    # the next audit retries without rewriting Cell ownership.
                    self.store.record_scheduler_failure(
                        "position-reconcile",
                        self.run_id,
                        exc,
                    )
                    self.last_reconcile_error = str(exc)
            self.next_reconcile_at = current_time + self.reconcile_interval_sec
        return processed

    def _exchange_for(self, market_type: FuturesMarket) -> SnapshotExchange:
        try:
            return self.exchanges[FuturesMarket(market_type)]
        except KeyError as exc:
            raise RuntimeError(
                f"exchange is not configured for {FuturesMarket(market_type).value}"
            ) from exc

    def _strategy_is_active(self, strategy_id: str) -> bool:
        config = self.store.get_strategy(strategy_id)
        return bool(
            config is not None
            and config.has_started
            and config.status in ACTIVE_STATUSES
        )

    def _record_strategy_failure(self, config, error: Exception) -> None:
        incident = self.store.record_scheduler_failure(
            f"strategy:{config.strategy_id}",
            self.run_id,
            error,
            strategy_id=config.strategy_id,
            market_type=config.market_type,
        )
        if incident["opened"]:
            self.store.append_event(
                config.strategy_id,
                "SCHEDULER_FAILURE_STARTED",
                {
                    "incident_id": incident["id"],
                    "error_type": incident["error_type"],
                    "error": incident["first_error"],
                    "started_at": incident["started_at"],
                },
                run_id=self.run_id,
            )

    def _record_strategy_recovery(self, config) -> None:
        incident = self.store.record_scheduler_recovery(
            f"strategy:{config.strategy_id}",
            self.run_id,
        )
        if incident is None:
            return
        self.store.append_event(
            config.strategy_id,
            "SCHEDULER_RECOVERED",
            {
                "incident_id": incident["id"],
                "started_at": incident["started_at"],
                "last_failed_at": incident["last_failed_at"],
                "recovered_at": incident["recovered_at"],
                "failure_count": incident["failure_count"],
                "last_error": incident["last_error"],
            },
            run_id=self.run_id,
        )

    @staticmethod
    def _utc_from_epoch(value: float) -> str:
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")

    def observe_loop(self) -> None:
        """Persist idle gaps since the previous loop finished."""

        now = float(self.wall_clock())
        gap = max(0.0, now - self._last_loop_wall)
        detected_at = self._utc_from_epoch(now)
        if gap >= self.gap_threshold_sec:
            active_count = sum(
                config.has_started and config.status in ACTIVE_STATUSES
                for config in self.store.list_strategies()
            )
            self.store.record_scheduler_gap(
                self.run_id,
                self._utc_from_epoch(self._last_loop_wall),
                detected_at,
                gap,
                active_count,
            )
        self._last_loop_wall = now

    def complete_loop(self) -> None:
        """Exclude normal exchange/SQLite processing time from gap detection."""

        now = float(self.wall_clock())
        detected_at = self._utc_from_epoch(now)
        self._last_loop_wall = now
        if (
            now - self._last_audit_heartbeat_wall
            >= self.audit_heartbeat_interval_sec
        ):
            self.store.touch_scheduler_run(
                self.run_id,
                observed_at=detected_at,
            )
            self._last_audit_heartbeat_wall = now

    def close_audit_run(self, reason: str) -> None:
        self.store.stop_scheduler_run(
            self.run_id,
            reason,
            observed_at=self._utc_from_epoch(float(self.wall_clock())),
        )

    def seconds_until_next_cycle(self, maximum: float = 1.0) -> float:
        if not self.next_due:
            return maximum
        delay = min(self.next_due.values()) - self.clock()
        return max(0.05, min(maximum, delay))


def run_scheduler_forever(scheduler: StrategyScheduler, stop_requested=lambda: False) -> None:
    reason = "stop_requested"
    try:
        while not stop_requested():
            scheduler.observe_loop()
            scheduler.run_once()
            scheduler.complete_loop()
            time.sleep(scheduler.seconds_until_next_cycle())
    except BaseException:
        reason = "unexpected_exit"
        raise
    finally:
        scheduler.close_audit_run(reason)


def main() -> int:
    load_environment()
    parser = argparse.ArgumentParser(description="Run all grid strategies in one scheduler process")
    parser.add_argument("--db", required=True)
    parser.add_argument("--pid-file", default="runtime/scheduler.pid")
    parser.add_argument("--base-url", default=binance_base_url())
    parser.add_argument("--coinm-base-url", default=binance_coinm_base_url())
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
        exchanges = {
            FuturesMarket.USDM: BinanceFuturesExchange(
                api_key,
                api_secret,
                args.base_url,
            ),
            FuturesMarket.COINM: BinanceCoinMExchange(
                api_key,
                api_secret,
                args.coinm_base_url,
            ),
        }
        scheduler = StrategyScheduler(
            store,
            exchanges,
            reconcile_interval_sec=float(
                os.getenv("GRID_POSITION_RECONCILE_INTERVAL_SEC", "60")
            ),
            position_settlement_grace_sec=float(
                os.getenv("GRID_POSITION_SETTLEMENT_GRACE_SEC", "15")
            ),
            gap_threshold_sec=float(
                os.getenv("GRID_SCHEDULER_GAP_THRESHOLD_SEC", "5")
            ),
            audit_heartbeat_interval_sec=float(
                os.getenv("GRID_SCHEDULER_AUDIT_HEARTBEAT_SEC", "30")
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
