# Codex 执行任务：主力资金数据源冗余优化（方案① - 轻量）

> 一次性执行指令。**严格按范围执行**，不要顺手重构无关代码。
> 项目根：`/Users/fangcang/new-france`，所有路径相对该根。
> 语言：新增注释/文档用**中文**；**禁止 emoji**。
> 目标模块：`backend/plugins/principal_capital/sources/`。

---

## 0. 背景与事实（先读，避免做错方向）

主力资金监控当前"3 个数据源"其实**本质都是东方财富**：
- `eastmoney_push2`（`push2.eastmoney.com`）
- `eastmoney_push2his`（`push2his.eastmoney.com`）
- `akshare`（底层 `ak.stock_individual_fund_flow_rank` **也是东财**，见 `sources/akshare_source.py` 字段全是东财口径）

所以东财 IP 被封时三源同时 502，形成"假冗余"。真正独立的只有新浪（`sources/sina.py`），但**新浪/腾讯的独立资金流接口都是"单股逐个查"，没有"全市场一次拉排行"**——全市场扫描客观上没有等价的免费独立源。

**本方案目标（不是换掉东财，而是让东财抖动时更容易恢复 + 增加独立核验/小批量兜底）**：
1. **放宽熔断**：现在连续 3 次失败就锁 30 分钟，太激进——数据源短暂抖动后本可恢复却被锁死，导致后续 cron 周期全部 `skipped_blocked`。
2. **东财取数更健壮**：超时收敛到 10s，增加同源多域名轮询 + 单页快速重试。
3. **新增腾讯独立单股源**（`qt.gtimg.cn` ff_ 接口），接入现有核验层作为第二独立核验源，并可用于小批量兜底。
4. **akshare 正确定位**：在注释/日志中标注其为"东财同源兜底"，不作独立源宣传（不影响其仍在降级链中）。

**范围外（不要做）**：不实现"东财全挂时逐股并发拼全市场"（那是方案②，Actions 6min 超时下有风险）；不改前端；不改 overnight 插件；不改缓存 TTL（保持 1800s）。

---

## 1. 熔断参数放宽 —— `backend/plugins/principal_capital/config.py`

`CONFIG` 字典（33-35 行）改为：
```python
"data_source_cache_ttl_seconds": 1800,          # 保持不变
"data_source_circuit_break_minutes": 8,         # 30 -> 8（约一个 5min cron 周期后即可重试）
"data_source_failure_threshold": 5,             # 3 -> 5（容忍更多短暂抖动再熔断）
```
（允许 env 覆盖的既有写法若存在则保留；此处只调默认值。）

---

## 2. 东财取数更健壮 —— `backend/plugins/principal_capital/sources/eastmoney.py`

### 2.1 超时与多域名
- `fetch_market_fund_flow(timeout: int = 12, ...)` 默认超时改为 **10**。
- 新增模块级常量：同源可轮询域名列表（供 multi_source 使用；东财常用镜像）：
  ```python
  EASTMONEY_HOSTS = [
      "https://push2.eastmoney.com/api/qt/clist/get",
      "https://push2his.eastmoney.com/api/qt/clist/get",
      "https://62.push2.eastmoney.com/api/qt/clist/get",
  ]
  ```
  （`DEFAULT_BASE_URL` 保持不变，向后兼容。）

### 2.2 单页快速重试
在分页抓取循环里（约 93-107 行 `for page in range(1, 8)` 内的 `http.get`），对**单次请求**失败（`requests.RequestException`）做 **1 次快速重试**（间隔 0.5s）再决定是否抛 `FundFlowFetchError`。避免单页瞬时抖动导致整源判失败。
- 实现：把单页请求封装小函数 `_get_page(http, base_url, fs, page, timeout)`，内部 try 一次、失败 sleep 0.5 再试一次、仍失败则 raise。
- 保持原有"diff 为空即 break""len(diff)<200 即 break"的分页终止逻辑不变。

---

## 3. 新增腾讯独立单股源 —— 新建 `backend/plugins/principal_capital/sources/tencent.py`

仿 `sources/sina.py` 的结构（单股 + 并发多股），接口：`http://qt.gtimg.cn/q=ff_<prefix><code>`。

- `_prefix(code)`：`sh` if code 以 6/9 开头 else `sz`（与 sina 一致）。
- 响应格式：`v_ff_sh600000="..."`，按 `~` 分割。字段（社区逆向，索引从 0）：
  - 0 code，1 主力流入，2 主力流出，3 主力净流入，5 散户流入，6 散户流出，9 资金流总和，12 名称，13 日期。
  - `main_net_inflow = 字段3`（单位：万元，需 ×1e4 转元以对齐东财口径），`total_amount ≈ 字段9 ×1e4`，`main_inflow_ratio = main_net/total*100`（total>0 时）。
  - **注意单位**：腾讯 ff_ 数值单位是"万元"，务必 ×10000 统一为元，和东财/新浪口径一致。若逆向字段与实际不符，以"3=主力净流入、9=总额"为准并在注释标注"字段索引为社区逆向，如有偏差需按实际响应校正"。
- `fetch_single_stock_fund_flow_tencent(code, timeout=8, session=None) -> Optional[dict]`：返回 `{code,name,main_net_inflow,main_inflow_ratio,source:"tencent"}`，异常返回 None（`logger.debug`）。
- `fetch_codes_fund_flow_tencent(codes, max_workers=10, timeout=8) -> List[dict]`：ThreadPoolExecutor 并发，仿 sina。
- 编码：腾讯返回是 GBK，需 `response.encoding = "gbk"` 或 `response.content.decode("gbk", errors="ignore")` 再解析文本。

