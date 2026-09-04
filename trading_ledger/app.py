from __future__ import annotations

import html
import sys
from decimal import Decimal
from pathlib import Path

import streamlit as st

MODULE_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = MODULE_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from trade_ui import (  # noqa: E402
    current_tracking_page,
    monthly_trade_statistics_page,
    operation_history_page,
)
from trading_ledger.application.contracts import (  # noqa: E402
    ApplicationError,
    ArchiveProjectCommand,
    CreateProjectCommand,
    ListProjectsQuery,
    RestoreProjectCommand,
    UpdateProjectCommand,
)
from trading_ledger.bootstrap import current_actor, get_application  # noqa: E402
from trading_ledger.domain import ProjectStatus  # noqa: E402


PROJECT_COLOR_OPTIONS = {
    "blue": ("🔵 蓝色", "#60A5FA"),
    "green": ("🟢 绿色", "#4ADE80"),
    "yellow": ("🟡 黄色", "#FACC15"),
    "purple": ("🟣 紫色", "#C084FC"),
    "orange": ("🟠 橙色", "#FB923C"),
    "rose": ("🔴 红色", "#FB7185"),
}


def _project_name_color(color_key: str) -> str:
    return PROJECT_COLOR_OPTIONS.get(color_key, PROJECT_COLOR_OPTIONS["blue"])[1]


st.set_page_config(
    page_title="交易账本",
    page_icon="↗",
    layout="wide",
    initial_sidebar_state="expanded",
)


