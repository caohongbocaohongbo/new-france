# 22 智能选股聚合中枢 — diff 核验

> 基准版本：2026-09-08 grill 定稿原稿  
> 更新版本：2026-09-08 补充 6 处缺失规格  
> 本文件仅记录变更内容，未改行不列出。

---

## DIFF-1：新增 §2.4 `read_snapshot_resilient` 规格

**位置**：`§2.3 组件清单` 表格末行之后，`§3 受控改造清单` 之前

**before**（原文直接是分隔线 + §3 标题）：

```
| 前端 | `frontend/js/plugins/research_plugins.js` + `frontend/index.html` | 改造 |

---

## 3. 受控改造清单
```

**after**（插入完整 §2.4）：

```
| 前端 | `frontend/js/plugins/research_plugins.js` + `frontend/index.html` | 改造 |

### 2.4 `read_snapshot_resilient` 规格（`backend/plugins/common.py` 新增）

函数签名：
  snapshot_name: str
  timeout: float = 5.0
  ttl_seconds: int = 600   # HUB_REMOTE_TTL_SECONDS，0 = 不缓存

URL 拼接：
  RAW_BASE = os.getenv("GITHUB_RAW_BASE", "https://raw.githubusercontent.com/{USER}/{REPO}/data-snapshots")
  url = f"{RAW_BASE}/reports/data_backend/{snapshot_name}_latest.json"

执行流程（4 步，永不抛异常）：
  步骤 1：读 reports/{name}_latest.json          → 成功 _source="local"
  步骤 2：读 reports/data_backend/{name}_latest.json → 成功 _source="local"
  步骤 3：chip 专属拦截（chip_remote_fetch=false） → no_data，不发网络
  步骤 4：httpx.get(url, timeout=5.0)            → 成功 _source="snapshot"；失败 _source="unavailable"

磁盘 TTL 缓存路径：reports/.cache/{name}_remote.json（不进 git）
内存缓存由各 router._latest_cached() 负责，函数本身不缓存。

---

## 3. 受控改造清单
```

**变更理由**：原文 §2/§3/§4 反复引用 `read_snapshot_resilient` 但无实现规格，云端兜底逻辑不可施工。

---

## DIFF-2：新增 §4.9 `charts` 字段结构，原 §4.9 顺延为 §4.10

**位置**：`§4.8 图表预计算` 之后

**before**：

```
### 4.9 组装输出 `build_payload(...)` → `write_snapshot("smart_picker", payload)` + SSE 广播
```

**after**（插入 §4.9，原节编号顺延）：

```
### 4.9 `charts` 字段结构（快照内嵌，与 §6.3 响应同构）

快照顶层增加 charts 对象，键为 6 位 code，值结构：
  charts.{code}.days           = int
  charts.{code}.records        = [{date, open, close, high, low, vol}]
  charts.{code}.series.ma      = {ma5, ma10, ma20, ma60}（数组，null 填充）
  charts.{code}.series.macd    = {dif, dea, hist}
  charts.{code}.series.kdj     = {k, d, j}
  charts.{code}.series.rsi     = 数组
  charts.{code}.series.boll    = {mb, ub, lb}

体积：40 只 × 80 bar × 5 series ≈ 20KB（JSON 含 null 压缩前）
charts 键不参与内存缓存 hash 变更检测（避免触发无意义 SSE 广播）

### 4.10 组装输出 `build_payload(...)` → `write_snapshot("smart_picker", payload)` + SSE 广播
```

**变更理由**：§4.8 写入快照 `charts.{code}`，§7.3 预算表列出 `charts`，但字段结构从未定义；前端和单测均依赖此结构。

---

## DIFF-3：§6.2 补充 `?date=` 历史查询响应格式

**位置**：`§6.2 GET /api/v1/smart-picker/{code}` 中 `?date=` 一行之后

**before**：

```
- `?date=YYYY-MM-DD`：本地走 SQLite `smart_picker_hits`（`raw_json` 反序列化）；云端返回 `{"status":"local_only_unavailable"}`。
```

**after**（补充响应格式）：

```
- `?date=YYYY-MM-DD`：本地走 SQLite `smart_picker_hits`（`raw_json` 反序列化）；云端返回 `{"status":"local_only_unavailable"}`。

  历史查询响应（本地有数据时）：
    {"status":"ok","code":"600519","date":"2026-09-03","source":"sqlite",
     "item": { /* §6.1 items[] 单行完整结构，含 hits/badges/charts_precomputed */ }}
  未命中（该 code 当日未进榜）：
    {"status":"ok","code":"600519","date":"2026-09-03","item":null,"note":"no_hit_on_date"}
```

