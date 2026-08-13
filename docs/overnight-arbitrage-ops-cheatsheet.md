# 尾盘隔夜套利 · 平台运维速查

一页看懂：邮件推送这条链路由哪几个平台协作、各管什么、出问题去哪查。

## 一、四个平台的职责分工

| 平台 | 管什么 | 不管什么 |
|---|---|---|
| **GitHub** | 存代码；`main` 分支是部署真源；Actions 每日跑 `--dry-run` 只产数据快照（`data-snapshots` 分支），**不发邮件** | 不触发正式发信、不存运行密钥 |
| **Render** | 跑后端代码（web 服务 `new-france-api`）；存运行密钥（`OA_TRIGGER_TOKEN`/`BREVO_API_KEY`/`SMTP_PASSWORD`）；`main` 更新后自动部署 | 不定时、不投递邮件（免费实例会休眠） |
| **cron-job.org** | 免费外部定时器；工作日北京 14:40 预热、14:43 打 `/scheduled-run` 触发正式发信 | 不跑业务逻辑、不存代码 |
| **Brevo** | 真正把邮件投递出去（HTTPS API 代发）；管发件人验证与到达率 | 不定时、不产决策内容 |

一句话链路：**cron-job.org 定时 → Render 跑决策 → Brevo 投递 → QQ 邮箱收信**；GitHub 只负责代码与快照。

## 二、正式发信触发链（交易日 14:43）

1. cron-job.org 任务 A（14:40，GET `/api/v1/overnight-arbitrage/latest`）唤醒 Render 免费实例（防冷启动）。
2. cron-job.org 任务 B（14:43，POST `/api/v1/overnight-arbitrage/scheduled-run`，Header `X-OA-Trigger-Token`）触发。
3. Render 校验 token → 后台跑 `run_overnight_arbitrage(dry_run=False)`。
4. 通过全部门禁后，经 Brevo 发一封邮件到 `896256756@qq.com`。

发信门禁（任一命中则不发，记在 `blocked_reasons`）：
`dry_run` / `non_trading_day`（周末）/ `outside_valid_window`（非北京 14:40–14:55）/ `data_unavailable` / `no_complete_candidates`（剔除缺字段后无候选）/ `no_qualified_candidates`（无票达阈值）/ `zt_pool_unavailable` / `already_sent`（当日已发）。

去重：当日仅发一封（`already_sent` 状态 + Brevo 按日幂等键）。

## 三、关键配置项

| 项 | 位置 | 值 / 说明 |
|---|---|---|
| `OA_TRIGGER_TOKEN` | Render `new-france-api` → Environment | 随机串（`openssl rand -hex 24`）；cron-job.org 任务 B 的 Header 必须一致 |
| `BREVO_API_KEY` | Render web 服务 Environment | Brevo 后台 API Keys |
| `SMTP_PASSWORD` | Render web 服务 Environment | Gmail 应用专用密码（SMTP 兜底用；主通道走 Brevo） |
| 发件人 | Brevo → Senders | `caohongbo183760584@gmail.com`，须 Verified |
| 收件人 | render.yaml `SMTP_TO` | `896256756@qq.com`（多个用逗号分隔） |
| cron-job.org 任务 A | cron-job.org | GET `.../latest`，14:40，Asia/Shanghai，Mon–Fri |
| cron-job.org 任务 B | cron-job.org | POST `.../scheduled-run`（https），14:43，Asia/Shanghai，Mon–Fri，带 token 头 |

Web 服务公网地址：`https://new-france-api.onrender.com`
API 前缀：`/api/v1/overnight-arbitrage`

## 四、排查入口（收不到邮件时按序查）

1. **Render → `new-france-api` → Logs**：看 14:43 那次请求
   - 打印 `blocked_reasons` → 按上面门禁对照（最常见 `outside_valid_window`、`zt_pool_unavailable`）。
   - 出现 `Brevo API 返回 4xx` → 发件人未验证 / API key 失效。
2. **cron-job.org → 任务 B → History**：看是否按时触发、返回码
   - `202` = 正常触发；`401` = token 不匹配；`503` = Render 无 token 或实例休眠中；`307` = URL 用了 http，改 https。
3. **QQ 邮箱垃圾箱**：发件人是 gmail 免费域名，首封可能进垃圾箱 → 标「非垃圾」+ 加白名单。
4. **Brevo → Transactional → Logs/Statistics**：确认 Brevo 侧是否收到发送请求、投递状态。

## 五、手动触发 / 自测命令

```bash
# 唤醒（免费实例可能休眠，先跑这条）
curl -s -o /dev/null -w "%{http_code}\n" \
  https://new-france-api.onrender.com/api/v1/overnight-arbitrage/latest

# 正式入口自测（非窗口时间会被 outside_valid_window 拦，返回 202 但不发信属正常）
curl -i -X POST \
  -H "X-OA-Trigger-Token: <你的token>" \
  https://new-france-api.onrender.com/api/v1/overnight-arbitrage/scheduled-run
```

## 六、明确「不做」的事

- 不建 Render 付费 Cron（定时交给免费的 cron-job.org）。
- GitHub Actions 保持 `--dry-run`，永不发正式邮件（其 cron 会漂移，不适合精确窗口）。
- 不为去重状态挂 Render 持久盘（单日单次触发 + Brevo 幂等键已够）。

## 七、维护须知

- **改代码 → 合并到 `main` → Render 自动部署**。改动务必走 PR 合 `main`，否则线上不更新（`/scheduled-run` 404 通常就是代码没部署到 `main`）。
- 轮换 token：先改 Render `OA_TRIGGER_TOKEN`（触发重部署），再同步 cron-job.org 任务 B 的 Header。
- 窗口太窄（14:40–14:55，15 分钟）：若观察到 Render 冷启动逼近 14:55 导致漏发，放宽 `service.py` 中 `outside_valid_window` 的上界并同步展示文案。
