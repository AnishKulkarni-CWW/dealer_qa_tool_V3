# BMW Dealer Panel QA Tool — Extended

## The interface

The app is a single-page console: a dark navigation rail, a sticky product
bar, a welcome panel, a live four-step progress rail, then the workflow —
**Upload → Validation options → Master reference → Run → Results** — with
every result set rendered as KPI tiles above a tabbed record table
(Issues / Warnings / Passed / All records).

There is **no sign-in and no history**. Every file you add lives in the
browser session and is discarded when the tab closes; nothing is written
to a server, and no run is ever stored.

Four views live on the navigation rail:

| View | What it is |
| --- | --- |
| **Home** | The welcome panel plus the whole workflow. |
| **QA Validation** | The same workflow without the welcome panel, for when you already know the tool. |
| **Settings** | Table density, whether the welcome panel and the progress rail are shown, a session reset, the OCR engine this install found, and the read-only QA thresholds from `modules/config.py`. |
| **Help** | How to run a pass, what every check does, how Master priority resolves, and the common causes when something looks wrong. |

Switching views never costs you an upload. Streamlit discards the state of
any widget it did not render on the latest run, so the workflow is always
rendered and simply hidden by CSS while another view is on screen — your
files, typed master text and last run's results are all still there when
you come back.

### Where the look lives

```
modules/theme.py     palette, navigation rail, top bar, welcome panel,
                     progress rail, cards, stat tiles, tables, and every
                     Streamlit widget restyle — one file, no colour or
                     radius set anywhere else
modules/icons.py     the inline SVG line-icon set (no emoji, no CDN)
modules/artwork.py   the welcome panel's mountains-and-car scene and the
                     navigation rail's watermark, drawn as vector art so
                     the app needs no image assets and no network
modules/results_ui.py  the one shared result-table renderer every QA
                     section goes through
.streamlit/config.toml  the palette tokens Streamlit paints its OWN
                     controls from (checkbox, toggle, focus rings)
```

Every widget kept its exact key, type and return value through the
redesign: the theme is presentation only, and removing it would leave a
working app that simply looks like stock Streamlit.

## Install

```bash
pip install -r requirements.txt
```

No admin/root password is required for any of this — `pip install` installs
into your own Python environment, exactly like your existing packages.

### About PaddleOCR
- Install it once (`pip install paddleocr paddlepaddle`).
- The **first time** you actually run an OCR check, PaddleOCR downloads its
  detection/recognition models to a local cache folder (`~/.paddleocr`).
  This is the *only* moment it needs internet.
- After that one-time download, it runs **fully offline**, and is faster /
  more accurate / lower-RAM than Tesseract for this kind of banner text.
- If PaddleOCR isn't installed, or the model download hasn't happened yet
  (e.g. fully air-gapped machine), the app **automatically falls back to
  Tesseract** — no crash, no manual switch needed. The engine choice is
  automatic; whichever one this install found is named on the navigation
  rail and in the top bar, and again under **Settings → Environment**.

### Master PDF support
Nothing extra to install. Master PDF pages are rendered with **PyMuPDF**
(`pip install pymupdf`, already in `requirements.txt`), which ships its own
renderer — the old `pdf2image` + `poppler` requirement is gone, and no
system binaries are needed on any platform.

## Running

```bash
streamlit run app.py
```

## What's new (all optional, all off by default)

1. **Excel Sheet Selection** — pick any sheet from the workbook instead of
   relying on auto-detected "Mailers - NSC ". Leave on auto-detect to keep
   old behaviour exactly.
2. **Dealer Selection dropdown** — pick a dealer explicitly; becomes the
   master dealer for Dealer Panel QA (skips HTML auto-detection).
3. **Dealer Name Validation** — opt-in checkboxes to verify the dealer name
   appears in the Banner and/or Body.
4. **Manual Text Mode** — type Headline / Subheadline / Dealer Name / Body
   Text directly; if any field is filled, it becomes the top-priority
   Master and disables Dealer Dropdown / Master JPG / PDF / HTML uploads.
5. **Master JPG** — upload a full email screenshot; auto-crops from the top
   down to the "Dear" salutation (via OCR) to isolate the banner.
6. **Master PDF** — same crop logic, with an optional page number or
   automatic "Dear"-page detection.
