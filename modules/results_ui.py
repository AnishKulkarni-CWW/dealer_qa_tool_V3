"""Shared Streamlit rendering for every QA result table.

Why the implementation lives here rather than in app.py: the same three
shapes of result get rendered in half a dozen places (Content QA, Exact
Match QA, Styling QA, and each Advanced QA module), and they must all
behave identically — otherwise a reviewer learns one table's rules and is
then surprised by the next one.

THE TABLE, AS REDESIGNED
------------------------
Every result table now renders as the reference design's tabbed record
view:

    ┌ 3 issues · 1 warning · 20 passed ─────────── [↗ Expand] ┐
    │ Issues (3) │ Warnings (1) │ Passed (20) │ All (24)      │
    └──────────────────────────────────────────────────────────┘
      #  STATUS   ITEM                DETAIL

  * The count strip above the tabs always names the failure and warning
    totals, so nothing that needs attention can hide behind an unopened
    tab — you can see there is something to look at before you click.
  * Tabs are client-side, so switching between them never re-runs the
    script and never recomputes a QA pass.
  * "Issues" comes first and is the tab you land on, because failures are
    the only rows anyone has to act on.
  * "↗ Expand" still opens the COMPLETE table in a modal for reading a
    long run without scrolling the page.

Rendering uses plain HTML tables rather than st.dataframe because a
dataframe truncates long detail text to a single clipped line and adds
its own inner scrollbar — which is exactly wrong for cells that routinely
contain a full sentence explaining why a check failed. Status colours and
every other token come from theme.py, so the palette lives in one place.
"""

from __future__ import annotations

import html as _html
from typing import List, Optional, Sequence

import pandas as pd
import streamlit as st

try:
    from .results import ModuleResult, PASS, FAIL, WARN
    from . import theme as _theme
except ImportError:  # pragma: no cover - standalone use
    from results import ModuleResult, PASS, FAIL, WARN
    import theme as _theme


# Status vocabulary used across the app. "Present" is Content QA's word
# for a pass; "Missing" is its word for a fail.
_PASS_WORDS = {"pass", "passed", "present", "ok", "found"}
_WARN_WORDS = {"warn", "warning"}
_FAIL_WORDS = {"fail", "failed", "missing", "error"}

# Column names prettified for the table head. Anything not listed keeps
# its own name, title-cased.
_HEADERS = {
    "item": "Check",
    "rule": "Rule",
    "status": "Status",
    "severity": "Status",
    "detail": "Detail / suggested action",
    "expected": "Expected",
    "found": "Found",
    "category": "Category",
    "module": "Module",
    "passed": "Passed",
    "warnings": "Warnings",
    "failed": "Failed",
}


def _status_class(value: object) -> str:
    v = str(value or "").strip().lower()
    if v in _PASS_WORDS:
        return "pass"
    if v in _WARN_WORDS:
        return "warn"
    if v in _FAIL_WORDS:
        return "fail"
    return ""


def _is_attention(value: object) -> bool:
    return _status_class(value) in ("warn", "fail")


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value)
    if text.strip().lower() == "nan":
        return ""
    return _html.escape(text).replace("\n", "<br>")


def _header(col: str) -> str:
    return _HEADERS.get(str(col).strip().lower(), str(col).replace("_", " ").title())


def _table_html(df: pd.DataFrame, status_col: str, start_index: int = 1) -> str:
    """One result table. The leading "#" column is the reference design's
    row number; it is generated here rather than taken from the data so
    that a filtered tab still numbers its rows 1..n."""
    if df is None or not len(df):
        return ""

    cols = list(df.columns)
    head = "<th></th>" + "".join(f"<th>{_html.escape(_header(c))}</th>" for c in cols)

    rows_html = []
    for n, (_, row) in enumerate(df.iterrows(), start=start_index):
        cls = _status_class(row.get(status_col, ""))
        cells = [f'<td class="oq-idx">{n}</td>']
        for ci, c in enumerate(cols):
            val = row.get(c, "")
            if c == status_col and cls:
                cells.append(f'<td><span class="oq-status {cls}">{_cell(val)}</span></td>')
            else:
                extra = ' class="oq-first"' if ci == 0 and c != status_col else ""
                cells.append(f"<td{extra}>{_cell(val)}</td>")
        row_cls = f' class="oq-{cls}"' if cls else ""
        rows_html.append(f"<tr{row_cls}>{''.join(cells)}</tr>")

    return (
        '<div class="oq-table-wrap"><table class="oq-table">'
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody>"
        "</table></div>"
    )


# Which column carries the name of the thing checked, and which carries
# the explanation — in the order the app's own tables use them.
_SINGLE_LABEL_COLS = ("item", "check", "rule", "category", "module")
_SINGLE_DETAIL_COLS = ("detail", "expected", "found")


def _first_filled(row, columns) -> str:
    for col in columns:
        text = _cell(row.get(col, "")).strip()
        if text:
            return text
    return ""


