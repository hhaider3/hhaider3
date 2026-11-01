#!/usr/bin/env python3
"""Rebuild the "Recently shipped" table in README.md from public GitHub activity.

Each row sums up a project's recent work as a whole, not just its latest commit:
it reads every meaningful commit and release from the two weeks before the
project's latest change, then lists the main themes. Themes that many commits
touch and subjects written as "Headline: details" rank first, and repairs only
appear if there is nothing else.

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
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

OWNER = os.environ.get("GITHUB_REPOSITORY_OWNER", "hhaider3")
README = Path(__file__).resolve().parent.parent / "README.md"
ACTIVE_DAYS = 45    # a project is listed if its latest change is this recent
SUMMARY_DAYS = 14   # how far back from its latest change a summary reaches
MAX_ROWS = 5        # rows kept in the table
MAX_TEXT = 170      # characters per summary
MAX_HEAD = 60       # characters per highlight

# Repositories that should never be listed (the profile repo itself, noisy or retired repos).
EXCLUDE = {OWNER, f"{OWNER}.github.io", "SER517_team5_industry", "terminalshooter-python"}

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

# Commits that say nothing about what shipped, or whose change was later undone
# (the OnePlus 165 Hz request was removed again in Endless Drive for Android 1.2).
NOISE = re.compile(
    r"^(merge|initial commit|bump|chore|wip|revert|deploy)\b"
    r"|^(site|release|docs?|ci|tests?|build)\s*:"
    r"|changelog|readme|screenshot|\bfix ci\b|typo|oneplus",
    re.I,
)
# Commits that read as repairs rather than something new.
REPAIR = re.compile(r"^(fix|keep|silence|restore|remove|drop|speed up|upgrade|run|set up)\b", re.I)
# Commits that read as something new.
NEW = re.compile(r"^(add|adds|new|introduce|replace|overhaul)\b", re.I)
# Words too common to identify a theme.
COMMON = set(
    "with from that this into than when their them only also more just like keep game drive "
    "project phone phones android app apps version better faster quicker smoother "
    "first after before across every still under over macmpv andrompv port".split()
)

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


def day(date):
    return f"{date:%b} {date.day}"


def clean(text):
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # [t](u) -> t
    text = re.sub(r"[*_`|<>\[\]]+", "", text)                   # markdown and table marks
    return re.sub(r"\s+", " ", text).strip().rstrip(".")


def keywords(text):
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z-]{3,}", text)} - COMMON
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in words}


def tidy(head):
    """Trim a phrase to a short highlight, or None if it says nothing on its own."""
    head = re.sub(r"^(and|also|adds?|new)\s+", "", clean(head), flags=re.I).strip(" ,;")
    while len(head) > MAX_HEAD:
        cut = re.split(r",| and | with | for | so | while ", head)
        if len(cut) == 1:
            return None
        head = re.sub(r"(,| and | with | for | so | while )[^,]*$", "", head) if len(cut) > 2 else cut[0]
    if len(head.split()) < 2 and not head[:1].isupper():
        return None
    return head[:1].upper() + head[1:] if head else None


def phrases(subject):
    """The highlights a commit subject offers: (text, weight)."""
    subject = clean(subject)
    if ":" in subject:
        head = subject.split(":", 1)[0]
        return [(tidy(head), 1 if re.search(r"\d", head) else 3)]
    out = []
    for part in subject.split(";"):
        added = re.search(r",\s*add\s+(.+)$", part, re.I)   # "Cut output, add endless mode, profile"
        if added:
            out.append((tidy(added.group(1)), 2))
            part = part[: added.start()]
        first = part.split(",")[0]
        if len(first.split()) < 2:                            # "Lanes, tracks and dust" stays whole
            first = part
        out.append((tidy(first), 2 if NEW.match(part.strip()) else 1))
    return out


def release_points(rel):
    """The opening bullets of a release's notes, which describe it for users."""
    bullets = [line.strip()[2:] for line in (rel.get("body") or "").splitlines() if re.match(r"\s*[-*] ", line)]
    bullets = [b for b in bullets if not re.match(r"\W*(earlier versions|install|both downloads|public builds)", b, re.I)]
    return [re.split(r"(?<=[.!?:])\s", clean(b).replace("Fixed ", "", 1), maxsplit=1)[0].rstrip(":") for b in bullets[:2]]


