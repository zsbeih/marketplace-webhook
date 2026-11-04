"""Load and validate cards.yaml."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CARDS_FILE = ROOT / "cards.yaml"


class ConfigError(Exception):
    pass


@dataclass
class Card:
    name: str
    ebay_queries: list[str] = field(default_factory=list)
    ebay_exclude: list[str] = field(default_factory=list)
    ebay_must_include: list[str] = field(default_factory=list)
    tcgplayer_product_ids: list[int] = field(default_factory=list)

    def title_ok(self, title: str) -> bool:
        t = title.lower()
        if any(word.lower() in t for word in self.ebay_exclude):
            return False
        if self.ebay_must_include and not any(w.lower() in t for w in self.ebay_must_include):
            return False
        return True


@dataclass
class Settings:
    poll_minutes: int = 15
    ebay_daily_call_limit: int = 5000
    ebay_category_id: str = "183454"
    ebay_marketplace: str = "EBAY_US"


def _str_list(value, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(v, (str, int)) for v in value):
        raise ConfigError(f"{where} must be a list of strings")
    return [str(v).strip() for v in value if str(v).strip()]


def load(path: Path = CARDS_FILE) -> tuple[Settings, list[Card]]:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name} is not valid YAML: {e}") from e

    settings = Settings(**(raw.get("settings") or {}))

    cards: list[Card] = []
    seen_names: set[str] = set()
    for i, entry in enumerate(raw.get("cards") or [], start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f"card #{i} must be a mapping with a 'name'")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise ConfigError(f"card #{i} is missing 'name'")
        if name in seen_names:
            raise ConfigError(f"duplicate card name: {name!r}")
        seen_names.add(name)

        unknown = set(entry) - {"name", "ebay", "tcgplayer"}
        if unknown:
            raise ConfigError(f"{name!r}: unknown keys {sorted(unknown)}")

        ebay = entry.get("ebay") or {}
        tcg = entry.get("tcgplayer") or {}
        try:
            product_ids = [int(p) for p in (tcg.get("product_ids") or [])]
        except (TypeError, ValueError) as e:
            raise ConfigError(f"{name!r}: tcgplayer.product_ids must be numbers") from e

        card = Card(
            name=name,
            ebay_queries=_str_list(ebay.get("queries"), f"{name!r}: ebay.queries"),
            ebay_exclude=_str_list(ebay.get("exclude"), f"{name!r}: ebay.exclude"),
            ebay_must_include=_str_list(ebay.get("must_include"), f"{name!r}: ebay.must_include"),
            tcgplayer_product_ids=product_ids,
        )
        if not card.ebay_queries and not card.tcgplayer_product_ids:
            raise ConfigError(f"{name!r}: needs ebay.queries or tcgplayer.product_ids")
        cards.append(card)

    if not cards:
        raise ConfigError(f"{path.name} has no cards")
    return settings, cards
