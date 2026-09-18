"""
Feature 10 — OCR Text Extraction.

Uses PaddleOCR if it is installed and its models are available (PaddleOCR
downloads its recognition/detection models ONCE on first use, into a local
cache folder — no admin rights are required for `pip install paddleocr`,
since pip installs into the user's own Python environment; after that
first model download, PaddleOCR runs fully offline).

If PaddleOCR is not installed, or its models are not cached and cannot be
fetched (e.g. no network available on this machine), we automatically and
silently fall back to Tesseract (pytesseract), which is already offline,
lightweight, and available. This guarantees the app keeps working
regardless of what's installed on a given Windows/macOS machine.

Only ever OCRs the banner image that is handed to it — never the full
email — per spec (item 10 & 8).

--------------------------------------------------------------------------
Headline vs Subheadline splitting
--------------------------------------------------------------------------
Banners commonly stack a large-font Headline directly above a smaller-font
Subheadline (see e.g. "DOMINATE EVERYDAY. YOUR WAY." / "DRIVE YOUR MATCH.").
A flat joined text string has no notion of which words belonged to which
line, so a single blob comparison can't reliably tell headline text apart
from subheadline text.

To fix this, `extract_lines()` returns each OCR detection as a `TextLine`
that keeps its text AND its pixel height (bounding-box height) and vertical
position. `cluster_lines_by_size()` then groups those lines into "large"
(headline-sized) and "small" (subheadline-sized) clusters using relative
font height — the tallest line-height band on the banner is treated as the
Headline band, and the next-tallest distinct band as the Subheadline band.
Everything else (dealer name, disclaimers, CTAs, logos-as-text, etc.) is
left as "other" and is still available via the full joined text.

`extract_text()` is kept as-is (flat joined string) for backward
compatibility with callers that only need the whole banner text (e.g. the
OCR QA tab, dealer-in-banner substring check) — nothing about its
behaviour changed.

--------------------------------------------------------------------------
Line hygiene: logo/graphic noise must not corrupt a line's font height
--------------------------------------------------------------------------
Tesseract groups words into lines using its own block/paragraph/line
numbering, and it happily puts a NON-TEXT element into a text line when
the two sit on the same baseline. A real, observed case: a banner with the
BMW roundel logo immediately to the left of the headline OCR'd as

    'oD) ENGINEERED TO DELIVER OPTIMAL'   (conf 24 on 'oD)')

where the junk 'oD)' token's bounding box was 75px tall against 22-23px
for the real words. Because the old code took the line's height as
`max(bottom) - min(top)` across every word in the group, that one junk
token tripled the measured font height of the headline line — which then
threw the whole font-size banding off: the headline's two visual lines
landed in two different bands, and the dealer name got promoted into the
Subheadline (or even Headline) band. Every downstream comparison then
failed on a banner that was actually completely correct.

Both problems are fixed at the source in `_lines_with_tesseract()`:
  1. Words below `MIN_WORD_CONFIDENCE` are dropped (real banner copy OCRs
     at 80-96; logo/icon debris comes back in the 0-35 range).
  2. Words whose box height is a wild outlier against the MEDIAN word
     height of their own line are dropped, so a tall graphic glued onto a
     line can never define that line's font size.
Both filters are self-limiting: if applying one would empty a line
entirely, the line is kept unfiltered, so genuinely low-contrast banner
copy is never silently discarded. PaddleOCR/RapidOCR return line-level
boxes with their own confidence scores, so they get the equivalent
confidence guard.

--------------------------------------------------------------------------
Content-aware field assignment (`assign_lines_by_content`)
--------------------------------------------------------------------------
Font-size banding is a heuristic, and no heuristic survives every banner:
a dealer name set at 22px directly under a 23px headline line is within
any sane jitter tolerance, so geometry alone WILL merge them. The same
reasoning that produced `find_dealer_line()` (match by content, not by
pixels) applies just as well to Headline and Subheadline: whenever the
expected values are known — and in this app they always are, since they
come from the Master JPG/PDF/HTML or Manual Text — each OCR'd line can be
assigned to whichever field its words actually belong to.

`assign_lines_by_content()` does exactly that, and is deliberately
conservative: a line is only assigned when a strong majority of its own
words belong to that field's expected text, so a genuinely WRONG line on
the banner matches nothing and is reported as unmatched rather than being
quietly absorbed into a field it doesn't belong to. Callers fall back to
the geometric bands whenever content assignment finds nothing for a field,
so behaviour is never worse than before.
"""

import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np
from PIL import Image


# --------------------------------------------------------------------------
# Line-hygiene tuning knobs (see the "Line hygiene" section of the module
# docstring). Every one of these is a single place to tune — nothing below
# hardcodes a threshold.
# --------------------------------------------------------------------------

# Word/line detections below this OCR confidence are treated as graphic
# debris (logo edges, icons, number plates blurring into the artwork) and
# dropped. Real banner copy comes back at 80+; the observed BMW-roundel
# artefact came back at 24.
MIN_WORD_CONFIDENCE = 35.0

# A word whose bounding box is more than this multiple of its own line's
# MEDIAN word height is not part of that line's text — it is a graphic that
# happens to share the baseline. (The observed case: a 75px logo box glued
# onto a line of 22px words.)
MAX_WORD_HEIGHT_RATIO = 1.8

# ...and the same guard in the other direction, for subscript-sized debris.
MIN_WORD_HEIGHT_RATIO = 0.40

# Content-assignment threshold: the fraction of a candidate LINE's own words
# that must belong to a field's expected text before that line is assigned
# to the field. Deliberately high enough that an unrelated line is left
# unmatched (and therefore still reported) rather than absorbed.
FIELD_MATCH_MIN_LINE_RATIO = 0.6

# When matching a dealer-name line, this many words on the line may be
# absent from the expected dealer name and the line still counts as a
# match. Exists because banners routinely brand-prefix the dealer name —
# the Excel column says "Bird Automotive" while the artwork reads "BMW Bird
# Automotive", which is a 0.67 word-overlap and used to be rejected outright
# by the old symmetric 0.7 threshold.
DEALER_LINE_MAX_EXTRA_TOKENS = 2


# --------------------------------------------------------------------------
# Small-type auto-upscale (see `_tesseract_word_boxes`)
# --------------------------------------------------------------------------
# Measured on real BMW EDM banners: the model badge line ("THE i7" /
# "THE 7") and the "BAYERISCHE MOTOREN WERKE" strapline render at 10-11px
# in an 800px-wide email banner. At that size Tesseract:
#   - merges words        -> "THEI7" instead of "THE i7"
#   - misreads glyphs     -> "THE?" instead of "THE 7"
#   - returns confidences in the teens for text that is perfectly crisp
#   - inflates bounding boxes around apostrophes ("DON'T" measured 33px
#     tall against 19px for "YOU" on the very same visual line), which then
#     splits ONE headline across two font-size bands
# Re-running OCR on an upscaled copy fixes all four at once, and because
# the Master creative and the dealer's email are OCR'd through the same
# path, it also makes their word-splitting AGREE — which is what stops a
# correct banner being failed for "missing word: THEI7".
#
# The image is only ever upscaled when its own measured type is small, and
# every coordinate is divided back down, so callers still work in original
# image pixels and nothing downstream needs to know this happened.
OCR_MIN_TEXT_HEIGHT_PX = 18.0    # below this median word height, re-OCR bigger
OCR_TARGET_TEXT_HEIGHT_PX = 26.0  # aim for roughly this median word height
OCR_MAX_UPSCALE = 3               # never scale beyond this (noise starts winning)

# --------------------------------------------------------------------------
# Low-contrast display type (see `_render_variants` / `_tesseract_word_boxes`)
# --------------------------------------------------------------------------
# The current BMW creative guideline sets the headline and subheadline in
# WHITE type directly over the photograph's sky — white on a pale blue that
# measures barely 2:1 against the glyphs. Tesseract returns literally
# nothing for that: not a low-confidence guess, zero word boxes, so the
# banner arrives at the clustering stage with no lines at all and every
# banner-text check reports "no expected value".
#
# The fix is to give Tesseract a second rendition of the same pixels in
# which the white type IS the ink: everything at or above this grey level
# becomes black, everything below becomes white paper. On the measured
# creative that recovers the complete headline and subheadline, and because
# the rendition keeps the original dimensions, every box it returns is
# already in original-image coordinates.
#
# 200 is deliberately high. Lower values (150-180) also read pure-white
# type on black, but on a light sky they start swallowing the sky itself;
# 200 isolates the type on both.
WHITE_INK_THRESHOLD = 200

# WHEN the white-ink rendition is used instead of the artwork as supplied.
#
# Measured across the ten EMAILER pages of a real sales-push bulletin, the
# original rendition reads them at 74-96% confidence and the white-ink
# rendition at 21-82% — on the same pages, recovering a similar number of
# characters but fragmenting words ("LET'S" -> "ET'S", "THE" -> "TH E").
# An earlier version of this merged both renditions' boxes and let the one
# with more characters lead; fragmentation inflates a character count, so
# the worse read won and three of the ten pages came back mangled.
#
# So: the artwork as supplied always leads, and another rendition replaces
# it only where it is decisively better — see the per-line merge below,
# which is where "decisively" is now decided, one line at a time instead of
# one banner at a time.

# --------------------------------------------------------------------------
# Display type over a BRIGHT, uneven background (see `_local_ink_rendition`)
# --------------------------------------------------------------------------
# WHITE_INK_THRESHOLD works off a single global grey level, which is only
# the right instrument while the background is darker than the type
# everywhere on the banner. On the X5 festive creative the subheadline runs
# across the sunlit flank of the car, and those pixels are BRIGHTER than
# the type — so a global threshold turns the car into ink and swallows the
# words sitting inside it ("GET THE BMW X5" came back as "GET THEB").
#
# A morphological top-hat removes whatever is broader than its structuring
# element and keeps whatever is thinner, which is precisely the difference
# between a car's flank and a letter stroke. What is left is strokes on a
# flat floor rather than type on a photograph, which is something a
# threshold can actually be set for (see LOCAL_INK_WINDOW_PX).
#
# The kernel has to be WIDER than the thickest stroke on the banner (a 42px
# headline is set in roughly 5px strokes) and narrower than the background
# features worth removing. 25px measured best across the sample creatives;
# at 9px the headline's own strokes start being eaten.
LOCAL_INK_KERNEL_PX = 25

# What is left has to be thresholded LOCALLY too, not by one level for the
# whole banner. A global level is set by whatever responds most strongly to
# the filter, which is the 42px headline — and the threshold that suits a
# 5px stroke erases a 2px one. Measured on the X5 master: with a global
# (Otsu) level the subheadline came back as "GET THE BM'", and on a crop
# containing ONLY the subheadline the very same filter read "GET THE BMW
# X5" perfectly, because Otsu then had only the subheadline to look at.
#
# So the ink is whatever stands this many grey levels above its own
# neighbourhood, over a window a few times the height of the smallest type
# on a banner. The bias is what keeps flat background flat: at 0 every
# patch of empty sky finds "ink" in its own sensor noise.
LOCAL_INK_WINDOW_PX = 31
LOCAL_INK_BIAS = 8

