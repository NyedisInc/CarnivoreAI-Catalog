"""Per-TV records as JSON files in the plugin's data folder (atomic write, mode 0600). One file per TV, plus the keys folder."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

ID_RE = re.compile(r"^tv_[0-9_]{7,15}$")


class Store:
    """Inputs: root (the data folder). Holds tvs/<id>.json and keys/<id>/adbkey."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        (self.root / "tvs").mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def key_path(self, tv_id: str) -> Path:
        """The adb key path for a TV id (validated, so no path escapes). Output: Path."""
        if not ID_RE.fullmatch(tv_id):
            raise ValueError("bad tv id")
        return self.root / "keys" / tv_id / "adbkey"

    def delete_key(self, tv_id: str) -> bool:
        """Delete that TV's key folder. Output: True when nothing is left."""
        shutil.rmtree(self.key_path(tv_id).parent, ignore_errors=True)
        return not self.key_path(tv_id).parent.exists()

    def key_exists(self, tv_id: str) -> bool:
        """Whether the TV's private key file exists."""
        return self.key_path(tv_id).exists()

    def load(self, tv_id: str) -> Optional[Dict[str, Any]]:
        """One TV record or None."""
        if not ID_RE.fullmatch(tv_id):
            return None
        try:
            return json.loads((self.root / "tvs" / f"{tv_id}.json").read_text())
        except (OSError, ValueError):
            return None

    def save(self, tv: Dict[str, Any]) -> None:
        """Write a TV record atomically, mode 0600. Inputs: tv."""
        path = self.root / "tvs" / f"{tv['id']}.json"
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(tv, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def remove(self, tv_id: str) -> None:
        """Delete a TV record."""
        (self.root / "tvs" / f"{tv_id}.json").unlink(missing_ok=True)

    def all(self) -> List[Dict[str, Any]]:
        """Every TV record, sorted by id."""
        return [t for t in (self.load(p.stem) for p in sorted((self.root / "tvs").glob("tv_*.json"))) if t]
