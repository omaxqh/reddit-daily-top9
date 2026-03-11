# Configuration Reference

## Generated files

After running `scripts/install_local.py`, the target project contains:

- `config.json`: runtime configuration
- `feeds.json`: RSS feed list
- `cron_templates.json`: OpenClaw cron job scaffolding

## config.json

### delivery
- `channel`: messaging channel, for example `telegram`
- `target`: destination chat/user/thread identifier. This must be filled per instance.

### collector
- `schedule`: cron expression for collection rounds
- `stop_hour`: hard stop hour for the collection window
- `interval_minutes`: loop interval when running in non-`--once` mode
- `max_posts_per_round`: per-round cap

### prepare
- `schedule`: cron expression for prepare rounds
- `prepare_top_n`: candidate pool size before final cut
- `top_n`: final count used during prepare/send

### report
- `receipt_enabled`: whether to send a short pre-send receipt
- `receipt_schedule`: cron expression for the receipt
- `send_schedule`: cron expression for the final send job
- `top_n`: required send count
- `retry_attempts`: retries per failed message
- `insufficient_alert`: alert text when not enough cards are ready
- `partial_failure_alert`: alert text when some cards fail to send

### format
Opinionated defaults are intentionally preserved:
- `language=zh`
- localized Chinese title first
- `原标题：...` on its own line
- recognition line included

## feeds.json

List of objects:

```json
[
  {"name": "r/openclaw", "url": "https://www.reddit.com/r/openclaw/hot/.rss?limit=30"},
  {"name": "r/OpenClawUseCases", "url": "https://www.reddit.com/r/OpenClawUseCases/hot/.rss?limit=30"},
  {"name": "search/openclaw", "url": "https://www.reddit.com/search.rss?q=openclaw&sort=hot&t=day"}
]
```

## cron_templates.json

The generated file is a convenience artifact. It contains job specs for:

- `reddit-openclaw-collector-window`
- `reddit-openclaw-prepare-window`
- `reddit-openclaw-receipt-before-send`
- `reddit-openclaw-report-send`

Use those payloads with the OpenClaw `cron` tool. Review the `delivery.target` field before creating jobs.

## Safe customization boundary

Keep these configurable per instance:
- chat/user/thread target
- timezone
- cron expressions
- base install directory
- alert text
- feed list

Keep these as the default product behavior unless the user says otherwise:
- Chinese card output
- title localization behavior
- original title line
- recognition summary line
