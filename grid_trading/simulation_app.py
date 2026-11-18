"""Read-only Streamlit viewer for the fixed-grid live-engine replay."""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
import streamlit as st


def _load_market_simulator():
    configured = os.environ.get("GRID_MARKET_SIMULATOR_ROOT", "")
    candidates = [
        Path(configured) if configured else None,
        Path(__file__).resolve().parents[2] / "strategy_trading" / "market_simulator",
        Path(__file__).resolve().parents[1] / "market_simulator",
    ]
    for root in candidates:
        if root is None or not (root / "packages" / "market_simulator").is_dir():
            continue
        for package in ("market_protocol", "market_simulator"):
            package_src = str(root / "packages" / package / "src")
            if package_src not in sys.path:
                sys.path.insert(0, package_src)
        from market_simulator import FixedBarMarketSource
        return FixedBarMarketSource
    raise RuntimeError("找不到 market_simulator；请设置 GRID_MARKET_SIMULATOR_ROOT")


from grid_rule.fixed_equal_usdt import FixedEntryMode  # noqa: E402
from grid_server.domain import SymbolFilters  # noqa: E402
from grid_server.simulation import FixedGridReplay  # noqa: E402
from grid_server.simulation.sample import SAMPLE_BARS  # noqa: E402


def _bars_from_csv(uploaded) -> tuple[tuple[str, str, str, str], ...]:
    table = pd.read_csv(uploaded)
    table.columns = [str(column).strip().lower() for column in table.columns]
    required = ("open", "high", "low", "close")
    if any(column not in table for column in required):
        raise ValueError("CSV 必须包含 open、high、low、close 四列")
    if not 2 <= len(table) <= 300:
        raise ValueError("CSV 需要 2–300 根 K 线")
    bars = []
    for index, row in table.iterrows():
        try:
            values = tuple(Decimal(str(row[column])) for column in required)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"第 {index + 2} 行的价格无效") from exc
        opening, high, low, close = values
        if (any(not value.is_finite() or value <= 0 for value in values)
                or low > min(opening, close) or high < max(opening, close)):
            raise ValueError(f"第 {index + 2} 行的 OHLC 范围无效")
        bars.append(tuple(str(value) for value in values))
    return tuple(bars)


@st.cache_data(show_spinner=False)
def _run_replay(
    bars: tuple[tuple[str, str, str, str], ...],
    symbol: str, lower: str, upper: str, count: int, budget: str,
    mode: str, partial_buy: bool,
):
    source = _load_market_simulator()(symbol, bars, step_milliseconds=60_000)
    filters = SymbolFilters(
        tick_size=Decimal("0.01"), step_size=Decimal("0.01"),
        min_qty=Decimal("0.01"), min_notional=Decimal("5"),
        contract_type="TRADIFI_PERPETUAL",
        percent_price_up=Decimal("1.03"),
        percent_price_down=Decimal("0.97"),
    )

    def fill_quantity(frame, order):
        remaining = order.original_qty - order.executed_qty
        if partial_buy and frame.sequence == 1 and order.side.value == "BUY":
            return (remaining / 2).quantize(filters.step_size)
        return remaining

    return FixedGridReplay(
        source, symbol=symbol,
        lower_price=Decimal(lower), upper_price=Decimal(upper),
        grid_count=count, grid_budget_usdt=Decimal(budget),
        filters=filters, entry_mode=FixedEntryMode(mode),
        fill_quantity=fill_quantity if partial_buy else None,
    ).run()


st.set_page_config(page_title="网格规则仿真", layout="wide")
st.title("网格规则仿真")
st.caption("grid-rule-parity · 同一固定网格执行引擎，使用临时数据库和内存交易所")

with st.sidebar.form("replay_settings"):
    st.subheader("回放参数")
    source_label = st.radio("行情", ["固定 SOXL 样例", "上传 OHLC CSV"])
    uploaded = st.file_uploader("CSV 文件", type="csv") if source_label == "上传 OHLC CSV" else None
    mode_label = st.radio("入场模式", ["触价激活（零预建仓）", "预建卖侧仓位"])
    partial_buy = st.checkbox("第二根 K 线的买单部分成交", value=False)
    symbol = st.text_input("合约", value="SOXLUSDT").strip().upper()
    lower = st.text_input("下限", value="40")
    upper = st.text_input("上限", value="60")
    count = st.number_input("单元格数", min_value=1, max_value=500, value=135)
    budget = st.text_input("总预算 U", value="2000")
    submitted = st.form_submit_button("运行回放", type="primary")

