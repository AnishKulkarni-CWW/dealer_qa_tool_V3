"""
Feature 16 — Dealer Website Link QA.

Verifies that BOTH of the following point to the correct dealer's own
website domain:
  1. The CTA button's <a href="..."> (the same blue-background button
     already located by run_style_qa's button-detection logic in app.py —
     "<a> wrapping a nested <table>, inside a colored <td>").
  2. The "Website: ..." line inside the Dealer Panel block itself (the
     same line already surfaced by the existing Content QA / Exact Match
     QA — this module doesn't replace those, it adds a THIRD, dealer-
     name-aware check on top).

--------------------------------------------------------------------------
Why domain-based matching, not exact-string matching
--------------------------------------------------------------------------
There is no dedicated "Website" column in the Excel sheet. The only
source of truth for a dealer's website is the "Website: www.bmw-
<dealer>.in" line already embedded inside that dealer's own
"Dealer Panels" cell (confirmed against the real BMW India mastersheet).
The CTA button, however, very commonly links to a DEEP PAGE on that same
domain rather than the bare homepage — e.g. the panel might say
"Website: www.bmw-birdautomotive.in" while the CTA button's real href is
"https://www.bmw-birdautomotive.in/new-cars/bmw-iX1" (confirmed against a
real sample HTML). An exact-URL comparison would therefore false-fail
every correctly-linked CTA button. The correct check is: does the CTA
href's DOMAIN match the dealer panel's own website domain, and does that
domain plausibly belong to the selected dealer (derived from the dealer
NAME, since that's the only independent source of truth available)?

--------------------------------------------------------------------------
Dealer-name -> link plausibility check
--------------------------------------------------------------------------
A dealer's link is theirs if their NAME is in it. Where in it varies, and
all three of these shapes are correct and appear in real emails:

  * in the domain          www.bmw-birdautomotive.in
  * in a BRANCH domain     www.bmw-deutschemotoren-bengaluru.in
                           (the same dealer as bmw-deutschemotoren.in)
  * in the link's PATH     https://www.bmwusedcars.in/bavaria-motors
                           (the dealer's page on a shared BMW property)

Matching the domain alone — which is what this module used to do —
failed the last two outright. So the dealer's name is looked for across
the whole link, and the cross-check between the CTA and the panel accepts
a branch domain, or two different domains that BOTH carry the dealer's
name, as agreement.


For the panel's own "Website:" line, every real domain in the sample
sheet follows the same shape:
"bmw-<slug of dealer name>[-<city/branch>].in" (or occasionally
".in/" or no "bmw-" prefix, but always containing a normalized form of
the dealer's name as a substring) — e.g. "Bird Automotive" ->
"bmw-birdautomotive.in", "Krishna Automotive" ->
"bmw-krishnaautomotive.in", "KUN Exclusive" (Chennai) ->
"bmw-kunexclusive-chennai.in". So the dealer NAME, with spaces/
punctuation stripped and lowercased, must appear as a substring of the
domain. This is the actual verification signal — not just "does the CTA
href match the panel's Website line" (which could both be wrong, e.g. if
someone pastes another dealer's link into both places, that structural
copy/paste error should still be caught).

Three independent sub-checks are produced (each becomes a row in Content
QA):
  1. Dealer Panel Website domain contains the dealer's own name.
  2. CTA Button Link domain contains the dealer's own name.
  3. CTA Button Link and Dealer Panel Website agree — same domain, one a
     branch of the other, or two domains that both carry this dealer's
     name (regardless of #1/#2 individually).

Any of these can fail independently — e.g. the CTA could correctly point
to the dealer's own site while the panel's Website line has a typo, or
vice versa, or both could point to entirely the wrong (some other
dealer's) domain.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup, Tag


@dataclass
class WebsiteLinkQAResult:
    rows: List[dict]  # each: {"item": str, "status": str, "detail": str}


def _normalize_dealer_slug(dealer_name: str) -> str:
    """Lowercases and strips everything except letters/digits, so 'Bird
    Automotive', 'BIRD AUTOMOTIVE', and 'Bird-Automotive' all normalize to
    the same comparable slug 'birdautomotive'. Trailing corporate suffix
    noise (Pvt/Ltd/Private/Limited) is stripped first since it never
    appears in the actual domain slug."""
    name = dealer_name or ""
    name = re.sub(r"\b(pvt|ltd|private|limited)\b", "", name, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _extract_domain(url: str) -> Optional[str]:
    """Pulls the bare registrable-ish domain out of a URL/href, lowercased,
    stripped of scheme/www/path/query. e.g.
    'https://www.bmw-birdautomotive.in/new-cars/bmw-iX1' -> 'bmw-birdautomotive.in'
    'www.bmw-birdautomotive.in' -> 'bmw-birdautomotive.in'
    """
    if not url:
        return None
    u = url.strip().lower()
    u = re.sub(r"^[a-z]+://", "", u)          # strip scheme
    u = u.split("/")[0]                        # strip path/query/fragment
    u = u.split("?")[0].split("#")[0]
    u = re.sub(r"^www\.", "", u)                # strip leading www.
    u = u.rstrip(".")
    if not u or "." not in u:
        return None
    return u


# Public suffixes that take two labels, so the "core" of a domain is what
# is left once the suffix is removed. Anything not listed loses one label.
_TWO_LABEL_SUFFIXES = (
    ".co.in", ".net.in", ".org.in", ".gov.in", ".ac.in", ".co.uk",
    ".org.uk", ".com.au", ".co.nz", ".com.sg",
)


def _domain_core(domain: Optional[str]) -> str:
    """A domain with its public suffix stripped and reduced to letters and
    digits: 'bmw-deutschemotoren-bengaluru.in' -> 'bmwdeutschemotorenbengaluru'."""
    if not domain:
        return ""
    d = domain.lower().rstrip(".")
    for suffix in _TWO_LABEL_SUFFIXES:
        if d.endswith(suffix):
            d = d[: -len(suffix)]
            break
    else:
        if "." in d:
            d = d.rsplit(".", 1)[0]
    return re.sub(r"[^a-z0-9]", "", d)


def _url_letters(url: Optional[str]) -> str:
    """The WHOLE link reduced to letters and digits — host, path and all.

    The host alone is not enough. A dealer's CTA is routinely a page ABOUT
    that dealer on a shared BMW property, e.g.
    'https://www.bmwusedcars.in/bavaria-motors': the host names the
    property and the PATH names the dealer. Matching the host only failed
    every one of those, so the dealer's name is looked for across the
    entire link.
    """
    if not url:
        return ""
    u = re.sub(r"^[a-z]+://", "", str(url).strip().lower())
    u = u.split("?")[0].split("#")[0]
    return re.sub(r"[^a-z0-9]", "", u)


def _dealer_name_in_url(url: Optional[str], dealer_name: str) -> Tuple[bool, str]:
    """Does this link belong to `dealer_name`, and on what evidence?

    Returns (matched, where) with `where` one of "domain", "link" or "".
    The distinction is reported to the reviewer, because "the dealer's
    name is in the domain" and "the dealer's name is further along the
    link" are different degrees of confidence and they should be able to
    see which one they got.
    """
    slug = _normalize_dealer_slug(dealer_name)
    if not slug or not url:
        return False, ""
    domain = _extract_domain(url)
    if domain and slug in re.sub(r"[^a-z0-9]", "", domain):
        return True, "domain"
    if slug in _url_letters(url):
        return True, "link"
    return False, ""


def _domain_matches_dealer(domain: Optional[str], dealer_name: str) -> bool:
    if not domain:
        return False
    slug = _normalize_dealer_slug(dealer_name)
    if not slug:
        return False
    domain_letters = re.sub(r"[^a-z0-9]", "", domain)
    return slug in domain_letters


def _domains_same_dealer(a: Optional[str], b: Optional[str]) -> Tuple[bool, str]:
    """Do two domains belong to the same dealer?

    Equal domains obviously do. So does a branch domain that EXTENDS the
    other — 'bmw-deutschemotoren-bengaluru.in' against
    'bmw-deutschemotoren.in' — which is how dealers with more than one
    outlet are actually set up, and which a straight equality test failed.
    Returns (same, reason_fragment).
    """
    if not a or not b:
        return False, ""
    if a == b:
        return True, "matches"
    core_a, core_b = _domain_core(a), _domain_core(b)
    if core_a and core_b and (core_a in core_b or core_b in core_a):
        return True, "is a branch of the same domain as"
    return False, ""


def _find_dealer_panel_website_line(panel_text: str) -> Optional[str]:
    for line in (panel_text or "").splitlines():
        if re.search(r"\bwebsite\s*:?", line, re.IGNORECASE) or re.search(
            r"https?://|www\.", line, re.IGNORECASE
        ):
            m = re.search(r"(https?://\S+|www\.\S+)", line, re.IGNORECASE)
            if m:
                return m.group(1).strip().rstrip(".,;")
    return None


def find_cta_button_href(html_raw: str) -> Optional[str]:
    """
    Locates the CTA button using the SAME detection rule already used by
    app.py's run_style_qa button-border-radius check: an <a> that wraps a
    nested <table> AND sits inside an ancestor <td> that has a
    bgcolor/background-color set. Returns that <a>'s href, or None if no
    such button exists. If multiple qualifying buttons exist, the FIRST
    one found (document order) is used — matching how run_style_qa already
    reports on "the" button.
    """
    soup = BeautifulSoup(html_raw, "html.parser")
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        inner_table = a.find("table")
        if inner_table is None:
            continue
        ancestor_tds = a.find_parents("td")
        if not ancestor_tds:
            continue
        for td in ancestor_tds:
            bgcolor = (td.get("bgcolor") or "").strip()
            style = (td.get("style") or "").lower()
            if bgcolor or "background-color" in style:
                return href.strip() if href else None
    return None


def run_website_link_qa(
    dealer_name: str,
    panel_text: str,
    html_raw: str,
    dealer_mismatch: bool = False,
) -> WebsiteLinkQAResult:
    """
    Produces up to 3 Content-QA-style rows (item/status/detail), inserted
    right after the existing 'Website: ...' Content QA row:
      1. Dealer Panel Website matches dealer name
      2. CTA Button Link matches dealer name
      3. CTA Button Link matches Dealer Panel Website
    If dealer_name is empty, all three are skipped (returns no rows) —
    there is nothing to validate against.

    `dealer_mismatch`: True when the dropdown-SELECTED dealer (whose
    `panel_text` this function was given — it always comes straight from
    the Excel sheet, see app.py's call site) is NOT the dealer this
    email's HTML actually belongs to (per the app's existing
    auto-detected-vs-selected comparison). This must be passed in
    explicitly because Row 1 (Dealer Panel Website vs dealer name) reads
    `panel_text`, which is ALWAYS the selected dealer's own Excel data —
    it never looks at the HTML at all. Without this flag, Row 1 is a
    tautology (an Excel cell's website always "corresponds to" that same
    cell's own dealer name) and would incorrectly PASS even when the
    email on screen is a completely different dealer's email — exactly
    the bug this parameter exists to close. When True, Row 1 (and Row 3,
    the CTA-vs-panel cross-check, which is equally meaningless when the
    panel itself isn't even the right dealer's panel) are forced to
    "Fail" regardless of what their own domain-matching would otherwise
    conclude, since the panel being shown is definitionally the wrong
    one for this email. Row 2 (CTA Button vs dealer name) is NOT forced
    here — it already independently reads the HTML's actual CTA href, so
    it already correctly fails/passes on its own real signal.
    """
    rows: List[dict] = []
    if not dealer_name or not dealer_name.strip():
        return WebsiteLinkQAResult(rows=rows)

    panel_website_raw = _find_dealer_panel_website_line(panel_text)
    panel_domain = _extract_domain(panel_website_raw) if panel_website_raw else None

    cta_href = find_cta_button_href(html_raw)
    cta_domain = _extract_domain(cta_href) if cta_href else None

    # ---- Row 1: Dealer Panel Website vs dealer name ----
    if panel_domain is None:
        rows.append({
            "item": "Website check (Dealer Panel): no website line found",
            "status": "Warn",
            "detail": f"No 'Website:' line with a URL could be found in {dealer_name}'s dealer panel text.",
        })
    elif dealer_mismatch:
        rows.append({
            "item": f"Website check (Dealer Panel): {panel_website_raw}",
            "status": "Fail",
            "detail": (
                f"Dealer mismatch — the Dealer Panel data (including this Website line) belongs to the "
                f"SELECTED dealer '{dealer_name}', but this email's HTML is not that dealer's email, so this "
                f"panel/website should not be considered validated for what's actually on screen."
            ),
        })
    else:
        ok = _domain_matches_dealer(panel_domain, dealer_name)
        rows.append({
            "item": f"Website check (Dealer Panel): {panel_website_raw}",
            "status": "Pass" if ok else "Fail",
            "detail": (
                f"Dealer Panel website domain '{panel_domain}' "
                f"{'correctly corresponds to' if ok else 'does NOT correspond to'} dealer '{dealer_name}'."
            ),
        })

    # ---- Row 2: CTA Button Link vs dealer name ----
    if cta_href is None:
        rows.append({
            "item": "Website check (CTA Button): no CTA button found",
            "status": "Warn",
            "detail": "No CTA-style button (linked, colored <td> wrapping a nested button table) was found in the HTML to check its link.",
        })
    elif cta_domain is None:
        rows.append({
            "item": f"Website check (CTA Button): {cta_href}",
            "status": "Warn",
            "detail": f"CTA button href '{cta_href}' does not look like a valid website URL.",
        })
    else:
        ok, where = _dealer_name_in_url(cta_href, dealer_name)
        if ok and where == "domain":
            detail = (
                f"CTA button link domain '{cta_domain}' correctly corresponds to "
                f"dealer '{dealer_name}'."
            )
        elif ok:
            detail = (
                f"CTA button link correctly corresponds to dealer '{dealer_name}' — the "
                f"dealer is named in the link itself ('{cta_href}') rather than in the "
                f"domain '{cta_domain}', which is how a dealer's page on a shared BMW "
                f"property is linked."
            )
        else:
            detail = (
                f"CTA button link '{cta_href}' does NOT correspond to dealer "
                f"'{dealer_name}' — the dealer's name appears neither in the domain "
                f"'{cta_domain}' nor anywhere else in the link."
            )
        rows.append({
            "item": f"Website check (CTA Button): {cta_href}",
            "status": "Pass" if ok else "Fail",
            "detail": detail,
        })

    # ---- Row 3: CTA Button Link vs Dealer Panel Website (cross-check) ----
    if panel_domain is not None and cta_domain is not None:
        same_domain, how = _domains_same_dealer(cta_domain, panel_domain)
        # Two different domains still agree when BOTH demonstrably belong
        # to this dealer: the panel points at the dealer's own site while
        # the CTA points at that dealer's page on a shared BMW property.
        cta_is_dealers = _dealer_name_in_url(cta_href, dealer_name)[0]
        panel_is_dealers = _dealer_name_in_url(panel_website_raw, dealer_name)[0]
        both_the_dealers = cta_is_dealers and panel_is_dealers

        ok = same_domain or both_the_dealers
        if same_domain:
            detail = (
                f"CTA button domain ('{cta_domain}') {how} the Dealer Panel website "
                f"domain ('{panel_domain}')."
            )
        elif both_the_dealers:
            detail = (
                f"CTA button domain ('{cta_domain}') is a different domain from the Dealer "
                f"Panel website ('{panel_domain}'), but both belong to dealer "
                f"'{dealer_name}' — the panel links the dealer's own site and the CTA links "
                f"that dealer's page on another BMW property."
            )
        else:
            detail = (
                f"CTA button domain ('{cta_domain}') does NOT match the Dealer Panel website "
                f"domain ('{panel_domain}'), and the two do not both belong to dealer "
                f"'{dealer_name}'."
            )
        if dealer_mismatch and not ok:
            detail += (
                f" This is expected: the Dealer Panel shown belongs to the SELECTED dealer "
                f"'{dealer_name}', not the dealer this email's HTML actually belongs to."
            )
        rows.append({
            "item": f"Website check (CTA vs Panel match): {cta_domain} vs {panel_domain}",
            "status": "Pass" if ok else "Fail",
            "detail": detail,
        })

    return WebsiteLinkQAResult(rows=rows)
