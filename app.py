import contextlib
import re
import html as html_escape_module
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from typing import List, Dict, Tuple, Optional

import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup, Comment, Tag

# ---------------------------------------------------------------------
# Extended-feature modules (all optional; app works unchanged without them)
# ---------------------------------------------------------------------
from modules import excel_loader as ext_excel
from modules import dealer_select as ext_dealer
from modules import manual_mode as ext_manual
from modules import priority as ext_priority
from modules import master_image as ext_master_image
from modules import master_pdf as ext_master_pdf
from modules import master_html_zip as ext_master_html_zip
from modules import banner_detect as ext_banner_detect
from modules import banner_compare as ext_banner_compare
from modules import ocr_engine as ext_ocr
from modules import banner_text_qa as ext_banner_text_qa
from modules import body_qa as ext_body_qa
from modules import results_ui as ext_results_ui
from modules import excel_report as ext_excel_report
from modules import theme as ext_theme
# website_link_qa.py is the one module of this project that is NOT
# shipped with the multi-adapt update, so a user who unzips the update
# into a NEW folder rather than over their existing project will not have
# it. Importing it defensively means that situation produces a clear,
# actionable message inside the app instead of a stack trace on startup
# that blocks every other check. The three website/CTA-link rows it
# contributes are reported as skipped rather than silently dropped, so a
# missing module can never be mistaken for a clean pass.
try:
    from modules import website_link_qa as ext_website_link_qa
except ImportError:
    ext_website_link_qa = None
from modules import multi_master as ext_multi_master
from modules import master_pdf_multi as ext_master_pdf_multi
from modules import master_image_multi as ext_master_image_multi
from modules import model_bundle as ext_model_bundle

# OCR engine preference is fixed (was previously a sidebar radio choice).
# PaddleOCR is preferred, with automatic silent fallback to Tesseract if
# PaddleOCR isn't installed / its models aren't cached — see ocr_engine.py.
EXT_OCR_PREFER_KEY = "paddleocr"
from modules.results import ModuleResult as ExtModuleResult
from modules.config import DEFAULT_CONFIG as EXT_CFG, font_label as ext_font_label


st.set_page_config(
    page_title="Dealer Panel QA Tool",
    page_icon="\U0001F697",
    layout="wide",
    initial_sidebar_state="expanded",
)

# The whole visual system — palette, navigation rail, top bar, cards, stat
# tiles, tables and every Streamlit widget restyle — lives in
# modules/theme.py. This ONLY changes appearance; every widget below keeps
# its exact arguments, keys and return values.
#
# Density is read straight from session_state rather than from a local
# variable because the stylesheet has to be injected before ANY widget is
# created, while the control that sets it lives on the Settings view far
# below. Streamlit has already committed the new value to session_state by
# the time the next run reaches this line, so the change lands on the very
# rerun the user's click triggers.
ext_theme.inject(density=st.session_state.get("dq_pref_density", "comfortable"))


# =========================================================
# Normalization utilities
# =========================================================

