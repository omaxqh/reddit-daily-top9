#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

TZ = timezone(timedelta(hours=8))
TMP_SUFFIX = ".tmp"
DEFAULT_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

EXCERPT_CHARS = 320
COMMENT_MAX_COUNT = 2
COMMENT_MAX_CHARS = 120
DEFAULT_PREPARE_TOP_N = 18
DEFAULT_SEND_TOP_N = 9


def now_cn() -> datetime:
    return datetime.now(TZ)


def resolve_date(date_arg: str) -> str:
    if date_arg == "today":
        return now_cn().strftime("%Y-%m-%d")
    if date_arg == "yesterday":
        return (now_cn() - timedelta(days=1)).strftime("%Y-%m-%d")
    return date_arg


def load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


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


def save_json(path: str, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def emit_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def compact_text(text: str) -> str:
    text = text or ""
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def trim_text(text: str, limit: int) -> str:
    text = compact_text(text)
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}…"


def normalize_title(title: str) -> str:
    lowered = compact_text(title).lower()
    lowered = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", lowered)
    lowered = re.sub(r"\s{2,}", " ", lowered)
    return lowered.strip()


def contains_chinese(text: str) -> bool:
    text = text or ""
    return any("一" <= char <= "鿿" for char in text)


def state_hash(items: List[Dict[str, Any]]) -> str:
    raw = json.dumps(items, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def candidate_hash(candidate: Dict[str, Any]) -> str:
    raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def is_noise_comment(text: str) -> bool:
    lowered = compact_text(text).lower()
    if not lowered:
        return True
    if lowered.startswith("welcome to r/"):
        return True
    if lowered.startswith("https://preview.redd.it/"):
        return True
    if lowered.startswith("https://i.redd.it/"):
        return True
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return True
    return False


def choose_comments(comments: List[str], max_count: int = COMMENT_MAX_COUNT) -> List[str]:
    picked: List[str] = []
    for comment in comments:
        text = compact_text(comment)
        if len(text) < 20 or is_noise_comment(text):
            continue
        normalized = trim_text(text, COMMENT_MAX_CHARS)
        if normalized not in picked:
            picked.append(normalized)
        if len(picked) >= max_count:
            break
    return picked


def build_source_seed(post: Dict[str, Any]) -> Dict[str, Any]:
    comments = post.get("comments", []) or []
    body = compact_text(post.get("body", ""))
    return {
        "id": post.get("id", ""),
        "title": post.get("title", "(无标题)"),
        "original_title": post.get("title", "(无标题)"),
        "url": post.get("url", ""),
        "feed_name": post.get("feed_name", ""),
        "fetch_status": post.get("fetch_status", "unknown"),
        "fail_reason": post.get("fail_reason", "") or "-",
        "body_excerpt": trim_text(body, EXCERPT_CHARS) if body else "",
        "body_chars": len(body),
        "comment_count": len(comments),
        "key_comments": choose_comments(comments, max_count=COMMENT_MAX_COUNT),
        "captured_at": post.get("captured_at", ""),
    }


def candidate_score(item: Dict[str, Any]) -> float:
    status = item.get("fetch_status", "unknown")
    body_chars = int(item.get("body_chars", 0) or 0)
    comment_count = int(item.get("comment_count", 0) or 0)

    score = 28.0
    score += min(30.0, body_chars / 18.0)
    score += min(20.0, float(comment_count) * 3.5)
    if status == "partial":
        score -= 8.0
    elif status == "failed":
        score -= 20.0

    if body_chars < 80:
        score -= 6.0
    return round(score, 2)


def dedupe_candidates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best_by_title: Dict[str, Dict[str, Any]] = {}
    for item in items:
        key = normalize_title(item.get("title", "")) or item.get("id", "")
        existing = best_by_title.get(key)
        if existing is None or float(item.get("candidate_score", 0)) > float(existing.get("candidate_score", 0)):
            best_by_title[key] = item
    return list(best_by_title.values())


def load_ranked_candidates(clean_dir: str) -> List[Dict[str, Any]]:
    payload = load_json(os.path.join(clean_dir, "report_source.json"), [])
    items: List[Dict[str, Any]] = []
    if not isinstance(payload, list):
        return items
    for post in payload:
        if not isinstance(post, dict):
            continue
        seed = build_source_seed(post)
        seed["candidate_score"] = candidate_score(seed)
        items.append(seed)
    items = dedupe_candidates(items)
    items.sort(key=lambda item: (float(item.get("candidate_score", 0)), item.get("captured_at", ""), item.get("id", "")), reverse=True)
    return items


def display_title(candidate: Dict[str, Any], index: int) -> str:
    title = compact_text(candidate.get("title", ""))
    if contains_chinese(title):
        return trim_text(title, 28)
    if title:
        return f"Reddit话题：{trim_text(title, 24)}"
    return f"Reddit候选#{index}"


def recognition(candidate: Dict[str, Any]) -> Dict[str, str]:
    comment_count = int(candidate.get("comment_count", 0) or 0)
    status = candidate.get("fetch_status", "unknown")
    if comment_count >= 10:
        level = "高"
        pro = "评论密度高，能看到多侧观点。"
        con = "热度高不代表信息都可靠。"
    elif comment_count >= 3:
        level = "中"
        pro = "评论有补充，可辅助判断。"
        con = "样本量仍有限。"
    else:
        level = "低"
        pro = "至少保留了正文线索。"
        con = "评论样本偏薄。"
    if status != "done":
        level = "低"
        con = "抓取受限，信息完整性不足。"
    return {"level": level, "pro": pro, "con": con}


def build_message_text(candidate: Dict[str, Any], index: int) -> str:
    title = display_title(candidate, index)
    original_title = compact_text(candidate.get("original_title", candidate.get("title", "")))
    excerpt = compact_text(candidate.get("body_excerpt", ""))
    status = candidate.get("fetch_status", "unknown")
    fail_reason = compact_text(candidate.get("fail_reason", ""))
    comments = candidate.get("key_comments", []) or []

    lines = [f"{index}. {title}"]
    if original_title and original_title != title:
        lines.append(f"原标题：{trim_text(original_title, 90)}")

    if excerpt:
        core = excerpt
    else:
        core = "正文抓取受限，本条基于可得元信息保底输出。"
    if status == "partial":
        core = trim_text(f"{core}（评论抓取不完整）", 130)
    elif status == "failed":
        core = "正文与评论均受限，本条仅保留来源线索。"
    lines.append(f"核心观点：{core}")

    if comments:
        lines.append("关键评论：")
        for comment in comments[:COMMENT_MAX_COUNT]:
            lines.append(f"- {compact_text(comment)}")

    vote = recognition(candidate)
    lines.append(f"认可度：{vote['level']}；赞成理由：{vote['pro']}；反对理由：{vote['con']}")
    if status != "done":
        lines.append(f"抓取状态：{status}（{fail_reason or '受限'}）")
    lines.append(candidate.get("url", ""))
    return "\n".join(lines).strip()


def write_report_backup(clean_dir: str, payload: Dict[str, Any]) -> None:
    lines = [f"reddit-daily-top9 日报 [{payload.get('date', '')}]", ""]
    lines.append(f"- source_mode: {payload.get('source_mode', 'report_source_ranked')}")
    lines.append(f"- readiness: {payload.get('readiness', 'unknown')}")
    lines.append(f"- ready_for_send: {payload.get('ready_for_send', False)}")
    lines.append(f"- candidate_count: {payload.get('candidate_count', 0)}")
    lines.append("")

    for item in payload.get("items", []):
        lines.append(item.get("message_text", ""))
        lines.append("")

    atomic_write_text(os.path.join(clean_dir, "report_0800.md"), "\n".join(lines).strip() + "\n")


def build_state(clean_dir: str, date_key: str, prepare_top_n: int, top_n: int) -> Dict[str, Any]:
    ranked = load_ranked_candidates(clean_dir)
    candidates = ranked[:prepare_top_n]
    selected = []

    for index, candidate in enumerate(candidates[:top_n], start=1):
        message_text = build_message_text(candidate, index)
        selected.append(
            {
                "id": candidate.get("id", f"item-{index}"),
                "index": index,
                "candidate": candidate,
                "candidate_hash": candidate_hash(candidate),
                "status": "pending",
                "sent_at": "",
                "error": "",
                "message_text": message_text,
            }
        )

    existing = load_json(os.path.join(clean_dir, "send_state.json"), {})
    existing_items = {item.get("id"): item for item in existing.get("items", []) if isinstance(item, dict)}
    for item in selected:
        old = existing_items.get(item["id"], {})
        if old.get("candidate_hash") == item["candidate_hash"] and old.get("status") == "sent":
            item["status"] = "sent"
            item["sent_at"] = old.get("sent_at", "")
            item["error"] = old.get("error", "")

    ready = len(selected) >= top_n
    payload = {
        "date": date_key,
        "generated_at": now_cn().isoformat(timespec="seconds"),
        "source_mode": "report_source_ranked",
        "readiness": "green" if ready else "red",
        "ready_for_send": ready,
        "reason": "候选池满足发送要求。" if ready else "候选不足，无法稳定凑满发送条数。",
        "candidate_count": len(candidates),
        "prepare_top_n": prepare_top_n,
        "send_top_n": top_n,
        "state_version": state_hash(selected),
        "items": selected,
    }
    save_json(os.path.join(clean_dir, "send_state.json"), payload)
    write_report_backup(clean_dir, payload)
    return payload


def summarize_state(payload: Dict[str, Any]) -> Dict[str, Any]:
    items = payload.get("items", []) if isinstance(payload, dict) else []
    total = len(items)
    sent = sum(1 for item in items if item.get("status") == "sent")
    failed = sum(1 for item in items if item.get("status") == "failed")
    pending = sum(1 for item in items if item.get("status") == "pending")
    return {
        "date": payload.get("date", ""),
        "generated_at": payload.get("generated_at", ""),
        "source_mode": payload.get("source_mode", "unknown"),
        "readiness": payload.get("readiness", "unknown"),
        "ready_for_send": payload.get("ready_for_send", False),
        "reason": payload.get("reason", ""),
        "candidate_count": payload.get("candidate_count", total),
        "total": total,
        "sent": sent,
        "failed": failed,
        "pending": pending,
        "state_version": payload.get("state_version", ""),
    }


def load_send_state(clean_dir: str) -> Dict[str, Any]:
    path = os.path.join(clean_dir, "send_state.json")
    payload = load_json(path, {})
    if not isinstance(payload, dict) or not payload.get("items"):
        raise FileNotFoundError(f"send_state missing or empty: {path}")
    return payload


def update_status(clean_dir: str, item_id: str, new_status: str, error: str = "") -> Dict[str, Any]:
    path = os.path.join(clean_dir, "send_state.json")
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        raise FileNotFoundError(f"send_state missing: {path}")

    now_text = now_cn().isoformat(timespec="seconds")
    matched = None
    for item in payload.get("items", []):
        if item.get("id") != item_id:
            continue
        item["status"] = new_status
        item["error"] = error
        if new_status == "sent":
            item["sent_at"] = now_text
        else:
            item["sent_at"] = ""
        matched = item
        break

    if matched is None:
        raise ValueError(f"item not found: {item_id}")

    payload["generated_at"] = now_text
    save_json(path, payload)
    write_report_backup(clean_dir, payload)

    ack = {"ok": True, "id": item_id, "status": new_status, "generated_at": now_text}
    if matched.get("sent_at"):
        ack["sent_at"] = matched["sent_at"]
    if error:
        ack["error"] = error
    return ack


def list_pending(clean_dir: str) -> Dict[str, Any]:
    payload = load_send_state(clean_dir)
    items = []
    for item in payload.get("items", []):
        if item.get("status") != "pending":
            continue
        items.append({"id": item.get("id", ""), "message_text": item.get("message_text", "")})
    return {
        "date": payload.get("date", ""),
        "ready_for_send": payload.get("ready_for_send", False),
        "count": len(items),
        "items": items,
    }


def run_prepare(args: argparse.Namespace) -> int:
    date_key = resolve_date(args.date)
    clean_dir = os.path.join(args.base_dir, "daily", date_key, "clean")
    payload = build_state(clean_dir, date_key, args.prepare_top_n, args.top_n)
    emit_json(summarize_state(payload))
    return 0


def run_send(args: argparse.Namespace) -> int:
    date_key = resolve_date(args.date)
    clean_dir = os.path.join(args.base_dir, "daily", date_key, "clean")
    _ = build_state(clean_dir, date_key, args.prepare_top_n, args.top_n)
    emit_json(list_pending(clean_dir))
    return 0


def run_mark(args: argparse.Namespace, new_status: str) -> int:
    date_key = resolve_date(args.date)
    clean_dir = os.path.join(args.base_dir, "daily", date_key, "clean")
    payload = update_status(clean_dir, args.item_id, new_status, args.error)
    emit_json(payload)
    return 0


def run_list_pending(args: argparse.Namespace) -> int:
    date_key = resolve_date(args.date)
    clean_dir = os.path.join(args.base_dir, "daily", date_key, "clean")
    payload = list_pending(clean_dir)
    emit_json(payload)
    return 0


def run_summary(args: argparse.Namespace) -> int:
    date_key = resolve_date(args.date)
    clean_dir = os.path.join(args.base_dir, "daily", date_key, "clean")
    payload = load_send_state(clean_dir)
    emit_json(summarize_state(payload))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reddit 日报 prepare/send 与发送账本维护")
    parser.add_argument("--base-dir", default=DEFAULT_BASE_DIR, help="项目根目录")
    parser.add_argument("--date", default="today", help="today | yesterday | YYYY-MM-DD")
    parser.add_argument("--prepare-top-n", type=int, default=DEFAULT_PREPARE_TOP_N, help="prepare 阶段保留候选数")
    parser.add_argument("--top-n", type=int, default=DEFAULT_SEND_TOP_N, help="最终正式发送条数")
    parser.add_argument("--prepare", action="store_true", help="生成 send_state 摘要")
    parser.add_argument("--send", action="store_true", help="生成 send_state 并输出待发送最小清单")
    parser.add_argument("--list-pending", action="store_true", help="输出待发送项目最小清单")
    parser.add_argument("--summary", action="store_true", help="输出发送账本摘要")
    parser.add_argument("--mark-sent", dest="item_id", help="把某条标记为 sent")
    parser.add_argument("--mark-failed", dest="failed_item_id", help="把某条标记为 failed")
    parser.add_argument("--error", default="", help="失败原因")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.send:
        raise SystemExit(run_send(args))
    if args.prepare:
        raise SystemExit(run_prepare(args))
    if args.list_pending:
        raise SystemExit(run_list_pending(args))
    if args.summary:
        raise SystemExit(run_summary(args))
    if args.item_id:
        raise SystemExit(run_mark(args, "sent"))
    if args.failed_item_id:
        args.item_id = args.failed_item_id
        raise SystemExit(run_mark(args, "failed"))
    raise SystemExit(run_prepare(args))
