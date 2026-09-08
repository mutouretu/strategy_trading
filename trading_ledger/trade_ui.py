from __future__ import annotations

import html
from decimal import Decimal

import pandas as pd
import streamlit as st

from trading_ledger.application.contracts import (
    AddTrackedInstrumentCommand,
    ApplicationError,
    ArchiveTrackedInstrumentCommand,
    CloseTrackedInstrumentCommand,
    ConfirmManualTradeCommand,
    GetMonthlyStatisticsQuery,
    ListOperationHistoryQuery,
    ListStatisticsMonthsQuery,
    ListTrackingQuery,
    PreviewManualTradeCommand,
    RecordDailyValuationCommand,
    RefreshTrackingPricesCommand,
    UpdateTrackedInstrumentCommand,
)
from trading_ledger.application.service import TradingLedgerApplication
from trading_ledger.domain import OperationKind, TradeSide


MARKET_UP_COLOR = "#FCA5A5"
MARKET_DOWN_COLOR = "#86EFAC"
MARKET_FLAT_COLOR = "#B6BEC9"


def money(value: Decimal | None) -> str:
    return "—" if value is None else f"¥{value:,.2f}"


def pct(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:.2%}"


def _operation_style(value: str) -> str:
    if value in {"买入", "冲正买入"}:
        return "background-color: #183A4C; color: #7DD3FC; font-weight: 700;"
    if value in {"卖出", "冲正卖出"}:
        return "background-color: #423C26; color: #FCD34D; font-weight: 700;"
    return "background-color: #292F38; color: #B6BEC9; font-weight: 700;"


def _profit_color(value) -> str:
    if value is None or pd.isna(value):
        return MARKET_FLAT_COLOR
    if value > 0:
        return MARKET_UP_COLOR
    if value < 0:
        return MARKET_DOWN_COLOR
    return MARKET_FLAT_COLOR


def _profit_style(value) -> str:
    return f"color: {_profit_color(value)}; font-weight: 700;"


def _profit_metric(label: str, text: str, value, key: str) -> None:
    with st.container(key=key):
        st.markdown(
            f'<style>.st-key-{key} [data-testid="stMetricValue"] '
            f'{{ color: {_profit_color(value)}; }}</style>',
            unsafe_allow_html=True,
        )
        st.metric(label, text)


def _show_error(error: ApplicationError) -> None:
    st.error(error.detail.message)


def _set_notice(message: str) -> None:
    st.session_state["ledger_notice"] = message


def _show_notice() -> None:
    if notice := st.session_state.pop("ledger_notice", None):
        st.success(notice)


def _price_placeholder(current_price: Decimal | None) -> str:
    if current_price is None:
        return "请输入成交价格"
    return f"请输入成交价格，如 {current_price:.2f}"


def _fill_tracking_identity(application: TradingLedgerApplication) -> None:
    st.session_state.pop("tracking_add_lookup_error", None)
    try:
        identity = application.lookup_instrument(st.session_state["tracking_add_symbol"])
    except ApplicationError as error:
        st.session_state["tracking_add_lookup_error"] = error.detail.message
    else:
        st.session_state["tracking_add_symbol"] = identity.symbol
        st.session_state["tracking_add_name"] = identity.name


@st.dialog("添加观察股", icon=":material/add:")
def render_add_tracking_dialog(
    application: TradingLedgerApplication, project_key: str, actor: str
) -> None:
    with st.form("add_tracking_form"):
        code_column, fetch_column = st.columns([4, 1], vertical_alignment="bottom")
        symbol = code_column.text_input(
            "股票代码 *", placeholder="例如：300377", key="tracking_add_symbol"
        )
        fetch_column.form_submit_button(
            "获取", on_click=_fill_tracking_identity, args=(application,), width="stretch"
        )
        name = st.text_input(
            "股票名称（选填）", placeholder="点击获取自动填写，也可手动输入",
            key="tracking_add_name",
        )
        if error := st.session_state.get("tracking_add_lookup_error"):
            st.warning(error)
        source_text = st.text_input(
            "来源 *", placeholder="例如：选股理由、研究员、策略或信息渠道"
        )
        submitted = st.form_submit_button("保存到观察中", type="primary")
    if not submitted:
        return
    if not symbol.strip() or not source_text.strip():
        st.error("股票代码和来源不能为空。")
        return
    try:
        tracking = application.add_tracking(
            AddTrackedInstrumentCommand(
                project_key=project_key,
                symbol=symbol,
                name=name,
                source_text=source_text,
                actor=actor,
            )
        )
    except ApplicationError as error:
        _show_error(error)
    else:
        _set_notice(f"{tracking.name or tracking.symbol} 已加入观察中。")
        st.rerun()


