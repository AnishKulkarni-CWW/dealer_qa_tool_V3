"""Reading model emails out of an uploaded .zip.

WHAT THIS SOLVES
----------------
A .zip used to mean exactly one email: an index.html with an images/
folder beside it, zipped at the top level. Real deliveries are not shaped
like that. A campaign arrives as ONE zip holding every model, each in its
own folder, and the folders are routinely buried a level or two down
inside a wrapper directory that repeats the campaign name:

    P_2188754_..._Sep26.zip
      └ P_2188754_..._Sep26/
          └ P_2188754_..._Sep26/
              ├ 5LWB/
              │   ├ index.html
              │   ├ P_2188754_..._Sep26 5LWB.png      <- the master creative
              │   └ images/
              ├ X3/
              │   └ ...
              └ X5/
                  └ ...

Rather than guess how deep the wrapper goes, this module looks for the
thing that actually identifies a model folder — an HTML file — wherever it
sits, and treats each HTML's own directory as one model. That handles the
single-email zip (one HTML, one folder), the campaign bundle (many HTMLs,
many folders), and any depth of wrapper, without a rule about nesting.

WHY EACH MODEL GETS ITS OWN ZIP
-------------------------------
Everything downstream that needs the email's images — Banner QA pulling
the banner's actual pixels, the 300KB image-size check — resolves the
paths the HTML itself uses, i.e. `images/header.jpg` relative to the HTML.
So each model is re-zipped with its own folder as the root, and the rest
of the tool carries on working exactly as it does for a single-email zip.

THE MASTER CREATIVE SITTING NEXT TO index.html
----------------------------------------------
The image files that sit directly beside index.html — not inside
images/ — are the approved creative for that model, named after it. They
are collected here and offered to `master_image_multi` as master
candidates, so a bundle carries its own masters and Advanced QA can run
without uploading anything else.
"""

from __future__ import annotations

import io
import os
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

HTML_SUFFIXES = (".html", ".htm")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")

# Directory names that hold an email's assets rather than an email. A
# stray HTML inside one of these is a fragment, not a model.
ASSET_DIR_NAMES = {"images", "image", "img", "assets", "media", "fonts", "css", "js"}

# Files a zip tool adds that are not part of the delivery.
_JUNK_PREFIXES = ("__MACOSX/",)
_JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


@dataclass
class BundledModel:
    """One model's email, lifted out of an uploaded zip."""
    name: str                       # what the run calls this adapt
    folder: str                     # its directory inside the source zip ("" = root)
    html: str
    image_sizes: Dict[str, int] = field(default_factory=dict)
    zip_bytes: bytes = b""          # a zip rooted at `folder`
    master_images: List[Tuple[str, bytes]] = field(default_factory=list)
    source_zip: str = ""

    def zip_file(self) -> io.BytesIO:
        """A fresh stream every call — zipfile consumes what it reads."""
        return io.BytesIO(self.zip_bytes)


def uniquify_names(jobs: List[dict], key: str = "name") -> List[dict]:
    """Makes sure no two adapts share a name. Edits `jobs` in place.

    Two adapts with the same name ARE the same adapt as far as everything
    downstream is concerned — the per-adapt Master store is keyed by name,
    so one would silently take the other's Master, and the widget keys built
    from it collide outright and take the page down with them.

    It happens the moment anyone uses the HTML uploader properly: every
    model folder on earth calls its email index.html, so three of them is
    three adapts all called "index.html". The second and third become
    "index.html (2)" and "index.html (3)", which is what a person would
    have called them anyway.
    """
    taken: set = set()
    for job in jobs or []:
        base = str(job.get(key) or "").strip() or "email"
        name, suffix = base, 1
        while name in taken:
            suffix += 1
            name = f"{base} ({suffix})"
        taken.add(name)
        job[key] = name
    return jobs


def _is_junk(path: str) -> bool:
    if any(path.startswith(p) for p in _JUNK_PREFIXES):
        return True
    base = posixpath.basename(path).lower()
    return base in _JUNK_NAMES or base.startswith("._")


def _dir_of(path: str) -> str:
    return posixpath.dirname(path.replace("\\", "/"))


