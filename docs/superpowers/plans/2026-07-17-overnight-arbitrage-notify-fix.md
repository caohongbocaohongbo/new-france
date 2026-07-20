# 尾盘隔夜套利 · 邮件推送修复规划（2026-07-17）

## 背景与根因

昨天收到两封隔夜套利邮件，且同一只股票在两封里评分不同。根因：

- GitHub Actions cron「可能延迟数小时」，在非 14:43 的时刻又跑了一次；
- 两次跑的是不同时刻的行情快照，同一只票评分自然不同；
- 当时没有任何按日去重，两封都发出去了。

上一轮改动（Codex）已经做了：GitHub Actions 改 `--dry-run`、加 `already_sent` 按日去重、加 14:43–14:55 时间窗、Brevo 幂等键、令牌鉴权、数据质量字段检测。方向正确，但引入两个必须补齐的问题：

1. **改完后没有任何自动触发器再发正式邮件**（GitHub dry-run、前端 `/run` 强制 dry-run、`/scheduled-run` 无人调用）→ 会从「两封」变「零封」。
2. Top20 任一票缺字段就整封不发，尾盘数据抖动时容易收不到。

## 已确认的决策

- 触发架构：**新增 Render cron 服务，北京时间 14:43 直接跑 CLI**（不走 HTTP/token，Render cron 比 GitHub cron 准时）。
- 数据质量：**剔除缺字段个股后照常发送**，邮件里标注已剔除数量。

---

## 变更清单

### 变更 1（核心 · 必做）：新增 Render cron 直跑隔夜套利

文件：`render.yaml`，在 `services:` 下新增一个 cron 服务：

```yaml
  - type: cron
    name: new-france-overnight-arbitrage
    env: python
    region: oregon
    schedule: "43 6 * * 1-5"   # UTC 06:43 = 北京时间 14:43，周一至周五
    buildCommand: pip install -r requirements.txt
    startCommand: python -m backend.main --run-overnight-arbitrage
    envVars:
      - key: BREVO_API_KEY
        sync: false
      - key: SMTP_PASSWORD
        sync: false
      - key: SMTP_USER
        value: "caohongbo183760584@gmail.com"
      - key: SMTP_HOST
        value: "smtp.gmail.com"
      - key: SMTP_PORT
        value: "587"
      - key: SMTP_TO
        value: "896256756@qq.com"
```

要点：
- 该 cron 每次是全新容器、单日单次运行，`already_sent` 状态文件不持久也无所谓；跨「极少见的双触发」由 Brevo 幂等键兜底。
- `SMTP_USER` 同时用作 Brevo 发件人，必须保留 SMTP_* 环境变量。
- `--run-overnight-arbitrage` 不带 `--dry-run` → `dry_run=False` → 走正式发信，并受时间窗/交易日门禁保护。
- GitHub Actions 维持 `--dry-run` 不动（继续只产快照）。
- `/scheduled-run` 端点保留，仅用于手动/测试，不作为自动入口。

#### 密钥配置（二选一）

`BREVO_API_KEY`、`SMTP_PASSWORD` 是 `sync: false`，值不在仓库里、按服务隔离，新 cron 部署后这两项为空、发信会失败。用的仍是同一份现有密钥，只需让新服务拿到值，两种方式任选：

- 方式 A（默认 · 最省事）：不改代码，上线前在 Render 控制台给 `new-france-overnight-arbitrage` 手动填入这两个密钥（复制现有值）。一次性操作。
- 方式 B（可选 · 一劳永逸）：改用 Render Environment Group 统一管理。
  - 在 Render 控制台建一个环境组（如 `new-france-secrets`），把 `BREVO_API_KEY`、`SMTP_PASSWORD` 填进去一次。
  - 三个服务（`new-france-api`、`new-france-daily-screening`、`new-france-overnight-arbitrage`）的 `envVars` 中，用 `fromGroup: new-france-secrets` 引用该组，并从各自 `envVars` 里删除这两个 `sync: false` 条目。示例：
    ```yaml
    envVars:
      - fromGroup: new-france-secrets
      - key: SMTP_USER
        value: "caohongbo183760584@gmail.com"
      # ... 其余明文变量保持不变
    ```
  - 收益：以后再加服务只需引用组，不必逐个复制密钥。代价：需先在控制台建组并回填值一次。
  - 说明：`render.yaml` 无法创建环境组本身，仍需在控制台建组并填值；Blueprint 只能声明 `fromGroup` 引用。

### 变更 2（必做）：调整有效时间窗

文件：`backend/plugins/overnight_arbitrage/service.py`（约 907 行 `outside_valid_window` 判断）。

