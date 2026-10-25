"""Package guards: shape, core Android, must-stay list, and the lists' batches. All pure; no device.

CORE_RE and CORE_OK are copied verbatim from upstream cleanyourtv tools/recovery.py:40-41 (and disable.sh:51-52) at the pin
204143007f. The Home, input-driver and handler guards need the device and live in cyt_flow.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

SETUPWRAITH = "com.google.android.tungsten.setupwraith"
PKG_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
CORE_RE = re.compile(r"^(android|com\.android\.systemui|com\.android\.shell|com\.android\.phone|com\.android\.settings|com\.android\.tv\.settings|com\.(google\.)?android\.(permissioncontroller|packageinstaller|ext\.services|ext\.shared|modulemetadata)|com\.google\.android\.overlay\.modules.*|com\.android\.providers\..*)$")
CORE_OK = re.compile(r"^com\.android\.providers\.(calendar|contacts|userdictionary|blockednumber)$")
NETWORK_RE = re.compile(r"(wifi|wi-fi|bluetooth)", re.I)  # REVIEW.md section 6: any Wi-Fi or Bluetooth package stays on
DATA = Path(__file__).resolve().parent / "data"
FIRST_BATCH_MAX = 4
BATCH_MAX = 8


def load_never() -> List[str]:
    """The must-stay patterns from data/never.txt. Output: list of exact names or 'prefix.*' patterns."""
    return [ln.split("#")[0].strip() for ln in (DATA / "never.txt").read_text().splitlines() if ln.split("#")[0].strip()]


def refusal_reason(pkg: str, never: List[str] = None) -> str:
    """Why a package may never be in a batch, or '' when it may. Inputs: pkg; never (default: the vendored list). Output: str."""
    if not PKG_RE.fullmatch(pkg):
        return "not a package name"
    if pkg == SETUPWRAITH:
        return "setupwraith is its own gated step, never a batch line"
    if CORE_RE.match(pkg) and not CORE_OK.match(pkg):
        return "core android package; disabling it can stop the tv booting"
    for pat in never if never is not None else load_never():
        if pat.endswith(".*"):
            if pkg.startswith(pat[:-1]):
                return "on the must-stay list"
        elif pkg == pat:
            return "on the must-stay list"
    if NETWORK_RE.search(pkg):
        return "a wifi or bluetooth package; must stay on"
    return ""


def parse_list(text: str) -> List[Tuple[str, List[str]]]:
    """A debloat list as comment-headed groups. Inputs: file text. Output: [(group title, [package, ...])], order kept.

    A '# ' comment line directly above package lines titles the group; blank lines end a group; a trailing '# note' on a package line is dropped.
    """
    groups: List[Tuple[str, List[str]]] = []
    title, pkgs, seen_blank = "", [], True
    for raw in text.lstrip("﻿").replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            if pkgs:
                groups.append((title, pkgs))
                title, pkgs = "", []
            seen_blank = True
        elif line.startswith("#"):
            if pkgs:
                groups.append((title, pkgs))
                pkgs = []
            if seen_blank:
                title = line.lstrip("# ").strip()
            seen_blank = False
        else:
            pkgs.append(line.split()[0])
            seen_blank = False
    if pkgs:
        groups.append((title, pkgs))
    return groups


def plan_batches(list_id: str, text: str, never: List[str] = None) -> Dict[str, object]:
    """Split a list into numbered batches, applying every pure guard. Inputs: list id; list text; never. Output: {batches, refused}.

    Each batch is {id, title, packages}; the first batch holds at most 4 packages, later ones at most 8. A refused package is listed with its reason, never batched.
    """
    batches, refused = [], []
    for title, pkgs in parse_list(text):
        ok = []
        for p in pkgs:
            why = refusal_reason(p, never)
            (refused if why else ok).append({"package": p, "reason": why} if why else p)
        while ok:
            cap = FIRST_BATCH_MAX if not batches else BATCH_MAX
            chunk, ok = ok[:cap], ok[cap:]
            batches.append({"id": f"{list_id}:{len(batches) + 1}", "title": title or "batch", "packages": chunk})
    return {"batches": batches, "refused": refused}