def _trade_preview(
    application: TradingLedgerApplication,
    project_key: str,
    symbol: str,
    side: TradeSide,
    percentage: float | None,
    price: float | None,
    signal_text: str,
):
    if percentage is None or price is None or not signal_text.strip():
        return None
    try:
        return application.preview_manual_trade(
            PreviewManualTradeCommand(
                project_key=project_key,
                symbol=symbol,
                side=side,
                allocation_ratio=Decimal(str(percentage)) / Decimal("100"),
                price=Decimal(str(price)),
                signal_text=signal_text,
            )
        )
    except ApplicationError as error:
        st.warning(error.detail.message)
        return None


def _render_preview(preview) -> None:
    details = [
        f"自动计算 {preview.quantity:,.0f} 股",
        f"成交额 {money(preview.gross_amount)}",
        f"手续费 {money(preview.commission_amount)}",
    ]
    if preview.side is TradeSide.SELL:
        details.extend(
            [
                f"印花税 {money(preview.stamp_tax_amount)}",
                f"过户费 {money(preview.transfer_fee_amount)}",
            ]
        )
    st.caption(" · ".join(details))
    label = "预计支出" if preview.side is TradeSide.BUY else "预计到账"
    st.markdown(f"**{label}：{money(abs(preview.net_cash_amount))}**")


@st.dialog("买入", icon=":material/add_shopping_cart:")
def render_buy_dialog(
    application: TradingLedgerApplication,
    project_key: str,
    actor: str,
    symbol: str,
    name: str,
    available_cash: Decimal,
    current_price: Decimal | None,
    total_equity: Decimal,
) -> None:
    cash_ratio = available_cash / total_equity if total_equity > 0 else None
    st.caption(
        f"{symbol} · {name} · 当前可用资金 {money(available_cash)}"
        f" · 可用资金占总权益 {pct(cash_ratio)}"
    )
    percentage = st.number_input(
        "买入比例（%） *",
        min_value=0.01,
        max_value=100.0,
        value=None,
        step=1.0,
        format="%.2f",
        placeholder="请输入可用资金的百分比",
        key=f"tracking_buy_ratio_{symbol}",
    )
    price = st.number_input(
        "买入价格 *",
        min_value=0.01,
        value=None,
        step=0.01,
        format="%.2f",
        placeholder=_price_placeholder(current_price),
        key=f"tracking_buy_price_{symbol}",
    )
    signal = st.text_input(
        "买入信号 *",
        placeholder="请输入本次买入信号",
        key=f"tracking_buy_signal_{symbol}",
    )
    preview = _trade_preview(
        application,
        project_key,
        symbol,
        TradeSide.BUY,
        percentage,
        price,
        signal,
    )
    if preview:
        _render_preview(preview)
    else:
        st.caption("填写比例、价格和信号后显示账本预览。")
    st.caption("买入比例按当前可用资金计算；手续费万分之 0.85，最低 ¥5.00。")
    if st.button(
        "确认买入",
        type="primary",
        icon=":material/check:",
        disabled=preview is None,
        key=f"tracking_buy_confirm_{symbol}",
        width="stretch",
    ):
        try:
            trade = application.confirm_manual_trade(
                ConfirmManualTradeCommand(
                    project_key=project_key,
                    symbol=symbol,
                    side=TradeSide.BUY,
                    allocation_ratio=Decimal(str(percentage)) / Decimal("100"),
                    price=Decimal(str(price)),
                    signal_text=signal,
                    actor=actor,
                )
            )
        except ApplicationError as error:
            _show_error(error)
        else:
            _set_notice(f"{name} 已买入 {trade.quantity:,.0f} 股。")
            st.rerun()


