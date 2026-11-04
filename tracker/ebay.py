"""eBay Browse API search for newly listed items."""

import base64
from datetime import datetime

import requests

from .config import Card, Settings
from .discord import field

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"
COLOR = 0xE53238


class EbayClient:
    def __init__(self, client_id: str, client_secret: str, settings: Settings):
        self.settings = settings
        self.calls = 0
        self._token = self._get_token(client_id, client_secret)

    def _get_token(self, client_id: str, client_secret: str) -> str:
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        r = requests.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": SCOPE},
            timeout=20,
        )
        if not r.ok:
            raise RuntimeError(f"eBay token request failed ({r.status_code}): {r.text[:300]}")
        return r.json()["access_token"]

    def search(self, query: str) -> list[dict]:
        self.calls += 1
        r = requests.get(
            SEARCH_URL,
            headers={
                "Authorization": f"Bearer {self._token}",
                "X-EBAY-C-MARKETPLACE-ID": self.settings.ebay_marketplace,
            },
            params={
                "q": query,
                "category_ids": self.settings.ebay_category_id,
                "sort": "newlyListed",
                "limit": 50,
                "filter": "buyingOptions:{FIXED_PRICE|AUCTION}",
            },
            timeout=20,
        )
        if not r.ok:
            raise RuntimeError(f"eBay search {query!r} failed ({r.status_code}): {r.text[:300]}")
        return r.json().get("itemSummaries", [])

    def search_card(self, card: Card) -> list[dict]:
        """Run every query for a card, merge, de-duplicate and apply title filters."""
        items: dict[str, dict] = {}
        for query in card.ebay_queries:
            for item in self.search(query):
                if item.get("itemId") and card.title_ok(item.get("title", "")):
                    items.setdefault(item["itemId"], item)
        return list(items.values())


def _money(m: dict | None) -> str | None:
    if not m or m.get("value") is None:
        return None
    symbol = "$" if m.get("currency") == "USD" else f"{m.get('currency', '')} "
    return f"{symbol}{float(m['value']):,.2f}"


def _unix(iso: str | None) -> int | None:
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def embed(item: dict, card: Card) -> dict:
    options = item.get("buyingOptions", [])
    is_auction = "AUCTION" in options
    fields = []

    if is_auction:
        bid = _money(item.get("currentBidPrice")) or _money(item.get("price"))
        fields.append(field("Current bid", f"{bid} ({item.get('bidCount', 0)} bids)"))
        if "FIXED_PRICE" in options and item.get("price"):
            fields.append(field("Buy It Now", _money(item["price"])))
    else:
        fields.append(field("Price", _money(item.get("price")) or "?"))

    shipping = (item.get("shippingOptions") or [{}])[0].get("shippingCost")
    if shipping:
        fields.append(field("Shipping", "Free" if float(shipping.get("value", 0)) == 0 else _money(shipping)))

    fields.append(field("Type", "Auction" if is_auction else "Buy It Now"))
    if is_auction and (end := _unix(item.get("itemEndDate"))):
        fields.append(field("Ends", f"<t:{end}:R>"))
    if item.get("condition"):
        fields.append(field("Condition", item["condition"]))

    seller = item.get("seller") or {}
    if seller.get("username"):
        fields.append(field("Seller", f"{seller['username']} ({seller.get('feedbackPercentage', '?')}%, {seller.get('feedbackScore', 0)})"))
    country = (item.get("itemLocation") or {}).get("country")
    if country:
        fields.append(field("Ships from", country))

    e = {
        "title": item.get("title", "eBay listing")[:256],
        "url": item.get("itemWebUrl"),
        "color": COLOR,
        "fields": fields,
        "footer": {"text": f"eBay • {card.name}"},
    }
    image = (item.get("image") or {}).get("imageUrl")
    if image:
        e["thumbnail"] = {"url": image}
    if item.get("itemCreationDate"):
        e["timestamp"] = item["itemCreationDate"]
    return e
