from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from urllib.parse import quote
import uuid

import pandas as pd
import streamlit as st

from grid_server.interfaces.web_client import GridApiClient, GridApiError
from grid_server.shared.config import api_base_url
from grid_server.shared.price_format import format_price, infer_price_precision


st.set_page_config(page_title="网格交易管理", layout="wide", initial_sidebar_state="expanded")


BINANCE_YELLOW = "#F0B90B"
BINANCE_GREEN = "#0ECB81"
BINANCE_RED = "#F6465D"
BINANCE_TEXT = "#EAECEF"


STATUS_LABELS = {
    "draft": "未启动",
    "starting": "启动中",
    "running": "运行中",
    "stopped": "已停止",
    "error": "运行异常",
    "archived": "已归档",
}
STAGE_LABELS = {
    "untriggered": "未触发",
    "pending_entry": "待建仓",
    "pending_exit": "待平仓",
    "manual_review": "人工检查",
}


@st.cache_resource
def api_client(base_url: str) -> GridApiClient:
    return GridApiClient(base_url)


def backend() -> GridApiClient:
    return api_client(api_base_url())


def apply_styles() -> None:
    st.markdown(
        """
        <style>
        header[data-testid="stHeader"] { background: transparent; }
        div[data-testid="stDecoration"] { display: none; }
        .block-container { max-width: 100%; padding: 2.2rem 1.25rem 3rem; }
        section[data-testid="stSidebar"][aria-expanded="true"],
        section[data-testid="stSidebar"][aria-expanded="true"] > div {
            width: 13rem !important; min-width: 13rem !important;
        }
        .muted { color: #848E9C; font-size: .85rem; }
        div[data-testid="stSidebar"] { border-right-color: #2B3139; }
        div[data-testid="stButton"] button[kind="primary"],
        div[data-testid="stFormSubmitButton"] button[kind="primary"] {
            color: #0B0E11 !important;
            font-weight: 700;
        }
        div[data-testid="stButton"] button[kind="secondary"],
        div[data-testid="stFormSubmitButton"] button[kind="secondary"] {
            border-color: #2B3139;
        }
        div[data-testid="stButton"] button[kind="secondary"]:hover,
        div[data-testid="stFormSubmitButton"] button[kind="secondary"]:hover {
            color: #F0B90B;
            border-color: #F0B90B;
        }
        div[data-testid="stDialog"] > div { border: 1px solid #2B3139; }
        div[data-testid="stMetric"] label,
        div[data-testid="stMetricLabel"] { font-size: .72rem !important; }
        div[data-testid="stMetricValue"] { font-size: 1.05rem !important; }
        div[data-testid="stMetric"] { padding-top: .15rem; padding-bottom: .15rem; }
        .grid-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1.2rem; margin: .8rem 0 1.15rem; }
        .grid-summary.config { grid-template-columns: 1.1fr 1.6fr 1fr; margin-bottom: .55rem; }
        .grid-summary.prices { margin-top: .25rem; }
        .grid-summary .item { min-width: 0; }
        .grid-summary .label { font-size: .72rem; opacity: .72; margin-bottom: .22rem; }
        .grid-summary .value { font-size: 1.05rem; font-weight: 600; line-height: 1.25; overflow-wrap: anywhere; }
        @media (max-width: 900px) { .grid-summary, .grid-summary.config { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
        @media (max-width: 600px) { .grid-summary, .grid-summary.config { grid-template-columns: 1fr; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


def decimal_number(value: object, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    return float(value)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def runtime_text(started_at: str | None, stopped_at: str | None) -> str:
    started = parse_time(started_at)
    if started is None:
        return "0分钟"
    finished = parse_time(stopped_at) or datetime.now(timezone.utc)
    seconds = max(0, int((finished - started).total_seconds()))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours or days:
        parts.append(f"{hours}小时")
    parts.append(f"{minutes}分")
    return "".join(parts)


def updated_text(value: str | None, has_started: bool) -> str:
    parsed = parse_time(value)
    if parsed is None:
        return "等待首次轮询" if has_started else "尚未启动"
    return parsed.astimezone().strftime("%m-%d %H:%M:%S")


def normalize_strategy(item: dict) -> dict:
    mode = "做多" if item["mode"] == "long" else "做空"
    market_type = item.get("market_type", "usdm")
    price_precision = infer_price_precision(
        item.get("anchor_price"),
        item.get("lower_price"),
        item.get("upper_price"),
    )
    return {
        "id": item["strategy_id"],
        "symbol": item["symbol"],
        "market_type": market_type,
        "grid_kind": item.get("grid_kind", "triggered"),
        "grid_budget_usdt": None if item.get("grid_budget_usdt") is None else decimal_number(item["grid_budget_usdt"]),
        "auto_reinvest_enabled": bool(item.get("auto_reinvest_enabled", False)),
        "market_label": (
            "固定等 U · U 本位永续"
            if item.get("grid_kind") == "fixed_equal_usdt"
            else ("币本位永续" if market_type == "coinm" else "U 本位永续")
        ),
        "quantity_unit": item.get(
            "quantity_unit",
            "contract" if market_type == "coinm" else "base_asset",
        ),
        "mode": mode,
        "mode_value": item["mode"],
        "price_precision": price_precision,
        "current_price": None if item.get("current_price") is None else decimal_number(item["current_price"]),
        "lower_price": None if item.get("lower_price") is None else decimal_number(item["lower_price"]),
        "upper_price": None if item.get("upper_price") is None else decimal_number(item["upper_price"]),
        "grid_ratio": decimal_number(item["grid_ratio"]) * 100,
        "order_usdt": decimal_number(item["order_usdt"]),
        "order_coin_qty": (
            None
            if item.get("order_coin_qty") is None
            else decimal_number(item["order_coin_qty"])
        ),
        "order_amount": decimal_number(item.get("order_amount", item["order_usdt"])),
        "order_unit": item.get("order_unit", "USD"),
        "contract_size": decimal_number(item.get("contract_size", "0")),
        "leverage": int(item["leverage"]),
        "grid_count": int(item["grid_count"]),
        "pending_entry": int(item.get("pending_entry", 0)),
        "entered": int(item.get("entered", 0)),
        "pending_exit": int(item.get("pending_exit", 0)),
        "manual_review": int(item.get("manual_review", 0)),
        "status": STATUS_LABELS.get(item["status"], item["status"]),
        "status_value": item["status"],
        "updated_at": updated_text(item.get("heartbeat_at"), bool(item.get("has_started"))),
        "runtime": runtime_text(item.get("started_at"), item.get("stopped_at")),
        "anchor_price": decimal_number(item["anchor_price"]),
        "poll_interval_sec": float(item.get("poll_interval_sec", 50.0)),
        "move_grid": bool(item.get("move_grid", True)),
        "has_started": bool(item.get("has_started")),
        "archived": bool(item.get("archived")),
        "last_error": item.get("last_error"),
    }


def load_strategies() -> list[dict]:
    return [normalize_strategy(item) for item in backend().list_strategies()]


def form_payload(
    symbol: str,
    market: str,
    mode: str,
    anchor_price: float,
    ratio_percent: float,
    grid_count: int,
    order_amount: float,
    leverage: int,
    *,
    poll_interval_sec: float = 50.0,
    move_grid: bool = True,
) -> dict:
    ratio = Decimal(str(ratio_percent)) / Decimal("100")
    payload = {
        "symbol": symbol.strip().upper(),
        "market_type": "coinm" if market == "币本位" else "usdm",
        "mode": "long" if mode == "做多" else "short",
        "anchor_price": str(anchor_price),
        "grid_ratio": str(ratio),
        "grid_count": int(grid_count),
        "leverage": int(leverage),
        "poll_interval_sec": float(poll_interval_sec),
        "move_grid": bool(move_grid),
    }
    if market == "币本位":
        payload["order_coin_qty"] = str(order_amount)
    else:
        payload["order_usdt"] = str(order_amount)
    return payload


def display_flash() -> None:
    error = st.session_state.pop("api_flash_error", None)
    success = st.session_state.pop("api_flash_success", None)
    if error:
        st.error(error)
    if success:
        st.toast(success)


def render_fixed_grid_create() -> None:
    st.caption("币安 U 本位测试网 · 固定天地网格 · 1 倍做多 · 每格等 U")
    with st.form("fixed_grid_preview_form"):
        symbol = st.text_input("交易对", placeholder="例如 SOLUSDT", key="fixed_symbol")
        lower = st.number_input("区间下界", min_value=0.0, value=None, step=0.01, format="%.8f")
        upper = st.number_input("区间上界", min_value=0.0, value=None, step=0.01, format="%.8f")
        count = st.number_input("网格数量", min_value=1, max_value=100, value=20, step=1)
        budget = st.number_input("网格交易预算 (USDT)", min_value=0.01, value=None, step=100.0, format="%.2f")
        preview_clicked = st.form_submit_button("生成预览", type="primary")
    if preview_clicked:
        if not symbol.strip() or lower is None or upper is None or budget is None:
            st.error("请填写交易对、上下界和交易预算。")
        else:
            payload = {
                "symbol": symbol.strip().upper(),
                "lower_price": str(lower),
                "upper_price": str(upper),
                "grid_count": int(count),
                "grid_budget_usdt": str(budget),
            }
            try:
                preview = backend().preview_fixed_grid(payload)
            except GridApiError as exc:
                st.error(str(exc))
            else:
                st.session_state["fixed_create_payload"] = payload
                st.session_state["fixed_create_preview"] = preview
    preview = st.session_state.get("fixed_create_preview")
    payload = st.session_state.get("fixed_create_payload")
    if not preview or not payload:
        return
    allocation = preview["allocation"]
    st.write(
        f"当前价 {allocation['current_price']} · 买格 {allocation['buy_cell_count']} · "
        f"卖格 {allocation['sell_cell_count']}"
    )
    st.info(
        f"启动时将按当时市价一次性买入全部 {allocation['sell_cell_count']} 个上方卖格"
        f"对应的数量，预计花费约 {float(allocation['estimated_seed_market_buy_usdt']):.2f} U；"
        f"这些格子的预算上限合计 {allocation['seed_notional_usdt']} U。"
        "下方买格等待价格跌到格线后买入。"
    )
    st.caption(
        "数量按每格预算 ÷ 该格买价计算；完整循环的卖出目标金额理论上约为"
        "单格预算 × (1 + 等比间距)。受币安数量与价格精度影响，"
        f"本次各格预计卖出金额为 {float(allocation['minimum_sell_order_notional_usdt']):.2f}–"
        f"{float(allocation['maximum_sell_order_notional_usdt']):.2f} U。"
    )
    st.caption(
        f"当前账户 maker {float(allocation['maker_fee_rate']) * 100:.4f}% / "
        f"taker {float(allocation['taker_fee_rate']) * 100:.4f}%；"
        f"预计初始开仓手续费 {float(allocation['estimated_seed_opening_fee_usdt']):.4f} U；"
        f"单格完整挂单循环的最低预计净利润 "
        f"{float(allocation['minimum_net_cycle_profit_pct']):.3f}% "
        f"({float(allocation['minimum_net_cycle_profit_usdt']):.4f} U)，"
        f"双边市价成交时最低约 "
        f"{float(allocation['minimum_net_cycle_profit_taker_pct']):.3f}% "
        f"({float(allocation['minimum_net_cycle_profit_taker_usdt']):.4f} U)。"
    )
    if allocation["deferred_buy_cell_count"] or allocation["deferred_sell_cell_count"]:
        st.info(
            f"当前有 {allocation['deferred_buy_cell_count']} 个买格和 "
            f"{allocation['deferred_sell_cell_count']} 个卖格超出交易所限价带，"
            "会先保留为逻辑格，价格靠近时再挂单。若标记价跳过格线，则以格线价格补挂限价单。"
        )
    if allocation["seed_cells_below_fee_break_even"]:
        st.warning(
            f"当前价距离 {allocation['seed_cells_below_fee_break_even']} 个高位卖格过近，"
            "初始市价开仓后立即卖出可能不足以覆盖手续费。"
        )
    if not allocation["inside_range"]:
        st.warning("当前价格已在区间外；价格回到区间内才能启动。")
    st.dataframe(
        pd.DataFrame([
            {
                "格子": f"#{int(cell['index']):03d}",
                "买价": cell["buy_price"],
                "卖价": cell["sell_price"],
                "预算 (U)": cell["budget_usdt"],
            }
            for cell in preview["cells"]
        ]),
        hide_index=True,
        width="stretch",
        height=min(500, 36 * (len(preview["cells"]) + 1)),
    )
    if st.button("确认创建固定网格", type="primary"):
        try:
            created = backend().create_fixed_grid(payload)
        except GridApiError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("fixed_create_payload", None)
            st.session_state.pop("fixed_create_preview", None)
            st.session_state["api_flash_success"] = f"{created['symbol']} 固定网格已创建"
            st.rerun()


@st.dialog("新增币对网格")
def create_grid_dialog() -> None:
    kind = st.segmented_control(
        "策略类型", ["触发式网格", "固定区间等 U"],
        default="触发式网格", selection_mode="single", key="create_grid_kind",
    ) or "触发式网格"
    if kind == "固定区间等 U":
        render_fixed_grid_create()
        return
    market = st.segmented_control(
        "合约类型",
        ["U 本位", "币本位"],
        default="币本位",
        selection_mode="single",
        key="create_market_type",
    ) or "币本位"
    with st.form("create_grid_preview_form"):
        symbol = st.text_input(
            "交易对",
            placeholder="例如 BTCUSD_PERP" if market == "币本位" else "例如 SOLUSDT",
        )
        mode = st.selectbox("方向", ["做多", "做空"])
        anchor_price = st.number_input(
            "锚定价格",
            min_value=0.0,
            value=None,
            step=0.00000001,
            format="%.8f",
            help="做多从锚定价向下生成；做空从锚定价向上生成。",
        )
        c1, c2 = st.columns(2)
        grid_ratio = c1.number_input("等比比例（%）", min_value=0.01, value=0.50, step=0.10)
        grid_count = c2.number_input("网格数量", min_value=1, max_value=20, value=5, step=1)
        c3, c4 = st.columns(2)
        order_amount = c3.number_input(
            "单格币数量" if market == "币本位" else "单格名义金额（USD）",
            min_value=0.01,
            value=1.0 if market == "币本位" else 10.0,
            step=0.1 if market == "币本位" else 10.0,
            format="%.8f" if market == "币本位" else "%.2f",
            help=(
                "填写 LINK、UNI、XRP 等标的币数量；下单时按该 Cell 价格换算为最接近的整数张。"
                if market == "币本位"
                else None
            ),
            key=f"create_order_amount_{market}",
        )
        leverage = c4.number_input("杠杆", min_value=1, max_value=125, value=3, step=1)
        preview_clicked = st.form_submit_button("生成预览", type="primary")

    if mode == "做多":
        st.caption("计算规则：从锚定价格向下反推，p(i+1) = p(i) / (1 + r)")
    else:
        st.caption("计算规则：从锚定价格向上推算，p(i+1) = p(i) × (1 + r)")

    if preview_clicked:
        if not symbol.strip() or anchor_price is None or float(anchor_price) <= 0:
            st.error("请填写有效的交易对和锚定价格。")
        else:
            payload = form_payload(
                symbol,
                market,
                mode,
                float(anchor_price),
                float(grid_ratio),
                int(grid_count),
                float(order_amount),
                int(leverage),
            )
            try:
                preview = backend().preview_strategy(payload)
            except GridApiError as exc:
                st.error(str(exc))
            else:
                st.session_state["grid_create_preview"] = preview
                st.session_state["grid_create_payload"] = payload

    preview = st.session_state.get("grid_create_preview")
    payload = st.session_state.get("grid_create_payload")
    if not preview or not payload:
        return

    preview_config = preview["config"]
    preview_precision = infer_price_precision(
        preview_config.get("lower_price"),
        preview_config.get("upper_price"),
        *(price for cell in preview["cells"] for price in (cell.get("buy_price"), cell.get("sell_price"))),
    )
    direction_text = "向下反推" if payload["mode"] == "long" else "向上推算"
    st.caption(
        f"{'做多' if payload['mode'] == 'long' else '做空'} · {direction_text} · "
        f"范围 {format_price(preview_config['lower_price'], preview_precision)} – "
        f"{format_price(preview_config['upper_price'], preview_precision)}"
    )
    preview_rows = [
        {
            "网格": f"#{int(cell['index']):03d}",
            "买入价": decimal_number(cell["buy_price"]),
            "卖出价": decimal_number(cell["sell_price"]),
        }
        for cell in preview["cells"]
    ]
    st.dataframe(
        pd.DataFrame(preview_rows),
        width="stretch",
        hide_index=True,
        height=36 * (len(preview_rows) + 1) + 3,
        row_height=35,
        column_config={
            "网格": st.column_config.TextColumn("网格", width="small"),
            "买入价": st.column_config.NumberColumn("买入价", format=f"%.{preview_precision}f"),
            "卖出价": st.column_config.NumberColumn("卖出价", format=f"%.{preview_precision}f"),
        },
    )
    if st.button("确认创建", type="primary", width="stretch"):
        try:
            created = backend().create_strategy(payload)
        except GridApiError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("grid_create_preview", None)
            st.session_state.pop("grid_create_payload", None)
            st.session_state["api_flash_success"] = f"{created['symbol']} 网格已创建"
            st.rerun()


def strategy_by_id(strategies: list[dict], strategy_id: str) -> dict | None:
    return next((item for item in strategies if item["id"] == strategy_id), None)


@st.dialog("编辑币对网格")
def edit_grid_dialog(grid: dict) -> None:
    if grid.get("has_started"):
        st.warning("该配置已经下发，不能再修改。")
        return

    with st.form(f"edit_grid_{grid['id']}"):
        market = st.selectbox(
            "合约类型",
            ["U 本位", "币本位"],
            index=1 if grid["market_type"] == "coinm" else 0,
        )
        symbol = st.text_input("交易对", value=grid["symbol"])
        mode = st.selectbox("方向", ["做多", "做空"], index=0 if grid["mode"] == "做多" else 1)
        anchor_price = st.number_input(
            "锚定价格",
            min_value=0.0,
            value=float(grid["anchor_price"]),
            step=0.00000001,
            format="%.8f",
        )
        left, right = st.columns(2)
        ratio = left.number_input("等比比例（%）", min_value=0.01, value=float(grid["grid_ratio"]), step=0.10)
        count = right.number_input("网格数量", min_value=1, max_value=20, value=int(grid["grid_count"]), step=1)
        left2, right2 = st.columns(2)
        order_amount = left2.number_input(
            (
                f"单格币数量（{base_asset(symbol)}）"
                if market == "币本位"
                else "单格名义金额（USD）"
            ),
            min_value=0.01,
            value=float(grid["order_amount"]),
            step=0.1 if market == "币本位" else 10.0,
            format="%.8f" if market == "币本位" else "%.2f",
        )
        leverage = right2.number_input("杠杆", min_value=1, max_value=125, value=int(grid["leverage"]), step=1)
        save = st.form_submit_button("保存", type="primary")

    if not save:
        return
    if not symbol.strip() or float(anchor_price) <= 0:
        st.error("请填写有效的交易对和锚定价格。")
        return
    payload = form_payload(
        symbol,
        market,
        mode,
        float(anchor_price),
        float(ratio),
        int(count),
        float(order_amount),
        int(leverage),
        poll_interval_sec=grid["poll_interval_sec"],
        move_grid=grid["move_grid"],
    )
    try:
        backend().update_strategy(grid["id"], payload)
    except GridApiError as exc:
        st.error(str(exc))
    else:
        st.session_state["api_flash_success"] = f"{payload['symbol']} 配置已更新"
        st.rerun()


@st.dialog("删除或归档")
def remove_grid_dialog(grid: dict) -> None:
    st.write(f"请选择如何处理 **{grid['symbol']}**。")
    archive_col, delete_col = st.columns(2)
    if archive_col.button("归档", width="stretch"):
        try:
            backend().archive_strategy(grid["id"])
        except GridApiError as exc:
            st.error(str(exc))
        else:
            st.session_state["api_flash_success"] = f"{grid['symbol']} 已归档"
            st.rerun()
    if delete_col.button("永久删除", type="primary", width="stretch"):
        try:
            backend().delete_strategy(grid["id"])
        except GridApiError as exc:
            st.error(str(exc))
        else:
            if st.query_params.get("strategy") == grid["id"]:
                st.query_params.clear()
            st.session_state["api_flash_success"] = f"{grid['symbol']} 已删除"
            st.rerun()


def render_sidebar(strategies: list[dict]) -> str:
    st.sidebar.title("交易管理")
    detail_active = bool(st.query_params.get("strategy") or st.query_params.get("symbol"))
    with st.sidebar.expander("网格交易管理", expanded=True):
        overview = st.button(
            "总览",
            width="stretch",
            type="secondary" if detail_active else "primary",
        )
        detail = st.button(
            "币对详情",
            width="stretch",
            type="primary" if detail_active else "secondary",
            disabled=not strategies,
        )
    if overview:
        st.query_params.clear()
        st.rerun()
    if detail and strategies:
        st.query_params.clear()
        st.query_params["strategy"] = strategies[0]["id"]
        st.rerun()
    return "详情" if detail_active and strategies else "总览"


def change_run_state(strategy_id: str, previous_running: bool, fixed_grid: bool = False) -> None:
    key = f"run_{strategy_id}"
    requested = bool(st.session_state[key])
    try:
        if requested:
            backend().start_strategy(strategy_id)
        else:
            backend().stop_strategy(strategy_id)
    except GridApiError as exc:
        st.session_state[key] = previous_running
        st.session_state["api_flash_error"] = str(exc)
    else:
        st.session_state[f"{key}_server"] = requested
        st.session_state["api_flash_success"] = (
            "策略已启动" if requested
            else "策略已停止；挂单和仓位仍保留" if fixed_grid
            else "策略已停止"
        )


def render_add_button() -> None:
    add_col, _ = st.columns([0.42, 8])
    if add_col.button("＋", width="stretch", help="新增币对网格"):
        create_grid_dialog()


def render_overview(strategies: list[dict]) -> None:
    st.title("网格交易总览")

    active = st.toggle("仅显示运行中", value=False)
    rows = [
        item for item in strategies
        if not active or item["status_value"] in {"starting", "running", "error"}
    ]
    if not rows:
        if strategies:
            st.info("当前没有运行中的网格策略。")
        else:
            st.info("当前策略数据库为空。点击下方「＋」，选择「固定区间等 U」创建网格。")
        render_add_button()
        return

    widths = [1.1, .65, .85, 1.35, .7, .8, .6, .7, .7, .7, .7, 1.05, .65, 1.25]
    header = st.columns(widths)
    labels = ["币对", "方向", "当前价格", "当前网格范围", "间距", "单格数量", "杠杆", "网格总数", "待建仓", "已建仓", "待平仓", "状态 / 更新", "启动", "操作"]
    for col, label in zip(header, labels):
        col.markdown(f"**{label}**")
    st.divider()

    for item in rows:
        cols = st.columns(widths, vertical_alignment="center")
        row_color = BINANCE_TEXT if not item["has_started"] else (BINANCE_GREEN if item["mode"] == "做多" else BINANCE_RED)

        def cell(column, value: object) -> None:
            column.markdown(f"<span style='color:{row_color};font-weight:600'>{value}</span>", unsafe_allow_html=True)

        href = f"?strategy={quote(item['id'])}"
        cols[0].markdown(
            f"<a href='{href}' target='_self' "
            f"style='color:{row_color};font-weight:700;text-decoration:none'>"
            f"{escape(item['symbol'])}</a><br><span style='color:{row_color};opacity:.7;font-size:.8rem'>{item['market_label']}</span>",
            unsafe_allow_html=True,
        )
        cell(cols[1], item["mode"])
        precision = item["price_precision"]
        cell(cols[2], format_price(item["current_price"], precision))
        cell(
            cols[3],
            f"{format_price(item['lower_price'], precision)} – "
            f"{format_price(item['upper_price'], precision)}",
        )
        cell(cols[4], f"{item['grid_ratio']:.2f}%")
        cell(cols[5], f"{format_quantity(item['order_amount'])} {item['order_unit']}")
        cell(cols[6], f"{item['leverage']}×")
        cell(cols[7], item["grid_count"])
        cell(cols[8], item["pending_entry"])
        cell(cols[9], item["entered"])
        cell(cols[10], item["pending_exit"])
        cols[11].markdown(
            f"<span style='color:{row_color};font-weight:700'>{item['status']}</span><br>"
            f"<span style='color:{row_color};opacity:.7;font-size:.8rem'>{item['updated_at']}</span>",
            unsafe_allow_html=True,
        )
        is_running = item["status_value"] in {"starting", "running", "error"}
        run_key = f"run_{item['id']}"
        server_key = f"{run_key}_server"
        if st.session_state.get(server_key) != is_running:
            st.session_state[run_key] = is_running
            st.session_state[server_key] = is_running
        cols[12].toggle(
            "运行",
            key=run_key,
            label_visibility="collapsed",
            disabled=item["archived"],
            help=(
                "固定网格停止后挂单和仓位仍保留，需要单独处理。"
                if item["grid_kind"] == "fixed_equal_usdt" else None
            ),
            on_change=change_run_state,
            args=(item["id"], is_running, item["grid_kind"] == "fixed_equal_usdt"),
        )
        with cols[13]:
            delete_col, edit_col, refresh_col = st.columns(3, gap="small")
            if delete_col.button("×", key=f"delete_{item['id']}", help="删除或归档", width="stretch"):
                remove_grid_dialog(item)
            if edit_col.button(
                "✎",
                key=f"edit_{item['id']}",
                help="编辑配置（启动后不可修改）",
                width="stretch",
                disabled=item["has_started"] or item["archived"] or item["grid_kind"] == "fixed_equal_usdt",
            ):
                edit_grid_dialog(item)
            if refresh_col.button("↻", key=f"refresh_{item['id']}", help="刷新价格", width="stretch"):
                try:
                    backend().refresh_price(item["id"])
                except GridApiError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["api_flash_success"] = f"{item['symbol']} 价格已刷新"
                    st.rerun()
        st.divider()

    st.caption(
        f"共 {len(rows)} 组网格 · "
        f"待建仓 {sum(item['pending_entry'] for item in rows)} 格 · "
        f"已建仓 {sum(item['entered'] for item in rows)} 格 · "
        f"待平仓 {sum(item['pending_exit'] for item in rows)} 格"
    )
    render_add_button()


def base_asset(symbol: str) -> str:
    root = symbol.upper().split("_", 1)[0]
    for quote_asset in ("USDT", "USDC", "FDUSD", "BUSD"):
        if root.endswith(quote_asset):
            return root[: -len(quote_asset)]
    if root.endswith("USD"):
        return root[:-3]
    return root


def format_quantity(value: object) -> str:
    quantity = Decimal(str(value))
    if quantity != quantity.to_integral_value():
        quantity = quantity.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
    text = format(quantity.normalize(), "f")
    integer, dot, fraction = text.partition(".")
    grouped = f"{int(integer):,}"
    return grouped if not dot else f"{grouped}.{fraction}"


def order_text(
    prefix: str,
    order_id: int | None,
    quantity: object,
    symbol: str,
    quantity_unit: str = "base_asset",
) -> str:
    if order_id is None:
        return ""
    text = f"{prefix}:#{order_id}"
    if quantity not in (None, ""):
        unit = "张" if quantity_unit == "contract" else base_asset(symbol)
        text += f" · {format_quantity(quantity)} {unit}"
    return text


def build_cell_rows(
    grid: dict, cells: list[dict], cell_net_profit: dict[str, str] | None = None,
    reinvest_counts: dict[str, int] | None = None,
) -> list[dict]:
    rows = []
    precision = grid["price_precision"]
    stage_column = "交易次数/复投次数/净利 (U)" if grid["grid_kind"] == "fixed_equal_usdt" else "阶段/次数"
    for cell in cells:
        stage = cell["stage"]
        entry_prefix = "成交" if decimal_number(cell.get("open_qty")) > 0 else "挂单"
        entry_text = order_text(
            entry_prefix,
            cell.get("entry_order_id"),
            cell.get("entry_qty"),
            grid["symbol"],
            grid["quantity_unit"],
        )
        exit_text = order_text(
            "挂单",
            cell.get("exit_order_id"),
            cell.get("exit_qty"),
            grid["symbol"],
            grid["quantity_unit"],
        )
        buy_order = ""
        sell_order = ""
        if grid["mode"] == "做多":
            buy_order = entry_text
            sell_order = exit_text
        else:
            sell_order = entry_text
            buy_order = exit_text
        if grid["grid_kind"] == "fixed_equal_usdt":
            if stage == "pending_entry" and cell.get("entry_order_id") is None:
                buy_order = "订单确认中" if cell.get("entry_client_id") else "等待价格带"
            if stage == "pending_exit" and cell.get("exit_order_id") is None:
                sell_order = "订单确认中" if cell.get("exit_client_id") else "等待价格带"
        stage_text = (
            f"{int(cell.get('cycle_count', 0))} / "
            f"{reinvest_counts.get(cell['cell_id'], 0) if reinvest_counts is not None else '—'}"
            if grid["grid_kind"] == "fixed_equal_usdt"
            else f"{STAGE_LABELS.get(stage, stage)} / {int(cell.get('cycle_count', 0))}"
        )
        buy_price_text = format_price(cell["buy_price"], precision)
        sell_price_text = format_price(cell["sell_price"], precision)
        if grid["grid_kind"] == "fixed_equal_usdt":
            open_qty = Decimal(cell["open_qty"])
            buy_order_qty = cell.get("entry_qty") if cell.get("entry_order_id") is not None else None
            paired_qty = (
                open_qty if open_qty > 0
                else Decimal(buy_order_qty) if buy_order_qty not in (None, "")
                else None
            )
            profit_text = (
                f"{Decimal(cell_net_profit[cell['cell_id']]):,.4f}"
                if cell_net_profit is not None and cell["cell_id"] in cell_net_profit
                else "—"
            )
            stage_text += f" / {profit_text}"
            if paired_qty is not None:
                buy_amount = format_quantity(paired_qty * Decimal(cell["buy_price"]))
                sell_amount = format_quantity(paired_qty * Decimal(cell["sell_price"]))
                buy_price_text += f" ({buy_amount} U)"
                sell_price_text += f" ({sell_amount} U)"
        rows.append(
            {
                "网格": f"#{int(cell['index']):03d}",
                "买入/卖出价": f"{buy_price_text} / {sell_price_text}",
                stage_column: stage_text,
                "买入": buy_order,
                "卖出": sell_order,
            }
        )
    return list(reversed(rows))


def visible_grid_cells(grid: dict, cells: list[dict], view: str) -> list[dict]:
    if view == "持仓格":
        return [cell for cell in cells if Decimal(cell["open_qty"]) > 0]
    if view == "空仓格":
        return [cell for cell in cells if Decimal(cell["open_qty"]) == 0]
    if view == "已成交格":
        return [cell for cell in cells if int(cell.get("cycle_count", 0)) > 0]
    if view != "现价附近" or len(cells) <= 25:
        return cells
    ordered = sorted(cells, key=lambda cell: Decimal(cell["buy_price"]))
    price = Decimal(str(
        grid["current_price"] if grid["current_price"] is not None else grid["anchor_price"]
    ))
    nearest = min(
        range(len(ordered)),
        key=lambda index: abs(
            (Decimal(ordered[index]["buy_price"]) + Decimal(ordered[index]["sell_price"])) / 2
            - price
        ),
    )
    start = min(max(0, nearest - 12), len(ordered) - 25)
    return ordered[start:start + 25]


def cell_action_state_key(strategy_id: str) -> str:
    return f"cell_action_pending_{strategy_id}"


def submit_cell_action(strategy_id: str, operation: str, boundary: str) -> None:
    try:
        action = backend().request_cell_action(strategy_id, operation, boundary)
    except GridApiError as exc:
        st.session_state["api_flash_error"] = str(exc)
    else:
        action_text = "新增" if operation == "add" else "删除"
        boundary_text = "上方" if boundary == "upper" else "下方"
        st.session_state[cell_action_state_key(strategy_id)] = {
            "id": action.get("id"),
            "operation": operation,
            "boundary": boundary,
        }
        st.session_state["api_flash_success"] = (
            f"已提交{boundary_text} Cell {action_text}请求，页面会自动更新"
        )
    st.rerun()


@st.dialog("删除边界 Cell")
def confirm_cell_removal(grid: dict, boundary: str, cell: dict) -> None:
    boundary_text = "上方" if boundary == "upper" else "下方"
    st.write(
        f"确认删除 **{grid['symbol']} {boundary_text}**的 "
        f"Cell #{int(cell['index']):03d}？"
    )
    if cell.get("entry_order_id") is not None:
        st.caption(
            f"待建仓单 #{cell['entry_order_id']} 会先由调度器撤销；"
            "只有币安确认零成交后才删除 Cell。"
        )
    else:
        st.caption("调度器会再次检查边界和持仓状态，状态变化时自动拒绝删除。")
    if st.button(
        "确认删除",
        type="primary",
        key=f"confirm_remove_{grid['id']}_{boundary}",
    ):
        submit_cell_action(grid["id"], "remove", boundary)


def render_cell_boundary_controls(
    grid: dict,
    cells: list[dict],
    boundary: str,
    pending_action: bool,
) -> None:
    if grid["grid_kind"] == "fixed_equal_usdt":
        return
    boundary_text = "上方" if boundary == "upper" else "下方"
    cell = cells[-1] if boundary == "upper" else cells[0]
    running = grid["status_value"] in {"starting", "running", "error"}
    protected = (
        decimal_number(cell.get("open_qty")) > 0
        or cell.get("stage") in {"pending_exit", "manual_review"}
    )
    remove_disabled = not running or pending_action or len(cells) <= 1 or protected
    add_disabled = not running or pending_action
    with st.container(
        horizontal=True,
        horizontal_alignment="right" if boundary == "upper" else "left",
    ):
        if st.button(
            f"＋ {boundary_text}",
            key=f"add_cell_{grid['id']}_{boundary}",
            disabled=add_disabled,
            help="在当前价格边界外新增一格",
        ):
            submit_cell_action(grid["id"], "add", boundary)
        if st.button(
            f"－ {boundary_text}",
            key=f"remove_cell_{grid['id']}_{boundary}",
            disabled=remove_disabled,
            help=(
                "持仓、待平仓或状态不确定的 Cell 不能删除"
                if protected
                else "只允许删除当前最外侧 Cell"
            ),
        ):
            confirm_cell_removal(grid, boundary, cell)


def render_fixed_topup(grid: dict, cells: list[dict]) -> None:
    strategy_id = grid["id"]
    try:
        actions = backend().topups(strategy_id)
    except GridApiError as exc:
        st.error(str(exc))
        return
    if any(action["operation"] == "seed" and action["status"] == "applied" for action in actions):
        held = [cell for cell in cells if Decimal(cell["open_qty"]) > 0]
        held_qty = sum((Decimal(cell["open_qty"]) for cell in held), Decimal("0"))
        posted_sells = sum(cell["exit_order_id"] is not None for cell in held)
        waiting_sells = sum(
            cell["stage"] == "pending_exit"
            and cell["exit_order_id"] is None
            and not cell.get("exit_client_id")
            for cell in held
        )
        st.caption(
            f"持仓 {format_quantity(held_qty)} {base_asset(grid['symbol'])}"
            f"（{len(held)} 格） · 卖单 {posted_sells} · 待挂 {waiting_sells}"
        )
    if actions:
        latest = actions[-1]
        if latest["status"] in {"manual_review", "rejected"}:
            message = latest.get("message") or "资金操作未执行，请重新预览"
            if "-4411" in message:
                st.error(
                    "币安测试网账户尚未接受 TradFi 永续合约协议。"
                    "请在 demo.binance.com/futures 确认 SOXLUSDT，"
                    "并核对登录账户与 test.env 的 API Key 属于同一 Demo 账户；"
                    "完成后需新建这组固定网格。"
                )
            else:
                st.error(message)
        elif latest["operation"] == "topup" and latest["status"] in {"requested", "planned", "submitted"}:
            st.info(f"追加资金 {latest['amount_usdt']} U 正在处理")
    if grid["status_value"] not in {"starting", "running", "error"}:
        return
    with st.expander("追加资金", expanded=False):
        render_fixed_topup_form(strategy_id)


def render_fixed_topup_form(strategy_id: str) -> None:
    with st.form(f"topup_form_{strategy_id}"):
        amount = st.number_input(
            "追加金额 (USDT)", min_value=0.01, value=None,
            step=10.0, format="%.2f", key=f"topup_amount_{strategy_id}",
        )
        preview_clicked = st.form_submit_button("预览资金分配")
    state_key = f"topup_preview_{strategy_id}"
    if preview_clicked:
        if amount is None:
            st.error("请填写追加金额。")
        else:
            try:
                details = backend().preview_topup(strategy_id, str(amount))
            except GridApiError as exc:
                st.error(str(exc))
            else:
                st.session_state[state_key] = {
                    "details": details,
                    "request_id": uuid.uuid4().hex,
                }
    planned = st.session_state.get(state_key)
    if not planned:
        return
    details = planned["details"]
    st.info(
        f"每格 +{details['per_cell_usdt']} U · "
        f"合并买入 {details['aggregate_buy_usdt']} U（{details['sell_cell_count']} 格） · "
        f"买单预算 +{details['buy_order_budget_increase_usdt']} U（{details['buy_cell_count']} 格） · "
        f"预计开仓费 {float(details['estimated_opening_fee_usdt']):.4f} U"
    )
    if st.button("确认追加资金", type="primary", key=f"topup_confirm_{strategy_id}"):
        try:
            backend().request_topup(
                strategy_id, planned["request_id"], details["amount_usdt"],
                details["sell_cell_ids"],
            )
        except GridApiError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop(state_key, None)
            st.toast("追加请求已提交，调度器将按实际成交数量分配。")
            st.rerun()


def render_fixed_reinvestment(grid: dict, cells: list[dict]) -> dict[str, int] | None:
    strategy_id = grid["id"]
    try:
        entries = backend().reinvestments(strategy_id)
    except GridApiError as exc:
        st.warning(f"复投记录暂不可用：{exc}")
        return None
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["cell_id"]] = counts.get(entry["cell_id"], 0) + 1
    invested = sum((Decimal(entry["amount_usdt"]) for entry in entries), Decimal("0"))
    enabled = grid["auto_reinvest_enabled"]
    label = "开启" if enabled else "关闭"
    with st.expander(f"自动复投 · {label} · {len(entries)} 次 / {invested:.4f} U", expanded=False):
        st.caption("每格完成卖出后，若该格累计净利扣除已复投金额仍足够，下一轮买入数量增加一个交易所数量步长；已有卖单不单独补买。")
        if st.button("关闭自动复投" if enabled else "开启自动复投", key=f"reinvest_toggle_{strategy_id}"):
            try:
                backend().set_auto_reinvest(strategy_id, not enabled)
            except GridApiError as exc:
                st.error(str(exc))
            else:
                st.rerun()
        if entries:
            indices = {cell["cell_id"]: int(cell["index"]) for cell in cells}
            st.dataframe(
                pd.DataFrame([
                    {
                        "格子": f"#{indices.get(entry['cell_id'], 0):03d}",
                        "完成轮数": entry["cycle_count"],
                        "增加数量": entry["quantity_step"],
                        "使用净利 (U)": entry["amount_usdt"],
                    }
                    for entry in entries[-20:][::-1]
                ]),
                hide_index=True, width="stretch",
            )
    return counts


def _render_detail_live(strategy_id: str) -> None:
    try:
        fresh_strategies = load_strategies()
    except GridApiError as exc:
        st.error(str(exc))
        return
    grid = strategy_by_id(fresh_strategies, strategy_id)
    if grid is None:
        st.warning("该网格已不存在，请返回总览。")
        return
    direction_color = BINANCE_GREEN if grid["mode"] == "做多" else BINANCE_RED
    precision = grid["price_precision"]
    performance = None
    performance_error = None
    entry_price = None
    try:
        if grid["grid_kind"] == "fixed_equal_usdt" and grid["has_started"]:
            performance = backend().performance(grid["id"])
            entry_price = performance.get("position_entry_price")
        else:
            entry_price = backend().position(grid["id"]).get("entry_price")
    except GridApiError as exc:
        performance_error = str(exc)
    per_cell_label = (
        f"{format_quantity(grid['order_amount'])} {grid['order_unit']}/格"
        if grid["market_type"] == "coinm"
        else f"{format_quantity(grid['order_amount'])} U/格"
    )
    grid_configuration = (
        f"{format_price(grid['lower_price'], precision)}–"
        f"{format_price(grid['upper_price'], precision)} · "
        f"{grid['grid_count']} 格 · {grid['grid_ratio']:.2f}% · {per_cell_label}"
    )
    budget_runtime = (
        f"{format_quantity(grid['grid_budget_usdt'])} U · {grid['runtime']}"
        if grid["grid_kind"] == "fixed_equal_usdt"
        else grid["runtime"]
    )
    config_items = [
        ("合约 / 方向 / 杠杆", f"{grid['market_label']} · {grid['mode']} {grid['leverage']}×"),
        ("网格范围 / 总数 / 间距 / 单格金额", grid_configuration),
        ("预算 / 运行", budget_runtime),
    ]
    def shown_price(value: str | None) -> str:
        return "—" if value is None else format_price(value, precision)

    if grid["grid_kind"] == "fixed_equal_usdt":
        price_items = [
            ("初始建仓价", shown_price(performance.get("initial_entry_price") if performance else None)),
            ("建仓均价", shown_price(entry_price)),
            ("当前价", shown_price(grid["current_price"])),
            ("损益两平价", shown_price(performance.get("position_break_even_price") if performance else None)),
        ]
        summary_sections = [("config", config_items), ("prices", price_items)]
    else:
        summary_sections = [
            ("", config_items + [("建仓价 / 当前价", f"{shown_price(entry_price)} / {shown_price(grid['current_price'])}")])
        ]
    for section_class, summary_items in summary_sections:
        summary_html = "".join(
            f"<div class='item'><div class='label'>{escape(label)}</div>"
            f"<div class='value'>{escape(value)}</div></div>"
            for label, value in summary_items
        )
        st.markdown(
            f"<div class='grid-summary {section_class}' style='color:{direction_color}'>{summary_html}</div>",
            unsafe_allow_html=True,
        )
    if grid["grid_kind"] == "fixed_equal_usdt":
        if performance is not None:
            metrics = st.columns(4)
            metrics[0].metric("总交易次数", f"{int(performance['completed_cycles']):,}")
        if (performance is not None and performance["coverage_complete"]
                and performance["accounting_complete"]
                and performance["net_grid_profit_usdt"] is not None):
            metrics[1].metric("纯网格盈利", f"{Decimal(performance['net_grid_profit_usdt']):,.4f} U")
            metrics[2].metric("网格收益率", f"{Decimal(performance['grid_profit_pct']):,.3f}%")
            annualized = performance["simple_annualized_pct"]
            metrics[3].metric(
                "简单年化", "—" if annualized is None else f"{Decimal(annualized):,.2f}%"
            )
        elif performance is not None:
            st.warning("成交历史或买卖配对尚未完整，或手续费币种不是 USDT；暂不显示网格收益。")
        elif performance_error is not None:
            st.warning(f"盈利数据暂不可用：{performance_error}")

    try:
        cells = backend().cells(grid["id"])
    except GridApiError as exc:
        st.error(str(exc))
        return
    if not cells:
        st.info("该策略还没有 Cell；启动后由调度器初始化网格。")
        return
    reinvest_counts = None
    if grid["grid_kind"] == "fixed_equal_usdt":
        render_fixed_topup(grid, cells)
        reinvest_counts = render_fixed_reinvestment(grid, cells)
    try:
        actions = backend().cell_actions(grid["id"])
    except GridApiError as exc:
        st.error(str(exc))
        return
    pending_actions = [action for action in actions if action.get("status") == "pending"]
    pending_action = bool(pending_actions)
    state_key = cell_action_state_key(grid["id"])
    tracked = st.session_state.get(state_key)
    tracked_action = None
    if tracked is not None and tracked.get("id") is not None:
        tracked_action = next(
            (action for action in actions if action.get("id") == tracked["id"]),
            None,
        )

    if tracked is not None and not pending_action:
        st.session_state.pop(state_key, None)
        operation_text = "新增" if tracked.get("operation") == "add" else "删除"
        boundary_text = "上方" if tracked.get("boundary") == "upper" else "下方"
        if tracked_action is not None and tracked_action.get("status") == "failed":
            st.session_state["api_flash_error"] = (
                f"{boundary_text} Cell {operation_text}失败："
                f"{tracked_action.get('message') or '调度器拒绝了该操作'}"
            )
        else:
            st.session_state["api_flash_success"] = f"{boundary_text} Cell {operation_text}完成"
        st.rerun()

    if tracked is None and pending_action:
        action = pending_actions[0]
        st.session_state[state_key] = {
            "id": action.get("id"),
            "operation": action.get("operation"),
            "boundary": action.get("boundary"),
        }
        st.rerun()

    if pending_action:
        st.caption("Cell 调整请求正在由调度器串行处理，本区域会自动更新。")
    view = "全部"
    if len(cells) > 25:
        view_column, count_column = st.columns([1, 3], vertical_alignment="bottom")
        with view_column:
            view = st.selectbox(
                "网格视图",
                ("现价附近", "持仓格", "空仓格", "已成交格", "全部"),
                index=0 if grid["grid_kind"] == "fixed_equal_usdt" else 4,
                key=f"grid_view_{grid['id']}",
            )
        visible_cells = visible_grid_cells(grid, cells, view)
        with count_column:
            st.caption(f"显示 {len(visible_cells)} / {len(cells)} 格 · 表格内可滚动")
    else:
        visible_cells = cells
    cell_net_profit = (
        performance.get("cell_net_grid_profit_usdt")
        if performance is not None and performance["coverage_complete"]
        and performance["accounting_complete"]
        else None
    )
    rows = build_cell_rows(grid, visible_cells, cell_net_profit, reinvest_counts)
    frame = pd.DataFrame(rows)
    stage_column = "交易次数/复投次数/净利 (U)" if grid["grid_kind"] == "fixed_equal_usdt" else "阶段/次数"
    stage_by_grid = {
        f"#{int(cell['index']):03d}": STAGE_LABELS.get(cell["stage"], cell["stage"])
        for cell in visible_cells
    }

    def color_grid_row(row: pd.Series) -> list[str]:
        stage = stage_by_grid[row["网格"]]
        if stage == "未触发":
            color = BINANCE_TEXT
        elif stage == "待建仓":
            color = BINANCE_GREEN if grid["mode"] == "做多" else BINANCE_RED
        elif stage == "待平仓":
            color = BINANCE_RED if grid["mode"] == "做多" else BINANCE_GREEN
        else:
            color = BINANCE_YELLOW
        return [f"color: {color}; font-weight: 600; background-color: transparent;"] * len(row)

    render_cell_boundary_controls(grid, cells, "upper", pending_action)
    if frame.empty:
        st.info(f"当前没有{view}。")
    else:
        styled_frame = frame.style.apply(color_grid_row, axis=1)
        st.dataframe(
            styled_frame,
            width="stretch",
            height=min(520, max(80, 36 * (len(frame) + 1) + 3)),
            row_height=35,
            hide_index=True,
            column_config={
                "网格": st.column_config.TextColumn("网格", width="small"),
                "买入/卖出价": st.column_config.TextColumn(
                    "买入/卖出价", width="large" if grid["grid_kind"] == "fixed_equal_usdt" else "medium"
                ),
                stage_column: st.column_config.TextColumn(
                    stage_column, width="medium",
                    help="交易次数 / 复投次数 / 累计纯网格净利（U）"
                    if grid["grid_kind"] == "fixed_equal_usdt" else None,
                ),
                "买入": st.column_config.TextColumn("买入", width="medium"),
                "卖出": st.column_config.TextColumn("卖出", width="medium"),
            },
        )
    render_cell_boundary_controls(grid, cells, "lower", pending_action)


@st.fragment
def render_detail_live(strategy_id: str) -> None:
    _render_detail_live(strategy_id)


@st.fragment(run_every="1s")
def render_pending_detail_live(strategy_id: str) -> None:
    _render_detail_live(strategy_id)


def render_detail(strategies: list[dict]) -> None:
    requested_id = st.query_params.get("strategy")
    if not requested_id and st.query_params.get("symbol"):
        legacy_symbol = st.query_params.get("symbol")
        requested_id = next((item["id"] for item in strategies if item["symbol"] == legacy_symbol), None)
    grid = strategy_by_id(strategies, requested_id or "") or strategies[0]

    st.markdown(f"### {grid['symbol']} 网格详情")
    options = [item["id"] for item in strategies]
    labels = {
        item["id"]: f"{item['symbol']} · {item['market_label']} · {item['mode']} · {item['id'][-8:]}"
        for item in strategies
    }
    selector, _ = st.columns([1.7, 6])
    selected = selector.selectbox(
        "切换币对",
        options,
        index=options.index(grid["id"]),
        format_func=lambda strategy_id: labels[strategy_id],
        label_visibility="collapsed",
    )
    if selected != requested_id:
        st.query_params.clear()
        st.query_params["strategy"] = selected
        st.rerun()

    if grid["grid_kind"] == "fixed_equal_usdt" or st.session_state.get(cell_action_state_key(grid["id"])):
        render_pending_detail_live(grid["id"])
    else:
        render_detail_live(grid["id"])


apply_styles()
display_flash()
try:
    strategies = load_strategies()
except GridApiError as exc:
    st.sidebar.title("交易管理")
    st.sidebar.caption(f"API {api_base_url()}")
    st.error(str(exc))
    st.info("请先启动 FastAPI：.venv/bin/uvicorn grid_server.api:create_app --factory --port 8100 --workers 1")
    st.stop()

current_page = render_sidebar(strategies)
if current_page == "详情":
    render_detail(strategies)
else:
    render_overview(strategies)
