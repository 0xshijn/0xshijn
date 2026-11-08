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


def _api_get(url: str, *, accept: str = "application/vnd.github+json", use_auth: bool = True) -> list | dict:
    headers = {
        "Accept": accept,
        "User-Agent": f"{OWNER}-profile-readme-updater",
    }
    if use_auth and TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fmt_vn_time(iso_str: str) -> str:
    if not iso_str:
        return "—"
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(VN_TZ)
    return dt.strftime("%d/%m %H:%M UTC+7")


def _clean_avatar(url: str, size: int = 56) -> str:
    if not url:
        return ""
    base = url.split("?", 1)[0]
    return f"{base}?v=4&s={size}"


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


def _seed_webhook_event(all_events: list[dict], stargazers: list[dict]) -> None:
    """Capture immediate watch/fork/issue webhook sender before REST /events indexing."""
    event_path = os.environ.get("GITHUB_EVENT_PATH", "").strip()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "").strip()
    if not event_path or not Path(event_path).is_file():
        return
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except Exception:
        return

    sender = payload.get("sender") or {}
    repo = (payload.get("repository") or {}).get("name", OWNER)
    login = sender.get("login", "")
    if not login or login == "github-actions[bot]":
        return

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if event_name == "watch":
        all_events.append({"label": "⭐ Starred", "actor": login, "repo": repo, "created_at": now_iso})
        stargazers.append({
            "login": login,
            "avatar_url": sender.get("avatar_url", ""),
            "html_url": sender.get("html_url", f"https://github.com/{login}"),
            "repo": repo,
            "starred_at": now_iso,
        })
    elif event_name == "fork":
        all_events.append({"label": "🔱 Forked", "actor": login, "repo": repo, "created_at": now_iso})
    elif event_name == "issues":
        num = (payload.get("issue") or {}).get("number")
        action = payload.get("action", "updated")
        suffix = f" #{num}" if num else ""
        all_events.append({"label": f"🐛 Issue {action}{suffix}", "actor": login, "repo": repo, "created_at": now_iso})


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
    _seed_webhook_event(all_events, stargazers)

    for repo in repos[:12]:
        name = repo["name"]
        try:
            evs = _api_get(f"https://api.github.com/repos/{OWNER}/{name}/events?per_page=30")
        except urllib.error.HTTPError:
            try:
                evs = _api_get(f"https://api.github.com/repos/{OWNER}/{name}/events?per_page=30", use_auth=False)
            except urllib.error.HTTPError:
                evs = []

        for ev in evs:
            actor_obj = ev.get("actor") or {}
            actor = actor_obj.get("login", "")
            if not actor or actor == "github-actions[bot]":
                continue
            label = _describe_event(ev)
            if not label:
                continue
            created = ev.get("created_at", "")
            all_events.append({
                "label": label,
                "actor": actor,
                "repo": name,
                "created_at": created,
            })
            if ev.get("type") == "WatchEvent":
                stargazers.append({
                    "login": actor,
                    "avatar_url": actor_obj.get("avatar_url", ""),
                    "html_url": f"https://github.com/{actor}",
                    "repo": name,
                    "starred_at": created,
                })

        if int(repo.get("stargazers_count", 0)) > 0:
            try:
                stars_raw = _api_get(
                    f"https://api.github.com/repos/{OWNER}/{name}/stargazers?per_page=30",
                    accept="application/vnd.github.star+json",
                )
            except urllib.error.HTTPError:
                stars_raw = []

            for item in stars_raw:
                u = item["user"] if isinstance(item, dict) and "user" in item else item
                login = u.get("login")
                if not login:
                    continue
                stargazers.append({
                    "login": login,
                    "avatar_url": u.get("avatar_url", ""),
                    "html_url": u.get("html_url", f"https://github.com/{login}"),
                    "repo": name,
                    "starred_at": item.get("starred_at", "") if isinstance(item, dict) else "",
                })

    # Deduplicate events (prefer webhook/REST matching without second-level drift if identical actor+repo+label)
    seen_ev: set[str] = set()
    unique_events: list[dict] = []
    for ev in sorted(all_events, key=lambda x: x["created_at"], reverse=True):
        day_hour = ev["created_at"][:13]
        key = f"{ev['label']}:{ev['actor']}:{ev['repo']}:{day_hour}"
        if key not in seen_ev:
            seen_ev.add(key)
            unique_events.append(ev)

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
        for st in stars:
            login = st["login"]
            av_small = _clean_avatar(st["avatar_url"], 56) or f"https://github.com/{login}.png?size=56"
            title = f"@{login} starred {OWNER}/{st['repo']}"
            lines.append(
                f'  <a href="{st["html_url"]}" title="{title}"><img src="{av_small}" width="28" height="28" alt="@{login}" /></a>'
            )
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
