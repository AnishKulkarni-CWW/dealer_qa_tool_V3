"""
Feature — Consolidated Excel QA Report.

Collects the Normal QA (Content + Styling) results AND, when enabled, every
Advanced QA module's results (Body QA, Banner QA, Summary, etc.) for EVERY
uploaded email/job in the run, and exports them all into a single,
professionally formatted .xlsx workbook — one workbook for the whole run,
not one file per job.

Design:
  - One "Overview" sheet: one row per job with headline Pass/Fail/Warn
    counts across Content + Style + every Advanced QA module that ran.
  - One "<JobName>" sheet per job carrying that model's WHOLE report,
    stacked top to bottom: a summary block, then Content QA, Styling QA
    and Advanced QA, each under its own heading band. It used to be three
    sheets per job, which turned a nine-model run into twenty-eight tabs
    and made "how did the X3 do?" a hunt across three of them.
  - Sheet names are sanitized/truncated/de-duplicated to respect Excel's
    31-character, no-special-character sheet name limits.

No formulas are needed here (this is a data export, not a financial
model) — everything is written as plain values/styles via openpyxl.
"""

import re
from dataclasses import dataclass
from io import BytesIO
from typing import Dict, List, Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

try:
    from .results import ModuleResult, PASS, FAIL, WARN
except ImportError:  # pragma: no cover - allows standalone use/testing
    from results import ModuleResult, PASS, FAIL, WARN


# =========================================================
# Styling constants
# =========================================================

FONT_NAME = "Arial"

HEADER_FILL = PatternFill("solid", fgColor="1C2B4A")
HEADER_FONT = Font(name=FONT_NAME, size=11, bold=True, color="FFFFFF")

TITLE_FONT = Font(name=FONT_NAME, size=16, bold=True, color="1C2B4A")
SUBTITLE_FONT = Font(name=FONT_NAME, size=10, italic=True, color="595959")
SECTION_FONT = Font(name=FONT_NAME, size=12, bold=True, color="1C2B4A")

STATUS_FILL = {
    "Pass": PatternFill("solid", fgColor="C6EFCE"),
    "Present": PatternFill("solid", fgColor="C6EFCE"),
    "Fail": PatternFill("solid", fgColor="FFC7CE"),
    "Missing": PatternFill("solid", fgColor="FFC7CE"),
    "Warn": PatternFill("solid", fgColor="FFEB9C"),
}
STATUS_FONT = {
    "Pass": Font(name=FONT_NAME, size=10, color="006100"),
    "Present": Font(name=FONT_NAME, size=10, color="006100"),
    "Fail": Font(name=FONT_NAME, size=10, color="9C0006"),
    "Missing": Font(name=FONT_NAME, size=10, color="9C0006"),
    "Warn": Font(name=FONT_NAME, size=10, color="9C6500"),
}

THIN_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)

SECTION_HEADER_FILL = PatternFill("solid", fgColor="1C2B4A")
SECTION_HEADER_FONT = Font(name=FONT_NAME, size=11, bold=True, color="FFFFFF")

# Every table on a model's sheet shares one set of column widths, so the
# sheet reads as one document rather than three tables fighting over the
# same columns. A = what was checked, B = its status, C = why.
SHEET_COL_WIDTHS = [62, 14, 90]
SECTION_BAND_WIDTH = len(SHEET_COL_WIDTHS)

BODY_FONT = Font(name=FONT_NAME, size=10)
WRAP_ALIGN = Alignment(vertical="top", wrap_text=True)
CENTER_ALIGN = Alignment(vertical="center", horizontal="center", wrap_text=True)


# =========================================================
# Data model collected by app.py as each job runs
# =========================================================

@dataclass
class JobReportData:
    """Everything one uploaded email/job contributes to the final workbook."""
    job_name: str
    dealer: str = ""
    region: str = ""
    content_df: Optional[pd.DataFrame] = None            # columns: item, status
    style_df: Optional[pd.DataFrame] = None               # columns: rule, severity, detail
    advanced_module_results: Optional[List[ModuleResult]] = None


# =========================================================
# Helpers
# =========================================================

def _sanitize_sheet_name(name: str, used: Dict[str, int]) -> str:
    """Excel sheet names: max 31 chars, no [ ] : * ? / \\, must be unique."""
    cleaned = re.sub(r"[\[\]\:\*\?\/\\]", "-", name).strip() or "Sheet"
    cleaned = cleaned[:31]
    base = cleaned
    if base not in used:
        used[base] = 0
        return base
    used[base] += 1
    suffix = f" ({used[base]})"
    return base[: 31 - len(suffix)] + suffix


