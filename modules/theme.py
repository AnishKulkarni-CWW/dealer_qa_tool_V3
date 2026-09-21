"""Visual theme — the product-console redesign.

WHAT THIS IS
------------
One place that owns every pixel of chrome: palette, typography, the dark
navigation rail, the sticky top bar, the welcome panel, the numbered
workflow cards, the stat tiles, the result tables and every Streamlit
widget restyle. Nothing in `app.py` or in any other module sets a colour,
a radius or a shadow of its own — they call the builders below.

WHY IT IS SHAPED LIKE THIS
--------------------------
Streamlit renders its own DOM, so a redesign can only be expressed as CSS
aimed at Streamlit's `data-testid` hooks plus small blocks of our own
markup injected through `st.markdown(..., unsafe_allow_html=True)`. That
makes two rules non-negotiable:

  1. **No widget may change its arguments, its key, or its return value
     to get a new look.** Every function here is presentation only. A
     card is a `st.container(border=True)` carrying a marker `<div>`;
     the CSS finds the container *through* that marker. Swap the theme
     out and the app still runs — it just looks like stock Streamlit.

  2. **Cards are identified structurally, not by a class we can set.**
     Streamlit will not put our class on its container, so each card
     renders a marker element first and the CSS selects the container
     with `:has(> [data-testid="stElementContainer"] .dq-card-mark)`.
     The `>` matters: without it an outer block also matches a nested
     card's marker and picks up a second border. The marker is its own
     class rather than `.dq-head` because `.dq-head` is ALSO used for
     plain inline headings inside expanders and tab panels, which must
     never turn their container into a card.

BACK-COMPATIBILITY
------------------
The previous theme's public surface — `inject()`, `render_hero()`,
`section()`, `sticky_bar()`, `group()` — is kept with the same signatures
and the same `oq-*` CSS class names used by `results_ui.py`, so the
redesign is a drop-in: no call site is forced to change, and any code
that still passes an old section *kind* ("content", "style", "master-b",
…) keeps working through the KINDS table below.

SECTION KINDS
-------------
A kind now picks an accent colour and a line icon for the card header
rather than a pastel fill — the redesign's cards are white, and colour is
reserved for meaning (status, accents) instead of decoration.
"""

from __future__ import annotations

import html as _html
from contextlib import contextmanager
from typing import Iterable, List, Optional, Sequence, Tuple

import streamlit as st

from . import artwork as _artwork
from .icons import data_uri as _icon_uri
from .icons import icon as _icon

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------

BRAND = "#1C69D4"          # the blue every primary action uses
BRAND_DARK = "#12529F"
BRAND_DEEP = "#0C3C7A"
BRAND_TINT = "#EAF2FE"
BRAND_LINE = "#C7DDF8"

NAVY_1 = "#081A34"         # navigation rail, top of gradient
NAVY_2 = "#102B52"
NAVY_3 = "#17396B"

CANVAS = "#F4F7FC"         # page background behind the cards
WHITE = "#FFFFFF"
CARD = "#FFFFFF"
LINE = "#E4EAF3"           # default card / table border
LINE_SOFT = "#EDF1F7"

INK = "#0F1B2D"            # headings
INK_SOFT = "#3C4A5E"       # body copy
MUTED = "#6B7A90"          # captions, labels

OK = "#067647"
OK_BG = "#E7F7EE"
OK_LINE = "#A9E5C8"
OK_SOLID = "#12B76A"

WARN = "#B54708"
WARN_BG = "#FEF6E7"
WARN_LINE = "#FBD9A1"
WARN_SOLID = "#F79009"

# The band that sits behind every section heading. A cool blue rather than
# the brand blue: it has to be obvious enough to find while scrolling past
# without reading, and quiet enough that the coloured icon tile and the
# status pills on top of it keep their meaning.
#
# The first version of this was far too polite — #EEF3FA measured 1.11:1
# against the white card it sits on, which is a tint you have to look for
# rather than one you notice. These are a step down the same hue and
# measure 1.48:1, and the heading's own subtitle is darkened to match so
# it does not lose contrast on the way (BAND_SUB below).
BAND = "#C3D6F0"
BAND_TOP = "#D3E2F6"
BAND_EDGE = "#A2BEE0"
BAND_SUB = "#455568"          # subtitle ink ON the band — 5.2:1

# The run-notes panel is NOT a section heading and must not read as one,
# so it keeps the pale tint the bands have given up.
NOTE_BG = "#F5F8FD"

# The "app is busy" bar across the very top of the window. Deliberately NOT
# the brand blue: the page is already mostly brand blue, and a progress bar
# that blends into the chrome is a progress bar nobody notices. Amber reads
# as "in flight" at a glance and holds its contrast against both the white
# top bar and the pale canvas.
BUSY = "#F59E0B"
BUSY_HOT = "#EA580C"
BUSY_TRACK = "#FCE3BE"
BUSY_INK = "#7C2D12"
BUSY_CHIP = "#FFF7ED"
BUSY_LINE = "#FDBA74"

BAD = "#B42318"
BAD_BG = "#FDECEC"
BAD_LINE = "#F7BFBB"
BAD_SOLID = "#F04438"

# Kept so any older code importing these names still resolves.
BORDER = LINE
INK_SOFT_LEGACY = INK_SOFT
BRAND_LIGHT = BRAND_TINT

RADIUS = "16px"
RADIUS_SM = "10px"
SHADOW = "0 1px 2px rgba(16,24,40,.05), 0 10px 26px -14px rgba(16,24,40,.20)"
SHADOW_LIFT = "0 2px 4px rgba(16,24,40,.06), 0 18px 40px -18px rgba(16,24,40,.28)"

# kind -> (accent colour, accent tint, icon name)
KINDS = {
    "content":  (BRAND, BRAND_TINT, "file"),
    "email":    ("#0E7490", "#E4F5F9", "upload-cloud"),
    "exact":    ("#6D28D9", "#F1EBFE", "check-square"),
    "style":    (WARN, WARN_BG, "sliders"),
    "advanced": ("#0F766E", "#E3F5F1", "layers"),
    "master-b": ("#BE185D", "#FCE9F1", "compare"),
    "master-c": ("#A16207", "#FCF3DD", "archive"),
    "routing":  ("#C2410C", "#FDEDE2", "route"),
    "multi":    ("#3730A3", "#EAEAFB", "layers"),
    "neutral":  ("#475569", "#EEF2F7", "file"),
    "report":   (OK, OK_BG, "chart"),
    "upload":   (BRAND, BRAND_TINT, "upload-cloud"),
    "options":  ("#6D28D9", "#F1EBFE", "sliders"),
    "results":  (OK, OK_BG, "chart"),
    "image":    ("#0F766E", "#E3F5F1", "image"),
    "pdf":      ("#BE185D", "#FCE9F1", "pdf"),
    "zip":      ("#A16207", "#FCF3DD", "archive"),
    "text":     ("#0E7490", "#E4F5F9", "type"),
    "help":     (BRAND, BRAND_TINT, "help"),
    "settings": ("#475569", "#EEF2F7", "settings"),
}

_ALIASES = {
    "input": "upload",
    "master": "advanced",
    "result": "results",
    "job": "neutral",
}


def _kind(name: str) -> str:
    k = (name or "").strip().lower()
    k = _ALIASES.get(k, k)
    return k if k in KINDS else "neutral"


def kind_accent(name: str) -> str:
    return KINDS[_kind(name)][0]


# --------------------------------------------------------------------------
# CSS
# --------------------------------------------------------------------------

# One entry per nav row, in the order render_sidebar is called with.
# "QA Validation" was removed: it rendered the identical workflow to Home
# minus the welcome panel, so it was a second door into the same room.
_NAV_ICONS = ["home", "settings", "help"]


def _nav_icon_rules() -> str:
    """Line icons for the navigation rail.

    Streamlit renders radio options as plain text, so a real icon can only
    be attached with `background-image` on a pseudo-element. Positional
    selectors are the only handle available, which is why the order of
    `_NAV_ICONS` must match the order of the nav items in `render_sidebar`.
    """
    out = []
    for i, name in enumerate(_NAV_ICONS, start=1):
        out.append(f"""
        section[data-testid="stSidebar"] div[role="radiogroup"] > label:nth-of-type({i})::before {{
            background-image:{_icon_uri(name, "#B9CCE6")};
        }}
        section[data-testid="stSidebar"] div[role="radiogroup"] > label:nth-of-type({i}):hover::before {{
            background-image:{_icon_uri(name, "#FFFFFF")};
        }}
        section[data-testid="stSidebar"] div[role="radiogroup"] > label:nth-of-type({i}):has(input:checked)::before,
        section[data-testid="stSidebar"] div[role="radiogroup"] > label:nth-of-type({i})[data-selected="true"]::before {{
            background-image:{_icon_uri(name, BRAND)};
        }}
        """)
    return "\n".join(out)


