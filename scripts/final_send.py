#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

TZ = timezone(timedelta(hours=8))
TMP_SUFFIX = ".tmp"
DEFAULT_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

EXCERPT_CHARS = 800
COMMENT_MAX_COUNT = 5
COMMENT_MAX_CHARS = 200
CORE_VIEW_LIMIT = 500
MIN_CORE_VIEW_CHARS = 250
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
    """机械打分（保底），用于无模型排序时的 fallback"""
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


def ai_rank_candidates(candidates: List[Dict[str, Any]], top_n: int) -> List[Dict[str, Any]]:
    """
    模型通读全局后排序（旧版逻辑）：
    1. 把全部候选的标题 + 正文摘要 + 评论数送入模型
    2. 让模型返回排序后的 ID 列表
    3. 按模型排序结果重排
    """
    if not candidates:
        return []
    
    # 构建输入：每条的标题 + 正文前 200 字 + 评论数
    input_items = []
    for c in candidates:
        input_items.append({
            "id": c.get("id", ""),
            "title": c.get("title", ""),
            "body_excerpt": c.get("body_excerpt", "")[:200],
            "comment_count": c.get("comment_count", 0),
            "fetch_status": c.get("fetch_status", "unknown"),
        })
    
    prompt = (
        "你是 Reddit 内容编辑。请通读以下全部帖子，按信息价值从高到低排序。\n"
        "排序标准：\n"
        "1. 有实操细节、可复用方法的优先\n"
        "2. 有排他信息、反例、深度分析的优先\n"
        "3. 评论密度高且讨论质量高的优先\n"
        "4. 避开纯营销、抱怨、重复内容\n"
        f"输入（共{len(input_items)}条）：\n{json.dumps(input_items, ensure_ascii=False, indent=2)}\n\n"
        "只输出排序后的 ID 列表（JSON 数组），如：[\"id1\",\"id2\",...]。不要解释。"
    )
    
    try:
        import subprocess
        cmd = [
            "openclaw", "agent", "--to", "telegram:reddit-daily-top9-rank",
            "--timeout", "120", "--verbose", "off", "--json", "--message", prompt,
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=150, check=False)
        if completed.returncode != 0:
            raise RuntimeError("ai rank failed")
        
        result = json.loads(completed.stdout)
        payloads = (((result or {}).get("result") or {}).get("payloads") or [])
        if not payloads:
            raise ValueError("no payloads")
        
        # 提取 JSON 数组
        text = payloads[0].get("text", "")
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            raise ValueError("no json array")
        
        ranked_ids = json.loads(text[start:end+1])
        
        # 按模型排序重排
        id_to_item = {c.get("id"): c for c in candidates}
        ranked = []
        for rid in ranked_ids:
            if rid in id_to_item:
                ranked.append(id_to_item[rid])
        
        # 补齐遗漏（模型可能漏掉一些）
        ranked_ids_set = set(ranked_ids)
        for c in candidates:
            if c.get("id") not in ranked_ids_set:
                ranked.append(c)
        
        return ranked[:top_n]
    except Exception:
        # fallback 到机械排序
        return sorted(candidates, key=lambda x: candidate_score(x), reverse=True)[:top_n]


def dedupe_candidates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best_by_title: Dict[str, Dict[str, Any]] = {}
    for item in items:
        key = normalize_title(item.get("title", "")) or item.get("id", "")
        existing = best_by_title.get(key)
        if existing is None or float(item.get("candidate_score", 0)) > float(existing.get("candidate_score", 0)):
            best_by_title[key] = item
    return list(best_by_title.values())


def load_ranked_candidates(clean_dir: str, use_ai_rank: bool = True) -> List[Dict[str, Any]]:
    """
    加载候选并排序。
    use_ai_rank=True 时，调用模型通读全局后排序（旧版逻辑）。
    use_ai_rank=False 时，用机械打分排序。
    """
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
    
    if use_ai_rank and len(items) >= 3:
        # 模型通读全局后排序
        return ai_rank_candidates(items, top_n=len(items))
    else:
        # 机械排序
        items.sort(key=lambda item: (float(item.get("candidate_score", 0)), item.get("captured_at", ""), item.get("id", "")), reverse=True)
        return items


def fallback_display_title(candidate: Dict[str, Any]) -> str:
    title = compact_text(candidate.get("title", ""))
    if contains_chinese(title):
        return trim_text(title, 28)
    if title:
        return f"Reddit话题：{trim_text(title, 24)}"
    return "Reddit话题：无标题"


