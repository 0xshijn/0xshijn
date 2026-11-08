#!/usr/bin/env python3
"""Sync live GitHub repository activity and recent stargazers into README.md."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README_PATH = ROOT / "README.md"
START_MARKER = "<!--START_SECTION:activity-->"
END_MARKER = "<!--END_SECTION:activity-->"
VN_TZ = timezone(timedelta(hours=7))

OWNER = os.environ.get("GITHUB_OWNER", "0xshijn").strip() or "0xshijn"
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

MAX_ACTIVITY_ROWS = 6
MAX_STARGAZERS = 14


def _api_get(url: str, *, accept: str = "application/vnd.github+json") -> list | dict:
    headers = {
        "Accept": accept,
        "User-Agent": f"{OWNER}-profile-readme-updater",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fmt_vn_time(iso_str: str) -> str:
    if not iso_str:
        return "—"
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(VN_TZ)
    return dt.strftime("%d/%m %H:%M UTC+7")


def _describe_event(ev: dict) -> str | None:
    etype = ev.get("type", "")
    payload = ev.get("payload") or {}

    if etype == "WatchEvent":
        return "⭐ Starred"
    if etype == "ForkEvent":
        return "🔱 Forked"
    if etype == "IssuesEvent":
        action = payload.get("action", "updated")
        num = (payload.get("issue") or {}).get("number")
        suffix = f" #{num}" if num else ""
        return f"🐛 Issue {action}{suffix}"
    if etype == "IssueCommentEvent":
        num = (payload.get("issue") or {}).get("number")
        suffix = f" #{num}" if num else ""
        return f"💬 Commented{suffix}"
    if etype == "PullRequestEvent":
        action = payload.get("action", "updated")
        num = (payload.get("pull_request") or {}).get("number")
        suffix = f" #{num}" if num else ""
        return f"🔀 PR {action}{suffix}"
    if etype == "ReleaseEvent":
        tag = (payload.get("release") or {}).get("tag_name", "")
        return f"🚀 Released `{tag}`" if tag else "🚀 Released"
    if etype == "CreateEvent":
        ref_type = payload.get("ref_type", "ref")
        return f"✨ Created {ref_type}"
    if etype == "PushEvent":
        commits = payload.get("commits") or []
        for c in commits:
            msg = c.get("message", "")
            if "[skip ci]" in msg or msg.startswith("chore(profile):"):
                return None
        ref = (payload.get("ref") or "").split("/")[-1]
        return f"📦 Pushed `{ref}`" if ref else "📦 Pushed"
    return None


def _collect_data() -> tuple[int, int, int, list[dict], list[dict]]:
    repos_raw = _api_get(
        f"https://api.github.com/users/{OWNER}/repos?per_page=100&type=owner&sort=updated"
    )
    repos = [r for r in repos_raw if not r.get("private")]

    total_repos = len(repos)
    total_stars = sum(int(r.get("stargazers_count", 0)) for r in repos)
    total_forks = sum(int(r.get("forks_count", 0)) for r in repos)

    all_events: list[dict] = []
    stargazers: list[dict] = []

    for repo in repos[:12]:
        name = repo["name"]
        try:
            evs = _api_get(
                f"https://api.github.com/repos/{OWNER}/{name}/events?per_page=20"
            )
            for ev in evs:
                actor = (ev.get("actor") or {}).get("login", "")
                if not actor or actor == "github-actions[bot]":
                    continue
                label = _describe_event(ev)
                if not label:
                    continue
                all_events.append(
                    {
                        "id": ev.get("id", ""),
                        "label": label,
                        "actor": actor,
                        "repo": name,
                        "created_at": ev.get("created_at", ""),
                    }
                )
        except urllib.error.HTTPError:
            pass

        if int(repo.get("stargazers_count", 0)) > 0:
            try:
                stars_raw = _api_get(
                    f"https://api.github.com/repos/{OWNER}/{name}/stargazers?per_page=30",
                    accept="application/vnd.github.star+json",
                )
            except urllib.error.HTTPError:
                try:
                    stars_raw = _api_get(
                        f"https://api.github.com/repos/{OWNER}/{name}/stargazers?per_page=30"
                    )
                except urllib.error.HTTPError:
                    stars_raw = []

            for item in stars_raw:
                if isinstance(item, dict) and "user" in item:
                    u = item["user"]
                    starred_at = item.get("starred_at", "")
                else:
                    u = item
                    starred_at = ""
                login = u.get("login")
                if not login:
                    continue
                stargazers.append(
                    {
                        "login": login,
                        "avatar_url": u.get("avatar_url", ""),
                        "html_url": u.get("html_url", f"https://github.com/{login}"),
                        "repo": name,
                        "starred_at": starred_at,
                    }
                )

    # Sort events newest first & deduplicate
    seen_ev: set[str] = set()
    unique_events: list[dict] = []
    for ev in sorted(all_events, key=lambda x: x["created_at"], reverse=True):
        key = f"{ev['label']}:{ev['actor']}:{ev['repo']}:{ev['created_at']}"
        if key not in seen_ev:
            seen_ev.add(key)
            unique_events.append(ev)

    # Sort stargazers newest first & deduplicate by login
    seen_star: set[str] = set()
    unique_stars: list[dict] = []
    for st in sorted(stargazers, key=lambda x: x["starred_at"], reverse=True):
        if st["login"] not in seen_star:
            seen_star.add(st["login"])
            unique_stars.append(st)

    return total_repos, total_stars, total_forks, unique_events[:MAX_ACTIVITY_ROWS], unique_stars[:MAX_STARGAZERS]


def _render_section() -> str:
    total_repos, total_stars, total_forks, events, stars = _collect_data()

    lines = [
        '<div align="center">',
        f'  <sub><b>Repos:</b> <code>{total_repos}</code> &nbsp;·&nbsp; <b>Total Stars:</b> <code>{total_stars}</code> &nbsp;·&nbsp; <b>Forks:</b> <code>{total_forks}</code> &nbsp;·&nbsp; <i>Synced automatically via GitHub Actions</i></sub>',
        "</div>",
        "",
    ]

    if events:
        lines.append('<div align="center">')
        lines.append("")
        lines.append("| Activity | Actor | Repository | Time (UTC+7) |")
        lines.append("|---|---|---|---|")
        for ev in events:
            actor = ev["actor"]
            repo = ev["repo"]
            lines.append(
                f"| {ev['label']} | [`@{actor}`](https://github.com/{actor}) | [`{OWNER}/{repo}`](https://github.com/{OWNER}/{repo}) | `{_fmt_vn_time(ev['created_at'])}` |"
            )
        lines.append("")
        lines.append("</div>")
    else:
        lines.append(
            '<p align="center"><sub>No recent public events recorded yet — drop a ⭐ on a repo to appear here!</sub></p>'
        )

    if stars:
        lines.append("")
        lines.append('<div align="center">')
        lines.append("  <sub><b>Recent Stargazers</b></sub><br />")
        avatar_links = []
        for st in stars:
            login = st["login"]
            av = st["avatar_url"]
            if "?" in av:
                av_small = f"{av}&s=56"
            else:
                av_small = f"{av}?s=56"
            title = f"@{login} starred {OWNER}/{st['repo']}"
            avatar_links.append(
                f'  <a href="{st["html_url"]}" title="{title}"><img src="{av_small}" width="28" height="28" alt="@{login}" /></a>'
            )
        lines.extend(avatar_links)
        lines.append("</div>")

    return "\n".join(lines)


def main() -> None:
    content = README_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        flags=re.DOTALL,
    )
    if not pattern.search(content):
        raise RuntimeError("Activity section markers missing from README.md")

    block = f"{START_MARKER}\n{_render_section()}\n{END_MARKER}"
    updated = pattern.sub(lambda _: block, content, count=1)
    if updated != content:
        README_PATH.write_text(updated, encoding="utf-8")
        print("README.md updated.")
    else:
        print("README.md already up to date.")


if __name__ == "__main__":
    main()
