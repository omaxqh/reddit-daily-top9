#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, quote_plus, urlparse

TZ = timezone(timedelta(hours=8))
DEFAULT_DAILY_CAP = 100
DEFAULT_TOPICS = [
    {
        "type": "subreddit",
        "raw_input": "r/openclaw",
        "canonical_url": "https://www.reddit.com/r/openclaw/",
        "normalized_key": "subreddit:r/openclaw",
        "label": "r/openclaw",
        "enabled": True,
        "source": "starter",
        "priority": "normal",
        "daily_cap": DEFAULT_DAILY_CAP,
        "added_at": "",
    }
]


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def default_topics_path(base_dir: str) -> str:
    return f"{base_dir.rstrip('/')}/topics.json"


def build_search_rss(query: str) -> str:
    return f"https://www.reddit.com/search.rss?q={quote_plus(query)}&sort=hot&t=day"


def compact_text(text: str) -> str:
    return re.sub(r"\s{2,}", " ", str(text or "").strip())


def subreddit_name_from_text(text: str) -> str:
    raw = compact_text(text)
    match = re.search(r"(?:^|/)r/([A-Za-z0-9_]+)", raw)
    if match:
        return match.group(1)
    parsed = urlparse(raw)
    match = re.search(r"/r/([A-Za-z0-9_]+)", parsed.path)
    if match:
        return match.group(1)
    return ""


def infer_topic_type(raw_input: str) -> str:
    raw = compact_text(raw_input)
    lowered = raw.lower()
    if re.search(r"/comments/[a-z0-9]+/", lowered):
        return "post"
    if lowered.startswith("r/") or subreddit_name_from_text(raw):
        return "subreddit"
    if "search.rss" in lowered or "/search" in lowered:
        return "search"
    if lowered.endswith(".rss") or lowered.endswith("/rss") or "atom" in lowered:
        return "feed"
    return "keyword"


def normalize_topic(item: Any) -> Dict[str, Any] | None:
    if isinstance(item, dict):
        raw_input = compact_text(item.get("raw_input") or item.get("input") or item.get("label") or item.get("url") or "")
        topic_type = compact_text(item.get("type") or infer_topic_type(raw_input)).lower()
        enabled = bool(item.get("enabled", True))
        source = compact_text(item.get("source") or "user") or "user"
        priority = compact_text(item.get("priority") or "normal") or "normal"
        daily_cap = int(item.get("daily_cap") or DEFAULT_DAILY_CAP)
        added_at = compact_text(item.get("added_at") or now_iso())
    else:
        raw_input = compact_text(item)
        topic_type = infer_topic_type(raw_input)
        enabled = True
        source = "user"
        priority = "normal"
        daily_cap = DEFAULT_DAILY_CAP
        added_at = now_iso()

    if not raw_input:
        return None

    canonical_url = raw_input
    normalized_key = ""
    label = raw_input

    if topic_type == "subreddit":
        sub = subreddit_name_from_text(raw_input)
        if not sub:
            return None
        label = f"r/{sub}"
        canonical_url = f"https://www.reddit.com/r/{sub}/"
        normalized_key = f"subreddit:r/{sub.lower()}"
    elif topic_type == "keyword":
        query = raw_input
        label = query
        canonical_url = build_search_rss(query)
        normalized_key = f"keyword:{query.lower()}"
    elif topic_type == "search":
        parsed = urlparse(raw_input)
        query = parse_qs(parsed.query).get("q", [""])[0].strip()
        if query:
            canonical_url = build_search_rss(query)
            label = f"search/{query}"
            normalized_key = f"search:{query.lower()}"
        else:
            canonical_url = raw_input
            label = compact_text(item.get("label") if isinstance(item, dict) else raw_input) if isinstance(item, dict) else raw_input
            normalized_key = f"search:{raw_input.lower()}"
    elif topic_type == "feed":
        canonical_url = raw_input
        label = compact_text(item.get("label") if isinstance(item, dict) else raw_input) if isinstance(item, dict) else raw_input
        normalized_key = f"feed:{canonical_url.lower()}"
    elif topic_type == "post":
        canonical_url = raw_input
        match = re.search(r"/comments/([a-z0-9]+)/", raw_input, re.IGNORECASE)
        post_id = match.group(1).lower() if match else raw_input.lower()
        label = compact_text(item.get("label") if isinstance(item, dict) else f"post/{post_id}") if isinstance(item, dict) else f"post/{post_id}"
        normalized_key = f"post:{post_id}"
    else:
        return None

    return {
        "type": topic_type,
        "raw_input": raw_input,
        "canonical_url": canonical_url,
        "normalized_key": normalized_key,
        "label": label,
        "enabled": enabled,
        "source": source,
        "priority": priority,
        "daily_cap": daily_cap,
        "added_at": added_at,
    }


def load_topics(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except FileNotFoundError:
        payload = DEFAULT_TOPICS
    except Exception:
        payload = DEFAULT_TOPICS

    if not isinstance(payload, list):
        payload = DEFAULT_TOPICS

    rows: List[Dict[str, Any]] = []
    for item in payload:
        topic = normalize_topic(item)
        if topic:
            rows.append(topic)
    return rows or [normalize_topic(DEFAULT_TOPICS[0])]  # type: ignore[list-item]


def resolve_topic_to_feeds(topic: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[str]]:
    if not topic.get("enabled", True):
        return [], []

    topic_type = compact_text(topic.get("type", "")).lower()
    label = compact_text(topic.get("label", "")) or compact_text(topic.get("raw_input", ""))
    topic_key = compact_text(topic.get("normalized_key", ""))
    daily_cap = str(int(topic.get("daily_cap") or DEFAULT_DAILY_CAP))

    if topic_type == "subreddit":
        sub = subreddit_name_from_text(topic.get("canonical_url", "") or topic.get("raw_input", ""))
        if not sub:
            return [], [f"invalid_subreddit:{label}"]
        return ([{
            "name": label,
            "url": f"https://www.reddit.com/r/{sub}/hot/.rss?limit=30",
            "topic_key": topic_key,
            "topic_label": label,
            "topic_type": topic_type,
            "daily_cap": daily_cap,
        }], [])

    if topic_type in {"keyword", "search"}:
        url = compact_text(topic.get("canonical_url", ""))
        if not url:
            return [], [f"invalid_search:{label}"]
        return ([{
            "name": label,
            "url": url,
            "topic_key": topic_key,
            "topic_label": label,
            "topic_type": topic_type,
            "daily_cap": daily_cap,
        }], [])

    if topic_type == "feed":
        url = compact_text(topic.get("canonical_url", ""))
        if not url:
            return [], [f"invalid_feed:{label}"]
        return ([{
            "name": label,
            "url": url,
            "topic_key": topic_key,
            "topic_label": label,
            "topic_type": topic_type,
            "daily_cap": daily_cap,
        }], [])

    if topic_type == "post":
        return [], [f"unsupported_post_topic:{label}"]

    return [], [f"unsupported_topic:{label}"]


def resolve_topics_to_feeds(topics: List[Dict[str, Any]]) -> Tuple[List[Dict[str, str]], List[str]]:
    feeds: List[Dict[str, str]] = []
    warnings: List[str] = []
    seen: set[tuple[str, str]] = set()

    for topic in topics:
        resolved, topic_warnings = resolve_topic_to_feeds(topic)
        warnings.extend(topic_warnings)
        for feed in resolved:
            key = (feed["name"], feed["url"])
            if key in seen:
                continue
            seen.add(key)
            feeds.append(feed)

    return feeds, warnings