@st.dialog("卖出", icon=":material/sell:")
def render_sell_dialog(
    application: TradingLedgerApplication,
    project_key: str,
    actor: str,
    symbol: str,
    name: str,
    sellable_quantity: Decimal,
    current_price: Decimal | None,
    position_ratio: Decimal | None,
) -> None:
    st.caption(
        f"{symbol} · {name} · 当前可卖持仓 {sellable_quantity:,.0f} 股"
        f" · 该股市值占总权益 {pct(position_ratio)}"
    )
    percentage = st.number_input(
        "卖出比例（%） *",
        min_value=0.01,
        max_value=100.0,
        value=None,
        step=1.0,
        format="%.2f",
        placeholder="请输入该股可卖持仓的百分比",
        key=f"tracking_sell_ratio_{symbol}",
    )
    price = st.number_input(
        "卖出价格 *",
        min_value=0.01,
        value=None,
        step=0.01,
        format="%.2f",
        placeholder=_price_placeholder(current_price),
        key=f"tracking_sell_price_{symbol}",
    )
    signal = st.text_input(
        "卖出信号 *",
        placeholder="请输入本次卖出信号",
        key=f"tracking_sell_signal_{symbol}",
    )
    preview = _trade_preview(
        application,
        project_key,
        symbol,
        TradeSide.SELL,
        percentage,
        price,
        signal,
    )
    if preview:
        _render_preview(preview)
    else:
        st.caption("填写比例、价格和信号后显示账本预览。")
    st.caption(
        "卖出比例按当前可卖持仓计算；手续费万分之 0.85，最低 ¥5.00；"
        "印花税 0.1%，过户费十万分之一。"
    )
    if st.button(
        "确认卖出",
        type="primary",
        icon=":material/check:",
        disabled=preview is None,
        key=f"tracking_sell_confirm_{symbol}",
        width="stretch",
    ):
        try:
            trade = application.confirm_manual_trade(
                ConfirmManualTradeCommand(
                    project_key=project_key,
                    symbol=symbol,
                    side=TradeSide.SELL,
                    allocation_ratio=Decimal(str(percentage)) / Decimal("100"),
                    price=Decimal(str(price)),
                    signal_text=signal,
                    actor=actor,
                )
            )
        except ApplicationError as error:
            _show_error(error)
        else:
            _set_notice(f"{name} 已卖出 {trade.quantity:,.0f} 股。")
            st.rerun()


def _pnl_markup(value: Decimal | None) -> str:
    if value is None:
        return "—"
    color = MARKET_UP_COLOR if value > 0 else MARKET_DOWN_COLOR if value < 0 else MARKET_FLAT_COLOR
    return f'<span style="color:{color};font-weight:700;">{value:+.1%}</span>'


