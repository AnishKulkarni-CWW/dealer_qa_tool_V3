"""Inline SVG icon set.

Every icon in the redesign is an inline SVG string rather than an emoji or
an external image, for three reasons:

  * the app must keep working with no network access (Streamlit Community
    Cloud containers and locked-down office machines both), so an
    `<img src="https://...">` is not an option;
  * emoji render differently on Windows / macOS / Linux and immediately
    break the clean product look the design calls for;
  * an inline SVG inherits `currentColor`, so one icon works on a white
    card, on a tinted stat tile and on the dark navy sidebar without a
    second copy.

`icon(name, size)` returns a bare `<svg>` string safe to drop into any
`st.markdown(..., unsafe_allow_html=True)` block. `data_uri(name, colour)`
returns the same icon as a `url("data:image/svg+xml,...")` value for CSS
`background-image`, which is how the sidebar nav gets real line icons on
labels that Streamlit renders as plain text.
"""

from __future__ import annotations

from urllib.parse import quote

# Every path is drawn on a 24x24 grid with a 1.75 stroke, no fill, round
# caps/joins — one consistent line-icon family.
_PATHS = {
    # navigation
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5.5 9.5V20h13V9.5"/><path d="M9.75 20v-5.5h4.5V20"/>',
    "check-square": '<rect x="3" y="3" width="18" height="18" rx="4"/><path d="M8 12.2l2.6 2.6L16 9.4"/>',
    "settings": '<circle cx="12" cy="12" r="3.1"/><path d="M19.4 15a1.6 1.6 0 0 0 .32 1.77l.06.06a1.9 1.9 0 1 1-2.7 2.7l-.05-.06a1.6 1.6 0 0 0-1.78-.32 1.6 1.6 0 0 0-.97 1.47V21a1.9 1.9 0 1 1-3.8 0v-.1a1.6 1.6 0 0 0-1.04-1.46 1.6 1.6 0 0 0-1.77.32l-.06.06a1.9 1.9 0 1 1-2.7-2.7l.06-.06a1.6 1.6 0 0 0 .32-1.77 1.6 1.6 0 0 0-1.47-.98H3a1.9 1.9 0 1 1 0-3.8h.1a1.6 1.6 0 0 0 1.46-1.04 1.6 1.6 0 0 0-.32-1.77l-.06-.06a1.9 1.9 0 1 1 2.7-2.7l.06.06a1.6 1.6 0 0 0 1.77.32H9a1.6 1.6 0 0 0 .97-1.47V3a1.9 1.9 0 1 1 3.8 0v.1a1.6 1.6 0 0 0 .97 1.46 1.6 1.6 0 0 0 1.78-.32l.05-.06a1.9 1.9 0 1 1 2.7 2.7l-.06.06a1.6 1.6 0 0 0-.32 1.77V9a1.6 1.6 0 0 0 1.47.97H21a1.9 1.9 0 1 1 0 3.8h-.1a1.6 1.6 0 0 0-1.46.97z"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.3 2.4c-.6.2-.9.8-.9 1.4v.5"/><circle cx="12" cy="17" r=".9" fill="currentColor" stroke="none"/>',

    # workflow / cards
    "upload-cloud": '<path d="M16.6 17.5A4.4 4.4 0 0 0 17 8.8a6 6 0 0 0-11.5 1.4A3.9 3.9 0 0 0 6.5 18"/><path d="M12 20V11"/><path d="m8.6 13.8 3.4-3.4 3.4 3.4"/>',
    "file": '<path d="M14 3H7.5A1.5 1.5 0 0 0 6 4.5v15A1.5 1.5 0 0 0 7.5 21h9a1.5 1.5 0 0 0 1.5-1.5V7z"/><path d="M14 3v4h4"/>',
    "sliders": '<path d="M4 8h10"/><path d="M18 8h2"/><path d="M4 16h4"/><path d="M12 16h8"/><circle cx="16" cy="8" r="2.1"/><circle cx="10" cy="16" r="2.1"/>',
    "play": '<path d="M7 4.8v14.4L19.5 12z"/>',
    "chart": '<path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M3 20h18"/>',
    "layers": '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
    "route": '<circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/><path d="M9 6.5h5.5A3.5 3.5 0 0 1 18 10v0a3.5 3.5 0 0 1-3.5 3.5h-5A3.5 3.5 0 0 0 6 17v.5"/>',
    "type": '<path d="M4 6.5V5h16v1.5"/><path d="M12 5v14"/><path d="M9 19h6"/>',
    "image": '<rect x="3" y="4.5" width="18" height="15" rx="2.5"/><circle cx="8.5" cy="10" r="1.6"/><path d="m4 17 4.5-4.2a2 2 0 0 1 2.7 0L20 20"/>',
    "pdf": '<path d="M14 3H7.5A1.5 1.5 0 0 0 6 4.5v15A1.5 1.5 0 0 0 7.5 21h9a1.5 1.5 0 0 0 1.5-1.5V7z"/><path d="M14 3v4h4"/><path d="M9 16.5h1.4a1.2 1.2 0 0 0 0-2.4H9v4.4"/><path d="M14 14.1v4.4h.8a2.2 2.2 0 0 0 0-4.4z"/>',
    "archive": '<rect x="3" y="4" width="18" height="4.5" rx="1.5"/><path d="M5 8.5V19a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V8.5"/><path d="M10 12.5h4"/>',
    "text": '<path d="M4 6h16"/><path d="M4 11h16"/><path d="M4 16h10"/>',
    "compare": '<path d="M12 3v18"/><path d="M7 7 3 12l4 5"/><path d="m17 7 4 5-4 5"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-3.6-3.6"/>',
    "download": '<path d="M12 3.5v11"/><path d="m7.8 10.3 4.2 4.2 4.2-4.2"/><path d="M4.5 18.5v1A1.5 1.5 0 0 0 6 21h12a1.5 1.5 0 0 0 1.5-1.5v-1"/>',
    "trash": '<path d="M4 6.5h16"/><path d="M9.5 6.5V5A1.5 1.5 0 0 1 11 3.5h2A1.5 1.5 0 0 1 14.5 5v1.5"/><path d="M6.5 6.5 7.4 20a1.5 1.5 0 0 0 1.5 1.4h6.2a1.5 1.5 0 0 0 1.5-1.4l.9-13.5"/>',
    "refresh": '<path d="M20 11.5a8 8 0 1 0-.8 4.4"/><path d="M20 4.5v6h-6"/>',

    # status / stats
    "doc-stack": '<path d="M8 3.5h7L19 7v11.5A1.5 1.5 0 0 1 17.5 20h-9A1.5 1.5 0 0 1 7 18.5V5A1.5 1.5 0 0 1 8.5 3.5z"/><path d="M15 3.5V7h4"/><path d="M5 7v12.5A2.5 2.5 0 0 0 7.5 22H16"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8.2 12.3 2.6 2.6 5-5.2"/>',
    "x-circle": '<circle cx="12" cy="12" r="9"/><path d="m9.2 9.2 5.6 5.6"/><path d="m14.8 9.2-5.6 5.6"/>',
    "alert-triangle": '<path d="M10.7 4.2 3.3 17a1.5 1.5 0 0 0 1.3 2.3h14.8a1.5 1.5 0 0 0 1.3-2.3L13.3 4.2a1.5 1.5 0 0 0-2.6 0z"/><path d="M12 9.5v4"/><circle cx="12" cy="16.6" r=".9" fill="currentColor" stroke="none"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><circle cx="12" cy="7.9" r=".95" fill="currentColor" stroke="none"/>',
    "zap": '<path d="M13.4 2.5 4.8 13.2h6l-1.2 8.3 8.6-10.7h-6z"/>',
    "shield": '<path d="M12 3 5 5.8v5.5c0 4.3 2.9 8.2 7 9.4 4.1-1.2 7-5.1 7-9.4V5.8z"/><path d="m9.2 12 2.1 2.1 4-4.2"/>',
    "car": '<path d="M5 15.5h14"/><path d="M6.5 15.5 8 9.8A2 2 0 0 1 9.9 8.3h4.2A2 2 0 0 1 16 9.8l1.5 5.7"/><path d="M4 15.5h16v3.2h-2.6"/><circle cx="8" cy="18.7" r="1.8"/><circle cx="16" cy="18.7" r="1.8"/>',
    "chevron-right": '<path d="m9.5 6 6 6-6 6"/>',
}


def icon(name: str, size: int = 18, stroke: float = 1.75, cls: str = "") -> str:
    """One inline `<svg>` string, coloured by the CSS `color` it inherits."""
    body = _PATHS.get(name, _PATHS["info"])
    class_attr = f' class="{cls}"' if cls else ""
    return (
        f'<svg{class_attr} width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="currentColor" stroke-width="{stroke}" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
        f'focusable="false">{body}</svg>'
    )


def data_uri(name: str, colour: str = "#FFFFFF", stroke: float = 1.75) -> str:
    """The same icon as a CSS `url("data:image/svg+xml,...")` value.

    Used where the mark has to ride on an element whose text content
    Streamlit owns (the sidebar nav labels), so it can only be placed with
    a `background-image` on a pseudo-element.
    """
    body = _PATHS.get(name, _PATHS["info"])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        f'viewBox="0 0 24 24" fill="none" stroke="{colour}" '
        f'stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    )
    return f'url("data:image/svg+xml,{quote(svg, safe="")}")'