def summarize(commits, releases):
    """Pick the main themes across all of a project's recent changes."""
    subjects = [(d, s) for d, s in commits]
    themes = Counter(k for _, s in subjects for k in keywords(s))
    candidates = []
    for rel in releases[:2]:
        title = rel.get("name") or ""
        if ":" in title:                                      # "App 1.3: music that doesn't stall"
            candidates.append((4, when(rel["published_at"]), tidy(title.split(":", 1)[1])))
    for rel in releases[:4]:
        for point in release_points(rel):
            reach = max((themes[k] for k in keywords(point)), default=1)
            candidates.append((3 + min(reach - 1, 3), when(rel["published_at"]), tidy(point)))
    for date, subject in subjects:
        repair = bool(REPAIR.match(subject))
        for text, weight in phrases(subject):
            if not text:
                continue
            reach = max((themes[k] for k in keywords(text)), default=1)
            score = (0 if repair else weight + min(reach - 1, 3))
            candidates.append((score, date, text))
    candidates = [c for c in candidates if c[2]]
    if not candidates:
        return clean(subjects[0][1])[:MAX_TEXT] if subjects else clean(releases[0]["name"] or releases[0]["tag_name"])

    # Strongest first; on a tie the oldest mention wins, since it usually introduced the theme.
    picked, seen = [], set()
    for score, date, text in sorted(candidates, key=lambda c: (-c[0], c[1])):
        words = keywords(text)
        if words & seen or (score == 0 and picked):
            continue
        picked.append((score, date, text))
        seen |= words

    # Newest themes first within the same strength, as far as the length allows.
    out, used = [], 0
    for _, _, text in sorted(picked, key=lambda c: (-c[0], -c[1].timestamp())):
        if out and used + len(text) + 3 > MAX_TEXT:
            continue
        out.append(text)
        used += len(text) + 3
    return " · ".join(out) or clean(subjects[0][1])[:MAX_TEXT]


def gather(repo, since):
    """Meaningful changes from the SUMMARY_DAYS before the repo's latest one, newest first."""
    commits = []
    for c in api(f"/repos/{OWNER}/{repo['name']}/commits?since={since:%Y-%m-%dT%H:%M:%SZ}&per_page=100"):
        subject = c["commit"]["message"].splitlines()[0].strip()
        if subject and not NOISE.search(subject):
            commits.append((when(c["commit"]["author"]["date"]), subject))
    releases = [
        x for x in api(f"/repos/{OWNER}/{repo['name']}/releases?per_page=20")
        if not x["draft"] and x["published_at"] and when(x["published_at"]) >= since
    ]
    dates = [d for d, _ in commits] + [when(x["published_at"]) for x in releases]
    if not dates:
        return None
    latest = max(dates)
    start = latest - timedelta(days=SUMMARY_DAYS)
    commits = sorted((c for c in commits if c[0] >= start), key=lambda c: c[0], reverse=True)
    releases = sorted(
        (x for x in releases if when(x["published_at"]) >= start),
        key=lambda x: x["published_at"], reverse=True,
    )
    earliest = min([d for d, _ in commits] + [when(x["published_at"]) for x in releases])
    return {"repo": repo, "latest": latest, "earliest": earliest, "commits": commits, "releases": releases}


def row(item):
    repo = item["repo"]
    name = DISPLAY.get(repo["name"], repo["name"])
    title = f"**[{name}]({repo['html_url']})**"
    if item["releases"]:
        rel = item["releases"][0]
        title += f" [{rel['tag_name']}]({rel['html_url']})"
    count = len(item["commits"]) or len(item["releases"])
    label = "change" if count == 1 else "changes"
    span = f"on {day(item['latest'])}" if day(item["earliest"]) == day(item["latest"]) else f"since {day(item['earliest'])}"
    text = summarize(item["commits"], item["releases"])
    return f"| **{day(item['latest']).replace(' ', '&nbsp;')}** | {title} <sub>{count} {label} {span}</sub><br>{text} |"


def collect():
    since = datetime.now(timezone.utc) - timedelta(days=ACTIVE_DAYS)
    items = []
    for repo in api(f"/users/{OWNER}/repos?per_page=100&sort=pushed"):
        if repo["fork"] or repo["archived"] or repo["name"] in EXCLUDE or when(repo["pushed_at"]) < since:
            continue
        item = gather(repo, since)
        if item:
            items.append(item)
    items.sort(key=lambda i: i["latest"], reverse=True)
    return [row(item) for item in items[:MAX_ROWS]]


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