def render_tracking_table(
    application: TradingLedgerApplication,
    project_key: str,
    actor: str,
    tracking_page,
) -> None:
    ratios = [1.25, 1.0, 1.65, 0.72, 0.78, 0.8, 0.75, 2.2]
    headers = st.columns(ratios, vertical_alignment="center")
    for column, label in zip(
        headers[:7],
        ["股票", "加入时间", "来源", "当前仓位", "当前价格", "买入均价", "盈亏"],
    ):
        column.markdown(f"**{label}**")
    header_actions = headers[7].columns([2.4, 1])
    header_actions[0].markdown("**操作**")
    if header_actions[1].button(
        ":material/refresh:",
        help="刷新价格",
        key="tracking_refresh_prices",
    ):
        try:
            result = application.refresh_tracking_prices(
                RefreshTrackingPricesCommand(project_key=project_key, actor=actor)
            )
        except ApplicationError as error:
            _show_error(error)
        else:
            _set_notice(
                f"已更新 {result.updated_count}/{result.requested_count} 只股票。"
            )
            st.rerun()
    st.divider()
    for row in tracking_page.rows:
        columns = st.columns(ratios, vertical_alignment="center")
        columns[0].markdown(f"**{row.symbol}**  \n{html.escape(row.name)}")
        columns[1].markdown(row.added_at.strftime("%Y-%m-%d  \n%H:%M"))
        columns[2].markdown(html.escape(row.source_text))
        columns[3].markdown(f"**{row.position_ratio:.1%}**")
        columns[4].markdown(money(row.reference_price))
        columns[5].markdown(money(row.average_cost))
        columns[6].markdown(_pnl_markup(row.pnl_ratio), unsafe_allow_html=True)
        actions = columns[7].columns(5)
        if actions[0].button(
            ":material/add_shopping_cart:",
            help="买入",
            key=f"tracking_buy_{row.tracking_id}",
            width="stretch",
        ):
            render_buy_dialog(
                application,
                project_key,
                actor,
                row.symbol,
                row.name,
                tracking_page.account.cash_balance,
                row.reference_price,
                total_equity=tracking_page.account.equity,
            )
        if actions[1].button(
            ":material/sell:",
            help=(
                "卖出"
                if row.sellable_quantity > 0
                else "当前没有持仓可卖"
            ),
            disabled=row.sellable_quantity <= 0,
            key=f"tracking_sell_{row.tracking_id}",
            width="stretch",
        ):
            render_sell_dialog(
                application,
                project_key,
                actor,
                row.symbol,
                row.name,
                row.sellable_quantity,
                row.reference_price,
                position_ratio=(
                    row.position_ratio
                    if tracking_page.account.equity > 0 and row.reference_price is not None
                    else None
                ),
            )
        with actions[2].popover(":material/edit:", help="编辑"):
            with st.form(f"tracking_edit_{row.tracking_id}"):
                symbol = st.text_input("股票代码 *", value=row.symbol)
                name = st.text_input(
                    "股票名称（选填）", value=row.name, placeholder="刷新价格后自动补全"
                )
                source = st.text_input("来源 *", value=row.source_text)
                submitted = st.form_submit_button("保存", type="primary")
            if submitted:
                try:
                    application.update_tracking(
                        UpdateTrackedInstrumentCommand(
                            project_key=project_key,
                            tracking_id=row.tracking_id,
                            symbol=symbol,
                            name=name,
                            source_text=source,
                            actor=actor,
                        )
                    )
                except (ApplicationError, ValueError) as error:
                    st.error(
                        error.detail.message
                        if isinstance(error, ApplicationError)
                        else str(error)
                    )
                else:
                    _set_notice(f"{name.strip() or symbol.strip()} 已更新。")
                    st.rerun()
        has_position = row.quantity > 0
        if actions[3].button(
            ":material/remove_circle_outline:",
            help=("移出当前跟踪" if not has_position else "有持仓时不能移出"),
            disabled=has_position,
            key=f"tracking_close_{row.tracking_id}",
            width="stretch",
        ):
            try:
                application.close_tracking(
                    CloseTrackedInstrumentCommand(
                        project_key=project_key,
                        tracking_id=row.tracking_id,
                        actor=actor,
                    )
                )
            except ApplicationError as error:
                _show_error(error)
            else:
                _set_notice(f"{row.name} 已移出当前跟踪。")
                st.rerun()
        if actions[4].button(
            ":material/archive:",
            help=("归档" if not has_position else "有持仓时不能归档"),
            disabled=has_position,
            key=f"tracking_archive_{row.tracking_id}",
            width="stretch",
        ):
            try:
                application.archive_tracking(
                    ArchiveTrackedInstrumentCommand(
                        project_key=project_key,
                        tracking_id=row.tracking_id,
                        actor=actor,
                    )
                )
            except ApplicationError as error:
                _show_error(error)
            else:
                _set_notice(f"{row.name} 已归档。")
                st.rerun()
        st.markdown(
            '<div style="height:1px;background:rgba(48,55,68,0.65);margin:0.05rem 0;"></div>',
            unsafe_allow_html=True,
        )


