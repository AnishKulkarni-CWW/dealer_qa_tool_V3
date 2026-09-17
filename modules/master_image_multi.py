"""Routing several Master creatives to the models they belong to.

WHY THIS EXISTS
---------------
A run with nine model zips needs nine masters. One shared bulletin PDF
already solves that when there is one — `master_pdf_multi` routes each
adapt to its own EMAILER page inside the deck. But the bulletin is not
always available, and the fallback is a folder of images, one per model,
named after the model:

    P_2188754_..._Sep26 5LWB.png
    P_2188754_..._Sep26 X3.png
    P_2188754_..._Sep26 X5.png
    P_2188754_..._Sep26 2GC.png

The model is the only thing that differs between those names, and it is
the same token the adapts themselves are named by. So the same matcher
that routes the PDF routes these: `master_pdf_multi.match_model` reads a
model code out of any string, and the strongest signal wins.

The other source of masters is a campaign bundle. A model folder inside
one routinely carries its own approved creative beside index.html (see
`model_bundle`), which is the same picture under the same name — so both
sources feed this one index and are routed by the same rule.

WHAT IT DOES NOT DO
-------------------
It never overrides a master a person assigned by hand. `app.py` consults
the per-adapt Master slot first, then the global Master JPG, and only then
this index — so automatic routing is the last resort, and it always says
in the run which file it picked and why.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from . import master_pdf_multi as _models
except ImportError:  # pragma: no cover - standalone use
    import master_pdf_multi as _models


@dataclass
class MasterImageEntry:
    """One master creative, and the model it was matched to."""
    file_name: str
    data: bytes
    model_code: str = ""
    score: int = 0
    origin: str = "uploaded"     # "uploaded" or "bundle"

    @property
    def model_label(self) -> str:
        return _models.model_display(self.model_code) if self.model_code else "unmatched"

    @property
    def option(self) -> str:
        return f"{self.model_label} — {self.file_name}" if self.model_code else self.file_name


@dataclass
class MasterImageIndex:
    entries: List[MasterImageEntry] = field(default_factory=list)
    note: str = ""

    @property
    def is_multi(self) -> bool:
        return len(self.entries) > 1

    def by_code(self) -> Dict[str, MasterImageEntry]:
        """The best-scoring entry per model code.

        Two files can name the same model — a bundle's own creative and an
        uploaded one, say. The higher-scoring match wins, and an uploaded
        file beats a bundle's on a tie, because uploading it was a
        deliberate act.
        """
        best: Dict[str, MasterImageEntry] = {}
        for entry in self.entries:
            if not entry.model_code:
                continue
            current = best.get(entry.model_code)
            if current is None:
                best[entry.model_code] = entry
                continue
            beats_on_score = entry.score > current.score
            wins_the_tie = (
                entry.score == current.score
                and entry.origin == "uploaded"
                and current.origin != "uploaded"
            )
            if beats_on_score or wins_the_tie:
                best[entry.model_code] = entry
        return best

    def options(self) -> List[str]:
        return [e.option for e in self.entries]

    def from_option(self, option_label: str) -> Optional[MasterImageEntry]:
        for e in self.entries:
            if e.option == option_label:
                return e
        return None


def index_master_images(
    files: Sequence[Tuple[str, bytes]],
    origin: str = "uploaded",
    existing: Optional[MasterImageIndex] = None,
) -> MasterImageIndex:
    """Reads a model code out of each file's NAME and indexes it.

    `files` is a sequence of `(file_name, raw_bytes)`. `existing` lets a
    second source (a campaign bundle's own creatives) be folded into an
    index already built from uploaded files.
    """
    index = existing or MasterImageIndex()
    for file_name, data in files or []:
        if not data:
            continue
        stem = os.path.splitext(os.path.basename(file_name or ""))[0]
        code, score = _models.match_model(stem)
        index.entries.append(MasterImageEntry(
            file_name=os.path.basename(file_name or "master"),
            data=data,
            model_code=code,
            score=score,
            origin=origin,
        ))

    index.note = _describe(index)
    return index


def _describe(index: "MasterImageIndex") -> str:
    """The one line the routing card shows above its table."""
    total = len(index.entries)
    if not total:
        return ""
    matched = sum(1 for e in index.entries if e.model_code)
    uploaded = sum(1 for e in index.entries if e.origin == "uploaded")
    bundled = total - uploaded
    models = len(index.by_code())

    where = f"{total} master image(s)"
    if uploaded and bundled:
        where += f" — {uploaded} uploaded, {bundled} found inside the campaign zip —"
    elif bundled:
        where += " found inside the campaign zip"

    if matched == total:
        note = f"{where} covering {models} model(s), each matched by its file name."
    else:
        note = (
            f"{where}: {matched} matched to a model by file name, "
            f"{total - matched} could not be matched and can be pinned by hand."
        )
    if uploaded and bundled:
        note += " Where both name the same model, the uploaded image is used."
    return note


def resolve_job_to_entry(
    job_name: str,
    html_text: str,
    index: MasterImageIndex,
) -> Tuple[Optional[MasterImageEntry], str]:
    """Routes one adapt to its master image.

    Same order of evidence as the master-PDF router, so the two behave
    identically: the adapt's own name first, then the model named inside
    the email's own terms & conditions.
    """
    by_code = index.by_code()
    if not by_code:
        return None, "None of the master images could be matched to a model by file name."

    stem = os.path.splitext(os.path.basename(job_name or ""))[0]
    code, _score = _models.match_model(stem)
    if code and code in by_code:
        entry = by_code[code]
        return entry, (
            f"Matched on the name '{job_name}' → {entry.model_label} "
            f"('{entry.file_name}')."
        )

    code2, _score2 = _models._model_hint_from_html(html_text or "")
    if code2 and code2 in by_code:
        entry = by_code[code2]
        return entry, (
            f"The name '{job_name}' was inconclusive — matched on the model named inside "
            f"the email's own terms &amp; conditions → {entry.model_label} "
            f"('{entry.file_name}')."
        )

    if code and code not in by_code:
        return None, (
            f"'{job_name}' looks like the {_models.model_display(code)}, but no master "
            f"image was supplied for that model."
        )
    return None, f"Could not work out which model '{job_name}' is — pick its master by hand."


def build_auto_map(
    jobs: Sequence[dict],
    index: MasterImageIndex,
) -> Dict[str, Tuple[Optional[MasterImageEntry], str]]:
    """{job name: (entry or None, reason)} for every adapt in the run."""
    out: Dict[str, Tuple[Optional[MasterImageEntry], str]] = {}
    for job in jobs or []:
        name = job.get("name", "")
        out[name] = resolve_job_to_entry(name, job.get("html", ""), index)
    return out
