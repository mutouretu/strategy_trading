from __future__ import annotations

import ast
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from trading_ledger.application.contracts import (
    ApplicationError,
    CreateProjectCommand,
    ErrorCode,
    ListOperationHistoryQuery,
    PreviewManualTradeCommand,
    RecordConfirmedTradeCommand,
    UpdateProjectCommand,
)
from trading_ledger.application.service import TradingLedgerApplication
from trading_ledger.domain import (
    CN_STOCK_FEE_SCHEDULE,
    CN_STOCK_LOT_SIZE,
    ExecutionSource,
    TradeSide,
)


class ContractTests(unittest.TestCase):
    def test_confirmed_frontend_rules_are_frozen(self) -> None:
        self.assertEqual(CN_STOCK_LOT_SIZE, 100)
        self.assertEqual(
            CN_STOCK_FEE_SCHEDULE.commission_rate, Decimal("0.000085")
        )
        self.assertEqual(
            CN_STOCK_FEE_SCHEDULE.minimum_commission, Decimal("5.00")
        )
        self.assertEqual(
            CN_STOCK_FEE_SCHEDULE.sell_stamp_tax_rate, Decimal("0.001")
        )
        self.assertEqual(
            CN_STOCK_FEE_SCHEDULE.sell_transfer_fee_rate, Decimal("0.00001")
        )

    def test_arbitrary_percentage_is_stored_as_decimal_ratio(self) -> None:
        command = PreviewManualTradeCommand(
            project_key="long-term-a",
            symbol="600000.SH",
            side=TradeSide.BUY,
            allocation_ratio=Decimal("0.2375"),
            price=Decimal("12.34"),
            signal_text="突破前期平台",
        )
        self.assertEqual(command.allocation_ratio, Decimal("0.2375"))

    def test_ratio_must_be_greater_than_zero_and_at_most_one(self) -> None:
        for ratio in (Decimal("0"), Decimal("1.0001")):
            with self.subTest(ratio=ratio), self.assertRaises(ValueError):
                PreviewManualTradeCommand(
                    project_key="long-term-a",
                    symbol="600000.SH",
                    side=TradeSide.SELL,
                    allocation_ratio=ratio,
                    price=Decimal("12.34"),
                    signal_text="止盈",
                )

    def test_source_and_signal_are_required_free_text(self) -> None:
        from trading_ledger.application.contracts import AddTrackedInstrumentCommand

        with self.assertRaisesRegex(ValueError, "source_text"):
            AddTrackedInstrumentCommand(
                project_key="long-term-a",
                symbol="600000.SH",
                name="浦发银行",
                source_text="  ",
                actor="local-user",
            )
        with self.assertRaisesRegex(ValueError, "signal_text"):
            PreviewManualTradeCommand(
                project_key="long-term-a",
                symbol="600000.SH",
                side=TradeSide.BUY,
                allocation_ratio=Decimal("0.25"),
                price=Decimal("12.34"),
                signal_text="",
            )

    def test_external_trade_requires_idempotency_and_external_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "request_id"):
            RecordConfirmedTradeCommand(
                project_key="long-term-a",
                request_id="",
                external_trade_id="fill-1",
                account_code="primary-cny",
                market="CN_STOCK",
                venue="SSE",
                symbol="600000.SH",
                side=TradeSide.BUY,
                trade_time=datetime.now().astimezone(),
                quantity=Decimal("100"),
                price=Decimal("12.34"),
                signal_text="突破",
                actor="auto-client",
                execution_source=ExecutionSource.AUTO_TRADING,
            )

    def test_external_trade_requires_timezone_and_complete_fee_group(self) -> None:
        common = {
            "project_key": "long-term-a",
            "request_id": "request-1",
            "external_trade_id": "fill-1",
            "account_code": "primary-cny",
            "market": "CN_STOCK",
            "venue": "SSE",
            "symbol": "600000.SH",
            "side": TradeSide.SELL,
            "quantity": Decimal("100"),
            "price": Decimal("12.34"),
            "signal_text": "止盈",
            "actor": "auto-client",
        }
        with self.assertRaisesRegex(ValueError, "timezone"):
            RecordConfirmedTradeCommand(
                **common,
                trade_time=datetime(2026, 9, 2, 10, 15, 30),
            )
        with self.assertRaisesRegex(ValueError, "fee fields"):
            RecordConfirmedTradeCommand(
                **common,
                trade_time=datetime(2026, 9, 2, 10, 15, 30, tzinfo=timezone.utc),
                commission_amount=Decimal("5.00"),
            )

    def test_contracts_are_immutable(self) -> None:
        command = CreateProjectCommand(
            project_name="长线组合",
            initial_capital=Decimal("1000000.00"),
            actor="local-user",
        )
        with self.assertRaises(FrozenInstanceError):
            command.project_name = "其他名称"  # type: ignore[misc]

    def test_project_color_must_be_supported(self) -> None:
        with self.assertRaisesRegex(ValueError, "color_key"):
            UpdateProjectCommand(
                project_key="long-term-a",
                project_name="长线组合",
                color_key="neon",
                actor="local-user",
            )

    def test_query_paging_boundaries_are_explicit(self) -> None:
        with self.assertRaises(ValueError):
            ListOperationHistoryQuery(project_key="long-term-a", page=0)
        with self.assertRaises(ValueError):
            ListOperationHistoryQuery(project_key="long-term-a", page_size=201)

    def test_application_error_exposes_safe_structured_detail(self) -> None:
        error = ApplicationError(
            ErrorCode.INSUFFICIENT_CASH,
            "可用资金不足。",
            retryable=False,
        )
        self.assertEqual(error.detail.code, ErrorCode.INSUFFICIENT_CASH)
        self.assertEqual(str(error), "可用资金不足。")

    def test_service_port_covers_confirmed_pages(self) -> None:
        expected_methods = {
            "create_project",
            "update_project",
            "archive_project",
            "restore_project",
            "list_projects",
            "add_tracking",
            "update_tracking",
            "close_tracking",
            "archive_tracking",
            "refresh_tracking_prices",
            "list_tracking",
            "preview_manual_trade",
            "confirm_manual_trade",
            "operation_history",
            "list_statistics_months",
            "monthly_statistics",
        }
        self.assertTrue(expected_methods.issubset(vars(TradingLedgerApplication)))


class IndependenceTests(unittest.TestCase):
    def test_new_package_does_not_import_other_repository_modules(self) -> None:
        source_root = Path(__file__).resolve().parents[2] / "src" / "trading_ledger"
        forbidden = {
            "db",
            "grid_trading",
            "market_simulator",
            "paper_db",
            "paper_services",
            "strategies_system",
            "theory_services",
            "tracking_services",
        }
        violations: list[str] = []
        for path in source_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                imported: list[str] = []
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported = [node.module]
                for name in imported:
                    if name.split(".", 1)[0] in forbidden:
                        violations.append(f"{path.name}: {name}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
