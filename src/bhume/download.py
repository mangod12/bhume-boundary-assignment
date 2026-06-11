"""Utilities to download assignment assets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict
from urllib.request import urlretrieve

from .config import BASE_URL, VILLAGE_DATA


def fetch_all(root: Path) -> Dict[str, Dict[str, str]]:
    """Download all required assets for both villages."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    records: Dict[str, Dict[str, str]] = {}

    for name, info in VILLAGE_DATA.items():
        village_root = root / info["slug"]
        village_root.mkdir(parents=True, exist_ok=True)
        for key in ("input", "imagery", "boundaries", "truth"):
            rel = info[key]
            url = f"{BASE_URL}{rel}"
            out_path = village_root / Path(rel).name
            urlretrieve(url, out_path)
            records.setdefault(name, {})[key] = str(out_path)
    starter = f"{BASE_URL}/bhume-starter-kit.zip"
    starter_out = root / "bhume-starter-kit.zip"
    urlretrieve(starter, starter_out)
    records["starter"] = {"zip": str(starter_out)}
    manifest = root / "download_manifest.json"
    with open(manifest, "w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=2)
    return records

