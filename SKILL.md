---
name: reddit-openclaw-daily
description: Build, install, configure, and maintain a daily Reddit watcher for OpenClaw-related topics. Use when the user wants to deploy a reusable Reddit monitoring/report pipeline, install it on another OpenClaw instance, generate local project files plus cron templates, tune feed sources/schedules/targets, or package/publish the watcher as a shareable skill.
---

# reddit-openclaw-daily

Install and operate a reusable Reddit daily watcher focused on OpenClaw topics.

## Core workflow

1. Install the runnable project with `scripts/install_local.py`.
2. Review or edit `config.json`, `feeds.json`, and `cron_templates.json` in the target project directory.
3. Use the generated cron templates to create scheduled jobs with the OpenClaw `cron` tool.
4. Run a one-shot collector round and a prepare/send dry run before declaring success.
5. Package the skill when the user asks for a distributable `.skill` file.

## What this skill contains

### scripts/install_local.py
Create a runnable local project from the bundled scripts.

Example:

```bash
python3 /root/skills/reddit-openclaw-daily/scripts/install_local.py \
  --base-dir ~/reddit-openclaw-daily \
  --channel telegram \
  --target 123456789
```

This writes:

- `collector.py`
- `final_send.py`
- `config.json`
- `feeds.json`
- `cron_templates.json`
- `.gitignore`

### scripts/collector.py
Collect Reddit RSS posts and comment RSS into a daily local dataset.

Use `--feeds-file <path>` to override the default feed list.

Example:

```bash
python3 ~/reddit-openclaw-daily/collector.py --once --feeds-file ~/reddit-openclaw-daily/feeds.json
```

### scripts/final_send.py
Build the candidate set, maintain `send_state.json`, and manage mark-sent / mark-failed acknowledgements.

Examples:

```bash
python3 ~/reddit-openclaw-daily/final_send.py --base-dir ~/reddit-openclaw-daily --date today --prepare
python3 ~/reddit-openclaw-daily/final_send.py --base-dir ~/reddit-openclaw-daily --date today --send
```

## Installation notes

- Keep target chat IDs, cron schedules, and deployment paths out of the skill source. Put them in `config.json` and `cron_templates.json`.
- Preserve the default Chinese card style unless the user explicitly wants a different report format.
- Treat `cron_templates.json` as generated scaffolding. Use it to create real jobs with the `cron` tool; do not assume jobs already exist.

## Validation checklist

After installing on a new instance:

1. Run `collector.py --once`.
2. Confirm `daily/YYYY-MM-DD/clean/report_source.json` exists.
3. Run `final_send.py --prepare`.
4. Run `final_send.py --send`.
5. Confirm the returned payload has `ready_for_send` and a non-empty `items` list when enough sources exist.

## Packaging

When the user asks for a distributable package, run:

```bash
python3 /usr/lib/node_modules/openclaw/skills/skill-creator/scripts/package_skill.py /root/skills/reddit-openclaw-daily
```

## References

- Read `references/config.md` when you need field-level guidance for `config.json`, `feeds.json`, or generated cron templates.
