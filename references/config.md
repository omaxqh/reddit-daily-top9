# Configuration Reference

## 生成文件
运行 `scripts/install_local.py` 后，会生成：
- `config.json`
- `topics.json`
- `cron_templates.json`
- `onboard.md`

## config.json
### 顶层字段
- `name`: 固定为 `reddit-daily-top9`
- `base_dir`: 运行目录
- `timezone`: 时区
- `send_time`: 每天发送时间，格式 `HH:MM`

### delivery
- `channel`: 发送通道，如 `telegram`
- `target`: 发送目标

### limits
- `top_n`: 每日报告上限，默认 `9`
- `prepare_top_n`: prepare 阶段保留候选数，默认 `18`
- `per_topic_daily_cap`: 单 topic 每日抓取预算，默认 `100`
- `total_daily_cap`: 总抓取预算，默认 `500`
- `topic_soft_limit`: 建议 topic 数上限，默认 `5`

### collector
- `max_posts_per_round`: 每轮最多处理帖子数，默认 `40`

### report
- `retry_attempts`: 单条发送重试次数
- `insufficient_alert`: 候选不足告警
- `partial_failure_alert`: 部分发送失败告警

### format
- `language`: 默认中文
- `quality_filter`: 默认 `标准偏高`

## topics.json
统一 topic 列表。

当前支持的输入模型：
- `subreddit`
- `search`
- `feed`
- `keyword`
- `post`

推荐字段：
```json
[
  {
    "type": "subreddit",
    "raw_input": "r/openclaw",
    "canonical_url": "https://www.reddit.com/r/openclaw/",
    "normalized_key": "subreddit:r/openclaw",
    "label": "r/openclaw",
    "enabled": true,
    "source": "starter",
    "priority": "normal",
    "daily_cap": 100,
    "added_at": "2026-03-11T00:00:00+08:00"
  }
]
```

## cron_templates.json
调度围绕 `send_time` 自动派生：
- collector window：T-110m 到 T-10m，每 20 分钟
- prepare window：T-105m 到 T-5m，每 20 分钟
- final send：T
- 默认不生成 receipt 任务

## onboard.md
首装欢迎与当前任务展示统一使用 `onboard.md`。
默认显示：
- 当前 topics
- 发送时间
- 时区
- 发送位置
- 每日报告上限
- 输出语言
- 质量过滤
- 可直接对话修改示例
