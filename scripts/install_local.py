#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_FEEDS = [
    {"name": "r/openclaw", "url": "https://www.reddit.com/r/openclaw/hot/.rss?limit=30"},
    {"name": "r/OpenClawUseCases", "url": "https://www.reddit.com/r/OpenClawUseCases/hot/.rss?limit=30"},
    {"name": "search/openclaw", "url": "https://www.reddit.com/search.rss?q=openclaw&sort=hot&t=day"},
]


def render_cron_templates(config: Dict[str, Any]) -> Dict[str, Any]:
    base_dir = config["base_dir"]
    timezone = config["timezone"]
    channel = config["delivery"]["channel"]
    target = config["delivery"]["target"]
    collector = config["collector"]
    prepare = config["prepare"]
    report = config["report"]

    return {
        "jobs": [
            {
                "name": "reddit-openclaw-collector-window",
                "schedule": {"kind": "cron", "expr": collector["schedule"], "tz": timezone},
                "sessionTarget": "isolated",
                "payload": {
                    "kind": "agentTurn",
                    "timeoutSeconds": 600,
                    "message": (
                        "执行 Reddit 日报抓取短任务。严格执行："
                        f"1) 运行 `python3 {base_dir}/collector.py --once --stop-hour {collector['stop_hour']} --max-posts-per-round {collector['max_posts_per_round']} --feeds-file {base_dir}/feeds.json`。"
                        "2) 若已过当天发送截止时间，由脚本自身 skip 并正常结束，不要补抓越界内容。"
                        f"3) 只允许写入 `{base_dir}/` 本地文件，不要对用户发送任何中间内容。"
                        "4) 成功只回复 `NO_REPLY`。5) 若失败，只回复一条中文错误摘要。"
                    ),
                },
                "delivery": {"mode": "none"},
            },
            {
                "name": "reddit-openclaw-prepare-window",
                "schedule": {"kind": "cron", "expr": prepare["schedule"], "tz": timezone},
                "sessionTarget": "isolated",
                "payload": {
                    "kind": "agentTurn",
                    "timeoutSeconds": 900,
                    "message": (
                        "执行 Reddit 日报 prepare 窗口任务。严格执行："
                        f"1) 运行 `python3 {base_dir}/final_send.py --base-dir {base_dir} --date today --prepare --prepare-top-n {prepare['prepare_top_n']} --top-n {prepare['top_n']}`。"
                        f"2) 只允许写入 `{base_dir}/daily/<TODAY>/clean/send_state.json` 与 `report_0800.md`。"
                        "3) 不要给用户发送正文。4) 成功只回复 `NO_REPLY`。5) 若失败，只回复一句中文错误摘要。"
                    ),
                },
                "delivery": {"mode": "none"},
            },
            {
                "name": "reddit-openclaw-receipt-before-send",
                "enabled": bool(report.get("receipt_enabled", True)),
                "schedule": {"kind": "cron", "expr": report["receipt_schedule"], "tz": timezone},
                "sessionTarget": "isolated",
                "payload": {
                    "kind": "agentTurn",
                    "timeoutSeconds": 60,
                    "message": (
                        "你是执行回执助手。现在是正式发送前的启动回执时间，请只输出一条中文消息给用户，格式："
                        "`【回执】已开始执行 Reddit 日报发送，当前步骤：读取 send_state 并准备逐条发送。`"
                        " 不要添加任何其它内容。"
                    ),
                },
                "delivery": {"mode": "announce", "channel": channel, "to": target},
            },
            {
                "name": "reddit-openclaw-report-send",
                "schedule": {"kind": "cron", "expr": report["send_schedule"], "tz": timezone},
                "sessionTarget": "isolated",
                "payload": {
                    "kind": "agentTurn",
                    "timeoutSeconds": 900,
                    "message": (
                        f"现在执行 Reddit 最终发送任务。严格执行：1) 运行 `python3 {base_dir}/final_send.py --base-dir {base_dir} --date today --send --prepare-top-n {prepare['prepare_top_n']} --top-n {report['top_n']}`，只使用其返回的最小待发送清单。"
                        f"2) 如果 `ready_for_send` 为 false 或待发送条目少于 {report['top_n']}，则用 `message` 工具只发送一条中文告警：`{report['insufficient_alert']}`，然后只回复 `NO_REPLY`。"
                        "3) 对返回清单中的每个项目，直接把 `message_text` 原样用 `message` 工具发到指定目标；不要二次改写。"
                        f"4) 每条首次发送后等待 1-2 秒；若单条发送失败，仅重试 {report['retry_attempts']} 次。"
                        f"5) 单条最终成功后，立刻运行 `python3 {base_dir}/final_send.py --base-dir {base_dir} --date today --mark-sent <ITEM_ID>`；若最终失败，立刻运行 `python3 {base_dir}/final_send.py --base-dir {base_dir} --date today --mark-failed <ITEM_ID> --error message_failed`，然后继续后续条目。"
                        f"6) 所有条目处理完后，运行 `python3 {base_dir}/final_send.py --base-dir {base_dir} --date today --summary`。7) 如果 `summary.sent` 小于 {report['top_n']}，再发送一条中文告警：`{report['partial_failure_alert']}`，然后只回复 `NO_REPLY`。8) 如果 `summary.sent` 等于 {report['top_n']}`，只回复 `NO_REPLY`。"
                        f"硬规则：所有用户可见消息都发到 channel={channel}, to={target}；最终 assistant 回复必须严格是 `NO_REPLY`。"
                    ),
                },
                "delivery": {"mode": "none"},
            },
        ]
    }


