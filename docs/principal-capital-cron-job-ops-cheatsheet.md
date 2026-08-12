# 主力资金监控 · cron-job.org 外部触发速查

> 用途：把主力资金扫描与看门狗的触发从「不守时的 GitHub schedule」迁到 cron-job.org 外部定时器，作为**主触发**；两个 workflow 里原有的 `schedule:` 保留不动作为**备份**。看门狗再兜第三层（异常时发告警邮件）。
>
> 仓库：`caohongbocaohongbo/new-france`　默认分支：`main`
> 最后更新：2026-08-11

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
- **真兜底（未做）**：东财 + akshare 同时挂时仍为 `no_data`，只是现在有告警 + 页面明确显示，不再静默。未来若接新浪/腾讯兜底，只能限定小票池或候选二次确认并限流（见 `multi_source.py` 内 TODO），严禁全市场逐股并发。