---

## 4. 多源协调器接入 —— `backend/plugins/principal_capital/sources/multi_source.py`

### 4.1 熔断退避区分临时/持久错误
`_mark_failure(source)`（约 89-95 行）改进：
- 保持 `failure_streak += 1`。
- 达到 `FAILURE_THRESHOLD` 才 `blocked_until = now + BLOCK_MINUTES`。
- **可选增强**：接受一个 `transient: bool = True` 参数；对明显临时错误（502/超时/连接重置）熔断时长用 `min(BLOCK_MINUTES, 3)` 分钟更短退避，对其它错误用 `BLOCK_MINUTES`。调用处（约 217 行 except 块）根据异常类型传入：`requests.Timeout / ConnectionError / 5xx` 视为 transient。若判断成本高，可简化为统一 `BLOCK_MINUTES`（已从 30 降到 8，可接受）——**以简单可靠为先，transient 细分为可选项**。

### 4.2 东财多域名轮询
`fetch_market_fund_flow_resilient` 的 `sources` 列表（约 180-185 行）中，把 `eastmoney_push2` 与 `eastmoney_push2his` 的 loader 改为"按 `EASTMONEY_HOSTS` 顺序轮询，任一域名成功即返回"。可保持两个 source 名（用于健康度分别记录），但每个 loader 内部尝试多个域名：
- 简化实现：新增内部 `_fetch_eastmoney_any()`：依次尝试 `EASTMONEY_HOSTS`，第一个成功返回 df；全失败抛 `FundFlowFetchError`。
- `sources` 第一项改为 `("eastmoney", _fetch_eastmoney_any)`；保留 `("akshare", ...)` 作为第二项。**去掉重复的 push2his 独立项**（已并入轮询），避免"假三源"。
- `active_source` 命名相应调整：成功走东财→`"eastmoney"`，akshare→`"akshare"`，缓存→`"cache"`。

### 4.3 akshare 定位注释
在 akshare loader 附近加注释：`# akshare 底层同为东财数据，非独立源，仅作东财主接口异常时的同源兜底`。

### 4.4 核验层增加腾讯为第二独立源
`_verify_sample(df)`（约 152-170 行）当前只用新浪抽样核验。增强为**新浪 + 腾讯双源交叉核验**：
- `from .tencent import fetch_codes_fund_flow_tencent`。
- 对 head(5) 样本，同时取 sina 与 tencent，comparisons 里各列出 `eastmoney / sina / tencent` 及偏差。
- 逻辑失败不影响主流程（保持 try 包裹或安全默认）。
- 这样"独立源"从 1 个（新浪）增加到 2 个（新浪+腾讯），核验更可信。

---

## 5. 测试 —— 新建 `tests/test_principal_capital_sources.py`

用 `unittest` + `unittest.mock`（不发真实网络请求）：
1. `tencent._prefix`：6/9 开头→sh，其它→sz。
2. `tencent.fetch_single_stock_fund_flow_tencent`：mock `requests.Session.get` 返回构造的 `v_ff_sh600000="..."` GBK 文本，断言 `main_net_inflow` 已 ×1e4、ratio 计算正确、异常返回 None。
3. `multi_source._fetch_eastmoney_any`：mock 使第一个 host 抛错、第二个成功，断言返回第二个的 df（验证多域名轮询）。
4. 熔断放宽：构造连续失败，断言第 5 次才写 `blocked_until`（阈值 5），且退避时长 ≤ 8 分钟。
5. 现有 `sources/tests/` 下的 `test_multi_source.py`、`test_sina.py`、`test_eastmoney.py` 必须仍通过（跑一遍确认无回归）。

---

## 6. 涉及文件白名单

- `backend/plugins/principal_capital/config.py`（熔断参数）
- `backend/plugins/principal_capital/sources/eastmoney.py`（超时/多域名/重试）
- `backend/plugins/principal_capital/sources/tencent.py`（新建）
- `backend/plugins/principal_capital/sources/multi_source.py`（轮询/熔断/核验）
- `tests/test_principal_capital_sources.py`（新建）

**不得改动**：前端、docs、overnight 插件、notifier、runtime_config、缓存 TTL、GitHub workflow。

---

## 7. 验证清单（执行完必须自查）

1. 语法：`python -m py_compile` 所有改动 .py。
2. 新测试全绿；`backend/plugins/principal_capital/sources/tests/` 既有测试无回归（`python -m pytest backend/plugins/principal_capital/tests -q` 或对应 unittest）。
3. 手动跑一次（本地取不到东财会走降级，主要验证不崩、日志清晰）：
   `python -m backend.main --run-principal-capital-scan --force`
   确认：`source_status.attempts` 里东财是单条 `eastmoney`（不再是 push2/push2his/akshare 三条假冗余），熔断锁定时长为 8min 量级。
4. 确认 `data_source_failure_threshold=5`、`circuit_break_minutes=8` 已生效（读 `data/principal_capital_source_health.json` 的 `blocked_until` 与 now 差值）。
5. 单位核对：腾讯 ff_ 返回值 ×1e4 后与东财同量级（若有真实网络，抽一只对比；无网络则以单测覆盖）。

## 8. 提交（如需）
- 分支：`feat/principal-capital-source-resilience`
- commit message 用中文，结尾附：
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
