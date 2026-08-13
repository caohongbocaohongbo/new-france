# 主力资金监控 · cron-job.org 外部触发速查

> 用途：把主力资金扫描与看门狗的触发从「不守时的 GitHub schedule」迁到 cron-job.org 外部定时器，作为**主触发**；两个 workflow 里原有的 `schedule:` 保留不动作为**备份**。看门狗再兜第三层（异常时发告警邮件）。
>
> 仓库：`caohongbocaohongbo/new-france`　默认分支：`main`
> 最后更新：2026-08-13

---

## 一、前置：GitHub Fine-grained PAT

创建地址：https://github.com/settings/personal-access-tokens/new

- Repository access：**Only select repositories** → 勾 `caohongbocaohongbo/new-france`
- Permissions → Repository permissions：
  - **Actions: Read and write**（触发 workflow_dispatch 唯一必需项）
  - **Metadata: Read-only**（GitHub 强制自动加，无法删，不用管）
  - 其余全部 No access（若手滑加了 `Repository security advisories` 等，删掉）
- 生成后立即复制 `github_pat_xxx`，只显示一次。到期需重新生成并更新到下方 4 个 job 的 Header。

> 触发接口依赖 workflow 文件已在 `main` 分支上。`workflow_dispatch` 只认默认分支上已存在的 workflow。

---

## 二、cron-job.org 建 4 个 job

登录 https://cron-job.org → 计划任务 → 创建计划任务。界面时区设为 **Asia/Shanghai**，则下表 Crontab 直接按北京时间填。

### 4 个 job 的公共设置

| 项 | 值 |
|---|---|
| 请求方法（进阶标签） | `POST` |
| 时区 | `Asia/Shanghai` |
| 激活任务 | 开 |
| 在任务历史中保存响应 | 开（便于排错） |

**Header（进阶标签，4 个 job 完全相同）：**

| 键 | 值 |
|---|---|
| `Authorization` | `Bearer github_pat_你的token`（`Bearer` 后有一个空格） |
| `Accept` | `application/vnd.github+json` |
| `X-GitHub-Api-Version` | `2022-11-28` |
| `Content-Type` | `application/json` |

### 各 job 差异

| Job | 标题 | Crontab（北京时间） | workflow 文件 | 请求正文 Body |
|---|---|---|---|---|
| 1 上午扫描 | `pc-scan-morning` | `25 9 * * 1-5` | `principal-capital-scan.yml` | `{"ref":"main","inputs":{"enable_verify":"true"}}` |
| 2 下午扫描 | `pc-scan-afternoon` | `55 12 * * 1-5` | `principal-capital-scan.yml` | `{"ref":"main","inputs":{"enable_verify":"true"}}` |
| 3 上午看门狗 | `pc-watchdog-morning` | `45 9 * * 1-5` | `principal-capital-watchdog.yml` | `{"ref":"main"}` |
| 4 下午看门狗 | `pc-watchdog-afternoon` | `15 13 * * 1-5` | `principal-capital-watchdog.yml` | `{"ref":"main"}` |

**网址**（把结尾 workflow 文件名换成上表对应值）：
```
https://api.github.com/repos/caohongbocaohongbo/new-france/actions/workflows/<workflow文件名>/dispatches
```
例如 Job 1：
```
https://api.github.com/repos/caohongbocaohongbo/new-france/actions/workflows/principal-capital-scan.yml/dispatches
```

> 为什么每段只触发一次：扫描 job 触发后会在 job 内自循环每 5 分钟扫到收盘（上午收于 11:30、下午收于 15:00），因此每个交易时段触发一次即可。09:25 / 12:55 略早于开盘，保证开盘那刻已在循环里（开盘前的空轮次会记 `skipped`，无副作用）。

---

## 三、验证

在任一 job 页面点「立即运行 / 测试运行」：

- cron-job.org 返回 **204 No Content** = 成功（GitHub 触发成功即空响应，属正常）。
- 到 https://github.com/caohongbocaohongbo/new-france/actions 看对应 workflow 是否出现一条来源为 `workflow_dispatch` 的新运行。

返回码排错：

| 返回码 | 原因 |
|---|---|
| 401 / 403 | PAT 权限或粘贴问题：确认 Actions=Read and write、`Bearer ` 后有空格、token 未过期 |
| 404 | 网址仓库名 / workflow 文件名拼错，或 workflow 文件尚未推到 `main` |
| 422 | Body 里 `ref` 必须是 `main`，或 inputs 字段名不匹配 |

---

## 四、三层可靠性与告警口径

- **第一层**：cron-job.org 主触发（本文档）。
- **第二层**：两个 workflow 自带的 `schedule:` 备份（扫描在开盘前后撒多个冗余点；看门狗 `45 1` / `15 5` UTC）。
- **第三层**：看门狗 `scripts/principal_capital_watchdog.py` 读快照判定并发一次性告警邮件：
  - 快照日期 ≠ 今天 → 「今日未产生快照（可能未启动）」
  - 今日 `no_data` / `error` → 「已运行但数据源失败 / 无数据」
  - 今日 `completed` / `skipped` → 不告警
  - 同日同类告警去重（状态文件 `reports/principal_capital_watchdog_state.json`，随快照分支持久化）。

