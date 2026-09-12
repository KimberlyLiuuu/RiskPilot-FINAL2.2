# -*- coding: utf-8 -*-
"""Merge supplier multilingual keys into RiskPilot locales/*.json.

Run from the RiskPilot project root:
    python merge_supplier_locales.py

A .bak file is created before each locale is first changed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCALES = ROOT / "locales"
FRAGMENTS = ROOT / "supplier_locale_fragments.json"


def set_dotted(data: dict, dotted_key: str, value: str) -> None:
    parts = dotted_key.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def main() -> None:
    fragments = json.loads(FRAGMENTS.read_text(encoding="utf-8"))

    changed = 0
    missing = []

    for locale, additions in fragments.items():
        path = LOCALES / f"{locale}.json"
        if not path.exists():
            missing.append(str(path))
            continue

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"{path} is not a JSON object")

        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            shutil.copy2(path, backup)

        for dotted_key, value in additions.items():
            set_dotted(data, dotted_key, value)

        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        changed += 1
        print(f"UPDATED  {path}")

    if missing:
        print("\nMissing locale files:")
        for item in missing:
            print(f"  - {item}")

    print(f"\nDone. Updated {changed} locale file(s).")
    print("Restart Streamlit so i18n.py reloads the locale cache.")


if __name__ == "__main__":
    main()
