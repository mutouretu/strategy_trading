from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from scripts import live_grid_maintenance as maintenance


def cell(index, *, stage="pending_entry", entry_id=None, exit_id=None,
         buy="50", sell="50.25", qty="0"):
    return {
        "cell_index": index, "stage": stage, "entry_order_id": entry_id,
        "exit_order_id": exit_id, "entry_client_id": "", "exit_client_id": "",
        "buy_price": buy, "sell_price": sell, "open_qty": qty,
    }


def order(order_id, *, side="BUY", client_id=""):
    return SimpleNamespace(
        order_id=order_id, client_order_id=client_id,
        side=SimpleNamespace(value=side), position_side="LONG",
    )


def test_virtual_cells_outside_window_do_not_look_like_missing_orders():
    cells = [cell(index, buy=str(40 + index * Decimal("0.1")))
             for index in range(1, 100)]
    assert maintenance.order_issues("grid", cells, [], Decimal("53")) == []


def test_missing_recorded_order_is_detected():
    issues = maintenance.order_issues("grid", [cell(1, entry_id=123)], [], Decimal("53"))
    assert issues == [{"kind": "recorded_order_missing", "cell_index": 1,
                       "role": "entry", "order_id": 123}]


def test_crossed_unarmed_cell_is_detected():
    issues = maintenance.order_issues(
        "grid", [cell(1, buy="54"), cell(2, stage="pending_exit",
                                      sell="52", qty="0.4")], [], Decimal("53")
    )
    assert {issue["kind"] for issue in issues} == {
        "crossed_buy_unarmed", "crossed_sell_unarmed",
    }


def test_matching_open_order_clears_gap():
    issues = maintenance.order_issues("grid", [cell(1, entry_id=123)],
                                      [order(123)], Decimal("53"))
    assert issues == []


def test_recheck_observes_scheduler_replenishment_without_placing_order():
    target = maintenance.TARGETS[0]
    initial = {"checked_at": "now", "venue": target.venue,
               "strategy_id": target.strategy_id, "symbol": "SOXLUSDT",
               "open_order_count": 19,
               "issues": [{"kind": "recorded_order_missing", "cell_index": 1,
                           "role": "entry", "order_id": 123}]}
    healed = {**initial, "issues": [], "open_order_count": 20}
    with patch.object(maintenance, "exchange_for", return_value=object()), \
            patch.object(maintenance, "scan", side_effect=[initial, healed]), \
            patch.object(maintenance.time, "sleep") as sleep:
        result = maintenance.run_target(target, recheck_delay=10)
    assert result["issues"] == []
    assert result["resolved_after_sync"] == 1
    assert result["observations"] == 2
    sleep.assert_called_once_with(10)


def test_okx_patrol_targets_current_live_grid():
    okx = next(target for target in maintenance.TARGETS if target.venue == "okx")
    assert okx.strategy_id == "soxl-usdt-swap-fixed-197a79bf"
    assert okx.db == maintenance.GRID_RUNTIME / "okx/grid.sqlite3"
    assert okx.env_file == maintenance.GRID_PACKAGE / "okx.env"
    binance = next(target for target in maintenance.TARGETS if target.venue == "binance")
    assert binance.db == maintenance.GRID_RUNTIME / "binance/grid.sqlite3"
    assert binance.env_file == maintenance.GRID_PACKAGE / ".env"


def test_scan_selects_target_among_other_strategies():
    target = next(target for target in maintenance.TARGETS if target.venue == "okx")
    database = {
        "strategies": [
            {"strategy_id": "another-draft", "symbol": "BTC-USDT-SWAP"},
            {"strategy_id": target.strategy_id, "symbol": "SOXL-USDT-SWAP"},
        ],
        "cells": [
            {"strategy_id": "another-draft"},
            {"strategy_id": target.strategy_id, **cell(1, entry_id=123)},
        ],
    }
    exchange = SimpleNamespace(
        get_open_orders=lambda symbol: [order(123)],
        get_mark_price=lambda symbol: Decimal("53"),
    )
    with patch.object(maintenance, "read_database", return_value=database):
        result = maintenance.scan(target, exchange)
    assert result["symbol"] == "SOXL-USDT-SWAP"
    assert result["issues"] == []