# --------------------------------------------------------------------------
# Picking the best reading of each LINE, not of the whole banner
# --------------------------------------------------------------------------
# Choosing one rendition for the entire banner cannot be right, because the
# renditions fail in different PLACES on the same artwork. Measured on the
# September festive creative, master and dealer email side by side:
#
#   as supplied   BRING HOME YOUR / FECT MATCH.          <- loses "PER"
#                 THIS FESTIVE SEASON, GET THE BMW X7    <- correct
#   white-ink     BRING HOME YOUR / PERFECT MATCH.       <- correct
#                 THIS FEST ASON, GET THE BMW X7         <- loses "IVE SE"
#
# Whichever one is picked for the whole banner, a field comes back wrong —
# and the master and the dealer's email do not fail in the same place, so
# the QA reported words missing that are plainly present on both. Choosing
# per LINE gets every field right out of the same passes.
#
# An earlier attempt merged the renditions at the WORD level and let the
# one with more characters lead. Fragmentation inflates a character count,
# so the worse read won and three of ten bulletin pages came back mangled.
# This is the opposite of that: a line is only ever taken WHOLE, from one
# rendition, so no line is ever stitched together out of two readings.
#
# ONE rendition still decides WHAT THE LINES ARE — the one that read the
# banner best overall. The others may only say what a line it already found
# SAYS. That asymmetry is load-bearing in both directions:
#
#   * nothing is added, so a rendition that hallucinates a scrap of the
#     photograph, or that reads a model badge the others cannot, cannot
#     change the line count — and the line count is what the block-gap and
#     small-type-upscale thresholds are measured against, so an extra line
#     silently re-bands the whole banner;
#   * nothing is removed or duplicated, so a rendition that measures one
#     line's boxes at twice their real height (the artwork as supplied does
#     this on soft-focus photography) cannot swallow the line beneath it.
#
# A challenger has to beat the line it is challenging by this margin, which
# keeps a merely-equal reading from changing a correct one: on a real
# bulletin page the model badge scores 260 as "THE7" and 264 as "THE?".
RENDITION_LINE_SWITCH_MARGIN = 1.12

# Two readings are of the SAME line when their core bands overlap by more
# than this fraction of the shorter band.
RENDITION_LINE_OVERLAP_RATIO = 0.5

# How a line's reading is scored for that comparison. Each word counts its
# alphanumeric characters times its confidence — so a fuller read of the
# same line wins, which is the whole point — and then two penalties for the
# shapes OCR noise actually takes on this artwork.
JUNK_CHARACTER_PENALTY = 0.5   # a character that is neither alphanumeric nor
                               # ordinary punctuation: "DE?!", "‘OE?", "BM'"
MIXED_CASE_PENALTY = 0.55      # lower case inside a line otherwise set in
                               # capitals: "BMw", "iaTiON", "BENEFIfg", "writ"
CAPS_LINE_RATIO = 0.7          # ...which is what makes a line "capitals"
_PLAIN_PUNCTUATION = set(" .,'‘’\"“”-–—&%:;!?()/+")

# ...and the tighter set used when deciding whether a word at the EDGE of a
# line is a scrap of artwork. Brackets and slashes are dropped from it: real
# copy does contain them, but never in a short, unsure word at one end of a
# line, whereas a fragment read out of a photograph regularly does ("ey)",
# "(we"). The confidence guard is what protects genuine punctuated copy.
_WORD_EDGE_PUNCTUATION = _PLAIN_PUNCTUATION - set("()/+")

# --------------------------------------------------------------------------
# Banner layout (see `group_lines_into_blocks` / `detect_banner_layout`)
# --------------------------------------------------------------------------
# Where the headline sits on the banner is a creative-guideline decision and
# it has now changed twice:
#
#   CLASSIC  the photograph fills the banner, the model badge ("THE X3") is
#            set large over it, and the headline + subheadline sit in a band
#            at the FOOT, next to the BMW roundel.
#   LATEST   the headline + subheadline are set at the HEAD of the banner,
#            above the photograph, and the roundel moves to the bottom-left.
#
# Font size alone cannot tell these apart, and on the classic layout the
# model badge is physically the largest type on the artwork — so pure
# size banding hands "X3" back as the headline and demotes the real
# headline to subheadline. Grouping the lines into vertical BLOCKS first
# and banding only within the block that actually carries the message
# solves both layouts at once: a two-character badge sitting on its own
# carries almost no text weight, so it never wins.
#
# A new block starts when the vertical gap to the previous line exceeds
# this multiple of the median line height.
BLOCK_GAP_RATIO = 1.9

BANNER_LAYOUT_AUTO = "auto"        # pick the message block by text weight
BANNER_LAYOUT_LATEST = "latest"    # force the topmost qualifying block
BANNER_LAYOUT_CLASSIC = "classic"  # force the bottommost qualifying block
BANNER_LAYOUT_SIZE_BANDS = "size-bands"  # the original size-only behaviour

BANNER_LAYOUT_CHOICES = (
    BANNER_LAYOUT_AUTO,
    BANNER_LAYOUT_LATEST,
    BANNER_LAYOUT_CLASSIC,
    BANNER_LAYOUT_SIZE_BANDS,
)

# --------------------------------------------------------------------------
# Robust per-line font size (see `_core_line_height`)
# --------------------------------------------------------------------------
# A line's font size must NOT be measured as `max(bottom) - min(top)` across
# its words. One apostrophe, one descender or one stray anti-aliased pixel
# inflates a single word's box and takes the whole line's measured size with
# it. Real case, X1 master creative:
#     YOU(19) MISS(19) THE(18) SHOTS(19)   -> line measured 19px
#     YOU(19) DON'T(33) TAKE.(33)          -> line measured 33px
# Those two lines are the SAME font on the artwork; the second is one visual
# line of the same headline. Measuring by extents made them 74% apart, so
# font-size banding put them in different bands and the expected Headline
# came out as "YOU DON'T TAKE." — half of the real headline.
#
# Instead the font size is the SMALLEST word box on the line, ignoring
# obviously-subscript debris. Inflation only ever makes a box bigger, so the
# smallest box is the closest thing to the real cap height.
CORE_HEIGHT_MIN_RATIO = 0.55

# --------------------------------------------------------------------------
# Band jitter floor (see `cluster_lines_by_size`)
# --------------------------------------------------------------------------
# The jitter tolerance is a percentage, which stops making sense at small
# absolute sizes: an 11px line and a 10px line differ by 9% — over the 8%
# tolerance — purely because of pixel quantisation, and get split into two
# bands. Any two lines within this many pixels of each other are treated as
# the same size regardless of the percentage. Kept deliberately small (one
# pixel): measured on a real deck, an 11.5px badge line and a 10.5px
# strapline are the same visual size and must merge, while a 15.5px
# subheadline and a 13.5px legal line are genuinely different and must not.
JITTER_TOLERANCE_MIN_PX = 2.0

# --------------------------------------------------------------------------
# Graphic-debris lines (see `_is_probable_debris_line`)
# --------------------------------------------------------------------------
# Upscaling makes Tesseract more willing to hallucinate 1-3 character
# "words" out of building textures and logo edges. A hallucinated fragment
# with a tall box can become the tallest "line" on the banner and steal the
# Headline band. A line is discarded only when it is short AND low
# confidence AND only one or two words — deliberately narrow, so real short
# banner copy ("THE X1", conf 90+) is never touched.
# A line is debris when NO word on it is longer than DEBRIS_LINE_MAX_CHARS
# alphanumeric characters AND no word reaches DEBRIS_LINE_MAX_CONFIDENCE.
# Measured examples this removes: "eet =e See" (confidences 0/47/36, read
# out of a reflection on the X3 creative) and "Pr ENG" (44/44, read out of
# a building facade). Measured examples it keeps: "THE i7" (78/87),
# "THE7" (92) and "THEI7" (17, but five characters long).
DEBRIS_LINE_MAX_CHARS = 3
DEBRIS_LINE_MAX_CONFIDENCE = 50.0
# ...but a short, unsure line that is LEFT-ALIGNED with the banner's real
# copy is real copy. On the "THE 7" master creative the badge line comes
# back at confidence 0 for both of its words, and dropping it cost the
# whole expected Headline. It starts at x=51.5 against x=52.0 for the
# strapline underneath it, whereas genuine debris ("eet =e See" read out of
# a reflection) starts at x=380 against x=118 for the real copy. Alignment
# separates the two cleanly where confidence and length cannot.
DEBRIS_ALIGN_TOLERANCE_RATIO = 0.02


@dataclass
class OCRResult:
    text: str
    engine_used: str  # "paddleocr" or "tesseract" or "none"
    warning: Optional[str] = None


@dataclass
class TextLine:
    text: str
    height: float          # bounding-box pixel height of this line of text
    top: float              # y-coordinate of the top of the box (0 = top of banner)
    center_y: float          # y-coordinate of the vertical center of the box


@dataclass
class LinesResult:
    lines: List[TextLine] = field(default_factory=list)
    engine_used: str = "none"
    warning: Optional[str] = None

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)


@dataclass
class ClusteredLines:
    headline_lines: List[TextLine] = field(default_factory=list)
    subheadline_lines: List[TextLine] = field(default_factory=list)
    other_lines: List[TextLine] = field(default_factory=list)
    # Populated only when `cluster_lines_by_size()` / `extract_clustered_text()`
    # was called WITH an `expected_dealer_name` and a matching line was found
    # anywhere among the OCR'd lines (see `find_dealer_line()` below). Kept
    # separate from `other_lines` because a content-matched dealer line is a
    # much stronger signal than "whatever fell into the smallest font band" —
    # callers should prefer this field when it is non-empty.
    dealer_line: Optional["TextLine"] = None
    # Which of the supplied candidate dealer names `dealer_line` actually
    # matched. Callers that pass a LIST of candidates (e.g. every dealer in
    # the Excel sheet, because the Master creative may carry a different
    # dealer than the email under test) need to know which one was found so
    # they can strip it out of the master-derived Headline/Subheadline.
    dealer_name_matched: str = ""
    # Lines that content assignment could not attribute to any expected
    # field. Never used for pass/fail on their own — surfaced as a note so
    # unexpected banner copy is visible rather than silently dropped.
    unmatched_lines: List["TextLine"] = field(default_factory=list)
    # Which banner layout the fields were read against — "latest" (headline
    # at the head of the banner) or "classic" (headline at the foot), or
    # "size-bands" when the legacy size-only path was forced. Reported so a
    # run can say which guideline it read the artwork as.
    layout_used: str = ""
    # Text found elsewhere on the artwork than the block carrying the
    # message: the model badge, a number plate, a strapline in a corner.
    # Deliberately NOT part of `other_lines`, because `other_lines` feeds
    # the Dealer Name fallback and a two-character model badge is the last
    # thing that should be offered up as a dealer name. Still searched by
    # the content matcher, which looks at every line regardless of band.
    outside_block_lines: List["TextLine"] = field(default_factory=list)

    @property
    def headline_text(self) -> str:
        return " ".join(l.text for l in self.headline_lines)

    @property
    def subheadline_text(self) -> str:
        return " ".join(l.text for l in self.subheadline_lines)

    @property
    def other_text(self) -> str:
        return " ".join(l.text for l in self.other_lines)

    @property
    def dealer_text(self) -> str:
        """
        Best available Dealer Name text: the content-matched `dealer_line`
        if one was found (most reliable — see `find_dealer_line()`),
        otherwise whatever fell into the geometric `other_lines` band
        (font-size-only guess, kept as a fallback for when no expected
        dealer name was supplied to match against).

        The fallback only ever draws on the message block's own smaller
        type. Text from elsewhere on the artwork lives in
        `outside_block_lines` and is never offered here: a classic-layout
        banner whose only other text is the model badge should report no
        dealer name at all rather than hand back "X3".
        """
        if self.dealer_line is not None:
            return self.dealer_line.text
        return self.other_text

    @property
    def all_text(self) -> str:
        # preserves top-to-bottom reading order across all bands
        all_lines = sorted(
            self.headline_lines + self.subheadline_lines
            + self.other_lines + self.outside_block_lines,
            key=lambda l: l.top,
        )
        return "\n".join(l.text for l in all_lines)



