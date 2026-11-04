"""Post embeds to a Discord webhook."""

import time

import requests

MAX_EMBEDS_PER_MESSAGE = 10


class DiscordError(Exception):
    pass


def post(webhook_url: str, embeds: list[dict], username: str) -> None:
    for i in range(0, len(embeds), MAX_EMBEDS_PER_MESSAGE):
        _send(webhook_url, {"username": username, "embeds": embeds[i : i + MAX_EMBEDS_PER_MESSAGE]})
        time.sleep(0.5)


def post_text(webhook_url: str, content: str, username: str) -> None:
    _send(webhook_url, {"username": username, "content": content})


def _send(webhook_url: str, payload: dict) -> None:
    for _ in range(5):
        r = requests.post(webhook_url, json=payload, timeout=20)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 1)) + 0.1)
            continue
        if r.status_code in (401, 404):
            raise DiscordError(f"webhook rejected ({r.status_code}); check the webhook URL secret")
        if not r.ok:
            raise DiscordError(f"webhook error {r.status_code}: {r.text[:300]}")
        return
    raise DiscordError("webhook still rate limited after 5 retries")


def field(name: str, value, inline: bool = True) -> dict:
    return {"name": name, "value": str(value)[:1024], "inline": inline}
