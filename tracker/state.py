"""Seen-listing history, stored in state/seen.json and committed back by the workflow."""

import json
from pathlib import Path

from .config import ROOT

STATE_FILE = ROOT / "state" / "seen.json"
MAX_IDS_PER_CARD = 1000


class State:
    def __init__(self, path: Path = STATE_FILE):
        self.path = path
        try:
            self.data = json.loads(path.read_text())
        except FileNotFoundError:
            self.data = {}

    def _bucket(self, source: str) -> dict[str, list[str]]:
        return self.data.setdefault(source, {})

    def is_seeded(self, source: str, card: str) -> bool:
        return card in self._bucket(source)

    def seen(self, source: str, card: str) -> set[str]:
        return set(self._bucket(source).get(card, []))

    def add(self, source: str, card: str, ids: list[str]) -> None:
        existing = self._bucket(source).setdefault(card, [])
        known = set(existing)
        existing.extend(i for i in ids if i not in known)
        del existing[:-MAX_IDS_PER_CARD]

    def get_counter(self, key: str) -> int:
        return int(self.data.get("counters", {}).get(key, 0))

    def set_counter(self, key: str, value: int) -> None:
        self.data.setdefault("counters", {})[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n")
