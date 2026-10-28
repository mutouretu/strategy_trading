"""Small, local-only drag sorter. No CDN, extra package or parent-DOM hooks."""
from pathlib import Path

import streamlit.components.v2 as components


def _component():
    return components.component(
        "ledger_tracking_sort",
        html='<div class="sort-root"></div>',
        css="""
        .sort-root { font: 14px/1.5 sans-serif; color: var(--st-text-color, #ddd); }
        .group { margin: 0 0 12px; }
        .group-title { opacity: .75; font-size: 12px; margin-bottom: 5px; }
        .item { display: flex; align-items: center; gap: 10px; padding: 8px 12px;
          margin: 4px 0; border: 1px solid #64748b55; border-radius: 7px;
          background: var(--st-secondary-background-color, #17202d); }
        .item.dragging { opacity: .4; }
        .item.over { border-color: #22d3ee; background: #22d3ee22; }
        .handle { cursor: grab; touch-action: none; background: none; border: 0;
          color: inherit; font-size: 20px; padding: 0 6px; }
        .handle:focus-visible { outline: 2px solid #22d3ee; }
        .status { font-size: 12px; opacity: .8; }
        """,
        js=(Path(__file__).parent / "assets" / "tracking_sort.js").read_text(encoding="utf-8"),
    )


def tracking_sort(rows, revision, key):
    data = {"revision": revision, "rows": [
        {"id": row.tracking_id, "label": f"{row.symbol} {row.name}",
         "group": ("持仓" if row.quantity > 0 else "无持仓") + (" · 置顶" if row.is_pinned else "")}
        for row in rows
    ]}
    return _component()(data=data, key=key, on_changed_change=lambda: None).changed
