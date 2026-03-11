# reddit-daily-top9 skill

这是一个可复用的 Reddit 日报 skill。

目标很简单：
**通过自然语言持续添加 Reddit 关注目标和话题，自动归一化输入，筛选出每天最值得看的 9 条内容，并按设定时间发送。**

## 当前结构
- `SKILL.md`
- `scripts/collector.py`
- `scripts/final_send.py`
- `scripts/topic_resolver.py`
- `scripts/install_local.py`
- `references/config.md`

## 安装后会生成
- `collector.py`
- `final_send.py`
- `topic_resolver.py`
- `config.json`
- `topics.json`
- `cron_templates.json`
- `onboard.md`
- `.gitignore`

## 这版已经完成的升级
- 运行目录迁到 `reddit_daily_top9`
- skill 名迁到 `reddit-daily-top9`
- `feeds.json` 升级为 `topics.json`
- `collector.py` 改为 `topics -> feed candidates`
- `final_send.py` 去掉 OpenClaw 专属打分和文案残留
- 调度改为围绕 `send_time` 派生
- receipt 默认移除
- 默认 starter topic 收缩为 `r/openclaw`

## 快速开始
```bash
python3 /root/skills/reddit-daily-top9/scripts/install_local.py \
  --base-dir ~/reddit_daily_top9 \
  --channel telegram \
  --target 123456789 \
  --send-time 08:00
```

## 默认输出
- 每日报告最多 9 条
- 中文输出
- 中文标题优先
- `原标题：...` 单独一行
- 保留认可度说明

## 已知限制
- 当前支持 `subreddit / search / feed / keyword / post URL` 五类 topic 输入
- `report_0800.md` 仍作为备份文件名保留

一句话：
**这不是把旧 OpenClaw 日报换个壳，而是把现有稳定主链升级成一个通用的 Reddit Top 9 skill。**