def _css(density: str = "comfortable") -> str:
    # The two densities have to be far enough apart to be worth a control:
    # an earlier pass differed by under 4px a row, which read on screen as
    # "this setting does nothing". Compact now drops the row height by
    # roughly a third and takes the type down with it.
    compact = density == "compact"
    row_pad = ".3rem .6rem" if compact else ".68rem .85rem"
    font_size = ".77rem" if compact else ".845rem"
    row_line = "1.35" if compact else "1.55"
    head_pad = ".38rem .6rem" if compact else ".6rem .85rem"
    status_pad = ".1rem .45rem" if compact else ".2rem .55rem"

    return f"""
<style>
/* ======================================================================
   0. Tokens
   ====================================================================== */
:root {{
    --dq-brand:{BRAND};
    --dq-brand-dark:{BRAND_DARK};
    --dq-brand-tint:{BRAND_TINT};
    --dq-brand-line:{BRAND_LINE};
    --dq-canvas:{CANVAS};
    --dq-card:{CARD};
    --dq-line:{LINE};
    --dq-line-soft:{LINE_SOFT};
    --dq-ink:{INK};
    --dq-soft:{INK_SOFT};
    --dq-muted:{MUTED};
    --dq-ok:{OK};        --dq-ok-bg:{OK_BG};     --dq-ok-line:{OK_LINE};
    --dq-warn:{WARN};    --dq-warn-bg:{WARN_BG}; --dq-warn-line:{WARN_LINE};
    --dq-bad:{BAD};      --dq-bad-bg:{BAD_BG};   --dq-bad-line:{BAD_LINE};
    --dq-radius:{RADIUS};
    --dq-radius-sm:{RADIUS_SM};
    --dq-shadow:{SHADOW};
    /* The heading scale. The welcome panel's title and the band that opens
       each section of the workspace are deliberately the SAME size, so a
       section break reads as a page break; a card title sits one clear
       step below it. Declared once here so the three can never drift. */
    --dq-title-xl:2.1rem;
    --dq-title-lg:1.45rem;
}}

/* ======================================================================
   1. Page shell
   ====================================================================== */
html, body, .stApp, [data-testid="stAppViewContainer"] {{
    background:{CANVAS};
    font-family:"Inter","Segoe UI",-apple-system,BlinkMacSystemFont,Roboto,
                "Helvetica Neue",Arial,sans-serif;
    color:{INK_SOFT};
    -webkit-font-smoothing:antialiased;
}}

/* Streamlit's own top strip is kept (it carries the sidebar re-open
   control) but flattened so the app's sticky top bar reads as the only
   header on the page. */
header[data-testid="stHeader"] {{
    background:transparent !important;
    height:0 !important;
    min-height:0 !important;
    box-shadow:none !important;
    z-index:999990;
}}
header[data-testid="stHeader"] [data-testid="stToolbar"] {{
    right:.6rem; top:.35rem;
}}
[data-testid="stDecoration"] {{ display:none !important; }}

/* ---- the running indicator ----
   Streamlit's own "Running..." chip lives in the top-right toolbar, where
   it is easy to miss on a wide screen and scrolls out of reach on a long
   report. It is replaced by an amber bar pinned across the full width of
   the very top of the viewport, with a centred "Working" chip beneath it,
   that shows for exactly as long as the app is busy.

   There is no event to hook: the chip exists in the DOM only while a run
   is in flight, so `body:has(...)` IS the "is it running" test, and the
   bar is styled off the presence of the thing it replaces. */
[data-testid="stStatusWidget"] {{ display:none !important; }}

.dq-topload {{
    position:fixed; top:0; left:0; right:0; z-index:1000001;
    display:flex; flex-direction:column; align-items:center;
    pointer-events:none;
    opacity:0; transform:translateY(-10px);
    transition:opacity .18s ease, transform .18s ease;
}}
body:has([data-testid="stStatusWidget"]) .dq-topload {{
    opacity:1; transform:none;
}}
/* Edge to edge and flush with the very top of the window: this is the one
   thing on the page that has to be seen without being looked for, so it
   gets the full width of the screen rather than a centred sliver. */
.dq-topload-track {{
    width:100%; height:6px;
    background:{BUSY_TRACK}; overflow:hidden;
    box-shadow:0 1px 10px -1px rgba(234,88,12,.55);
}}
.dq-topload-bar {{
    height:100%; width:30%;
    background:linear-gradient(90deg,
        rgba(245,158,11,0) 0%, {BUSY} 30%, {BUSY_HOT} 55%,
        {BUSY} 78%, rgba(245,158,11,0) 100%);
    animation:dq-topload-slide 1.15s ease-in-out infinite;
}}
@keyframes dq-topload-slide {{
    0%   {{ transform:translateX(-120%); }}
    100% {{ transform:translateX(450%); }}
}}
.dq-topload-text {{
    margin-top:.38rem;
    font-size:.685rem; font-weight:800; letter-spacing:.1em; text-transform:uppercase;
    color:{BUSY_INK}; background:{BUSY_CHIP};
    border:1px solid {BUSY_LINE}; border-radius:999px; padding:.16rem .68rem;
    box-shadow:0 3px 10px -3px rgba(234,88,12,.5);
}}
/* The element is `fixed`, so it contributes no height of its own; this
   stops its (empty) Streamlit container from adding a gap to the page. */
[data-testid="stElementContainer"]:has(> .dq-topload) {{
    height:0 !important; min-height:0 !important; margin:0 !important;
}}
@media (prefers-reduced-motion: reduce) {{
    .dq-topload-bar {{ animation-duration:2.4s; }}
}}
[data-testid="stAppDeployButton"], [data-testid="stAppDeployButton"] + div {{ display:none !important; }}
footer, #MainMenu {{ visibility:hidden; }}

.block-container {{
    padding-top:1.1rem !important;
    padding-bottom:3.5rem !important;
    padding-left:1.9rem;
    padding-right:1.9rem;
    max-width:1480px;
}}

h1,h2,h3,h4,h5,
[data-testid="stHeading"] h1,[data-testid="stHeading"] h2,[data-testid="stHeading"] h3 {{
    color:{INK} !important; font-weight:700 !important; letter-spacing:-.015em;
}}
h1,[data-testid="stHeading"] h1 {{ font-size:1.45rem !important; }}
h2,[data-testid="stHeading"] h2 {{ font-size:1.12rem !important; }}
h3,[data-testid="stHeading"] h3 {{ font-size:.98rem !important; padding-top:0 !important; }}
[data-testid="stCaptionContainer"], .stCaption, [data-testid="stCaptionContainer"] p {{
    color:{MUTED} !important; font-size:.795rem !important; line-height:1.55;
}}
hr, [data-testid="stDivider"] {{ border-color:{LINE_SOFT} !important; }}

/* ======================================================================
   2. Navigation rail (sidebar)
   ====================================================================== */
section[data-testid="stSidebar"] {{
    background:linear-gradient(176deg,{NAVY_1} 0%,{NAVY_2} 52%,{NAVY_3} 100%);
    border-right:1px solid rgba(255,255,255,.06);
    width:268px !important;
}}
section[data-testid="stSidebar"] > div {{ background:transparent; }}
section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {{
    padding:1.15rem .95rem 1rem .95rem;
    background:transparent;
}}
section[data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] svg,
section[data-testid="stSidebar"] button[kind="header"] svg {{ color:#CFE0F5 !important; }}
section[data-testid="stSidebar"] * {{ color:#E8F0FA; }}

.dq-brandmark {{
    display:flex; align-items:center; gap:.65rem;
    padding:.15rem .25rem 1.05rem .25rem;
    border-bottom:1px solid rgba(255,255,255,.09);
    margin-bottom:.95rem;
}}
.dq-brandmark-tile {{
    flex:none; width:38px; height:38px; border-radius:11px;
    background:linear-gradient(145deg,{BRAND} 0%,{BRAND_DEEP} 100%);
    display:flex; align-items:center; justify-content:center; color:#fff;
    box-shadow:0 6px 16px -6px rgba(28,105,212,.85);
}}
.dq-brandmark-name {{
    font-size:.93rem; font-weight:750; color:#FFFFFF; line-height:1.15;
    letter-spacing:-.01em;
}}
.dq-brandmark-tag {{
    font-size:.665rem; color:#9FBBDD; letter-spacing:.02em; margin-top:.15rem;
}}

/* ---- the primary action, at the head of the navigation rail ----
   Run QA used to sit in the Validation Options card, which is fine until
   the run produces a report several thousand pixels long: re-running then
   means scrolling all the way back up to find the button. The rail does
   not scroll, so the button is always in reach from wherever you are.

   It is styled here rather than inheriting the main-area primary button
   because the rail is dark navy and the page is not — the same gradient
   that reads as "primary" on a pale canvas nearly disappears on it. This
   one is a step lighter at the top, carries an inner highlight, and is
   bordered in translucent white so it lifts off the navy. */
.dq-side-action {{ height:0; }}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"]:has(
    > [data-testid="stElementContainer"] .dq-side-action) {{
    gap:.35rem;
    padding:0 0 1rem 0;
    margin:-.15rem 0 .95rem 0;
    border-bottom:1px solid rgba(255,255,255,.09);
}}
/* Matched WITHOUT the usual `.stButton >` parent: passing `help=` to the
   button wraps it in a tooltip target instead, and the child combinator
   then quietly matches nothing at all — which is how this shipped once
   already, looking correct only because Streamlit's own primary colour
   happens to be the same blue. */
[data-testid="stSidebar"] .stTooltipHoverTarget {{ width:100%; }}
[data-testid="stSidebar"] button[kind="primary"],
[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] {{
    width:100%;
    background:linear-gradient(180deg,#3688F2 0%,{BRAND} 52%,{BRAND_DARK} 100%);
    border:1px solid rgba(255,255,255,.24);
    color:#FFFFFF !important;
    font-weight:750; font-size:.86rem;
    min-height:2.6rem; border-radius:11px;
    /* No drop glow. It read as a smear of light under the button on the
       navy rail rather than as depth. */
    box-shadow:none;
}}
[data-testid="stSidebar"] button[kind="primary"] *,
[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] * {{
    color:#FFFFFF !important;
}}
[data-testid="stSidebar"] button[kind="primary"]:hover,
[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"]:hover {{
    background:linear-gradient(180deg,#4E9BF7 0%,#2374DE 52%,{BRAND_DARK} 100%);
    border-color:rgba(255,255,255,.38);
}}
[data-testid="stSidebar"] button[kind="primary"]:focus:not(:active),
[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"]:focus:not(:active) {{
    box-shadow:0 0 0 3px rgba(95,165,245,.45);
}}
/* The readiness line under it: what this run would actually cover. */
.dq-side-ready {{
    display:flex; flex-wrap:wrap; gap:.3rem .5rem;
    font-size:.655rem; letter-spacing:.02em; line-height:1.3;
    padding:.12rem .12rem 0 .12rem;
}}
.dq-side-ready span {{
    display:inline-flex; align-items:center; gap:.25rem;
    color:#9FBBDD;
}}
.dq-side-ready span.ok b {{ color:#BFE3CF; font-weight:650; }}
.dq-side-ready span.warn b {{ color:#FFD79A; font-weight:650; }}
.dq-side-ready b {{ font-weight:650; }}

.dq-navlabel {{
    font-size:.62rem; font-weight:800; letter-spacing:.15em;
    text-transform:uppercase; color:#7C9AC0; margin:.15rem .35rem .45rem .35rem;
}}

/* the radio group IS the nav list */
section[data-testid="stSidebar"] div[role="radiogroup"] {{ gap:.25rem !important; }}
section[data-testid="stSidebar"] div[role="radiogroup"] > label {{
    position:relative;
    display:flex; align-items:center;
    padding:.6rem .8rem .6rem 2.65rem;
    border-radius:11px; cursor:pointer;
    background:transparent; border:1px solid transparent;
    transition:background .13s ease, color .13s ease;
    margin:0 !important;
}}
section[data-testid="stSidebar"] div[role="radiogroup"] > label::before {{
    content:""; position:absolute; left:.85rem; top:50%;
    width:19px; height:19px; margin-top:-9.5px;
    background-repeat:no-repeat; background-size:19px 19px;
}}
/* Hide the stock radio dot. Streamlit wraps it two levels deep as the
   sibling that precedes the option's label text, so it is selected by
   "the child of the option row that is not the markdown container"
   rather than by a position that a future release could shuffle. */
section[data-testid="stSidebar"] label[data-testid="stRadioOption"] > div > div > div:not([data-testid="stMarkdownContainer"]) {{
    display:none !important;
}}
section[data-testid="stSidebar"] div[role="radiogroup"] > label p {{
    font-size:.855rem !important; font-weight:600 !important;
    color:#C3D6EC !important; margin:0 !important;
}}
section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {{
    background:rgba(255,255,255,.08);
}}
section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover p {{ color:#FFFFFF !important; }}
section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked),
section[data-testid="stSidebar"] div[role="radiogroup"] > label[data-selected="true"] {{
    background:#FFFFFF;
    box-shadow:0 6px 18px -10px rgba(0,0,0,.8);
}}
section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) p,
section[data-testid="stSidebar"] div[role="radiogroup"] > label[data-selected="true"] p {{
    color:{BRAND} !important; font-weight:700 !important;
}}
{_nav_icon_rules()}

/* The rail's lower half: a rule, a group label, and the view switches
   app.py puts there through sidebar_prefs(). */
.dq-side-prefs {{
    margin-top:1.15rem; padding-top:.1rem;
    border-top:1px solid rgba(255,255,255,.10);
}}
.dq-side-prefs-label {{ margin-top:.85rem !important; }}
/* st.toggle renders under the stCheckbox test id in this Streamlit line
   (the switch is marked by role="switch" on the input, not by a test id
   of its own), so both hooks are listed — stToggle for the day a release
   introduces it, stCheckbox for today. */
section[data-testid="stSidebar"] [data-testid="stToggle"] label,
section[data-testid="stSidebar"] [data-testid="stToggle"] label p,
section[data-testid="stSidebar"] [data-testid="stCheckbox"] label,
section[data-testid="stSidebar"] [data-testid="stCheckbox"] label p {{
    color:#C3D6EC !important; font-size:.81rem !important; font-weight:600 !important;
}}
section[data-testid="stSidebar"] [data-testid="stToggle"],
section[data-testid="stSidebar"] [data-testid="stCheckbox"] {{ padding:.1rem .25rem; }}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{
    color:#8FAACB !important; font-size:.7rem !important; padding:0 .25rem;
}}

/* ======================================================================
   3. Sticky top bar
   ====================================================================== */
.dq-topbar {{
    position:sticky; top:0; z-index:99;
    display:flex; align-items:center; gap:.85rem;
    background:rgba(255,255,255,.93);
    backdrop-filter:blur(10px);
    border:1px solid {LINE};
    border-radius:14px;
    padding:.62rem .95rem;
    margin:0 0 1.05rem 0;
    box-shadow:0 1px 2px rgba(16,24,40,.04);
}}
.dq-topbar-tile {{
    flex:none; width:38px; height:38px; border-radius:11px;
    background:linear-gradient(145deg,{BRAND} 0%,{BRAND_DEEP} 100%);
    display:flex; align-items:center; justify-content:center; color:#fff;
}}
.dq-topbar-title {{ font-size:1rem; font-weight:750; color:{INK}; line-height:1.2; }}
.dq-topbar-sub {{ font-size:.735rem; color:{MUTED}; margin-top:.08rem; }}
.dq-topbar-spacer {{ flex:1 1 auto; }}
.dq-topbar-chips {{ display:flex; align-items:center; gap:.45rem; flex-wrap:wrap; justify-content:flex-end; }}

.dq-chip {{
    display:inline-flex; align-items:center; gap:.35rem;
    font-size:.705rem; font-weight:650; letter-spacing:.01em;
    padding:.3rem .6rem; border-radius:999px;
    border:1px solid {LINE}; background:{WHITE}; color:{INK_SOFT};
    white-space:nowrap;
}}
.dq-chip svg {{ width:13px; height:13px; }}
.dq-chip.brand {{ background:{BRAND_TINT}; border-color:{BRAND_LINE}; color:{BRAND_DARK}; }}
.dq-chip.ok    {{ background:{OK_BG};   border-color:{OK_LINE};   color:{OK}; }}
.dq-chip.warn  {{ background:{WARN_BG}; border-color:{WARN_LINE}; color:{WARN}; }}
.dq-chip.bad   {{ background:{BAD_BG};  border-color:{BAD_LINE};  color:{BAD}; }}
.dq-chip.ghost {{ background:{CANVAS}; }}

/* ======================================================================
   4. Welcome panel
   ====================================================================== */
.dq-hero {{
    position:relative; overflow:hidden;
    border:1px solid {LINE}; border-radius:20px;
    background:linear-gradient(103deg,#FFFFFF 0%,#FBFDFF 38%,#EEF5FE 100%);
    padding:1.7rem 1.9rem 1.55rem 1.9rem;
    margin:0 0 1.15rem 0;
    box-shadow:{SHADOW};
    min-height:236px;
}}
.dq-hero-art {{
    position:absolute; inset:0 0 0 auto; height:100%; width:66%;
    z-index:0; pointer-events:none;
}}
.dq-hero-inner {{
    position:relative; z-index:1; display:flex; gap:2rem;
    align-items:center; justify-content:space-between;
}}
.dq-hero-main {{ flex:1 1 auto; min-width:0; max-width:660px; }}
.dq-hero-eyebrow {{
    font-size:.665rem; font-weight:800; letter-spacing:.19em; text-transform:uppercase;
    color:{BRAND}; margin-bottom:.45rem;
}}
.dq-hero-title {{
    font-size:var(--dq-title-xl); font-weight:800; color:{INK}; line-height:1.08;
    letter-spacing:-.03em;
}}
.dq-hero-sub {{
    font-size:.9rem; color:{INK_SOFT}; margin-top:.55rem; line-height:1.6; max-width:62ch;
}}
.dq-hero-feats {{ display:flex; gap:1.6rem; flex-wrap:wrap; margin-top:1.25rem; }}
.dq-hero-feat {{ display:flex; align-items:center; gap:.6rem; }}
.dq-hero-feat-ico {{
    flex:none; width:36px; height:36px; border-radius:11px;
    background:{BRAND_TINT}; border:1px solid {BRAND_LINE}; color:{BRAND};
    display:flex; align-items:center; justify-content:center;
}}
.dq-hero-feat-t {{ font-size:.805rem; font-weight:700; color:{INK}; line-height:1.2; }}
.dq-hero-feat-s {{ font-size:.715rem; color:{MUTED}; margin-top:.1rem; }}
.dq-hero-side {{ flex:none; text-align:right; padding-left:1.5rem; }}
.dq-hero-side-line {{
    font-size:.735rem; font-weight:700; letter-spacing:.2em;
    text-transform:uppercase; color:{INK_SOFT}; line-height:2;
    text-shadow:0 1px 0 rgba(255,255,255,.9);
}}
.dq-hero-side-rule {{
    width:56px; height:3px; border-radius:2px; background:{BRAND};
    margin:.65rem 0 0 auto;
}}

/* ======================================================================
   5. Stepper
   ====================================================================== */
.dq-stepper {{
    display:flex; align-items:stretch; gap:0;
    background:{WHITE}; border:1px solid {LINE}; border-radius:{RADIUS};
    padding:.85rem 1.1rem; margin:0 0 1.15rem 0; box-shadow:{SHADOW};
    overflow-x:auto;
}}
.dq-step {{ display:flex; align-items:center; gap:.65rem; flex:0 0 auto; }}
.dq-step-num {{
    flex:none; width:32px; height:32px; border-radius:50%;
    display:flex; align-items:center; justify-content:center;
    font-size:.8rem; font-weight:750;
    background:{CANVAS}; color:{MUTED}; border:1.5px solid {LINE};
}}
.dq-step.active .dq-step-num {{ background:{BRAND}; color:#fff; border-color:{BRAND};
    box-shadow:0 0 0 4px {BRAND_TINT}; }}
.dq-step.done .dq-step-num {{ background:{OK_BG}; color:{OK}; border-color:{OK_LINE}; }}
.dq-step-t {{ font-size:.815rem; font-weight:700; color:{MUTED}; line-height:1.2; white-space:nowrap; }}
.dq-step.active .dq-step-t, .dq-step.done .dq-step-t {{ color:{INK}; }}
.dq-step-s {{ font-size:.695rem; color:{MUTED}; margin-top:.12rem; white-space:nowrap; }}
.dq-step-bar {{
    flex:1 1 auto; min-width:24px; height:2px; border-radius:2px;
    background:{LINE}; margin:0 .9rem; align-self:center;
}}
.dq-step-bar.done {{ background:{OK_LINE}; }}

/* ======================================================================
   6. Cards
   ====================================================================== */
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .dq-card-mark) {{
    background:{CARD} !important;
    border:1px solid {LINE} !important;
    border-radius:{RADIUS} !important;
    padding:1.15rem 1.25rem 1.2rem 1.25rem !important;
    box-shadow:{SHADOW} !important;
    gap:.65rem !important;
}}
/* Streamlit's legacy outer wrapper would otherwise draw a second border
   around the same card on versions that still emit it. */
[data-testid="stVerticalBlockBorderWrapper"]:has([data-testid="stElementContainer"] .dq-card-mark) {{
    background:transparent !important; border:none !important;
    box-shadow:none !important; padding:0 !important;
}}

/* Card headings are centred too, one clear step below a section band, so
   every card announces itself at a glance instead of having to be read.

   Each one sits on a tinted band running the full width of whatever holds
   it. On a report that scrolls for thousands of pixels the title alone is
   not enough — you have to read it to know a new section has started. A
   band is visible in peripheral vision, so scrolling past one registers
   without looking directly at it. */
.dq-head {{
    display:flex; align-items:center; justify-content:center;
    text-align:center; gap:.7rem; flex-wrap:wrap;
    margin:0 0 .75rem 0; padding:.8rem 1rem .75rem 1rem;
    background:linear-gradient(180deg,{BAND_TOP} 0%,{BAND} 100%);
    border:1px solid {BAND_EDGE}; border-radius:12px;
}}
/* Inside a card the band goes edge to edge, cancelling the card's own
   padding, and takes the card's top corners as its own. */
.dq-head:has(.dq-card-mark) {{
    margin:-1.15rem -1.25rem 1rem -1.25rem;
    padding:.95rem 1.25rem .9rem 1.25rem;
    border-width:0 0 1px 0;
    border-radius:{RADIUS} {RADIUS} 0 0;
}}
.dq-card-mark {{ display:none; }}
.dq-head-ico {{
    flex:none; width:42px; height:42px; border-radius:12px;
    display:flex; align-items:center; justify-content:center;
}}
.dq-head-step {{
    flex:none; width:36px; height:36px; border-radius:50%;
    background:{BRAND}; color:#fff; font-size:.92rem; font-weight:750;
    display:flex; align-items:center; justify-content:center;
    box-shadow:0 0 0 4px {BRAND_TINT};
}}
.dq-head-txt {{ min-width:0; flex:0 1 auto; }}
.dq-head-title {{
    font-size:var(--dq-title-lg); font-weight:780; color:{INK};
    line-height:1.2; letter-spacing:-.02em;
}}
.dq-head-sub {{
    font-size:.8rem; color:{BAND_SUB}; margin-top:.25rem; line-height:1.5;
    max-width:62ch; margin-left:auto; margin-right:auto;
}}
.dq-head-right {{ flex:none; display:flex; align-items:center; gap:.4rem; }}

/* The band that opens a section of the workspace. Centred and set at the
   welcome panel's own title size so that scrolling past one is
   unmistakably "a new part of the page starts here". */
.dq-sectitle {{
    display:flex; flex-direction:column; align-items:center; text-align:center;
    gap:.25rem; margin:2.1rem 0 1.05rem 0;
}}
.dq-sectitle-t {{
    font-size:var(--dq-title-xl); font-weight:800; color:{INK};
    letter-spacing:-.03em; line-height:1.12;
}}
.dq-sectitle-s {{ font-size:.85rem; color:{MUTED}; max-width:70ch; }}
.dq-sectitle-rule {{
    width:84px; height:3px; border-radius:2px; background:{BRAND};
    margin-top:.5rem; flex:none;
}}
.dq-sectitle svg {{ display:none; }}   /* the rule carries the accent now */

/* ======================================================================
   7. Stat tiles
   ====================================================================== */
.dq-stats {{ display:flex; gap:.8rem; flex-wrap:wrap; margin:.35rem 0 .9rem 0; }}
.dq-stat {{
    flex:1 1 170px; min-width:160px;
    display:flex; align-items:center; gap:.75rem;
    border-radius:14px; padding:.85rem .95rem; border:1px solid transparent;
}}
.dq-stat-ico {{
    flex:none; width:40px; height:40px; border-radius:11px;
    background:rgba(255,255,255,.92);
    display:flex; align-items:center; justify-content:center;
}}
.dq-stat-l {{ font-size:.73rem; font-weight:700; letter-spacing:.01em; }}
.dq-stat-v {{ font-size:1.55rem; font-weight:800; line-height:1.1; letter-spacing:-.02em; }}
.dq-stat-x {{ font-size:.685rem; opacity:.85; margin-top:.1rem; }}
.dq-stat.total {{ background:{BRAND_TINT}; border-color:{BRAND_LINE}; }}
.dq-stat.total .dq-stat-ico {{ color:{BRAND}; }}
.dq-stat.total .dq-stat-l, .dq-stat.total .dq-stat-x {{ color:{BRAND_DARK}; }}
.dq-stat.total .dq-stat-v {{ color:{BRAND_DEEP}; }}
.dq-stat.ok {{ background:{OK_BG}; border-color:{OK_LINE}; }}
.dq-stat.ok .dq-stat-ico {{ color:{OK_SOLID}; }}
.dq-stat.ok .dq-stat-l, .dq-stat.ok .dq-stat-x, .dq-stat.ok .dq-stat-v {{ color:{OK}; }}
.dq-stat.bad {{ background:{BAD_BG}; border-color:{BAD_LINE}; }}
.dq-stat.bad .dq-stat-ico {{ color:{BAD_SOLID}; }}
.dq-stat.bad .dq-stat-l, .dq-stat.bad .dq-stat-x, .dq-stat.bad .dq-stat-v {{ color:{BAD}; }}
.dq-stat.warn {{ background:{WARN_BG}; border-color:{WARN_LINE}; }}
.dq-stat.warn .dq-stat-ico {{ color:{WARN_SOLID}; }}
.dq-stat.warn .dq-stat-l, .dq-stat.warn .dq-stat-x, .dq-stat.warn .dq-stat-v {{ color:{WARN}; }}
.dq-stat.neutral {{ background:{CANVAS}; border-color:{LINE}; }}
.dq-stat.neutral .dq-stat-ico {{ color:{MUTED}; }}
.dq-stat.neutral .dq-stat-l, .dq-stat.neutral .dq-stat-x {{ color:{MUTED}; }}
.dq-stat.neutral .dq-stat-v {{ color:{INK}; }}

/* ======================================================================
   8. Buttons
   ====================================================================== */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
    border-radius:10px; font-weight:650; font-size:.85rem;
    border:1px solid {LINE}; background:{WHITE}; color:{INK_SOFT};
    padding:.48rem .95rem; min-height:2.45rem;
    transition:background .12s ease, border-color .12s ease, color .12s ease,
               box-shadow .12s ease, transform .12s ease;
    box-shadow:0 1px 2px rgba(16,24,40,.04);
}}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {{
    background:{BRAND_TINT}; border-color:{BRAND_LINE}; color:{BRAND_DARK};
}}
.stButton > button:active {{ transform:translateY(1px); }}
.stButton > button:focus:not(:active) {{ color:{BRAND_DARK}; border-color:{BRAND}; box-shadow:0 0 0 3px {BRAND_TINT}; }}
.stButton > button[kind="primary"], .stButton > button[data-testid="baseButton-primary"],
.stFormSubmitButton > button[kind="primary"] {{
    background:linear-gradient(180deg,{BRAND} 0%,{BRAND_DARK} 100%);
    border-color:{BRAND_DARK}; color:#FFFFFF !important; font-weight:700;
    box-shadow:0 8px 20px -10px rgba(28,105,212,.9);
}}
.stButton > button[kind="primary"] p, .stButton > button[kind="primary"] div {{ color:#FFFFFF !important; }}
.stButton > button[kind="primary"]:hover {{
    background:linear-gradient(180deg,{BRAND_DARK} 0%,{BRAND_DEEP} 100%);
    border-color:{BRAND_DEEP}; color:#FFFFFF !important;
}}
.stDownloadButton > button {{
    background:linear-gradient(180deg,{BRAND} 0%,{BRAND_DARK} 100%);
    border-color:{BRAND_DARK}; color:#FFFFFF !important; font-weight:700;
    box-shadow:0 8px 20px -10px rgba(28,105,212,.9);
}}
.stDownloadButton > button * {{ color:#FFFFFF !important; }}
.stDownloadButton > button:hover {{
    background:linear-gradient(180deg,{BRAND_DARK} 0%,{BRAND_DEEP} 100%);
    border-color:{BRAND_DEEP};
}}

/* ======================================================================
   9. Inputs
   ====================================================================== */
label, .stTextInput label, .stTextArea label, .stSelectbox label,
.stNumberInput label, .stFileUploader label, .stRadio label[data-testid="stWidgetLabel"] {{
    font-weight:650 !important; color:{INK} !important;
    font-size:.79rem !important; letter-spacing:.005em;
}}
[data-testid="stWidgetLabel"] p {{ font-size:.79rem !important; font-weight:650 !important; color:{INK} !important; }}

/* Text / number / area / select fields.
   Streamlit 1.6x moved these off BaseWeb onto its own
   `st*RootElement` / `st*Container` test ids and paints them with the
   theme's secondaryBackgroundColor, which is why they came through as
   grey boxes on a white card. Both generations of hook are listed so the
   theme holds on older Streamlit too. */
div[data-baseweb="input"] > div,
div[data-baseweb="select"] > div,
div[data-baseweb="base-input"],
div[data-baseweb="textarea"],
[data-testid="stTextInputRootElement"],
[data-testid="stTextAreaRootElement"],
[data-testid="stNumberInputContainer"],
[data-testid="stSelectboxRootElement"],
.stSelectbox div[data-baseweb="select"] > div,
.stTextArea textarea,
.stNumberInput input {{
    border-radius:10px !important;
    border:1px solid {LINE} !important;
    background:{WHITE} !important;
    font-size:.85rem !important;
    box-shadow:none !important;
}}
[data-testid="stTextInputRootElement"] input,
[data-testid="stTextAreaRootElement"] textarea,
[data-testid="stNumberInputContainer"] input {{
    background:transparent !important; color:{INK} !important;
}}
div[data-baseweb="input"] > div:hover,
div[data-baseweb="select"] > div:hover,
[data-testid="stTextInputRootElement"]:hover,
[data-testid="stTextAreaRootElement"]:hover,
[data-testid="stNumberInputContainer"]:hover {{
    border-color:{BRAND_LINE} !important;
}}
div[data-baseweb="input"] > div:focus-within,
div[data-baseweb="select"] > div:focus-within,
[data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stNumberInputContainer"]:focus-within,
.stTextArea textarea:focus {{
    border-color:{BRAND} !important; box-shadow:0 0 0 3px {BRAND_TINT} !important;
}}
div[data-baseweb="popover"] li[role="option"] {{ font-size:.85rem; }}
div[data-baseweb="popover"] li[aria-selected="true"] {{ background:{BRAND_TINT} !important; color:{BRAND_DARK} !important; }}

/* file uploader — the dashed drop zone from the reference design.
   Streamlit 1.6x renders the zone as
       <section stFileUploaderDropzone>
         <span><button>Upload</button></span>
         <div stFileUploaderDropzoneInstructions>1GB per file • XLSX</div>
       </section>
   which is a compact single row with the button first. The rules below
   turn it into the reference's centred column — cloud mark, prompt,
   accepted formats, then the browse button — using `order` rather than
   moving any DOM, and adding the prompt line with generated content
   because this Streamlit version does not render one of its own. */
[data-testid="stFileUploader"] section,
[data-testid="stFileUploaderDropzone"] {{
    position:relative;
    background:linear-gradient(180deg,#FCFDFF 0%,#F2F7FE 100%) !important;
    border:1.5px dashed {BRAND_LINE} !important;
    border-radius:14px !important;
    padding:1.15rem 1rem 1.2rem 1rem !important;
    display:flex !important;
    flex-direction:column !important;
    align-items:center !important;
    justify-content:center !important;
    text-align:center !important;
    gap:.45rem !important;
    transition:border-color .14s ease, background .14s ease;
}}
[data-testid="stFileUploaderDropzone"]::before {{
    content:"";
    width:38px; height:38px; flex:none;
    background-image:{_icon_uri("upload-cloud", BRAND, 1.6)};
    background-repeat:no-repeat; background-size:38px 38px;
}}
[data-testid="stFileUploaderDropzone"]:hover {{
    border-color:{BRAND} !important;
    background:linear-gradient(180deg,#FFFFFF 0%,{BRAND_TINT} 100%) !important;
}}
[data-testid="stFileUploaderDropzoneInstructions"] {{
    order:2 !important;
    align-items:center !important; text-align:center !important;
    flex-direction:column !important; gap:.1rem !important;
    color:{MUTED} !important; padding:0 !important;
}}
[data-testid="stFileUploaderDropzoneInstructions"]::before {{
    content:"Drag and drop your file here";
    display:block; font-size:.87rem; font-weight:700; color:{INK};
    margin-bottom:.15rem;
}}
[data-testid="stFileUploaderDropzoneInstructions"] span,
[data-testid="stFileUploaderDropzoneInstructions"] small {{
    font-size:.715rem !important; color:{MUTED} !important; font-weight:500 !important;
}}
[data-testid="stFileUploaderDropzone"] > span {{ order:3 !important; }}
/* Only the BROWSE button gets the primary treatment. The chip's own
   remove / add-more controls live inside the same dropzone and would
   otherwise inherit it, turning a pair of quiet icon buttons into two
   bright blue pills. */
[data-testid="stFileUploaderDropzone"] button[data-testid="stBaseButton-secondary"] {{
    background:linear-gradient(180deg,{BRAND} 0%,{BRAND_DARK} 100%) !important;
    border:1px solid {BRAND_DARK} !important; color:#fff !important;
    border-radius:9px !important; font-weight:700 !important; font-size:.82rem !important;
    padding:.42rem 1.1rem !important; min-height:2.2rem !important;
    box-shadow:0 8px 18px -10px rgba(28,105,212,.9) !important;
}}
[data-testid="stFileUploaderDropzone"] button[data-testid="stBaseButton-secondary"] * {{ color:#fff !important; }}
[data-testid="stFileUploaderDropzone"] button[data-testid="stBaseButton-secondary"]:hover {{
    background:linear-gradient(180deg,{BRAND_DARK} 0%,{BRAND_DEEP} 100%) !important;
}}

/* the chips listing the files already chosen */
[data-testid="stFileChips"] {{ order:1 !important; width:100%; }}
[data-testid="stFileChip"] {{
    background:{WHITE} !important;
    border:1px solid {LINE} !important;
    border-radius:10px !important;
    padding:.3rem .45rem !important;
    box-shadow:0 1px 2px rgba(16,24,40,.05) !important;
}}
[data-testid="stFileChip"] > div:first-child {{
    background:{BRAND_TINT} !important; color:{BRAND} !important;
    border-radius:8px !important;
}}
[data-testid="stFileChip"] svg {{ color:{BRAND} !important; fill:{BRAND} !important; }}
[data-testid="stFileChipName"] {{
    color:{INK} !important; font-weight:650 !important; font-size:.8rem !important;
}}
[data-testid="stFileChip"] button,
[data-testid="stFileChipDeleteBtn"] {{
    background:transparent !important; border:none !important;
    box-shadow:none !important; color:{MUTED} !important;
    min-height:auto !important; padding:.15rem !important;
}}
[data-testid="stFileChip"] button svg {{ color:{MUTED} !important; fill:{MUTED} !important; }}
[data-testid="stFileChip"] button:hover svg {{ color:{BAD} !important; fill:{BAD} !important; }}
[data-testid="stFileUploaderFile"] {{
    background:{WHITE}; border:1px solid {LINE}; border-radius:11px;
    padding:.5rem .65rem; margin-top:.4rem;
}}
[data-testid="stFileUploaderFileName"] {{ font-size:.8rem !important; color:{INK} !important; font-weight:600; }}

/* checkbox + toggle */
[data-testid="stCheckbox"] label, [data-testid="stToggle"] label {{
    font-size:.83rem !important; font-weight:600 !important; color:{INK_SOFT} !important;
}}
[data-testid="stCheckbox"] label p, [data-testid="stToggle"] label p {{
    font-size:.83rem !important; font-weight:600 !important; color:{INK_SOFT} !important;
}}

/* main-area radio rendered as a segmented control */
[data-testid="stMain"] [data-testid="stRadio"] {{ width:100% !important; }}
[data-testid="stMain"] div[role="radiogroup"] {{
    display:flex !important;
    flex-direction:row !important;   /* Streamlit stacks radio options */
    gap:.35rem; flex-wrap:wrap; width:100%;
    background:{CANVAS}; border:1px solid {LINE};
    border-radius:12px; padding:.3rem;
}}
/* `flex:1 1 0` with `min-width:0` let an option shrink below its own
   text, and the nowrap label then painted outside the grey pill
   ("...s) (.zip)" hanging off the right edge). Sizing from the content
   instead means the row grows to fit, and wraps onto a second line when
   the card is too narrow, rather than overflowing. */
[data-testid="stMain"] div[role="radiogroup"] > label {{
    flex:1 1 auto; min-width:max-content; justify-content:center; text-align:center;
    padding:.45rem .9rem; border-radius:9px; margin:0 !important;
    background:transparent; cursor:pointer; transition:all .12s ease;
    border:1px solid transparent; overflow:hidden;
}}
[data-testid="stMain"] label[data-testid="stRadioOption"] > div > div > div:not([data-testid="stMarkdownContainer"]) {{
    display:none !important;
}}
[data-testid="stMain"] div[role="radiogroup"] > label p {{
    font-size:.815rem !important; font-weight:650 !important; color:{MUTED} !important;
    margin:0 !important; white-space:nowrap;
}}
[data-testid="stMain"] div[role="radiogroup"] > label:hover {{ background:{WHITE}; }}
[data-testid="stMain"] div[role="radiogroup"] > label:has(input:checked),
[data-testid="stMain"] div[role="radiogroup"] > label[data-selected="true"] {{
    background:{WHITE}; border-color:{BRAND_LINE};
    box-shadow:0 1px 3px rgba(16,24,40,.10);
}}
[data-testid="stMain"] div[role="radiogroup"] > label:has(input:checked) p,
[data-testid="stMain"] div[role="radiogroup"] > label[data-selected="true"] p {{
    color:{BRAND_DARK} !important; font-weight:700 !important;
}}

/* ======================================================================
   10. Alerts, expanders, tabs, metrics, dataframes
   ====================================================================== */
[data-testid="stAlert"] {{
    border-radius:12px; border:1px solid {LINE}; padding:.7rem .9rem;
    font-size:.82rem; box-shadow:none;
}}
[data-testid="stAlert"] p {{ font-size:.82rem; }}
[data-testid="stAlertContentSuccess"], div[data-testid="stAlert"][data-baseweb="notification"] {{ }}

[data-testid="stExpander"] {{
    border:1px solid {LINE} !important; border-radius:14px !important;
    background:{WHITE}; box-shadow:0 1px 2px rgba(16,24,40,.04); overflow:hidden;
}}
[data-testid="stExpander"] summary {{
    font-weight:700 !important; color:{INK} !important; font-size:.875rem !important;
    padding:.75rem .95rem !important; background:{WHITE};
}}
[data-testid="stExpander"] summary:hover {{ background:{BRAND_TINT}; color:{BRAND_DARK} !important; }}
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {{ padding:.2rem .95rem 1rem .95rem; }}

.stTabs [data-baseweb="tab-list"] {{
    gap:.15rem; border-bottom:1px solid {LINE}; padding-bottom:0;
}}
.stTabs [data-baseweb="tab"] {{
    font-weight:650; color:{MUTED}; padding:.55rem .95rem;
    font-size:.845rem; border-radius:9px 9px 0 0;
}}
.stTabs [data-baseweb="tab"]:hover {{ background:{CANVAS}; color:{INK}; }}
.stTabs [aria-selected="true"] {{ color:{BRAND} !important; font-weight:750; }}
.stTabs [data-baseweb="tab-highlight"] {{ background:{BRAND} !important; height:2.5px; }}
.stTabs [data-baseweb="tab-border"] {{ background:{LINE} !important; }}

[data-testid="stMetric"] {{
    background:{WHITE}; border:1px solid {LINE};
    border-radius:12px; padding:.7rem .9rem;
}}
[data-testid="stMetricLabel"] {{
    font-size:.685rem !important; font-weight:750 !important;
    letter-spacing:.08em; text-transform:uppercase; color:{MUTED} !important;
}}
[data-testid="stMetricValue"] {{
    font-size:1.5rem !important; font-weight:800 !important; color:{INK} !important;
}}

[data-testid="stDataFrame"] {{ width:100% !important; border-radius:12px; overflow:hidden; }}
[data-testid="stDataFrame"] > div {{ overflow:auto !important; }}

[data-testid="stImage"] img {{ border-radius:12px; border:1px solid {LINE}; }}

/* ======================================================================
   11. Result tables  (class names kept from the previous theme so
       results_ui.py needs no change to its markup contract)
   ====================================================================== */
.oq-table-wrap {{
    border:1px solid {LINE}; border-radius:13px; overflow:hidden auto;
    background:{WHITE}; max-height:none;
}}
table.oq-table {{ width:100%; border-collapse:collapse; font-size:{font_size}; }}
table.oq-table thead th {{
    background:{CANVAS}; color:{MUTED};
    font-size:.685rem; font-weight:750; letter-spacing:.07em; text-transform:uppercase;
    text-align:left; padding:{head_pad}; border-bottom:1px solid {LINE};
    white-space:nowrap; position:sticky; top:0; z-index:1;
}}
table.oq-table td {{
    padding:{row_pad}; vertical-align:top; color:{INK_SOFT};
    border-bottom:1px solid {LINE_SOFT}; line-height:{row_line};
    overflow-wrap:anywhere; word-break:break-word;
}}
table.oq-table tbody tr:last-child td {{ border-bottom:none; }}
table.oq-table tbody tr:hover td {{ background:#F8FAFD; }}
table.oq-table td.oq-idx {{
    width:2.4rem; color:{MUTED}; font-variant-numeric:tabular-nums;
    font-weight:650; text-align:right; padding-right:.55rem;
}}
table.oq-table td.oq-first {{ font-weight:650; color:{INK}; }}
table.oq-table tr.oq-pass td.oq-idx {{ box-shadow:inset 3px 0 0 {OK_SOLID}; }}
table.oq-table tr.oq-warn td.oq-idx {{ box-shadow:inset 3px 0 0 {WARN_SOLID}; }}
table.oq-table tr.oq-fail td.oq-idx {{ box-shadow:inset 3px 0 0 {BAD_SOLID}; }}
table.oq-table tr.oq-fail td {{ background:#FFFBFA; }}
table.oq-table tr.oq-fail:hover td {{ background:{BAD_BG}; }}
table.oq-table tr.oq-warn td {{ background:#FFFCF6; }}
table.oq-table tr.oq-warn:hover td {{ background:{WARN_BG}; }}
table.oq-table tr.oq-pass:hover td {{ background:{OK_BG}; }}

.oq-status {{
    display:inline-flex; align-items:center; gap:.3rem;
    font-size:.7rem; font-weight:750; letter-spacing:.01em;
    padding:{status_pad}; border-radius:999px; white-space:nowrap;
    border:1px solid transparent;
}}
.oq-status::before {{
    content:""; width:6px; height:6px; border-radius:50%; background:currentColor;
}}
.oq-status.pass {{ color:{OK};   background:{OK_BG};   border-color:{OK_LINE}; }}
.oq-status.warn {{ color:{WARN}; background:{WARN_BG}; border-color:{WARN_LINE}; }}
.oq-status.fail {{ color:{BAD};  background:{BAD_BG};  border-color:{BAD_LINE}; }}

/* What a run used — see theme.run_notes. Bordered and labelled so it reads
   as a record of the run rather than as four stray captions. */
.dq-runnotes {{
    border:1px solid {BAND_EDGE}; border-radius:11px;
    background:{NOTE_BG};
    padding:.55rem .75rem; margin:.1rem 0 .7rem 0;
    display:flex; flex-direction:column; gap:.3rem;
}}
.dq-runnote {{
    display:flex; gap:.6rem; align-items:baseline;
    font-size:.775rem; line-height:1.45;
}}
.dq-runnote-k {{
    flex:none; min-width:11ch; color:{BRAND_DARK};
    font-weight:700; letter-spacing:.01em;
}}
.dq-runnote-v {{ color:{INK_SOFT}; min-width:0; }}
@media (max-width:820px) {{
    .dq-runnote {{ flex-direction:column; gap:.05rem; }}
}}

/* A section with exactly one check: the verdict, what was checked and why,
   on one line. See results_ui.render_single_check for why. */
.oq-single {{
    display:flex; align-items:center; gap:.6rem; flex-wrap:wrap;
    padding:.6rem .85rem; border-radius:11px;
    border:1px solid {LINE}; background:{WHITE};
    font-size:.845rem; line-height:1.5;
}}
.oq-single.pass {{ border-color:{OK_LINE};   background:{OK_BG}; }}
.oq-single.warn {{ border-color:{WARN_LINE}; background:{WARN_BG}; }}
.oq-single.fail {{ border-color:{BAD_LINE};  background:{BAD_BG}; }}
.oq-single-label {{ font-weight:700; color:{INK}; }}
.oq-single-detail {{ color:{INK_SOFT}; flex:1 1 16ch; min-width:0; }}

.oq-empty {{
    display:flex; align-items:center; gap:.55rem;
    padding:.85rem 1rem; border-radius:12px; background:{OK_BG};
    color:{OK}; font-size:.83rem; border:1px solid {OK_LINE}; font-weight:600;
}}
.oq-empty.plain {{ background:{CANVAS}; color:{MUTED}; border-color:{LINE}; font-weight:500; }}
.oq-note {{
    font-size:.83rem; color:{INK_SOFT}; line-height:1.65;
    background:{CANVAS}; border:1px solid {LINE};
    border-radius:12px; padding:.75rem .9rem; margin:.45rem 0;
}}

details.oq-details {{
    border:1px solid {LINE}; border-radius:12px; background:{WHITE};
    margin:.55rem 0 0 0; overflow:hidden;
}}
details.oq-details > summary {{
    cursor:pointer; list-style:none; padding:.6rem .9rem;
    font-weight:650; font-size:.83rem; color:{INK_SOFT};
    display:flex; align-items:center; gap:.5rem; background:{WHITE};
}}
details.oq-details > summary::-webkit-details-marker {{ display:none; }}
details.oq-details > summary::before {{
    content:"\\25b8"; font-size:.75rem; color:{MUTED}; transition:transform .15s ease;
}}
details.oq-details[open] > summary::before {{ transform:rotate(90deg); }}
details.oq-details > summary:hover {{ background:{CANVAS}; color:{BRAND_DARK}; }}
.oq-details-body {{ border-top:1px solid {LINE}; }}
.oq-details-body .oq-table-wrap {{ border:none; border-radius:0; }}

/* job header inside a result group */
.oq-jobhead {{ display:flex; align-items:center; gap:.55rem; flex-wrap:wrap;
    font-weight:750; font-size:1rem; color:{INK}; }}
.oq-jobsub {{ font-size:.8rem; color:{MUTED}; font-weight:500; }}
.oq-chip {{
    display:inline-block; font-size:.68rem; font-weight:750;
    padding:.15rem .5rem; border-radius:999px; border:1px solid {LINE};
}}
.oq-chip.ok   {{ color:{OK};   background:{OK_BG};   border-color:{OK_LINE}; }}
.oq-chip.warn {{ color:{WARN}; background:{WARN_BG}; border-color:{WARN_LINE}; }}
.oq-chip.bad  {{ color:{BAD};  background:{BAD_BG};  border-color:{BAD_LINE}; }}

/* ======================================================================
   12. Banners, lists, misc
   ====================================================================== */
.dq-banner {{
    display:flex; gap:.7rem; align-items:flex-start;
    border-radius:13px; padding:.85rem 1rem; margin:.35rem 0;
    border:1px solid {LINE}; background:{WHITE}; font-size:.845rem; line-height:1.6;
}}
.dq-banner svg {{ flex:none; margin-top:.1rem; }}
.dq-banner.ok   {{ background:{OK_BG};   border-color:{OK_LINE};   color:{OK}; }}
.dq-banner.warn {{ background:{WARN_BG}; border-color:{WARN_LINE}; color:{WARN}; }}
.dq-banner.bad  {{ background:{BAD_BG};  border-color:{BAD_LINE};  color:{BAD}; }}
.dq-banner.info {{ background:{BRAND_TINT}; border-color:{BRAND_LINE}; color:{BRAND_DARK}; }}
.dq-banner b {{ font-weight:750; }}

.dq-kv {{ display:flex; flex-wrap:wrap; gap:.45rem .6rem; margin:.3rem 0 .1rem 0; }}
/* The "what will run" chips sat flush against the Run button, reading as
   one merged block. Separate the two so the chips clearly belong to the
   options above them and the button stands on its own. */
.dq-kv.dq-ready {{ margin:.45rem 0 .75rem 0; gap:.5rem; }}
.dq-kv-item {{
    font-size:.745rem; color:{INK_SOFT}; background:{CANVAS};
    border:1px solid {LINE}; border-radius:8px; padding:.25rem .55rem;
}}
.dq-kv-item b {{ color:{INK}; font-weight:700; }}

.dq-help-h {{ font-size:.95rem; font-weight:750; color:{INK}; margin:.2rem 0 .35rem 0; }}
.dq-help-p {{ font-size:.845rem; color:{INK_SOFT}; line-height:1.7; margin:0 0 .55rem 0; }}
.dq-help-ol, .dq-help-ul {{ margin:.1rem 0 .6rem 1.1rem; padding:0; }}
.dq-help-ol li, .dq-help-ul li {{
    font-size:.845rem; color:{INK_SOFT}; line-height:1.7; margin-bottom:.25rem;
}}
.dq-help-ol li b, .dq-help-ul li b {{ color:{INK}; }}
.dq-help-ul {{ list-style:none; margin-left:0; }}
.dq-help-ul li {{ position:relative; padding-left:1.15rem; }}
.dq-help-ul li::before {{
    content:""; position:absolute; left:.15rem; top:.62em;
    width:5px; height:5px; border-radius:50%; background:{BRAND};
}}
/* The help pages are read, not scanned — a heading inside a card needs a
   little air above it so its own paragraphs stay attached to it. */
.dq-help-h {{ margin-top:1.15rem; }}
.dq-help-h:first-child {{ margin-top:.2rem; }}
.dq-help-p code, .dq-help-ul code {{
    background:{CANVAS}; border:1px solid {LINE}; border-radius:5px;
    padding:.05rem .3rem; font-size:.8em;
}}

/* hidden view holder — keeps every widget mounted (and therefore keeps
   uploaded files and typed text alive) while another nav view is on
   screen. Display:none is deliberate: unmounting the widgets is what
   would drop their state. */
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .dq-hide) {{
    display:none !important;
}}

/* sticky action bar (kept for callers that still use it) */
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .oq-sticky) {{
    position:sticky !important; top:.5rem; z-index:98;
    background:rgba(255,255,255,.95) !important; backdrop-filter:blur(8px);
    border:1px solid {LINE} !important; border-radius:13px !important;
    padding:.5rem .6rem !important; box-shadow:{SHADOW} !important;
}}
.oq-sticky {{ display:none; }}

/* ======================================================================
   13. Narrow screens
   ====================================================================== */
/* Below this width the four-step rail no longer fits its captions, and
   the welcome panel's artwork starts running under the headline. Both
   give way rather than overflow. */
@media (max-width:1280px) {{
    .dq-step-s {{ display:none; }}
    .dq-step-bar {{ margin:0 .55rem; min-width:14px; }}
    .dq-step {{ gap:.5rem; }}
    .stButton > button, .stDownloadButton > button {{
        font-size:.8rem; padding:.45rem .6rem;
    }}
    /* Streamlit clips a button label that no longer fits ("Clear …");
       letting it wrap keeps the whole action readable instead. */
    .stButton > button p, .stDownloadButton > button p {{
        white-space:normal !important; line-height:1.25;
    }}
}}
@media (max-width:1100px) {{
    .dq-hero-side {{ display:none; }}
    .dq-hero-art {{ width:74%; opacity:.38; }}
    .dq-hero-title {{ font-size:1.7rem; }}
    .dq-hero-sub {{ max-width:46ch; }}
}}
@media (max-width:1280px) {{
    :root {{ --dq-title-xl:1.7rem; --dq-title-lg:1.22rem; }}
}}
@media (max-width:820px) {{
    .block-container {{ padding-left:.9rem; padding-right:.9rem; }}
    .dq-hero {{ padding:1.25rem 1.15rem; }}
    .dq-hero-feats {{ gap:.9rem; }}
    .dq-stat {{ flex:1 1 100%; }}
    .dq-topbar-sub {{ display:none; }}
}}
</style>
"""