def apply_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --beili-navy: #F1F3F7;
            --beili-navy-soft: #C5CBD6;
            --beili-violet: #22D3EE;
            --beili-violet-soft: #12313A;
            --beili-teal: #34D399;
            --beili-teal-soft: #103229;
            --beili-amber: #FB7185;
            --beili-amber-soft: #3A1D29;
            --beili-canvas: #070B12;
            --beili-card: #0E1724;
            --beili-input: #121E2E;
            --beili-line: #223249;
            --beili-text: #E8F0F7;
            --beili-muted: #8494A8;
        }
        .stApp { background: var(--beili-canvas); }
        header[data-testid="stHeader"] { background: transparent; }
        div[data-testid="stDecoration"] { display: none; }
        .block-container {
            max-width: 92rem;
            padding: 2rem 2rem 3rem;
        }
        section[data-testid="stSidebar"] {
            width: 12.5rem !important;
            min-width: 12.5rem !important;
            max-width: 12.5rem !important;
            background:
                radial-gradient(circle at 10% 0%, rgba(34, 211, 238, 0.18), transparent 30%),
                linear-gradient(180deg, #09131F 0%, #060B12 100%);
            border-right: 0;
        }
        section[data-testid="stSidebar"] > div:first-child {
            width: 12.5rem !important;
        }
        section[data-testid="stSidebar"] * {
            color: #F7FAFC;
        }
        section[data-testid="stSidebar"] div[role="radiogroup"] label {
            border-radius: 0.65rem;
            padding: 0.35rem 0.55rem;
            transition: background 120ms ease;
        }
        section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
            background: rgba(34, 211, 238, 0.15);
            box-shadow: inset 3px 0 0 var(--beili-violet);
        }
        .beili-brand {
            margin: 0.2rem 0 1.5rem;
            padding-bottom: 1.2rem;
            border-bottom: 1px solid rgba(255,255,255,0.12);
        }
        .beili-brand-mark {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 2.4rem;
            height: 2.4rem;
            margin-bottom: 0.65rem;
            border-radius: 0.75rem;
            background: linear-gradient(135deg, #0891B2, #10B981);
            color: white;
            font-weight: 800;
            letter-spacing: 0.04em;
            box-shadow: 0 8px 20px rgba(8,145,178,0.24);
        }
        .beili-brand-title {
            color: white;
            font-size: 1.08rem;
            font-weight: 750;
            line-height: 1.3;
        }
        .beili-brand-subtitle {
            margin-top: 0.55rem;
            color: #8FA4B8;
            font-size: 0.78rem;
            font-weight: 600;
            letter-spacing: 0.12em;
            line-height: 1.6;
        }
        .beili-page-header {
            margin-bottom: 1.35rem;
        }
        .beili-page-eyebrow {
            color: var(--beili-violet);
            font-size: 0.72rem;
            font-weight: 750;
            letter-spacing: 0.12em;
        }
        .beili-page-title {
            margin-top: 0.2rem;
            color: var(--beili-navy);
            font-size: 2rem;
            font-weight: 780;
            line-height: 1.18;
        }
        .beili-project-name {
            font-weight: 780;
        }
        .st-key-active_project_row {
            margin: 0.18rem 0;
            padding: 0.45rem 0.6rem 0.25rem;
            background: rgba(34, 211, 238, 0.11);
            border: 1px solid rgba(34, 211, 238, 0.38);
            border-radius: 0.75rem;
            box-shadow: inset 3px 0 0 var(--beili-violet);
        }
        div[data-testid="stMetric"] {
            min-height: 7rem;
            padding: 1rem 1.05rem;
            background: var(--beili-card);
            border: 1px solid var(--beili-line);
            border-radius: 0.9rem;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.16);
        }
        div[data-testid="stMetric"] label {
            color: var(--beili-muted);
        }
        div[data-testid="stMetricValue"] {
            color: var(--beili-navy);
        }
        div[data-testid="stDataFrame"],
        div[data-testid="stForm"],
        details[data-testid="stExpander"] {
            overflow: hidden;
            background: var(--beili-card);
            border: 1px solid var(--beili-line);
            border-radius: 0.9rem;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.14);
        }
        div.stButton > button[kind="primary"],
        div.stFormSubmitButton > button[kind="primary"] {
            border: 0;
            background: linear-gradient(135deg, #0891B2, #10B981);
            color: white;
            box-shadow: 0 6px 16px rgba(8,145,178,0.22);
        }
        div.stButton > button[kind="secondary"],
        div.stDownloadButton > button {
            border-color: #465063;
            background: var(--beili-card);
            color: var(--beili-navy);
        }
        div[data-baseweb="input"] > div,
        div[data-baseweb="select"] > div,
        textarea {
            border-color: #3A4352 !important;
            background: var(--beili-input) !important;
        }
        h1, h2, h3, h4 {
            color: var(--beili-navy);
        }
        .beili-book-heading {
            display: flex;
            align-items: center;
            gap: 0.65rem;
            margin: 1.45rem 0 0.7rem;
            color: var(--beili-navy);
            font-size: 1.05rem;
            font-weight: 750;
        }
        .beili-book-badge {
            padding: 0.24rem 0.55rem;
            border-radius: 999px;
            font-size: 0.7rem;
            font-weight: 800;
            letter-spacing: 0.06em;
        }
        .beili-book-badge.paper {
            color: var(--beili-teal);
            background: var(--beili-teal-soft);
        }
        .beili-book-badge.holding {
            color: var(--beili-amber);
            background: var(--beili-amber-soft);
        }
        .beili-note {
            padding: 0.78rem 0.95rem;
            border-left: 3px solid var(--beili-violet);
            border-radius: 0.25rem 0.65rem 0.65rem 0.25rem;
            background: #1B2029;
            color: #AEB6C3;
            font-size: 0.88rem;
        }
        .beili-table-wrap {
            overflow-x: auto;
            border: 1px solid var(--beili-line);
            border-radius: 0.9rem;
            background: var(--beili-card);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.14);
        }
        .beili-table {
            width: 100%;
            min-width: 1180px;
            border-collapse: collapse;
            font-size: 0.82rem;
        }
        .beili-table th {
            padding: 0.78rem 0.72rem;
            border-bottom: 1px solid var(--beili-line);
            color: var(--beili-muted);
            background: #151920;
            text-align: left;
            white-space: nowrap;
            font-weight: 700;
        }
        .beili-table td {
            padding: 0.72rem;
            border-bottom: 1px solid #252B35;
            color: var(--beili-text);
            vertical-align: middle;
            white-space: nowrap;
        }
        .beili-table tr:last-child td { border-bottom: 0; }
        .beili-table tbody tr:hover td { background: #1E242D; }
        .beili-stock-link {
            color: var(--beili-teal) !important;
            text-decoration: none;
            font-weight: 750;
        }
        .beili-stock-link:hover { text-decoration: underline; }
        .beili-action {
            display: inline-block;
            min-width: 3.5rem;
            padding: 0.23rem 0.48rem;
            border-radius: 0.38rem;
            text-align: center;
            font-weight: 750;
        }
        .beili-action.buy {
            color: #7DD3FC;
            background: rgba(56, 189, 248, 0.22);
        }
        .beili-action.sell {
            color: #FCD34D;
            background: rgba(246, 196, 83, 0.22);
        }
        .beili-action.watch {
            color: #B6BEC9;
            background: rgba(146, 155, 170, 0.12);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _reset_project_page_state() -> None:
    exact_keys = {
        "_applied_query_navigation",
        "daily_recommendation_search",
        "operation_history_query",
        "operation_history_page_number",
        "operation_history_symbol",
        "signal_history_symbol",
        "trade_statistics_month",
        "trade_statistics_month_marker",
    }
    input_prefixes = (
        "tracking_buy_",
        "tracking_sell_",
        "edit_symbol_",
        "edit_name_",
        "edit_source_",
    )
    for key in list(st.session_state):
        if key in exact_keys or key.startswith(input_prefixes):
            st.session_state.pop(key, None)


def _navigation_changed() -> None:
    page = st.session_state.get("navigation_page")
    if page:
        st.query_params["page"] = page


def _load_projects():
    application = get_application()
    return tuple(application.list_projects(ListProjectsQuery()))


def _active_project(projects):
    active = {
        project.project_key: project
        for project in projects
        if project.status is ProjectStatus.ACTIVE
    }
    next_key = st.session_state.pop("_ledger_next_project_key", None)
    selected_key = next_key or st.query_params.get("project")
    if selected_key not in active:
        selected_key = st.session_state.get("active_ledger_project_key")
    if selected_key not in active:
        selected_key = next(iter(active), None)
    previous = st.session_state.get("active_ledger_project_key")
    if selected_key and previous != selected_key:
        _reset_project_page_state()
    if selected_key:
        st.session_state["active_ledger_project_key"] = selected_key
        st.query_params["project"] = selected_key
        return active[selected_key]
    st.session_state.pop("active_ledger_project_key", None)
    st.query_params.pop("project", None)
    return None


def _show_application_error(error: ApplicationError) -> None:
    st.error(error.detail.message)


@st.dialog("新建项目", icon=":material/create_new_folder:")
def render_create_project_dialog() -> None:
    with st.form("create_ledger_project_form", clear_on_submit=True):
        project_name = st.text_input("项目名称 *", placeholder="例如：长线价值组合")
        initial_capital = st.number_input(
            "初始资金 *",
            min_value=0.01,
            value=10_000_000.0,
            step=10_000.0,
            format="%.2f",
        )
        description = st.text_area(
            "项目说明",
            placeholder="选填，用于说明项目用途",
            height=88,
        )
        submitted = st.form_submit_button("创建项目", type="primary")

    if not submitted:
        return
    if not project_name.strip():
        st.error("项目名称不能为空。")
        return
    try:
        project = get_application().create_project(
            CreateProjectCommand(
                project_name=project_name.strip(),
                initial_capital=Decimal(str(initial_capital)),
                description=description.strip(),
                actor=current_actor(),
            )
        )
    except ApplicationError as error:
        _show_application_error(error)
    else:
        st.session_state["_ledger_next_project_key"] = project.project_key
        st.session_state["_ledger_project_notice"] = (
            f"项目“{project.project_name}”已创建。"
        )
        st.rerun()


@st.dialog("项目设置", icon=":material/settings:")
def render_project_settings_dialog(project_key: str) -> None:
    projects = {project.project_key: project for project in _load_projects()}
    project = projects[project_key]
    with st.form(f"ledger_project_settings_{project_key}"):
        project_name = st.text_input(
            "项目名称 *",
            value=project.project_name,
            key=f"ledger_project_name_{project_key}",
        )
        color_keys = tuple(PROJECT_COLOR_OPTIONS)
        color_key = st.selectbox(
            "项目颜色",
            color_keys,
            index=color_keys.index(project.color_key),
            format_func=lambda key: PROJECT_COLOR_OPTIONS[key][0],
            key=f"ledger_project_color_{project_key}",
        )
        description = st.text_area(
            "项目说明",
            value=project.description,
            height=88,
            key=f"ledger_project_description_{project_key}",
        )
        rename_submitted = st.form_submit_button("保存设置", type="primary")

    if rename_submitted:
        if not project_name.strip():
            st.error("项目名称不能为空。")
        else:
            try:
                get_application().update_project(
                    UpdateProjectCommand(
                        project_key=project_key,
                        project_name=project_name.strip(),
                        description=description.strip(),
                        color_key=color_key,
                        actor=current_actor(),
                    )
                )
            except ApplicationError as error:
                _show_application_error(error)
            else:
                st.session_state["_ledger_project_notice"] = "项目设置已保存。"
                st.rerun()

    st.divider()
    if project.status is ProjectStatus.ACTIVE:
        active_keys = [
            key
            for key, item in projects.items()
            if item.status is ProjectStatus.ACTIVE
        ]
        st.caption("归档后项目只读，但仍会保留在项目列表和历史账本中。")
        if st.button(
            "归档项目",
            icon=":material/archive:",
            disabled=len(active_keys) <= 1,
            help="至少需要保留一个活跃项目。" if len(active_keys) <= 1 else None,
            width="stretch",
        ):
            try:
                get_application().archive_project(
                    ArchiveProjectCommand(
                        project_key=project_key,
                        actor=current_actor(),
                    )
                )
            except ApplicationError as error:
                _show_application_error(error)
            else:
                st.session_state["_ledger_next_project_key"] = next(
                    key for key in active_keys if key != project_key
                )
                st.session_state["_ledger_project_notice"] = (
                    f"项目“{project.project_name}”已归档。"
                )
                st.rerun()
    else:
        st.caption("该项目已归档，恢复后会重新出现在当前项目选择器中。")
        if st.button(
            "恢复项目",
            icon=":material/unarchive:",
            width="stretch",
        ):
            try:
                get_application().restore_project(
                    RestoreProjectCommand(
                        project_key=project_key,
                        actor=current_actor(),
                    )
                )
            except ApplicationError as error:
                _show_application_error(error)
            else:
                st.session_state["_ledger_next_project_key"] = project_key
                st.session_state["_ledger_project_notice"] = (
                    f"项目“{project.project_name}”已恢复。"
                )
                st.rerun()


def render_project_management_page(projects, active_project) -> None:
    active_project_key = active_project.project_key if active_project else None
    if notice := st.session_state.pop("_ledger_project_notice", None):
        st.success(notice)
    st.markdown(
        '<div class="beili-note">集中查看和维护交易账本项目。'
        "项目、账户和初始资金已经保存到本地账本。</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="beili-book-heading"><span class="beili-book-badge paper">'
        "项目</span><span>项目列表</span></div>",
        unsafe_allow_html=True,
    )
    column_ratios = [1.55, 0.65, 1.0, 1.9, 1.0]
    header = st.columns(column_ratios, vertical_alignment="center")
    for column, label in zip(
        header,
        ["项目信息", "状态", "初始资金", "项目说明", "操作"],
    ):
        column.markdown(f"**{label}**")
    st.markdown(
        '<div style="height:1px;background:var(--beili-line);margin:-0.35rem 0 0.15rem;"></div>',
        unsafe_allow_html=True,
    )

    if not projects:
        st.info("还没有项目，请先添加第一个项目。")
    else:
        for project in projects:
            project_key = project.project_key
            project_color = _project_name_color(project.color_key)
            row_key = (
                "active_project_row"
                if project_key == active_project_key
                else f"project_row_{project_key}"
            )
            with st.container(key=row_key):
                columns = st.columns(column_ratios, vertical_alignment="center")
                columns[0].markdown(
                    f'<span class="beili-project-name" style="color:{project_color}">'
                    f"{html.escape(project.project_name)}</span><br>"
                    f"<code>{html.escape(project_key)}</code>",
                    unsafe_allow_html=True,
                )
                if project.status is ProjectStatus.ARCHIVED:
                    status_text = "已归档"
                elif project_key == active_project_key:
                    status_text = "当前"
                else:
                    status_text = "活跃"
                columns[1].markdown(status_text)
                columns[2].markdown(f"¥{project.initial_capital:,.2f}")
                columns[3].markdown(html.escape(project.description or "—"))
                actions = columns[4].columns(2)
                if actions[0].button(
                    "当前" if project_key == active_project_key else "进入",
                    disabled=(
                        project.status is ProjectStatus.ARCHIVED
                        or project_key == active_project_key
                    ),
                    key=f"project_management_enter_{project_key}",
                    width="stretch",
                ):
                    st.session_state["_ledger_next_project_key"] = project_key
                    st.query_params["page"] = "当前跟踪"
                    st.query_params.pop("symbol", None)
                    st.rerun()
                if actions[1].button(
                    "编辑",
                    key=f"project_management_edit_{project_key}",
                    width="stretch",
                ):
                    render_project_settings_dialog(project_key)
                st.markdown(
                    '<div style="height:1px;background:rgba(48,55,68,0.65);'
                    'margin:0.05rem 0;"></div>',
                    unsafe_allow_html=True,
                )

    if st.button(
        "添加项目",
        type="primary",
        icon=":material/add:",
        key="project_management_add",
    ):
        render_create_project_dialog()


def main() -> None:
    apply_styles()
    st.sidebar.markdown(
        """
        <div class="beili-brand">
          <div class="beili-brand-mark">账</div>
          <div class="beili-brand-title">交易账本</div>
          <div class="beili-brand-subtitle">记录 · 执行 · 复盘</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    application = get_application()
    projects = _load_projects()
    active_project = _active_project(projects)
    pages = ["当前跟踪", "操作历史", "交易统计", "项目管理"]
    if active_project is None:
        st.session_state["navigation_page"] = "项目管理"
    requested_page = st.query_params.get("page")
    requested_symbol = st.query_params.get("symbol")
    if requested_page == "信号历史":
        requested_page = "操作历史"
    query_navigation = (requested_page, requested_symbol)
    if (
        requested_page in pages
        and st.session_state.get("_applied_query_navigation") != query_navigation
    ):
        st.session_state["navigation_page"] = requested_page
        if requested_symbol:
            st.session_state["operation_history_symbol"] = requested_symbol
        st.session_state["_applied_query_navigation"] = query_navigation

    page = st.sidebar.radio(
        "功能导航",
        pages,
        key="navigation_page",
        on_change=_navigation_changed,
    )
    st.query_params["page"] = page
    page_title = html.escape(page)
    if page != "项目管理" and active_project is not None:
        project_color = _project_name_color(active_project.color_key)
        page_title = (
            f"{html.escape(page)} · "
            f'<span class="beili-project-name" style="color:{project_color}">'
            f"{html.escape(active_project.project_name)}</span>"
        )
    st.markdown(
        f"""
        <div class="beili-page-header">
          <div class="beili-page-eyebrow">TRADING LEDGER · 交易记录</div>
          <div class="beili-page-title">{page_title}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if page == "项目管理":
        render_project_management_page(projects, active_project)
    elif active_project is None:
        st.info("请先在“项目管理”中创建项目。")
    elif page == "当前跟踪":
        current_tracking_page(application, active_project.project_key, current_actor())
    elif page == "操作历史":
        operation_history_page(application, active_project.project_key)
    else:
        monthly_trade_statistics_page(
            application, active_project.project_key, current_actor()
        )


if __name__ == "__main__":
    main()
