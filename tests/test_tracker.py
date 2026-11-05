"""Offline tests: config validation, eBay filtering/embeds, and first-run seeding.

    python -m unittest discover tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tracker import ebay, main
from tracker.config import Card, ConfigError, Settings, load
from tracker.state import State

ITEM_BIN = {
    "itemId": "v1|111|0",
    "title": "Pokemon Chandelure 057/052 SR Hail Blizzard 1st Edition Japanese",
    "itemWebUrl": "https://www.ebay.com/itm/111",
    "price": {"value": "149.99", "currency": "USD"},
    "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"],
    "condition": "Ungraded",
    "seller": {"username": "cardshop", "feedbackPercentage": "99.8", "feedbackScore": 1200},
    "shippingOptions": [{"shippingCost": {"value": "0.00", "currency": "USD"}}],
    "image": {"imageUrl": "https://i.ebayimg.com/111.jpg"},
    "itemLocation": {"country": "JP"},
    "itemCreationDate": "2026-11-03T20:00:00.000Z",
}
ITEM_AUCTION = {
    "itemId": "v1|222|0",
    "title": "Chandelure SR 057/052 PSA 9",
    "itemWebUrl": "https://www.ebay.com/itm/222",
    "price": {"value": "80.00", "currency": "USD"},
    "currentBidPrice": {"value": "80.00", "currency": "USD"},
    "bidCount": 4,
    "buyingOptions": ["AUCTION"],
    "itemEndDate": "2026-11-06T20:00:00.000Z",
}
ITEM_REGULAR = {**ITEM_BIN, "itemId": "v1|333|0", "title": "Chandelure 014/052 Hail Blizzard holo"}

CARD = Card(name="Chandelure SR", ebay_queries=["q1", "q2"], ebay_exclude=["014/052"])


class FakeEbay(ebay.EbayClient):
    def __init__(self, results):
        self.results, self.calls, self.settings = results, 0, Settings()

    def search(self, query):
        self.calls += 1
        return self.results[query]


class ConfigTests(unittest.TestCase):
    def write(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        f.write(text)
        f.close()
        return Path(f.name)

    def test_repo_cards_yaml_is_valid(self):
        settings, cards = load()
        self.assertTrue(cards)
        self.assertGreater(settings.poll_minutes, 0)

    def test_duplicate_names_rejected(self):
        path = self.write("cards:\n- name: A\n  ebay: {queries: [a]}\n- name: A\n  ebay: {queries: [b]}\n")
        with self.assertRaisesRegex(ConfigError, "duplicate"):
            load(path)

    def test_card_needs_a_source(self):
        with self.assertRaisesRegex(ConfigError, "needs"):
            load(self.write("cards:\n- name: A\n"))

    def test_typo_key_rejected(self):
        with self.assertRaisesRegex(ConfigError, "unknown keys"):
            load(self.write("cards:\n- name: A\n  ebya: {queries: [a]}\n"))


class EbayTests(unittest.TestCase):
    def test_merges_dedupes_and_filters(self):
        client = FakeEbay({"q1": [ITEM_BIN, ITEM_REGULAR], "q2": [ITEM_BIN, ITEM_AUCTION]})
        ids = [i["itemId"] for i in client.search_card(CARD)]
        self.assertEqual(ids, ["v1|111|0", "v1|222|0"])
        self.assertEqual(client.calls, 2)

    def test_must_include(self):
        card = Card(name="x", ebay_queries=["q"], ebay_must_include=["psa"])
        self.assertFalse(card.title_ok(ITEM_BIN["title"]))
        self.assertTrue(card.title_ok(ITEM_AUCTION["title"]))

    def test_bin_embed(self):
        e = ebay.embed(ITEM_BIN, CARD)
        fields = {f["name"]: f["value"] for f in e["fields"]}
        self.assertEqual(fields["Price"], "$149.99")
        self.assertEqual(fields["Shipping"], "Free")
        self.assertEqual(fields["Type"], "Buy It Now")
        self.assertEqual(e["thumbnail"]["url"], "https://i.ebayimg.com/111.jpg")

    def test_auction_embed(self):
        fields = {f["name"]: f["value"] for f in ebay.embed(ITEM_AUCTION, CARD)["fields"]}
        self.assertEqual(fields["Current bid"], "$80.00 (4 bids)")
        self.assertTrue(fields["Ends"].startswith("<t:"))


class SeedingTests(unittest.TestCase):
    def test_first_run_seeds_then_only_new_posted(self):
        state = State(Path(tempfile.mkdtemp()) / "seen.json")
        id_of = lambda i: i["itemId"]
        with mock.patch.object(main.discord, "post") as post:
            main.process("ebay", CARD, [ITEM_BIN], id_of, ebay.embed, "url", state, dry_run=False)
            post.assert_not_called()
            main.process("ebay", CARD, [ITEM_AUCTION, ITEM_BIN], id_of, ebay.embed, "url", state, dry_run=False)
            post.assert_called_once()
            self.assertEqual(len(post.call_args.args[1]), 1)
            main.process("ebay", CARD, [ITEM_AUCTION, ITEM_BIN], id_of, ebay.embed, "url", state, dry_run=False)
            post.assert_called_once()

    def test_reset_posts_everything_once(self):
        state = State(Path(tempfile.mkdtemp()) / "seen.json")
        id_of = lambda i: i["itemId"]
        state.add("ebay", CARD.name, [id_of(ITEM_BIN), id_of(ITEM_AUCTION)])
        state.reset("ebay", CARD.name)
        with mock.patch.object(main.discord, "post") as post:
            main.process("ebay", CARD, [ITEM_AUCTION, ITEM_BIN], id_of, ebay.embed, "url", state, dry_run=False)
            self.assertEqual(len(post.call_args.args[1]), 2)
            main.process("ebay", CARD, [ITEM_AUCTION, ITEM_BIN], id_of, ebay.embed, "url", state, dry_run=False)
            post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