def _pick_html(paths: List[str]) -> str:
    """index.html wins; otherwise the shortest path, which is the least
    likely to be a fragment or an alternate version."""
    return sorted(
        paths,
        key=lambda p: (0 if posixpath.basename(p).lower() == "index.html" else 1, len(p), p),
    )[0]


def _clean_component(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("_", " ")).strip()


def extract_models(zip_source, source_name: str = "") -> Tuple[List[BundledModel], str]:
    """Every model email inside `zip_source`.

    Returns `(models, note)`. `note` is a one-line human summary of what
    was found, for the UI to show; it is never an error channel — a zip
    with nothing readable in it comes back as an empty list.
    """
    stem = os.path.splitext(os.path.basename(source_name or ""))[0]

    with zipfile.ZipFile(zip_source) as zf:
        entries = [
            zi for zi in zf.infolist()
            if not zi.is_dir() and not _is_junk(zi.filename)
        ]
        if not entries:
            return [], f"'{source_name}' contains no readable files."

        html_paths = [
            zi.filename.replace("\\", "/") for zi in entries
            if zi.filename.lower().endswith(HTML_SUFFIXES)
        ]
        if not html_paths:
            return [], f"No HTML file was found anywhere inside '{source_name}'."

        # One candidate folder per directory that holds an HTML file.
        by_folder: Dict[str, List[str]] = {}
        for path in html_paths:
            by_folder.setdefault(_dir_of(path), []).append(path)

        # An HTML inside an assets directory is a fragment, as long as a
        # real model folder exists elsewhere in the zip.
        real_folders = {
            folder for folder in by_folder
            if posixpath.basename(folder).lower() not in ASSET_DIR_NAMES
        }
        if real_folders:
            by_folder = {f: v for f, v in by_folder.items() if f in real_folders}

        folders = sorted(by_folder)
        multi = len(folders) > 1

        models: List[BundledModel] = []
        for folder in folders:
            html_path = _pick_html(by_folder[folder])
            try:
                html_text = zf.read(html_path).decode("utf-8", errors="ignore")
            except Exception:
                continue
            if not html_text.strip():
                continue

            prefix = folder + "/" if folder else ""
            member_paths = [
                zi.filename.replace("\\", "/") for zi in entries
                if zi.filename.replace("\\", "/").startswith(prefix)
            ]

            image_sizes: Dict[str, int] = {}
            master_images: List[Tuple[str, bytes]] = []
            for zi in entries:
                path = zi.filename.replace("\\", "/")
                if not path.startswith(prefix):
                    continue
                if not path.lower().endswith(IMAGE_SUFFIXES):
                    continue
                rel = path[len(prefix):]
                image_sizes[posixpath.basename(path).lower()] = zi.file_size
                # Directly beside the HTML, not inside images/ — that is
                # the approved creative for this model, not an email asset.
                if "/" not in rel:
                    try:
                        master_images.append((posixpath.basename(path), zf.read(path)))
                    except Exception:
                        pass

            # Re-zip this folder as its own root so `images/...` in the
            # HTML resolves exactly as it does for a single-email zip.
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
                for path in member_paths:
                    rel = path[len(prefix):]
                    if not rel:
                        continue
                    try:
                        out.writestr(rel, zf.read(path))
                    except Exception:
                        continue

            folder_label = _clean_component(posixpath.basename(folder))
            if multi and folder_label:
                name = f"{stem} / {folder_label}" if stem else folder_label
            else:
                name = source_name or stem or folder_label or "email"

            models.append(BundledModel(
                name=name,
                folder=folder,
                html=html_text,
                image_sizes=image_sizes,
                zip_bytes=buf.getvalue(),
                master_images=master_images,
                source_zip=source_name,
            ))

    if not models:
        return [], f"No usable email folder was found inside '{source_name}'."

    if len(models) == 1:
        note = f"'{source_name}' holds one email."
    else:
        depth = max(m.folder.count("/") for m in models) + 1
        note = (
            f"'{source_name}' holds {len(models)} model folders "
            f"({depth} level(s) deep): " + ", ".join(
                posixpath.basename(m.folder) or "(root)" for m in models
            )
        )
    n_masters = sum(len(m.master_images) for m in models)
    if n_masters:
        note += f" — {n_masters} master creative(s) found beside the HTML."
    return models, note
