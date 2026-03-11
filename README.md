# reddit-openclaw-daily-skill

一个可复用的 OpenClaw Skill，用来构建 **Reddit 日报监控与发送链路**。

它把现有的 Reddit 日报能力抽成了可分发的 skill 与本地项目模板，目标不是把你的个人实例原样拷走，而是把**能力内核**保留下来，把**目标 chat、cron、路径、发送策略**改成可配置。

## 这是什么

这个仓库提供两层东西：

1. **Skill 本体**
   - `SKILL.md`
   - `scripts/collector.py`
   - `scripts/final_send.py`
   - `scripts/install_local.py`
   - `references/config.md`

2. **可分发安装包**
   - `dist/reddit-openclaw-daily.skill`

其中 `install_local.py` 会把 skill 落成一个可运行的本地项目，并生成：

- `collector.py`
- `final_send.py`
- `config.json`
- `feeds.json`
- `cron_templates.json`
- `.gitignore`

## 适合什么场景

适合这些需求：

- 想做 OpenClaw 相关 Reddit 日报
- 想把 Reddit RSS 抓取、评论抓取、候选排序、卡片生成、发送账本做成标准流程
- 想把日报能力迁移到另一台 OpenClaw 实例
- 想保留固定输出风格，但不想把目标 chat id、部署路径、发送时间写死

## 设计原则

这套 skill 明确做了拆分：

### 可配置部分
- 发送目标
- cron 时间
- 安装路径
- feed 列表
- 告警文案
- 发送条数与重试次数

### 保留默认产品风格的部分
- 中文标题优先
- `原标题：...` 单独一行
- `核心观点` 卡片式输出
- `认可度：高/中/低 + 正反理由`
- send_state 账本机制
- `mark-sent / mark-failed` 流程

一句话：**把实例私货剥离，把流程骨架固化。**

## 仓库结构

```text
.
├── SKILL.md
├── README.md
├── references/
│   └── config.md
├── scripts/
│   ├── collector.py
│   ├── final_send.py
│   └── install_local.py
└── dist/
    └── reddit-openclaw-daily.skill
```

## 快速开始

### 1）安装到本地目录

```bash
python3 /root/skills/reddit-openclaw-daily/scripts/install_local.py \
  --base-dir ~/reddit-openclaw-daily \
  --channel telegram \
  --target 123456789
```

执行后会生成一个本地项目目录，例如：

```text
~/reddit-openclaw-daily/
├── collector.py
├── final_send.py
├── config.json
├── feeds.json
├── cron_templates.json
└── .gitignore
```

### 2）检查配置

重点看这几个文件：

- `config.json`
- `feeds.json`
- `cron_templates.json`

其中：

- `config.json` 管运行参数
- `feeds.json` 管抓取源
- `cron_templates.json` 提供 OpenClaw cron 创建模板

字段说明见：`references/config.md`

### 3）先跑一次抓取

```bash
cd ~/reddit-openclaw-daily
python3 collector.py --once --base-dir ~/reddit-openclaw-daily --feeds-file ~/reddit-openclaw-daily/feeds.json
```

### 4）生成发送状态

```bash
python3 final_send.py --base-dir ~/reddit-openclaw-daily --date today --prepare
```

### 5）查看待发送清单

```bash
python3 final_send.py --base-dir ~/reddit-openclaw-daily --date today --send
```

### 6）查看汇总

```bash
python3 final_send.py --base-dir ~/reddit-openclaw-daily --date today --summary
```

## 产物说明

### collector.py
负责：

- 读取 Reddit RSS
- 拉帖子评论 RSS
- 生成本地结构化产物
- 维护 `report_source.json`
- 维护 `manifest.json`
- 做幂等与断点恢复

### final_send.py
负责：

- 候选排序
- 生成卡片文案
- 输出待发送最小清单
- 维护 `send_state.json`
- 提供 `mark-sent` / `mark-failed`
- 生成 `report_0800.md` 备份稿

### install_local.py
负责：

- 复制运行脚本到目标目录
- 生成 `config.json`
- 生成 `feeds.json`
- 生成 `cron_templates.json`
- 让这套 skill 可以在新实例快速落地

## cron 用法

`install_local.py` 生成的 `cron_templates.json` 只是模板，不会自动创建任务。

你需要再用 OpenClaw 的 `cron` 工具把这些模板真正落成任务。通常会包含：

- collector 窗口任务
- prepare 窗口任务
- 发送前回执任务
- 正式发送任务

也就是说：**模板是脚手架，不是魔法。**

## 输出风格

默认保留中文日报卡片风格，包括：

- 中文标题
- `原标题：...`
- `核心观点：...`
- `关键评论：...`
- `认可度：...`
- 原帖链接

如果你想改成英文、改成更短摘要、或者完全换模板，可以从 `final_send.py` 下手。

## 安全边界

这个仓库默认不应包含：

- 你的 chat id
- 你的 session key
- 你的私有 cron 目标
- 你的 API key
- 你的私有部署路径

这些都应该留在实例侧配置里，而不是写死在 skill 源码中。

## 打包 skill

如果你修改了 skill 内容，重新打包：

```bash
python3 /usr/lib/node_modules/openclaw/skills/skill-creator/scripts/package_skill.py /root/skills/reddit-openclaw-daily /root/skills/dist
```

输出文件：

```text
/root/skills/dist/reddit-openclaw-daily.skill
```

## 当前状态

目前这版已经完成：

- skill 目录整理
- 本地安装器
- 配置模板生成
- `.skill` 打包
- GitHub 仓库存档
- 空候选集时 `final_send.py --send` 的边界修复

## 后续可继续做

如果要把它做得更像正式产品，下一步值得补：

- 更完整的 README 示例图
- GitHub Release 自动上传 `.skill`
- 一键导入 cron 的辅助脚本
- 更通用的 feed 配置机制
- 非中文输出模板

---

如果你在找一句最短结论：

**这不是把现有项目搬上 GitHub，而是把 Reddit 日报能力抽成一个能迁移、能配置、能复用的 OpenClaw skill。**
