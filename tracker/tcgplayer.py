"""TCGplayer marketplace listings, via the (unofficial) endpoint the website itself uses."""

import time

import requests

from .config import Card
from .discord import field

LISTINGS_URL = "https://mp-search-api.tcgplayer.com/v1/product/{product_id}/listings"
PRODUCT_URL = "https://www.tcgplayer.com/product/{product_id}"
IMAGE_URL = "https://tcgplayer-cdn.tcgplayer.com/product/{product_id}_200w.jpg"
PAGE_SIZE = 50
MAX_PAGES = 10
COLOR = 0x1F6FEB
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Origin": "https://www.tcgplayer.com",
    "Referer": "https://www.tcgplayer.com/",
}


def listing_id(listing: dict) -> str:
    return str(int(listing["listingId"]))


def _post(url: str, body: dict, attempts: int = 3) -> dict:
    """POST with retries; TCGplayer's gateway returns occasional 5xx/timeouts."""
    for attempt in range(1, attempts + 1):
        try:
            r = requests.post(url, json=body, headers=HEADERS, timeout=20)
            if r.status_code < 500:
                break
            error = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            error = type(e).__name__
        if attempt == attempts:
            raise RuntimeError(f"{url} failed after {attempts} attempts ({error})")
        time.sleep(2 * attempt)
    if not r.ok:
        raise RuntimeError(f"{url} failed (HTTP {r.status_code}): {r.text[:200]}")
    return r.json()


def _listings_page(product_id: int, body: dict, attempts: int = 3) -> dict:
    """One page of listings. TCGplayer sometimes answers 200 with an empty "results" array;
    retry that like a 5xx instead of failing the card."""
    for attempt in range(1, attempts + 1):
        results = _post(LISTINGS_URL.format(product_id=product_id), body).get("results") or []
        if results and isinstance(results[0], dict) and "results" in results[0]:
            return results[0]
        if attempt == attempts:
            raise RuntimeError(f"TCGplayer product {product_id}: malformed listings response "
                               f"after {attempts} attempts")
        time.sleep(2 * attempt)


def fetch_listings(product_id: int) -> list[dict]:
    listings: list[dict] = []
    for page in range(MAX_PAGES):
        body = {
            "filters": {
                "term": {"sellerStatus": "Live", "channelId": 0},
                "range": {"quantity": {"gte": 1}},
                "exclude": {"channelExclusion": 0},
            },
            "from": page * PAGE_SIZE,
            "size": PAGE_SIZE,
            "sort": {"field": "price+shipping", "order": "asc"},
            "context": {"shippingCountry": "US", "cart": {}},
            "aggregations": ["listingType"],
        }
        result = _listings_page(product_id, body)
        listings.extend(result["results"])
        if len(listings) >= result.get("totalResults", 0) or not result["results"]:
            break
    return listings


def fetch_card(card: Card) -> list[dict]:
    listings: dict[str, dict] = {}
    for product_id in card.tcgplayer_product_ids:
        for listing in fetch_listings(product_id):
            listings.setdefault(listing_id(listing), listing)
    return list(listings.values())


def embed(listing: dict, card: Card) -> dict:
    product_id = int(listing["productId"])
    price = float(listing.get("price", 0))
    shipping = float(listing.get("shippingPrice", 0))
    seller = listing.get("sellerName", "?")
    if listing.get("sellerRating") is not None:
        seller += f" ({listing['sellerRating']}%, {listing.get('sellerSales', '?')} sales)"

    fields = [
        field("Price", f"${price:,.2f}"),
        field("Shipping", "Free" if shipping == 0 else f"${shipping:,.2f}"),
        field("Condition", f"{listing.get('condition', '?')} • {listing.get('printing', '')}".strip(" •")),
        field("Language", listing.get("language", "?")),
        field("Quantity", int(listing.get("quantity", 1))),
        field("Seller", seller),
    ]
    e = {
        "title": f"{card.name} — ${price:,.2f}"[:256],
        "url": PRODUCT_URL.format(product_id=product_id),
        "color": COLOR,
        "fields": fields,
        "thumbnail": {"url": IMAGE_URL.format(product_id=product_id)},
        "footer": {"text": f"TCGplayer • {card.name}"},
    }
    photos = [u for u in (listing.get("customData") or {}).get("images") or [] if str(u).startswith("http")]
    if photos:
        e["image"] = {"url": photos[0]}
    return e
