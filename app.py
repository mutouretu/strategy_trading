from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from html import escape
from urllib.parse import quote

import pandas as pd
import streamlit as st

from gridtrader.config import api_base_url
from gridtrader.price_format import format_price, infer_price_precision
from gridtrader.web_client import GridApiClient, GridApiError


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
        .grid-summary { display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 1rem; margin: .8rem 0 1.15rem; }
        .grid-summary .item { min-width: 0; }
        .grid-summary .label { font-size: .72rem; opacity: .72; margin-bottom: .22rem; }
        .grid-summary .value { font-size: 1.05rem; font-weight: 600; line-height: 1.25; overflow-wrap: anywhere; }
        @media (max-width: 900px) { .grid-summary { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
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
    price_precision = infer_price_precision(
        item.get("anchor_price"),
        item.get("lower_price"),
        item.get("upper_price"),
    )
    return {
        "id": item["strategy_id"],
        "symbol": item["symbol"],
        "mode": mode,
        "mode_value": item["mode"],
        "price_precision": price_precision,
        "current_price": None if item.get("current_price") is None else decimal_number(item["current_price"]),
        "lower_price": None if item.get("lower_price") is None else decimal_number(item["lower_price"]),
        "upper_price": None if item.get("upper_price") is None else decimal_number(item["upper_price"]),
        "grid_ratio": decimal_number(item["grid_ratio"]) * 100,
        "order_usdt": decimal_number(item["order_usdt"]),
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
    mode: str,
    anchor_price: float,
    ratio_percent: float,
    grid_count: int,
    order_usdt: float,
    leverage: int,
    *,
    poll_interval_sec: float = 50.0,
    move_grid: bool = True,
) -> dict:
    ratio = Decimal(str(ratio_percent)) / Decimal("100")
    return {
        "symbol": symbol.strip().upper(),
        "mode": "long" if mode == "做多" else "short",
        "anchor_price": str(anchor_price),
        "grid_ratio": str(ratio),
        "grid_count": int(grid_count),
        "order_usdt": str(order_usdt),
        "leverage": int(leverage),
        "poll_interval_sec": float(poll_interval_sec),
        "move_grid": bool(move_grid),
    }


def display_flash() -> None:
    error = st.session_state.pop("api_flash_error", None)
    success = st.session_state.pop("api_flash_success", None)
    if error:
        st.error(error)
    if success:
        st.toast(success)


@st.dialog("新增币对网格")
def create_grid_dialog() -> None:
    with st.form("create_grid_preview_form"):
        symbol = st.text_input("交易对", placeholder="例如 SOLUSDT")
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
        order_usdt = c3.number_input("单格金额（USDT）", min_value=0.01, value=10.0, step=10.0, format="%.2f")
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
                mode,
                float(anchor_price),
                float(grid_ratio),
                int(grid_count),
                float(order_usdt),
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
        order_usdt = left2.number_input("单格金额（USDT）", min_value=0.01, value=float(grid["order_usdt"]), step=10.0)
        leverage = right2.number_input("杠杆", min_value=1, max_value=125, value=int(grid["leverage"]), step=1)
        save = st.form_submit_button("保存", type="primary")

    if not save:
        return
    if not symbol.strip() or float(anchor_price) <= 0:
        st.error("请填写有效的交易对和锚定价格。")
        return
    payload = form_payload(
        symbol,
        mode,
        float(anchor_price),
        float(ratio),
        int(count),
        float(order_usdt),
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
    st.sidebar.caption(f"API {api_base_url()} · Binance Futures")
    return "详情" if detail_active and strategies else "总览"


def change_run_state(strategy_id: str, previous_running: bool) -> None:
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
        st.session_state["api_flash_success"] = "策略已启动" if requested else "策略已停止"


def render_add_button() -> None:
    add_col, _ = st.columns([0.42, 8])
    if add_col.button("＋", width="stretch", help="新增币对网格"):
        create_grid_dialog()


def render_overview(strategies: list[dict]) -> None:
    st.title("网格交易总览")
    st.caption("每行对应一组触发式跟踪网格；同一币对可以创建多组。")

    active = st.toggle("仅显示运行中", value=False)
    rows = [
        item for item in strategies
        if not active or item["status_value"] in {"starting", "running", "error"}
    ]
    if not rows:
        st.info("暂无符合条件的网格策略。")
        render_add_button()
        return

    widths = [1.1, .65, .85, 1.35, .7, .8, .6, .7, .7, .7, .7, 1.05, .65, 1.25]
    header = st.columns(widths)
    labels = ["币对", "方向", "当前价格", "当前网格范围", "间距", "单格金额", "杠杆", "网格总数", "待建仓", "已建仓", "待平仓", "状态 / 更新", "启动", "操作"]
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
            f"{escape(item['symbol'])}</a><br><span style='color:{row_color};opacity:.7;font-size:.8rem'>U 本位永续</span>",
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
        cell(cols[5], f"{item['order_usdt']:,.2f} U")
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
            on_change=change_run_state,
            args=(item["id"], is_running),
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
                disabled=item["has_started"] or item["archived"],
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
    for quote_asset in ("USDT", "USDC", "FDUSD", "BUSD"):
        if symbol.endswith(quote_asset):
            return symbol[: -len(quote_asset)]
    return symbol


def format_quantity(value: object) -> str:
    quantity = Decimal(str(value))
    text = format(quantity.normalize(), "f")
    integer, dot, fraction = text.partition(".")
    grouped = f"{int(integer):,}"
    return grouped if not dot else f"{grouped}.{fraction}"


def order_text(prefix: str, order_id: int | None, quantity: object, symbol: str) -> str:
    if order_id is None:
        return ""
    text = f"{prefix}:#{order_id}"
    if quantity not in (None, ""):
        text += f" · {format_quantity(quantity)} {base_asset(symbol)}"
    return text


def build_cell_rows(grid: dict, cells: list[dict]) -> list[dict]:
    rows = []
    precision = grid["price_precision"]
    for cell in cells:
        stage = cell["stage"]
        entry_prefix = "成交" if decimal_number(cell.get("open_qty")) > 0 else "挂单"
        entry_text = order_text(
            entry_prefix,
            cell.get("entry_order_id"),
            cell.get("entry_qty"),
            grid["symbol"],
        )
        exit_text = order_text(
            "挂单",
            cell.get("exit_order_id"),
            cell.get("exit_qty"),
            grid["symbol"],
        )
        buy_order = ""
        sell_order = ""
        if grid["mode"] == "做多":
            buy_order = entry_text
            sell_order = exit_text
        else:
            sell_order = entry_text
            buy_order = exit_text
        rows.append(
            {
                "网格": f"#{int(cell['index']):03d}",
                "买入价": format_price(cell["buy_price"], precision),
                "卖出价": format_price(cell["sell_price"], precision),
                "当前阶段": STAGE_LABELS.get(stage, stage),
                "买入": buy_order,
                "卖出": sell_order,
                "成交次数": int(cell.get("cycle_count", 0)),
            }
        )
    return list(reversed(rows))


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
    summary_items = [
        ("方向", grid["mode"]),
        ("当前价格", format_price(grid["current_price"], precision)),
        (
            "网格范围",
            f"{format_price(grid['lower_price'], precision)} – "
            f"{format_price(grid['upper_price'], precision)}",
        ),
        ("间距", f"{grid['grid_ratio']:.2f}%"),
        ("单格金额", f"{grid['order_usdt']:,.2f} U"),
        ("网格总数", str(grid["grid_count"])),
        ("杠杆", f"{grid['leverage']}×"),
        ("运行时间", grid["runtime"]),
    ]
    summary_html = "".join(
        f"<div class='item'><div class='label'>{label}</div><div class='value'>{value}</div></div>"
        for label, value in summary_items
    )
    st.markdown(
        f"<div class='grid-summary' style='color:{direction_color}'>{summary_html}</div>",
        unsafe_allow_html=True,
    )

    try:
        cells = backend().cells(grid["id"])
    except GridApiError as exc:
        st.error(str(exc))
        return
    if not cells:
        st.info("该策略还没有 Cell；启动后由调度器初始化网格。")
        return
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
    rows = build_cell_rows(grid, cells)
    frame = pd.DataFrame(rows)

    def color_grid_row(row: pd.Series) -> list[str]:
        if row["当前阶段"] == "未触发":
            color = BINANCE_TEXT
        elif row["当前阶段"] == "待建仓":
            color = BINANCE_GREEN if grid["mode"] == "做多" else BINANCE_RED
        elif row["当前阶段"] == "待平仓":
            color = BINANCE_RED if grid["mode"] == "做多" else BINANCE_GREEN
        else:
            color = BINANCE_YELLOW
        return [f"color: {color}; font-weight: 600; background-color: transparent;"] * len(row)

    render_cell_boundary_controls(grid, cells, "upper", pending_action)
    styled_frame = frame.style.apply(color_grid_row, axis=1)
    st.dataframe(
        styled_frame,
        width="stretch",
        height=max(80, 36 * (len(frame) + 1) + 3),
        row_height=35,
        hide_index=True,
        column_config={
            "网格": st.column_config.TextColumn("网格", width="small"),
            "买入价": st.column_config.TextColumn("买入价", width="medium"),
            "卖出价": st.column_config.TextColumn("卖出价", width="medium"),
            "当前阶段": st.column_config.TextColumn("当前阶段", width="medium"),
            "买入": st.column_config.TextColumn("买入", width="medium"),
            "卖出": st.column_config.TextColumn("卖出", width="medium"),
            "成交次数": st.column_config.NumberColumn("成交次数", width="small", format="%d"),
        },
    )
    render_cell_boundary_controls(grid, cells, "lower", pending_action)
    st.caption(
        "数据来自 FastAPI、SQLite 和调度器；Cell 增删由调度器串行执行。"
        "删除待建仓 Cell 时会联动撤销零成交挂单。"
    )


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
        item["id"]: f"{item['symbol']} · {item['mode']} · {item['id'][-8:]}"
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

    if st.session_state.get(cell_action_state_key(grid["id"])):
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
    st.info("请先启动 FastAPI：.venv/bin/uvicorn gridtrader.api:create_app --factory --port 8100 --workers 1")
    st.stop()

current_page = render_sidebar(strategies)
if current_page == "详情":
    render_detail(strategies)
else:
    render_overview(strategies)
