"""Build immutable 6B baseline comparisons from stored experiment metrics."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

from experiment_system import ExperimentReader, RunQuery, sha256_document

from .errors import StudyConfigError
from .models import StudyBundle


def _metric(
    detail: Mapping[str, object],
    *,
    metric_set_id: str,
    version: str,
    key: str,
    dimensions: Mapping[str, str] | None = None,
) -> object | None:
    evaluations = detail.get("metrics", ())
    if not isinstance(evaluations, tuple):
        raise StudyConfigError("Run metric evaluations are malformed")
    matches = [
        item
        for item in evaluations
        if item.get("metric_set_id") == metric_set_id
        and item.get("metric_set_version") == version
    ]
    if len(matches) != 1 or matches[0].get("status") != "SUCCEEDED":
        raise StudyConfigError(
            f"Run {detail.get('run_id')!r} requires a successful "
            f"{metric_set_id}/{version} evaluation"
        )
    values = matches[0].get("values", [])
    expected_dimensions = dict(dimensions or {})
    selected = [
        item
        for item in values
        if item.get("metric_key") == key
        and item.get("dimensions") == expected_dimensions
    ]
    if len(selected) != 1:
        return None
    if selected[0].get("status") != "AVAILABLE":
        return None
    return selected[0].get("value")


def _decimal(value: object, *, name: str) -> Decimal:
    if value is None:
        raise StudyConfigError(f"required baseline metric {name} is unavailable")
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise StudyConfigError(
            f"baseline metric {name} is not a decimal"
        ) from exc


def _component(
    run_spec: Mapping[str, object],
    name: str,
) -> Mapping[str, object]:
    value = run_spec.get(name)
    if not isinstance(value, Mapping):
        raise StudyConfigError(f"Run {name} component is malformed")
    return value


def _comparison_key(detail: Mapping[str, object]) -> tuple[str, str, str, int]:
    run_spec = detail["run_spec"]
    assert isinstance(run_spec, Mapping)
    return (
        str(_component(run_spec, "market").get("key")),
        str(_component(run_spec, "execution").get("key")),
        str(_component(run_spec, "account").get("key")),
        int(detail["seed"]),
    )


def _core(
    detail: Mapping[str, object],
    key: str,
    dimensions: Mapping[str, str] | None = None,
) -> object | None:
    return _metric(
        detail,
        metric_set_id="core",
        version="v1",
        key=key,
        dimensions=dimensions,
    )


def _grid(detail: Mapping[str, object], key: str) -> object | None:
    try:
        return _metric(
            detail,
            metric_set_id="grid",
            version="v2",
            key=key,
        )
    except StudyConfigError:
        return None


def build_baseline_report(
    database_path: str | Path,
    bundle: StudyBundle,
) -> dict[str, object]:
    """Compare every Run with HODL under the same non-strategy components."""

    reader = ExperimentReader(database_path)
    experiment = reader.experiment_detail()
    if experiment["experiment_id"] != bundle.experiment.experiment_id:
        raise StudyConfigError(
            "baseline database does not match the Study ExperimentSpec"
        )
    rows = reader.query_runs(
        RunQuery(statuses=("SUCCEEDED",), limit=None)
    ).rows
    if len(rows) != experiment["planned_run_count"]:
        raise StudyConfigError("baseline report requires every planned Run")
    details = [reader.run_detail(str(row["run_id"])) for row in rows]
    baselines: dict[tuple[str, str, str, int], Mapping[str, object]] = {}
    for detail in details:
        run_spec = detail["run_spec"]
        assert isinstance(run_spec, Mapping)
        strategy_type = str(_component(run_spec, "strategy").get("type"))
        if strategy_type != bundle.objective_profile.baseline_strategy_type:
            continue
        key = _comparison_key(detail)
        if key in baselines:
            raise StudyConfigError(
                f"multiple HODL baselines exist for comparison key {key!r}"
            )
        baselines[key] = detail

    btc_total = {
        "scope": "account.total_equity",
        "valuation_asset": "BTC",
    }
    btc_futures = {
        "scope": "account.futures_equity",
        "valuation_asset": "BTC",
    }
    usdt_total = {
        "scope": "account.total_equity",
        "valuation_asset": "USDT",
    }
    result_rows = []
    for detail in details:
        comparison_key = _comparison_key(detail)
        if comparison_key not in baselines:
            raise StudyConfigError(
                f"Run {detail['run_id']!r} has no matching HODL baseline"
            )
        baseline = baselines[comparison_key]
        run_spec = detail["run_spec"]
        assert isinstance(run_spec, Mapping)
        strategy = _component(run_spec, "strategy")
        market = _component(run_spec, "market")
        final_futures_btc = _decimal(
            _core(detail, "return.final_equity", btc_futures),
            name="return.final_equity BTC futures",
        )
        baseline_futures_btc = _decimal(
            _core(baseline, "return.final_equity", btc_futures),
            name="HODL return.final_equity BTC futures",
        )
        completed = bool(_core(detail, "run.completed"))
        liquidated = bool(_core(detail, "run.liquidated"))
        bankrupt = bool(_core(detail, "run.bankrupt"))
        result_rows.append(
            {
                "run_id": detail["run_id"],
                "baseline_run_id": baseline["run_id"],
                "market_key": market.get("key"),
                "strategy_key": strategy.get("key"),
                "strategy_type": strategy.get("type"),
                "seed": detail["seed"],
                "eligible": completed and not liquidated and not bankrupt,
                "completed": completed,
                "liquidated": liquidated,
                "bankrupt": bankrupt,
                "btc_initial_equity": _core(
                    detail, "return.initial_equity", btc_total
                ),
                "btc_final_equity": _core(
                    detail, "return.final_equity", btc_total
                ),
                "btc_futures_final_equity": str(final_futures_btc),
                "btc_total_return_rate": _core(
                    detail, "return.total_rate", btc_total
                ),
                "excess_btc_vs_hodl": str(
                    final_futures_btc - baseline_futures_btc
                ),
                "btc_max_drawdown_rate": _core(
                    detail, "risk.max_drawdown_rate", btc_futures
                ),
                "usdt_initial_equity": _core(
                    detail, "return.initial_equity", usdt_total
                ),
                "usdt_final_equity": _core(
                    detail, "return.final_equity", usdt_total
                ),
                "usdt_total_return_rate": _core(
                    detail, "return.total_rate", usdt_total
                ),
                "fill_count": _core(detail, "execution.fill_count"),
                "fees_btc": _core(
                    detail,
                    "cost.total_fees",
                    {"valuation_asset": "BTC"},
                ),
                "funding_btc": _core(
                    detail,
                    "funding.net_wallet_delta",
                    {"valuation_asset": "BTC"},
                ),
                "grid_completed_cycles": _grid(
                    detail, "grid.completed_cycles"
                ),
            }
        )
    result_rows.sort(
        key=lambda item: (
            str(item["market_key"]),
            str(item["strategy_type"]),
            int(item["seed"]),
        )
    )
    manifest = experiment.get("manifest", {})
    reproducible = bool(
        manifest.get("reproducible")
        if isinstance(manifest, Mapping)
        else False
    )
    report: dict[str, object] = {
        "schema_version": "baseline-report/v1",
        "study_id": bundle.study.study_id,
        "experiment_id": bundle.experiment.experiment_id,
        "objective_profile_id": bundle.objective_profile.profile_id,
        "dataset_split_id": bundle.dataset_split.split_id,
        "dataset_status": bundle.dataset_split.status.value,
        "baseline_strategy_type": (
            bundle.objective_profile.baseline_strategy_type
        ),
        "reproducible": reproducible,
        "holdout_executed": False,
        "rows": result_rows,
        "known_limitations": [
            "资金费固定为零，不代表真实历史资金费路径",
            "未模拟订单簿、排队、滑点和网络延迟",
            "Maker/Taker 费率固定，不包含交易所等级变化",
            "训练与验证窗口较短，不能外推为长期稳定收益",
            "HOLDOUT 已内容锁定但未在第六部分运行",
        ],
    }
    report["report_fingerprint"] = sha256_document(report)
    return report
