#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

from topic_resolver import DEFAULT_DAILY_CAP, DEFAULT_TOPICS, normalize_topic

PRODUCT_NAME = "reddit-daily-top9"


def parse_time_hhmm(text: str) -> tuple[int, int]:
    hour_text, minute_text = text.strip().split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"invalid HH:MM time: {text}")
    return hour, minute


def derive_cron_expr(send_time: str, offsets_minutes: List[int]) -> str:
    send_hour, send_minute = parse_time_hhmm(send_time)
    anchor = datetime(2000, 1, 2, send_hour, send_minute)
    grouped: dict[int, list[int]] = defaultdict(list)
    for offset in offsets_minutes:
        dt = anchor - timedelta(minutes=offset)
        grouped[dt.hour].append(dt.minute)

    hours = sorted(grouped)
    minute_sets = {tuple(sorted(set(grouped[hour]))) for hour in hours}
    if len(minute_sets) != 1:
        parts = []
        for hour in hours:
            minutes = ",".join(str(m) for m in sorted(set(grouped[hour])))
            parts.append(f"{minutes} {hour} * * *")
        raise ValueError(f"cannot compress cron windows into one expr: {parts}")

    minutes = ",".join(str(m) for m in sorted(next(iter(minute_sets))))
    hours_expr = f"{hours[0]}-{hours[-1]}" if hours == list(range(hours[0], hours[-1] + 1)) else ",".join(str(h) for h in hours)
    return f"{minutes} {hours_expr} * * *"


def render_onboard(config: Dict[str, Any], topics: List[Dict[str, Any]]) -> str:
    topic_lines = "\n".join(f"- {topic.get('label', topic.get('raw_input', ''))}" for topic in topics if topic.get("enabled", True)) or "- （暂无）"
    send_time = config["send_time"]
    timezone = config["timezone"]
    channel = config["delivery"]["channel"]
    target = config["delivery"]["target"]
    top_n = config["limits"]["top_n"]
    language = config["format"]["language"]
    quality = config["format"]["quality_filter"]
    return (
        f"reddit-daily-top9 当前任务\n\n"
        f"- 当前 topics：\n{topic_lines}\n"
        f"- 发送时间：{send_time}\n"
        f"- 时区：{timezone}\n"
        f"- 发送位置：{channel}:{target}\n"
        f"- 每日报告上限：{top_n}\n"
        f"- 输出语言：{language}\n"
        f"- 质量过滤：{quality}\n\n"
        "你可以直接对话修改：\n"
        "- 加这个 URL\n"
        "- 再加 btc 和 nvda\n"
        "- 删掉 r/openclaw\n"
        "- 改成晚上 9 点发\n"
        "- 先给我试跑预览\n"
    )


def render_cron_templates(config: Dict[str, Any]) -> Dict[str, Any]:
    base_dir = config["base_dir"]
    timezone = config["timezone"]
    send_time = config["send_time"]
    channel = config["delivery"]["channel"]
    target = config["delivery"]["target"]
    collector = config["collector"]
    limits = config["limits"]

    collector_schedule = derive_cron_expr(send_time, [110, 90, 70, 50, 30, 10])
    prepare_schedule = derive_cron_expr(send_time, [105, 85, 65, 45, 25, 5])
    send_hour, send_minute = parse_time_hhmm(send_time)
    send_schedule = f"{send_minute} {send_hour} * * *"

    return {
        "jobs": [
            {
                "name": "reddit-daily-top9-collector-window",
                "schedule": {"kind": "cron", "expr": collector_schedule, "tz": timezone},
                "sessionTarget": "isolated",
                "payload": {
                    "kind": "agentTurn",
                    "timeoutSeconds": 600,
                    "message": (
                        "执行 reddit-daily-top9 抓取短任务。严格执行："
                        f"1) 运行 `python3 {base_dir}/collector.py --base-dir {base_dir} --topics-file {base_dir}/topics.json --once --stop-at {send_time} --max-posts-per-round {collector['max_posts_per_round']}`。"
                        "2) 若已过当天发送截止时间，由脚本自身 skip 并正常结束，不要补抓越界内容。"
                        f"3) 只允许写入 `{base_dir}/` 本地文件，不要对用户发送任何中间内容。"
                        "4) 成功只回复 `NO_REPLY`。5) 若失败，只回复一条中文错误摘要。"
                    ),
                },
                "delivery": {"mode": "none"},
            },
            {
                "name": "reddit-daily-top9-prepare-window",
                "schedule": {"kind": "cron", "expr": prepare_schedule, "tz": timezone},
                "sessionTarget": "isolated",
                "payload": {
                    "kind": "agentTurn",
                    "timeoutSeconds": 900,
                    "message": (
                        "执行 reddit-daily-top9 prepare 窗口任务。严格执行："
                        f"1) 运行 `python3 {base_dir}/final_send.py --base-dir {base_dir} --date today --prepare --prepare-top-n {limits['prepare_top_n']} --top-n {limits['top_n']}`。"
                        f"2) 只允许写入 `{base_dir}/daily/<TODAY>/clean/send_state.json` 与 `report_backup.md`。"
                        "3) 不要给用户发送正文。4) 成功只回复 `NO_REPLY`。5) 若失败，只回复一句中文错误摘要。"
                    ),
                },
                "delivery": {"mode": "none"},
            },
            {
                "name": "reddit-daily-top9-report-send",
                "schedule": {"kind": "cron", "expr": send_schedule, "tz": timezone},
                "sessionTarget": "isolated",
                "payload": {
                    "kind": "agentTurn",
                    "timeoutSeconds": 900,
                    "message": (
                        f"现在执行 reddit-daily-top9 最终发送任务。严格执行：1) 运行 `python3 {base_dir}/final_send.py --base-dir {base_dir} --date today --send --prepare-top-n {limits['prepare_top_n']} --top-n {limits['top_n']}`，只使用其返回的最小待发送清单。"
                        f"2) 如果 `ready_for_send` 为 false 或待发送条目少于 {limits['top_n']}，则用 `message` 工具只发送一条中文告警：`{config['report']['insufficient_alert']}`，然后只回复 `NO_REPLY`。"
                        "3) 对返回清单中的每个项目，直接把 `message_text` 原样用 `message` 工具发到指定目标；不要二次改写。"
                        f"4) 每条首次发送后等待 1-2 秒；若单条发送失败，仅重试 {config['report']['retry_attempts']} 次。"
                        f"5) 单条最终成功后，立刻运行 `python3 {base_dir}/final_send.py --base-dir {base_dir} --date today --mark-sent <ITEM_ID>`；若最终失败，立刻运行 `python3 {base_dir}/final_send.py --base-dir {base_dir} --date today --mark-failed <ITEM_ID> --error message_failed`，然后继续后续条目。"
                        f"6) 所有条目处理完后，运行 `python3 {base_dir}/final_send.py --base-dir {base_dir} --date today --summary`。7) 如果 `summary.sent` 小于 {limits['top_n']}，再发送一条中文告警：`{config['report']['partial_failure_alert']}`，然后只回复 `NO_REPLY`。8) 如果 `summary.sent` 等于 {limits['top_n']}`，只回复 `NO_REPLY`。"
                        f"硬规则：所有用户可见消息都发到 channel={channel}, to={target}；最终 assistant 回复必须严格是 `NO_REPLY`。"
                    ),
                },
                "delivery": {"mode": "none"},
            },
        ]
    }


