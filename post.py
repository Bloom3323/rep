"""
Posts new items from an X/Twitter RSS feed to a Discord webhook,
rewriting x.com / twitter.com links to vxtwitter.com so videos play.

Uses only Python's built-in libraries, so there is nothing to install.

Needs two environment variables (set as GitHub Actions secrets):
  FEED_URL             - the RSS feed URL (the same one you used in MonitoRSS)
  DISCORD_WEBHOOK_URL  - the webhook URL for your Discord channel

Remembers what it already posted in seen.json.
On the very first run it only posts the newest item, so it doesn't flood the channel.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

FEED_URL = os.environ["FEED_URL"]
WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

STATE_FILE = Path("seen.json")
MAX_SEEN = 500  # how many past post IDs to remember
X_DOMAIN = re.compile(r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com", re.IGNORECASE)
ATOM = "{http://www.w3.org/2005/Atom}"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"

def to_vx(url: str) -> str:
    return X_DOMAIN.sub("https://vxtwitter.com", url, count=1)


def parse_time(text: str) -> float:
    if not text:
        return 0
    text = text.strip()
    try:
        return parsedate_to_datetime(text).timestamp()  # RSS style
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()  # Atom style
    except ValueError:
        return 0


def read_feed(xml_bytes: bytes) -> list:
    """Returns a list of {id, title, link, time}, oldest first."""
    root = ET.fromstring(xml_bytes)
    items = []

    for it in root.iter("item"):  # RSS 2.0
        link = (it.findtext("link") or "").strip()
        items.append({
            "id": (it.findtext("guid") or link).strip(),
            "title": it.findtext("title") or "",
            "link": link,
            "time": parse_time(it.findtext("pubDate") or ""),
        })

    for it in root.iter(ATOM + "entry"):  # Atom
        link = ""
        for l in it.findall(ATOM + "link"):
            if l.get("rel", "alternate") == "alternate":
                link = l.get("href", "")
                break
        items.append({
            "id": (it.findtext(ATOM + "id") or link).strip(),
            "title": it.findtext(ATOM + "title") or "",
            "link": link.strip(),
            "time": parse_time(it.findtext(ATOM + "published") or it.findtext(ATOM + "updated") or ""),
        })

    items = [i for i in items if i["id"] and i["link"]]
    if all(i["time"] for i in items):
        items.sort(key=lambda i: i["time"])
    else:
        items.reverse()  # feeds list newest first, so flip it
    return items


def load_seen():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return None  # first run


def save_seen(seen: list) -> None:
    STATE_FILE.write_text(json.dumps(seen[-MAX_SEEN:], indent=1), encoding="utf-8")


def post_to_discord(title: str, link: str) -> None:
    title = " ".join(title.split())
    content = f"**{title}**\n{link}" if title else link
    if len(content) > 2000:  # Discord's message limit
        keep = 2000 - len(link) - 10
        content = f"**{title[:keep]}…**\n{link}"

    body = json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode()
    for _ in range(5):
        req = urllib.request.Request(
            WEBHOOK_URL,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30):
                return
        except urllib.error.HTTPError as err:
            if err.code == 429:  # rate limited: wait and retry
                try:
                    wait = float(json.loads(err.read()).get("retry_after", 2))
                except ValueError:
                    wait = 2
                time.sleep(wait + 0.5)
                continue
            raise
    raise RuntimeError("Discord kept rate-limiting the webhook")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def main() -> None:
    entries = read_feed(fetch(FEED_URL))
    if not entries:
        print("Feed has no items.")
        sys.exit(1)

    seen = load_seen()
    if seen is None:
        # First run: remember everything, post only the newest item as a test.
        seen = [e["id"] for e in entries[:-1]]
        new_entries = entries[-1:]
        print("First run: posting only the newest item.")
    else:
        seen_set = set(seen)
        new_entries = [e for e in entries if e["id"] not in seen_set]

    for e in new_entries:
        link = to_vx(e["link"])
        post_to_discord(e["title"], link)
        print(f"Posted: {link}")
        seen.append(e["id"])
        save_seen(seen)  # save after each post so a crash never causes duplicates
        time.sleep(1)

    if not new_entries:
        print("No new posts.")
    save_seen(seen)


if __name__ == "__main__":
    main()