def display_title(candidate: Dict[str, Any], index: int) -> str:
    title = fallback_display_title(candidate)
    if title == "Reddit话题：无标题":
        return f"Reddit候选#{index}"
    return title


def recognition(candidate: Dict[str, Any]) -> Dict[str, str]:
    comment_count = int(candidate.get("comment_count", 0) or 0)
    status = candidate.get("fetch_status", "unknown")
    if comment_count >= 10:
        level = "高"
        pro = "评论密度高，能看到赞成与反对两边的真实理由。"
        con = "高热度不等于高质量，情绪噪声也会同步上来。"
    elif comment_count >= 3:
        level = "中"
        pro = "有一些补充信息，足够辅助判断帖子真实价值。"
        con = "讨论样本还不够厚，结论需要留余地。"
    else:
        level = "低"
        pro = "至少保留了正文线索。"
        con = "评论样本偏薄，难以判断讨论质量。"
    if status != "done":
        level = "低"
        con = "抓取受限，信息完整性不足。"
    return {"level": level, "pro": pro, "con": con}


def extract_json_from_text(text: str) -> Tuple[bool, Any]:
    text = text or ""
    candidates: List[str] = []
    fence_hits = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    candidates.extend(fence_hits)
    candidates.append(text)

    for candidate in candidates:
        snippet = candidate.strip()
        if not snippet:
            continue
        try:
            return True, json.loads(snippet)
        except Exception:
            pass

        for left, right in (("[", "]"), ("{", "}")):
            start = snippet.find(left)
            end = snippet.rfind(right)
            if start == -1 or end == -1 or end <= start:
                continue
            try:
                return True, json.loads(snippet[start : end + 1])
            except Exception:
                continue

    return False, None


def trim_complete_text(text: str, limit: int, min_boundary: int = 72) -> str:
    text = compact_text(text)
    if len(text) <= limit:
        return text

    primary_marks = "。！？；"
    secondary_marks = "，："

    primary_cut = max((idx for idx, ch in enumerate(text[: limit + 1]) if ch in primary_marks), default=-1)
    if primary_cut >= min_boundary - 1:
        return text[: primary_cut + 1].strip()

    secondary_cut = max((idx for idx, ch in enumerate(text[: limit + 1]) if ch in secondary_marks), default=-1)
    if secondary_cut >= min_boundary - 1:
        trimmed = text[:secondary_cut].rstrip("，：、； ")
        if trimmed:
            return trimmed + "。"

    trimmed = text[:limit].rstrip("，：、；,.!！？ ")
    return trimmed + "。"


