"""Decorative inline SVG artwork (hero scene + sidebar watermark).

The design reference puts a car photographed against a mountain range
behind the welcome panel and a second car at the foot of the navigation
rail. Shipping actual photography would mean either bundling large binary
assets into the repo or fetching them over the network at render time —
and the network is exactly what this tool cannot depend on (it is run
from locked-down office machines and from a Streamlit Community Cloud
container with no outbound allowance for asset CDNs).

So the same composition is drawn as vector art instead: layered mountain
silhouettes, a horizon, a light sweep, and a modern SUV in profile. It is
a few kilobytes of markup, scales to any width without a second asset,
recolours with the palette, and costs nothing to load.

A NOTE ON CONTRAST
------------------
The first pass of this file used near-white blues and a full-width white
sweep across the scene. The result was mathematically present and
visually absent: sampled pixels came back at rgb(243,248,254) against a
rgb(248,250,255) panel, i.e. a car nobody could see. The palette below is
deliberately several steps darker, and the white sweep now fades out by
the 58% mark so the right-hand third of the panel keeps full contrast —
the fade exists to protect the headline's legibility on the LEFT, not to
erase the picture on the right.
"""

from __future__ import annotations


def _suv(body: str, body_dark: str, glass: str, trim: str, tyre: str,
         opacity: float = 1.0) -> str:
    """A modern SUV in profile, drawn on a 520x230 grid.

    Wheel centres sit at y=186 with r=36, so the tyres touch y=222: place
    the group with `translate(x, groundY - 222)` to stand it on a horizon.
    """
    return f"""
<g opacity="{opacity}">
<ellipse cx="262" cy="224" rx="238" ry="12" fill="{tyre}" opacity=".22"/>
<path d="M16 152c0-31 13-46 42-52l70-13c23-33 62-47 124-47 68 0 109 16 134 49l74 14c35 6 52 21 54 45l1 19c0 8-6 13-15 13h-40a36 36 0 0 0-72 0H194a36 36 0 0 0-72 0H31c-10 0-16-7-15-18z" fill="{body}"/>
<path d="M16 152c0-31 13-46 42-52l70-13 6 9-76 15c-26 5-38 19-38 45z" fill="{body_dark}" opacity=".5"/>
<path d="M154 99c19-28 52-40 100-41v41z" fill="{glass}"/>
<path d="M270 58c46 1 80 14 100 41H270z" fill="{glass}"/>
<path d="M258 58v41h6V58z" fill="{body_dark}" opacity=".7"/>
<path d="M18 140h58c7 0 11 5 11 11s-4 11-11 11H17z" fill="{trim}"/>
<path d="M452 142h44c7 0 11 4 11 10s-4 10-11 10h-44z" fill="{trim}" opacity=".8"/>
<path d="M128 168h264" stroke="{body_dark}" stroke-width="3.5" opacity=".45" stroke-linecap="round"/>
<path d="M40 122h44" stroke="{glass}" stroke-width="5" opacity=".55" stroke-linecap="round"/>
<circle cx="158" cy="186" r="36" fill="{tyre}"/>
<circle cx="158" cy="186" r="18" fill="{glass}"/>
<circle cx="158" cy="186" r="8" fill="{tyre}" opacity=".55"/>
<circle cx="418" cy="186" r="36" fill="{tyre}"/>
<circle cx="418" cy="186" r="18" fill="{glass}"/>
<circle cx="418" cy="186" r="8" fill="{tyre}" opacity=".55"/>
</g>
"""


def hero_scene(uid: str = "dqhero") -> str:
    """The wide mountains-and-car scene behind the welcome panel."""
    return f"""
<svg class="dq-hero-art" viewBox="0 0 1200 420" preserveAspectRatio="xMaxYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
<defs>
<linearGradient id="{uid}-sky" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#CFE1F6"/>
<stop offset="60%" stop-color="#E6F0FB"/>
<stop offset="100%" stop-color="#F4F9FF"/>
</linearGradient>
<linearGradient id="{uid}-far" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#8AAAD2"/>
<stop offset="100%" stop-color="#B9CFE9"/>
</linearGradient>
<linearGradient id="{uid}-mid" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#6489BC"/>
<stop offset="100%" stop-color="#9DBBDF"/>
</linearGradient>
<linearGradient id="{uid}-near" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#456A9E"/>
<stop offset="100%" stop-color="#7EA2CE"/>
</linearGradient>
<linearGradient id="{uid}-ground" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#D7E4F4"/>
<stop offset="100%" stop-color="#EEF4FC"/>
</linearGradient>
<linearGradient id="{uid}-fade" x1="0" y1="0" x2="1" y2="0">
<stop offset="0%" stop-color="#FFFFFF" stop-opacity="1"/>
<stop offset="26%" stop-color="#FFFFFF" stop-opacity=".9"/>
<stop offset="44%" stop-color="#FFFFFF" stop-opacity=".45"/>
<stop offset="57%" stop-color="#FFFFFF" stop-opacity="0"/>
<stop offset="100%" stop-color="#FFFFFF" stop-opacity="0"/>
</linearGradient>
</defs>
<rect width="1200" height="420" fill="url(#{uid}-sky)"/>
<circle cx="1004" cy="86" r="46" fill="#FFFFFF" opacity=".6"/>
<path d="M0 214 132 112l86 68 108-88 122 114 96-52 132 100 118-80 148 118 158-70 100 40v192H0z" fill="url(#{uid}-far)" opacity=".7"/>
<path d="M132 112l30 24-20 14-24-20z" fill="#FFFFFF" opacity=".85"/>
<path d="M0 258 118 168l104 82 124-88 130 110 116-66 140 94 152-92 166 112 150-40v168H0z" fill="url(#{uid}-mid)" opacity=".72"/>
<path d="M118 168l28 22-18 12-22-18z" fill="#FFFFFF" opacity=".7"/>
<path d="M0 292 156 206l128 74 136-58 142 86 152-62 170 92 148-54 168 78v154H0z" fill="url(#{uid}-near)" opacity=".68"/>
<rect y="318" width="1200" height="102" fill="url(#{uid}-ground)"/>
<path d="M0 319h1200" stroke="#A8C4E4" stroke-width="2"/>
<path d="M500 366h410" stroke="#BFD4EC" stroke-width="4" stroke-linecap="round" stroke-dasharray="38 30"/>
<g transform="translate(404 96)">
{_suv(body="#3E6EAE", body_dark="#1F477E", glass="#E7F0FB", trim="#16365F", tyre="#1B3557")}
</g>
<rect width="1200" height="420" fill="url(#{uid}-fade)"/>
</svg>
"""


def sidebar_watermark(uid: str = "dqside") -> str:
    """The car that anchors the bottom of the navigation rail."""
    return f"""
<svg class="dq-side-art" viewBox="0 0 620 300" preserveAspectRatio="xMidYMax meet" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
<defs>
<linearGradient id="{uid}-glow" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#3C6FB4" stop-opacity="0"/>
<stop offset="100%" stop-color="#4E86CE" stop-opacity=".30"/>
</linearGradient>
</defs>
<rect width="620" height="300" fill="url(#{uid}-glow)"/>
<path d="M0 214 116 146l90 48 104-56 106 66 110-50 94 44v100H0z" fill="#5E8CC8" opacity=".22"/>
<path d="M0 262h620" stroke="#7FA9DC" stroke-width="2" opacity=".35"/>
<g transform="translate(50 40)">
{_suv(body="#6C97D2", body_dark="#3E6BA6", glass="#D8E7F8", trim="#33608F", tyre="#2C4E7C", opacity=0.92)}
</g>
</svg>
"""