def build_topics(args: argparse.Namespace) -> List[Dict[str, Any]]:
    starter = dict(DEFAULT_TOPICS[0])
    starter["daily_cap"] = args.per_topic_daily_cap or DEFAULT_DAILY_CAP
    starter["added_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    topic = normalize_topic(starter)
    return [topic] if topic else []


def build_config(args: argparse.Namespace, base_dir: str) -> Dict[str, Any]:
    return {
        "name": PRODUCT_NAME,
        "base_dir": base_dir,
        "timezone": args.timezone,
        "send_time": args.send_time,
        "delivery": {
            "channel": args.channel,
            "target": args.target or "REPLACE_ME",
        },
        "limits": {
            "top_n": args.top_n,
            "prepare_top_n": args.prepare_top_n,
            "per_topic_daily_cap": args.per_topic_daily_cap,
            "total_daily_cap": args.total_daily_cap,
            "topic_soft_limit": args.topic_soft_limit,
        },
        "collector": {
            "interval_minutes": 20,
            "max_posts_per_round": args.max_posts_per_round,
        },
        "report": {
            "retry_attempts": args.retry_attempts,
            "insufficient_alert": args.insufficient_alert,
            "partial_failure_alert": args.partial_failure_alert,
        },
        "format": {
            "language": "中文",
            "quality_filter": "标准偏高",
        },
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def copy_script(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    dst.chmod(0o755)


def write_gitignore(base_dir: Path) -> None:
    path = base_dir / ".gitignore"
    if path.exists():
        return
    path.write_text("__pycache__/\ndaily/\nstate/\n*.tmp\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install the reddit-daily-top9 skill into a runnable local project")
    parser.add_argument("--base-dir", default="~/reddit_daily_top9", help="Where to install the runnable project")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--send-time", default="08:00")
    parser.add_argument("--channel", default="telegram")
    parser.add_argument("--target", default="", help="Message target/chat id; leave empty to fill later")
    parser.add_argument("--max-posts-per-round", type=int, default=40)
    parser.add_argument("--prepare-top-n", type=int, default=18)
    parser.add_argument("--top-n", type=int, default=9)
    parser.add_argument("--retry-attempts", type=int, default=1)
    parser.add_argument("--per-topic-daily-cap", type=int, default=100)
    parser.add_argument("--total-daily-cap", type=int, default=500)
    parser.add_argument("--topic-soft-limit", type=int, default=5)
    parser.add_argument("--insufficient-alert", default="今天的 Reddit 日报候选池不足，无法稳定凑满 9 条，请检查 topics 或抓取源。")
    parser.add_argument("--partial-failure-alert", default="今天的 Reddit 日报有部分卡片发送失败，请检查消息通道或发送账本。")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = os.path.abspath(os.path.expanduser(args.base_dir))
    base = Path(base_dir)
    skill_dir = Path(__file__).resolve().parent

    if base.exists() and any(base.iterdir()) and not args.force:
        raise SystemExit(f"target not empty: {base_dir} (use --force if you want to overwrite files)")

    base.mkdir(parents=True, exist_ok=True)
    copy_script(skill_dir / "collector.py", base / "collector.py")
    copy_script(skill_dir / "final_send.py", base / "final_send.py")
    copy_script(skill_dir / "topic_resolver.py", base / "topic_resolver.py")
    write_gitignore(base)

    config = build_config(args, base_dir)
    topics = build_topics(args)
    cron_templates = render_cron_templates(config)
    onboard = render_onboard(config, topics)

    write_json(base / "config.json", config)
    write_json(base / "topics.json", topics)
    write_json(base / "cron_templates.json", cron_templates)
    (base / "onboard.md").write_text(onboard, encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "base_dir": base_dir,
        "config": str(base / "config.json"),
        "topics": str(base / "topics.json"),
        "cron_templates": str(base / "cron_templates.json"),
        "onboard": str(base / "onboard.md"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
