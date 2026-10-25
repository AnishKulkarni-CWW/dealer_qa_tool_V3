"""Shared Streamlit rendering for every QA result table.

Why the implementation lives here rather than in app.py: the same three
shapes of result get rendered in half a dozen places (Content QA, Exact
Match QA, Styling QA, and each Advanced QA module), and they must all
behave identically — otherwise a reviewer learns one table's rules and is
then surprised by the next one.

THE TABLE
---------
Every result table is one complete list — "All records" — under the
section's count tiles:

    [ checks ] [ passed ] [ failed ] [ warnings ]      <- render_stat_row
    All records (24)
      #  ITEM                STATUS   DETAIL

Each row carries its own Pass / Warn / Fail colour, and the rows run
Fail first, then Warn, then Pass, so what needs fixing is at the top of
one table read top to bottom — no switching between Issues / Warnings /
Passed tabs. There is no "Expand" popup: the table is already shown in
full on the page. (The popup was also where the app crashed — closing it
with the X or Esc left it flagged open, so the next Expand tried to open
a second dialog in the same run, which Streamlit refuses.)

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
    "module": "Check",
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


# Fail first, then Warn, then Pass. A status the vocabulary does not know
# sorts with the failures: it is exactly what a reviewer must not miss.
_ATTENTION_ORDER = {"fail": 0, "": 0, "warn": 1, "pass": 2}


def _attention_first(df: pd.DataFrame, status_col: str) -> pd.DataFrame:
    """The rows that need action at the top. The sort is stable, so within
    each status the rows keep the order the check produced them in."""
    if status_col not in df.columns:
        return df
    rank = df[status_col].apply(lambda v: _ATTENTION_ORDER[_status_class(v)])
    return (df.assign(_attention_rank=rank)
              .sort_values("_attention_rank", kind="stable")
              .drop(columns="_attention_rank"))


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

    Four KPI tiles, a heading and a one-row table, all to say "1 of 1
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


def render_qa_table(
    df: pd.DataFrame,
    status_col: str,
    table_key: str,
    pass_label: str = "Passed",
) -> None:
    """The single shared QA table renderer — see the module docstring.

    `table_key` and `pass_label` are accepted for the callers' sake; the
    table has no widgets of its own any more, so neither is used."""
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

    st.markdown(
        f'<div class="oq-records">All records ({len(df)})</div>',
        unsafe_allow_html=True,
    )
    _render_table(_attention_first(df, status_col), status_col)


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
        _empty("No Advanced QA checks produced results for this email.")
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
        total=len(results), total_label="Checks run",
    )

    render_qa_table(
        pd.DataFrame(rows), status_col="status",
        table_key=table_key, pass_label="Modules fully passed",
    )