def build_config(args: argparse.Namespace, base_dir: str) -> Dict[str, Any]:
    return {
        "base_dir": base_dir,
        "timezone": args.timezone,
        "delivery": {
            "channel": args.channel,
            "target": args.target or "REPLACE_ME",
        },
        "collector": {
            "schedule": args.collector_schedule,
            "stop_hour": args.stop_hour,
            "interval_minutes": args.interval_minutes,
            "max_posts_per_round": args.max_posts_per_round,
        },
        "prepare": {
            "schedule": args.prepare_schedule,
            "prepare_top_n": args.prepare_top_n,
            "top_n": args.top_n,
        },
        "report": {
            "receipt_enabled": not args.disable_receipt,
            "receipt_schedule": args.receipt_schedule,
            "send_schedule": args.send_schedule,
            "top_n": args.top_n,
            "retry_attempts": args.retry_attempts,
            "insufficient_alert": args.insufficient_alert,
            "partial_failure_alert": args.partial_failure_alert,
        },
        "format": {
            "language": "zh",
            "localized_title": True,
            "keep_original_title_line": True,
            "include_recognition": True,
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
    parser = argparse.ArgumentParser(description="Install the reddit-openclaw-daily skill into a runnable local project")
    parser.add_argument("--base-dir", default="~/reddit-openclaw-daily", help="Where to install the runnable project")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--channel", default="telegram")
    parser.add_argument("--target", default="", help="Message target/chat id; leave empty to fill later")
    parser.add_argument("--collector-schedule", default="0,20,40 6-7 * * *")
    parser.add_argument("--prepare-schedule", default="10,30,50 6-7 * * *")
    parser.add_argument("--receipt-schedule", default="59 7 * * *")
    parser.add_argument("--send-schedule", default="0 8 * * *")
    parser.add_argument("--stop-hour", type=int, default=8)
    parser.add_argument("--interval-minutes", type=float, default=20.0)
    parser.add_argument("--max-posts-per-round", type=int, default=40)
    parser.add_argument("--prepare-top-n", type=int, default=18)
    parser.add_argument("--top-n", type=int, default=9)
    parser.add_argument("--retry-attempts", type=int, default=1)
    parser.add_argument("--disable-receipt", action="store_true")
    parser.add_argument("--insufficient-alert", default="今天的 Reddit 日报候选池不足，无法稳定凑满 9 条，请检查抓取源。")
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
    write_gitignore(base)

    config = build_config(args, base_dir)
    write_json(base / "config.json", config)
    write_json(base / "feeds.json", DEFAULT_FEEDS)
    write_json(base / "cron_templates.json", render_cron_templates(config))

    print(json.dumps({
        "ok": True,
        "base_dir": base_dir,
        "config": str(base / 'config.json'),
        "feeds": str(base / 'feeds.json'),
        "cron_templates": str(base / 'cron_templates.json'),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