def current_tracking_page(
    application: TradingLedgerApplication, project_key: str, actor: str
) -> None:
    _show_notice()
    st.markdown(
        '<div class="beili-note">当前跟踪用于记录观察来源和人工买卖。'
        "有持仓的股票不能移出当前跟踪或归档。</div>",
        unsafe_allow_html=True,
    )
    keyword = st.text_input(
        "搜索股票", placeholder="输入股票代码或名称", key="daily_recommendation_search"
    )
    try:
        page = application.list_tracking(
            ListTrackingQuery(project_key=project_key, keyword=keyword)
        )
    except ApplicationError as error:
        _show_error(error)
        return
    metrics = st.columns(4)
    metrics[0].metric("当前跟踪", len(page.rows))
    metrics[1].metric("当前持仓", page.summary.holding_count)
    metrics[2].metric("今日新增", page.summary.today_added_count)
    metrics[3].metric("已到期", page.summary.expired_count)
    st.caption(
        f"账户总权益 {money(page.account.equity)} · "
        f"可用资金 {money(page.account.cash_balance)}"
    )
    if not page.rows:
        st.info("当前没有符合条件的观察或持仓股票。")
    else:
        render_tracking_table(application, project_key, actor, page)
    if st.button(
        "添加观察股",
        type="primary",
        icon=":material/add:",
        key="tracking_add_instrument",
    ):
        for key in ("tracking_add_symbol", "tracking_add_name", "tracking_add_lookup_error"):
            st.session_state.pop(key, None)
        render_add_tracking_dialog(application, project_key, actor)


def _reset_operation_history_page() -> None:
    st.session_state["operation_history_page_number"] = 1


def _set_operation_history_page(page: int) -> None:
    st.session_state["operation_history_page_number"] = page


def operation_history_page(
    application: TradingLedgerApplication, project_key: str
) -> None:
    st.markdown(
        '<div class="beili-note">按股票查询观察、买入、卖出和冲正记录。'
        "观察显示来源，交易显示本次信号。</div>",
        unsafe_allow_html=True,
    )
    requested_symbol = st.session_state.pop("operation_history_symbol", None)
    if requested_symbol:
        st.session_state["operation_history_query"] = requested_symbol
        st.session_state["operation_history_page_number"] = 1
    st.session_state.setdefault("operation_history_page_number", 1)
    keyword = st.text_input(
        "搜索股票",
        placeholder="输入股票代码或名称，留空显示全部",
        key="operation_history_query",
        on_change=_reset_operation_history_page,
    )
    try:
        result = application.operation_history(
            ListOperationHistoryQuery(
                project_key=project_key,
                keyword=keyword,
                page=st.session_state["operation_history_page_number"],
                page_size=50,
            )
        )
    except ApplicationError as error:
        _show_error(error)
        return
    st.session_state["operation_history_page_number"] = result.page
    if not result.rows:
        st.info("暂无操作记录。")
        return
    action_text = {
        (OperationKind.TRACKING, None): "观察",
        (OperationKind.TRADE, TradeSide.BUY): "买入",
        (OperationKind.TRADE, TradeSide.SELL): "卖出",
        (OperationKind.REVERSAL, TradeSide.BUY): "冲正买入",
        (OperationKind.REVERSAL, TradeSide.SELL): "冲正卖出",
    }
    frame = pd.DataFrame(
        [
            {
                "股票代码": row.symbol,
                "股票名称": row.name,
                "记录时间": row.recorded_at.strftime("%Y-%m-%d %H:%M:%S"),
                "操作": action_text[(row.operation_kind, row.side)],
                "来源/信号": row.source_or_signal,
                "成交价格": float(row.price) if row.price is not None else None,
                "交易数量": float(row.quantity) if row.quantity is not None else None,
                "交易金额": float(row.gross_amount) if row.gross_amount is not None else None,
                "资金变动": float(row.cash_change) if row.cash_change is not None else None,
            }
            for row in result.rows
        ]
    )
    st.dataframe(
        frame.style.map(_operation_style, subset=["操作"]).format(
            {
                "成交价格": "{:.2f}",
                "交易数量": "{:,.0f}",
                "交易金额": "{:,.2f}",
                "资金变动": "{:,.2f}",
            },
            na_rep="—",
        ),
        width="stretch",
        hide_index=True,
    )
    st.caption(f"第 {result.page}/{result.page_count} 页 · 共 {result.total_count} 条")
    if result.page_count > 1:
        columns = st.columns([1, 1, 2, 1, 1])
        columns[0].button(
            "首页",
            disabled=result.page == 1,
            on_click=_set_operation_history_page,
            args=(1,),
            width="stretch",
        )
        columns[1].button(
            "上一页",
            disabled=result.page == 1,
            on_click=_set_operation_history_page,
            args=(result.page - 1,),
            width="stretch",
        )
        columns[3].button(
            "下一页",
            disabled=result.page == result.page_count,
            on_click=_set_operation_history_page,
            args=(result.page + 1,),
            width="stretch",
        )
        columns[4].button(
            "末页",
            disabled=result.page == result.page_count,
            on_click=_set_operation_history_page,
            args=(result.page_count,),
            width="stretch",
        )