def _match_tokens(text: str, match_case: bool = False) -> List[str]:
    """Word tokens, in reading order (duplicates preserved)."""
    src = text or ""
    if not match_case:
        src = src.lower()
    return re.findall(r"[A-Za-z0-9]+", src)


def spacing_tolerant_match(expected_tokens: Sequence[str],
                           found_tokens: Sequence[str]) -> Tuple[set, set]:
    """
    Matches two token streams while tolerating OCR word-splitting and
    word-merging. Returns `(matched_expected_indices, matched_found_indices)`.

    ----------------------------------------------------------------------
    Why this exists
    ----------------------------------------------------------------------
    The expected Headline is OCR'd from the Master creative and the found
    text is OCR'd from the dealer's email — two different renderings of the
    SAME artwork. Tesseract does not always break tightly-tracked display
    type at the same place in both:

        Master creative : "THEI7"  "BAYERISCHE" "MOTOREN" "WERKE"
        Dealer email    : "THE" "I7" "BAYERISCHE" "MOTOREN" "WERKE"

    Plain bag-of-words comparison sees zero overlap on the first token, so
    a pixel-perfect banner was reported as `Missing word(s): THEI7` and the
    line was not even attributed to the Headline field. The same happens in
    reverse with "ALL-IN,NO" vs "ALL-IN, NO".

    ----------------------------------------------------------------------
    Why it is still strict
    ----------------------------------------------------------------------
    A token is only ever reconciled when the characters account for each
    other EXACTLY and the tokens involved are CONSECUTIVE — "THE" + "I7"
    == "THEI7" is accepted; "NO" being a substring of "NOTHING" is not.
    Nothing is matched on partial or fuzzy grounds, so a genuinely wrong
    word is still reported as missing.

    Three passes, in decreasing strength:
      1. exact token matches (each found token consumed at most once)
      2. split:  one expected token == 2+ consecutive unused found tokens
      3. merge:  one found token == 2+ consecutive unused expected tokens
    """
    exp = list(expected_tokens)
    fnd = list(found_tokens)
    used_e: set = set()
    used_f: set = set()
    if not exp or not fnd:
        return used_e, used_f

    # Pass 1 — exact matches, first-come first-served.
    by_text: dict = {}
    for j, t in enumerate(fnd):
        by_text.setdefault(t, []).append(j)
    for i, t in enumerate(exp):
        bucket = by_text.get(t)
        if bucket:
            used_e.add(i)
            used_f.add(bucket.pop(0))

    # Pass 2 — an expected token that OCR split into several found tokens.
    for i, t in enumerate(exp):
        if i in used_e:
            continue
        for j in range(len(fnd)):
            if j in used_f:
                continue
            acc = ""
            run: List[int] = []
            k = j
            while k < len(fnd) and k not in used_f and len(acc) < len(t):
                acc += fnd[k]
                run.append(k)
                k += 1
            if acc == t and len(run) >= 2:
                used_e.add(i)
                used_f.update(run)
                break

    # Pass 3 — several expected tokens that OCR merged into one found token.
    for j, t in enumerate(fnd):
        if j in used_f:
            continue
        for i in range(len(exp)):
            if i in used_e:
                continue
            acc = ""
            run = []
            k = i
            while k < len(exp) and k not in used_e and len(acc) < len(t):
                acc += exp[k]
                run.append(k)
                k += 1
            if acc == t and len(run) >= 2:
                used_f.add(j)
                used_e.update(run)
                break

    return used_e, used_f


def tokens_covered_ratio(line_text: str, field_text: str) -> float:
    """
    Fraction of `line_text`'s own words that are accounted for by
    `field_text`, tolerating OCR word split/merge (see
    `spacing_tolerant_match`). 0.0 when the line has no words.
    """
    line_tokens = _match_tokens(line_text)
    field_tokens = _match_tokens(field_text)
    if not line_tokens or not field_tokens:
        return 0.0
    matched_line, _ = spacing_tolerant_match(line_tokens, field_tokens)
    return len(matched_line) / len(line_tokens)


def _dealer_name_tokens(text: str) -> set:
    return set(re.findall(r"[A-Za-z0-9]+", (text or "").lower()))


def _as_name_list(expected_dealer_name: Union[str, Sequence[str], None]) -> List[str]:
    """
    Normalises the `expected_dealer_name` argument, which is now allowed to
    be either a single name (as before) or a sequence of candidate names.

    A LIST matters for the Master banner: the Master creative frequently
    carries a DIFFERENT dealer than the email under test (a generic master
    built for "Bavaria Motors" is used to QA an "Infinity Cars" mailer).
    Passing every dealer name from the Excel sheet lets the master's own
    dealer line be located and lifted out of the master-derived Headline /
    Subheadline, instead of leaking into them as expected copy that the
    email under test could never possibly contain.
    """
    if not expected_dealer_name:
        return []
    if isinstance(expected_dealer_name, str):
        name = expected_dealer_name.strip()
        return [name] if name else []
    out = []
    seen = set()
    for n in expected_dealer_name:
        name = str(n or "").strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append(name)
    return out


def find_dealer_line_with_name(
    lines: List[TextLine],
    expected_dealer_name: Union[str, Sequence[str], None],
    min_ratio: float = 0.7,
    max_extra_line_tokens: int = DEALER_LINE_MAX_EXTRA_TOKENS,
) -> Tuple[Optional[TextLine], str]:
    """
    Same as `find_dealer_line()` but also returns WHICH candidate name was
    matched (empty string when nothing matched). See that function's
    docstring for the full rationale.
    """
    candidates = _as_name_list(expected_dealer_name)
    if not candidates or not lines:
        return None, ""

    best_line = None
    best_name = ""
    best_score = 0.0
    for name in candidates:
        d_tokens = _dealer_name_tokens(name)
        if not d_tokens:
            continue
        for line in lines:
            l_tokens = _dealer_name_tokens(line.text)
            if not l_tokens:
                continue
            overlap = d_tokens & l_tokens
            if not overlap:
                continue
            ratio_of_expected = len(overlap) / len(d_tokens)
            ratio_of_line = len(overlap) / len(l_tokens)
            extra_on_line = len(l_tokens - d_tokens)
            # The expected-name direction stays strict: most of the dealer
            # name's own words must be on the line, so an unrelated line
            # never matches. The line direction is relaxed by a small
            # absolute allowance so a brand-prefixed rendering ("BMW Bird
            # Automotive" for "Bird Automotive") still matches — that is 2
            # of 3 words = 0.67, which the old symmetric ratio rejected.
            line_ok = (ratio_of_line >= min_ratio) or (extra_on_line <= max_extra_line_tokens)
            if ratio_of_expected >= min_ratio and line_ok:
                # Tie-break toward the match with the most shared words, so a
                # longer, more specific dealer name beats a shorter one that
                # happens to be a prefix of it.
                score = ratio_of_expected + ratio_of_line + (0.01 * len(overlap))
                if score > best_score:
                    best_score = score
                    best_line = line
                    best_name = name
    return best_line, best_name


def find_dealer_line(
    lines: List[TextLine],
    expected_dealer_name: Union[str, Sequence[str], None],
    min_ratio: float = 0.7,
) -> Optional[TextLine]:
    """
    Content-aware Dealer Name line finder — scans ALL OCR'd lines (not
    just one font-size band) for the single line whose words most
    strongly match `expected_dealer_name`, and returns that `TextLine`
    (or None if nothing matches well enough).

    --------------------------------------------------------------------
    Why this exists / why geometry (font-size band) alone isn't enough
    --------------------------------------------------------------------
    `cluster_lines_by_size()` groups lines purely by relative font
    height. That works well for Headline vs Subheadline (which are
    usually a clearly different size), but real banners frequently
    render the Subheadline and Dealer Name at THE SAME size (e.g. a
    banner reading "BMW FUEL ADDITIVES." directly above "Bavaria
    Motors" at matching 17px OCR'd heights) — two genuinely different
    lines that height-only clustering cannot tell apart, so they get
    merged into a single band and the Dealer Name is lost.

    A pixel-geometry fix (e.g. "look for an unusually large vertical
    gap within a same-height group") was tried and rejected: real
    banners don't have a consistent gap-to-height ratio that reliably
    separates "a wrapped second line of the same block of copy" from
    "a new, distinct element below it" — the ratio for a genuine
    wrapped line on one banner can be numerically closer to the ratio
    for a genuine new-element break on another banner than either is
    to its own category. Any single threshold ends up right for some
    banners and wrong for others.

    Content matching sidesteps the geometry problem entirely: whenever
    an expected Dealer Name is available (dropdown / Manual Text /
    Excel), we already know what we're looking for, so we search for
    it directly by matching tokens rather than guessing from pixel
    size/position. This is strictly additive — it only ever "finds" a
    line when there's real word-level evidence for it, so a banner
    with no dealer name rendered at all correctly returns None instead
    of guessing.

    Matching is bidirectional (checked both ways):
      - most of the expected dealer name's own words must appear in
        the candidate line (so a line missing key dealer-name words
        doesn't match), AND
      - most of the candidate line's words must belong to the dealer
        name (so a long, unrelated line that merely happens to
        contain one shared word — e.g. a stray "BMW" — doesn't match).
    Both directions use the same `min_ratio` (default 0.7, matching
    the token-overlap fallback ratio already used elsewhere in this
    codebase for dealer-name matching, e.g. dealer_select.py).

    If more than one line meets the threshold, the single BEST-matching
    line (highest combined overlap ratio) is returned — real banners
    have at most one dealer-name line, so ties are broken toward the
    strongest match rather than the first/last positionally.

    `expected_dealer_name` may be a single name or a sequence of candidate
    names (see `_as_name_list()`); use `find_dealer_line_with_name()` when
    you also need to know which candidate matched.
    """
    line, _name = find_dealer_line_with_name(lines, expected_dealer_name, min_ratio=min_ratio)
    return line