- 将窗口下界 `time(14, 43)` 改为 `time(14, 40)`，上界维持 `time(14, 55)`（即 14:40–14:55）。
- 邮件展示用的 `valid_window`/`valid_until` 文案同步为 `14:40-14:55`（检索 `"14:43"`、`"14:55"` 与 `"14:43-14:55"` 字面量一并更新，含 `service.py` 中 `valid_until` 字段与 HTML/文本模板）。

### 变更 3（必做）：数据质量由「一票否决」改为「剔除后照常发」

文件：`backend/plugins/overnight_arbitrage/service.py`

- 在 `build_overnight_decision` 里，取 `results = candidates[:limit]` **之前**，先把 `missing_quote_fields` 非空的候选剔除，并记录被剔除数量与明细（`removed_count`、`removed`）。
- `data_quality` 结构改为：
  - `status` 固定为 `complete`（因为进入名单的都完整）；
  - 保留 `required_fields`；
  - 新增 `removed_count`、`removed`（含 code/name/missing_fields）。
- `run_overnight_arbitrage` 的 `blocked_reasons` 里**删除** `incomplete_quote_fields` 这条；改为兜底：**若剔除后 `results` 为空**，追加 `no_complete_candidates` 阻断发信。
- 邮件文本与 HTML：当 `removed_count > 0` 时增加一行提示，例如「已剔除 N 只行情字段不全个股」。

### 变更 4（建议）：Brevo 多收件人拆分

文件：`backend/plugins/overnight_arbitrage/notifier.py` 的 `_send_via_brevo`。

- 将 `"to": [{"email": to_addr}]` 改为按逗号拆分：
  `"to": [{"email": e.strip()} for e in to_addr.split(",") if e.strip()]`
- 当前收件人是单地址，属预防性改动，避免以后配多地址踩坑。

### 变更 5（建议）：Brevo 发件人失败自愈

文件：`notifier.py` 的 `_send_via_brevo`。

- 函数内补一行 `api_key` 非空校验，失败返回明确错误，避免调用方误判。
- 说明：发信失败时 `run_overnight_arbitrage` 不写 `already_sent`，因此 Render cron 当天不会自动重试（单次 cron）；如需重试，可手动调 `/scheduled-run`（带 token）。此行为符合预期，无需额外改。

---

## 明确「不做」的事（避免过度设计）

- 不为去重状态文件挂 Render 持久盘。触发已收敛为「单日单次 Render cron」，去重不再是主要防线；Brevo 幂等键（按日期）作为双触发兜底即可。
- 不改动令牌鉴权与 `/scheduled-run` 逻辑（保留作手动入口）。
- 不改 GitHub Actions 的 dry-run 快照职责。

---

## 上线前人工确认清单（非代码）

1. Render 控制台为新 cron 配好 `BREVO_API_KEY`、`SMTP_PASSWORD`（sync:false 的密钥）。
2. **Brevo 后台已验证发件人** `caohongbo183760584@gmail.com`（否则 API 返回 401/403、发信必失败）。
3. `requirements.txt` 含 `requests`（notifier 新增依赖；确认已存在，缺则补）。
4. 确认 Render 时区理解：cron 的 `schedule` 按 UTC，`06:43` = 北京 14:43。

---

## 测试与验收

需 Codex 同步更新/新增测试：

- `tests/test_pipeline.py`
  - 更新 `test_pipeline_blocks_official_email_when_volume_ratio_is_missing`：缺字段个股应被**剔除**；若剔除后名单为空则断言 `no_complete_candidates` 阻断；新增一条「部分票缺字段、其余完整 → 正常发送且 `data_quality.removed_count>0`」用例。
  - 更新窗口相关用例：下界改 14:40（如 14:45 用例仍在窗内），上界维持 14:55（原 17:01 越窗用例仍应阻断）。
- `tests/test_notify.py`
  - 新增 Brevo 多收件人拆分断言（`to` 列表长度与内容）。
  - 邮件文案含「已剔除」提示（当有剔除时）。
- 运行：`python -m pytest backend/plugins/overnight_arbitrage/tests/ -q` 全绿。

验收标准：

1. 交易日北京 14:43，Render cron 触发后**恰好发一封**正式邮件，主题含当日日期与生成时刻。
2. Top20 中缺字段个股被剔除、其余照常入选，邮件标注剔除数量。
3. 手动 `/run` 默认 dry-run 不发信；GitHub Actions 仍只产快照不发信。
4. 同一交易日不会出现第二封不同评分的邮件。

---

## 交给 Codex 的执行顺序

1. 变更 1（render.yaml cron）——先解决「零邮件」。
2. 变更 3（数据质量剔除）+ 变更 2（窗口放宽）。
3. 变更 4、5（Brevo 健壮性）。
4. 更新测试并跑通。
5. 人工确认清单逐项核对后再部署。