if submitted or "replay_result" not in st.session_state:
    try:
        if not symbol:
            raise ValueError("合约不能为空")
        bars = _bars_from_csv(uploaded) if source_label == "上传 OHLC CSV" else SAMPLE_BARS
        mode = (FixedEntryMode.PRICE_TRIGGERED if mode_label.startswith("触价")
                else FixedEntryMode.SEEDED)
        with st.spinner("正在回放固定网格订单..."):
            result = _run_replay(
                bars, symbol, lower, upper, int(count), budget, mode.value, partial_buy,
            )
        st.session_state.replay_result = result
        st.session_state.replay_info = (source_label, symbol, mode_label, len(bars))
    except Exception as exc:
        st.error(f"回放失败：{exc}")
        st.stop()

result = st.session_state.replay_result
source_label, symbol, mode_label, bar_count = st.session_state.replay_info
st.info(f"{source_label} · {symbol} · {mode_label} · {bar_count} 根 K 线。样例是人为构造的震荡路径，不是历史收益回测。")

first, last = result.frames[0], result.frames[-1]
columns = st.columns(5)
columns[0].metric("初始逻辑仓位", f"{first.logical_position_qty:.2f}")
columns[1].metric("最终逻辑仓位", f"{last.logical_position_qty:.2f}")
columns[2].metric("完成买卖周期", last.completed_cycles)
columns[3].metric("最后买单挂单", last.entry_order_count)
columns[4].metric("最后卖单挂单", last.exit_order_count)

rows = [
    {
        "K 线": frame.sequence,
        "收盘价": float(frame.mark),
        "买单挂单": frame.entry_order_count,
        "卖单挂单": frame.exit_order_count,
        "全量成交": len(frame.filled_order_ids),
        "部分成交": len(frame.partially_filled_order_ids),
        "逻辑仓位": float(frame.logical_position_qty),
        "交易所仓位": float(frame.exchange_position_qty),
        "累计周期": frame.completed_cycles,
    }
    for frame in result.frames
]
frame_table = pd.DataFrame(rows)
price_tab, position_tab, events_tab, cells_tab = st.tabs(
    ["价格与挂单", "仓位对账", "成交事件", "最终单元格"]
)
with price_tab:
    st.line_chart(frame_table.set_index("K 线")[["收盘价"]])
    st.dataframe(frame_table[["K 线", "收盘价", "买单挂单", "卖单挂单", "全量成交", "部分成交", "累计周期"]],
                 hide_index=True, width="stretch")
with position_tab:
    st.line_chart(frame_table.set_index("K 线")[["逻辑仓位", "交易所仓位"]])
    st.dataframe(frame_table[["K 线", "逻辑仓位", "交易所仓位"]],
                 hide_index=True, width="stretch")
with events_tab:
    interesting = {
        "ENTRY_FILLED", "ENTRY_PARTIAL_FINALIZED", "EXIT_PARTIALLY_FILLED",
        "CYCLE_CLOSED", "ENTRY_ENDED", "EXIT_ENDED", "MANUAL_REVIEW",
    }
    events = [
        {
            "事件序号": event["id"], "单元格": event["cell_id"] or "",
            "事件": event["event_type"],
            "详情": json.dumps(event["payload"], ensure_ascii=False),
        }
        for event in result.events if event["event_type"] in interesting
    ]
    st.dataframe(pd.DataFrame(events), hide_index=True, width="stretch")
with cells_tab:
    cells = [
        {
            "格": cell.index, "买价": float(cell.buy_price),
            "卖价": float(cell.sell_price), "预算 U": float(cell.budget_usdt),
            "阶段": cell.stage.value, "持仓": float(cell.open_qty),
            "交易次数": cell.cycle_count,
        }
        for cell in result.final_cells
    ]
    st.dataframe(pd.DataFrame(cells), hide_index=True, width="stretch")

st.caption("仅对照订单状态；未计入手续费、资金费、保证金、盘口深度或真实价格带，不能据此判断净收益。")