@dataclass
class FieldAssignment:
    """Result of `assign_lines_by_content()`."""
    headline_lines: List[TextLine] = field(default_factory=list)
    subheadline_lines: List[TextLine] = field(default_factory=list)
    dealer_line: Optional[TextLine] = None
    dealer_name_matched: str = ""
    unmatched_lines: List[TextLine] = field(default_factory=list)

    @property
    def headline_text(self) -> str:
        return " ".join(l.text for l in self.headline_lines)

    @property
    def subheadline_text(self) -> str:
        return " ".join(l.text for l in self.subheadline_lines)

    @property
    def dealer_text(self) -> str:
        return self.dealer_line.text if self.dealer_line is not None else ""


def assign_lines_by_content(
    lines: List[TextLine],
    expected_headline: str = "",
    expected_subheadline: str = "",
    expected_dealer_name: Union[str, Sequence[str], None] = None,
    min_line_ratio: float = FIELD_MATCH_MIN_LINE_RATIO,
) -> FieldAssignment:
    """
    Assigns each OCR'd line to Headline / Subheadline / Dealer Name by what
    the line actually SAYS, rather than by how tall it was measured.

    Why this is needed on top of `cluster_lines_by_size()`: font-size
    banding cannot separate lines that are genuinely the same measured
    size. A real, observed banner renders

        ENGINEERED TO DELIVER OPTIMAL      (23px)
        PERFORMANCE EVERY DAY.             (23px)
        BMW FUEL ADDITIVES.                (17px)
        Infinity Cars                      (22px)   <- dealer name

    where the dealer name at 22px is within 5% of the 23px headline lines —
    inside any jitter tolerance that also has to absorb genuine OCR
    measurement noise. Geometry therefore puts the dealer name in the
    Headline band, and both Headline and Subheadline then fail on a banner
    that is completely correct. Because this app always knows what the
    fields are supposed to contain (Master JPG/PDF/HTML or Manual Text),
    matching on content sidesteps the measurement problem entirely.

    Deliberately conservative, so QA stays honest:
      - A line is assigned only when at least `min_line_ratio` of the
        LINE's own words appear in that field's expected text. A line whose
        copy is genuinely wrong therefore matches nothing, lands in
        `unmatched_lines`, and is reported — never silently absorbed into a
        field so that the field appears to pass.
      - Each line goes to its single best-scoring field, so a word shared
        between two expected values (e.g. "BMW" appearing in both) cannot
        drag a line into the wrong field.
      - Dealer name is resolved first and bidirectionally (via
        `find_dealer_line_with_name()`), since it is the strongest signal.
    """
    assignment = FieldAssignment()
    if not lines:
        return assignment

    dealer_line, dealer_name = find_dealer_line_with_name(lines, expected_dealer_name)
    assignment.dealer_line = dealer_line
    assignment.dealer_name_matched = dealer_name

    for line in lines:
        if line is dealer_line:
            continue
        if not _match_tokens(line.text):
            continue
        # Spacing-tolerant on purpose: the expected text comes from OCR of
        # the Master creative and the line comes from OCR of the dealer's
        # email, and the two do not always split tightly-tracked display
        # type at the same place ("THEI7" vs "THE i7"). Without this the
        # line matched no field at all, landed in `unmatched_lines`, and
        # the Headline was reported as missing a word that was right there
        # on the artwork. See `spacing_tolerant_match`.
        h_ratio = tokens_covered_ratio(line.text, expected_headline)
        s_ratio = tokens_covered_ratio(line.text, expected_subheadline)
        if h_ratio >= min_line_ratio and h_ratio >= s_ratio:
            assignment.headline_lines.append(line)
        elif s_ratio >= min_line_ratio:
            assignment.subheadline_lines.append(line)
        else:
            assignment.unmatched_lines.append(line)

    assignment.headline_lines.sort(key=lambda l: l.top)
    assignment.subheadline_lines.sort(key=lambda l: l.top)
    assignment.unmatched_lines.sort(key=lambda l: l.top)
    return assignment


def _try_load_paddleocr():
    """
    Attempts to construct a PaddleOCR reader. Returns None if the package
    isn't installed, or if model initialisation fails for any reason
    (e.g. no cached models and no network to fetch them) — the caller
    falls back to Tesseract in that case.
    """
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError:
        return None
    try:
        # use_angle_cls off keeps this fast/low-RAM for the mostly-horizontal
        # marketing banner text we deal with here.
        reader = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)
        return reader
    except Exception:
        return None


@lru_cache(maxsize=1)
def _get_paddle_reader():
    return _try_load_paddleocr()


def _box_height_and_center(box) -> Tuple[float, float, float]:
    """
    box: list of 4 [x, y] points (PaddleOCR polygon format), in any order.
    Returns (height, top_y, center_y).
    """
    ys = [pt[1] for pt in box]
    top = min(ys)
    bottom = max(ys)
    height = max(bottom - top, 1.0)
    center_y = (top + bottom) / 2.0
    return height, top, center_y


def _lines_with_paddle(reader, image: Image.Image) -> List[TextLine]:
    arr = np.array(image.convert("RGB"))
    result = reader.ocr(arr, cls=False)
    lines: List[TextLine] = []
    if result:
        for page in result:
            if not page:
                continue
            for det in page:
                # det = [box, (text, confidence)]
                try:
                    box = det[0]
                    text = det[1][0]
                    if not text or not text.strip():
                        continue
                    # Drop pure punctuation/symbol noise (e.g. a stray ")"
                    # picked up from a logo edge) — see Tesseract path for
                    # the same rationale.
                    if not re.search(r"[A-Za-z0-9]", text):
                        continue
                    # Drop low-confidence detections: a logo/icon read as
                    # text carries a huge bounding box that would otherwise
                    # define a bogus font-size band. PaddleOCR scores are
                    # 0.0-1.0, MIN_WORD_CONFIDENCE is a 0-100 percentage.
                    try:
                        if float(det[1][1]) * 100.0 < MIN_WORD_CONFIDENCE:
                            continue
                    except (TypeError, ValueError, IndexError):
                        pass
                    height, top, center_y = _box_height_and_center(box)
                    lines.append(TextLine(text=text.strip(), height=height, top=top, center_y=center_y))
                except Exception:
                    continue
    # top-to-bottom reading order
    lines.sort(key=lambda l: l.top)
    return lines


def _ocr_with_paddle(reader, image: Image.Image) -> str:
    return "\n".join(l.text for l in _lines_with_paddle(reader, image))


def _configure_tesseract_path_if_needed(pytesseract_module) -> None:
    """
    On Windows, the `tesseract` binary is frequently not on PATH even after
    installing it, which makes pytesseract raise
    "tesseract is not installed or it's not in your PATH". This checks a
    couple of the standard install locations and points pytesseract at the
    binary directly if found, so the app works without the user having to
    edit their PATH manually. No-op on macOS/Linux, and no-op if the
    binary is already discoverable.
    """
    import shutil
    import sys

    if shutil.which("tesseract"):
        return  # already on PATH, nothing to do

    # Hosts where apt is unavailable or broken (notably Streamlit Community
    # Cloud, whose build image currently fails `apt-get update` on an expired
    # Debian bullseye security repo) can still get a real Tesseract. This
    # downloads a self-contained build once and caches it; it is a no-op on
    # any machine that already has one, and it never raises — a False return
    # just leaves the RapidOCR fallback below to do its job.
    try:
        from .tesseract_bootstrap import ensure_tesseract
        if ensure_tesseract():
            return
    except Exception:
        pass

    if sys.platform.startswith("win"):
        import os
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                pytesseract_module.pytesseract.tesseract_cmd = path
                return


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2.0


def _measurement_words(words: List[dict]) -> List[dict]:
    """
    Picks the subset of one Tesseract line's words that may be used to
    MEASURE that line's font size. See the "Line hygiene" section of the
    module docstring for the case this exists for (a BMW roundel logo
    OCR'ing as 'oD)' at confidence 24 with a 75px box, glued onto a line of
    22px headline words, tripling that line's measured font height and
    wrecking the font-size banding for the whole banner).

    IMPORTANT — this no longer decides the line's TEXT.
    ------------------------------------------------------------------
    It used to. That was a bug, and a bad one. Both passes below can and do
    reject real banner copy: on the X1 email banner Tesseract returns

        YOU(h26,conf88) MISS(h26,96) THE(h26,96) SHOTS(h26,96)
        YOU(h26,97) DON'T(h59,94) TAKE.(h59,25)

    where the apostrophe inflates DON'T's box to 59px and TAKE. comes back
    at confidence 25 because it sits over a dark photo. The confidence pass
    deleted TAKE., the height pass deleted DON'T, and the line's text became
    "YOU MISS THE SHOTS YOU" — so a perfectly correct banner was reported
    as missing two of its own words, while `extract_text()`'s flat blob
    (which never ran these filters) still showed the full, correct string.
    The two code paths openly disagreed with each other.

    Measuring and reading are now separated: this function is only ever used
    to work out how big the type is. The line keeps every word it OCR'd.

    Two independent passes, each of which REFUSES to empty the line:
      1. confidence — drop anything below MIN_WORD_CONFIDENCE
      2. height outliers — drop anything whose box height is far from the
         median height of the (surviving) words on this line
    """
    if not words:
        return words

    confident = []
    for w in words:
        try:
            if float(w["conf"]) >= MIN_WORD_CONFIDENCE:
                confident.append(w)
        except (TypeError, ValueError):
            confident.append(w)
    kept = confident if confident else list(words)

    heights = [float(w["height"]) for w in kept if float(w["height"]) > 0]
    med = _median(heights)
    if med <= 0:
        return kept

    in_range = [
        w for w in kept
        if MIN_WORD_HEIGHT_RATIO * med <= float(w["height"]) <= MAX_WORD_HEIGHT_RATIO * med
    ]
    return in_range if in_range else kept


def _core_line_height(words: List[dict]) -> float:
    """
    The line's font size, measured robustly (see CORE_HEIGHT_MIN_RATIO).

    Takes the SMALLEST word box on the line rather than the line's overall
    top-to-bottom extent, ignoring anything small enough to be subscript
    debris. Bounding-box inflation from apostrophes, descenders and
    anti-aliasing only ever makes a box taller, never shorter, so the
    smallest box on a line of same-size type is the closest available
    estimate of the real cap height — and, crucially, it is the same
    estimate for a line with an apostrophe in it as for one without.
    """
    heights = [float(w["height"]) for w in words if float(w["height"]) > 0]
    if not heights:
        return 1.0
    med = _median(heights)
    core = [h for h in heights if h >= CORE_HEIGHT_MIN_RATIO * med]
    return max(min(core) if core else med, 1.0)


