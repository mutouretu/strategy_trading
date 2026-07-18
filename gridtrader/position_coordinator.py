from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from .domain import CellStage, GridCell, Mode, OrderSide, StrategyConfig, StrategyStatus
from .engine import TradingEngine
from .grid_math import round_down
from .snapshot_exchange import SnapshotExchange
from .store import SQLiteStore


MANAGED_STATUSES = {
    StrategyStatus.STARTING,
    StrategyStatus.RUNNING,
    StrategyStatus.ERROR,
}


@dataclass
class PositionReconcileResult:
    repaired_exits: int = 0
    resized_exits: int = 0
    released_cells: int = 0
    cancel_failures: int = 0
    rescan_strategy_ids: set[str] = field(default_factory=set)


class PositionCoordinator:
    """Allocates aggregate Binance positions to logical grid cells safely."""

    def __init__(
        self,
        store: SQLiteStore,
        exchange: SnapshotExchange,
        run_id: str,
        settlement_grace_sec: float = 0.0,
    ) -> None:
        self.store = store
        self.exchange = exchange
        self.run_id = run_id
        self.settlement_grace_sec = max(0.0, settlement_grace_sec)

    def reconcile(
        self,
        engines: dict[str, TradingEngine],
        protected_shortage_pools: set[tuple[str, str]] | None = None,
    ) -> PositionReconcileResult:
        result = PositionReconcileResult()
        protected_shortage_pools = protected_shortage_pools or set()
        configs = {
            config.strategy_id: config
            for config in self.store.list_strategies(
                include_archived=True,
                include_deleted=False,
            )
        }
        cells_by_pool: dict[tuple[str, str], list[GridCell]] = {}
        all_cells = self.store.list_all_cells()
        relevant_cells = [
            cell for cell in all_cells
            if cell.strategy_id in configs
        ]
        for cell in relevant_cells:
            config = configs.get(cell.strategy_id)
            if config is not None and cell.open_qty > 0:
                cells_by_pool.setdefault(
                    (config.symbol, self._position_side(config.mode)), []
                ).append(cell)

        positions: dict[tuple[str, str], Decimal] = {}
        for position in self.exchange.get_positions():
            key = (position.symbol, position.position_side)
            positions[key] = positions.get(key, Decimal("0")) + abs(position.quantity)

        tracked_order_ids = {
            order_id
            for cell in relevant_cells
            for order_id in (cell.entry_order_id, cell.exit_order_id)
            if order_id is not None
        }
        pool_keys = set(cells_by_pool) | set(positions)
        self.store.delete_position_pools_except(pool_keys)
        symbols = {symbol for symbol, _side in pool_keys}
        open_orders = {
            symbol: self.exchange.get_open_orders(symbol)
            for symbol in symbols
        }

        for pool_key in sorted(pool_keys):
            cells = cells_by_pool.get(pool_key, [])
            symbol, position_side = pool_key
            position_qty = positions.get(pool_key, Decimal("0"))
            self._reconcile_pool(
                symbol,
                position_side,
                position_qty,
                cells,
                configs,
                engines,
                open_orders.get(symbol, []),
                tracked_order_ids,
                result,
                pool_key in protected_shortage_pools,
            )
        return result

    def _reconcile_pool(
        self,
        symbol: str,
        position_side: str,
        position_qty: Decimal,
        cells: list[GridCell],
        configs: dict[str, StrategyConfig],
        engines: dict[str, TradingEngine],
        open_orders: list,
        tracked_order_ids: set[int],
        result: PositionReconcileResult,
        protect_shortage: bool = False,
        attempt: int = 0,
    ) -> None:
        open_by_id = {order.order_id: order for order in open_orders}
        external_reserved = sum(
            (
                max(Decimal("0"), order.original_qty - order.executed_qty)
                for order in open_orders
                if order.order_id not in tracked_order_ids
                and order.position_side == position_side
                and self._is_exit_side(position_side, order.side)
            ),
            Decimal("0"),
        )

        managed: list[GridCell] = []
        fixed_reserved = Decimal("0")
        for cell in cells:
            config = configs[cell.strategy_id]
            if config.status in MANAGED_STATUSES and cell.strategy_id in engines:
                managed.append(cell)
            else:
                # Stopped/archived groups are not mutated, but their logical
                # position must be reserved so a running group cannot claim it.
                fixed_reserved += cell.open_qty

        current_logical_qty = fixed_reserved + sum(
            (cell.open_qty for cell in managed),
            Decimal("0"),
        )
        if (
            position_qty < current_logical_qty
            and (
                protect_shortage
                or self._has_recent_entry_fill(cells)
            )
        ):
            # Binance can expose a freshly FILLED entry order before its
            # position-risk endpoint reflects the new quantity.  Rewriting the
            # cells from that stale snapshot would cancel the protective exit,
            # release the cell and immediately open the position again.  The
            # scheduler protects only pools whose logical quantity increased in
            # this exact cycle; the next independent position snapshot performs
            # the normal consistency decision.
            self._save_pool(
                symbol,
                position_side,
                position_qty,
                current_logical_qty,
                external_reserved,
                status_override="settling",
            )
            return

        mark_price = self.exchange.get_mark_price(symbol)

        # Position ownership is a business decision and must not depend on
        # whether an exit order happened to survive at Binance. Across every
        # running group in one symbol+side pool, farther entry prices retain
        # ownership first; cells closest to the current mark absorb shortages.
        ownership_cells = sorted(
            managed,
            key=lambda cell: self._ownership_priority(
                cell,
                configs[cell.strategy_id],
                mark_price,
            ),
        )

        filters = self.exchange.get_symbol_filters(symbol)
        # Position ownership and exit-order coverage are deliberately separate.
        # A pending external close order reserves order capacity, but it has not
        # reduced the real position yet and must not destructively shrink a
        # cell's logical open quantity. If it is canceled, grid exits can expand
        # again; if it fills, the next position snapshot shrinks ownership.
        ownership_allocations: dict[tuple[str, str], Decimal] = {}
        ownership_remaining = max(Decimal("0"), position_qty - fixed_reserved)
        for cell in ownership_cells:
            allocated = round_down(
                min(cell.open_qty, ownership_remaining), filters.step_size
            )
            ownership_allocations[(cell.strategy_id, cell.cell_id)] = allocated
            ownership_remaining = max(Decimal("0"), ownership_remaining - allocated)

        # Exit-order coverage is a separate concern. Once ownership has been
        # decided, retain already-open exits first to reduce needless churn.
        ownership_rank = {
            (cell.strategy_id, cell.cell_id): rank
            for rank, cell in enumerate(ownership_cells)
        }
        exit_cells = sorted(
            ownership_cells,
            key=lambda cell: (
                0 if cell.exit_order_id in open_by_id else 1,
                ownership_rank[(cell.strategy_id, cell.cell_id)],
            ),
        )
        exit_allocations: dict[tuple[str, str], Decimal] = {}
        exit_remaining = max(
            Decimal("0"),
            position_qty - fixed_reserved - external_reserved,
        )
        for cell in exit_cells:
            key = (cell.strategy_id, cell.cell_id)
            allocated = round_down(
                min(ownership_allocations[key], exit_remaining), filters.step_size
            )
            exit_allocations[key] = allocated
            exit_remaining = max(Decimal("0"), exit_remaining - allocated)

        # Any active exit whose remaining quantity is larger than its allocation
        # must be canceled before accounting is rewritten. The final order state
        # is synchronized, then the next reconciliation pass will re-plan from a
        # fresh position snapshot.
        mismatched: list[tuple[GridCell, object]] = []
        for cell in ownership_cells:
            order = open_by_id.get(cell.exit_order_id)
            if order is None:
                continue
            desired = exit_allocations[(cell.strategy_id, cell.cell_id)]
            order_remaining = round_down(
                max(Decimal("0"), order.original_qty - order.executed_qty),
                filters.step_size,
            )
            if order_remaining == desired:
                continue
            mismatched.append((cell, order))

        if mismatched and attempt >= 2:
            for cell, order in mismatched:
                result.cancel_failures += 1
                self.store.append_event(
                    cell.strategy_id,
                    "POSITION_RECONCILE_UNSTABLE",
                    {"order_id": order.order_id, "attempt": attempt},
                    cell.cell_id,
                    self.run_id,
                )
            self._save_pool(
                symbol,
                position_side,
                position_qty,
                fixed_reserved + sum(ownership_allocations.values(), Decimal("0")),
                external_reserved,
                status_override="error",
            )
            return

        canceled_any = False
        cancel_failed = False
        for cell, order in mismatched:
            engine = engines[cell.strategy_id]
            try:
                self.exchange.cancel_order(symbol, order.order_id)
                engine.sync_cell(cell)
            except Exception as exc:
                cancel_failed = True
                result.cancel_failures += 1
                self.store.append_event(
                    cell.strategy_id,
                    "POSITION_RECONCILE_CANCEL_FAILED",
                    {"order_id": order.order_id, "error": str(exc)},
                    cell.cell_id,
                    self.run_id,
                )
                continue
            canceled_any = True

        if cancel_failed:
            self._save_pool(
                symbol,
                position_side,
                position_qty,
                fixed_reserved + sum(ownership_allocations.values(), Decimal("0")),
                external_reserved,
                status_override="error",
            )
            return
        if canceled_any:
            self.exchange.invalidate_positions()
            refreshed_positions = {
                (item.symbol, item.position_side): abs(item.quantity)
                for item in self.exchange.get_positions()
            }
            refreshed_cells = []
            for cell in self.store.list_all_cells():
                config = configs.get(cell.strategy_id)
                if (
                    config is not None
                    and config.symbol == symbol
                    and self._position_side(config.mode) == position_side
                    and cell.open_qty > 0
                ):
                    refreshed_cells.append(cell)
            refreshed_orders = self.exchange.get_open_orders(symbol)
            self._reconcile_pool(
                symbol,
                position_side,
                refreshed_positions.get((symbol, position_side), Decimal("0")),
                refreshed_cells,
                configs,
                engines,
                refreshed_orders,
                tracked_order_ids,
                result,
                False,
                attempt + 1,
            )
            return

        apply_status = self._apply_allocations(
            symbol,
            ownership_cells,
            ownership_allocations,
            exit_allocations,
            open_by_id,
            engines,
            filters,
            result,
        )
        logical_qty = fixed_reserved + sum(
            ownership_allocations.values(), Decimal("0")
        )
        status_override = apply_status
        # Re-read the shared in-cycle order cache after repairs. Position
        # ownership can be perfectly aligned while its close orders are not
        # (for example a stopped group's exit was manually deleted).
        exit_coverage = sum(
            (
                max(Decimal("0"), order.original_qty - order.executed_qty)
                for order in self.exchange.get_open_orders(symbol)
                if order.position_side == position_side
                and self._is_exit_side(position_side, order.side)
            ),
            Decimal("0"),
        )
        if status_override is None and logical_qty == position_qty:
            if exit_coverage > position_qty:
                status_override = "order_excess"
            elif exit_coverage < logical_qty:
                status_override = "manual_review"
        self._save_pool(
            symbol,
            position_side,
            position_qty,
            logical_qty,
            external_reserved,
            status_override=status_override,
        )

    @staticmethod
    def _ownership_priority(
        cell: GridCell,
        config: StrategyConfig,
        mark_price: Decimal,
    ) -> tuple:
        entry_price = cell.buy_price if config.mode == Mode.LONG else cell.sell_price
        distance = abs(entry_price - mark_price)
        return (
            -distance,
            cell.entry_filled_at or "9999",
            cell.strategy_id,
            cell.index,
            cell.cell_id,
        )

    def _has_recent_entry_fill(self, cells: list[GridCell]) -> bool:
        if self.settlement_grace_sec <= 0:
            return False
        now = datetime.now(timezone.utc)
        for cell in cells:
            if not cell.entry_filled_at:
                continue
            try:
                filled_at = datetime.fromisoformat(cell.entry_filled_at)
            except ValueError:
                continue
            if filled_at.tzinfo is None:
                filled_at = filled_at.replace(tzinfo=timezone.utc)
            age = (now - filled_at.astimezone(timezone.utc)).total_seconds()
            if age <= self.settlement_grace_sec:
                return True
        return False

    def _apply_allocations(
        self,
        symbol: str,
        cells: list[GridCell],
        ownership_allocations: dict[tuple[str, str], Decimal],
        exit_allocations: dict[tuple[str, str], Decimal],
        open_by_id: dict,
        engines: dict[str, TradingEngine],
        filters,
        result: PositionReconcileResult,
    ) -> str | None:
        status_override: str | None = None
        for cell in cells:
            key = (cell.strategy_id, cell.cell_id)
            allocated = ownership_allocations[key]
            exit_target = exit_allocations[key]
            previous_qty = cell.open_qty
            active_order = open_by_id.get(cell.exit_order_id)

            if allocated <= 0:
                cell.open_qty = Decimal("0")
                cell.stage = CellStage.UNTRIGGERED
                cell.entry_order_id = None
                cell.exit_order_id = None
                cell.entry_client_id = ""
                cell.exit_client_id = ""
                cell.exit_executed_qty = Decimal("0")
                cell.entry_filled_at = ""
                self.store.save_cell(cell)
                self.store.append_event(
                    cell.strategy_id,
                    "POSITION_RESOURCE_RELEASED",
                    {"previous_open_qty": str(previous_qty), "symbol": symbol},
                    cell.cell_id,
                    self.run_id,
                )
                result.released_cells += 1
                result.rescan_strategy_ids.add(cell.strategy_id)
                continue

            cell.open_qty = allocated
            if active_order is not None:
                cell.stage = CellStage.PENDING_EXIT
                self.store.save_cell(cell)
                continue

            previous_stage = cell.stage
            cell.exit_order_id = None
            cell.exit_client_id = ""
            cell.exit_executed_qty = Decimal("0")

            if exit_target <= 0:
                # The real position still belongs to this cell, but an external
                # close order currently consumes all available exit coverage.
                # MANUAL_REVIEW suppresses the engine's unconditional exit
                # repair; the coordinator restores it when capacity returns.
                cell.stage = CellStage.MANUAL_REVIEW
                self.store.save_cell(cell)
                if previous_stage != CellStage.MANUAL_REVIEW or allocated != previous_qty:
                    self.store.append_event(
                        cell.strategy_id,
                        "POSITION_EXIT_EXTERNALLY_RESERVED",
                        {
                            "open_qty": str(allocated),
                            "exit_target_qty": "0",
                            "symbol": symbol,
                        },
                        cell.cell_id,
                        self.run_id,
                    )
                continue

            cell.stage = CellStage.PENDING_EXIT
            self.store.save_cell(cell)

            if exit_target < filters.min_qty:
                cell.stage = CellStage.MANUAL_REVIEW
                self.store.save_cell(cell)
                self.store.append_event(
                    cell.strategy_id,
                    "POSITION_RESOURCE_DUST",
                    {
                        "open_qty": str(allocated),
                        "exit_target_qty": str(exit_target),
                        "min_qty": str(filters.min_qty),
                    },
                    cell.cell_id,
                    self.run_id,
                )
                if status_override != "error":
                    status_override = "manual_review"
                continue

            try:
                engines[cell.strategy_id].ensure_exit(cell, exit_target)
            except Exception as exc:
                cell.stage = CellStage.MANUAL_REVIEW
                self.store.save_cell(cell)
                self.store.append_event(
                    cell.strategy_id,
                    "POSITION_EXIT_REPAIR_FAILED",
                    {"open_qty": str(allocated), "error": str(exc)},
                    cell.cell_id,
                    self.run_id,
                )
                status_override = "error"
                continue

            if allocated < previous_qty or exit_target < allocated:
                result.resized_exits += 1
                event_type = "POSITION_EXIT_RESIZED"
            else:
                result.repaired_exits += 1
                event_type = "POSITION_EXIT_RESTORED"
            self.store.append_event(
                cell.strategy_id,
                event_type,
                {
                    "previous_open_qty": str(previous_qty),
                    "allocated_qty": str(allocated),
                    "exit_target_qty": str(exit_target),
                },
                cell.cell_id,
                self.run_id,
            )
        return status_override

    @staticmethod
    def _position_side(mode: Mode) -> str:
        return "LONG" if mode == Mode.LONG else "SHORT"

    @staticmethod
    def _is_exit_side(position_side: str, side: OrderSide) -> bool:
        return (position_side == "LONG" and side == OrderSide.SELL) or (
            position_side == "SHORT" and side == OrderSide.BUY
        )

    def _save_pool(
        self,
        symbol: str,
        position_side: str,
        actual_qty: Decimal,
        logical_qty: Decimal,
        external_reserved_qty: Decimal,
        status_override: str | None = None,
    ) -> None:
        # external_reserved_qty is pending order coverage, not position
        # ownership. Only logical ownership participates in the position
        # surplus/shortage calculation.
        unassigned = max(Decimal("0"), actual_qty - logical_qty)
        shortage = max(Decimal("0"), logical_qty - actual_qty)
        if status_override is not None:
            status = status_override
        elif shortage > 0:
            status = "shortage"
        elif unassigned > 0:
            status = "unassigned"
        else:
            status = "consistent"
        self.store.save_position_pool(
            symbol,
            position_side,
            actual_qty,
            logical_qty,
            external_reserved_qty,
            unassigned,
            shortage,
            status,
        )
