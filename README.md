# Marketplace Webhook

Watches eBay and TCGplayer for new listings of specific Pokémon cards and posts each one to Discord, one channel per marketplace. It runs every 15 minutes on GitHub Actions, so nothing has to run on your computer.

- **eBay:** new Buy It Now and auction listings, via the official Browse API.
- **TCGplayer:** new seller listings on each card's product page, via the endpoint tcgplayer.com itself uses.

The card list lives in [`cards.yaml`](cards.yaml).

---

## Adding a card

1. Open `cards.yaml` and copy any existing card block.
2. Fill in the fields:

```yaml
  - name: Reshiram - BW1 Black Collection SR 055/053 (JP)   # unique; shown in Discord
    ebay:
      queries:                       # each one is a separate eBay search; results are merged
        - reshiram black collection -013
        - reshiram 055/053
      exclude: ["013/053"]           # optional: drop titles containing any of these
      must_include: []               # optional: keep only titles containing one of these
    tcgplayer:
      product_ids: [600825]          # optional: from the TCGplayer product URL
```

3. Commit and push (or edit the file directly on GitHub). That's it.

On a new card's first run, the existing listings are only **recorded**, not posted, so the channel doesn't get flooded. Alerts start from the run after that.

You can leave out `ebay` or `tcgplayer` if you only want one marketplace for a card. To stop tracking a card, delete its block.

### Writing good eBay queries
Sellers title the same card in many ways, so give each card 2–3 queries that cover the common styles:

| Style | Example |
|---|---|
| Pokémon + set name | `chandelure hail blizzard` |
| Pokémon + card number | `chandelure 057/052` |
| Pokémon + alternatives | `black kyurem (legendary shine,020/027)` |

eBay search syntax:
- words separated by spaces must **all** match
- `-word` excludes a word (`-014`, `-ex`, `-celebrations`)
- `(a,b,c)` matches **any** of a, b or c (no spaces after the commas)

If the regular (non-secret) version of a card keeps showing up, add its number to the query as `-014` and/or to `exclude`.

All searches are limited to eBay's *CCG Individual Cards* category (`settings.ebay_category_id`).

### Finding a TCGplayer product ID
Open the card on tcgplayer.com. The URL looks like `https://www.tcgplayer.com/product/601061/pokemon-japan-bw3-hail-blizzard-chandelure-057-052`, and the number after `/product/` is the ID. Japanese cards are listed under the *Pokemon Japan* product line. Some cards have more than one product (for example 1st Edition vs Unlimited); list all of them: `product_ids: [111, 222]`.

### Testing a card before committing
```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m tracker.main --dry-run --card chandelure               # both marketplaces
.venv/bin/python -m tracker.main --dry-run --card chandelure --source tcgplayer
```
`--dry-run` prints what each search finds without posting or saving anything. For eBay it needs `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` set in your shell.

`cards.yaml` is checked at the start of every run. A typo (an unknown key, a duplicate name, a card with no queries) stops the run with a clear message in the Actions log.

---

## Setup

### 1. Discord
For each channel (e.g. `#ebay-alerts`, `#tcgplayer-alerts`): **Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL**. No bot is needed. Treat the URL like a password, because anyone who has it can post to the channel.

### 2. eBay API keys
1. Sign up at <https://developer.ebay.com> (free).
2. **Application Keys → Production → Create a keyset.**
3. Copy the **App ID (Client ID)** and **Cert ID (Client Secret)**.

Until these are added, eBay is skipped and TCGplayer keeps working.

### 3. GitHub secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `DISCORD_WEBHOOK_EBAY` | webhook URL for the eBay channel |
| `DISCORD_WEBHOOK_TCGPLAYER` | webhook URL for the TCGplayer channel |
| `EBAY_CLIENT_ID` | eBay App ID |
| `EBAY_CLIENT_SECRET` | eBay Cert ID |

If you named your secrets differently, edit the `env:` block in [`.github/workflows/poll.yml`](.github/workflows/poll.yml).

### 4. Turn it on
**Actions** tab → *Poll listings* → **Run workflow**:
- Tick **test_webhook** first, to confirm each channel gets a test message.
- Then run it normally once. This records the current listings (nothing is posted), and from then on the 15-minute schedule takes over.

### Posting everything that's listed right now
To get every current listing in Discord once (a backlog), go to **Actions → Poll listings → Run workflow** and set **post_current** to `both`, `ebay` or `tcgplayer`. Runs after that go back to posting only new listings. This works for a single card locally too: `python -m tracker.main --post-current --card skyla`. Expect a burst of messages, since some cards have 50+ listings.

Each run commits `state/seen.json` (the IDs already alerted on) back to the repo, so commits titled "Update seen listings" are expected.

---

## eBay call budget
eBay allows 5,000 Browse API calls per day. Each query costs one call per run:

```
calls/day = total queries × (1440 ÷ poll_minutes)
          = 32 × 96 ≈ 3,072   (current list, every 15 min)
```

Every run prints this number and warns when it passes 90% of the limit. If you add many cards, either merge alternatives into one query with `(a,b)`, or poll less often: change the cron in `poll.yml` **and** `settings.poll_minutes` in `cards.yaml` (e.g. `7,27,47 * * * *` and `20`). Keep the minutes off :00/:15/:30/:45, since GitHub is busiest then and delays or drops more runs.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `webhook rejected (401/404)` | The webhook was deleted or the secret is wrong. Make a new one and update the secret. |
| `eBay token request failed` | Check `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET`. Use the **Production** keys, not Sandbox. |
| "TCGplayer lookups have failed 3 runs in a row" in Discord | TCGplayer probably changed their site. The endpoint is unofficial, so it may need a code update in `tracker/tcgplayer.py`. |
| Runs are late or skipped | GitHub delays scheduled workflows by 5–15 min at busy times, which is normal. GitHub also pauses schedules after 60 days without repo activity; the state commits normally prevent that, and re-enabling it in the Actions tab fixes it. |
| A card went quiet for one run after editing it | Its `name` changed. History is stored by name, so a renamed card counts as new and silently re-seeds on its next run. |

## Layout
```
cards.yaml               cards to watch + settings
tracker/main.py          entry point: python -m tracker.main [--dry-run] [--card X] [--source ebay|tcgplayer] [--test-webhook] [--post-current]
tracker/ebay.py          eBay Browse API search + Discord embed
tracker/tcgplayer.py     TCGplayer listings + Discord embed
tracker/discord.py       webhook posting (batching, rate limits)
tracker/state.py         state/seen.json read/write
tests/                   python -m unittest discover tests
```