def _is_probable_debris_line(words: List[dict], text: str) -> bool:
    """
    True for a line that is almost certainly a hallucinated fragment of
    artwork rather than banner copy (see DEBRIS_LINE_MAX_* constants).

    Both conditions must hold — no word longer than
    DEBRIS_LINE_MAX_CHARS alphanumeric characters, and no word at or above
    DEBRIS_LINE_MAX_CONFIDENCE. Real banner copy clears at least one of the
    two: a short "THE i7" badge line comes back at confidence 78-87, and a
    low-confidence "THEI7" is five characters long. Only scraps that are
    both short and unsure — "eet =e See", "Pr ENG", "a" — are discarded.

    This matters more than it looks: such a scrap becomes a TextLine with a
    font height of its own, and font-size banding will happily hand it a
    band, so a reflection in a photograph can end up as the expected
    Subheadline for every dealer version of a campaign.
    """
    longest = 0
    best_conf = 0.0
    for w in words:
        longest = max(longest, len(re.sub(r"[^A-Za-z0-9]", "", w["text"] or "")))
        try:
            best_conf = max(best_conf, float(w["conf"]))
        except (TypeError, ValueError):
            return False
    if longest > DEBRIS_LINE_MAX_CHARS:
        return False
    if best_conf < DEBRIS_LINE_MAX_CONFIDENCE:
        return True
    # A whole "line" of ONE character is a scrap whatever Tesseract's
    # confidence in it, because banner copy is not one character long. The
    # measured case: a lone "x" read at confidence 55 out of the paving in
    # a photograph, three inches to the right of a headline, which font-size
    # banding then handed to the Headline field as its first word. Real
    # single characters ("THE 7") arrive on a line with other words, and a
    # genuinely isolated one is still kept by the left-alignment test in
    # `_drop_debris_lines`.
    alphanumeric = sum(
        len(re.sub(r"[^A-Za-z0-9]", "", w["text"] or "")) for w in words)
    return alphanumeric <= 1


def _tesseract_data(image: Image.Image, config: str = "") -> dict:
    import pytesseract
    _configure_tesseract_path_if_needed(pytesseract)
    return pytesseract.image_to_data(
        image.convert("RGB"), output_type=pytesseract.Output.DICT, config=config
    )


def _word_boxes(data: dict, source: str = "") -> List[dict]:
    """Flattens Tesseract's parallel-array output into word dicts.

    `source` records which rendition a box came from and namespaces the
    line key with it. Each rendition numbers its blocks/paragraphs/lines
    from zero, so the namespace is what keeps two renditions' lines from
    ever being glued together by `_group_boxes_into_lines`.
    """
    boxes: List[dict] = []
    n = len(data.get("text", []))
    confs = data.get("conf", ["-1"] * n)
    for i in range(n):
        word = (data["text"][i] or "").strip()
        if not word:
            continue
        conf = confs[i]
        try:
            if float(conf) < 0:
                continue
        except (ValueError, TypeError):
            pass
        boxes.append({
            "text": word,
            "top": float(data["top"][i]),
            "height": float(data["height"][i]),
            "left": float(data["left"][i]),
            "conf": conf,
            "key": (source, data["block_num"][i], data["par_num"][i], data["line_num"][i]),
        })
    return boxes


def _group_boxes_into_lines(boxes: List[dict]) -> List[List[dict]]:
    """Groups word boxes by Tesseract's own block/paragraph/line numbering,
    dropping symbol-only tokens (logo edges, icon fragments) first."""
    groups: dict = {}
    for w in boxes:
        if not re.search(r"[A-Za-z0-9]", w["text"]):
            continue
        groups.setdefault(w["key"], []).append(w)
    return [sorted(ws, key=lambda w: w["left"]) for ws in groups.values()]


def _upscale_factor_for(boxes: List[dict]) -> int:
    """
    How much to enlarge the image before re-OCR'ing it, based on how big
    its type actually measured on the first pass. 1 means "leave it alone".
    See the OCR_MIN_TEXT_HEIGHT_PX block for the measured cases this fixes.

    The decision is made on the median of the LINES' font sizes, not the
    median word height. A per-word median is dominated by whichever line
    happens to have the most words on it: on a real X1 banner the 9-word
    legal strapline (13px) outvoted the 5-word headline (26px), giving a
    per-word median of 13 and triggering an upscale the banner did not
    need — which then had Tesseract hallucinating "Pr"/"ENG" out of the
    building in the photograph. One vote per line is the honest measure of
    "is the type on this banner small".
    """
    line_heights = [_core_line_height(ws) for ws in _group_boxes_into_lines(boxes)]
    line_heights = [h for h in line_heights if h > 0]
    if not line_heights:
        return 1
    med = _median(line_heights)
    if med <= 0 or med >= OCR_MIN_TEXT_HEIGHT_PX:
        return 1
    factor = int(round(OCR_TARGET_TEXT_HEIGHT_PX / med))
    return max(2, min(OCR_MAX_UPSCALE, factor))


def _local_ink_rendition(image: Image.Image) -> Optional[Image.Image]:
    """The banner's type lifted off a bright, uneven background.

    See LOCAL_INK_KERNEL_PX for what the top-hat is doing and why a single
    global grey level cannot do it. Returns None when OpenCV is not
    installed or the filter fails, in which case the banner is simply read
    from the other renditions — this is an extra chance at a hard banner,
    never a requirement.
    """
    try:
        import cv2
        import numpy as np
    except Exception:
        return None
    try:
        grey = np.array(image.convert("L"))
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (LOCAL_INK_KERNEL_PX, LOCAL_INK_KERNEL_PX))
        tophat = cv2.morphologyEx(grey, cv2.MORPH_TOPHAT, kernel)
        binary = cv2.adaptiveThreshold(
            tophat, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
            LOCAL_INK_WINDOW_PX, -LOCAL_INK_BIAS)
        return Image.fromarray(255 - binary).convert("RGB")
    except Exception:
        return None


def _render_variants(image: Image.Image) -> List[Tuple[str, Image.Image]]:
    """The renditions of a banner that Tesseract is asked to read.

    "original" is the artwork as supplied, and comes first because it wins
    ties (see `_merge_rendition_lines`). "white-ink" re-renders it so that
    near-white pixels become black ink on white paper, which is the only
    way the current guideline's white-on-sky headline is readable at all
    (see WHITE_INK_THRESHOLD). "local-ink" keeps only what is too THIN to
    be background, for type set over something brighter than itself (see
    LOCAL_INK_KERNEL_PX).

    All three keep the original dimensions, so every box any of them
    returns is already in original-image coordinates.
    """
    variants: List[Tuple[str, Image.Image]] = [("original", image)]
    try:
        grey = image.convert("L")
        white_ink = grey.point(
            lambda p: 0 if p >= WHITE_INK_THRESHOLD else 255
        ).convert("RGB")
        variants.append(("white-ink", white_ink))
    except Exception:
        pass
    local_ink = _local_ink_rendition(image)
    if local_ink is not None:
        variants.append(("local-ink", local_ink))
    return variants


def _line_core_band(words: List[dict]) -> Tuple[float, float]:
    """The vertical band a line's TYPE occupies, as (top, bottom).

    Deliberately not the line's raw box extent. One inflated box — an
    apostrophe, a descender, a smear of anti-aliasing — stretches that
    extent far past the type: on the X7 master the white-ink rendition
    returns "PERFECT MATCH." spanning y=85..148, which overlaps the
    subheadline 50px below it and would make the two look like readings of
    the same line. The median word top with the robust `_core_line_height`
    on top of it stays on the type itself.
    """
    measured = _measurement_words(words)
    if not measured:
        return 0.0, 0.0
    top = _median([float(w["top"]) for w in measured])
    return top, top + _core_line_height(measured)


