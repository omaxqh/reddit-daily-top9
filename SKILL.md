---
name: reddit-daily-top9
description: Build, install, configure, and maintain a reusable Reddit daily Top 9 watcher. Use when the user wants to deploy a natural-language configurable Reddit report pipeline, install it on another OpenClaw instance, tune topics/schedule/target, or package it as a shareable skill.
---

# reddit-daily-top9

一个可复用的 Reddit 日报 skill。

## 核心能力
- 通过 `topics.json` 管理关注目标
- 把 `topics` 解析成 feed candidates
- 抓取 Reddit 内容并落盘
- 本地排序、去重、筛选出每天最多 9 条
- 通过 cron 模板按 `send_time` 派生调度

## 安装流程
1. 运行 `scripts/install_local.py`
2. 检查生成的 `config.json`、`topics.json`、`cron_templates.json`、`onboard.md`
3. 用 `cron` 工具把模板真正创建成任务
4. 手动跑一轮 `collector.py` 和 `final_send.py --prepare/--send` 验证

## 生成文件
- `collector.py`
- `final_send.py`
- `topic_resolver.py`
- `config.json`
- `topics.json`
- `cron_templates.json`
- `onboard.md`
- `.gitignore`

## 设计边界
- 不再使用 `feeds.json`
- 默认不生成 receipt 任务
- 默认 starter topic 仅保留 `r/openclaw`
- 当前 `post` topic 已通过单帖 RSS 接入现有抓取主链

## 参考
- 配置说明见：`references/config.md`