7. **Master HTML ZIP** — point at a master email's HTML+images zip; the
   banner `<img>` is located and extracted automatically.
8. **Banner Detection** — shared logic that finds the first genuine banner
   image (not a logo/icon) in any HTML, and pulls it from a zip.
9. **Banner Visual Comparison** — OpenCV SSIM + ORB alignment + red-box
   difference highlighting, with Master / Input / Diff / Overlay images.
10. **OCR Text Extraction** — PaddleOCR-preferred with Tesseract fallback,
    banner-only (never OCRs the whole email).
11. **Banner Text QA** — compares Headline/Subheadline/Dealer Name against
    OCR'd banner text, flags missing/extra words.
12. **Body QA (dealer name)** — opt-in dealer-in-body check.
13. **Manual Body Comparison** — As Is / To Be vs the live HTML body, with
    Added/Removed/Modified highlighting.
14. **Priority engine** — Manual Text > Master JPG > Master PDF > Master
    HTML ZIP > Dealer Dropdown > Excel. Only the single highest-priority
    active source is ever used; sources are never combined.
15. **Tabbed results** — every QA section renders as KPI tiles (total /
    passed / failed / warnings) above a tabbed record table: Issues /
    Warnings / Passed / All records, plus an Expand button that opens the
    complete table in a popup. Advanced QA adds its own Body QA / Banner
    QA / Summary tabs. The failure and warning counts sit above the tabs
    and stay on screen whichever tab is open, so nothing that needs
    attention can hide behind a tab you have not clicked.
16. **Dealer Website Link QA (V6)** — verifies the CTA button's `href`
    AND the Dealer Panel's own "Website: ..." line both belong to the
    correct dealer's domain (derived from the dealer's own name, since
    there's no dedicated Website column in Excel), and that the two agree
    with each other. Uses the dropdown-selected dealer if one is chosen,
    otherwise the auto-detected dealer — same resolution order as the
    rest of Dealer Panel QA. Domain-matched (not exact-URL), so a CTA
    button linking to a deep page like
    `https://www.bmw-birdautomotive.in/new-cars/bmw-iX1` still correctly
    passes against a panel website of `www.bmw-birdautomotive.in`. Three
    rows are added directly below the existing "Website: ..." row in the
    Content QA table: Dealer Panel Website vs dealer name, CTA Button
    Link vs dealer name, and CTA Button Link vs Dealer Panel Website.

To use the Advanced QA tabs, switch on **"Enable Advanced QA (Body /
Banner / OCR)"** in the **Validation Options** card. Left off, the run
covers Content QA, Dealer Panel Exact Match QA, Styling QA and the
Website/CTA link checks exactly as before.

## Architecture

```
app.py                      <- QA logic unchanged; only its UI section was relaid out
modules/
  config.py                 <- every threshold/constant lives here (no hardcoding elsewhere)
  results.py                <- shared QAItem / ModuleResult (Pass/Fail/Warn) types
  theme.py                  <- the entire visual system (see "The interface" above)
  icons.py                  <- inline SVG line icons
  artwork.py                <- the welcome panel / navigation rail vector artwork
  excel_loader.py            (1) sheet selection
  dealer_select.py            (2)(3) dealer dropdown + dealer-name validation
  manual_mode.py              (4) manual text master
  master_image.py             (5) JPG -> crop-to-"Dear"
  master_pdf.py                (6) PDF page select + crop-to-"Dear"
  master_html_zip.py            (7) master HTML+zip -> banner
  banner_detect.py               (8) generic banner locator (shared)
  banner_compare.py               (9) SSIM + ORB + diff overlay
  ocr_engine.py                    (10) PaddleOCR w/ Tesseract fallback
  banner_text_qa.py                 (11) banner text word-diff QA
  body_qa.py                         (12)(13) body dealer check + As-Is/To-Be diff
  priority.py                         (14) master-source priority resolver
  results_ui.py                        (15) the one shared result-table renderer
  website_link_qa.py                    (16) CTA + dealer panel website domain QA
```

Everything runs locally: OpenCV, scikit-image, Pillow, PyMuPDF,
BeautifulSoup, pandas, and (Paddle)OCR. No network calls, no cloud APIs,
Windows/macOS compatible. That includes the interface: every icon is an
inline SVG and the welcome panel's artwork is vector, so nothing is
fetched from a CDN and the app renders identically offline.