def _bands_overlap(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """How well two core bands agree that they are the same line of type.

    Returns the overlap as a fraction of the SHORTER band, or 0.0 when the
    two do not overlap enough to be readings of one line at all.
    """
    overlap = min(a[1], b[1]) - max(a[0], b[0])
    if overlap <= 0:
        return 0.0
    shortest = min(a[1] - a[0], b[1] - b[0])
    if shortest <= 0:
        return 0.0
    agreement = overlap / shortest
    return agreement if agreement >= RENDITION_LINE_OVERLAP_RATIO else 0.0


def _line_is_capitals(words: List[dict]) -> bool:
    """Whether this line is set in capitals, judged only on the words the
    reading is actually sure of.

    Judging on every word lets one hallucinated scrap decide. Measured on a
    real bulletin page: the badge line "THE i7" is capitals, so the lower
    case in "i7" is penalised as the OCR artefact it usually is — but a
    second rendition read the same line as "THE i7 (we)" at confidence 0
    for the scrap, which dropped the line to 50% capitals, exempted "i7"
    from the penalty, and let the reading WITH the scrap in it win.
    """
    trusted = _measurement_words(words) or words
    letters = [ch for w in trusted for ch in (w.get("text") or "") if ch.isalpha()]
    if not letters:
        return False
    return sum(1 for ch in letters if ch.isupper()) / len(letters) >= CAPS_LINE_RATIO


def _word_reading_score(word: dict, line_is_capitals: bool) -> float:
    """How much real text one word of a reading is worth.

    Characters times confidence, less the two penalties described at
    JUNK_CHARACTER_PENALTY / MIXED_CASE_PENALTY. Both penalise the shape of
    a misread rather than its content, so a genuinely wrong word on the
    artwork is scored exactly like a right one — the merge chooses between
    READINGS of a line, and must never prefer a line for what it says.
    """
    text = word.get("text") or ""
    letters = re.sub(r"[^A-Za-z0-9]", "", text)
    if not letters:
        return 0.0
    try:
        confidence = max(float(word.get("conf")), 0.0)
    except (TypeError, ValueError):
        confidence = 100.0
    penalty = 1.0
    if any((not ch.isalnum()) and ch not in _PLAIN_PUNCTUATION for ch in text):
        penalty *= JUNK_CHARACTER_PENALTY
    if line_is_capitals and any(ch.islower() for ch in text if ch.isalpha()):
        penalty *= MIXED_CASE_PENALTY
    return len(letters) * confidence * penalty


def _line_reading_score(words: List[dict]) -> float:
    capitals = _line_is_capitals(words)
    return sum(_word_reading_score(w, capitals) for w in words)


def _strip_edge_debris_words(words: List[dict]) -> List[dict]:
    """Removes a hallucinated scrap glued to the START or END of a line.

    A rendition that reads a line better than the others can still pick up
    a fragment of the artwork at one end of it and carry that fragment into
    the merged reading — measured on a real bulletin page, "ey) LET'S SKIP
    TO THE GOOD PART.", where "ey)" is a piece of the photograph.

    Deliberately narrow, and deliberately only at the ends: a word is
    dropped only when it is short AND unsure AND contains a character that
    does not belong in banner copy at all. Real short copy clears at least
    one of the three — the "50%*" in a real subheadline is punctuated but
    confident, and "THE" is short and clean. Nothing is ever removed from
    the middle of a line, where a scrap cannot be, and a line is never
    emptied.
    """
    def is_scrap(word: dict) -> bool:
        text = word.get("text") or ""
        if len(re.sub(r"[^A-Za-z0-9]", "", text)) > DEBRIS_LINE_MAX_CHARS:
            return False
        try:
            if float(word.get("conf")) >= DEBRIS_LINE_MAX_CONFIDENCE:
                return False
        except (TypeError, ValueError):
            return False
        return any((not ch.isalnum()) and ch not in _WORD_EDGE_PUNCTUATION for ch in text)

    kept = list(words)
    while len(kept) > 1 and is_scrap(kept[0]):
        kept.pop(0)
    while len(kept) > 1 and is_scrap(kept[-1]):
        kept.pop()
    return kept


def _read_lines_of(boxes: List[dict]) -> List[dict]:
    """One rendition's reading, as a list of `{band, score, words}` lines."""
    reading: List[dict] = []
    for words in _group_boxes_into_lines(boxes):
        band = _line_core_band(words)
        if band[1] <= band[0]:
            continue
        reading.append({
            "band": band,
            "score": _line_reading_score(words),
            "words": words,
        })
    return reading


def _corroborated_total(rendition: dict, every: List[dict]) -> float:
    """How much of a rendition's reading a DIFFERENT rendition also found."""
    elsewhere = [
        line for other in every if other["index"] != rendition["index"]
        for line in other["lines"]
    ]
    return sum(
        line["score"] for line in rendition["lines"]
        if any(_bands_overlap(line["band"], seen["band"]) for seen in elsewhere)
    )


def _merge_rendition_lines(readings: List[Tuple[str, List[dict]]]) -> List[dict]:
    """The best reading of every line, each taken WHOLE from one rendition.

    `readings` is `[(label, word_boxes), ...]` with the artwork as supplied
    first. The rendition that read the most CORROBORATED text becomes the
    SKELETON: its lines, and only its lines, are the banner's lines. Every
    other rendition then challenges each skeleton line it overlaps, and
    wins that line's text — never its place on the banner — by beating it by
    RENDITION_LINE_SWITCH_MARGIN. See the block above the margin for why
    the line set has to come from a single rendition.

    Ties go to the artwork as supplied, which is first in the list.
    """
    scored = [
        {"index": index, "lines": _read_lines_of(boxes)}
        for index, (_label, boxes) in enumerate(readings)
    ]
    # A rendition is scored only on the lines ANOTHER rendition can also
    # see. Something only one rendition finds is either a scrap of the
    # photograph or a line the others could not read, and neither is
    # evidence that this rendition read the BANNER better. Measured on the
    # 2GC master: white-ink read the subheadline as "GET THE BMW 2G" where
    # both other renditions read "2GC", and won the skeleton anyway on the
    # strength of two scraps ("até", "cr") that nothing else saw.
    for rendition in scored:
        rendition["total"] = _corroborated_total(rendition, scored)
    if not any(rendition["total"] for rendition in scored):
        # Nothing corroborates anything — one rendition read the banner and
        # the rest came back empty. Score them as they are, so a banner only
        # one rendition can read is still read.
        for rendition in scored:
            rendition["total"] = sum(line["score"] for line in rendition["lines"])

    skeleton = max(scored, key=lambda r: (r["total"], -r["index"]))
    slots = [dict(line) for line in skeleton["lines"]]
    if not slots:
        return []

    for rendition in scored:
        if rendition["index"] == skeleton["index"]:
            continue
        for line in rendition["lines"]:
            target, agreement = None, 0.0
            for position, slot in enumerate(slots):
                overlap = _bands_overlap(slot["band"], line["band"])
                if overlap > agreement:
                    target, agreement = position, overlap
            if target is None:
                continue
            if line["score"] > slots[target]["score"] * RENDITION_LINE_SWITCH_MARGIN:
                # The slot keeps the skeleton's band, so that every later
                # challenger is matched against the same stable geometry.
                slots[target]["score"] = line["score"]
                slots[target]["words"] = line["words"]

    return [w for slot in slots for w in _strip_edge_debris_words(slot["words"])]


def _boxes_for_renditions(image: Image.Image) -> List[dict]:
    """Reads `image` in every rendition and keeps the best reading of each
    line — see `_merge_rendition_lines` and the block above it."""
    readings: List[Tuple[str, List[dict]]] = []
    for label, rendered in _render_variants(image):
        try:
            readings.append((label, _word_boxes(_tesseract_data(rendered), source=label)))
        except Exception:
            readings.append((label, []))
    return _merge_rendition_lines(readings)


# How many banners' word boxes are remembered. A banner is OCR'd twice by
# the QA — once for its lines and once for the flat blob — and a run of
# nine adapts reads eighteen banners, so anything above a couple of dozen
# is just memory. Keyed on the pixels, so two different banners never
# collide and a re-uploaded file is recognised as the same artwork.
_WORD_BOX_CACHE_SIZE = 24
_WORD_BOX_CACHE: "OrderedDict[str, Tuple[List[dict], int]]" = OrderedDict()


def _image_fingerprint(image: Image.Image) -> Optional[str]:
    try:
        digest = hashlib.blake2b(image.tobytes(), digest_size=16)
        digest.update(f"{image.mode}:{image.size}".encode("ascii"))
        return digest.hexdigest()
    except Exception:
        return None


def _copy_boxes(boxes: List[dict]) -> List[dict]:
    """Callers get their own dicts, so nothing they do can reach the cache."""
    return [dict(box) for box in boxes]


def _tesseract_word_boxes(image: Image.Image) -> Tuple[List[dict], int]:
    """
    OCRs `image` and returns `(word_boxes, scale_used)`.

    Every pass goes through `_boxes_for_renditions`, which reads the
    artwork in each rendition and keeps the best reading of each LINE —
    without which the current guideline's white-on-sky headline produces
    either no boxes at all or half a headline, depending on the banner.

    When the first pass shows the type is too small to read reliably, the
    image is enlarged and OCR'd again — and every coordinate is divided
    back down, so the boxes are always expressed in ORIGINAL image pixels
    and no caller has to know this happened.

    Both `extract_lines()` and `extract_text()` go through here, which is
    deliberate: they used to run two different Tesseract calls with two
    different sets of filters and could return contradictory text for the
    same banner (the flat blob showed a word that the per-line output had
    silently dropped). One source of truth removes that whole class of bug.

    ...and because both of them are called for every banner, the result is
    remembered against the banner's own pixels (see _WORD_BOX_CACHE_SIZE),
    which halves the OCR a run does. The cache hands out copies, so a
    caller can do as it likes with what it gets back.
    """
    fingerprint = _image_fingerprint(image)
    if fingerprint is not None and fingerprint in _WORD_BOX_CACHE:
        cached_boxes, cached_scale = _WORD_BOX_CACHE[fingerprint]
        _WORD_BOX_CACHE.move_to_end(fingerprint)
        return _copy_boxes(cached_boxes), cached_scale

    result = _read_word_boxes(image)

    if fingerprint is not None:
        _WORD_BOX_CACHE[fingerprint] = (_copy_boxes(result[0]), result[1])
        while len(_WORD_BOX_CACHE) > _WORD_BOX_CACHE_SIZE:
            _WORD_BOX_CACHE.popitem(last=False)
    return result


def _read_word_boxes(image: Image.Image) -> Tuple[List[dict], int]:
    """`_tesseract_word_boxes` without the cache in front of it."""
    boxes = _boxes_for_renditions(image)
    factor = _upscale_factor_for(boxes)
    if factor <= 1:
        return boxes, 1

    try:
        big = image.convert("RGB")
        big = big.resize((big.width * factor, big.height * factor), Image.LANCZOS)
        big_boxes = _boxes_for_renditions(big)
    except Exception:
        return boxes, 1
    if not big_boxes:
        return boxes, 1

    for w in big_boxes:
        w["top"] /= factor
        w["height"] /= factor
        w["left"] /= factor
    return big_boxes, factor


def _line_longest_word(words: List[dict]) -> int:
    return max((len(re.sub(r"[^A-Za-z0-9]", "", w["text"] or "")) for w in words), default=0)


def _line_best_confidence(words: List[dict]) -> float:
    best = 0.0
    for w in words:
        try:
            best = max(best, float(w["conf"]))
        except (TypeError, ValueError):
            return 100.0
    return best


def _drop_debris_lines(entries, image_width: float):
    """
    Removes hallucinated scraps of artwork from a banner's line list.

    `entries` is a list of `(TextLine, words)`. A line is only a candidate
    for removal when it is BOTH short and unsure (see DEBRIS_LINE_MAX_*),
    and even then it is kept if it is left-aligned with any confident line
    on the banner — banner copy is set flush to a common left edge, stray
    OCR of a photograph is not. If the banner has no confident line at all,
    nothing is dropped: a hard-to-read banner must never come back empty.
    """
    confident = [
        (tl, ws) for tl, ws in entries
        if _line_longest_word(ws) > DEBRIS_LINE_MAX_CHARS
        and _line_best_confidence(ws) >= DEBRIS_LINE_MAX_CONFIDENCE
    ]
    if not confident:
        return entries

    tolerance = max(image_width * DEBRIS_ALIGN_TOLERANCE_RATIO, 2.0)
    confident_lefts = [min(w["left"] for w in ws) for _tl, ws in confident]

    kept = []
    for tl, ws in entries:
        if not _is_probable_debris_line(ws, tl.text):
            kept.append((tl, ws))
            continue
        left = min(w["left"] for w in ws)
        if any(abs(left - ref) <= tolerance for ref in confident_lefts):
            kept.append((tl, ws))
    return kept


def _lines_with_tesseract(image: Image.Image) -> List[TextLine]:
    """
    Groups Tesseract's word boxes into lines using its own
    block/paragraph/line numbering.

    The line KEEPS EVERY WORD IT OCR'D. Only the font-size measurement is
    taken from the filtered subset (`_measurement_words`) — see that
    function for why conflating the two silently deleted real headline
    words from perfectly correct banners.
    """
    boxes, _scale = _tesseract_word_boxes(image)

    entries = []
    # `_group_boxes_into_lines` already drops isolated punctuation/symbol
    # tokens (e.g. a stray ")" from a logo edge) so they don't get glued
    # onto the front/back of real headline/subheadline text.
    for words in _group_boxes_into_lines(boxes):
        text = " ".join(w["text"] for w in words).strip()
        if not text or not re.search(r"[A-Za-z0-9]", text):
            continue
        measured = _measurement_words(words)
        height = _core_line_height(measured)
        top = min(w["top"] for w in measured)
        bottom = max(w["top"] + w["height"] for w in measured)
        center_y = (top + bottom) / 2.0
        entries.append((TextLine(text=text, height=height, top=top, center_y=center_y), words))

    entries = _drop_debris_lines(entries, float(image.width))
    lines = [tl for tl, _ws in entries]
    lines.sort(key=lambda l: l.top)
    return lines


def _ocr_with_tesseract(image: Image.Image) -> str:
    """
    Flat, top-to-bottom banner text. Rebuilt from the SAME word boxes
    `_lines_with_tesseract()` uses, so the flat "found" text and the
    per-field "found" text can never contradict each other, and so the blob
    benefits from the small-type upscale too.
    """
    boxes, _scale = _tesseract_word_boxes(image)
    if not boxes:
        import pytesseract
        _configure_tesseract_path_if_needed(pytesseract)
        return pytesseract.image_to_string(image.convert("RGB"))

    entries = []
    for ws in _group_boxes_into_lines(boxes):
        line = " ".join(w["text"] for w in ws).strip()
        if not line:
            continue
        entries.append((TextLine(text=line, height=1.0,
                                 top=min(w["top"] for w in ws),
                                 center_y=min(w["top"] for w in ws)), ws))
    # Same debris pass the per-line path uses, so the flat text and the
    # per-field text stay consistent with each other.
    entries = _drop_debris_lines(entries, float(image.width))
    entries.sort(key=lambda e: (e[0].top, min(w["left"] for w in e[1])))
    return "\n".join(tl.text for tl, _ws in entries)



# --------------------------------------------------------------------------
# RapidOCR — a last-resort engine that needs nothing outside pip
# --------------------------------------------------------------------------
# `pip install pytesseract` only installs a WRAPPER; Tesseract itself is a
# system binary that has to be installed separately. On a machine where
# nobody ran that installer, and where PaddleOCR is also absent, every OCR
# call raised and both extract_text() and extract_lines() returned nothing
# at all. Because the banner headline exists only as pixels, that meant
# Headline / Subheadline / Dealer Name came back empty and Banner Text QA
# silently reported "No expected value provided for this field" - the
# user could not tell a blank banner apart from a missing OCR engine.
#
# RapidOCR ships its ONNX models inside the wheel and runs on onnxruntime,
# so `pip install rapidocr-onnxruntime` is genuinely all that is needed:
# no admin rights, no system package, no model download.
#
# IMPORTANT - this is ONLY an engine. It is reached exclusively when both
# PaddleOCR and Tesseract have already failed, i.e. on the exact code path
# that previously produced an empty result. The line clustering
# (cluster_lines_by_size / extract_clustered_text) is untouched, so on any
# machine where Tesseract works, behaviour is bit-for-bit what it was.
#
# Caveat worth knowing: RapidOCR tends to merge words on tightly-tracked
# display type ("BavariaMotors" for "Bavaria Motors"), which Tesseract
# handles correctly. Callers can detect this via `engine_used` and relax
# their word comparison accordingly. Tesseract remains the recommended
# engine and is preferred whenever it is present.

_RAPID_READER = None
_RAPID_TRIED = False


def _try_load_rapidocr():
    global _RAPID_READER, _RAPID_TRIED
    if _RAPID_TRIED:
        return _RAPID_READER
    _RAPID_TRIED = True
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        _RAPID_READER = RapidOCR()
    except Exception:
        _RAPID_READER = None
    return _RAPID_READER


def _lines_with_rapidocr(reader, image: Image.Image) -> List[TextLine]:
    """RapidOCR returns a flat [[box, text, score], ...]; the box is the
    same four-point polygon PaddleOCR uses, so the downstream font-height
    clustering sees exactly the same shape of data."""
    arr = np.array(image.convert("RGB"))
    result, _elapse = reader(arr)
    lines: List[TextLine] = []
    for det in (result or []):
        try:
            box, text = det[0], det[1]
            if not text or not text.strip():
                continue
            if not re.search(r"[A-Za-z0-9]", text):
                continue
            # Same confidence guard as the Paddle path: RapidOCR's score is
            # det[2], 0.0-1.0. Keeps logo/icon debris from defining a band.
            try:
                if float(det[2]) * 100.0 < MIN_WORD_CONFIDENCE:
                    continue
            except (TypeError, ValueError, IndexError):
                pass
            height, top, center_y = _box_height_and_center(box)
            lines.append(TextLine(text=text.strip(), height=height, top=top, center_y=center_y))
        except Exception:
            continue
    lines.sort(key=lambda l: l.top)
    return lines


def _ocr_with_rapidocr(reader, image: Image.Image) -> str:
    return "\n".join(l.text for l in _lines_with_rapidocr(reader, image))


def ocr_status() -> Tuple[bool, str, str]:
    """(available, engine_name, human_readable_message).

    Lets the UI say "no OCR engine is installed" outright instead of
    leaving the user staring at empty expected values with no explanation.
    """
    if _try_load_paddleocr() is not None:
        return True, "paddleocr", "PaddleOCR is active."
    try:
        import pytesseract
        _configure_tesseract_path_if_needed(pytesseract)
        pytesseract.get_tesseract_version()
        return True, "tesseract", "Tesseract is active."
    except Exception:
        pass
    if _try_load_rapidocr() is not None:
        return True, "rapidocr", (
            "Tesseract is not installed, so the bundled RapidOCR fallback is being used. "
            "It reads the banner but tends to run words together, so install Tesseract "
            "for accurate word spacing."
        )
    return False, "none", (
        "No OCR engine is available, so Headline, Subheadline and Dealer Name cannot be "
        "read from the banner and will show as empty. Install Tesseract from "
        "https://github.com/UB-Mannheim/tesseract/wiki (the app finds the default install "
        "location automatically), or run: pip install rapidocr-onnxruntime"
    )


def extract_text(image: Image.Image, prefer: str = "paddleocr") -> OCRResult:
    """
    Main entry point (unchanged behaviour). `image` must already be the
    cropped banner region — callers should never pass a full-email
    screenshot here. Returns a flat joined string, top-to-bottom.
    """
    if prefer == "paddleocr":
        reader = _get_paddle_reader()
        if reader is not None:
            try:
                text = _ocr_with_paddle(reader, image)
                return OCRResult(text=text, engine_used="paddleocr")
            except Exception as e:
                # fall through to tesseract
                fallback_warning = f"PaddleOCR failed at runtime ({e}); used Tesseract instead."
            else:
                fallback_warning = None
        else:
            fallback_warning = "PaddleOCR not installed/available; used Tesseract instead."
    else:
        fallback_warning = None

    try:
        text = _ocr_with_tesseract(image)
        return OCRResult(text=text, engine_used="tesseract", warning=fallback_warning)
    except Exception as e:
        rapid = _try_load_rapidocr()
        if rapid is not None:
            try:
                return OCRResult(
                    text=_ocr_with_rapidocr(rapid, image), engine_used="rapidocr",
                    warning=f"Tesseract unavailable ({e}); used the RapidOCR fallback.",
                )
            except Exception as e2:
                return OCRResult(text="", engine_used="none", warning=f"OCR unavailable: {e2}")
        return OCRResult(text="", engine_used="none", warning=f"OCR unavailable: {e}")


def extract_lines(image: Image.Image, prefer: str = "paddleocr") -> LinesResult:
    """
    Structured entry point: returns each OCR'd line with its font height
    and vertical position, so callers can distinguish Headline (large
    font) from Subheadline (smaller font) from other banner text. Same
    PaddleOCR-preferred / Tesseract-fallback behaviour as extract_text().
    """
    if prefer == "paddleocr":
        reader = _get_paddle_reader()
        if reader is not None:
            try:
                lines = _lines_with_paddle(reader, image)
                return LinesResult(lines=lines, engine_used="paddleocr")
            except Exception as e:
                fallback_warning = f"PaddleOCR failed at runtime ({e}); used Tesseract instead."
            else:
                fallback_warning = None
        else:
            fallback_warning = "PaddleOCR not installed/available; used Tesseract instead."
    else:
        fallback_warning = None

    try:
        lines = _lines_with_tesseract(image)
        return LinesResult(lines=lines, engine_used="tesseract", warning=fallback_warning)
    except Exception as e:
        rapid = _try_load_rapidocr()
        if rapid is not None:
            try:
                return LinesResult(
                    lines=_lines_with_rapidocr(rapid, image), engine_used="rapidocr",
                    warning=f"Tesseract unavailable ({e}); used the RapidOCR fallback.",
                )
            except Exception as e2:
                return LinesResult(lines=[], engine_used="none", warning=f"OCR unavailable: {e2}")
        return LinesResult(lines=[], engine_used="none", warning=f"OCR unavailable: {e}")


def group_lines_into_blocks(lines: List[TextLine]) -> List[List[TextLine]]:
    """Splits OCR'd lines into vertically-separated blocks of copy.

    A banner is not one run of text: it is a headline block, maybe a model
    badge somewhere else, maybe a dealer line down in a corner. A new block
    starts wherever the vertical gap to the previous line exceeds
    `BLOCK_GAP_RATIO` times the MEDIAN line height on the banner.

    The median is the right yardstick rather than either line's own height:
    on the classic layout the model badge is five times the height of the
    body copy, so measuring the gap against the badge's own height would
    swallow the entire banner into a single block, and measuring against
    the smallest line would split a two-line headline in half.
    """
    usable = [l for l in lines if l.height > 0]
    if len(usable) <= 1:
        return [sorted(usable, key=lambda l: l.top)] if usable else []

    ordered = sorted(usable, key=lambda l: l.top)
    threshold = max(_median([l.height for l in ordered]) * BLOCK_GAP_RATIO, 1.0)

    blocks: List[List[TextLine]] = [[ordered[0]]]
    for prev, cur in zip(ordered, ordered[1:]):
        gap = cur.top - (prev.top + prev.height)
        if gap > threshold:
            blocks.append([cur])
        else:
            blocks[-1].append(cur)
    return blocks


def _block_weight(block: List[TextLine]) -> float:
    """How much MESSAGE a block carries: characters weighted by type size.

    Deliberately not "tallest type wins". On the classic layout the model
    badge ("X3") is the largest thing on the artwork but carries two
    characters; the headline is smaller type but many times the text. Any
    measure that ignores length hands the headline slot to the badge.
    """
    total = 0.0
    for line in block:
        chars = len(re.sub(r"[^A-Za-z0-9]", "", line.text or ""))
        total += chars * max(line.height, 1.0)
    return total


def _block_center(block: List[TextLine]) -> float:
    if not block:
        return 0.0
    return sum(l.center_y for l in block) / len(block)


def detect_banner_layout(
    lines: List[TextLine], image_height: Optional[float] = None
) -> str:
    """Which guideline this artwork follows, from where its message sits.

    Returns BANNER_LAYOUT_LATEST when the message block sits in the upper
    half of the banner (headline set at the head, the current guideline)
    and BANNER_LAYOUT_CLASSIC when it sits in the lower half (headline in a
    band at the foot, next to the roundel).

    `image_height` is what makes the answer trustworthy, so callers that
    have the banner should always pass it. Measuring the block's position
    against the other TEXT on the banner instead cannot answer at all when
    the banner carries a single block of copy — that block is then both the
    topmost and the bottommost thing on the artwork and lands at exactly
    the midpoint — which is the common case on the current guideline, where
    the headline and subheadline are the only type on the banner. Without a
    height the text-extent fallback is used and ties resolve to CLASSIC,
    which is what the tool assumed before either guideline was named.
    """
    blocks = group_lines_into_blocks(lines)
    if not blocks:
        return BANNER_LAYOUT_CLASSIC
    message = max(blocks, key=_block_weight)
    center = _block_center(message)

    if image_height and image_height > 0:
        return BANNER_LAYOUT_LATEST if (center / image_height) < 0.5 else BANNER_LAYOUT_CLASSIC

    tops = [l.top for l in lines if l.height > 0]
    bottoms = [l.top + l.height for l in lines if l.height > 0]
    if not tops:
        return BANNER_LAYOUT_CLASSIC
    extent_top, extent_bottom = min(tops), max(bottoms)
    if extent_bottom - extent_top <= 0:
        return BANNER_LAYOUT_CLASSIC
    position = (center - extent_top) / (extent_bottom - extent_top)
    return BANNER_LAYOUT_LATEST if position < 0.5 else BANNER_LAYOUT_CLASSIC


def _pick_message_block(
    blocks: List[List[TextLine]], layout: str, image_height: Optional[float] = None
) -> Tuple[List[TextLine], str]:
    """The block whose copy the Headline/Subheadline are read from.

    `layout` AUTO picks purely on text weight, which is what identifies the
    message on both guidelines. LATEST and CLASSIC additionally constrain
    the choice to the upper / lower half of the banner, for when a reviewer
    knows which guideline the artwork follows and auto-detection has picked
    the wrong block. A forced side with no block on it falls back to the
    weightiest block rather than returning nothing.
    """
    if not blocks:
        return [], layout
    ranked = sorted(blocks, key=_block_weight, reverse=True)

    if layout in (BANNER_LAYOUT_LATEST, BANNER_LAYOUT_CLASSIC):
        centers = [_block_center(b) for b in blocks]
        if image_height and image_height > 0:
            midpoint = image_height / 2.0
        else:
            midpoint = (min(centers) + max(centers)) / 2.0 if centers else 0.0
        if layout == BANNER_LAYOUT_LATEST:
            side = [b for b in ranked if _block_center(b) <= midpoint]
        else:
            side = [b for b in ranked if _block_center(b) >= midpoint]
        if side:
            return side[0], layout

    best = ranked[0]
    resolved = detect_banner_layout(
        [l for b in blocks for l in b], image_height=image_height)
    return best, resolved


def _band_lines_by_size(
    lines: List[TextLine], jitter_tolerance_ratio: float
) -> List[List[TextLine]]:
    """Splits lines into up to three font-size bands, largest first.

    This is the original biggest-relative-gap banding, lifted out of
    `cluster_lines_by_size` unchanged so that both the layout-aware path
    and the legacy size-only path run the exact same algorithm. See
    `cluster_lines_by_size`'s docstring for why the cut points are chosen
    by the biggest gap rather than a fixed percentage.
    """
    if not lines:
        return []

    by_height_desc = sorted(lines, key=lambda l: l.height, reverse=True)
    size_groups: List[List[TextLine]] = []
    group_anchor_height = None
    for line in by_height_desc:
        if group_anchor_height is None:
            size_groups.append([line])
            group_anchor_height = line.height
        else:
            delta = group_anchor_height - line.height
            drop = delta / group_anchor_height if group_anchor_height > 0 else 0
            # A percentage tolerance alone stops working at small absolute
            # sizes: an 11px line and a 10px line are 9% apart — over the 8%
            # tolerance — purely because of pixel quantisation, and used to
            # be split into two different bands. Anything within
            # JITTER_TOLERANCE_MIN_PX is the same size, whatever the ratio.
            if drop > jitter_tolerance_ratio and delta > JITTER_TOLERANCE_MIN_PX:
                size_groups.append([line])
                group_anchor_height = line.height
            else:
                size_groups[-1].append(line)
                group_anchor_height = max(group_anchor_height, line.height)

    if len(size_groups) == 1:
        return [size_groups[0]]

    # Each size group's representative height = its tallest member (most
    # reliable single measurement, since OCR under-measures more often
    # than it over-measures on partial/descender-heavy text).
    group_heights = [max(l.height for l in g) for g in size_groups]
    gaps = []
    for i in range(len(group_heights) - 1):
        taller, shorter = group_heights[i], group_heights[i + 1]
        gaps.append((taller - shorter) / taller if taller > 0 else 0)

    num_cuts = min(2, len(gaps))
    cut_positions = sorted(
        sorted(range(len(gaps)), key=lambda i: gaps[i], reverse=True)[:num_cuts]
    )

    bands: List[List[TextLine]] = []
    band_start = 0
    for cut_idx in cut_positions:
        bands.append([l for g in size_groups[band_start:cut_idx + 1] for l in g])
        band_start = cut_idx + 1
    bands.append([l for g in size_groups[band_start:] for l in g])
    return bands


def cluster_lines_by_size(
    lines_result: LinesResult,
    jitter_tolerance_ratio: float = 0.08,
    expected_dealer_name: Union[str, Sequence[str], None] = "",
    layout: str = BANNER_LAYOUT_AUTO,
    image_height: Optional[float] = None,
) -> ClusteredLines:
    """
    Reads Headline / Subheadline / Dealer Name off a banner's OCR'd lines.

    TWO STAGES: WHERE, THEN HOW BIG
    -------------------------------
    1. `group_lines_into_blocks()` splits the lines into vertically
       separated blocks, and the block carrying the most text weight is
       taken as the banner's message. Everything else on the artwork — the
       model badge, a number plate, a dealer line in a far corner — is set
       aside into `other_lines`.
    2. Inside that block, `_band_lines_by_size()` splits by font size:
       largest band = Headline, next = Subheadline, anything smaller =
       Dealer Name / Other.

    WHY THE BLOCK STAGE EXISTS
    --------------------------
    Size alone was enough while every banner put its headline in a band at
    the foot and the OCR pass could not read the model badge set over the
    photograph. Both of those stopped being true: the current guideline
    sets the headline at the HEAD of the banner, and reading white type off
    a pale sky (see WHITE_INK_THRESHOLD) also recovers the badge. On the
    classic layout the badge is physically the largest type on the artwork,
    so pure size banding returns Headline "X3" and demotes the real
    headline to Subheadline. Weighing a block by characters-times-size
    rather than height alone puts a two-character badge where it belongs
    however large it is set, and works unchanged on both guidelines.

    `layout` is BANNER_LAYOUT_AUTO by default, which decides from the
    artwork. BANNER_LAYOUT_LATEST / BANNER_LAYOUT_CLASSIC force the message
    to be read from the upper / lower half for artwork that auto-detection
    reads wrongly. BANNER_LAYOUT_SIZE_BANDS restores the original
    size-only behaviour across every line on the banner, block stage and
    all, for comparing against how a run behaved before this change.

    Band sizing itself is unchanged: see `_band_lines_by_size()` for why
    the cut points are chosen by the biggest relative gap on THIS banner
    rather than a fixed percentage.

    --------------------------------------------------------------------
    `expected_dealer_name` — content-aware dealer-line correction
    --------------------------------------------------------------------
    Font-size geometry alone cannot always separate Subheadline from
    Dealer Name: real banners commonly render both at THE SAME size
    (e.g. "BMW FUEL ADDITIVES." directly above "Bavaria Motors" at
    matching OCR'd heights), so height-only clustering merges them into
    one band and no dedicated Dealer Name band ever forms.

    When `expected_dealer_name` is supplied (non-empty), this runs
    `find_dealer_line()` across ALL OCR'd lines — regardless of which
    band or block they landed in — looking for a line whose words strongly
    match the expected name. If a confident match is found, that exact line
    is moved out of whichever band it was sitting in (so it never also
    pollutes Headline/Subheadline text) and recorded on
    `clustered.dealer_line`. This is purely additive/corrective: if no
    confident content match is found (e.g. no dealer name is actually
    shown on this banner), the bands are returned unchanged, so a banner
    without a dealer name never gets a false dealer line invented for it.
    """
    clustered = ClusteredLines()
    if not lines_result.lines:
        clustered.layout_used = layout if layout != BANNER_LAYOUT_AUTO else ""
        return clustered

    if layout == BANNER_LAYOUT_SIZE_BANDS:
        message_lines = list(lines_result.lines)
        outside: List[TextLine] = []
        resolved_layout = BANNER_LAYOUT_SIZE_BANDS
    else:
        blocks = group_lines_into_blocks(lines_result.lines)
        message_lines, resolved_layout = _pick_message_block(
            blocks, layout, image_height=image_height)
        in_message = {id(l) for l in message_lines}
        outside = [l for l in lines_result.lines if id(l) not in in_message]

    clustered.layout_used = resolved_layout

    bands = _band_lines_by_size(message_lines, jitter_tolerance_ratio)
    if len(bands) >= 1:
        clustered.headline_lines = sorted(bands[0], key=lambda l: l.top)
    if len(bands) >= 2:
        clustered.subheadline_lines = sorted(bands[1], key=lambda l: l.top)

    # Anything in the message block smaller than the Subheadline band. This
    # is the Dealer Name band, and the `dealer_text` fallback reads it.
    clustered.other_lines = sorted(
        [l for band in bands[2:] for l in band], key=lambda l: l.top)
    # Everything else on the artwork, kept separately and never used as a
    # dealer-name guess — see `outside_block_lines`.
    clustered.outside_block_lines = sorted(outside, key=lambda l: l.top)

    _apply_content_aware_dealer_match(clustered, lines_result.lines, expected_dealer_name)
    return clustered


def _apply_content_aware_dealer_match(
    clustered: ClusteredLines,
    all_lines: List[TextLine],
    expected_dealer_name: Union[str, Sequence[str], None],
) -> None:
    """
    Shared helper used by both branches of `cluster_lines_by_size()`
    (single-band and multi-band). Searches every OCR'd line for the
    expected dealer name via `find_dealer_line()`; if found, sets
    `clustered.dealer_line` and removes that exact line from whichever
    geometric band it was sitting in (matched by identity via `is`, so
    an OCR line whose text happens to repeat elsewhere on the banner
    isn't accidentally also removed). No-op if `expected_dealer_name`
    is blank or no confident match exists.
    """
    if not _as_name_list(expected_dealer_name):
        return
    match, matched_name = find_dealer_line_with_name(all_lines, expected_dealer_name)
    if match is None:
        return
    clustered.dealer_line = match
    clustered.dealer_name_matched = matched_name
    clustered.headline_lines = [l for l in clustered.headline_lines if l is not match]
    clustered.subheadline_lines = [l for l in clustered.subheadline_lines if l is not match]
    clustered.other_lines = [l for l in clustered.other_lines if l is not match]
    clustered.outside_block_lines = [
        l for l in clustered.outside_block_lines if l is not match]


def extract_clustered_text(
    image: Image.Image,
    prefer: str = "paddleocr",
    jitter_tolerance_ratio: float = 0.08,
    expected_dealer_name: Union[str, Sequence[str], None] = "",
    layout: str = BANNER_LAYOUT_AUTO,
) -> Tuple[ClusteredLines, LinesResult]:
    """
    Convenience wrapper: OCRs the banner and returns both the size-based
    line clusters and the raw LinesResult (for the flat "found" text /
    warnings callers may still want to display).

    `layout` selects how Headline/Subheadline are located on the artwork —
    auto-detected by default; see `cluster_lines_by_size()`.

    `expected_dealer_name` is optional and passed straight through to
    `cluster_lines_by_size()` for content-aware dealer-line correction
    (see that function's docstring) — pass it whenever a dealer name is
    already known (dropdown / Manual Text / Excel) so the Dealer Name
    band is reliable even when it shares a font size with the
    Subheadline. Omitting it preserves the exact prior behaviour
    (geometric-only banding).
    """
    lines_result = extract_lines(image, prefer=prefer)
    clustered = cluster_lines_by_size(
        lines_result,
        jitter_tolerance_ratio=jitter_tolerance_ratio,
        expected_dealer_name=expected_dealer_name,
        layout=layout,
        image_height=float(getattr(image, "height", 0) or 0),
    )
    return clustered, lines_result
