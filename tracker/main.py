"""Poll eBay and TCGplayer for new listings and post them to Discord.

    python -m tracker.main                    # normal run (what the GitHub workflow does)
    python -m tracker.main --dry-run          # print results, post nothing, save nothing
    python -m tracker.main --card zekrom      # only cards whose name contains "zekrom"
    python -m tracker.main --source tcgplayer # only one marketplace
    python -m tracker.main --test-webhook     # send one test message to each configured webhook
    python -m tracker.main --post-current     # post every current listing once, then only new ones
"""

import argparse
import os
import sys

from . import discord, ebay, tcgplayer
from .config import Card, ConfigError, Settings, load
from .state import State

TCG_FAILURE_ALERT_AFTER = 3
DRY_RUN_SHOW = 15
USERNAMES = {"ebay": "eBay Tracker", "tcgplayer": "TCGplayer Tracker"}


def log(msg: str) -> None:
    print(msg, flush=True)


def process(source: str, card: Card, listings: list, id_of, embed_of, webhook: str | None,
            state: State, dry_run: bool) -> None:
    ids = [id_of(x) for x in listings]
    seeded = state.is_seeded(source, card.name)
    seen = state.seen(source, card.name)
    new = [x for x in listings if id_of(x) not in seen]
    new_ids = {id_of(x) for x in new}

    if dry_run:
        log(f"  [{source}] {card.name}: {len(listings)} results, {len(new) if seeded else 0} new"
            + ("" if seeded else " (not seeded yet: first real run records these silently)"))
        for x in listings[:DRY_RUN_SHOW]:
            e = embed_of(x, card)
            mark = "NEW " if seeded and id_of(x) in new_ids else "    "
            details = ", ".join(f["value"] for f in e["fields"][:3])
            log(f"    {mark}{e['title'][:80]}  [{details}]  {e['url']}")
        if len(listings) > DRY_RUN_SHOW:
            log(f"    ... and {len(listings) - DRY_RUN_SHOW} more")
        return

    if not seeded:
        log(f"  [{source}] {card.name}: seeded {len(ids)} existing listings (no alerts on first run)")
        state.add(source, card.name, ids)
        return

    if new:
        # oldest first so the newest ends up at the bottom of the channel
        new.reverse()
        discord.post(webhook, [embed_of(x, card) for x in new], username=USERNAMES[source])
        log(f"  [{source}] {card.name}: posted {len(new)} new")
    state.add(source, card.name, ids)


def run_ebay(cards: list[Card], settings: Settings, state: State, dry_run: bool) -> bool:
    cid, secret = os.getenv("EBAY_CLIENT_ID"), os.getenv("EBAY_CLIENT_SECRET")
    webhook = os.getenv("DISCORD_WEBHOOK_EBAY")
    cards = [c for c in cards if c.ebay_queries]
    if not (cid and secret):
        log("eBay: skipped (EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set)")
        return True
    if not webhook and not dry_run:
        log("eBay: skipped (DISCORD_WEBHOOK_EBAY not set)")
        return True

    queries = sum(len(c.ebay_queries) for c in cards)
    per_day = queries * (24 * 60 // settings.poll_minutes)
    log(f"eBay: {len(cards)} cards, {queries} queries/run, ~{per_day}/day "
        f"(limit {settings.ebay_daily_call_limit})")
    if per_day > settings.ebay_daily_call_limit * 0.9:
        log("  WARNING: close to or over the eBay daily limit. See README → 'eBay call budget'.")

    client = ebay.EbayClient(cid, secret, settings)
    ok = True
    for card in cards:
        try:
            items = client.search_card(card)
            process("ebay", card, items, lambda i: i["itemId"], ebay.embed, webhook, state, dry_run)
        except Exception as e:  # keep going with the other cards
            ok = False
            log(f"  [ebay] {card.name}: ERROR {e}")
        if not dry_run:
            state.save()
    return ok


def run_tcgplayer(cards: list[Card], state: State, dry_run: bool) -> bool:
    webhook = os.getenv("DISCORD_WEBHOOK_TCGPLAYER")
    cards = [c for c in cards if c.tcgplayer_product_ids]
    if not webhook and not dry_run:
        log("TCGplayer: skipped (DISCORD_WEBHOOK_TCGPLAYER not set)")
        return True

    log(f"TCGplayer: {len(cards)} cards")
    failures = 0
    for card in cards:
        try:
            listings = tcgplayer.fetch_card(card)
            process("tcgplayer", card, listings, tcgplayer.listing_id, tcgplayer.embed, webhook, state, dry_run)
        except Exception as e:
            failures += 1
            log(f"  [tcgplayer] {card.name}: ERROR {e}")
        if not dry_run:
            state.save()

    if dry_run:
        return failures == 0

    # TCGplayer's endpoint is unofficial; warn once in Discord if it keeps failing.
    if cards and failures == len(cards):
        streak = state.get_counter("tcgplayer_failed_runs") + 1
        state.set_counter("tcgplayer_failed_runs", streak)
        if streak == TCG_FAILURE_ALERT_AFTER:
            try:
                discord.post_text(webhook, f"⚠️ TCGplayer lookups have failed {streak} runs in a row. "
                                  "Their site may have changed; check the GitHub Actions logs.",
                                  username="TCGplayer Tracker")
            except Exception as e:
                log(f"  could not post TCGplayer failure warning: {e}")
    else:
        state.set_counter("tcgplayer_failed_runs", 0)
    state.save()
    return failures == 0


def test_webhooks() -> int:
    sent = 0
    for env, name in (("DISCORD_WEBHOOK_EBAY", USERNAMES["ebay"]), ("DISCORD_WEBHOOK_TCGPLAYER", USERNAMES["tcgplayer"])):
        url = os.getenv(env)
        if not url:
            log(f"{env}: not set")
            continue
        discord.post_text(url, f"✅ Test message from the card listing tracker ({env}).", username=name)
        log(f"{env}: test message sent")
        sent += 1
    return 0 if sent else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Post new card listings to Discord.")
    p.add_argument("--dry-run", action="store_true", help="print results; don't post or save state")
    p.add_argument("--card", help="only cards whose name contains this text (case-insensitive)")
    p.add_argument("--source", choices=["ebay", "tcgplayer"], help="only this marketplace")
    p.add_argument("--test-webhook", action="store_true", help="send a test message to each webhook")
    p.add_argument("--post-current", action="store_true",
                   help="forget seen listings for the selected cards, so every current listing posts once")
    args = p.parse_args()

    if args.test_webhook:
        return test_webhooks()

    try:
        settings, cards = load()
    except ConfigError as e:
        log(f"cards.yaml error: {e}")
        return 2
    if args.card:
        cards = [c for c in cards if args.card.lower() in c.name.lower()]
        if not cards:
            log(f"no card name contains {args.card!r}")
            return 2

    state = State()
    if args.post_current and not args.dry_run:
        for source in ("ebay", "tcgplayer"):
            if args.source in (None, source):
                for card in cards:
                    state.reset(source, card.name)
        log("post-current: every current listing for the selected cards will be posted")
    ok = True
    if args.source in (None, "ebay"):
        ok &= run_ebay(cards, settings, state, args.dry_run)
    if args.source in (None, "tcgplayer"):
        ok &= run_tcgplayer(cards, state, args.dry_run)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