def request_publish_map(prompt: str, valid_ids: set[str]) -> Dict[str, Dict[str, Any]]:
    cmd = [
        "openclaw",
        "agent",
        "--agent",
        "main",
        "--timeout",
        "180",
        "--verbose",
        "off",
        "--json",
        "--message",
        prompt,
    ]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=220, check=False)
        if completed.returncode != 0:
            return {}
        result = json.loads(completed.stdout)
        payloads = (((result or {}).get("result") or {}).get("payloads") or [])
        if not payloads:
            return {}
        ok, parsed = extract_json_from_text(payloads[0].get("text", ""))
        if not ok:
            return {}

        items = parsed
        if isinstance(parsed, dict):
            maybe = parsed.get("items")
            if isinstance(maybe, list):
                items = maybe

        if not isinstance(items, list):
            return {}

        output: Dict[str, Dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id", "")).strip()
            if not item_id or item_id not in valid_ids:
                continue
            output[item_id] = item
        return output
    except Exception:
        return {}


def build_publish_prompt(source_items: List[Dict[str, Any]]) -> str:
    return (
        "你是中文科技编辑。请把下面的 Reddit 候选条目改写成可直接发送的中文卡片素材。\n"
        "要求：\n"
        "1) title 必须是中文标题，简洁、像新闻标题，不要保留英文整句。\n"
        "2) core_viewpoint 必须是 250-300 个中文字符，允许 3-5 句，写清做了什么、怎么做、解决什么、代价/限制；必须是完整句子，禁止半句收尾。\n"
        "3) core_viewpoint 至少包含 2 个具体细节，优先保留流程、组件、评论补充、反例、部署门槛或约束条件。\n"
        "4) 不要写模板腔，避免“问题很真实”“价值在于”“限制是内容更”这类空话；优先写具体事实。\n"
        "5) key_comments 选 2-4 条，必须是中文；优先排他信息、实操细节、反例。\n"
        "6) recognition_level 只能是 高/中/低。\n"
        "7) recognition_pro / recognition_con 各一句，具体不空泛。\n"
        "8) 严禁编造，不确定就保守描述。\n"
        "输出：只输出 JSON 数组，不要任何解释。每项结构：\n"
        "{id,title,core_viewpoint,key_comments,recognition_level,recognition_pro,recognition_con}\n\n"
        f"输入：{json.dumps(source_items, ensure_ascii=False)}"
    )


def build_rewrite_prompt(source_items: List[Dict[str, Any]]) -> str:
    return (
        "你是中文科技编辑。下面这些条目的第一版 core_viewpoint 太短，没有达到要求。请只重写这些条目。\n"
        "硬性要求：\n"
        f"1) core_viewpoint 必须至少 {MIN_CORE_VIEW_CHARS} 个中文字符，目标 250-320 字，允许 4-5 句。\n"
        "2) 必须补足细节，至少写出两个具体事实，例如流程步骤、调用方式、评论反例、部署门槛、争议点。\n"
        "3) 必须完整成段，不能偷懒写成一句长句，也不能回避限制条件。\n"
        "4) title、key_comments、recognition_* 也一并输出，保持可直接发送。\n"
        "5) 严禁编造，不确定就用保守表述。\n"
        "输出：只输出 JSON 数组，不要任何解释。每项结构：\n"
        "{id,title,core_viewpoint,key_comments,recognition_level,recognition_pro,recognition_con}\n\n"
        f"输入：{json.dumps(source_items, ensure_ascii=False)}"
    )


def ai_build_publish_map(sources: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not sources:
        return {}

    source_items = []
    source_by_id: Dict[str, Dict[str, Any]] = {}
    for src in sources:
        item = {
            "id": src.get("id", ""),
            "original_title": src.get("original_title", src.get("title", "")),
            "body_excerpt": src.get("body_excerpt", ""),
            "key_comments": src.get("key_comments", []) or [],
            "comment_count": src.get("comment_count", 0),
            "fetch_status": src.get("fetch_status", "unknown"),
            "fail_reason": src.get("fail_reason", ""),
            "url": src.get("url", ""),
        }
        source_items.append(item)
        source_by_id[item["id"]] = item

    valid_ids = {src.get("id", "") for src in sources}
    output = request_publish_map(build_publish_prompt(source_items), valid_ids)

    short_items = []
    for item_id, source_item in source_by_id.items():
        draft = output.get(item_id, {})
        core = compact_text(draft.get("core_viewpoint", ""))
        if len(core) >= MIN_CORE_VIEW_CHARS:
            continue
        short_items.append(
            {
                **source_item,
                "draft_title": draft.get("title", ""),
                "draft_core_viewpoint": core,
                "draft_key_comments": draft.get("key_comments", []) or [],
                "draft_recognition_level": draft.get("recognition_level", ""),
                "draft_recognition_pro": draft.get("recognition_pro", ""),
                "draft_recognition_con": draft.get("recognition_con", ""),
            }
        )

    if short_items:
        rewrite_map = request_publish_map(build_rewrite_prompt(short_items), {item["id"] for item in short_items})
        for item_id, item in rewrite_map.items():
            output[item_id] = item

    return output


def normalize_publish(source: Dict[str, Any], publish_raw: Dict[str, Any]) -> Dict[str, Any]:
    vote_default = recognition(source)
    raw = publish_raw if isinstance(publish_raw, dict) else {}

    title = compact_text(raw.get("title", ""))
    if not title or not contains_chinese(title):
        title = fallback_display_title(source)

    core = compact_text(raw.get("core_viewpoint", ""))
    if not core:
        excerpt = compact_text(source.get("body_excerpt", ""))
        if excerpt:
            core = excerpt
        else:
            core = "正文抓取受限，本条基于可得元信息保底输出。"

    status = source.get("fetch_status", "unknown")
    if status == "failed":
        core = "正文与评论均受限，本条仅保留来源线索。"
    elif status == "partial":
        core = trim_complete_text(f"{core} 评论抓取不完整。", CORE_VIEW_LIMIT)
    else:
        core = trim_complete_text(core, CORE_VIEW_LIMIT)

    raw_comments = raw.get("key_comments", [])
    comments: List[str] = []
    if isinstance(raw_comments, list):
        comments = choose_comments([str(comment) for comment in raw_comments], max_count=COMMENT_MAX_COUNT)
    if not comments:
        comments = choose_comments(source.get("key_comments", []) or [], max_count=COMMENT_MAX_COUNT)

    level = compact_text(raw.get("recognition_level", ""))
    if level not in {"高", "中", "低"}:
        level = vote_default["level"]

    pro = compact_text(raw.get("recognition_pro", "")) or vote_default["pro"]
    con = compact_text(raw.get("recognition_con", "")) or vote_default["con"]

    return {
        "title": title,
        "core_viewpoint": core,
        "key_comments": comments,
        "recognition_level": level,
        "recognition_pro": pro,
        "recognition_con": con,
    }


def build_message_text(source: Dict[str, Any], publish: Dict[str, Any], index: int) -> str:
    title = compact_text(publish.get("title", "")) or display_title(source, index)
    original_title = compact_text(source.get("original_title", source.get("title", "")))
    status = source.get("fetch_status", "unknown")
    fail_reason = compact_text(source.get("fail_reason", ""))

    lines = [f"{index}. {title}"]
    if original_title and original_title != title:
        lines.append(f"原标题：{trim_text(original_title, 90)}")

    core = compact_text(publish.get("core_viewpoint", ""))
    if not core:
        core = "正文抓取受限，本条基于可得元信息保底输出。"
    lines.append(f"核心观点：{core}")

    comments = publish.get("key_comments", []) or []
    if comments:
        lines.append("关键评论：")
        for comment in comments[:COMMENT_MAX_COUNT]:
            lines.append(f"- {compact_text(comment)}")

    level = compact_text(publish.get("recognition_level", "")) or recognition(source)["level"]
    pro = compact_text(publish.get("recognition_pro", "")) or recognition(source)["pro"]
    con = compact_text(publish.get("recognition_con", "")) or recognition(source)["con"]
    lines.append(f"认可度：{level}；赞成理由：{pro}；反对理由：{con}")

    if status != "done":
        lines.append(f"抓取状态：{status}（{fail_reason or '受限'}）")
    lines.append(source.get("url", ""))
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

    atomic_write_text(os.path.join(clean_dir, "report_backup.md"), "\n".join(lines).strip() + "\n")


def build_state(clean_dir: str, date_key: str, prepare_top_n: int, top_n: int) -> Dict[str, Any]:
    ranked = load_ranked_candidates(clean_dir, use_ai_rank=True)
    candidates = ranked[:prepare_top_n]
    selected_sources = candidates[:top_n]
    selected = []

    # 统计抓取状态（用于汇总头）
    done_count = sum(1 for c in ranked if c.get("fetch_status") == "done")
    partial_count = sum(1 for c in ranked if c.get("fetch_status") == "partial")
    failed_count = sum(1 for c in ranked if c.get("fetch_status") == "failed")

    # 生成汇总头（旧版格式必须有）
    header_lines = [
        f"OpenClaw Reddit 中文日报 [{date_key} 08:00]",
        "",
        f"- 抓取总数：{len(ranked)}（done={done_count} / partial={partial_count} / failed={failed_count}）",
        f"- 精选条数：{min(len(selected_sources), top_n)}",
    ]
    if failed_count == 0 and partial_count == 0:
        header_lines.append("- 受限说明：今日抓取正常，精选条目均为 done 状态。")
    elif failed_count == 0:
        header_lines.append(f"- 受限说明：今日 {partial_count} 条评论抓取不完整，精选条目已避开受限内容。")
    else:
        header_lines.append(f"- 受限说明：今日 {failed_count} 条抓取失败，{partial_count} 条不完整，精选条目已优先选择 done 状态。")
    header_lines.append("")
    header_lines.append("---")
    header_text = "\n".join(header_lines)

    ai_publish_map = ai_build_publish_map(selected_sources)

    for index, source in enumerate(selected_sources, start=1):
        publish = normalize_publish(source, ai_publish_map.get(source.get("id", ""), {}))
        message_text = build_message_text(source, publish, index)

        # 第一条消息前加上汇总头
        if index == 1:
            message_text = header_text + "\n\n" + message_text

        selected.append(
            {
                "id": source.get("id", f"item-{index}"),
                "index": index,
                "source": source,
                "publish": publish,
                "candidate_hash": candidate_hash(source),
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
    parser.add_argument("--send", action="store_true", help="读取 send_state 并输出待发送最小清单")
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