# The running indicator's markup. Rendered once, next to the stylesheet;
# CSS decides when it is visible (see the .dq-topload rules).
_TOP_PROGRESS = (
    '<div class="dq-topload" aria-hidden="true">'
    '<div class="dq-topload-track"><div class="dq-topload-bar"></div></div>'
    '<div class="dq-topload-text">Working</div>'
    '</div>'
)


def inject(density: str = "comfortable") -> None:
    """Injects the whole stylesheet, and the top progress bar that replaces
    Streamlit's own running indicator. Call once, first thing in the app."""
    st.markdown(_css(density), unsafe_allow_html=True)
    st.markdown(_TOP_PROGRESS, unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------

def _md(markup: str) -> None:
    """Emit a block of our own HTML.

    Every line is stripped of its leading whitespace first. Streamlit runs
    `st.markdown` input through a Markdown parser even with
    `unsafe_allow_html=True`, and Markdown turns any line indented by four
    or more spaces into a literal code block. Our markup is built inside
    indented f-strings and embeds SVG that carries its own indentation, so
    without this the welcome panel's artwork rendered on screen as a wall
    of raw `<path d="...">` source. Leading whitespace carries no meaning
    in this markup — every block is ordinary flow HTML, never a <pre> —
    so stripping it is safe and makes the output indentation-proof.
    """
    st.markdown(
        "\n".join(line.strip() for line in str(markup).strip().splitlines()),
        unsafe_allow_html=True,
    )


def _esc(v) -> str:
    return _html.escape(str(v if v is not None else ""))


def chip(text: str, kind: str = "", icon_name: str = "") -> str:
    """A small status pill, returned as markup so several can share a row."""
    ico = _icon(icon_name, 13) if icon_name else ""
    return f'<span class="dq-chip {kind}">{ico}{_esc(text)}</span>'


def render_topbar(title: str, subtitle: str = "", chips: Sequence[str] = ()) -> None:
    """The sticky product bar: brand tile, title, and status chips."""
    chips_html = "".join(chips)
    sub = f'<div class="dq-topbar-sub">{_esc(subtitle)}</div>' if subtitle else ""
    _md(
        f"""
        <div class="dq-topbar">
          <div class="dq-topbar-tile">{_icon("car", 21)}</div>
          <div>
            <div class="dq-topbar-title">{_esc(title)}</div>
            {sub}
          </div>
          <div class="dq-topbar-spacer"></div>
          <div class="dq-topbar-chips">{chips_html}</div>
        </div>
        """
    )


# Where `render_sidebar` parks the container that `sidebar_action` fills.
# In session_state rather than a module global: the module is shared by
# every browser session in the process, and a container belongs to exactly
# one of them.
_ACTION_SLOT_KEY = "_dq_sidebar_action_slot"


def render_sidebar(nav_items: Sequence[Tuple[str, str]], key: str = "dq_nav") -> str:
    """The dark navigation rail. Returns the selected view's id.

    `nav_items` is a sequence of `(id, label)` pairs; their ORDER must
    match `_NAV_ICONS` above, which is what puts the right line icon on
    each row.

    The rail carries the brandmark, the workspace's primary action, and
    the menu. It used to end in a block of status chips, a strapline and a
    watermark; those are gone, and `sidebar_prefs()` below now hands that
    space to the handful of view switches worth reaching without opening
    Settings.

    The action itself cannot be drawn here — whether a run is ready to go
    is not known until the uploaders further down the script have been
    rendered — so this reserves a container for it and `sidebar_action()`
    fills it in place, however much later that happens.
    """
    ids = [i for i, _ in nav_items]
    labels = {i: l for i, l in nav_items}

    # A view that no longer exists can still be sitting in session_state —
    # a tab left open across an update, say. Streamlit raises rather than
    # falling back when a stored value is not among the options, so clear
    # it here and let the radio start on the first view instead.
    if st.session_state.get(key) not in ids:
        st.session_state.pop(key, None)

    with st.sidebar:
        _md(
            f"""
            <div class="dq-brandmark">
              <div class="dq-brandmark-tile">{_icon("car", 21)}</div>
              <div>
                <div class="dq-brandmark-name">Dealer Panel QA</div>
                <div class="dq-brandmark-tag">Validate. Compare. Verify.</div>
              </div>
            </div>
            """
        )
        # The primary action's slot. It is filled much later in the script,
        # once the run's readiness is known — see `sidebar_action()`.
        st.session_state[_ACTION_SLOT_KEY] = st.container()
        _md('<div class="dq-navlabel">Menu</div>')
        choice = st.radio(
            "Navigation",
            options=ids,
            format_func=lambda i: labels.get(i, i),
            key=key,
            label_visibility="collapsed",
        )

    return choice


@contextmanager
def sidebar_action(hidden: bool = False):
    """Fills the slot `render_sidebar()` left under the brandmark.

    The caller's widgets are created wherever the caller happens to be in
    the script, but land at the head of the rail. `hidden` hides the whole
    block on views that are not the workspace — the widgets are still
    created, because Streamlit drops the state of anything it did not
    render and the caller's `if run_clicked:` still has to have an answer.

    Falls back to appending to the sidebar if no slot was reserved, so this
    can never be the thing that takes the page down.
    """
    slot = st.session_state.get(_ACTION_SLOT_KEY)
    target = slot if slot is not None else st.sidebar
    with target:
        _md(
            '<div class="dq-side-action"></div>'
            + ('<div class="dq-hide"></div>' if hidden else "")
        )
        yield


def sidebar_ready_note(*bits: Tuple[str, bool]) -> None:
    """The one-line "what this run would cover" note under the rail's
    action. Each bit is `(text, is_ready)`."""
    if not bits:
        return
    _md(
        '<div class="dq-side-ready">'
        + "".join(
            f'<span class="{"ok" if ready else "warn"}">'
            f'{_icon("check-circle" if ready else "alert-triangle", 11)}'
            f'&nbsp;<b>{_esc(text)}</b></span>'
            for text, ready in bits
        )
        + "</div>"
    )


@contextmanager
def sidebar_prefs(label: str = "View"):
    """The switches that live at the foot of the navigation rail.

    A context manager rather than a widget list because the widgets
    themselves belong to app.py — this only opens the sidebar, draws the
    divider and the group label, and lets the caller put its own
    `st.toggle` inside.
    """
    with st.sidebar:
        _md(
            f'<div class="dq-side-prefs"></div>'
            f'<div class="dq-navlabel dq-side-prefs-label">{_esc(label)}</div>'
        )
        yield


def render_hero(
    title: str,
    subtitle: str = "",
    badge: str = "",
    eyebrow: str = "Welcome to",
    features: Sequence[Tuple[str, str, str]] = (),
    side_lines: Sequence[str] = (),
) -> None:
    """The welcome panel.

    Signature is backwards compatible with the previous theme — the old
    three positional arguments still work — with the feature row and the
    right-hand strapline added as optional extras.
    """
    if not features:
        features = (
            ("zap", "Fast & Accurate", "Results in seconds"),
            ("shield", "Catch Every Issue", "Content, style, links"),
            ("chart", "Actionable Insights", "Exportable QA report"),
        )
    if not side_lines:
        side_lines = ("Better data", "Stronger dealers", "Brighter roads")

    feats = "".join(
        f'<div class="dq-hero-feat">'
        f'<div class="dq-hero-feat-ico">{_icon(ic, 18)}</div>'
        f'<div><div class="dq-hero-feat-t">{_esc(t)}</div>'
        f'<div class="dq-hero-feat-s">{_esc(s)}</div></div></div>'
        for ic, t, s in features
    )
    side = "".join(f'<div class="dq-hero-side-line">{_esc(l)}</div>' for l in side_lines)
    sub = f'<div class="dq-hero-sub">{_esc(subtitle)}</div>' if subtitle else ""
    eyebrow_text = badge or eyebrow

    _md(
        f"""
        <div class="dq-hero">
          {_artwork.hero_scene()}
          <div class="dq-hero-inner">
            <div class="dq-hero-main">
              <div class="dq-hero-eyebrow">{_esc(eyebrow_text)}</div>
              <div class="dq-hero-title">{_esc(title)}</div>
              {sub}
              <div class="dq-hero-feats">{feats}</div>
            </div>
            <div class="dq-hero-side">
              {side}
              <div class="dq-hero-side-rule"></div>
            </div>
          </div>
        </div>
        """
    )


def render_stepper(steps: Sequence[Tuple[str, str]], current: int = 0) -> None:
    """Numbered progress rail. `current` is the 0-based active step; every
    step before it renders as done."""
    parts = []
    for i, (title, sub) in enumerate(steps):
        if i < current:
            state = "done"
        elif i == current:
            state = "active"
        else:
            state = ""
        num = _icon("check-circle", 17) if state == "done" else str(i + 1)
        parts.append(
            f'<div class="dq-step {state}">'
            f'<div class="dq-step-num">{num}</div>'
            f'<div><div class="dq-step-t">{_esc(title)}</div>'
            f'<div class="dq-step-s">{_esc(sub)}</div></div></div>'
        )
        if i < len(steps) - 1:
            parts.append(f'<div class="dq-step-bar {"done" if i < current else ""}"></div>')
    _md(f'<div class="dq-stepper">{"".join(parts)}</div>')


def stat_tiles(items: Sequence[Tuple[str, object, str, str, str]]) -> None:
    """The KPI row.

    Each item is `(label, value, tone, icon_name, subtext)` where tone is
    one of total / ok / bad / warn / neutral.
    """
    tiles = []
    for label, value, tone, icon_name, sub in items:
        sub_html = f'<div class="dq-stat-x">{_esc(sub)}</div>' if sub else ""
        tiles.append(
            f'<div class="dq-stat {tone}">'
            f'<div class="dq-stat-ico">{_icon(icon_name, 20)}</div>'
            f'<div><div class="dq-stat-l">{_esc(label)}</div>'
            f'<div class="dq-stat-v">{_esc(value)}</div>{sub_html}</div></div>'
        )
    _md(f'<div class="dq-stats">{"".join(tiles)}</div>')


def banner(text_html: str, tone: str = "info", icon_name: str = "info") -> None:
    """A coloured inline message. `text_html` is inserted as-is, so callers
    that need <b>/<br> can pass them; escape anything user-supplied."""
    _md(f'<div class="dq-banner {tone}">{_icon(icon_name, 17)}<div>{text_html}</div></div>')


def run_notes(notes: Sequence[Tuple[str, str]]) -> None:
    """What a run actually used, as one labelled panel.

    These four facts — which master was read, how it was cropped, which OCR
    engine answered, which guideline each banner was read as — are the
    first things anyone asks when a result looks wrong. They used to be
    `st.caption` lines scattered down the card, small and grey and easy to
    scroll straight past, and each one vanished entirely whenever its value
    happened to be empty, so "no master was supplied" looked exactly like
    "everything is fine". One panel, always present, every line accounted
    for.
    """
    rows = [(label, text) for label, text in (notes or []) if str(text or "").strip()]
    if not rows:
        return
    _md(
        '<div class="dq-runnotes">'
        + "".join(
            f'<div class="dq-runnote"><span class="dq-runnote-k">{_esc(label)}</span>'
            f'<span class="dq-runnote-v">{_esc(text)}</span></div>'
            for label, text in rows
        )
        + "</div>"
    )


def section_title(title: str, subtitle: str = "", icon_name: str = "") -> None:
    """A full-width heading that separates groups of cards."""
    ico = (
        f'<span style="color:{BRAND};display:flex;align-items:center">{_icon(icon_name, 18)}</span>'
        if icon_name else ""
    )
    sub = f'<span class="dq-sectitle-s">{_esc(subtitle)}</span>' if subtitle else ""
    _md(f'<div class="dq-sectitle">{ico}<span class="dq-sectitle-t">{_esc(title)}</span>'
        f'{sub}<span class="dq-sectitle-rule"></span></div>')


@contextmanager
def section(
    kind: str = "neutral",
    label: str = "",
    subtitle: str = "",
    step: Optional[int] = None,
    icon_name: str = "",
    right_html: str = "",
):
    """A white content card.

    Backwards compatible with the previous theme's `section(kind, label)`;
    `subtitle`, `step`, `icon_name` and `right_html` are new optional
    extras used by the redesigned layout.
    """
    k = _kind(kind)
    accent, tint, default_icon = KINDS[k]
    name = icon_name or default_icon

    box = st.container(border=True)
    with box:
        if step is not None:
            lead = f'<div class="dq-head-step">{int(step)}</div>'
        else:
            lead = (
                f'<div class="dq-head-ico" style="background:{tint};color:{accent}">'
                f'{_icon(name, 19)}</div>'
            )
        sub = f'<div class="dq-head-sub">{_esc(subtitle)}</div>' if subtitle else ""
        right = f'<div class="dq-head-right">{right_html}</div>' if right_html else ""
        _md(f'<div class="dq-head"><span class="dq-card-mark"></span>'
            f'{lead}<div class="dq-head-txt">'
            f'<div class="dq-head-title">{_esc(label)}</div>{sub}</div>{right}</div>')
        yield box


# `card` reads better at the new call sites; same object either way.
card = section


@contextmanager
def sticky_bar():
    """A container pinned near the top of the content column."""
    box = st.container(border=True)
    with box:
        st.markdown('<div class="oq-sticky"></div>', unsafe_allow_html=True)
        yield box


@contextmanager
def group(label: str = ""):
    """A titled band that wraps a run's whole result set."""
    box = st.container()
    with box:
        if label:
            section_title(label, icon_name="chart")
        yield box


@contextmanager
def hidden(is_hidden: bool = True):
    """Renders its body but hides it from view when `is_hidden`.

    Used by the navigation: switching to Settings or Help must NOT unmount
    the workflow's widgets, because Streamlit drops the state of any
    widget it did not render — which would silently throw away uploaded
    files and typed master text the moment somebody looked at another
    page. So everything stays mounted and CSS hides it instead.
    """
    box = st.container()
    with box:
        if is_hidden:
            st.markdown('<div class="dq-hide"></div>', unsafe_allow_html=True)
        yield box


def icon_html(name: str, size: int = 18) -> str:
    """Re-exported so call sites don't each import modules.icons."""
    return _icon(name, size)