def _autosize_columns(ws: Worksheet, df_columns: List[str], max_width: int = 60, min_width: int = 12) -> None:
    for idx, col_name in enumerate(df_columns, start=1):
        letter = get_column_letter(idx)
        longest = max([len(str(col_name))] + [len(str(v)) for v in []])
        width = min(max(longest + 4, min_width), max_width)
        ws.column_dimensions[letter].width = width


def _write_title_block(ws: Worksheet, title: str, subtitle: str = "", start_row: int = 1) -> int:
    ws.cell(row=start_row, column=1, value=title).font = TITLE_FONT
    row = start_row + 1
    if subtitle:
        ws.cell(row=row, column=1, value=subtitle).font = SUBTITLE_FONT
        row += 1
    return row + 1  # blank row after title block


def _write_table(
    ws: Worksheet,
    start_row: int,
    headers: List[str],
    rows: List[List],
    status_col_indexes: Optional[List[int]] = None,
    col_widths: Optional[List[int]] = None,
    freeze: bool = True,
) -> int:
    """
    Writes a header row + data rows starting at `start_row`, applying
    banded rows, borders, wrap text, and status-cell color coding.
    Returns the row number just after the table.
    """
    status_col_indexes = status_col_indexes or []

    for c_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=start_row, column=c_idx, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER_ALIGN
        cell.border = THIN_BORDER

    r = start_row + 1
    for row_idx, row_vals in enumerate(rows):
        band_fill = PatternFill("solid", fgColor="F2F2F2") if row_idx % 2 == 1 else None
        for c_idx, val in enumerate(row_vals, start=1):
            cell = ws.cell(row=r, column=c_idx, value=val)
            cell.border = THIN_BORDER
            cell.alignment = WRAP_ALIGN
            if c_idx in status_col_indexes and isinstance(val, str) and val in STATUS_FILL:
                cell.fill = STATUS_FILL[val]
                cell.font = STATUS_FONT[val]
            else:
                cell.font = BODY_FONT
                if band_fill is not None:
                    cell.fill = band_fill
        r += 1

    if col_widths:
        for c_idx, width in enumerate(col_widths, start=1):
            ws.column_dimensions[get_column_letter(c_idx)].width = width

    # A model sheet stacks several tables, so freezing on any one of them
    # would pin the wrong rows; only single-table sheets freeze.
    if freeze:
        ws.freeze_panes = ws.cell(row=start_row + 1, column=1).coordinate
    return r + 1


def _counts_from_status_series(statuses: List[str]) -> Dict[str, int]:
    counts = {"Pass": 0, "Fail": 0, "Warn": 0}
    for s in statuses:
        if s in ("Pass", "Present"):
            counts["Pass"] += 1
        elif s in ("Fail", "Missing"):
            counts["Fail"] += 1
        elif s == "Warn":
            counts["Warn"] += 1
    return counts


# =========================================================
# Sheet builders
# =========================================================

def _build_overview_sheet(wb: Workbook, jobs: List[JobReportData]) -> None:
    ws = wb.active
    ws.title = "Overview"

    row = _write_title_block(
        ws, "Dealer Panel QA — Consolidated Report",
        f"{len(jobs)} email(s)/model(s) processed.",
    )

    headers = [
        "Email / Model", "Dealer", "Region",
        "Content Present", "Content Missing",
        "Style Pass", "Style Warn", "Style Fail",
        "Advanced QA Pass", "Advanced QA Warn", "Advanced QA Fail",
        "Overall",
    ]
    rows = []
    for job in jobs:
        content_present = content_missing = 0
        if job.content_df is not None and len(job.content_df):
            # "Present"/"Missing" cover the original fuzzy-match rows; the
            # website-link QA rows (Feature 16) use "Pass"/"Fail" for the
            # same content_df, meaning the same thing ("correct"/"wrong"),
            # so both status vocabularies are counted together here.
            content_present = int(job.content_df["status"].isin(["Present", "Pass"]).sum())
            content_missing = int(job.content_df["status"].isin(["Missing", "Fail"]).sum())

        style_pass = style_warn = style_fail = 0
        if job.style_df is not None and len(job.style_df):
            style_pass = int((job.style_df["severity"] == "Pass").sum())
            style_warn = int((job.style_df["severity"] == "Warn").sum())
            style_fail = int((job.style_df["severity"] == "Fail").sum())

        adv_pass = adv_warn = adv_fail = 0
        if job.advanced_module_results:
            for mr in job.advanced_module_results:
                p, f, w = mr.counts()
                adv_pass += p
                adv_warn += w
                adv_fail += f

        overall = "Fail" if (content_missing or style_fail or adv_fail) else (
            "Warn" if (style_warn or adv_warn) else "Pass"
        )

        rows.append([
            job.job_name, job.dealer, job.region,
            content_present, content_missing,
            style_pass, style_warn, style_fail,
            adv_pass, adv_warn, adv_fail,
            overall,
        ])

    _write_table(
        ws, row, headers, rows,
        status_col_indexes=[len(headers)],
        col_widths=[30, 20, 14, 14, 14, 12, 12, 12, 16, 16, 16, 12],
    )