**告警口径**：以快照 `status` / `now` 为准，**不以「有没有收到邮件」为准**——扫描邮件仅在有 fresh 买卖信号时才发，正常交易日多数轮次是「completed 但无命中、不发邮件」，属正常。

---

## 五、维护提醒

- **PAT 到期**：4 个 job 的 `Authorization` Header 需同步换新 token。
- **仓库改名 / 迁移**：4 个 job 的网址、以及 workflow 里的 `PC_SNAPSHOT_RAW_BASE` 需同步更新。
- **改动 workflow 文件名**：cron-job.org 网址结尾要跟着改。
- **数据源真兜底（已做，见第六节）**：东财 + akshare 同时失效时，由新浪全主板接管；不再是静默 `no_data`。

---

## 六、数据源方案：东财失效 → 新浪兜底

### 6.1 为什么东财会失败（根因）

- 主力资金全市场榜单原走东方财富 `push2.eastmoney.com`，但该接口对**海外 / 数据中心 IP** 限流封禁。
- 扫描跑在 **GitHub Actions（微软 Azure 美国机房 IP）**，Render 读接口也在美国 —— 两者都连不通东财，表现为 `http_code=000` / 502 / 空响应。
- `akshare` 底层同为东财数据源，**非独立源**，东财挂它必然一起挂。
- 结果：告警邮件里 `eastmoney: skipped_blocked` + `akshare: skipped_blocked`，快照 `no_data`。

> 结论：这**不是「免费接口」的问题**，而是「请求发起地（美国 IP）被东财封」的问题。换付费源也多为日频 EOD，不匹配盘中每 5 分钟需求。

### 6.2 兜底方案：新浪全主板（独立源）

数据源链条：**东方财富 → akshare（同源兜底）→ 新浪全主板（独立真兜底）→ 本地缓存降级**。

新浪实现要点（`backend/plugins/principal_capital/sources/sina_market.py`）：

- **代码清单**：新浪全A榜单 `Market_Center.getHQNodeData` 翻页取沪深主板（隔夜套利插件已在美国 IP 用它，证明可访问）。
- **资金流**：`MoneyFlow` **单股接口并发查询**。
  - 为什么不用逗号批量：批量返回**乱序且不带 code**，会张冠李戴（实测 30/30 全错位）；单股查询用传入 code 直接对应，**天然零错位**。
- **主板口径**：`60 / 000 / 001 / 002 / 003`（002/003 为深市主板，原先漏含，已补全）。
- **字段换算**：新浪 `r0~r3`（超大/大/中/小单）→ 主力净流入 `(r0_in+r1_in)-(r0_out+r1_out)`、成交额 `Σ(r*_in+r*_out)`、占比 = 主力净流入 / 成交额 ×100，组装成与东财一致的 12 列 DataFrame。

### 6.3 性能与关键参数（美国 IP 实测）

| 配置 | 清单 | 资金流(全主板~3100只) | 总计 | 成功率 |
|---|---|---|---|---|
| workers=20 | 25s | 112s | **138s（超 120s 墙钟）** | 100% |
| workers=40 | 31s | 54s | **85s** | 100% |
| workers=40 + 清单缓存命中 | ~0s | 54s | **~54s（距墙钟余 66s）** | 100% |

落地参数（`config.py` 的 `CONFIG`，可调）：

- `sina_max_workers = 40` —— 美国 IP 实测 40 并发仍 100% 成功、不被限频。
- `sina_codes_cache_ttl_seconds = 259200`（3 天）—— 主板成分变动极慢，缓存省每轮翻页约 25s。
  - 缓存文件 `data/principal_capital_sina_codes.json`，已纳入 commit/restore 快照，跨 job / 跨天复用。
- 扫描 `scan_once` 的 `timeout 120` **无需改动**（54s 稳稳落在墙钟内）。

### 6.4 验收方法

1. **非交易时段快速验链路**：手动触发 `Principal Capital Scan`，`force=true`（只跑一轮）。
2. **交易时段验真实扫描**：等 cron-job.org / schedule 自动触发，或 `force=false` 手动跑。
3. 看 `/api/v1/principal-capital/latest` 或页面：
   - 东财可用时 `active_source: eastmoney`；东财失效时应回退到 `active_source: sina`、`status: completed`、有扫描数与买卖信号。
   - 页面底部「数据源状态」应能看到 `eastmoney / akshare / sina` 三行健康状态。

### 6.5 验收结论

- 连通性（美国 IP）：新浪榜单 + 资金流两接口均 **100% 可用、不限并发**。
- 全主板扫描在 40 并发 + 清单缓存下 **~54s 完成**，稳定塞进 120s 墙钟。
- 数据字段、代码对应、买卖候选均校验正确（零错位）。
- 遗留：新浪若未来也对美国 IP 限制，需转境内 VPS 抓取（当前无此迹象）。
