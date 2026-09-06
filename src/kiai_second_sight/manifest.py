from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import StudyCard


def load_manifest(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"version": 1, "cards": []}
    return json.loads(path.read_text(encoding="utf-8"))


def upsert_cards(path: str | Path, cards: list[StudyCard]) -> dict:
    path = Path(path)
    manifest = load_manifest(path)
    by_id = {item["card_id"]: item for item in manifest.get("cards", [])}
    for card in cards:
        payload = asdict(card)
        # The complete analysis is useful for reproducibility but can be large due to ownership.
        # Keep it in the manifest for now; later versions can split analysis into cache files.
        by_id[card.card_id] = payload
    manifest = {"version": 1, "cards": sorted(by_id.values(), key=lambda c: c["card_id"])}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