def _write_section_heading(ws: Worksheet, row: int, text: str, note: str = "") -> int:
    """A band across the sheet that opens one section of a model's report."""
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = SECTION_HEADER_FONT
    cell.fill = SECTION_HEADER_FILL
    cell.alignment = Alignment(vertical="center", horizontal="left")
    for c in range(2, SECTION_BAND_WIDTH + 1):
        band = ws.cell(row=row, column=c)
        band.fill = SECTION_HEADER_FILL
    ws.row_dimensions[row].height = 20
    row += 1
    if note:
        ws.cell(row=row, column=1, value=note).font = SUBTITLE_FONT
        row += 1
    return row


def _write_counts_line(ws: Worksheet, row: int, label: str, counts: Dict[str, int]) -> int:
    """`Passed 14   Warnings 0   Failed 0` under a section heading."""
    ws.cell(row=row, column=1, value=label).font = Font(
        name=FONT_NAME, size=10, bold=True, color="404040")
    pairs = [("Passed", counts.get("Pass", 0)), ("Warnings", counts.get("Warn", 0)),
             ("Failed", counts.get("Fail", 0))]
    col = 2
    for name, value in pairs:
        c = ws.cell(row=row, column=col, value=f"{name}: {value}")
        c.font = Font(name=FONT_NAME, size=10,
                      color={"Passed": "006100", "Warnings": "9C6500", "Failed": "9C0006"}[name],
                      bold=value > 0)
        col += 1
    return row + 1