def render_single_check(row, status_col: str) -> None:
    """A section holding exactly ONE check, rendered on one line.

    Four KPI tiles, a chip strip, four tabs and a table, all to say "1 of 1
    passed", is most of a screen spent on a single sentence — and a report
    with several such sections becomes mostly furniture. The verdict, what
    was checked and why are the entire content, so that is the entire row.
    """
    cls = _status_class(row.get(status_col, ""))
    label = _first_filled(row, _SINGLE_LABEL_COLS)
    detail = _first_filled(row, _SINGLE_DETAIL_COLS)
    status = _cell(row.get(status_col, "")) or "—"
    st.markdown(
        f'<div class="oq-single oq-{cls or "plain"}">'
        f'<span class="oq-status {cls}">{status}</span>'
        + (f'<span class="oq-single-label">{label}</span>' if label else "")
        + (f'<span class="oq-single-detail">{detail}</span>' if detail else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def _render_table(df: pd.DataFrame, status_col: str) -> None:
    html = _table_html(df, status_col)
    if html:
        st.markdown(html, unsafe_allow_html=True)


def _empty(message: str, tone: str = "plain") -> None:
    icon = _theme.icon_html("check-circle" if tone == "ok" else "info", 16)
    st.markdown(
        f'<div class="oq-empty {"" if tone == "ok" else "plain"}">{icon}'
        f'<span>{_html.escape(message)}</span></div>',
        unsafe_allow_html=True,
    )


def _count_strip(n_fail: int, n_warn: int, n_pass: int, n_total: int) -> None:
    """The always-visible summary above the tabs.

    This is the reason a tabbed table does not hide anything: whichever
    tab is open, the failure and warning totals are on screen.
    """
    bits = []
    if n_fail:
        bits.append(_theme.chip(f"{n_fail} issue" + ("s" if n_fail != 1 else ""), "bad", "x-circle"))
    if n_warn:
        bits.append(_theme.chip(f"{n_warn} warning" + ("s" if n_warn != 1 else ""), "warn", "alert-triangle"))
    if n_pass:
        bits.append(_theme.chip(f"{n_pass} passed", "ok", "check-circle"))
    if not n_fail and not n_warn and n_total:
        bits.insert(0, _theme.chip("All checks passed", "ok", "shield"))
    bits.append(_theme.chip(f"{n_total} checks total", "ghost", "doc-stack"))
    st.markdown(
        f'<div class="dq-kv" style="margin:.1rem 0 .55rem 0">{"".join(bits)}</div>',
        unsafe_allow_html=True,
    )


def render_qa_table(
    df: pd.DataFrame,
    status_col: str,
    table_key: str,
    pass_label: str = "Passed",
) -> None:
    """The single shared QA table renderer — see the module docstring."""
    if df is None or not len(df):
        _empty("No checks were produced for this section.")
        return

    if status_col not in df.columns:
        _render_table(df, status_col)
        return

    # One check needs one line, not the whole apparatus — see
    # `render_single_check`. The tiles above it are suppressed by
    # `render_stat_row` for the same reason.
    if len(df) == 1:
        render_single_check(df.iloc[0], status_col)
        return

    classes = df[status_col].apply(_status_class)
    fail_df = df[classes == "fail"]
    warn_df = df[classes == "warn"]
    pass_df = df[classes == "pass"]
    other_df = df[classes == ""]
    # Rows with a status word the vocabulary does not know are shown with
    # the issues, never dropped: an unrecognised status is exactly the
    # kind of thing a reviewer must see rather than have filtered away.
    if len(other_df):
        fail_df = pd.concat([fail_df, other_df], ignore_index=True)

    n_fail, n_warn, n_pass, n_total = len(fail_df), len(warn_df), len(pass_df), len(df)

    # One header row: the always-visible counts on the left, the table's
    # own action on the right — the same shape as the reference design's
    # result header.
    _counts_col, btn = st.columns([6, 1])
    with _counts_col:
        _count_strip(n_fail, n_warn, n_pass, n_total)
    with btn:
        expand_clicked = st.button(
            "↗ Expand", key=f"{table_key}__expand", use_container_width=True,
            help="Open every row of this table in a full-screen popup.",
        )
    if expand_clicked:
        st.session_state[f"{table_key}__dialog_open"] = True
    if st.session_state.get(f"{table_key}__dialog_open"):
        _open_dialog(df, status_col, table_key)

    tabs = st.tabs([
        f"Issues ({n_fail})",
        f"Warnings ({n_warn})",
        f"{pass_label} ({n_pass})",
        f"All records ({n_total})",
    ])

    with tabs[0]:
        if n_fail:
            _render_table(fail_df, status_col)
        else:
            _empty("Nothing needs attention here — no check failed.", tone="ok")
    with tabs[1]:
        if n_warn:
            _render_table(warn_df, status_col)
        else:
            _empty("No warnings were raised for this section.")
    with tabs[2]:
        if n_pass:
            _render_table(pass_df, status_col)
        else:
            _empty("No rows passed in this section yet.")
    with tabs[3]:
        _render_table(df, status_col)


def _open_dialog(df: pd.DataFrame, status_col: str, table_key: str) -> None:
    """st.dialog needs a decorated function; it arrived in Streamlit 1.31,
    which is this app's documented minimum. On anything older the full
    table is rendered inline instead of in a modal, so the button still
    does something useful rather than raising."""
    dialog_fn = getattr(st, "dialog", None)
    if dialog_fn is None:
        st.markdown("**Full table**")
        _render_table(df, status_col)
        if st.button("Close", key=f"{table_key}__dialog_close_inline"):
            st.session_state[f"{table_key}__dialog_open"] = False
            st.rerun()
        return

    @dialog_fn("Full QA table", width="large")
    def _show():
        _render_table(df, status_col)
        if st.button("Close", key=f"{table_key}__dialog_close", type="primary"):
            st.session_state[f"{table_key}__dialog_open"] = False
            st.rerun()

    _show()


def render_stat_row(passed: int, warnings: int, failed: int, total: Optional[int] = None,
                    total_label: str = "Total checks") -> None:
    """The four KPI tiles used above every result table.

    Draws nothing at all for a section with one check or none: four tiles
    reading 1/1/0/0 say less than the single line underneath them already
    does, and cost a screenful to say it.
    """
    if total is None:
        total = passed + warnings + failed
    if total <= 1:
        return
    _theme.stat_tiles([
        (total_label, total, "total", "doc-stack", ""),
        ("Passed", passed, "ok", "check-circle",
         f"{(passed / total * 100):.1f}%" if total else ""),
        ("Failed", failed, "bad", "x-circle",
         f"{(failed / total * 100):.1f}%" if total else ""),
        ("Warnings", warnings, "warn", "alert-triangle",
         f"{(warnings / total * 100):.1f}%" if total else ""),
    ])


def render_module_result(result: ModuleResult, table_key: str) -> None:
    """Renders one ModuleResult: heading, Pass/Warn/Fail tiles, its item
    table, then any notes and images the module attached."""
    if result is None:
        return

    st.markdown(
        f'<div class="dq-head" style="margin:.2rem 0 .1rem 0">'
        f'<div class="dq-head-ico" style="background:{_theme.BRAND_TINT};color:{_theme.BRAND}">'
        f'{_theme.icon_html("check-square", 18)}</div>'
        f'<div class="dq-head-txt"><div class="dq-head-title">'
        f'{_html.escape(result.module_name)}</div></div></div>',
        unsafe_allow_html=True,
    )

    items = result.items or []
    if items:
        p, f, w = result.counts()
        render_stat_row(passed=p, warnings=w, failed=f, total=len(items))

        df = pd.DataFrame([{
            "category": i.category,
            "rule": i.rule,
            "status": i.status,
            "detail": i.detail,
            "expected": i.expected,
            "found": i.found,
        } for i in items])
        render_qa_table(df, status_col="status", table_key=table_key, pass_label="Passed")

    for note in (result.notes or []):
        st.markdown(f'<div class="oq-note">{note}</div>', unsafe_allow_html=True)

    images = result.images or []
    if images:
        cols = st.columns(min(len(images), 4))
        for i, art in enumerate(images):
            with cols[i % len(cols)]:
                st.image(art.png_bytes, caption=art.label, use_container_width=True)

    if not items and not result.notes and not images:
        _empty("This check did not run — nothing to report.")


def render_summary(results: Sequence[ModuleResult], table_key: str) -> None:
    """One row per module: its overall verdict plus Pass/Warn/Fail counts."""
    results = [r for r in (results or []) if r is not None]
    if not results:
        _empty("No Advanced QA modules produced results for this email.")
        return

    rows = []
    mod_pass = mod_warn = mod_fail = 0
    for r in results:
        p, f, w = r.counts()
        status = r.overall_status() if r.items else WARN
        cls = _status_class(status)
        if cls == "pass":
            mod_pass += 1
        elif cls == "fail":
            mod_fail += 1
        else:
            mod_warn += 1
        rows.append({
            "module": r.module_name,
            "status": status,
            "passed": p,
            "warnings": w,
            "failed": f,
        })

    # The tiles count MODULES, not individual checks, because that is what
    # the table below them lists — one row per module with one verdict.
    # Summing every module's item counts instead produced the confusing
    # pairing of "0 checks" tiles above a four-row table, since a module
    # that could not run at all reports a Warn verdict and zero items.
    render_stat_row(
        passed=mod_pass, warnings=mod_warn, failed=mod_fail,
        total=len(results), total_label="Modules run",
    )

    render_qa_table(
        pd.DataFrame(rows), status_col="status",
        table_key=table_key, pass_label="Modules fully passed",
    )