**变更理由**：历史查询是 `picker_perf_daily` 和前端详情追溯的必经路径，无响应格式则单测无法编写。

---

## DIFF-4：§7.2 补充并发写安全说明

**位置**：§7.2 末尾，`快照双写` 一行之后

**before**（§7.2 末行）：

```
- 快照双写：`reports/smart_picker_latest.json` + `reports/data_backend/...`（`common.write_snapshot`，原子写 + 内存缓存 + SSE 广播）；提交 `data-snapshots` 分支（`commit_screening_data.sh` 现成，0 改动）。
```

**after**（追加并发说明）：

```
- 快照双写：...（同上）
- **并发写安全**：`--run-smart-picker-all`（cron）与 `--run-smart-picker-hub`（手动）不支持并发；
  `db_delete + db_append` 非原子，并发会产生部分数据。
  规避：cron 运行期间不手动触发；如需保障，在 `run_smart_picker_hub_once` 入口写
  `reports/.hub.lock`（进入时创建，退出时删除），第二个进程检测到 lock 即跳过并记日志。
```

**变更理由**：`db_delete + db_append` 非原子操作，cron 与手动并发时会产生数据竞争，需明确规避策略。

---

## DIFF-5：§7.3 体积预算细化 + `commit_screening_data.sh` 兼容性确认

**位置**：§7.3 末行

**before**：

```
- 预算：**≤120KB**（实测模拟：全字段合并 98KB，现状四快照合计 163KB）。
```

**after**：

```
- 预算：**≤120KB**（实测模拟：items 全字段合并 98KB + charts 约 20KB，总计 ~118KB；现状四快照合计 163KB）。
- `commit_screening_data.sh` 兼容性确认：该脚本 rsync 整个 `reports/` 目录（含 `reports/data_backend/`），
  新生成的两个 smart_picker 快照均在覆盖范围内，**无需改动脚本**。
  如脚本实际使用 glob 而非整目录，施工时需验证两个路径均命中。
```

**变更理由**：原文体积预算未计入 charts 部分（~20KB），且 `commit_screening_data.sh` "0 改动"结论未经验证。

---

## DIFF-6：§7.4 交易日历来源 + `trading_days_after` 函数规格

**位置**：§7.4 伪代码块之前（整节重写）

**before**（原伪代码注释）：

```
      # 只取「D 之后第 k 个交易日」的 bar，且该 bar 日期 ≤ R（未来数据不可见）
      target = D 之后第 k 个交易日（按 trading_calendar 交易日序列，非自然日）
      if target > R: 跳过（未到期，不填）
```

**after**（补充完整函数 + 来源说明）：

```
交易日历来源：直接从 K 线 bar 序列推导，不依赖外部日历接口。

def trading_days_after(bars, anchor_date, k) -> str | None:
    dates = [b["date"] for b in bars]
    try:
        idx = dates.index(anchor_date)
    except ValueError:
        return None
    target_idx = idx + k
    return dates[target_idx] if target_idx < len(dates) else None

原理：东财 fetch_historical 返回的 bar 序列本身就是交易日序列，
      已剔除休市/停牌缺 bar 日，无需外部日历文件，无网络依赖。
停牌处理：bars 在 anchor 之后不足 k 条 → data_missing=1，跳过该窗口。
单测验证：构造 mock bars 直接断言返回值，无任何外部依赖。

伪代码 target 行改为：
      target = trading_days_after(bars, D, k)
      if target is None or target > R: 跳过
```

**变更理由**：`trading_calendar 交易日序列` 来源不明，是无前视单测能否通过的前提；改为从 K 线 bar 推导消除外部依赖。

---

## 变更汇总

| DIFF | 位置 | 类型 | 影响等级 |
|---|---|---|---|
| DIFF-1 | §2.4（新增） | 新增函数规格 | 高：云端兜底实现前提 |
| DIFF-6 | §7.4 | 重写伪代码 + 新增函数 | 高：无前视单测前提 |
| DIFF-2 | §4.9（新增） | 新增字段结构 | 中：前端/单测参考 |
| DIFF-3 | §6.2 | 补充响应格式 | 中：历史查询单测前提 |
| DIFF-4 | §7.2 | 追加并发说明 | 低：极端场景保护 |
| DIFF-5 | §7.3 | 体积预算细化 | 低：验收项补全 |