def _monthly_csv(report) -> bytes:
    rows = [
        {
            "记录类型": "汇总",
            "月份": report.month,
            "股票代码": "",
            "股票名称": "",
            "买入次数": report.buy_count,
            "卖出次数": report.sell_count,
            "期初资金": report.opening_capital,
            "外部资金净流入": report.external_net_flow,
            "期末资金": report.closing_capital,
            "本月盈亏": report.pnl,
            "收益率": report.return_rate,
            "最大回撤": report.max_drawdown,
            "年化波动率": report.annualized_volatility,
            "估值状态": "部分估值" if report.is_partial else "完整",
        }
    ]
    rows.extend(
        {
            "记录类型": "股票明细",
            "月份": report.month,
            "股票代码": row.symbol,
            "股票名称": row.name,
            "买入次数": row.buy_count,
            "卖出次数": row.sell_count,
            "期初资金": "",
            "外部资金净流入": "",
            "期末资金": "",
            "本月盈亏": row.pnl,
            "收益率": row.return_rate,
            "最大回撤": "",
            "年化波动率": "",
            "估值状态": row.closing_status,
        }
        for row in report.rows
    )
    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig")


def _valuation_chart_frame(report) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "日期": pd.Timestamp(point.valuation_date),
                "总权益": float(point.equity),
            }
            for point in report.valuation_points
        ]
    )


def _valuation_chart_domain(report) -> tuple[float, float]:
    opening_capital = float(report.opening_capital)
    margin = opening_capital * 0.15
    return opening_capital - margin, opening_capital + margin