def normalize_text(text: str) -> str:
    if text is None:
        return ""
    text = str(text)
    text = text.replace("\xa0", " ")
    text = text.replace("\r", "\n")
    text = re.sub(r"[\t ]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = text.strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


def split_lines_keep_order(text: str) -> List[str]:
    lines = []
    for raw in str(text).splitlines():
        line = raw.strip()
        if line:
            lines.append(line)
    return lines


# =========================================================
# Excel: Mailers - NSC  sheet reader
# =========================================================

MAILERS_SHEET_CANDIDATES = ["Mailers - NSC ", "mailers-ncs", "mailers_ncs", "mailers ncs"]


def find_mailers_sheet(xl: pd.ExcelFile) -> Optional[str]:
    for name in xl.sheet_names:
        n = name.strip().lower()
        if n in MAILERS_SHEET_CANDIDATES:
            return name
    for name in xl.sheet_names:
        if "mailer" in name.strip().lower():
            return name
    return None


@dataclass
class DealerRow:
    dealer: str
    region: str
    panel_text: str
    # Lines that must render BOLD per spec: the dealer name (column C's
    # first line) plus every branch/city name (the first line of every
    # column block, including column C's own second line and every extra
    # column). Populated once at Excel-read time in
    # build_dealer_rows_from_df, since the column boundaries needed to
    # identify "first line of each block" are lost once everything is
    # flattened into panel_text. Empty for a DealerRow built any other
    # way (e.g. the Advanced QA manual-entry path), in which case the
    # bold-text check below has nothing to check and is skipped.
    bold_required_lines: List[str] = field(default_factory=list)


def build_dealer_rows_from_df(df: pd.DataFrame, sheet_name: str = "") -> List[DealerRow]:
    """
    Given an already-parsed DataFrame with (at least) 'Dealer', 'Region',
    and a 'Dealer Panel(s)'-named column, builds the full List[DealerRow]
    — including every additional, unlabeled branch/location column
    positioned AFTER the named panels column (see the docstring on
    read_dealer_panels_from_excel, which calls this function, for the
    full reasoning on why those extra columns matter and must be read).

    THIS IS THE SINGLE PLACE this column-reading logic lives. Every
    caller that turns a parsed Excel sheet into List[DealerRow] — whether
    from the default auto-detected sheet or from the sidebar's manual
    "Excel Sheet Selection" dropdown — MUST route through this function
    rather than re-implementing its own column mapping. The extra-column
    fix was previously applied only inside read_dealer_panels_from_excel
    itself; the manual-sheet-selection code path in the sidebar had its
    own separate, never-updated copy of the same column-mapping logic,
    so a user who explicitly picked a sheet from that dropdown (rather
    than leaving it on auto-detect) got the OLD, extra-columns-blind
    behaviour even after the fix shipped — confirmed against a real
    session where the sidebar's "Loaded N dealer row(s) from MANUALLY
    SELECTED sheet" message proved that code path was the one that ran.
    Routing both paths through this one function makes that class of
    bug (two implementations silently drifting apart) structurally
    impossible going forward.
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    col_map = {}
    panels_col_position = None
    for pos, c in enumerate(df.columns):
        cl = c.lower().strip()
        if cl == "dealer":
            col_map["dealer"] = c
        elif cl == "region":
            col_map["region"] = c
        elif "dealer panel" in cl:
            col_map["panels"] = c
            panels_col_position = pos

    missing = [k for k in ("dealer", "panels") if k not in col_map]
    if missing:
        _names = {"dealer": "Dealer", "panels": "Dealer Panels"}
        raise ValueError(
            f"Sheet '{sheet_name}' has no "
            + " or ".join(f"'{_names[m]}'" for m in missing)
            + " column — pick the sheet that holds the dealer panels."
        )

    # The columns after "Dealer Panels" that sit under its (merged) header
    # are the dealer's other branch/location blocks. pandas reads those as
    # "Unnamed: N". A column with a heading of its own is a different field
    # — the mastersheet's "Print Ad - NSC" tab ends in "Single number for
    # GKL" — and reading it as a branch put a stray phone number into the
    # panel as if it were an address line.
    def _is_branch_column(col) -> bool:
        name = str(col).strip().lower()
        return (not name or name == "nan" or name.startswith("unnamed")
                or any(w in name for w in ("panel", "branch", "location", "address")))

    extra_panel_columns = [c for c in df.columns[panels_col_position + 1:] if _is_branch_column(c)]

    def _clean_cell(value) -> str:
        """A panel cell as the email should show it: every line trimmed,
        blank lines dropped, and any run of spaces inside a line read as
        one. Sheets pad cells — trailing spaces, a leading blank line, or a
        long run of spaces used to push text onto the next visual row
        ("Sunpharma Road,<100 spaces>Pramukh Swamy Nagar,") — and none of
        that is text the email is meant to reproduce."""
        text = str(value or "")
        if text.strip().lower() == "nan":
            return ""
        lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in text.splitlines()]
        return "\n".join(ln for ln in lines if ln)

    rows: List[DealerRow] = []
    for _, r in df.iterrows():
        dealer = str(r.get(col_map.get("dealer", ""), "") or "").strip()
        region = str(r.get(col_map.get("region", ""), "") or "").strip()
        panels = _clean_cell(r.get(col_map.get("panels", ""), ""))

        extra_blocks = []
        for extra_col in extra_panel_columns:
            extra_val = _clean_cell(r.get(extra_col, ""))
            if extra_val:
                extra_blocks.append(extra_val)

        combined_panel_text = "\n".join([b for b in [panels] + extra_blocks if b])

        # Bold-required lines: dealer name (column C's own first line)
        # plus every branch/city name (first line of EVERY column block —
        # column C's second line, and every extra column's own first
        # line). Computed here, not from combined_panel_text, because the
        # per-column boundaries needed to tell "this is a block's first
        # line" apart from "this is just some other line" only exist
        # before flattening.
        bold_required_lines: List[str] = []
        panels_own_lines = [ln.strip() for ln in panels.splitlines() if ln.strip()]
        if panels_own_lines:
            bold_required_lines.append(panels_own_lines[0])  # dealer name
            if len(panels_own_lines) > 1:
                bold_required_lines.append(panels_own_lines[1])  # column C's own city/area name
        for extra_val in extra_blocks:
            extra_own_lines = [ln.strip() for ln in extra_val.splitlines() if ln.strip()]
            if extra_own_lines:
                bold_required_lines.append(extra_own_lines[0])  # that branch's city/area name

        if combined_panel_text:
            rows.append(DealerRow(
                dealer=dealer, region=region, panel_text=combined_panel_text,
                bold_required_lines=bold_required_lines,
            ))

    return rows


def read_dealer_panels_from_excel(uploaded_file) -> Tuple[List[DealerRow], str]:
    """
    Reads the 'Mailers - NSC ' sheet. Handles a title row above the real
    header row by scanning the first few rows for one containing both
    'Dealer' and 'Region'. Column mapping and the extra unlabeled panel
    columns (a dealer's additional branch/location blocks — see
    build_dealer_rows_from_df's docstring for the full reasoning) are
    handled by build_dealer_rows_from_df, which this function calls.
    """
    xl = pd.ExcelFile(uploaded_file)
    sheet_name = find_mailers_sheet(xl)
    if sheet_name is None:
        raise ValueError(
            f"This file has no 'Mailers - NSC' sheet (it has: {', '.join(xl.sheet_names)}). "
            f"Pick the sheet that holds the dealer panels in 'Excel sheet' below."
        )

    raw = xl.parse(sheet_name, header=None, dtype=str)

    header_row_idx = None
    for i in range(min(10, len(raw))):
        row_vals = [str(v).strip().lower() for v in raw.iloc[i].tolist()]
        if "dealer" in row_vals and "region" in row_vals:
            header_row_idx = i
            break

    if header_row_idx is None:
        raise ValueError(
            f"Sheet '{sheet_name}' has no heading row with 'Dealer' and 'Region' columns."
        )

    df = xl.parse(sheet_name, header=header_row_idx, dtype=str)
    rows = build_dealer_rows_from_df(df, sheet_name=sheet_name)
    return rows, sheet_name


def panel_text_to_lines(panel_text: str) -> List[str]:
    return split_lines_keep_order(panel_text)


# =========================================================
# HTML extraction (visible text)
# =========================================================

def html_to_visible_text(soup: BeautifulSoup) -> str:
    work = BeautifulSoup(str(soup), "html.parser")
    for tag in work(['script', 'style', 'noscript']):
        tag.decompose()
    for c in work.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for br in work.find_all("br"):
        br.replace_with("\n")
    text = work.get_text(separator="\n")
    text = text.replace("\xa0", " ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def html_to_visible_text_raw(soup: BeautifulSoup) -> str:
    """
    Same extraction as html_to_visible_text() (strip script/style/
    comments, turn <br> into newlines, &nbsp; into a real space) but
    WITHOUT collapsing runs of spaces/tabs down to one. html_to_visible_text
    intentionally collapses whitespace so fuzzy line-matching isn't
    tripped up by incidental formatting — but that collapsing would
    silently erase the exact thing the exact-match spacing check exists
    to find. That check must read this raw variant, not the collapsed
    one. (The double-space check uses html_to_rendered_text() below
    instead — see that function for why.)
    """
    work = BeautifulSoup(str(soup), "html.parser")
    for tag in work(['script', 'style', 'noscript']):
        tag.decompose()
    for c in work.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for br in work.find_all("br"):
        br.replace_with("\n")
    text = work.get_text(separator="\n")
    text = text.replace("\xa0", " ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_rendered_text(soup: BeautifulSoup) -> str:
    """
    Simulates what a browser actually SHOWS on screen, whitespace-wise —
    used specifically for the double-space check (run_double_space_qa),
    which must only flag a double space a reader would actually see, not
    HTML source-code formatting.

    Per standard HTML whitespace rules (the default white-space: normal,
    which this app's emails all use — no white-space: pre anywhere), a
    browser collapses ANY run of plain whitespace (spaces, tabs,
    newlines) down to exactly one rendered space, no matter how much
    indentation or how many line breaks are in the source. This is why
    an HTML file can be nicely tab-indented for readability — extra tabs
    between "BMW Bird Automotive" and its closing </td>, or inside a long
    indented paragraph — and still render as normal single-spaced text;
    those tabs are invisible to an actual reader and must be invisible to
    this check too.

    The ONE way to force a browser to render more than one consecutive
    visible space is &nbsp; (it does NOT collapse). So a genuine
    double-space authoring bug looks like "word&nbsp;&nbsp;word" or
    "word &nbsp;word" in the source — &nbsp; adjacent to a plain space or
    another &nbsp;. This function keeps &nbsp; as its own distinct
    character (U+00A0) rather than normalizing it to a plain space, so
    the caller can tell "collapsible source formatting" apart from
    "non-collapsible, actually-rendered extra space" — collapsing only
    runs of plain whitespace, never absorbing or collapsing &nbsp; itself.
    """
    work = BeautifulSoup(str(soup), "html.parser")
    for tag in work(['script', 'style', 'noscript']):
        tag.decompose()
    for c in work.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for br in work.find_all("br"):
        br.replace_with("\n")
    text = work.get_text(separator="\n")

    # Collapse runs of PLAIN whitespace (space/tab/CR/LF) to a single
    # space, but never merge across an &nbsp; (U+00A0) — split on nbsp
    # boundaries first so it's never swallowed into a collapsed run.
    NBSP = "\xa0"
    parts = re.split(f"({NBSP})", text)
    collapsed_parts = []
    for part in parts:
        if part == NBSP:
            collapsed_parts.append(part)
        else:
            collapsed_parts.append(re.sub(r"[ \t\r]+", " ", part))
    text = "".join(collapsed_parts)

    # Newlines are block/line-break boundaries (from <br>, <p>, <td>,
    # etc.) — these ARE meaningful for where one visual line ends and
    # another begins, so, unlike the space run above, they're preserved
    # (only excess blank lines are trimmed) rather than collapsed into
    # the same single-space treatment as horizontal whitespace.
    text = re.sub(r"\n[ \t]*\n[ \t]*(\n[ \t]*)+", "\n\n", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return text.strip()


def extract_body_greeting_slice(full_body_text: str, dealer_name: str) -> str:
    """
    Isolates just the greeting/body-copy portion of the email: from the
    word "Dear" up to (but not including) the sign-off line where the
    dealer name appears WITH "BMW" in front of it (e.g. body text reads
    "...regards, Bird Automotive" while the sign-off/dealer-panel further
    down reads "BMW Bird Automotive" — that "BMW <Dealer>" line marks the
    end of the body-copy section and the start of the dealer panel).

    This exists because the Dealer-in-Body check must only look at the
    actual body copy, not accidentally also match the dealer panel below
    it (which is already separately QA'd against the Excel sheet at the
    top of the report) — so a body missing the dealer's name wouldn't be
    silently marked Pass just because "BMW <Dealer>" appears further down
    in the panel.

    Falls back to the full text if "Dear" or a "BMW <dealer>" sign-off
    marker can't be located, so the check never silently breaks.
    """
    text = full_body_text or ""
    if not text.strip():
        return text

    dear_match = re.search(r"\bDear\b", text, flags=re.IGNORECASE)
    start = dear_match.start() if dear_match else 0

    end = len(text)
    if dealer_name and dealer_name.strip():
        # Look for "BMW <dealer name>" (dealer name possibly with minor
        # whitespace/punctuation differences), case-insensitive, anywhere
        # after the greeting — that marks the dealer-panel sign-off line.
        dealer_pattern = re.escape(dealer_name.strip())
        dealer_pattern = dealer_pattern.replace(r"\ ", r"\s+")
        matches = list(re.finditer(
            rf"\bBMW\s+{dealer_pattern}\b", text[start:], flags=re.IGNORECASE
        ))
        if matches:
            # Body copy may name the dealer too ("visit BMW KUN Exclusive"),
            # so the FIRST mention is not necessarily the panel. The panel
            # heading is the last mention before the panel's own Tel. /
            # Website lines.
            label = re.search(r"\b(?:Tel\.?|Website|Email)\s*:", text[start:], flags=re.IGNORECASE)
            before_panel = [m for m in matches if label is None or m.start() < label.start()]
            heading = (before_panel or matches)[-1]
            end = start + heading.start()

    return text[start:end].strip()


def _digit_sequence(text: str) -> str:
    """All digits in `text`, concatenated, in order — used to compare
    phone/landline numbers by their actual digit content regardless of
    spacing/punctuation differences (e.g. '82228 22201' vs '82228-22201')."""
    return re.sub(r"[^0-9]", "", text or "")


# A telephone label ("Tel.:", "Ph:", "Mobile", "Toll-free"), an
# international "+" prefix, or an STD-landline shape are all positive
# evidence that a line IS a phone line. Used by _is_number_bearing_line
# below to tell a phone number apart from prose that merely contains a
# long-ish number.
_PHONE_LABEL_RE = re.compile(
    r"\b(?:tel|telephone|phone|ph|mob|mobile|fax|toll[\s\-]*free|landline)\b\.?\s*:?",
    re.IGNORECASE,
)
_STD_LANDLINE_SHAPE_RE = re.compile(r"\b\d{2,5}-\d{6,8}\b")


def _is_number_bearing_line(text: str) -> bool:
    """True if this line is substantially a number (phone/landline/etc.)
    rather than prose that merely contains a digit somewhere.

    A bare "5 or more digits" test was too loose, and it made every
    address line carrying a postal code fail the exact-match check for a
    reason that had nothing to do with the email. An Indian PIN code is 6
    digits, so "Maharashtra 431006, India" and "Goa 403722, India" both
    cleared the old threshold, were classified as phone lines, and then
    went down _exact_match_comparison_target's phone path — which compares
    only the part of the line from the first digit onward. That meant the
    Excel line was reduced to "431006, India" and compared against the
    HTML's (correct) "Maharashtra 431006, India", so the row could only
    ever report "3 word(s) found vs 2 expected" no matter how perfect the
    email was. Postal codes in an address line are prose, not phone
    numbers.

    A line is now only treated as a phone/number line when there is
    positive evidence for it:
      - an explicit telephone label ("Tel.:", "Ph", "Mobile", "Fax", ...)
      - an international "+" prefix immediately before digits
      - an STD landline shape (2-5 digits, hyphen, 6-8 digits), which is
        one of the three formats config.py already recognises
      - or a line that is nothing but a number (no letters at all)
    A PIN code on its own meets none of these, and neither does an address
    that simply holds a lot of numbers: "222, 220A Whitefield Main Road,
    ... India - 560048." carries 12 digits, and an old "10 or more digits"
    rule turned exactly that kind of address into a phone number.
    """
    raw = text or ""
    digits = _digit_sequence(raw)
    if len(digits) < 5:
        return False
    if _PHONE_LABEL_RE.search(raw):
        return True
    if re.search(r"\+\s*\d", raw):
        return True
    if _STD_LANDLINE_SHAPE_RE.search(raw):
        return True
    return not re.search(r"[A-Za-z]", raw) and len(digits) >= 8


_DOMAIN_OR_EMAIL_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
    re.IGNORECASE,
)


def _extract_domain_or_email(text: str) -> Optional[str]:
    """
    Pulls out the actual domain/URL/email token from a line (e.g.
    "Website: www.BirdAuto.com" -> "www.birdauto.com",
    "Email: sales@BirdAuto.com" -> "sales@birdauto.com"), lowercased so
    two differently-cased renderings of the identical address still
    compare equal. Returns None if the line has no domain-shaped token.
    """
    m = _DOMAIN_OR_EMAIL_RE.search(text or "")
    if not m:
        return None
    domain = m.group(0).lower()
    # If there's an email local-part immediately before the domain
    # (word@domain), include it -- "sales@birdauto.com" must not be
    # treated as equal to a line containing only "birdauto.com".
    before = (text or "")[: m.start()]
    email_local_match = re.search(r"([a-z0-9._%+-]+)@$", before, re.IGNORECASE)
    if email_local_match:
        return f"{email_local_match.group(1).lower()}@{domain}"
    return domain


def _is_url_bearing_line(text: str) -> bool:
    """True if this line's primary content is a domain/URL/email address
    (Website:/Email: dealer-panel rows) rather than prose that merely
    happens to contain a word with a dot in it."""
    return _extract_domain_or_email(text) is not None


# Words that appear in nearly every dealer's panel line and therefore
# carry no discriminating power when scoring a fuzzy text match — see
# find_best_match()'s final fallback below. Deliberately narrow (panel-
# specific scaffold only): general English stopwords are NOT included
# here, since dealer/address text needs its real words (including short
# common ones) to count toward a match.
_PANEL_SCAFFOLD_WORDS = {
    "bmw", "pvt", "ltd", "tel", "website", "email",
    "com", "www", "http", "https", "mailto", "dear",
}


def find_best_match(source_lines: List[str], target: str) -> Optional[Tuple[int, str]]:
    """
    Finds the source_lines entry that best corresponds to `target`
    (an Excel dealer-panel line), used both to report Present/Missing
    and — via raw_line_text_from_html() — to locate the exact HTML text
    for the stricter case/spacing/format checks.

    Three content types get a DELIBERATELY STRICT, exact check instead of
    fuzzy matching, and — critically — these strict checks run BEFORE the
    loose substring/token-overlap fallback, not as a fallback after it:

    1. Number-bearing targets (phone/landline numbers): only an identical
       digit sequence counts as a match. Token-overlap scoring is unsafe
       here — a wrong number differing only in its last few digits still
       shares most tokens with the correct one (e.g. Excel "...22201" vs
       HTML "...22299" share 3 of 4 tokens) and would otherwise pass.

    2. URL/email-bearing targets (Website:/Email: lines): only an
       identical domain/email counts. Scaffold words ("website", "email",
       the ".com" TLD) are common enough that two completely different
       domains can still share 2+ tokens purely on that scaffolding.

    3. General text (dealer names, addresses, panel prose): NOT given a
       dedicated exact-only block, but the final fallback below still
       discounts panel-wide scaffold words ("bmw", "pvt", "ltd", ...) for
       the same reason — those words alone are not evidence of a match
       between two different dealers.

    These strict checks run BEFORE the loose "is one line a substring of
    the other" shortcut specifically because that shortcut can match a
    bare label fragment — e.g. HTML text "Tel.:" is a substring of target
    "Tel.: +91 82228 22201" — and return on that fragment alone, even
    though the target's actual number is a SEPARATE line elsewhere in the
    HTML. This happens routinely for a correctly hyperlinked Tel./Website
    value, since wrapping it in <a> splits it onto its own text node
    during extraction, separate from its label. A label substring is not
    evidence the real number/domain is present anywhere at all, so for
    these two content types the exact check must be authoritative, not a
    fallback that only runs once the (unsafe, for these shapes) loose
    check has already failed to find anything.
    """
    t = normalize_text(target)
    if not t:
        return None

    if _is_number_bearing_line(target):
        target_digits = _digit_sequence(target)
        for i, line in enumerate(source_lines):
            if _digit_sequence(line) == target_digits:
                return i, line
        return None

    if _is_url_bearing_line(target):
        target_domain = _extract_domain_or_email(target)
        for i, line in enumerate(source_lines):
            if _extract_domain_or_email(line) == target_domain:
                return i, line
        return None

    # Loose substring check: HTML line n is (or contains, or is contained
    # by) the target t. Deliberately requires the SHORTER side to cover a
    # meaningful proportion of the LONGER side, not just any substring
    # match — a bare short fragment (e.g. a page's standalone "BMW" logo
    # alt-text/heading elsewhere in the email) is trivially a substring of
    # a longer target like "BMW Bird Automotive", and returning on that
    # alone — before the real, full "BMW Bird Automotive" line elsewhere
    # in the document is ever considered — is exactly the same class of
    # false-match bug already fixed for phone/URL/scaffold-word cases
    # above: a short generic fragment must not be allowed to stand in for
    # much longer, more specific target content. The proportion floor is
    # deliberately lenient (covers genuine truncation/partial-render
    # cases, e.g. HTML "Tel." matching target "Tel. Number") while still
    # rejecting a single short word matching a multi-word target.
    best_substring_match = None
    best_substring_len = 0
    for i, line in enumerate(source_lines):
        n = normalize_text(line)
        if not n:
            continue
        if n == t:
            return i, line
        shorter, longer = (n, t) if len(n) <= len(t) else (t, n)
        if shorter in longer and len(shorter) / len(longer) >= 0.6:
            if len(shorter) > best_substring_len:
                best_substring_len = len(shorter)
                best_substring_match = (i, line)
    if best_substring_match is not None:
        return best_substring_match

    # General fallback for everything else (dealer names, addresses, panel
    # prose): proportional, stopword-discounted token overlap instead of a
    # flat absolute count. A flat "2+ shared tokens" threshold has the
    # SAME failure mode as the phone/URL cases above — dealer-panel text
    # is full of scaffold words that appear on nearly every dealer's line
    # ("bmw", "pvt", "ltd", "tel", "website", "email"), so two lines
    # belonging to COMPLETELY DIFFERENT dealers can still share 2-3 tokens
    # on scaffold words alone (e.g. "BMW Bird Automotive Pvt Ltd" vs
    # "BMW Prestige Motors Pvt Ltd" share {"bmw","pvt","ltd"} = score 3,
    # while sharing NONE of the words that actually identify the dealer).
    # Scoring here discounts those scaffold words and requires the
    # MEANINGFUL (non-scaffold) overlap to cover a majority of the
    # target's own meaningful content, so a match still requires the
    # actual distinguishing words to line up — not just the boilerplate
    # around them. This still tolerates real formatting noise (reordered
    # words, "Pvt Ltd" vs "Private Limited", extra corporate-suffix words)
    # since it's proportional rather than exact.
    target_tokens = set(re.findall(r"[a-z0-9]+", t))
    meaningful_target = target_tokens - _PANEL_SCAFFOLD_WORDS

    best_score = 0
    best = None
    for i, line in enumerate(source_lines):
        n = normalize_text(line)
        line_tokens = set(re.findall(r"[a-z0-9]+", n))
        if not line_tokens:
            continue
        overlap = target_tokens & line_tokens
        meaningful_overlap = overlap - _PANEL_SCAFFOLD_WORDS

        if not meaningful_target:
            # Target itself is entirely scaffold words (rare, e.g. a
            # standalone "Tel." label with no number) -- fall back to the
            # original absolute-count behaviour so this case never
            # becomes impossible to match.
            is_match = len(overlap) >= 2
        elif len(meaningful_target) == 1:
            # Very short distinguishing content -- require that one
            # meaningful token to actually be present.
            is_match = len(meaningful_overlap) >= 1
        else:
            is_match = (
                len(meaningful_overlap) >= 2
                and len(meaningful_overlap) / len(meaningful_target) >= 0.5
            )

        score = len(overlap)  # still used only to pick the BEST among matches, not to decide match/no-match
        if is_match and score > best_score:
            best_score = score
            best = (i, line)

    return best


@dataclass
class CheckResult:
    item: str
    status: str


# =========================================================
# Exact-match (case-sensitive, space-sensitive) dealer panel QA
# =========================================================
#
# compare_source_to_html()/find_best_match() above are deliberately fuzzy
# (lowercased, whitespace-collapsed) so they can still find a dealer
# panel line even if it moved position or has trivial formatting
# differences — that fuzziness is the right behaviour for "is this line
# present at all". It is the WRONG behaviour for verifying the dealer's
# actual name/address/contact text is correct, because it would silently
# treat "Bird automotive" as identical to "Bird Automotive" or swallow an
# extra/missing space inside the line. This second pass runs only after
# a line has already been located by the fuzzy matcher above, and checks
# that exact HTML text against the Excel cell byte-for-byte (case and
# internal spacing both included).

def raw_line_text_from_html(html_raw: str, source_line: str) -> Optional[str]:
    """
    Returns the HTML's own visible-text rendering of the line that
    corresponds to `source_line` (located the same way compare_source_to_html
    finds it), WITHOUT the lowercasing/whitespace-collapsing that
    normalize_text()/html_to_visible_text() apply for fuzzy matching —
    i.e. exactly as the email actually renders it, casing and internal
    spacing preserved. Returns None if no corresponding line could be
    located at all (already reported as "Missing" by the fuzzy content
    check, so this exact check doesn't need to duplicate that).

    Two-step approach (deliberately does NOT assume the collapsed and raw
    text split into the same number of lines at the same indices, which
    would be a fragile assumption): first locate WHICH line matches using
    the existing robust fuzzy matcher on the collapsed text (so spacing/
    casing anomalies don't prevent the line from being found at all);
    then re-locate that same content directly inside the RAW (uncollapsed)
    text via a regex built from the matched line's own words, tolerant of
    any amount of whitespace between them — this recovers the actual
    as-rendered spacing without relying on the two texts staying index-
    aligned.

    Label reconstruction (number/URL-bearing targets only): a correctly
    built dealer panel wraps the phone number / website / email VALUE in
    its own <a> tag, e.g. `Tel.: <a href="tel:...">+91 82228 22201</a>`.
    HTML text extraction treats the label ("Tel.:") and the linked value
    as separate text nodes/lines, even though they render on the same
    visual line in the email. This is correct, required HTML structure —
    not a bug — so when the target has a label prefix (text before the
    first digit or before the domain/email), this function looks at the
    line immediately preceding the matched value line; if THAT line's
    text is a case-insensitive match for the target's own label portion,
    the two are joined back together (using the target's own spacing)
    so callers doing an exact-match/format comparison are comparing
    against what the email actually, correctly renders as one line —
    not an artifact of DOM text-node boundaries.
    """
    soup = BeautifulSoup(html_raw, "html.parser")
    collapsed_lines = split_lines_keep_order(html_to_visible_text(soup))
    match = find_best_match(collapsed_lines, source_line)
    if match is None:
        return None
    match_idx, collapsed_match_line = match

    raw_text = html_to_visible_text_raw(soup)

    def _locate_raw(collapsed_line: str) -> Optional[str]:
        words = collapsed_line.split(" ")
        if not words:
            return collapsed_line
        pattern = r"\s+".join(re.escape(w) for w in words if w)
        m = re.search(pattern, raw_text)
        return m.group(0) if m else None

    raw_value = _locate_raw(collapsed_match_line)
    if raw_value is None:
        raw_value = collapsed_match_line  # fallback: shouldn't happen, see below

    if _is_number_bearing_line(source_line) or _is_url_bearing_line(source_line):
        label_prefix_match = re.match(r"^(.*?)[:\s]*([+\d].*|[a-zA-Z0-9].*)$", source_line.strip())
        target_label = ""
        if _is_number_bearing_line(source_line):
            value_start = re.search(r"[+\d]", source_line)
            target_label = source_line[: value_start.start()].strip() if value_start else ""
        else:
            domain = _extract_domain_or_email(source_line)
            if domain:
                idx = source_line.lower().find(domain.split("@")[-1])
                target_label = source_line[:idx].strip() if idx > 0 else ""

        if target_label and match_idx > 0:
            preceding_collapsed = collapsed_lines[match_idx - 1]
            if normalize_text(preceding_collapsed) == normalize_text(target_label):
                raw_label = _locate_raw(preceding_collapsed)
                if raw_label:
                    return f"{raw_label} {raw_value}"

    return raw_value


def describe_exact_mismatch(expected: str, found: str) -> Tuple[str, set]:
    """
    Explains how `found` diverges from `expected`, and classifies it.

    Returns `(note, kinds)` where `kinds` is a set drawn from
    {"spacing", "casing", "text"} — the caller uses it to decide
    severity. Spacing on its own is a warning: a stray or missing space
    bar is a real defect worth reporting, but it is a typographic one,
    and calling it a failure buried genuinely wrong copy among rows of
    whitespace nits. Casing, or text that actually differs, stays a
    failure.

    The three kinds:
      * "spacing" — the same characters, laid out differently. Covers an
        added space bar that splits one word in two, a missing one that
        merges two words into one, and a single-vs-double space between
        the same two words.
      * "casing" — the same characters and spacing, different letter case.
      * "text" — the wording itself differs; no amount of respacing or
        recasing would make the two match.
    A line can be both mis-spaced and mis-cased at once, in which case
    both notes are included and the caller fails it. (The current caller
    only invokes this when the strings are already known to differ, but
    the guard below keeps this function correct on its own terms.)
    """
    e_s, f_s = expected.strip(), found.strip()
    if e_s == f_s:
        return "No difference — strings are identical.", set()

    # Take case out of the comparison first, then ask what is left. If the
    # two strings still differ but their non-space characters match, the
    # difference IS the spacing; if the non-space characters differ too,
    # the wording itself is wrong and no amount of respacing fixes it.
    e_c, f_c = e_s.casefold(), f_s.casefold()
    e_ns, f_ns = _collapse_spaces(e_s), _collapse_spaces(f_s)
    e_nsc, f_nsc = _collapse_spaces(e_c), _collapse_spaces(f_c)

    kinds: set = set()
    notes: List[str] = []

    expected_words = e_s.split()
    found_words = f_s.split()

    if e_nsc != f_nsc:
        kinds.add("text")
        count_note = (
            f" ({len(found_words)} word(s) found vs {len(expected_words)} expected)"
            if len(found_words) != len(expected_words) else ""
        )
        notes.append(
            "Text does not match the Excel reference — the wording itself differs"
            f"{count_note}. Found: \"{f_s[:120]}\"."
        )
    else:
        # Same characters, so anything left is spacing and/or casing.
        if e_c != f_c:
            kinds.add("spacing")
            if len(expected_words) != len(found_words):
                notes.append(
                    "Spacing does not match the Excel reference — the number/position of space "
                    "bars between words/characters differs from the source line "
                    f"({len(found_words)} word(s) found vs {len(expected_words)} expected)."
                )
            else:
                notes.append(
                    "Spacing does not match the Excel reference — the number of consecutive "
                    "spaces at one or more points differs from the source line."
                )
        if e_ns != f_ns:
            kinds.add("casing")
            notes.append(
                "Casing does not match the Excel reference — one or more letters that should be "
                "uppercase are lowercase (or vice-versa)."
            )

    if not notes:
        kinds.add("text")
        notes.append("Text differs from the Excel reference.")
    return " ".join(notes), kinds


def _collapse_spaces(text: str) -> str:
    """The line with every run of whitespace removed, so two strings that
    differ ONLY in spacing compare equal."""
    return re.sub(r"\s+", "", text or "")


def diff_exact_spacing(expected: str, found: str) -> str:
    """The note on its own, for callers that do not need the
    classification."""
    return describe_exact_mismatch(expected, found)[0]


# A phone label written as a word of its own — "Tel.:", "Tel:", "Telephone",
# "Ph.", "Mobile.:", "Mob:", "Toll-free:", "Fax:". It must END at a word
# boundary: "Tel" at the start of "Telangana" is not a label, and treating
# it as one turned a Hyderabad address into a phone line.
_PHONE_FIELD_LABEL_RE = re.compile(
    r"(?<![A-Za-z])(?:tel(?:ephone)?|phone|ph|mob(?:ile)?|toll[\s\-]*free|landline|fax)\b\.?\s*:?",
    re.IGNORECASE,
)


def _phone_parts(line: str) -> Optional[Tuple[str, List[str]]]:
    """(label, numbers) for a phone line; None for anything else.

    A phone line is one that carries a phone label (see
    _PHONE_FIELD_LABEL_RE), or one that is nothing but a number. An
    address that merely holds house numbers and a PIN code is NOT one:
    "222, 220A Whitefield Main Road, ... India - 560048." and "5th Main
    Rd, 4' Block, Jayanagar, ... - 560011." used to be read as landline
    numbers purely because of how many digits they contain.

    `label` is the label exactly as written ("Tel.:", "Mobile.:"), or ""
    for a bare number. When the label sits after an address on the same
    line, only the part from the label onward is the phone. Several
    numbers after one label ("Tel.: +91 82422 75777, +91 82422 75999")
    come back as separate numbers.
    """
    text = (line or "").strip()
    m = _PHONE_FIELD_LABEL_RE.search(text)
    if m:
        value = text[m.end():]
        numbers = [n.strip() for n in re.split(r",|;|/|\bor\b", value)
                   if len(_digit_sequence(n)) >= 6]
        return (m.group(0).strip(), numbers) if numbers else None
    if text and not re.search(r"[A-Za-z]", text) and len(_digit_sequence(text)) >= 8:
        return "", [text]
    return None


def _number_kind(number: str, labelled: bool) -> Optional[str]:
    digits = _digit_sequence(number)
    if labelled and digits.startswith("91") and len(digits) == 12:
        return "mobile_intl"
    if labelled and digits.startswith("1800") and len(digits) == 11:
        return "tollfree"
    if not labelled and 8 <= len(digits) <= 13:
        return "std_landline"
    # A labelled number that is neither +91 mobile nor 1800 toll-free is
    # still a phone line, just not one of the accepted shapes — reported
    # as a Warn rather than skipped, so it is never invisible.
    return "unclassified" if labelled else None


def classify_expected_phone_format(excel_line: str, config) -> Optional[str]:
    """
    Determines which of the three accepted phone formats applies to this
    line, based on the digit shape of the NUMBER ITSELF as given in the
    Excel cell (Excel is the source of truth for what the number is — the
    format is then a rendering rule on top of that). Returns one of
    "mobile_intl" / "tollfree" / "std_landline" / "unclassified", or None
    if this line isn't a phone/landline line at all (no format check
    applies).
    """
    parts = _phone_parts(excel_line)
    if parts is None:
        return None
    label, numbers = parts
    return _number_kind(numbers[0], bool(label))


def _number_regex(config_pattern: str) -> str:
    """The number part of one of config.py's whole-line phone patterns
    ("^Tel\\.:\\s\\+91\\s\\d{5}\\s\\d{5}$" -> "\\+91\\s\\d{5}\\s\\d{5}"), so the
    same grouping rule can follow whatever label the sheet uses."""
    p = config_pattern.lstrip("^").rstrip("$")
    return re.sub(r"^Tel\\\.:\\s", "", p)


def check_phone_number_format(excel_line: str, html_found_line: str, config) -> Optional[dict]:
    """
    Verifies the phone/landline number as it actually appears in the HTML
    matches the one-of-three accepted formats:
        Format 1: "Tel.: +91 82228 22201"
        Format 2: "Tel.: 1800 103 2211"
        Format 3: "040-27676946"
    The expected format is determined from the Excel line's own digit
    shape (see classify_expected_phone_format) — the check then verifies
    the HTML rendering matches THAT specific format's pattern, so a
    number in the wrong format (extra/missing spaces, wrong punctuation,
    missing "Tel.:" prefix, hyphens instead of spaces, etc.) fails even
    when the digits themselves are correct. Returns None if this line
    isn't a phone/landline line (no format check applies to it).
    """
    parts = _phone_parts(excel_line)
    if parts is None:
        return None
    label, numbers = parts
    fmt = _number_kind(numbers[0], bool(label))
    if fmt is None:
        return None

    found = html_found_line.strip()
    # Only the phone part of the email's line is held to the format: an
    # address that shares the line ("... Karnataka, India Tel.: +91 ...")
    # is checked by the content row above, not here.
    if label:
        fm = _PHONE_FIELD_LABEL_RE.search(found)
        if fm:
            found = found[fm.start():].strip()

    if fmt == "unclassified":
        return {
            "item": f"Phone number format: {excel_line}"[:120],
            "status": "Warn",
            "detail": (
                f"'{found}' has a '{label}' label but its number is neither a +91 mobile "
                f"number (10 digits) nor a 1800 toll-free number (7 digits after 1800), "
                f"so it could not be checked against the accepted formats. Check it by eye."
            ),
        }

    number_patterns = {
        "mobile_intl": (_number_regex(config.phone_format_mobile_intl), "+91 XXXXX XXXXX"),
        "tollfree": (_number_regex(config.phone_format_tollfree), "1800 XXX XXXX"),
        "std_landline": (_number_regex(config.phone_format_std_landline),
                         "XXX-XXXXXXX (STD code-hyphen-number, no spaces)"),
    }
    number_re, number_example = number_patterns[fmt]
    if label:
        # A telephone line is always written "Tel.:" (config.py's accepted
        # formats); any other label ("Mobile.:") is held to the sheet's own.
        # Every further number on the line follows a comma and may be
        # mobile or toll-free.
        shown_label = "Tel.:" if re.match(r"tel", label, re.IGNORECASE) else label
        any_number = "|".join(f"(?:{_number_regex(p)})" for p in (
            config.phone_format_mobile_intl, config.phone_format_tollfree))
        pattern = rf"^{re.escape(shown_label)}\s{number_re}(?:,\s(?:{any_number}))*$"
        example = f"{shown_label} {number_example}"
    else:
        pattern = rf"^{number_re}$"
        example = number_example

    if re.match(pattern, found):
        return {
            "item": f"Phone number format: {excel_line}"[:120],
            "status": "Pass",
            "detail": f"'{found}' matches the required format ({example}).",
        }

    return {
        "item": f"Phone number format: {excel_line}"[:120],
        "status": "Fail",
        "detail": f"'{found}' does not match the required format — expected pattern like '{example}'.",
    }


def check_tel_href_matches_number(excel_line: str, html_raw: str, config) -> Optional[dict]:
    """
    Cross-checks that a `tel:` link's ACTUAL href digits match the
    Excel-sourced number for this line — catches the case where the
    visible text on the page is correct but the underlying link is wired
    to a different number (a real authoring mistake that a pure visible-
    text comparison can't see, since the href is invisible on the
    rendered page). Only runs for lines classified as phone/landline
    lines; returns None otherwise.
    """
    parts = _phone_parts(excel_line)
    if parts is None or _number_kind(parts[1][0], bool(parts[0])) is None:
        return None
    numbers = parts[1]

    soup = BeautifulSoup(html_raw, "html.parser")
    tel_links = [a for a in soup.find_all("a") if (a.get("href") or "").lower().startswith("tel:")]

    def _link_for(expected_digits: str):
        for a in tel_links:
            href_digits = _digit_sequence(a.get("href") or "")
            # A tel: href may or may not include the country code; accept a
            # match either on the full digit string or on the local number
            # (last 10 digits), so a correctly-linked number in either style
            # isn't flagged just because of a country-code convention choice.
            if href_digits == expected_digits or (
                len(expected_digits) >= 10 and href_digits.endswith(expected_digits[-10:]) and len(href_digits) >= 10
            ):
                return a
        return None

    # Every number on the line needs its own link — each one is a separate
    # thing a reader can tap.
    found_links = [(_n, _link_for(_digit_sequence(_n))) for _n in numbers]
    unlinked = [_n for _n, _a in found_links if _a is None]

    if not unlinked:
        hrefs = ", ".join(f'href="{_a.get("href")}"' for _, _a in found_links)
        return {
            "item": f"Tel. link target: {excel_line}"[:120],
            "status": "Pass",
            "detail": f"A tel: link with matching digits was found ({hrefs}).",
        }

    if tel_links:
        hrefs = ", ".join(f'"{a.get("href")}"' for a in tel_links[:5])
        missing = ", ".join(unlinked)
        return {
            "item": f"Tel. link target: {excel_line}"[:120],
            "status": "Fail",
            "detail": f"No tel: link matches {missing}. Tel. link(s) found in the email: {hrefs}.",
        }

    return {
        "item": f"Tel. link target: {excel_line}"[:120],
        "status": "Fail",
        "detail": "No tel: link found anywhere in the HTML to check against the Excel number.",
    }


def _exact_match_comparison_target(source_line: str, found: str, config) -> str:
    """
    Returns the portion of `source_line` that should be compared
    byte-for-byte against `found`, for the generic exact-match (casing/
    spacing) row. For an ordinary line this is the whole source_line, as
    before. For a number-bearing or URL/email-bearing line, a correctly
    built HTML normally puts the label ("Tel.:", "Website:") and the
    value in SEPARATE DOM text nodes — this happens precisely because the
    value is (correctly) wrapped in its own <a> tag, and is not a spacing
    bug. In that case `found` legitimately contains only the value, not
    the label, so the fair comparison target is the source_line's own
    value portion (its digit sequence rendered as-is, or its domain/
    email), not the full "Tel.: +91 82228 22201" string. The dedicated
    check_phone_number_format() / check_tel_href_matches_number() checks
    already validate the label+format and link-target correctness
    separately, so this function only needs to isolate the value for the
    plain text-identity comparison.
    """
    if _is_number_bearing_line(source_line):
        # If `found` itself still contains the label (a non-hyperlinked
        # or differently-structured number line, where label+value share
        # one text node), compare the full line as normal.
        if _digit_sequence(found) and re.search(r"[a-zA-Z]", found) and ":" in found:
            return source_line
        # Otherwise `found` is value-only (the normal, correct hyperlinked
        # case) -- compare against just the source line's own value
        # portion, i.e. everything from the first digit onward.
        value_match = re.search(r"[+\d].*$", source_line.strip())
        return value_match.group(0) if value_match else source_line

    if _is_url_bearing_line(source_line):
        found_domain = _extract_domain_or_email(found)
        source_domain = _extract_domain_or_email(source_line)
        # If `found` contains ONLY the domain/email (no label text before
        # it), compare domain-to-domain; otherwise compare the full line.
        found_stripped = found.strip()
        if found_domain and found_stripped.lower() == found_domain:
            return source_domain or source_line

    return source_line


def _line_is_verbatim_in_html(source_line: str, html_raw: str) -> bool:
    """
    True when `source_line` appears in the email's visible text
    character-for-character — same words, same internal spacing, same
    casing — even if the email breaks the line somewhere else than the
    Excel cell does.

    Exists for the dealer-panel exact-match rule. Excel stores a panel as
    fixed lines; the email is free to wrap the same address at a different
    point, so a strict line-vs-line comparison reports a "spacing" failure
    ("8 word(s) found vs 5 expected") for text that is in fact perfect.
    Line breaks are flattened to a single space here, then the comparison
    stays byte-exact — so a real double space, a missing space, or a
    casing error still cannot slip through, because any of those would
    break the verbatim match.
    """
    target = " ".join((source_line or "").split(" ")).strip()
    if not target:
        return False
    soup = BeautifulSoup(html_raw, "html.parser")
    raw_text = html_to_visible_text_raw(soup)
    # Only newlines/tabs are normalised — runs of spaces are preserved so
    # that a genuine spacing defect still fails.
    flattened = re.sub(r"[\r\n\t]+", " ", raw_text)
    return target in flattened


def _exact_match_verdict(line: str, found: str, html_raw: str, config) -> Tuple[str, str]:
    """
    Verifies the HTML's actual rendered text for one dealer-panel line is
    identical to the Excel cell's line — casing and internal spacing both
    included (see _exact_match_comparison_target for how the comparison
    target is chosen for number/URL-bearing lines, where the label and
    value normally live in separate DOM nodes). Returns (status, detail).
    """
    compare_target = _exact_match_comparison_target(line, found, config)
    exact_ok = found.strip() == compare_target.strip()
    if exact_ok:
        status, detail = "Pass", "Found in the email; casing and spacing match the sheet exactly."
    elif _line_is_verbatim_in_html(compare_target, html_raw):
        # The characters are identical — the email simply breaks the
        # address at a different point than the Excel cell does, so the
        # matched HTML line carries one Excel line plus part of the
        # next (or vice-versa). That is a layout choice, not the
        # spacing/casing defect this rule exists to catch, and
        # reporting it as a Fail buried the real failures in noise.
        # Downgraded to a Warn (not silently passed) so a genuinely
        # unintended re-wrap is still visible.
        status = "Warn"
        detail = (
            "Text matches the Excel reference exactly, but the email wraps the line at a "
            "different point than the Excel cell does — the matched HTML line is "
            f"\"{found.strip()[:120]}\". Casing and spacing themselves are correct."
        )
    else:
        detail, kinds = describe_exact_mismatch(compare_target, found)
        # Spacing on its own is a typographic defect, not wrong copy:
        # still reported, but as a warning. Anything that changes the
        # words themselves or their casing is still a failure.
        status = "Warn" if kinds == {"spacing"} else "Fail"
    return status, detail


def build_content_qa(panel_lines: List[str], html_raw: str, config) -> pd.DataFrame:
    """
    Content QA: ONE row per line of the dealer's Excel panel.

    Presence and exact match used to be two separate tables that listed
    every line twice — once as Present/Missing, once as an exact-match
    Pass/Warn/Fail. They are one question ("is this line in the email,
    exactly as the sheet has it?"), so each line now gets one verdict:

      * Fail — the line is not in the email at all.
      * Pass — found, with casing and spacing exactly as in the sheet.
      * Warn — found, but only the spacing differs, or the email breaks
        the line at a different point than the sheet does.
      * Fail — found, but the wording or casing differs.

    A phone line is followed by its own phone-format row and its tel:
    link row (check_phone_number_format / check_tel_href_matches_number),
    since all three need the same located HTML line. Returns columns
    item, status, detail.
    """
    presence = compare_source_to_html(panel_lines, html_raw)
    rows = []
    for _, pr in presence.iterrows():
        line = pr["item"]
        if pr["status"] != "Present":
            rows.append({"item": line, "status": "Fail", "detail": "Not found in the email."})
            continue
        found = raw_line_text_from_html(html_raw, line) if config.dealer_panel_exact_match else None
        if found is None:
            # Located only by joining several email lines (an address the
            # email sets across more lines than the sheet), so there is no
            # single rendered line to compare character by character.
            rows.append({"item": line, "status": "Pass", "detail": "Found in the email."})
            continue
        status, detail = _exact_match_verdict(line, found, html_raw, config)
        rows.append({"item": line, "status": status, "detail": detail})

        phone_status = check_phone_number_format(line, found, config)
        if phone_status is not None:
            rows.append(phone_status)

        href_status = check_tel_href_matches_number(line, html_raw, config)
        if href_status is not None:
            rows.append(href_status)

    if rows:
        return pd.DataFrame(rows)
    return pd.DataFrame(columns=["item", "status", "detail"])


# =========================================================
# Dealer auto-detection from HTML
# =========================================================

def panel_presence_ratio(row: "DealerRow", visible_norm: str) -> float:
    """How much of a dealer row's own panel actually appears in the email.

    Used to tell one BRANCH of a dealer from another. Returns the fraction
    of the row's panel lines found in the email's visible text, so a row
    with a long panel is not favoured over a short one just for being long.
    """
    lines = [ln for ln in panel_text_to_lines(row.panel_text) if ln.strip()]
    if not lines:
        return 0.0
    hits = 0
    for line in lines:
        needle = normalize_text(line)
        if needle and needle in visible_norm:
            hits += 1
    return hits / len(lines)


def detect_dealer_from_html(html_raw: str, dealer_rows: List[DealerRow]) -> Optional[DealerRow]:
    """
    Finds which dealer (from the Excel rows) this HTML email belongs to,
    by looking for a 'BMW <Dealer Name>' heading in the HTML and matching
    it against each row's Dealer column / first panel line.

    ONE DEALER, SEVERAL BRANCHES
    ----------------------------
    A dealer name does not identify a row on its own. The sheet carries
    three rows called "Deutsche Motoren" — Delhi, Bengaluru and Kozhikode —
    and every one of them matches the "BMW Deutsche Motoren" heading in
    every one of their emails equally well. Taking the first meant a
    Bengaluru mailer was compared against the Delhi panel, and Content QA
    reported all nineteen of Delhi's address lines missing from an email
    that was entirely correct.

    So when the name matches several rows, the PANEL decides: whichever
    row's own address block is actually present in this email is the row
    this email was built from. Where the panels cannot tell them apart
    either, the first still wins, exactly as before.
    """
    soup = BeautifulSoup(html_raw, "html.parser")
    visible = html_to_visible_text(soup)
    visible_norm = normalize_text(visible)

    best_len = 0
    matched: List[DealerRow] = []
    for row in dealer_rows:
        dealer_name = row.dealer.strip()
        if not dealer_name:
            continue
        candidates = {dealer_name, f"BMW {dealer_name}"}
        panel_lines = panel_text_to_lines(row.panel_text)
        if panel_lines:
            candidates.add(panel_lines[0])

        row_len = 0
        for cand in candidates:
            cn = normalize_text(cand)
            if cn and cn in visible_norm:
                row_len = max(row_len, len(cn))

        if not row_len:
            continue
        if row_len > best_len:
            best_len, matched = row_len, [row]
        elif row_len == best_len:
            matched.append(row)

    if not matched:
        return None
    if len(matched) == 1:
        return matched[0]
    # `max` keeps the first row on a tie, so a set of branches whose panels
    # are all equally (un)present resolves exactly as it used to.
    return max(matched, key=lambda r: panel_presence_ratio(r, visible_norm))


# =========================================================
# Style / linking QA — scoped to the dealer panel block plus
# the module immediately above it (not disclaimer/social).
# =========================================================

@dataclass
class StyleIssue:
    rule: str
    severity: str
    detail: str


def get_bg_mode(container: Tag) -> str:
    node = container
    while node is not None and isinstance(node, Tag):
        style = (node.get("style") or "").lower()
        bgcolor = (node.get("bgcolor") or "").lower()
        combined = style + " " + bgcolor
        m = re.search(r"background-color:\s*#?([0-9a-f]{3,6})", combined)
        hexval = None
        if m:
            hexval = m.group(1)
        elif bgcolor:
            hexval = bgcolor.lstrip("#")
        if hexval:
            hexval = hexval.strip()
            if hexval in ("000", "000000"):
                return "black"
            if hexval in ("fff", "ffffff"):
                return "white"
        node = node.parent
    return "unknown"


def locate_dealer_panel_container(soup: BeautifulSoup) -> Optional[Tag]:
    """
    Finds the panel content scoped to the dealer name heading through the
    Website/Email row that follows it -- just the <tr> rows that make up
    the dealer panel itself, not the whole enclosing wrapper table (which
    may also contain unrelated sibling rows like a "FOLLOW US" social
    block or a disclaimer further down).
    """
    candidates = soup.find_all(string=re.compile(r"Tel\.?:|Website:", re.I))
    if not candidates:
        return None

    rows: List[Tag] = []
    seen_ids = set()
    for c in candidates:
        node = c.parent
        while node is not None and node.name != "tr":
            node = node.parent
        if node is not None and id(node) not in seen_ids:
            rows.append(node)
            seen_ids.add(id(node))

    if not rows:
        return None

    parent_table = rows[0]
    while parent_table is not None and parent_table.name != "table":
        parent_table = parent_table.parent
    if parent_table is None:
        return rows[0]

    all_trs = parent_table.find_all("tr", recursive=True)
    top_level_trs = [tr for tr in all_trs if tr.find_parent("table") is parent_table]

    row_indices = [top_level_trs.index(r) for r in rows if r in top_level_trs]
    if not row_indices:
        return rows[0]

    start_idx = min(row_indices)
    end_idx = max(row_indices)
    selected_trs = top_level_trs[start_idx:end_idx + 1]

    wrapper = BeautifulSoup("<table><tbody></tbody></table>", "html.parser")
    tbody = wrapper.tbody
    for tr in selected_trs:
        tbody.append(BeautifulSoup(str(tr), "html.parser"))
    return wrapper.table


def locate_scope_container(soup: BeautifulSoup) -> Optional[Tag]:
    """
    Scope for style QA = the dealer panel rows themselves (dealer-name
    heading through the last Tel./Website/Email row). This already
    excludes footer disclaimers and social-icon blocks that sit in
    sibling rows of the same wrapper table.
    """
    return locate_dealer_panel_container(soup)


def extract_px(value: str) -> Optional[float]:
    m = re.search(r"([\d.]+)\s*px", value)
    if m:
        return float(m.group(1))
    return None


def run_style_qa(html_raw: str) -> List[StyleIssue]:
    issues: List[StyleIssue] = []
    soup = BeautifulSoup(html_raw, "html.parser")

    panel = locate_dealer_panel_container(soup)
    if panel is None:
        issues.append(StyleIssue(
            "Dealer panel block", "Warn",
            "Could not locate a dealer panel block (no 'Tel.:' / 'Website:' text found) to run styling QA."
        ))
        return issues

    scope = locate_scope_container(soup) or panel

    # Background mode must be read from a node still attached to the
    # original document (the cloned `panel`/`scope` wrapper has no
    # ancestors), so find the original in-document Tel./Website row first.
    orig_candidates = soup.find_all(string=re.compile(r"Tel\.?:|Website:", re.I))
    bg_source = orig_candidates[0].parent if orig_candidates else panel
    panel_bg_mode = get_bg_mode(bg_source)
    expected_text_color = "#ffffff" if panel_bg_mode == "black" else "#666666"
    expected_link_color = "#ffffff" if panel_bg_mode == "black" else "#1c69d4"

    issues.append(StyleIssue(
        "Background detected", "Pass" if panel_bg_mode != "unknown" else "Warn",
        f"Dealer panel background detected as: {panel_bg_mode}."
    ))

    # ---- Rule: panel/scope text color matches background mode ----
    color_issue = False
    for td in scope.find_all(["td", "span", "font"]):
        style = (td.get("style") or "").lower()
        color_m = re.search(r"(?<!background-)color:\s*#?([0-9a-f]{3,6})", style)
        if not color_m:
            continue
        if td.find("a"):
            continue  # links checked separately below
        color = "#" + color_m.group(1)
        if color.lower() != expected_text_color:
            snippet = td.get_text(" ", strip=True)[:40]
            issues.append(StyleIssue(
                "Panel text color", "Fail",
                f"Text '{snippet}' has color {color}, expected {expected_text_color} on {panel_bg_mode} background."
            ))
            color_issue = True
    if not color_issue:
        issues.append(StyleIssue("Panel text color", "Pass", f"Panel text color correctly set to {expected_text_color}."))

    # ---- Rule: Tel / Website / Email should be hyperlinked ----
    panel_text_all = panel.get_text(" ", strip=True)
    for label, pattern in [
        ("Tel.", re.compile(r"tel\.?:?", re.I)),
        ("Website", re.compile(r"website:?", re.I)),
        ("Email", re.compile(r"email:?", re.I)),
    ]:
        if pattern.search(panel_text_all):
            label_nodes = panel.find_all(string=pattern)
            linked = False
            for ln in label_nodes:
                row = ln.parent
                hop = 0
                while row is not None and hop < 6:
                    a = row.find("a") if isinstance(row, Tag) else None
                    if a and a.get("href"):
                        href = a.get("href", "")
                        if label == "Tel." and href.lower().startswith("tel:"):
                            linked = True
                        elif label == "Website" and (href.lower().startswith("http") or href.lower().startswith("www")):
                            linked = True
                        elif label == "Email" and href.lower().startswith("mailto:"):
                            linked = True
                    row = row.parent
                    hop += 1
            if linked:
                issues.append(StyleIssue(f"{label} hyperlink", "Pass", f"{label} appears correctly hyperlinked."))
            else:
                issues.append(StyleIssue(f"{label} hyperlink", "Fail", f"{label} was found in the panel but no matching <a href> link (tel:/http/mailto:) was detected."))

    # ---- Rule: Tel/Website/Email link color ----
    for a in panel.find_all("a"):
        href = (a.get("href") or "").lower()
        style = (a.get("style") or "").lower()
        color_m = re.search(r"color:\s*#?([0-9a-f]{3,6})", style)
        color = ("#" + color_m.group(1)) if color_m else None
        if href.startswith(("tel:", "mailto:")) or "http" in href:
            if color and color.lower() != expected_link_color:
                issues.append(StyleIssue(
                    f"Link color ({panel_bg_mode} bg)", "Fail",
                    f"Link '{a.get_text(strip=True)}' has color {color}, expected {expected_link_color}."
                ))

    # ---- Rule: dealer panel font size 14px <= size < 17px ----
    font_size_issue = False
    for td in scope.find_all(["td", "span", "font"]):
        style = (td.get("style") or "")
        if "font-size" not in style.lower():
            continue
        size = extract_px(style)
        if size is None:
            continue
        problems = []
        if size < 14:
            problems.append(f"{size}px is below the 14px minimum")
        if size >= 17:
            problems.append(f"{size}px is at/above the 17px ceiling")
        if problems:
            snippet = td.get_text(" ", strip=True)[:40]
            issues.append(StyleIssue(
                "Dealer panel font size", "Fail",
                f"Text '{snippet}' — " + "; ".join(problems)
            ))
            font_size_issue = True
    if not font_size_issue:
        issues.append(StyleIssue("Dealer panel font size", "Pass", "All detected dealer-panel font sizes are within 14px–16.99px."))

    # ---- Rule: bold text uses bmwtypenextbold, light text uses bmwtypenextlight ----
    bold_font_issue = False
    bold_checked_snippets = []
    for tag in scope.find_all(["strong", "b"]):
        style = (tag.get("style") or "").lower()
        snippet = " ".join(tag.get_text(" ", strip=True).split())[:40]
        bold_checked_snippets.append(snippet)
        if "bmwtypenextbold" not in style:
            bold_font_issue = True
            issues.append(StyleIssue(
                f"Bold font-family: '{snippet}'", "Fail",
                f"Bold text '{snippet}' does not use the {ext_font_label('bmwtypenextbold')} font."
            ))
    if not bold_font_issue:
        if bold_checked_snippets:
            checked_label = ", ".join(f"'{s}'" for s in bold_checked_snippets[:6])
            if len(bold_checked_snippets) > 6:
                checked_label += f", and {len(bold_checked_snippets) - 6} more"
            issues.append(StyleIssue(
                f"Bold font-family: {checked_label}", "Pass",
                f"All bold (<strong>/<b>) text uses the {ext_font_label('bmwtypenextbold')} font. Checked: {checked_label}."
            ))
        else:
            issues.append(StyleIssue("Bold font-family", "Pass", "No bold (<strong>/<b>) text found in scope to check."))

    light_font_issue = False
    light_checked_snippets = []
    for td in scope.find_all("td"):
        style = (td.get("style") or "").lower()
        if "font-weight: 300" in style or "font-weight:300" in style:
            snippet = " ".join(td.get_text(" ", strip=True).split())[:40]
            light_checked_snippets.append(snippet)
            if "bmwtypenextlight" not in style:
                light_font_issue = True
                issues.append(StyleIssue(
                    f"Light font-family: '{snippet}'", "Fail",
                    f"Light-weight text '{snippet}' does not use the {ext_font_label('bmwtypenextlight')} font."
                ))
    if not light_font_issue:
        if light_checked_snippets:
            checked_label = ", ".join(f"'{s}'" for s in light_checked_snippets[:6])
            if len(light_checked_snippets) > 6:
                checked_label += f", and {len(light_checked_snippets) - 6} more"
            issues.append(StyleIssue(
                f"Light font-family: {checked_label}", "Pass",
                f"All light-weight (font-weight:300) text uses the {ext_font_label('bmwtypenextlight')} font. Checked: {checked_label}."
            ))
        else:
            issues.append(StyleIssue("Light font-family", "Pass", "No light-weight (font-weight:300) text found in scope to check."))

    # ---- Rule: "Follow Us" footer text should use bmwtypenextregular ----
    # Only some email templates render this footer line with the expected
    # font declared explicitly — when it's missing, this is a WARN, never
    # a Fail (per spec), since not every template is required to set it.
    # Searches the WHOLE document (not `scope`), since "Follow Us" sits in
    # the footer/social-icon block, outside the dealer panel itself.
    follow_us_nodes = soup.find_all(string=re.compile(r"follow\s+us", re.I))
    if follow_us_nodes:
        follow_us_font_issue = False
        for node in follow_us_nodes:
            style_node = node.parent
            hop = 0
            found_style = ""
            while style_node is not None and isinstance(style_node, Tag) and hop < 6:
                s = (style_node.get("style") or "")
                if s:
                    found_style = s.lower()
                    break
                style_node = style_node.parent
                hop += 1
            if EXT_CFG.follow_us_expected_font not in found_style:
                follow_us_font_issue = True
        if follow_us_font_issue:
            issues.append(StyleIssue(
                "Follow Us font", "Warn",
                f"'Follow Us' text found but does not use the {ext_font_label(EXT_CFG.follow_us_expected_font)} font. "
                f"This is only a warning — not every template is required to set this."
            ))
        else:
            issues.append(StyleIssue("Follow Us font", "Pass", f"'Follow Us' text correctly uses the {ext_font_label(EXT_CFG.follow_us_expected_font)} font."))
    # If "Follow Us" text isn't present at all in this email, no row is
    # added — the check simply doesn't apply to this template.

    # ---- Rule: buttons should have border-radius ----
    # A real CTA button (e.g. "Book a test drive") is an <a> that wraps a
    # nested <table> forming the button shape. Plain links sitting inside a
    # colored <td> — phone numbers, website URLs, social icons — do NOT
    # wrap a table and must be excluded, otherwise every colored link in
    # the email gets mistakenly flagged as a "button".
    button_found = False
    radius_re = re.compile(r"border-radius\s*:\s*(\d+(?:\.\d+)?)\s*px", re.IGNORECASE)
    bg_color_re = re.compile(r"background-color:\s*#?([0-9a-f]{3,6})", re.IGNORECASE)
    expected_cta_bg = EXT_CFG.cta_button_expected_bg.lstrip("#").lower()
    for a in soup.find_all("a"):
        href = a.get("href") or ""

        # Must wrap a nested <table> — this is what makes it a CTA button
        # rather than a plain text/icon link.
        inner_table = a.find("table")
        if inner_table is None:
            continue

        ancestor_tds = a.find_parents("td")
        if not ancestor_tds:
            continue

        cta_td = None
        for td in ancestor_tds:
            bgcolor = (td.get("bgcolor") or "").strip()
            style = (td.get("style") or "").lower()
            if bgcolor or "background-color" in style:
                cta_td = td
                break

        if cta_td is None:
            continue

        button_found = True
        style = (cta_td.get("style") or "").lower()
        a_style = (a.get("style") or "").lower()
        inner_td_style = " ".join((td.get("style") or "").lower() for td in inner_table.find_all("td"))

        combined_style = style + " " + a_style + " " + inner_td_style
        match = radius_re.search(combined_style)
        has_radius = match is not None
        radius_ok = has_radius and float(match.group(1)) >= 3

        label = a.get_text(strip=True)[:30] or href
        if radius_ok:
            issues.append(StyleIssue("Button border-radius", "Pass", f"Button '{label}' has rounded corners ({match.group(1)}px)."))
        elif has_radius:
            issues.append(StyleIssue("Button border-radius", "Fail", f"Button '{label}' has border-radius of {match.group(1)}px — should be at least 3px."))
        else:
            issues.append(StyleIssue("Button border-radius", "Warn", f"Button '{label}' has no border-radius set — should have rounded corners (min 3px)."))

        # ---- Rule: CTA button background color must match config.cta_button_expected_bg ----
        cta_bgcolor_attr = (cta_td.get("bgcolor") or "").strip().lstrip("#").lower()
        style_bg_match = bg_color_re.search(style)
        actual_bg = (style_bg_match.group(1).lower() if style_bg_match else None) or cta_bgcolor_attr or None
        if actual_bg is None:
            issues.append(StyleIssue(
                "CTA button background color", "Fail",
                f"Button '{label}' has no background color set — expected {EXT_CFG.cta_button_expected_bg.upper()}."
            ))
        elif actual_bg == expected_cta_bg:
            issues.append(StyleIssue(
                "CTA button background color", "Pass",
                f"Button '{label}' background color correctly set to {EXT_CFG.cta_button_expected_bg.upper()}."
            ))
        else:
            issues.append(StyleIssue(
                "CTA button background color", "Fail",
                f"Button '{label}' has background color #{actual_bg.upper()} — expected {EXT_CFG.cta_button_expected_bg.upper()}."
            ))
    if not button_found:
        issues.append(StyleIssue("Button border-radius", "Warn", "No CTA button was found in the email, so its corners could not be checked."))
        issues.append(StyleIssue("CTA button background color", "Warn", "No CTA button was found in the email, so its colour could not be checked."))

    return issues


# =========================================================
# Image size QA
# =========================================================

def run_double_space_qa(html_raw: str, config) -> List[StyleIssue]:
    """
    Flags a run of 2+ consecutive RENDERED space characters anywhere in
    the email — i.e. what a reader would actually see as a double space
    on the page, not HTML source-code formatting. Reads
    html_to_rendered_text() (see that function's docstring for the full
    reasoning), which simulates real browser whitespace-collapsing: any
    run of plain whitespace (spaces/tabs/newlines used for HTML source
    indentation) collapses to one rendered space exactly like a browser
    does, so a developer's tab-indented markup never trips this check.
    Only &nbsp; forces a genuinely non-collapsing rendered space, so a
    real double-space bug is &nbsp; sitting next to another &nbsp; or a
    plain space — that combination IS still detected. Scans the WHOLE
    email, not just the dealer panel scope, since this can happen in any
    section.
    """
    issues: List[StyleIssue] = []
    soup = BeautifulSoup(html_raw, "html.parser")
    rendered = html_to_rendered_text(soup)

    min_run = config.double_space_min_run
    # Match a run of 2+ characters that are EACH a rendered space -- a
    # plain space or an nbsp, in any combination -- since both display as
    # visible whitespace and a reader can't tell them apart on the page.
    pattern = re.compile(r"[ \xa0]{" + str(min_run) + r",}")

    found_runs = []
    for line in rendered.splitlines():
        for m in pattern.finditer(line):
            snippet_start = max(0, m.start() - 20)
            snippet_end = min(len(line), m.end() + 20)
            context = line[snippet_start:snippet_end].strip()
            found_runs.append((len(m.group()), context))

    if found_runs:
        for run_len, context in found_runs[:15]:
            issues.append(StyleIssue(
                "Double space", "Fail",
                f"Found a {run_len}-space run — double space bars should not be present. Context: '...{context}...'",
            ))
        if len(found_runs) > 15:
            issues.append(StyleIssue(
                "Double space", "Fail",
                f"...and {len(found_runs) - 15} more double-space occurrence(s) not shown.",
            ))
    else:
        issues.append(StyleIssue("Double space", "Pass", "No double space bars found anywhere in the email's rendered text."))

    return issues


def _looks_like_domain_or_email_token(word: str) -> bool:
    """
    True if `word` (a single whitespace-delimited token) is, AS A WHOLE,
    shaped like a domain, URL, or email address — used to exclude these
    from the punctuation-spacing check below, since a domain/URL/email
    legitimately has dots with no surrounding space (e.g.
    "www.bmw-birdautomotive.in", "info@bmw-krishnaautomotive.in").

    This is NOT the same test as _extract_domain_or_email() used
    elsewhere in this file (that one finds a domain-shaped SPAN inside a
    larger string, for phone/website line matching) — this one tests
    whether the ENTIRE token is domain-shaped, which is the safer test
    here: a run-on sentence like "sentence.Next" is superficially
    word.Word-shaped too, so the two things this function checks for are
    what make the difference:
      - A capital letter immediately after a dot is treated as a
        sentence boundary, not a domain — real domains don't do this
        (a genuine run-on like "sentence.Next" is excluded from being
        treated as a domain by this rule alone).
      - The final dot-separated segment must be letters-only and at
        least 2 characters (a TLD shape: "in", "com" — NOT a number,
        which is what correctly distinguishes a domain from something
        like "Rs.500").
    """
    w = re.sub(r"^https?://", "", word)
    w = w.split("/")[0]
    if not w:
        return False
    if re.search(r"\.[A-Z]", w):
        return False
    parts = w.rstrip(".").split(".")
    if len(parts) < 2:
        return False
    last = parts[-1]
    if not (last.isalpha() and len(last) >= 2):
        return False
    host = w.split("@")[-1]
    host_parts = host.rstrip(".").split(".")
    return all(re.match(r"^[a-zA-Z0-9-]+$", seg) for seg in host_parts)


def _looks_like_numeric_identifier_token(word: str) -> bool:
    """
    True if `word`, once trailing punctuation is stripped, is made up
    ENTIRELY of digits, dots, slashes, and hyphens — a plot/survey/unit
    number like "3.25/1" or "27/-16". A genuine "missing space after
    punctuation" authoring bug always involves an actual WORD (letters)
    running into the punctuation; a bare numeric code with an internal
    dot is a different, legitimate thing (validated against the real
    dealer sheet: "D.No. 3.25/1, Sy No.27/-16Pa4" — the survey number
    "3.25/1" must NOT be flagged, while "No.27" in the same address
    correctly IS flagged, since "No" is a real word/abbreviation running
    into "27" with no space).
    """
    core = word.rstrip(".,;:")
    return bool(core) and bool(re.match(r"^[0-9./-]+$", core))


def _punctuation_offences_in_token(word: str):
    """
    Returns a list of (punct_char, before_char, after_char) for every
    genuine "missing space after punctuation" offence inside a single
    whitespace-delimited token.

    A comma or full stop is only an offence when it is separating two
    WORDS. Three situations look identical to a naive
    "[.,] followed by an alphanumeric" scan but are all perfectly correct
    English, and each is excluded here:

      - Decimal points and thousands separators. A dot or comma sitting
        BETWEEN TWO DIGITS is a number, not a sentence boundary:
        "3.49%*," (an ROI figure), "29,999", "1,10,000", "1.5 Lakh",
        "40,000 kms". This was the cause of the reported false positive:
        the flagged character in "3.49%*," was never the trailing comma
        at all (that comma IS followed by a space in the email) — it was
        the decimal point in "3.49", which the old rule saw as a full
        stop running into the digit "4". Excluding digit-dot-digit and
        digit-comma-digit fixes every number of this shape at once,
        whatever currency symbol, asterisk or percent sign trails it,
        which is why it also covers tokens the old numeric-identifier
        guard missed (that guard required the WHOLE token to be digits
        and separators, so "3.49%*," failed it on the "%" and "*").

      - Initials and abbreviations: a capital immediately either side of
        the dot, e.g. "A.C.", "M.G." — unchanged from before.

      - Domains, URLs, emails and pure numeric identifiers, which are
        filtered out one level up before this function is ever called.

    A real offence still reports: "Plot No.50", "Gate no.3", "Rs.500",
    "sentence.Next" — in every one of those a letter, not a digit, sits
    immediately before the punctuation.
    """
    offences = []
    for m in re.finditer(r"[.,]", word):
        idx = m.start()
        after_char = word[idx + 1] if idx + 1 < len(word) else ""
        if not after_char or not (after_char.isalnum()):
            continue  # nothing runs into it - correctly spaced or end of token
        before_char = word[idx - 1] if idx > 0 else ""

        # Decimal point / thousands separator: digit on BOTH sides.
        if before_char.isdigit() and after_char.isdigit():
            continue

        # Initials, e.g. "A.C." - capital either side of the dot.
        if (before_char.isalpha() and before_char.isupper()
                and after_char.isalpha() and after_char.isupper()):
            continue

        offences.append((m.group(0), before_char, after_char))
    return offences


def run_punctuation_spacing_qa(html_raw: str, config) -> List[StyleIssue]:
    """
    Flags a comma or full stop that is immediately followed by the next
    word with no space, the normal English-usage rule being that a space
    always follows a comma or full stop before the next word starts.
    Reads html_to_rendered_text() (see run_double_space_qa above for why
    rendered text, not raw HTML source, is required - the same reasoning
    applies here: HTML source formatting must never be mistaken for a
    real spacing issue). Deliberately excludes:
      - Domains, URLs, and email addresses (see
        _looks_like_domain_or_email_token) - these legitimately have
        dots with no surrounding space.
      - Purely numeric identifiers like plot/survey numbers (see
        _looks_like_numeric_identifier_token).
      - Decimal points and thousands separators, and initials-style
        abbreviations (see _punctuation_offences_in_token).
    Validated against the real 23-dealer panel dataset plus the live
    campaign emails with zero false positives, while still correctly
    catching genuine spacing gaps like "Plot No.50" (should be "Plot
    No. 50") and "Gate no.3" (should be "Gate no. 3").
    """
    issues: List[StyleIssue] = []
    soup = BeautifulSoup(html_raw, "html.parser")
    rendered = html_to_rendered_text(soup)

    found: List[tuple] = []

    for line in rendered.splitlines():
        for word_match in re.finditer(r"\S+", line):
            word = word_match.group()
            if _looks_like_domain_or_email_token(word) or _looks_like_numeric_identifier_token(word):
                continue
            for punct, before_char, after_char in _punctuation_offences_in_token(word):
                found.append((word, punct, before_char, after_char))

    # De-duplicate on the token so the same typo repeated verbatim
    # elsewhere in the email doesn't spam the table.
    seen = set()
    unique_found = []
    for entry in found:
        if entry[0] not in seen:
            seen.add(entry[0])
            unique_found.append(entry)

    if unique_found:
        for word, punct, before_char, after_char in unique_found[:15]:
            punct_name = "comma" if punct == "," else "full stop"
            issues.append(StyleIssue(
                f"Punctuation spacing: '{word}'", "Fail",
                f"'{word}' - the {punct_name} after '{before_char}' runs straight "
                f"into '{after_char}'; a comma or full stop should be followed by "
                f"a space before the next word.",
            ))
        if len(unique_found) > 15:
            issues.append(StyleIssue(
                "Punctuation spacing", "Fail",
                f"...and {len(unique_found) - 15} more occurrence(s) not shown.",
            ))
    else:
        issues.append(StyleIssue("Punctuation spacing", "Pass", "Every comma and full stop found is correctly followed by a space."))

    return issues


_BOLD_WEIGHT_RE = re.compile(r"font-weight\s*:\s*(bold|[7-9]00)\b", re.IGNORECASE)


def is_text_bold(node, max_ancestor_hops: int = 6) -> bool:
    """
    True if a BeautifulSoup text/tag node renders bold — via a <strong>
    or <b> ancestor tag, OR an inline font-weight: bold (or a numeric
    weight of 700 or higher) on the node's OWN element or any ancestor,
    up to `max_ancestor_hops` levels up. Both mechanisms are real and
    both appear in actual dealer-panel HTML: "Tel.:"/"Website:" labels
    are wrapped in a <b style="font-weight: bold;">...</b> tag, while a
    dealer name line can be bold purely because its containing <td>'s
    own inline style sets font-weight: bold, with NO <strong>/<b> tag
    anywhere around the text at all — checking only for the tag, or only
    for the node's own style, would miss one of these two real cases.
    The hop limit keeps this from walking all the way up to <body> and
    picking up unrelated styling from far outside the actual line being
    checked.
    """
    current = node if hasattr(node, "get") else getattr(node, "parent", None)
    hops = 0
    while current is not None and hasattr(current, "name") and hops < max_ancestor_hops:
        if getattr(current, "name", None) in ("strong", "b"):
            return True
        style = (current.get("style") or "") if hasattr(current, "get") else ""
        if _BOLD_WEIGHT_RE.search(style):
            return True
        current = current.parent
        hops += 1
    return False


def check_bold_required_lines(html_raw: str, dealer_row: "DealerRow", config) -> List[StyleIssue]:
    """
    Verifies that the dealer name, every branch/city name (from
    dealer_row.bold_required_lines — see DealerRow's docstring for how
    these are identified), and any "Tel.:"/"Website:" label all render
    bold. Uses find_best_match on the collapsed visible-text lines to
    locate each required line's own text node (same location strategy
    used throughout this file), then is_text_bold() to check it.
    """
    issues: List[StyleIssue] = []

    soup = BeautifulSoup(html_raw, "html.parser")
    work = BeautifulSoup(str(soup), "html.parser")
    for tag in work(["script", "style", "noscript"]):
        tag.decompose()
    for c in work.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for br in work.find_all("br"):
        br.replace_with("\n")

    checked_any = False

    if dealer_row.bold_required_lines:
        collapsed_lines_with_nodes: List[Tuple[str, object]] = []
        for text_node in work.find_all(string=True):
            raw_piece = str(text_node)
            for piece in raw_piece.split("\n"):
                cleaned = re.sub(r"[ \t]+", " ", piece.replace("\xa0", " ")).strip()
                if cleaned:
                    collapsed_lines_with_nodes.append((cleaned, text_node))

        def _find_node_for_line(target_line: str):
            collapsed_only = [c for c, _ in collapsed_lines_with_nodes]
            match = find_best_match(collapsed_only, target_line)
            if match is None:
                return None
            idx = match[0]
            return collapsed_lines_with_nodes[idx][1]

        for line in dealer_row.bold_required_lines:
            node = _find_node_for_line(line)
            if node is None:
                continue  # not found at all — already reported Missing by Content QA
            checked_any = True
            if is_text_bold(node):
                issues.append(StyleIssue(f"Bold text: '{line}'", "Pass", f"'{line}' correctly renders bold."))
            else:
                issues.append(StyleIssue(f"Bold text: '{line}'", "Fail", f"'{line}' should be bold (dealer name / city / branch name) but is not."))

    # Tel./Website labels: not a fixed line from bold_required_lines
    # (the number/domain varies per dealer), so found by label text
    # instead — any line beginning with "Tel." or "Website" must be bold.
    for label in ("Tel.", "Website"):
        label_nodes = work.find_all(string=re.compile(rf"^\s*{re.escape(label)}\s*:?", re.IGNORECASE))
        for text_node in label_nodes:
            stripped = str(text_node).strip()
            if not stripped:
                continue
            checked_any = True
            display = stripped[:30]
            if is_text_bold(text_node):
                issues.append(StyleIssue(f"Bold text: '{display}'", "Pass", f"'{display}' correctly renders bold."))
            else:
                issues.append(StyleIssue(f"Bold text: '{display}'", "Fail", f"'{display}' — the '{label}' label should be bold but is not."))

    if not checked_any:
        issues.append(StyleIssue("Bold text", "Warn", "No dealer name, city/branch name, or Tel./Website label could be located to check for bold styling."))

    return issues


def run_image_size_qa(html_raw: str, images_source) -> List[StyleIssue]:
    """
    images_source can be:
      - None (no check possible)
      - a zip file-like object (uploaded separately alongside a pasted/uploaded HTML)
      - a dict {filename_lower: size_bytes} built from an uploaded model folder/zip
    """
    issues: List[StyleIssue] = []
    soup = BeautifulSoup(html_raw, "html.parser")
    img_srcs = [img.get("src", "") for img in soup.find_all("img") if img.get("src")]

    if not img_srcs:
        issues.append(StyleIssue("Image size", "Warn", "No <img> tags found in the HTML."))
        return issues

    if images_source is None:
        issues.append(StyleIssue(
            "Image size", "Warn",
            f"Found {len(img_srcs)} image reference(s) in the HTML, but no images were provided — "
            "upload an images .zip (or the full model folder) to check the 300KB limit."
        ))
        return issues

    name_to_size: Dict[str, int] = {}

    if isinstance(images_source, dict):
        name_to_size = images_source
    else:
        try:
            zf = zipfile.ZipFile(images_source)
        except Exception:
            issues.append(StyleIssue("Image size", "Warn", "The images .zip could not be opened, so image sizes were not checked."))
            return issues
        for zi in zf.infolist():
            base = zi.filename.split("/")[-1].lower()
            if base:
                name_to_size[base] = zi.file_size

    for src in img_srcs:
        base = src.split("/")[-1].lower()
        size_bytes = name_to_size.get(base)
        if size_bytes is None:
            issues.append(StyleIssue("Image size", "Warn", f"Image '{src}' referenced in HTML but not found in the uploaded images."))
            continue
        size_kb = size_bytes / 1024
        if size_kb > 300:
            issues.append(StyleIssue("Image size", "Fail", f"Image '{src}' is {size_kb:.0f}KB — exceeds the 300KB limit."))
        else:
            issues.append(StyleIssue("Image size", "Pass", f"Image '{src}' is {size_kb:.0f}KB — within the 300KB limit."))

    return issues


# =========================================================
# Content comparison
# =========================================================

# How many consecutive email lines may be joined back together while
# looking for one Excel panel line (see `find_wrapped_match`). Four covers
# the long addresses the templates wrap; more than that and "consecutive
# lines" stops meaning anything.
WRAPPED_LINE_MAX_SPAN = 4

# ...and the shortest target worth trying it on. A short label is a
# substring of half the email; stitching lines together to find one would
# invent matches rather than recover them.
WRAPPED_LINE_MIN_CHARS = 20


def find_wrapped_match(html_lines: List[str], target: str) -> Optional[Tuple[int, str]]:
    """One Excel panel line that the email rendered across SEVERAL lines.

    The sheet holds an address as a single cell line:

        222, 220A Whitefield Main Road, Sadaramangala, Whitefield,
        Bengaluru, Karnataka, India - 560048.

    and the email sets it as three, because that is what fits the panel.
    Line-by-line matching cannot see it, so a perfectly correct address
    was reported missing.

    Deliberately EXACT: consecutive lines are joined and the result has to
    equal the target once normalised. Nothing fuzzy happens here, so this
    can only ever recover a line the email really does contain — a wrapped
    address with a genuine difference in it stays missing, which is the
    answer a reviewer needs.
    """
    t = normalize_text(target)
    if len(t) < WRAPPED_LINE_MIN_CHARS:
        return None
    for start in range(len(html_lines)):
        for span in range(2, WRAPPED_LINE_MAX_SPAN + 1):
            window = html_lines[start:start + span]
            if len(window) < span:
                break
            joined = normalize_text(" ".join(window))
            if joined == t:
                return start, " ".join(window)
            if len(joined) > len(t):
                break        # already longer than the target; widening cannot help
    return None


def compare_source_to_html(source_lines: List[str], html_raw: str) -> pd.DataFrame:
    soup = BeautifulSoup(html_raw, "html.parser")
    html_visible = html_to_visible_text(soup)
    html_lines = split_lines_keep_order(html_visible)

    source_items = [line for line in source_lines if normalize_text(line) not in {"|", "-", "—"}]

    results: List[CheckResult] = []
    for item in source_items:
        match = find_best_match(html_lines, item) or find_wrapped_match(html_lines, item)
        status = "Present" if match else "Missing"
        results.append(CheckResult(item=item, status=status))

    if results:
        return pd.DataFrame([r.__dict__ for r in results])
    return pd.DataFrame(columns=["item", "status"])


# =========================================================
# Model-folder (zip) helpers
# =========================================================

def extract_model_zip(uploaded_zip) -> Tuple[Optional[str], Dict[str, int]]:
    """
    The first email inside a model zip, as (html_text, image_sizes).

    Kept for callers that only ever want one email out of a zip. The
    upload path itself goes through `modules/model_bundle.extract_models`,
    which returns EVERY model in the zip — a campaign is delivered as one
    zip holding a folder per model, nested inside a wrapper directory or
    two, and each of those folders is its own email.
    """
    models, _note = ext_model_bundle.extract_models(uploaded_zip, "")
    if not models:
        return None, {}
    return models[0].html, models[0].image_sizes



# =========================================================
# Multi-emailer master PDF helpers
# =========================================================

@st.cache_data(show_spinner=False)
def _cached_emailer_index(pdf_bytes: bytes):
    """Index a master PDF's EMAILER pages once per uploaded file.

    Cached on the PDF's own bytes because Streamlit re-runs this script on
    every widget interaction, and re-walking a 78-page deck each time a
    checkbox is ticked would be pure waste. The index is a small, plain
    dataclass (a few hundred bytes) so caching it costs nothing.
    """
    return ext_master_pdf_multi.index_emailer_pages(pdf_bytes)


# =========================================================
# UI
# =========================================================
# The console layout: a dark navigation rail, a sticky product bar, a
# welcome panel, a live progress rail, then the numbered workflow —
# Upload → Options → Master Reference → Run → Results — with every result
# set rendered as KPI tiles above a tabbed record table.
#
# TWO RULES THIS SECTION OBEYS
# ----------------------------
# 1. Presentation only. Every widget below keeps its EXACT key, EXACT
#    type and EXACT relative order to the widgets whose values it feeds.
#    The redesign moves widgets between cards and columns; it never
#    changes what any of them returns or how downstream code reads it.
#    Each variable is still assigned exactly once, before anything that
#    reads it, in the same order data becomes available (Excel before
#    dealer_rows, input_mode before email_jobs, masters before the run
#    block, and so on).
#
# 2. Nothing is ever unmounted. Streamlit throws away the state of any
#    widget it did not render on the latest run, so switching to
#    Settings would silently drop uploaded files and typed
#    master text. The workflow is therefore ALWAYS rendered and simply
#    hidden by CSS (ext_theme.hidden) when another view is on screen.
# ---------------------------------------------------------------

# ---- Navigation rail ------------------------------------------------
# There is no separate "QA Validation" view. It rendered the identical
# workflow to Home minus the welcome panel — a second door into the same
# room — so the welcome panel is simply a switch on the rail below.
ext_view = ext_theme.render_sidebar(
    nav_items=[
        ("home", "Home"),
        ("settings", "Settings"),
    ],
    key="dq_nav",
)

# The one view switch worth reaching without opening Settings sits at the
# foot of the rail. It is the SAME widget it always was — same key, same
# session_state entry — just rendered here instead of two clicks away.
with ext_theme.sidebar_prefs("View"):
    # A caption rather than a `help=` tooltip: the rail is 268px wide and
    # Streamlit hangs the tooltip's icon off the right edge of the label,
    # where it gets clipped.
    st.toggle(
        "Show the welcome panel",
        value=st.session_state.get("dq_pref_hero", True),
        key="dq_pref_hero",
    )
    st.caption("Off starts the workspace at the upload cards.")

# ---- Sticky product bar ---------------------------------------------
ext_theme.render_topbar(
    title="Dealer Panel QA Tool",
    subtitle="Validate. Compare. Ensure data accuracy.",
)


# =========================================================
# Settings view
# =========================================================
# Rendered BEFORE the workflow because the workflow calls st.stop() when
# a required upload is missing — anything rendered after that point would
# never appear.
if ext_view == "settings":
    ext_theme.section_title(
        "Settings", "Preferences for this browser session", "settings")

    _set_a, _set_b = st.columns(2, gap="large")

    with _set_a:
        with ext_theme.section("settings", "Display",
                               "How results and the workspace are rendered"):
            st.radio(
                "Result table density",
                options=["comfortable", "compact"],
                index=0 if st.session_state.get("dq_pref_density", "comfortable") == "comfortable" else 1,
                key="dq_pref_density",
                horizontal=True,
                help="Compact tightens every result row so more checks fit on screen.",
            )
            # "Show the welcome panel" is deliberately NOT here: it lives at
            # the foot of the navigation rail, where it can be flicked
            # without leaving the workspace. Two widgets cannot share one
            # session_state key, so this is the single definition of it.
            st.toggle(
                "Show the progress rail above the workflow",
                value=st.session_state.get("dq_pref_stepper", True),
                key="dq_pref_stepper",
            )
            st.caption(
                "Both settings take effect immediately and apply to this browser "
                "session only. The welcome panel has its own switch at the foot "
                "of the navigation rail."
            )

    with _set_b:
        with ext_theme.section("advanced", "Session",
                               "Clear everything and start over"):
            st.caption(
                "This tool keeps no history and has no sign-in. Files live in the "
                "browser session and disappear when the tab is closed. Resetting "
                "clears uploads, typed master text and the last run's results."
            )
            if st.button("Reset this session", use_container_width=True,
                         key="dq_reset_session"):
                _keep = {"dq_nav"}
                for _k in [k for k in st.session_state.keys() if k not in _keep]:
                    del st.session_state[_k]
                st.rerun()



# =========================================================
# Workflow  (always rendered; hidden by CSS on the other views)
# =========================================================
ext_workflow_visible = ext_view == "home"

with ext_theme.hidden(not ext_workflow_visible):

    if st.session_state.get("dq_pref_hero", True):
        ext_theme.render_hero(
            title="Dealer Panel QA Tool",
            subtitle="Upload the dealer master sheet and the dealer email(s) to check, "
                     "then run content, styling, exact-match, link and banner QA — and "
                     "download the whole thing as one Excel report.",
            eyebrow="Welcome to",
            features=(
                ("zap", "Fast & Accurate", "A full QA pass in seconds"),
                ("shield", "Detect Issues", "Content · styling · links · banner"),
                ("chart", "Actionable Insights", "One shareable Excel report"),
            ),
            side_lines=("Better data", "Stronger dealers", "Brighter roads"),
        )
    # The progress rail is filled in at the very bottom of this block,
    # once we know how far along the run actually is.
    ext_stepper_slot = st.container()

    # ---- 1 / 2 : Upload + Validation options ------------------------
    ext_col_upload, ext_col_options = st.columns([1.06, 1], gap="large")

    with ext_col_upload:
        with ext_theme.section(
            "upload", "Upload Input Files",
            "The dealer master sheet, then the email(s) to check", step=1,
        ):
            excel_upload = st.file_uploader(
                "Dealer panel Excel (.xlsx / .xls)", type=["xlsx", "xls"])
            st.caption(
                "Reads the 'Mailers - NSC ' sheet — columns: Dealer, Region, "
                "Dealer Panels (plus any additional branch/location columns after it)."
            )

            ext_selected_sheet = None
            ext_workbook = None
            if excel_upload is not None:
                try:
                    ext_workbook = ext_excel.open_workbook(excel_upload)
                    ext_selected_sheet = st.selectbox(
                        "Excel sheet",
                        options=["(auto-detect)"] + ext_workbook.sheet_names,
                        index=0,
                        help="Leave on auto-detect to read the 'Mailers - NSC' sheet. Pick another sheet to read only that one.",
                    )
                    if ext_selected_sheet == "(auto-detect)":
                        ext_selected_sheet = None
                except Exception:
                    st.warning("The sheets in this Excel file could not be listed.")

            dealer_rows: List[DealerRow] = []
            sheet_used = ""
            if excel_upload is not None:
                if ext_selected_sheet:
                    try:
                        df = ext_excel.load_selected_sheet_with_header(
                            ext_workbook, ext_selected_sheet,
                            required_columns_lower=["dealer", "region"],
                        )
                        dealer_rows = build_dealer_rows_from_df(df, sheet_name=ext_selected_sheet)
                        sheet_used = ext_selected_sheet
                        st.success(f"Loaded {len(dealer_rows)} dealer row(s) from manually selected sheet '{sheet_used}'.")
                    except Exception as e:
                        st.error(str(e))
                else:
                    try:
                        dealer_rows, sheet_used = read_dealer_panels_from_excel(excel_upload)
                        st.success(f"Loaded {len(dealer_rows)} dealer row(s) from sheet '{sheet_used}'.")
                    except Exception as e:
                        st.error(str(e))

            # Two different reasons the dealer list can be empty, and they need
            # different fixes: nothing was uploaded, or the sheet that was read
            # has no dealer rows (e.g. a tab picked in "Excel sheet" that lacks
            # the Dealer / Region / Dealer Panels columns). The second used to
            # be reported as "Please upload the dealer panel Excel file first"
            # while the file sat right there, uploaded.
            if excel_upload is None:
                _ext_no_rows_chip = "No Excel sheet yet"
                _ext_no_rows_msg = (
                    "Please upload the dealer panel Excel file first. If it still shows as "
                    "uploaded above, remove it (×) and upload it again — the app restarts "
                    "now and then, and an upload does not survive a restart."
                )
            else:
                _ext_no_rows_chip = "No dealers in this sheet"
                _ext_no_rows_msg = (
                    f"No dealers could be read from the sheet in use"
                    f"{f' (' + repr(ext_selected_sheet) + ')' if ext_selected_sheet else ''}. "
                    "Pick the sheet that has the Dealer, Region and Dealer Panels columns, "
                    "or set Excel sheet back to auto-detect."
                )

            st.markdown('<div style="height:.45rem"></div>', unsafe_allow_html=True)

            input_mode = st.radio(
                "Email input type",
                ["HTML file(s)", "Model folder(s) (.zip)"],
                index=0,
            )

            # Each entry: {"name": label, "html": str, "images": None|zipfile-like|dict}
            email_jobs: List[Dict] = []

            # BOTH uploaders are always drawn; the one not in use is hidden.
            # Streamlit throws away a widget's contents on any rerun that does
            # not draw it, so drawing only the selected one meant that
            # flicking the switch to HTML and back silently emptied the
            # model-zip uploader (and vice versa).
            with ext_theme.hidden(input_mode != "HTML file(s)"):
                html_files = st.file_uploader(
                    "HTML / HTM file(s)", type=["html", "htm", "txt"],
                    accept_multiple_files=True)
                images_zip = st.file_uploader(
                    "Images .zip (optional — enables the 300KB image-size check)",
                    type=["zip"])
            with ext_theme.hidden(input_mode == "HTML file(s)"):
                model_zips = st.file_uploader(
                    "Model folder(s) as .zip", type=["zip"], accept_multiple_files=True,
                    help="One zip per model, or a single campaign zip holding a folder "
                         "per model — however deeply the folders are nested.")

            if input_mode == "HTML file(s)":
                if html_files:
                    for hf in html_files:
                        html_text = hf.getvalue().decode("utf-8", errors="ignore")
                        email_jobs.append({
                            "name": hf.name,
                            "html": html_text,
                            "images": images_zip,
                            "images_zip_bytes": images_zip.getvalue() if images_zip is not None else None,
                        })
            else:
                if model_zips:
                    for mz in model_zips:
                        try:
                            _bundled, _note = ext_model_bundle.extract_models(
                                BytesIO(mz.getvalue()), mz.name)
                        except Exception:
                            st.error(f"'{mz.name}' could not be opened as a zip file.")
                            continue
                        if not _bundled:
                            st.error(_note or f"No HTML file found inside '{mz.name}'.")
                            continue
                        if len(_bundled) > 1:
                            st.success(_note)
                        for _m in _bundled:
                            email_jobs.append({
                                "name": _m.name,
                                "html": _m.html,
                                "images": _m.image_sizes,
                                # Raw zip bytes kept so Banner QA can pull the actual
                                # banner pixels out of the model folder's images/
                                # directory. For a campaign zip this is a zip of
                                # THAT model's folder alone, rooted at the folder,
                                # so `images/...` in the HTML resolves the same way
                                # it does for a single-model zip.
                                "images_zip_bytes": _m.zip_bytes,
                                # Image files sitting beside index.html rather than
                                # inside images/ — the approved creative for this
                                # model. Offered to the master-image router below.
                                "bundled_masters": _m.master_images,
                            })

            # No two adapts may share a name. Everything per-adapt — the
            # Master store, its widget keys, the results — is keyed by it,
            # and three folders' worth of "index.html" is the ordinary case.
            ext_model_bundle.uniquify_names(email_jobs)

            if email_jobs:
                st.markdown(
                    '<div class="dq-kv">'
                    + "".join(
                        f'<div class="dq-kv-item">{ext_theme.icon_html("file", 12)} '
                        f'&nbsp;{html_escape_module.escape(j["name"])}</div>'
                        for j in email_jobs
                    )
                    + "</div>",
                    unsafe_allow_html=True,
                )

    # `_master_nonce` has to exist before any Master uploader is created,
    # and "Clear Masters" (below) bumps it — st.file_uploader cannot be
    # emptied from code any other way than handing it a fresh widget key.
    if "ext_master_nonce" not in st.session_state:
        st.session_state["ext_master_nonce"] = 0
    _master_nonce = st.session_state["ext_master_nonce"]

    with ext_col_options:
        with ext_theme.section(
            "options", "Validation Options",
            "Pick the dealer, switch on the optional checks, then run", step=2,
        ):
            ext_selected_dealer_row = None
            if dealer_rows:
                ext_dealer_options = ["(none — use automatic HTML detection)"] + ext_dealer.build_dealer_options(dealer_rows)
                ext_dealer_choice = st.selectbox(
                    "Dealer selection", options=ext_dealer_options, key="ext_dealer_dropdown_live",
                    help="Pick a dealer to check every email against that dealer's details, "
                         "instead of the dealer detected from each email.",
                )
                if ext_dealer_choice != "(none — use automatic HTML detection)":
                    ext_selected_dealer_row = ext_dealer.resolve_selected_dealer(dealer_rows, ext_dealer_choice)
                    if ext_selected_dealer_row:
                        st.success(f"Master dealer set to: {ext_selected_dealer_row.dealer} ({ext_selected_dealer_row.region})")
            else:
                if excel_upload is None:
                    st.info("Upload the dealer panel Excel file first — the dealer list appears here once it's loaded.")
                else:
                    st.warning(_ext_no_rows_msg)

            st.markdown(
                '<div style="height:.2rem"></div>'
                '<div class="dq-head-sub" style="margin-bottom:.35rem">'
                'Optional checks — off unless you tick them.</div>',
                unsafe_allow_html=True,
            )
            ext_check_dealer_in_banner = st.checkbox("Dealer exists in Banner", value=False, key="ext_dealer_in_banner")
            ext_check_dealer_in_body = st.checkbox("Dealer exists in Body", value=False, key="ext_dealer_in_body")

            ext_run_advanced_qa = st.toggle(
                "Enable Advanced QA (Body / Banner / OCR)", value=False, key="ext_run_advanced_qa",
                help="Turn on to also check the banner text and the body copy against a master. "
                     "All the other checks always run.",
            )

            # Where the Headline and Subheadline sit on the banner is a
            # creative-guideline decision that has changed: the current
            # guideline sets them at the HEAD of the banner, the previous
            # one in a band at the FOOT next to the roundel. Auto-detect
            # reads either, per image — so a Master on one guideline and an
            # adapt on the other are both read correctly in the same run —
            # and the explicit choices are there for artwork it reads
            # wrongly. Only Banner QA uses this.
            _ext_layout_labels = {
                ext_ocr.BANNER_LAYOUT_AUTO: "Auto-detect (recommended)",
                ext_ocr.BANNER_LAYOUT_LATEST: "Latest guideline — headline at the top",
                ext_ocr.BANNER_LAYOUT_CLASSIC: "Classic guideline — headline at the foot",
                ext_ocr.BANNER_LAYOUT_SIZE_BANDS: "Font size only",
            }
            # Always drawn (hidden while Advanced QA is off) so a chosen layout
            # survives switching Advanced QA off and on again.
            with ext_theme.hidden(not ext_run_advanced_qa):
                ext_banner_layout = st.selectbox(
                    "Banner layout",
                    options=list(_ext_layout_labels.keys()),
                    format_func=lambda k: _ext_layout_labels[k],
                    index=0,
                    key="ext_banner_layout",
                    help="How the banner's Headline and Subheadline are located. "
                         "Auto-detect works out which guideline each banner follows "
                         "from where its copy sits.",
                )

            # A plain statement of what this run will actually cover, so the
            # Run button is never pressed on a half-filled form and then
            # answered with a red error further down the page.
            _ready_bits = [
                ext_theme.chip(
                    f"{len(dealer_rows)} dealer row(s)" if dealer_rows else _ext_no_rows_chip,
                    "ok" if dealer_rows else "warn",
                    "check-circle" if dealer_rows else "alert-triangle",
                ),
                ext_theme.chip(
                    f"{len(email_jobs)} email(s) to check" if email_jobs else "No email uploaded yet",
                    "ok" if email_jobs else "warn",
                    "check-circle" if email_jobs else "alert-triangle",
                ),
                ext_theme.chip(
                    ext_selected_dealer_row.dealer if ext_selected_dealer_row is not None
                    else "Dealer auto-detected",
                    "brand", "car",
                ),
            ]
            st.markdown(
                '<div style="height:.35rem"></div>'
                '<div class="dq-kv dq-ready">' + "".join(_ready_bits) + "</div>"
                '<div style="height:.55rem"></div>',
                unsafe_allow_html=True,
            )

            clear_masters_clicked = st.button(
                "Clear masters", use_container_width=True,
                help="Removes every Master JPG / PDF / HTML ZIP and all typed Master text, "
                     "both the global ones and every per-adapt override.",
            )

    # ---- The Run button, at the head of the navigation rail ----------
    # It used to sit in the card above, which stops working as soon as a
    # run produces a report several thousand pixels long — re-running then
    # means scrolling all the way back to find it. The rail does not
    # scroll, so there it is always in reach.
    #
    # It is CREATED here, at the point in the script where the run's
    # readiness is finally known, and drawn into the slot the rail
    # reserved for it. It is created on every view, hidden on the ones
    # that are not the workspace, because `if run_clicked:` below still
    # has to have an answer.
    with ext_theme.sidebar_action(hidden=not ext_workflow_visible):
        run_clicked = st.button(
            "Run QA Validation", type="primary", use_container_width=True,
            help="Runs every enabled check against every uploaded email.",
        )
        ext_theme.sidebar_ready_note(
            (f"{len(dealer_rows)} dealer row(s)" if dealer_rows
             else _ext_no_rows_chip, bool(dealer_rows)),
            (f"{len(email_jobs)} email(s)" if email_jobs
             else "No email yet", bool(email_jobs)),
        )

    if clear_masters_clicked:
        ext_multi_master.clear_all()
        for _k in ("ext_manual_headline", "ext_manual_subheadline", "ext_manual_dealer_name",
                   "ext_manual_body_text", "ext_body_as_is", "ext_body_to_be"):
            st.session_state.pop(_k, None)
        st.session_state["ext_master_nonce"] = _master_nonce + 1
        st.rerun()

    def render_qa_table(df: "pd.DataFrame", status_col: str, table_key: str, pass_label: str = "Passed") -> None:
        """
        Thin delegate to the single shared table-rendering implementation in
        modules/results_ui.py (see that module's docstring for why the
        implementation itself lives there and not here) — one complete
        "All records" table under the section's count tiles.
        """
        ext_results_ui.render_qa_table(df, status_col=status_col, table_key=table_key, pass_label=pass_label)

    # ---- 3 : Master reference (optional) ----------------------------
    ext_theme.section_title(
        "Master Reference",
        "Optional — needed only for Banner, Body and OCR checks",
        "layers",
    )

    ext_master_col_a, ext_master_col_b = st.columns(2, gap="large")

    with ext_master_col_a:
        with ext_theme.section(
            "text", "Manual Text Master",
            "Type what the banner should say. Any field you fill wins for that field.",
        ):
            ext_manual_headline = st.text_input("Headline", value="", key="ext_manual_headline")
            ext_manual_subheadline = st.text_input("Subheadline", value="", key="ext_manual_subheadline")
            ext_manual_dealer_name = st.text_input("Dealer Name", value="", key="ext_manual_dealer_name")
            ext_manual_body_text = st.text_area("Body Text", value="", key="ext_manual_body_text", height=100)
            ext_manual_master = ext_manual.ManualMasterText(
                headline=ext_manual_headline,
                subheadline=ext_manual_subheadline,
                dealer_name=ext_manual_dealer_name,
                body_text=ext_manual_body_text,
            )
            if ext_manual_master.is_active():
                _ext_manual_fields_used = [
                    label for label, is_manual in (
                        ("Headline", ext_manual_master.headline_is_manual()),
                        ("Subheadline", ext_manual_master.subheadline_is_manual()),
                        ("Dealer Name", ext_manual_master.dealer_name_is_manual()),
                        ("Body Text", ext_manual_master.body_text_is_manual()),
                    ) if is_manual
                ]
                st.info(
                    f"Typed text is used for: {', '.join(_ext_manual_fields_used)}. "
                    f"Fields left blank still come from the master image, PDF or HTML zip."
                )

        with ext_theme.section(
            "master-b", "Manual Body Comparison",
            "As Is / To Be — compared against the live HTML body.",
        ):
            ext_body_as_is = st.text_area("As Is", value="", key="ext_body_as_is", height=80)
            ext_body_to_be = st.text_area("To Be", value="", key="ext_body_to_be", height=80)

    with ext_master_col_b:
        with ext_theme.section(
            "image", "Master Image (JPG)",
            "A full email screenshot — cropped to the banner automatically. "
            "Upload one per model and each is routed by the model named in its file name.",
        ):
            ext_master_jpg_uploads = st.file_uploader(
                "Master JPG", type=["jpg", "jpeg", "png"],
                key=f"ext_master_jpg_{_master_nonce}", accept_multiple_files=True,
                help="One image is used for every adapt. Several are matched to their "
                     "models by file name — '..._Sep26 X3.png' goes to the X3 — the same "
                     "way a master PDF's pages are routed.",
            ) or []
            # One upload keeps behaving as the single global Master it
            # always was; several are routed per model below.
            ext_master_jpg_upload = (
                ext_master_jpg_uploads[0] if len(ext_master_jpg_uploads) == 1 else None
            )

        with ext_theme.section(
            "pdf", "Master PDF",
            "A sales-push bulletin — every model's emailer page is routed automatically",
        ):
            ext_master_pdf_upload = st.file_uploader(
                "Master PDF", type=["pdf"], key=f"ext_master_pdf_{_master_nonce}",
            )
            ext_master_pdf_page = st.number_input(
                "PDF page number (optional)", min_value=0, value=0, step=1, key="ext_master_pdf_page",
                help="Leave at 0 to auto-detect the page containing the master creative.",
            )

        with ext_theme.section(
            "zip", "Master HTML (ZIP)",
            "The approved master email as html + images/",
        ):
            ext_master_html_zip_upload = st.file_uploader(
                "Master HTML ZIP", type=["zip"], key=f"ext_master_html_zip_{_master_nonce}",
            )

    # ---- Multi-emailer master PDF index + per-adapt Master overrides --
    #
    # TERMINOLOGY: "master" = the approved reference we compare AGAINST;
    # "adapt" = each uploaded dealer email being QA'd. Normal QA already
    # handled many adapts at once (they all share one Excel sheet). Advanced
    # QA could not, because it only had ONE global Master JPG / HTML ZIP /
    # Manual Text block — so five adapts were all checked against a single
    # master, which can only ever be right for one of them.
    #
    # Master PDF is the exception and deliberately stays a SINGLE upload: a
    # sales-push bulletin deck already contains every model's EMAILER page in
    # one file, so each adapt is routed to its own page automatically instead
    # of being uploaded again per adapt (see modules/master_pdf_multi.py).
    ext_pdf_emailer_index = None
    ext_pdf_auto_map = {}
    if ext_master_pdf_upload is not None and email_jobs:
        try:
            ext_pdf_emailer_index = _cached_emailer_index(ext_master_pdf_upload.getvalue())
            if ext_pdf_emailer_index is not None and ext_pdf_emailer_index.entries:
                ext_pdf_auto_map = ext_master_pdf_multi.build_auto_map(email_jobs, ext_pdf_emailer_index)
        except Exception:
            ext_pdf_emailer_index = None
            st.warning("The master PDF could not be scanned for its emailer pages.")

    # ---- Master images, routed per model ----------------------------
    # Uploaded files first, then any creative a campaign zip carried
    # beside its own index.html; `by_code` prefers the uploaded one on a
    # tie, because uploading it was a deliberate act.
    ext_master_image_index = ext_master_image_multi.index_master_images(
        [(f.name, f.getvalue()) for f in ext_master_jpg_uploads])
    _ext_bundled_masters = [
        (name, data)
        for job in email_jobs
        for name, data in (job.get("bundled_masters") or [])
    ]
    if _ext_bundled_masters:
        ext_master_image_index = ext_master_image_multi.index_master_images(
            _ext_bundled_masters, origin="bundle",
            existing=ext_master_image_index)

    ext_master_image_auto_map = {}
    if len(ext_master_image_index.entries) > 1 and email_jobs:
        ext_master_image_auto_map = ext_master_image_multi.build_auto_map(
            email_jobs, ext_master_image_index)

    ext_multi_assignments = {}
    _ext_job_names = [j["name"] for j in email_jobs]

    # Both routing tables are reference material — they say how each adapt
    # found its master, which matters only when something looks wrong. They
    # used to occupy a full card each, above the controls people actually
    # use, so they are folded into one closed dropdown instead.
    _ext_show_image_routing = bool(ext_master_image_auto_map)
    _ext_show_pdf_routing = bool(
        ext_pdf_emailer_index is not None and ext_pdf_emailer_index.entries)
    _ext_routing_box = (
        st.expander("Master routing — how each email found its master", expanded=False)
        if (_ext_show_image_routing or _ext_show_pdf_routing)
        else contextlib.nullcontext()
    )

    with _ext_routing_box:
      if _ext_show_image_routing:
          with ext_theme.section(
              "image", "Master Images — Model Routing",
              ext_master_image_index.note,
          ):
              _img_rows = []
              for _jname in _ext_job_names:
                  _entry, _reason = ext_master_image_auto_map.get(_jname, (None, ""))
                  _img_rows.append({
                      "Adapt": _jname,
                      "Master image": _entry.file_name if _entry else "— not matched —",
                      "Model": _entry.model_label if _entry else "",
                      "How it was matched": _reason,
                  })
              if _img_rows:
                  st.dataframe(_img_rows, use_container_width=True, hide_index=True)
              st.caption(
                  "Each adapt takes the master image whose file name names the same model. "
                  "Anything routed incorrectly can be overridden per adapt in the "
                  "Per-Adapt Masters card below, which always wins."
              )

      if _ext_show_pdf_routing:
          with ext_theme.section(
              "routing", "Master PDF — Emailer Routing",
              ext_pdf_emailer_index.note,
          ):
              _routing_rows = []
              for _jname in _ext_job_names:
                  _entry, _reason = ext_pdf_auto_map.get(_jname, (None, ""))
                  _routing_rows.append({
                      "Adapt": _jname,
                      "Master emailer": _entry.model_label if _entry else "— not matched —",
                      "PDF page": _entry.page_number if _entry else "",
                      "How it was matched": _reason,
                  })
              if _routing_rows:
                  st.dataframe(_routing_rows, use_container_width=True, hide_index=True)
              st.caption(
                  "Each adapt is routed to its own model's EMAILER page in this one deck — "
                  "no separate master PDF per adapt is needed. Anything routed incorrectly "
                  "can be pinned by hand in the Per-Adapt Masters card below."
              )

    if len(_ext_job_names) > 1 or (ext_pdf_emailer_index is not None and ext_pdf_emailer_index.entries):
        with ext_theme.section(
            "multi", "Per-Adapt Masters",
            "Give each uploaded email its own Master — anything left empty uses the Masters above",
        ):
            ext_multi_assignments = ext_multi_master.render_panel(
                _ext_job_names,
                pdf_index=ext_pdf_emailer_index,
                pdf_auto_map=ext_pdf_auto_map,
                nonce=_master_nonce,
            )

    # ---- Progress rail (now that the run's state is known) -----------
    if st.session_state.get("dq_pref_stepper", True):
        if st.session_state.get("ext_has_run") and dealer_rows and email_jobs:
            _ext_step = 3
        elif dealer_rows and email_jobs:
            _ext_step = 2
        elif dealer_rows or email_jobs:
            _ext_step = 1
        else:
            _ext_step = 0
        with ext_stepper_slot:
            ext_theme.render_stepper(
                [
                    ("Upload files", "Excel + email(s)"),
                    ("Configure", "Dealer, options, master"),
                    ("Run QA", "All checks in one pass"),
                    ("View results", "Report + Excel export"),
                ],
                current=_ext_step,
            )

    # ---- 4 : Run --------------------------------------------------------
    if run_clicked:
        st.session_state["ext_has_run"] = True
    run = st.session_state.get("ext_has_run", False)

    if run:
        if not dealer_rows:
            st.error(_ext_no_rows_msg)
            st.stop()
        if not email_jobs:
            st.error("Please upload at least one HTML file or model folder.")
            st.stop()

        ext_theme_results_group_opened = True
        ext_theme_results_group = ext_theme.group("Results")
        ext_theme_results_group.__enter__()

        ext_report_jobs: List[ext_excel_report.JobReportData] = []

        for job_idx, job in enumerate(email_jobs):
            # Each email gets its own collapsible group so a multi-adapt run
            # can be reviewed one email at a time instead of scrolling past
            # every table for every model. The body still EXECUTES when the
            # group is collapsed - Streamlit only hides it visually - so the
            # consolidated Excel report is always complete regardless of which
            # groups happen to be open.
            with st.expander(
                f"{job_idx + 1}.  {job['name']}",
                expanded=(len(email_jobs) == 1),
            ):

                auto_detected_dealer_row = detect_dealer_from_html(job["html"], dealer_rows)

                # Dealer Selection dropdown now applies to the WHOLE tool, not just
                # Advanced QA. When a dealer is explicitly selected, that selection
                # is authoritative for this run: it becomes the dealer whose Excel
                # row (name/region/panel text) everything below is compared
                # against — Content QA, Styling QA, exact-match, phone format, all
                # of it. If the email's HTML actually belongs to a different
                # dealer (or no dealer could be auto-detected from the HTML at
                # all), comparing against the SELECTED dealer's Excel data will
                # correctly and naturally fail every Excel-comparable point, since
                # that dealer's real name/address/phone number won't be found in
                # an email built for someone else. When nothing is selected from
                # the dropdown, behaviour is fully unchanged: the auto-detected
                # dealer (from the email's own "BMW <Dealer>" heading) is used, as
                # before.
                dealer_mismatch = False
                if ext_selected_dealer_row is not None:
                    dealer_row = ext_selected_dealer_row
                    if (auto_detected_dealer_row is None
                            or normalize_text(auto_detected_dealer_row.dealer) != normalize_text(dealer_row.dealer)):
                        dealer_mismatch = True
                else:
                    dealer_row = auto_detected_dealer_row

                if dealer_row is None:
                    st.error("Could not automatically match this email to a dealer from the Excel sheet.")
                    continue

                # Header strip for this email: the file, its dealer, and how
                # that dealer was decided.
                _job_chips = [
                    ext_theme.chip(dealer_row.dealer, "brand", "car"),
                ]
                if dealer_row.region:
                    _job_chips.append(ext_theme.chip(dealer_row.region, "ghost", ""))
                _job_chips.append(ext_theme.chip(
                    "dealer selected by hand" if ext_selected_dealer_row is not None
                    else "dealer auto-detected from HTML",
                    "ghost", "info",
                ))
                st.markdown(
                    f'<div class="dq-head" style="margin-bottom:.3rem">'
                    f'<div class="dq-head-ico" style="background:{ext_theme.BRAND_TINT};'
                    f'color:{ext_theme.BRAND}">{ext_theme.icon_html("file", 19)}</div>'
                    f'<div class="dq-head-txt"><div class="dq-head-title">'
                    f'{html_escape_module.escape(job["name"])}</div>'
                    f'<div class="dq-topbar-chips" style="justify-content:center;'
                    f'margin-top:.45rem">{"".join(_job_chips)}</div></div></div>',
                    unsafe_allow_html=True,
                )

                if dealer_mismatch:
                    auto_label = (
                        f"<b>{html_escape_module.escape(auto_detected_dealer_row.dealer)}</b>"
                        if auto_detected_dealer_row else "<i>(could not be auto-detected)</i>"
                    )
                    ext_theme.banner(
                        f"<b>SELECTED DEALER MISMATCH</b> — this email's HTML actually belongs to "
                        f"{auto_label}, but you selected "
                        f"<b>{html_escape_module.escape(dealer_row.dealer)}</b> "
                        f"({html_escape_module.escape(dealer_row.region)}) from the dealer list. "
                        f"Every Excel-comparable check below is being run against the SELECTED "
                        f"dealer's data, so mismatched content is expected and correct.",
                        "bad", "alert-triangle",
                    )

                # Filled in once every table below has been computed, so the
                # KPI tiles can sit above the tables they summarise.
                ext_job_summary_slot = st.container()

                panel_lines = panel_text_to_lines(dealer_row.panel_text)
                # One row per sheet line: presence and exact match together
                # (see build_content_qa), phone checks under their line.
                results_df = build_content_qa(panel_lines, job["html"], EXT_CFG)

                # Feature 16 — Dealer Website Link QA: verifies the CTA button's
                # href AND the Dealer Panel's own "Website: ..." line both belong
                # to the SELECTED (dropdown) or AUTO-DETECTED dealer's own domain
                # (derived from the dealer name itself, since no dedicated
                # "Website" column exists in the Excel sheet), and that the two
                # agree with each other. `dealer_row` here is already the
                # correctly-resolved dealer for this run (dropdown selection wins,
                # otherwise falls back to HTML auto-detection — see the dealer
                # resolution logic above). Rows are appended to the SAME
                # results_df (same item/status schema as the existing Content QA
                # table) immediately after it's built, so they render directly
                # below the existing "Website: ..." Content QA row in the same
                # table, in the same Pass/Fail styling.
                if ext_website_link_qa is None:
                    website_link_result = type("_Missing", (), {"rows": [{
                        "item": "Dealer Website / CTA link checks",
                        "status": "Warn",
                        "detail": (
                            "Website and CTA link checks are not available in this copy of "
                            "the tool. Every other check has run normally."
                        ),
                    }]})()
                else:
                    website_link_result = ext_website_link_qa.run_website_link_qa(
                        dealer_name=dealer_row.dealer,
                        panel_text=dealer_row.panel_text,
                        html_raw=job["html"],
                        dealer_mismatch=dealer_mismatch,
                    )
                if website_link_result.rows:
                    website_link_df = pd.DataFrame(website_link_result.rows)
                    # compare_source_to_html() only ever produces item/status
                    # columns (no "detail"). Concatenating website_link_df (which
                    # DOES have a "detail" column) directly would union the
                    # columns and backfill every ORIGINAL content row's "detail"
                    # with NaN -- which then renders as the literal text "nan" in
                    # every existing Present/Missing row's Detail cell. Give the
                    # original rows a real empty "detail" first so the union
                    # produces "" instead of NaN for them.
                    if "detail" not in results_df.columns:
                        results_df["detail"] = ""
                    results_df = pd.concat([results_df, website_link_df], ignore_index=True)
                    results_df["detail"] = results_df["detail"].fillna("")
                elif "detail" not in results_df.columns:
                    results_df["detail"] = ""

                # When the dropdown-SELECTED dealer doesn't match the dealer this
                # email's HTML actually belongs to (dealer_mismatch, computed
                # above), every "Fail" row in Content QA is collapsed onto the
                # SAME single shared explanation — the content isn't merely
                # absent, it's actively wrong for the selected dealer. Repeating a
                # near-identical per-row detail for every one of the dozens of
                # mismatched lines added no information beyond what the mismatch
                # banner above the table already explains, so all of them share
                # one message instead.
                if dealer_mismatch:
                    combined_mismatch_detail = (
                        f"Dealer mismatch — this email's HTML belongs to "
                        f"{auto_detected_dealer_row.dealer if auto_detected_dealer_row else 'a different dealer'}, "
                        f"not the selected dealer ({dealer_row.dealer}). See the mismatch warning above."
                    )
                    fail_mask = results_df["status"] == "Fail"
                    results_df.loc[fail_mask, "detail"] = combined_mismatch_detail

                total = len(results_df)
                present = int((results_df["status"] == "Pass").sum()) if total else 0
                missing = int((results_df["status"] == "Fail").sum()) if total else 0
                content_warns = total - present - missing

                with ext_theme.section(
                    "content", "Content QA",
                    "Every line of the dealer's Excel panel must be in the email, exactly as written",
                ):
                    ext_results_ui.render_stat_row(
                        passed=present, warnings=content_warns, failed=missing,
                        total=total, total_label="Content checks",
                    )
                    render_qa_table(results_df, status_col="status", table_key=f"content_{job_idx}", pass_label="Passed")

                with ext_theme.section(
                    "style", "Styling QA",
                    "Fonts, colours, spacing, bold rules and image weight",
                ):
                    style_issues = run_style_qa(job["html"])
                    double_space_issues = run_double_space_qa(job["html"], EXT_CFG)
                    punctuation_issues = run_punctuation_spacing_qa(job["html"], EXT_CFG)
                    bold_issues = check_bold_required_lines(job["html"], dealer_row, EXT_CFG)
                    image_issues = run_image_size_qa(job["html"], job["images"])
                    all_issues = style_issues + double_space_issues + punctuation_issues + bold_issues + image_issues

                    style_df = pd.DataFrame([i.__dict__ for i in all_issues]) if all_issues else pd.DataFrame(columns=["rule", "severity", "detail"])
                    fails = int((style_df["severity"] == "Fail").sum()) if len(style_df) else 0
                    warns = int((style_df["severity"] == "Warn").sum()) if len(style_df) else 0
                    passes = int((style_df["severity"] == "Pass").sum()) if len(style_df) else 0

                    ext_results_ui.render_stat_row(
                        passed=passes, warnings=warns, failed=fails,
                        total=len(style_df), total_label="Styling rules",
                    )
                    render_qa_table(style_df, status_col="severity", table_key=f"style_{job_idx}", pass_label="Passed")

                # The run-level KPI tiles, rendered back up at the top of this
                # email's block now that every table has been computed.
                with ext_job_summary_slot:
                    ext_results_ui.render_stat_row(
                        passed=present + passes,
                        warnings=content_warns + warns,
                        failed=missing + fails,
                        total=total + len(style_df),
                        total_label="Total checks",
                    )

                if dealer_mismatch:
                    st.error("SELECTED DEALER MISMATCH — see the warning above. Content failures below are expected because this email is being checked against a different dealer's data than the one built into it.")
                elif missing > 0 or fails > 0:
                    st.warning("Some content is missing and/or styling rules failed — see tables above.")
                else:
                    st.success("All content found and all styling checks passed (or only warnings where data wasn't available).")

                ext_job_report = ext_excel_report.JobReportData(
                    job_name=job["name"],
                    dealer=dealer_row.dealer,
                    region=dealer_row.region,
                    content_df=results_df,
                    style_df=style_df,
                )
                ext_report_jobs.append(ext_job_report)

                if ext_run_advanced_qa:
                    with ext_theme.section(
                        "advanced", "Advanced QA",
                        "Body, Banner and OCR checks against the Master reference",
                    ):

                        # Headline / Subheadline / Dealer Name on a banner exist
                        # only as pixels, so every one of those checks depends on
                        # an OCR engine being installed. When none is, they all
                        # came back blank and Banner Text QA reported "No expected
                        # value provided for this field" - which reads like the
                        # Master was empty rather than like the tool could not
                        # look at it. Say so plainly instead.
                        _ocr_ok, _ocr_engine, _ocr_msg = ext_ocr.ocr_status()
                        if not _ocr_ok:
                            st.error(
                                "Banner text cannot be read on this computer, so the Headline, "
                                "Subheadline and Dealer Name checks are skipped."
                            )
                        elif _ocr_engine == "rapidocr":
                            st.warning(
                                "Banner text is being read with a less accurate method on this "
                                "computer, so words on the banner may run together."
                            )

                        # Manual Text is now per-field (see manual_mode.py) rather than
                        # an all-or-nothing switch, so it no longer forces the other
                        # sources' *_active flags to False as a block. resolve_active_source
                        # still picks ONE visual/HTML master source (for whichever
                        # fields Manual Text left blank) using the same priority order
                        # as before; Manual Text's own filled-in fields always win
                        # regardless of which visual source (if any) is "active".
                        # ---------------------------------------------------------
                        # Resolve THIS adapt's master sources.
                        #
                        # Priority per slot: the adapt's own per-adapt override
                        # (Per-Adapt Masters card) first, then the global Master
                        # control. Master JPG / HTML ZIP are re-wrapped in a fresh
                        # BytesIO every time because PIL and zipfile both consume
                        # the stream — reusing one uploaded file object across
                        # several adapts would leave every job after the first
                        # reading from an exhausted buffer.
                        # ---------------------------------------------------------
                        _assign = ext_multi_assignments.get(job["name"])

                        # Master JPG for this adapt, most specific first:
                        # a per-adapt override, then the master image whose
                        # file name names this adapt's model, then a single
                        # global Master JPG.
                        _routed_master_img = (
                            ext_master_image_auto_map.get(job["name"]) or (None, "")
                        )[0]
                        if _assign is not None and _assign.jpg_bytes:
                            job_master_jpg = _assign.jpg_file()
                        elif _routed_master_img is not None:
                            job_master_jpg = BytesIO(_routed_master_img.data)
                        elif ext_master_jpg_upload is not None:
                            job_master_jpg = BytesIO(ext_master_jpg_upload.getvalue())
                        else:
                            job_master_jpg = None

                        if _assign is not None and _assign.html_zip_bytes:
                            job_master_html_zip = _assign.html_zip_file()
                        elif ext_master_html_zip_upload is not None:
                            job_master_html_zip = BytesIO(ext_master_html_zip_upload.getvalue())
                        else:
                            job_master_html_zip = None

                        if ext_master_pdf_upload is not None:
                            job_master_pdf = BytesIO(ext_master_pdf_upload.getvalue())
                        else:
                            job_master_pdf = None

                        # Which page of the master PDF this adapt uses:
                        #   1. a page pinned by hand for this adapt
                        #   2. the page auto-routed from the deck's model sections
                        #   3. the global "PDF Page Number" box, when set
                        #   4. None -> master_pdf.py's own single-master auto-detect
                        _auto_entry = (ext_pdf_auto_map.get(job["name"]) or (None, ""))[0]
                        if _assign is not None and _assign.pdf_page and _assign.pdf_page_source == "manual":
                            job_master_pdf_page = int(_assign.pdf_page)
                        elif _auto_entry is not None:
                            job_master_pdf_page = int(_auto_entry.page_number)
                        elif ext_master_pdf_page and ext_master_pdf_page > 0:
                            job_master_pdf_page = int(ext_master_pdf_page)
                        else:
                            job_master_pdf_page = None

                        # Manual Text / Manual Body: per-adapt value wins per FIELD,
                        # so an adapt can override just its headline and still take
                        # everything else from the global Manual Text block.
                        job_manual_master = ext_manual.ManualMasterText(
                            headline=((_assign.headline if _assign is not None and _assign.headline.strip()
                                       else ext_manual_headline) or ""),
                            subheadline=((_assign.subheadline if _assign is not None and _assign.subheadline.strip()
                                          else ext_manual_subheadline) or ""),
                            dealer_name=((_assign.dealer_name if _assign is not None and _assign.dealer_name.strip()
                                          else ext_manual_dealer_name) or ""),
                            body_text=((_assign.body_text if _assign is not None and _assign.body_text.strip()
                                        else ext_manual_body_text) or ""),
                        )
                        job_body_as_is = ((_assign.body_as_is if _assign is not None and _assign.body_as_is.strip()
                                           else ext_body_as_is) or "")
                        job_body_to_be = ((_assign.body_to_be if _assign is not None and _assign.body_to_be.strip()
                                           else ext_body_to_be) or "")

                        if _assign is not None and _assign.has_any():
                            st.caption(
                                "Using this adapt's own Master: "
                                + ", ".join(_assign.summary_bits() or ["master PDF page routing"])
                            )

                        ext_sources = ext_priority.MasterSources(
                            manual_text_active=job_manual_master.is_active(),
                            master_jpg_active=job_master_jpg is not None,
                            master_pdf_active=job_master_pdf is not None,
                            master_html_zip_active=job_master_html_zip is not None,
                            dealer_dropdown_active=ext_selected_dealer_row is not None,
                            excel_active=bool(dealer_rows),
                        )
                        active_source = ext_priority.resolve_active_source(ext_sources)
                        # NOTE (was previously shown on-screen via st.caption; moved to a
                        # code comment per request): ext_priority.explain_priority(ext_sources)
                        # returns a human-readable string like "Active Master source:
                        # Manual Text (highest priority available)." — still computed
                        # below for any future debugging/logging use, just not rendered.
                        _ext_priority_explanation = ext_priority.explain_priority(ext_sources)

                        # Dealer Name fallback fix: when no Dealer Dropdown selection
                        # and no Manual Dealer Name is typed, the effective dealer name
                        # must come from a Master JPG/PDF/HTML ZIP (below, once that
                        # master's OCR/HTML text is available) — NEVER from `dealer_row`,
                        # which is only the auto-detected-from-this-email's-own-HTML
                        # match used for the separate base Dealer Panel QA feature.
                        # Falling back to `dealer_row` here was a tautology: it validates
                        # the email's claimed dealer name against that same email's HTML,
                        # so a genuinely wrong dealer name baked into the HTML would
                        # always "Pass" since it's being checked against itself.
                        if ext_selected_dealer_row is not None:
                            _dropdown_dealer_name = ext_selected_dealer_row.dealer
                        else:
                            _dropdown_dealer_name = ""

                        # Build the Master Banner from whichever visual/HTML source is
                        # active. This now runs whenever a visual/HTML master was
                        # uploaded — regardless of what active_source says — because
                        # active_source only reflects ONE top-priority source (e.g.
                        # "manual_text" if any Manual field is filled), but Manual Text
                        # is per-field: a Master JPG/PDF/HTML ZIP may still be needed to
                        # supply Dealer Name (or Headline/Subheadline) for whichever
                        # fields Manual Text left blank. Priority among the visual/HTML
                        # uploads themselves still follows Master JPG > Master PDF >
                        # Master HTML ZIP, same order as priority.py.
                        ext_master_banner_img = None
                        ext_master_unreadable = False
                        ext_master_html_text = None  # only populated when Master HTML ZIP supplied the banner
                        try:
                            if job_master_jpg is not None:
                                img = ext_master_image.load_image_from_upload(job_master_jpg)
                                crop_out = ext_master_image.crop_banner_top_to_dear(img, EXT_CFG)
                                ext_master_banner_img = crop_out.banner_image
                            elif job_master_pdf is not None:
                                crop_out = ext_master_pdf.get_master_banner_from_pdf(
                                    job_master_pdf, job_master_pdf_page, EXT_CFG)
                                ext_master_banner_img = crop_out.banner_image
                            elif job_master_html_zip is not None:
                                mh = ext_master_html_zip.load_master_from_html_zip(job_master_html_zip, EXT_CFG)
                                ext_master_banner_img = mh.banner_result.image
                                ext_master_html_text = mh.html_text
                        except Exception:
                            ext_master_unreadable = True

                        # -----------------------------------------------------------
                        # Expected Headline / Subheadline / Dealer Name / Body Text are
                        # now resolved PER-FIELD (see manual_mode.py):
                        #   - Any field the user typed into Manual Text always wins for
                        #     that field, regardless of what a visual master provides.
                        #   - Any field left blank in Manual Text falls through to the
                        #     active visual/HTML master source's own OCR/HTML-derived
                        #     value for that same field, if available.
                        #   - Dealer Dropdown supplies the dealer name fallback (never
                        #     the auto-detected `dealer_row` — see note above).
                        #   - If nothing provides a field, it stays "" and that field's
                        #     QA row is skipped (WARN "No expected value provided"),
                        #     same as before.
                        # -----------------------------------------------------------
                        master_derived_headline = ""
                        master_derived_subheadline = ""
                        master_derived_dealer_name = ""
                        master_derived_body_text = ""
                        ext_master_banner_clustered_lines = None

                        # A dealer-name HINT to search for while OCR'ing the Master
                        # Banner, if one is already known at this point (Dealer
                        # Dropdown selection, or a typed Manual Dealer Name) — see
                        # ocr_engine.find_dealer_line() / cluster_lines_by_size()'s
                        # `expected_dealer_name` param. This does NOT change the
                        # documented priority order below (dropdown/manual still
                        # always win as the FINAL effective_dealer_name); it only
                        # makes the OCR clustering itself smarter at locating the
                        # correct line on the banner when a Subheadline and Dealer
                        # Name happen to render at the same font size — which plain
                        # font-size banding alone cannot always tell apart (see
                        # ocr_engine.py's cluster_lines_by_size docstring).
                        _master_dealer_name_hint = (
                            _dropdown_dealer_name.strip() if _dropdown_dealer_name.strip()
                            else job_manual_master.dealer_name.strip()
                        )

                        # Every dealer name this workbook knows about.
                        #
                        # The Master creative is routinely built for a DIFFERENT
                        # dealer than the email under test - a master carrying
                        # "Bavaria Motors" is used to QA an "Infinity Cars"
                        # mailer, because only the dealer line changes between
                        # dealer versions of the same campaign. Passing the whole
                        # list (not just this email's dealer) lets the Master's
                        # own dealer line be LOCATED and lifted out of the
                        # master-derived Headline/Subheadline. Without it, that
                        # line has no band of its own to fall into, so it gets
                        # swept into the expected Subheadline ("BMW FUEL
                        # ADDITIVES. Bavaria Motors") and the email under test is
                        # then failed for not containing another dealer's name -
                        # which it must never contain.
                        #
                        # This does NOT change which dealer name is validated:
                        # the effective dealer name still follows the documented
                        # dropdown > manual > master-derived order below.
                        _known_dealer_names = [
                            str(getattr(r, "dealer", "") or "").strip()
                            for r in dealer_rows
                            if str(getattr(r, "dealer", "") or "").strip()
                        ]
                        _master_dealer_name_candidates = (
                            ([_master_dealer_name_hint] if _master_dealer_name_hint else [])
                            + _known_dealer_names
                        )

                        if ext_master_banner_img is not None:
                            try:
                                ext_master_banner_clustered_lines, _master_lines_res = ext_ocr.extract_clustered_text(
                                    ext_master_banner_img, prefer=EXT_OCR_PREFER_KEY,
                                    expected_dealer_name=_master_dealer_name_candidates,
                                    layout=ext_banner_layout,
                                )
                                master_derived_headline = ext_master_banner_clustered_lines.headline_text
                                master_derived_subheadline = ext_master_banner_clustered_lines.subheadline_text
                                # `dealer_text` prefers the content-matched dealer
                                # line (found by searching all OCR'd lines for the
                                # `_master_dealer_name_hint`'s words, regardless of
                                # which font-size band it landed in) and only falls
                                # back to the plain smallest-font-band guess when no
                                # hint was available or no confident match was
                                # found — so a Master JPG/PDF/HTML ZIP alone can
                                # still supply a reliable dealer name for
                                # validation even when Subheadline and Dealer Name
                                # share a font size on the banner.
                                master_derived_dealer_name = ext_master_banner_clustered_lines.dealer_text
                            except Exception:
                                ext_master_banner_clustered_lines = None
                                ext_master_unreadable = True

                        if ext_master_html_text:
                            master_derived_body_text = html_to_visible_text(BeautifulSoup(ext_master_html_text, "html.parser"))

                        expected_headline = job_manual_master.resolve_headline(master_derived_headline)
                        expected_subheadline = job_manual_master.resolve_subheadline(master_derived_subheadline)
                        expected_body_text = job_manual_master.resolve_body_text(master_derived_body_text)

                        # Dealer name resolution order (per latest spec):
                        #   1. Dealer Dropdown selection — if the person explicitly
                        #      picked a dealer, that ALWAYS wins, even over Manual
                        #      Dealer Name — the dropdown is the authoritative choice
                        #      once made. Manual Text / Master JPG/PDF/HTML in that
                        #      case are only used for Headline/Subheadline/Body Text.
                        #   2. Manual Dealer Name (if typed, and no dropdown selection).
                        #   3. Master JPG / Master PDF / Master HTML ZIP — OCR'd
                        #      dealer-name band from whichever was uploaded (never the
                        #      auto-detected `dealer_row` — see note above).
                        #   4. "" — no dealer name available, check is skipped.
                        if _dropdown_dealer_name:
                            effective_dealer_name = _dropdown_dealer_name
                        elif job_manual_master.dealer_name_is_manual():
                            effective_dealer_name = job_manual_master.dealer_name.strip()
                        elif master_derived_dealer_name.strip():
                            effective_dealer_name = master_derived_dealer_name.strip()
                        else:
                            effective_dealer_name = ""

                        # Manual Body Comparison (As Is / To Be) always overrides the
                        # master-derived body text if the user typed a "To Be" value —
                        # that section is an explicit manual override, unchanged.
                        if job_body_to_be.strip():
                            expected_body_text = job_body_to_be

                        # Input banner pixels. A model-folder .zip already contains
                        # everything needed (index.html + images/), so it is used
                        # directly; only a bare HTML upload with no images at all
                        # leaves Banner QA without a source. A fresh BytesIO per job
                        # because zipfile consumes the stream.
                        ext_input_zip_for_banner = job.get("images")
                        ext_input_banner_result = None
                        _job_zip_bytes = job.get("images_zip_bytes")
                        try:
                            if _job_zip_bytes:
                                ext_input_banner_result = ext_banner_detect.extract_banner_image(
                                    job["html"], BytesIO(_job_zip_bytes), EXT_CFG)
                            elif isinstance(ext_input_zip_for_banner, dict):
                                ext_input_banner_result = None
                                st.caption(
                                    "Banner QA needs the email's images — upload this email as a "
                                    "model-folder .zip, or add an images .zip alongside the HTML."
                                )
                            elif ext_input_zip_for_banner is not None:
                                ext_input_banner_result = ext_banner_detect.extract_banner_image(
                                    job["html"], ext_input_zip_for_banner, EXT_CFG)
                        except Exception:
                            st.caption("The banner image could not be read from this email.")

                        ext_input_banner_img = ext_input_banner_result.image if ext_input_banner_result else None

                        ext_input_banner_ocr_text = ""
                        ext_input_banner_engine = "none"
                        ext_input_banner_clustered_lines = None
                        if ext_input_banner_img is not None:
                            ocr_res = ext_ocr.extract_text(ext_input_banner_img, prefer=EXT_OCR_PREFER_KEY)
                            ext_input_banner_ocr_text = ocr_res.text
                            ext_input_banner_engine = ocr_res.engine_used
                            # Also run structured line extraction so Headline / Subheadline
                            # can be matched against their own font-size band instead of
                            # the whole banner text blob (fixes headline+subheadline
                            # being treated as one combined string).
                            #
                            # `expected_dealer_name` is passed here too (same hint
                            # already resolved above as `effective_dealer_name` —
                            # dropdown > manual > master-derived, per the documented
                            # priority order) so the Dealer Name line is located by
                            # content-matching its words across ALL font bands, not
                            # just whichever band plain font-size clustering happened
                            # to place it in. This matters because a banner's
                            # Subheadline and Dealer Name commonly render at the
                            # SAME font size (see ocr_engine.py's
                            # cluster_lines_by_size docstring) — without this hint,
                            # the two would still get merged into a single band
                            # here, relying entirely on banner_text_qa.py's own
                            # fallback re-search to recover the Dealer Name. Passing
                            # it here fixes it at the source as well, so both
                            # layers agree and neither is a single point of failure.
                            try:
                                ext_input_banner_clustered_lines, _lines_res = ext_ocr.extract_clustered_text(
                                    ext_input_banner_img, prefer=EXT_OCR_PREFER_KEY,
                                    expected_dealer_name=effective_dealer_name,
                                    layout=ext_banner_layout,
                                )
                            except Exception:
                                ext_input_banner_clustered_lines = None


                        if ext_master_unreadable:
                            st.warning(
                                "The master for this email could not be read, so its banner "
                                "was not compared with the master."
                            )

                        ext_html_body_text = html_to_visible_text(BeautifulSoup(job["html"], "html.parser"))
                        # Dealer-in-Body must only look at the greeting/body-copy
                        # portion (Dear ... up to the "BMW <Dealer>" sign-off line),
                        # NOT the dealer panel further down — that's already QA'd
                        # separately against the Excel sheet in the main report above.
                        # The dealer to look for in the banner and the body: the
                        # one named by a master or the dropdown when there is one,
                        # otherwise the dealer this email was matched to. Without
                        # this fallback both checks reported "no dealer name
                        # available" on every run that relied on auto-detection.
                        # The body slice must use the SAME name: it ends at the
                        # "BMW <Dealer>" panel heading, and without a name it ran
                        # on to the end of the email, panel included — so the
                        # dealer was always "found in the body".
                        _dealer_name_to_find = effective_dealer_name or dealer_row.dealer
                        ext_html_body_greeting_slice = extract_body_greeting_slice(ext_html_body_text, _dealer_name_to_find)

                        ext_tab_names = ["Body QA", "Banner QA", "Summary"]
                        ext_tabs = st.tabs(ext_tab_names)
                        ext_all_module_results: List[ExtModuleResult] = []

                        with ext_tabs[0]:
                            body_dealer_result = ext_body_qa.run_body_dealer_qa(
                                ext_check_dealer_in_body, _dealer_name_to_find, ext_html_body_greeting_slice
                            )
                            ext_results_ui.render_module_result(body_dealer_result, table_key=f"adv_body_dealer_{job_idx}")
                            st.divider()
                            manual_body_result = ext_body_qa.run_manual_body_comparison(
                                job_body_as_is if job_body_as_is.strip() else expected_body_text,
                                job_body_to_be if job_body_to_be.strip() else expected_body_text,
                                ext_html_body_greeting_slice,
                            )
                            ext_results_ui.render_module_result(manual_body_result, table_key=f"adv_manual_body_{job_idx}")
                            ext_all_module_results.extend([body_dealer_result, manual_body_result])

                        with ext_tabs[1]:
                            if ext_input_banner_img is None:
                                st.info(
                                    "No banner image was found for this email. Upload it as a "
                                    "model-folder .zip (index.html + images) to check the banner."
                                )
                            else:
                                # expected_headline / expected_subheadline were already
                                # resolved above from whichever Master source is active
                                # (Manual Text, or OCR'd from Master JPG/PDF/HTML-ZIP
                                # banner) — not Manual Text Mode only.
                                banner_text_result = ext_banner_text_qa.run_banner_text_qa(
                                    expected_headline, expected_subheadline, effective_dealer_name,
                                    ext_input_banner_ocr_text, EXT_CFG,
                                    clustered_lines=ext_input_banner_clustered_lines,
                                    # RapidOCR merges words on tight display type;
                                    # Tesseract/PaddleOCR do not, and keep the
                                    # original strict spacing comparison.
                                    space_insensitive=(ext_input_banner_engine == "rapidocr"),
                                    # Lets Banner Text QA recognise (and strip) a
                                    # trailing dealer name belonging to ANOTHER
                                    # dealer, which is what leaks into the
                                    # expected Headline/Subheadline when the
                                    # Master creative was built for a different
                                    # dealer than this email. Second safety net
                                    # for the same problem the candidate list
                                    # above fixes at the source.
                                    known_dealer_names=_known_dealer_names,
                                )
                                ext_results_ui.render_module_result(banner_text_result, table_key=f"adv_banner_text_{job_idx}")
                                ext_all_module_results.append(banner_text_result)

                                st.divider()
                                dealer_banner_result = ext_dealer.run_dealer_name_validation(
                                    dealer_name=_dealer_name_to_find,
                                    check_in_banner=ext_check_dealer_in_banner,
                                    check_in_body=False,
                                    banner_ocr_text=ext_input_banner_ocr_text,
                                    body_text=None,
                                )
                                ext_results_ui.render_module_result(dealer_banner_result, table_key=f"adv_dealer_banner_{job_idx}")
                                ext_all_module_results.append(dealer_banner_result)

                        with ext_tabs[2]:
                            ext_results_ui.render_summary(ext_all_module_results, table_key=f"adv_summary_{job_idx}")

                        ext_job_report.advanced_module_results = ext_all_module_results
                        if effective_dealer_name:
                            ext_job_report.dealer = effective_dealer_name

    if run and "ext_report_jobs" in dir() and ext_report_jobs:
        with ext_theme.section(
            "report", "Consolidated Excel QA Report",
            "One workbook, one sheet per email — summary, Content QA, Styling QA and "
            "Advanced QA stacked down the sheet, behind a run Overview.",
            right_html=ext_theme.chip(f"{len(ext_report_jobs)} email(s)", "brand", "file"),
        ):
            ext_workbook_bytes = ext_excel_report.build_qa_workbook(ext_report_jobs)
            st.download_button(
                "Download full QA report (Excel)",
                data=ext_workbook_bytes,
                file_name="dealer_panel_qa_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="ext_download_full_excel_report",
            )

    if run and "ext_theme_results_group_opened" in dir():
        ext_theme_results_group.__exit__(None, None, None)
