#!/usr/bin/env python3
"""Rebuild the "Recently shipped" table in README.md from public GitHub activity.

Run by .github/workflows/update-recent.yml (daily), or by hand:

    GITHUB_TOKEN=$(gh auth token) python3 scripts/update_recent.py --dry-run

Only the text between the RECENT:START and RECENT:END markers is rewritten, and
the file is only touched when the table actually changes. Uses the standard
library only. Private repositories never appear, because the API lists public
repositories for the owner.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

OWNER = os.environ.get("GITHUB_REPOSITORY_OWNER", "hhaider3")
README = Path(__file__).resolve().parent.parent / "README.md"
WINDOW_DAYS = 45   # how far back to look
MAX_ROWS = 5       # rows kept in the table
MAX_TEXT = 120     # characters per description

# Repositories that should never be listed (the profile repo itself, noisy repos).
EXCLUDE = {OWNER, f"{OWNER}.github.io", "SER517_team5_industry"}

# Friendlier names for the table; anything missing falls back to the repo name.
DISPLAY = {
    "project-magellan": "Endless Drive",
    "endless-drive-android": "Endless Drive for Android",
    "live-sat-location": "Earth Orbit Live",
    "terminalshooter": "TERMINAL // BREACH",
    "stockfishonline": "Stockfish Board",
    "motionlab": "Motion Lab",
    "ble-scanner": "NearbyBluetooth",
}

# Commit subjects that say nothing about what shipped.
BORING = re.compile(r"^(merge|initial commit|bump|fix ci|chore|wip)|readme|screenshot|\bdocs?\b|typo", re.I)

START, END = "<!-- RECENT:START -->", "<!-- RECENT:END -->"


def api(path):
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "profile-readme-updater"},
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def when(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def clean(text):
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # [t](u) -> t
    text = re.sub(r"[*_`]+", "", text)                          # bold / code marks
    text = re.sub(r"\s+", " ", text).strip()
    return text


def first_sentence(text):
    text = clean(text)
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    out = m.group(1) if m else text
    if len(out) > MAX_TEXT:
        out = out[: MAX_TEXT - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out.rstrip(".") + "." if not out.endswith("…") else out


def summarize_release(rel):
    """Prefer the first bullet of the notes, then the first plain paragraph, then the title."""
    body = rel.get("body") or ""
    bullet = para = None
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "|", ">", "```")):
            continue
        if re.match(r"[-*] ", line) and bullet is None:
            bullet = line[2:]
        elif para is None and not re.match(r"[-*] ", line):
            para = line
    pick = bullet or para or rel.get("name") or rel["tag_name"]
    sentence = first_sentence(pick)
    if sentence.endswith("\u2026") and rel.get("name"):
        # Notes start with a very long sentence: the release title is a better summary.
        title = rel["name"].split(":", 1)[-1]
        return first_sentence(title)
    return sentence


def row(date, name, url, text):
    return f"| **{date:%b} {date.day}** | **[{name}]({url})**: {text} |"


def collect():
    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    repos = [
        r for r in api(f"/users/{OWNER}/repos?per_page=100&sort=pushed")
        if not r["fork"] and not r["archived"] and r["name"] not in EXCLUDE
    ]
    entries = []  # (datetime, markdown row)
    for r in repos:
        name = DISPLAY.get(r["name"], r["name"])
        releases = [x for x in api(f"/repos/{OWNER}/{r['name']}/releases?per_page=5") if not x["draft"]]
        releases = [x for x in releases if when(x["published_at"]) >= since]
        if releases:
            rel = max(releases, key=lambda x: x["published_at"])
            label = f"{name} {rel['tag_name']}"
            entries.append((when(rel["published_at"]), row(when(rel["published_at"]), label, rel["html_url"], summarize_release(rel))))
            continue
        if when(r["pushed_at"]) < since:
            continue
        if when(r["created_at"]) >= since and r.get("description"):
            entries.append((when(r["created_at"]), row(when(r["created_at"]), name, r["html_url"], f"new. {first_sentence(r['description'])}")))
            continue
        for c in api(f"/repos/{OWNER}/{r['name']}/commits?per_page=10"):
            subject = c["commit"]["message"].splitlines()[0]
            if BORING.search(subject):
                continue
            d = when(c["commit"]["author"]["date"])
            if d >= since:
                entries.append((d, row(d, name, r["html_url"], first_sentence(subject))))
            break
    entries.sort(key=lambda e: e[0], reverse=True)
    return [md for _, md in entries[:MAX_ROWS]]


def main():
    dry = "--dry-run" in sys.argv
    rows = collect()
    if not rows:
        print("No recent activity found; leaving README untouched.")
        return
    table = "\n".join(["| When | What |", "| :--- | :--- |", *rows])
    text = README.read_text()
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        sys.exit(f"Markers {START} / {END} not found in README.md")
    new = pattern.sub(lambda _: f"{START}\n{table}\n{END}", text)
    if dry:
        print(table)
        return
    if new == text:
        print("Table already up to date.")
        return
    README.write_text(new)
    print(f"Updated {len(rows)} rows.")


if __name__ == "__main__":
    main()