def monthly_trade_statistics_page(
    application: TradingLedgerApplication, project_key: str, actor: str
) -> None:
    _show_notice()
    st.markdown(
        '<div class="beili-note">按月查看交易、外部资金流、收益和风险指标。'
        "启用服务器定时任务后，工作日 15:15 自动记录估值；补录交易后可手动更新。</div>",
        unsafe_allow_html=True,
    )
    if st.button("记录今日估值", type="primary", icon=":material/calculate:"):
        try:
            valuation = application.record_daily_valuation(
                RecordDailyValuationCommand(project_key=project_key, actor=actor)
            )
        except ApplicationError as error:
            _show_error(error)
        else:
            status = "部分估值" if valuation.is_partial else "完整估值"
            _set_notice(f"{valuation.valuation_date} {status}已记录。")
            st.rerun()
    months = application.list_statistics_months(
        ListStatisticsMonthsQuery(project_key=project_key)
    )
    selected_month = st.selectbox("统计月份", months, key="trade_statistics_month")
    report = application.monthly_statistics(
        GetMonthlyStatisticsQuery(project_key=project_key, month=selected_month)
    )
    first = st.columns(4)
    first[0].metric("交易股票", f"{report.instrument_count} 只")
    first[1].metric("买入次数", f"{report.buy_count} 次")
    first[2].metric("卖出次数", f"{report.sell_count} 次")
    first[3].metric("外部资金净流入", money(report.external_net_flow))
    second = st.columns(4)
    second[0].metric("期初资金", money(report.opening_capital))
    with second[1]:
        _profit_metric("本月盈亏", money(report.pnl), report.pnl, "monthly_pnl")
    second[2].metric("期末资金", money(report.closing_capital))
    with second[3]:
        _profit_metric(
            "本月收益率", pct(report.return_rate), report.return_rate, "monthly_return"
        )
    risk = st.columns(2)
    with risk[0]:
        _profit_metric(
            "最大回撤", pct(report.max_drawdown), report.max_drawdown, "monthly_drawdown"
        )
    risk[1].metric("年化波动率", pct(report.annualized_volatility))
    if report.is_partial:
        detail = (
            "缺少价格：" + "、".join(report.missing_symbols)
            if report.missing_symbols
            else "当月还没有估值快照"
        )
        st.warning(f"当前报告为部分估值：{detail}。")
    st.markdown("#### 估值走势")
    valuation_frame = _valuation_chart_frame(report)
    if valuation_frame.empty:
        st.caption("本月记录每日估值后显示走势。")
    else:
        domain_min, domain_max = _valuation_chart_domain(report)
        st.vega_lite_chart(
            valuation_frame,
            {
                "mark": {
                    "type": "line",
                    "color": _profit_color(report.pnl),
                    "point": True,
                },
                "encoding": {
                    "x": {
                        "field": "日期",
                        "type": "temporal",
                        "title": None,
                        "axis": {"format": "%m-%d"},
                    },
                    "y": {
                        "field": "总权益",
                        "type": "quantitative",
                        "title": "总权益",
                        "scale": {
                            "domain": [domain_min, domain_max],
                            "nice": False,
                            "zero": False,
                        },
                        "axis": {"format": ",.0f"},
                    },
                    "tooltip": [
                        {"field": "日期", "type": "temporal", "title": "日期"},
                        {
                            "field": "总权益",
                            "type": "quantitative",
                            "title": "总权益",
                            "format": ",.2f",
                        },
                    ],
                },
            },
            width="stretch",
            height=320,
        )
    if report.rows:
        frame = pd.DataFrame(
            [
                {
                    "股票代码": row.symbol,
                    "股票名称": row.name,
                    "买入次数": row.buy_count,
                    "月末仓位": float(row.closing_position_ratio),
                    "卖出次数": row.sell_count,
                    "卖出金额": float(row.sell_amount),
                    "已实现盈亏": float(row.realized_pnl),
                    "未实现盈亏变动": float(row.unrealized_pnl_change),
                    "本月盈亏": float(row.pnl),
                    "收益率": float(row.return_rate) if row.return_rate is not None else None,
                    "月末状态": row.closing_status,
                }
                for row in report.rows
            ]
        )
        st.dataframe(
            frame.style.map(
                _profit_style,
                subset=["已实现盈亏", "未实现盈亏变动", "本月盈亏", "收益率"],
            ).format(
                {
                    "月末仓位": "{:.2%}",
                    "卖出金额": "{:,.2f}",
                    "已实现盈亏": "{:,.2f}",
                    "未实现盈亏变动": "{:,.2f}",
                    "本月盈亏": "{:,.2f}",
                    "收益率": "{:.2%}",
                },
                na_rep="—",
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("本月暂无交易或持仓记录。")
    st.download_button(
        "导出本月报告",
        data=_monthly_csv(report),
        file_name=f"trading-ledger-{project_key}-{selected_month}.csv",
        mime="text/csv",
        icon=":material/download:",
    )
