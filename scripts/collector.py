#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any, Dict, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

TZ = timezone(timedelta(hours=8))
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
USER_AGENT = "Mozilla/5.0 (compatible; openclaw-reddit-daily/1.0)"
TMP_SUFFIX = ".tmp"

DEFAULT_FEEDS = [
    {"name": "r/openclaw", "url": "https://www.reddit.com/r/openclaw/hot/.rss?limit=30"},
    {"name": "r/OpenClawUseCases", "url": "https://www.reddit.com/r/OpenClawUseCases/hot/.rss?limit=30"},
    {"name": "search/openclaw", "url": "https://www.reddit.com/search.rss?q=openclaw&sort=hot&t=day"},
]


def load_feeds(path: str) -> List[Dict[str, str]]:
    if not path:
        return DEFAULT_FEEDS
    try:
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except FileNotFoundError:
        return DEFAULT_FEEDS
    if not isinstance(payload, list):
        return DEFAULT_FEEDS
    rows: List[Dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if name and url:
            rows.append({"name": name, "url": url})
    return rows or DEFAULT_FEEDS


def now_cn() -> datetime:
    return datetime.now(TZ)


def resolve_stop_at(window_hours: float, stop_hour: int | None) -> datetime:
    now = now_cn()
    if stop_hour is None:
        return now + timedelta(hours=window_hours)
    return now.replace(hour=stop_hour, minute=0, second=0, microsecond=0)


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def ensure_dirs(base_dir: str, date_key: str) -> Dict[str, str]:
    daily_dir = os.path.join(base_dir, "daily", date_key)
    raw_rss = os.path.join(daily_dir, "raw", "rss")
    raw_comments = os.path.join(daily_dir, "raw", "comments")
    posts_dir = os.path.join(daily_dir, "posts")
    comments_dir = os.path.join(daily_dir, "comments")
    clean_dir = os.path.join(daily_dir, "clean")
    for path in (raw_rss, raw_comments, posts_dir, comments_dir, clean_dir):
        os.makedirs(path, exist_ok=True)
    return {
        "daily": daily_dir,
        "raw_rss": raw_rss,
        "raw_comments": raw_comments,
        "posts": posts_dir,
        "comments": comments_dir,
        "clean": clean_dir,
        "progress": os.path.join(daily_dir, "progress.log"),
    }


def append_log(path: str, message: str) -> None:
    ts = now_cn().strftime("%Y-%m-%d %H:%M:%S %z")
    with open(path, "a", encoding="utf-8") as file:
        file.write(f"[{ts}] {message}\n")


def safe_unlink(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def atomic_write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=TMP_SUFFIX, dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_path, path)
    except Exception:
        safe_unlink(tmp_path)
        raise


def load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path: str, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def load_manifest(path: str, date_key: str) -> Dict[str, Any]:
    manifest = load_json(path, {})
    if manifest.get("date") != date_key:
        return {"date": date_key, "updated_at": "", "posts": {}}
    manifest.setdefault("posts", {})
    return manifest


def save_manifest(path: str, manifest: Dict[str, Any]) -> None:
    manifest["updated_at"] = now_cn().isoformat(timespec="seconds")
    save_json(path, manifest)


def acquire_lock(lock_path: str, progress_path: str) -> bool:
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    if os.path.exists(lock_path):
        try:
            payload = load_json(lock_path, {})
        except Exception:
            payload = {}
        existing_pid = int(payload.get("pid", 0) or 0)
        if pid_exists(existing_pid):
            append_log(progress_path, f"collector_skip reason=lock_active pid={existing_pid}")
            return False
        safe_unlink(lock_path)

    save_json(lock_path, {"pid": os.getpid(), "started_at": now_cn().isoformat(timespec="seconds")})

    def cleanup() -> None:
        safe_unlink(lock_path)

    atexit.register(cleanup)
    return True


def cleanup_temp_files(root_dir: str) -> int:
    removed = 0
    for current_root, _, files in os.walk(root_dir):
        for name in files:
            if not name.endswith(TMP_SUFFIX):
                continue
            safe_unlink(os.path.join(current_root, name))
            removed += 1
    return removed


def clean_html_to_text(html: str) -> str:
    text = html or ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def strip_reddit_rss_footer(text: str) -> str:
    cleaned = text or ""
    cleaned = re.sub(r"\n*submitted by\s+/u/[^\n]*\n\s*\[link\]\s*\[comments\]\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_post_id(url: str) -> str:
    match = re.search(r"/comments/([a-z0-9]+)/", url, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def http_get(url: str, timeout: int = 25) -> Tuple[bool, str, str]:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
        return True, text, ""
    except HTTPError as error:
        return False, "", f"http_{error.code}"
    except URLError as error:
        return False, "", f"url_error:{error.reason}"
    except Exception as error:
        return False, "", f"unexpected:{error}"


def parse_feed_entries(feed_xml: str, feed_name: str, feed_url: str) -> List[Dict[str, Any]]:
    root = ET.fromstring(feed_xml)
    entries = root.findall("a:entry", ATOM_NS)
    rows: List[Dict[str, Any]] = []
    for entry in entries:
        title = (entry.findtext("a:title", default="", namespaces=ATOM_NS) or "").strip()
        updated = (entry.findtext("a:updated", default="", namespaces=ATOM_NS) or "").strip()
        entry_id = (entry.findtext("a:id", default="", namespaces=ATOM_NS) or "").strip()
        author = ""
        author_node = entry.find("a:author/a:name", ATOM_NS)
        if author_node is not None and author_node.text:
            author = author_node.text.strip()

        link = ""
        link_node = entry.find("a:link", ATOM_NS)
        if link_node is not None:
            link = (link_node.attrib.get("href") or "").strip()

        content_html = ""
        content_node = entry.find("a:content", ATOM_NS)
        if content_node is not None and content_node.text:
            content_html = content_node.text

        summary_html = ""
        summary_node = entry.find("a:summary", ATOM_NS)
        if summary_node is not None and summary_node.text:
            summary_html = summary_node.text

        post_id = extract_post_id(link)
        if not post_id and entry_id:
            post_id = extract_post_id(entry_id)
        if not post_id:
            continue

        rows.append(
            {
                "id": post_id,
                "title": title,
                "url": link,
                "author": author,
                "updated": updated,
                "entry_id": entry_id,
                "feed_name": feed_name,
                "feed_url": feed_url,
                "body_html": content_html,
                "summary_html": summary_html,
            }
        )
    return rows


def fetch_comments_rss(post_url: str, raw_comments_dir: str, post_id: str) -> Tuple[List[str], str, str, List[Dict[str, Any]]]:
    candidates = [f"{post_url.rstrip('/')}/.rss", f"{post_url.rstrip('/')}.rss"]
    attempts: List[Dict[str, Any]] = []

    for index, candidate in enumerate(candidates, start=1):
        ok, body, error = http_get(candidate)
        attempts.append({"stage": f"comments_rss_{index}", "url": candidate, "ok": ok, "error": error})
        if not ok:
            continue

        raw_path = os.path.join(raw_comments_dir, f"{post_id}_comments.xml")
        atomic_write_text(raw_path, body)

        try:
            root = ET.fromstring(body)
            entries = root.findall("a:entry", ATOM_NS)
            comments: List[str] = []
            seen = set()
            for entry in entries:
                content_node = entry.find("a:content", ATOM_NS)
                fragment = content_node.text if content_node is not None and content_node.text else ""
                text = clean_html_to_text(fragment)
                if not text or text.lower() == "[deleted]":
                    continue
                if text in seen:
                    continue
                seen.add(text)
                comments.append(text)
            return comments, "done", "", attempts
        except Exception as error:
            attempts.append({"stage": "comments_parse", "url": candidate, "ok": False, "error": f"parse_error:{error}"})

    fail_reason = attempts[-1]["error"] if attempts else "comments_fetch_failed"
    return [], "partial", fail_reason, attempts


def summarize_body(entry: Dict[str, Any]) -> str:
    body = strip_reddit_rss_footer(clean_html_to_text(entry.get("body_html", "")))
    if body:
        return body
    return strip_reddit_rss_footer(clean_html_to_text(entry.get("summary_html", "")))


def write_markdown_post(path: str, post: Dict[str, Any]) -> None:
    lines = [
        f"# {post.get('title', '')}",
        "",
        f"- id: {post.get('id', '')}",
        f"- url: {post.get('url', '')}",
        f"- feed: {post.get('feed_name', '')}",
        f"- fetch_status: {post.get('fetch_status', '')}",
        f"- fail_reason: {post.get('fail_reason', '')}",
        "",
        "## Post",
        post.get("body", ""),
        "",
    ]
    atomic_write_text(path, "\n".join(lines))


def write_markdown_comments(path: str, comments: List[str]) -> None:
    lines = [f"## Comments (flattened {len(comments)})"]
    for comment in comments:
        lines.append(f"- {' '.join(comment.split())}")
    lines.append("")
    atomic_write_text(path, "\n".join(lines))


def remove_post_artifacts(dirs: Dict[str, str], post_id: str) -> None:
    for path in (
        os.path.join(dirs["posts"], f"{post_id}.json"),
        os.path.join(dirs["posts"], f"{post_id}.md"),
        os.path.join(dirs["comments"], f"{post_id}_comments.md"),
        os.path.join(dirs["raw_comments"], f"{post_id}_comments.xml"),
    ):
        safe_unlink(path)


def persist_round_state(dirs: Dict[str, str], collected_by_id: Dict[str, Dict[str, Any]], manifest: Dict[str, Any], manifest_path: str) -> None:
    merged = sorted(collected_by_id.values(), key=lambda item: item.get("captured_at", ""))
    save_json(os.path.join(dirs["clean"], "report_source.json"), merged)
    save_manifest(manifest_path, manifest)


def reconcile_day_state(
    dirs: Dict[str, str],
    date_key: str,
    seen_today: set[str],
    seen_global: Dict[str, str],
) -> Tuple[Dict[str, Any], str]:
    report_source_path = os.path.join(dirs["clean"], "report_source.json")
    manifest_path = os.path.join(dirs["clean"], "manifest.json")
    manifest = load_manifest(manifest_path, date_key)

    temp_removed = cleanup_temp_files(dirs["daily"])
    if temp_removed:
        append_log(dirs["progress"], f"recovery_tmp_removed count={temp_removed}")

    collected = load_json(report_source_path, [])
    collected_by_id = {item.get("id"): item for item in collected if item.get("id")}

    disk_post_ids = {
        name[:-5]
        for name in os.listdir(dirs["posts"])
        if name.endswith(".json") and not name.endswith(TMP_SUFFIX)
    }
    all_post_ids = set(collected_by_id) | set(manifest.get("posts", {})) | disk_post_ids

    dirty = False
    manifest_posts = manifest.setdefault("posts", {})

    for post_id in sorted(all_post_ids):
        post_json_path = os.path.join(dirs["posts"], f"{post_id}.json")
        if not os.path.exists(post_json_path):
            if post_id in collected_by_id or post_id in manifest_posts:
                remove_post_artifacts(dirs, post_id)
                collected_by_id.pop(post_id, None)
                manifest_posts.pop(post_id, None)
                seen_today.discard(post_id)
                seen_global.pop(post_id, None)
                dirty = True
                append_log(dirs["progress"], f"recovery_reset post={post_id} reason=missing_post_json")
            continue

        try:
            record = load_json(post_json_path, {})
        except Exception as error:
            remove_post_artifacts(dirs, post_id)
            collected_by_id.pop(post_id, None)
            manifest_posts.pop(post_id, None)
            seen_today.discard(post_id)
            seen_global.pop(post_id, None)
            dirty = True
            append_log(dirs["progress"], f"recovery_reset post={post_id} reason=invalid_post_json error={error}")
            continue

        if record.get("id") != post_id:
            remove_post_artifacts(dirs, post_id)
            collected_by_id.pop(post_id, None)
            manifest_posts.pop(post_id, None)
            seen_today.discard(post_id)
            seen_global.pop(post_id, None)
            dirty = True
            append_log(dirs["progress"], f"recovery_reset post={post_id} reason=id_mismatch")
            continue

        collected_by_id[post_id] = record
        manifest_posts[post_id] = {
            "id": post_id,
            "title": record.get("title", ""),
            "url": record.get("url", ""),
            "feed_name": record.get("feed_name", ""),
            "captured_at": record.get("captured_at", ""),
            "fetch": {
                "status": record.get("fetch_status", "done"),
                "body_ready": bool(record.get("body")),
                "comments_ready": bool(record.get("comments")),
                "record_ready": True,
                "fail_reason": record.get("fail_reason", ""),
                "updated_at": record.get("captured_at", now_cn().isoformat(timespec="seconds")),
            }
        }

    if dirty or temp_removed:
        persist_round_state(dirs, collected_by_id, manifest, manifest_path)

    return manifest, manifest_path


def process_round(
    dirs: Dict[str, str],
    manifest: Dict[str, Any],
    manifest_path: str,
    seen_today: set[str],
    seen_global: Dict[str, str],
    max_posts_per_round: int,
    feeds: List[Dict[str, str]],
) -> Tuple[int, int, int]:
    collected = load_json(os.path.join(dirs["clean"], "report_source.json"), [])
    collected_by_id = {item.get("id"): item for item in collected if item.get("id")}

    new_feed_rows: List[Dict[str, Any]] = []
    for feed in feeds:
        ok, body, error = http_get(feed["url"])
        filename = f"{feed['name'].replace('/', '_')}.xml"
        raw_path = os.path.join(dirs["raw_rss"], filename)
        if ok:
            atomic_write_text(raw_path, body)
            try:
                rows = parse_feed_entries(body, feed["name"], feed["url"])
                new_feed_rows.extend(rows)
                append_log(dirs["progress"], f"feed={feed['name']} fetched entries={len(rows)}")
            except Exception as parse_error:
                append_log(dirs["progress"], f"feed={feed['name']} parse_error={parse_error}")
        else:
            append_log(dirs["progress"], f"feed={feed['name']} fetch_error={error}")

    dedup_rows: Dict[str, Dict[str, Any]] = {}
    for row in new_feed_rows:
        dedup_rows.setdefault(row["id"], row)

    accepted = 0
    partial = 0
    failed = 0

    for post_id, entry in dedup_rows.items():
        if post_id in seen_today or post_id in seen_global:
            continue
        if accepted + partial + failed >= max_posts_per_round:
            break

        manifest_entry = manifest.setdefault("posts", {}).setdefault(post_id, {})
        manifest_entry.update(
            {
                "id": post_id,
                "title": entry.get("title", ""),
                "url": entry.get("url", ""),
                "feed_name": entry.get("feed_name", ""),
                "fetch": {
                    "status": "in_progress",
                    "body_ready": False,
                    "comments_ready": False,
                    "record_ready": False,
                    "fail_reason": "",
                    "updated_at": now_cn().isoformat(timespec="seconds"),
                }
            }
        )
        save_manifest(manifest_path, manifest)

        body = summarize_body(entry)
        comments: List[str] = []
        fetch_status = "done" if body else "partial"
        fail_reason = "" if body else "body_missing_from_rss"
        comment_attempts: List[Dict[str, Any]] = []

        for attempt in range(1, 4):
            comments, comments_status, comments_reason, attempts = fetch_comments_rss(
                post_url=entry["url"],
                raw_comments_dir=dirs["raw_comments"],
                post_id=post_id,
            )
            comment_attempts = attempts
            if comments_status == "done":
                break
            if attempt < 3:
                wait_seconds = 2 * attempt
                append_log(dirs["progress"], f"post={post_id} comments_retry={attempt} wait={wait_seconds}s")
                time.sleep(wait_seconds)
            elif comments_reason and not fail_reason:
                fail_reason = comments_reason

        if comments:
            body_set = set(body.split()) if body else set()
            compacted = []
            for text in comments:
                words = set(text.split())
                overlap = len(words.intersection(body_set)) / max(1, len(words)) if body_set else 0
                if overlap < 0.95:
                    compacted.append(text)
            comments = compacted

        if not comments and fetch_status == "done":
            fetch_status = "partial"
            if not fail_reason:
                fail_reason = "comments_missing_from_rss"

        if not body and not comments:
            fetch_status = "failed"
            if not fail_reason:
                fail_reason = "body_and_comments_unavailable"

        record = {
            "id": post_id,
            "title": entry.get("title", ""),
            "url": entry.get("url", ""),
            "author": entry.get("author", ""),
            "updated": entry.get("updated", ""),
            "feed_name": entry.get("feed_name", ""),
            "feed_url": entry.get("feed_url", ""),
            "body": body,
            "comments": comments,
            "fetch_status": fetch_status,
            "fail_reason": fail_reason,
            "fetch_attempts": comment_attempts,
            "captured_at": now_cn().isoformat(timespec="seconds"),
        }

        try:
            save_json(os.path.join(dirs["posts"], f"{post_id}.json"), record)
            write_markdown_post(os.path.join(dirs["posts"], f"{post_id}.md"), record)
            write_markdown_comments(os.path.join(dirs["comments"], f"{post_id}_comments.md"), comments)
        except Exception as error:
            remove_post_artifacts(dirs, post_id)
            manifest_entry["fetch"] = {
                "status": "pending",
                "body_ready": False,
                "comments_ready": False,
                "record_ready": False,
                "fail_reason": f"write_failed:{error}",
                "updated_at": now_cn().isoformat(timespec="seconds"),
            }
            save_manifest(manifest_path, manifest)
            append_log(dirs["progress"], f"post={post_id} write_failed error={error}")
            failed += 1
            continue

        collected_by_id[post_id] = record
        seen_today.add(post_id)
        seen_global[post_id] = now_cn().strftime("%Y-%m-%d")

        manifest_entry.update(
            {
                "id": post_id,
                "title": record.get("title", ""),
                "url": record.get("url", ""),
                "feed_name": record.get("feed_name", ""),
                "captured_at": record.get("captured_at", ""),
                "fetch": {
                    "status": fetch_status,
                    "body_ready": bool(body),
                    "comments_ready": bool(comments),
                    "record_ready": True,
                    "fail_reason": fail_reason,
                    "updated_at": now_cn().isoformat(timespec="seconds"),
                }
            }
        )

        persist_round_state(dirs, collected_by_id, manifest, manifest_path)

        if fetch_status == "done":
            accepted += 1
        elif fetch_status == "partial":
            partial += 1
        else:
            failed += 1

        append_log(
            dirs["progress"],
            f"post={post_id} status={fetch_status} body_len={len(body)} comments={len(comments)} fail_reason={fail_reason or '-'}",
        )

    persist_round_state(dirs, collected_by_id, manifest, manifest_path)
    return accepted, partial, failed


def run(args: argparse.Namespace) -> int:
    date_key = now_cn().strftime("%Y-%m-%d")
    dirs = ensure_dirs(args.base_dir, date_key)

    state_dir = os.path.join(args.base_dir, "state")
    os.makedirs(state_dir, exist_ok=True)
    seen_today_path = os.path.join(dirs["clean"], "seen_ids.json")
    seen_global_path = os.path.join(state_dir, "global_seen_ids.json")
    lock_path = os.path.join(state_dir, "collector.lock")

    if not acquire_lock(lock_path, dirs["progress"]):
        return 0

    seen_today = set(load_json(seen_today_path, []))
    seen_global = load_json(seen_global_path, {})
    manifest, manifest_path = reconcile_day_state(dirs, date_key, seen_today, seen_global)

    stop_at = resolve_stop_at(args.window_hours, args.stop_hour)
    append_log(
        dirs["progress"],
        f"collector_start once={args.once} window_hours={args.window_hours} interval_minutes={args.interval_minutes} stop_at={stop_at.isoformat(timespec='seconds')}",
    )

    if args.once and now_cn() >= stop_at:
        append_log(dirs["progress"], f"collector_skip reason=past_stop_at stop_at={stop_at.isoformat(timespec='seconds')}")
        save_json(seen_today_path, sorted(seen_today))
        save_json(seen_global_path, seen_global)
        save_manifest(manifest_path, manifest)
        print(f"daily_dir={dirs['daily']}")
        print(f"manifest_file={manifest_path}")
        return 0

    if args.once:
        accepted, partial, failed = process_round(
            dirs,
            manifest,
            manifest_path,
            seen_today,
            seen_global,
            args.max_posts_per_round,
            load_feeds(args.feeds_file),
        )
        append_log(dirs["progress"], f"round_done done={accepted} partial={partial} failed={failed}")
    else:
        round_no = 0
        while now_cn() < stop_at:
            round_no += 1
            accepted, partial, failed = process_round(
                dirs,
                manifest,
                manifest_path,
                seen_today,
                seen_global,
                args.max_posts_per_round,
                load_feeds(args.feeds_file),
            )
            append_log(dirs["progress"], f"round={round_no} done={accepted} partial={partial} failed={failed}")
            remaining = (stop_at - now_cn()).total_seconds()
            if remaining <= 0:
                break
            sleep_seconds = min(int(args.interval_minutes * 60), int(remaining))
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    save_json(seen_today_path, sorted(seen_today))
    save_json(seen_global_path, seen_global)
    save_manifest(manifest_path, manifest)
    append_log(dirs["progress"], "collector_finish")

    print(f"daily_dir={dirs['daily']}")
    print(f"manifest_file={manifest_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaw Reddit 每日抓取")
    parser.add_argument("--base-dir", default=os.path.expanduser("~/reddit-openclaw-daily"), help="项目根目录")
    parser.add_argument("--feeds-file", default="", help="可选 feeds JSON 文件路径")
    parser.add_argument("--once", action="store_true", help="仅执行一轮")
    parser.add_argument("--window-hours", type=float, default=2.0, help="窗口执行时长（小时）")
    parser.add_argument("--stop-hour", type=int, default=8, help="按当天整点截止（默认 08:00）")
    parser.add_argument("--interval-minutes", type=float, default=20.0, help="轮询间隔（分钟）")
    parser.add_argument("--max-posts-per-round", type=int, default=40, help="每轮最多处理帖子数")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
