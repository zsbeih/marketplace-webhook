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


class ConditionTests(unittest.TestCase):
    def client(self, item_response=None, **settings):
        c = FakeEbay({})
        c.settings = Settings(**settings)
        c._get = mock.Mock(return_value=mock.Mock(ok=True, json=lambda: item_response or {}))
        return c

    def descriptors(self, value):
        return {"conditionDescriptors": [{"name": "Card Condition", "values": [{"content": value}]}]}

    def test_ungraded_condition_looked_up_and_filtered(self):
        item = {**ITEM_BIN, "conditionId": "4000"}
        self.assertTrue(self.client(self.descriptors("Near mint or better")).condition_ok(dict(item)))
        self.assertTrue(self.client(self.descriptors("Lightly played (Excellent)")).condition_ok(dict(item)))
        self.assertFalse(self.client(self.descriptors("Moderately played (Very good)")).condition_ok(dict(item)))
        self.assertFalse(self.client({}).condition_ok(dict(item)))  # unknown condition is skipped

    def test_condition_ids_understood(self):
        item = {**ITEM_BIN, "conditionId": "4000"}
        ids = {"conditionDescriptors": [{"name": "40001", "values": [{"content": "400015"}]}]}
        self.assertTrue(self.client(ids).condition_ok(item))
        self.assertEqual(item["_card_condition"], "Lightly Played (Excellent)")

    def test_condition_shown_in_embed(self):
        item = {**ITEM_BIN, "conditionId": "4000"}
        self.client(self.descriptors("Near mint or better")).condition_ok(item)
        fields = {f["name"]: f["value"] for f in ebay.embed(item, CARD)["fields"]}
        self.assertEqual(fields["Condition"], "Near mint or better")

    def test_graded_toggle(self):
        item = {**ITEM_AUCTION, "conditionId": "2750"}
        self.assertTrue(self.client().condition_ok(item))
        self.assertFalse(self.client(ebay_include_graded=False).condition_ok(item))

    def test_empty_conditions_means_any(self):
        c = self.client(conditions=[])
        self.assertTrue(c.condition_ok({**ITEM_BIN, "conditionId": "4000"}))
        c._get.assert_not_called()

    def test_tcgplayer_conditions(self):
        s = Settings()
        self.assertTrue(s.condition_ok("Near Mint"))
        self.assertTrue(s.condition_ok("Lightly Played"))
        self.assertFalse(s.condition_ok("Moderately Played"))

    def test_rejected_new_listings_are_still_marked_seen(self):
        state = State(Path(tempfile.mkdtemp()) / "seen.json")
        state.reset("ebay", CARD.name)
        with mock.patch.object(main.discord, "post") as post:
            main.process("ebay", CARD, [ITEM_BIN], lambda i: i["itemId"], ebay.embed, "url", state,
                         dry_run=False, keep=lambda i: False)
            post.assert_not_called()
        self.assertIn(ITEM_BIN["itemId"], state.seen("ebay", CARD.name))


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


class CardFilterTest(unittest.TestCase):
    def card(self, name):
        return next(c for c in load()[1] if c.name.startswith(name))

    def test_wrong_printings_are_excluded(self):
        cases = {
            "Victini": ["Pokemon Victini V CS3.5C 070/066 Simplified Chinese", "Victini V cs3,5c 070/066",
                        "Victini 070/066 Chinese", "Pokemon card Victini V"],
            "White Kyurem": ["White Kyurem 021/027 CP2 Korean"],
            "Terrakion": ["Terrakion Cold Flare #155 Japanese"],
            "Chandelure": ["Chandelure Hail Blizzard 018/052 Japanese"],
        }
        for name, titles in cases.items():
            for title in titles:
                self.assertFalse(self.card(name).title_ok(title), title)

    def test_real_cards_still_pass(self):
        self.assertTrue(self.card("Victini").title_ok("Victini 070/066 SR Red Collection Japanese V-create"))
        self.assertTrue(self.card("White Kyurem").title_ok("White Kyurem 021/027 CP2 Legendary Shine"))
        self.assertTrue(self.card("Terrakion").title_ok("Terrakion 064/059 SR Cold Flare"))
        self.assertTrue(self.card("Chandelure").title_ok("Chandelure 057/052 Hail Blizzard SR"))

    def test_removed_cards(self):
        names = " ".join(c.name for c in load()[1]).lower()
        for gone in ("zekrom", "reshiram", "ghetsis"):
            self.assertNotIn(gone, names)


class TcgplayerRetryTest(unittest.TestCase):
    def test_empty_results_is_retried(self):
        from tracker import tcgplayer
        good = {"results": [{"results": [{"listingId": 1.0}], "totalResults": 1}]}
        with mock.patch.object(tcgplayer, "_post", side_effect=[{"results": []}, good]) as post, \
                mock.patch.object(tcgplayer.time, "sleep"):
            self.assertEqual(tcgplayer.fetch_listings(602243), [{"listingId": 1.0}])
        self.assertEqual(post.call_count, 2)

    def test_persistent_empty_results_raises_clear_error(self):
        from tracker import tcgplayer
        with mock.patch.object(tcgplayer, "_post", return_value={"results": []}), \
                mock.patch.object(tcgplayer.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "malformed"):
                tcgplayer.fetch_listings(602243)


class EbayRetryTest(unittest.TestCase):
    def client(self):
        c = FakeEbay({})
        c._token = "t"
        return c

    def test_timeout_is_retried(self):
        import requests
        ok = mock.Mock(status_code=200)
        with mock.patch.object(ebay.requests, "get", side_effect=[requests.ReadTimeout(), ok]) as get, \
                mock.patch.object(ebay.time, "sleep"):
            self.assertIs(self.client()._get("https://x"), ok)
        self.assertEqual(get.call_count, 2)

    def test_persistent_failure_raises(self):
        import requests
        with mock.patch.object(ebay.requests, "get", side_effect=requests.ReadTimeout()), \
                mock.patch.object(ebay.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts"):
                self.client()._get("https://x")

    def test_4xx_not_retried(self):
        bad = mock.Mock(status_code=400)
        with mock.patch.object(ebay.requests, "get", return_value=bad) as get:
            self.assertIs(self.client()._get("https://x"), bad)
        get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