def _build_model_sheet(wb: Workbook, job: JobReportData, sheet_name: str) -> None:
    """EVERY section of one model's QA on ONE sheet, stacked top to bottom.

    This used to be three sheets per model — Content, Style and Advanced QA
    — which is fine for a single email and unusable for a real run: nine
    uploaded model zips produced twenty-eight tabs, and answering "how did
    the X3 do?" meant hunting through three of them. One sheet per model
    means one tab per model, read straight down: summary, then Content QA,
    then Styling QA, then Advanced QA.

    Column widths are shared by every table on the sheet, so the tables
    have to agree on what each column is for. They do: column A is always
    the thing being checked, column B always its status, column C always
    the explanation.
    """
    ws = wb.create_sheet(sheet_name)

    row = _write_title_block(
        ws,
        f"{job.job_name} — QA Report",
        " · ".join([b for b in (job.dealer, job.region) if b]) or "Dealer not identified",
    )

    content_df = job.content_df if job.content_df is not None else pd.DataFrame(columns=["item", "status"])
    style_df = job.style_df if job.style_df is not None else pd.DataFrame(columns=["rule", "severity", "detail"])
    module_results = job.advanced_module_results or []

    content_counts = _counts_from_status_series(
        [str(v) for v in content_df["status"]] if len(content_df) else [])
    style_counts = _counts_from_status_series(
        [str(v) for v in style_df["severity"]] if len(style_df) else [])
    adv_counts = {"Pass": 0, "Warn": 0, "Fail": 0}
    for mr in module_results:
        p, f, w = mr.counts()
        adv_counts["Pass"] += p
        adv_counts["Warn"] += w
        adv_counts["Fail"] += f

    # ---- summary of this model -----------------------------------------
    row = _write_section_heading(ws, row, "SUMMARY")
    total_fail = content_counts["Fail"] + style_counts["Fail"] + adv_counts["Fail"]
    total_warn = content_counts["Warn"] + style_counts["Warn"] + adv_counts["Warn"]
    overall = "Fail" if total_fail else ("Warn" if total_warn else "Pass")
    summary_rows = [
        ["Content QA", content_counts["Pass"], content_counts["Warn"], content_counts["Fail"]],
        ["Styling QA", style_counts["Pass"], style_counts["Warn"], style_counts["Fail"]],
        ["Advanced QA", adv_counts["Pass"], adv_counts["Warn"], adv_counts["Fail"]],
    ]
    row = _write_table(
        ws, row, ["Section", "Passed", "Warnings", "Failed"], summary_rows,
        col_widths=SHEET_COL_WIDTHS, freeze=False,
    )
    ws.cell(row=row, column=1, value="Overall").font = Font(
        name=FONT_NAME, size=11, bold=True, color="1C2B4A")
    verdict = ws.cell(row=row, column=2, value=overall)
    verdict.fill = STATUS_FILL.get(overall, STATUS_FILL["Warn"])
    verdict.font = STATUS_FONT.get(overall, BODY_FONT)
    verdict.border = THIN_BORDER
    verdict.alignment = CENTER_ALIGN
    row += 2

    # ---- content --------------------------------------------------------
    row = _write_section_heading(
        ws, row, "CONTENT QA",
        "Every line of the dealer's Excel panel must appear in the email.")
    row = _write_counts_line(ws, row, "Totals", content_counts)
    content_rows = [
        [r.get("item", ""), r.get("status", ""), str(r.get("detail", "") or "")]
        for _, r in content_df.iterrows()
    ] if len(content_df) else []
    if content_rows:
        row = _write_table(ws, row, ["Item", "Status", "Detail"], content_rows,
                           status_col_indexes=[2], col_widths=SHEET_COL_WIDTHS, freeze=False)
    else:
        row = _write_empty_note(ws, row, "No content checks were produced for this email.")
    row += 1

    # ---- styling --------------------------------------------------------
    row = _write_section_heading(
        ws, row, "STYLING QA",
        "Fonts, colours, spacing, bold rules, links and image weight.")
    row = _write_counts_line(ws, row, "Totals", style_counts)
    style_rows = [
        [r.get("rule", ""), r.get("severity", ""), str(r.get("detail", "") or "")]
        for _, r in style_df.iterrows()
    ] if len(style_df) else []
    if style_rows:
        row = _write_table(ws, row, ["Rule", "Severity", "Detail"], style_rows,
                           status_col_indexes=[2], col_widths=SHEET_COL_WIDTHS, freeze=False)
    else:
        row = _write_empty_note(ws, row, "No styling checks were produced for this email.")
    row += 1

    # ---- advanced -------------------------------------------------------
    row = _write_section_heading(
        ws, row, "ADVANCED QA",
        "Banner, Body, Dealer Name and OCR checks against the Master reference.")
    if not module_results:
        _write_empty_note(
            ws, row,
            "Advanced QA did not run for this email — switch on 'Enable Advanced QA "
            "(Body / Banner / OCR)' in Validation Options before running.")
        return

    row = _write_counts_line(ws, row, "Totals", adv_counts)
    module_rows = []
    for mr in module_results:
        p, f, w = mr.counts()
        module_rows.append([mr.module_name, mr.overall_status(), f"Passed {p} · Warnings {w} · Failed {f}"])
    row = _write_table(ws, row, ["Module", "Status", "Counts"], module_rows,
                       status_col_indexes=[2], col_widths=SHEET_COL_WIDTHS, freeze=False)
    row += 1

    detail_rows = []
    for mr in module_results:
        for item in mr.items:
            explanation = item.detail or ""
            if item.expected or item.found:
                explanation = (
                    f"{explanation}\n"
                    f"Expected: {item.expected}\n"
                    f"Found: {item.found}"
                ).strip()
            detail_rows.append([
                f"{mr.module_name} — {item.rule}" if item.rule else mr.module_name,
                item.status,
                explanation,
            ])
    if detail_rows:
        ws.cell(row=row, column=1, value="Detailed checks").font = SECTION_FONT
        row += 1
        _write_table(ws, row, ["Check", "Status", "Detail"], detail_rows,
                     status_col_indexes=[2], col_widths=SHEET_COL_WIDTHS, freeze=False)
    else:
        _write_empty_note(
            ws, row,
            "No individual check items were recorded (all inputs empty or disabled).")


def _write_empty_note(ws: Worksheet, row: int, text: str) -> int:
    ws.cell(row=row, column=1, value=text).font = Font(
        name=FONT_NAME, size=10, italic=True, color="767676")
    return row + 1


# =========================================================
# Public entry point
# =========================================================

def build_qa_workbook(jobs: List[JobReportData]) -> bytes:
    """
    Builds the full consolidated workbook for every job in the run and
    returns it as raw .xlsx bytes, ready for a Streamlit download button.
    """
    wb = Workbook()
    _build_overview_sheet(wb, jobs)

    used_names: Dict[str, int] = {"Overview": 0}
    for job in jobs:
        # ONE sheet per model. Three sheets each turned a nine-model run
        # into twenty-eight tabs; everything for a model now reads down a
        # single sheet instead.
        _build_model_sheet(wb, job, _sanitize_sheet_name(job.job_name or "Email", used_names))

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
