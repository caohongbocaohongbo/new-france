# New France 邮件推荐升级 · 施工文档（交付 DeepSeek 执行）

> 本文档由方案设计方产出，交 DeepSeek 执行具体开发，由 Claude 验证代码质量。
> 目标：把每日推荐邮件从「看好」升级为「可执行 + 可解读 + 可信」。
> 三大能力：可执行操作建议 + LLM 智能解读 + 策略胜率回测。

---

## 【第一红线 · 作用域锁定 · 违反任意一条即视为交付失败】

本次为**纯增量**升级。DeepSeek 只允许改动本方案明确列出的文件，且改动必须与本方案直接相关。

### 允许改动的文件（白名单，仅此清单）
- 新建：`backend/services/indicators.py`
- 新建：`backend/services/action_planner.py`
- 新建：`backend/services/backtest.py`
- 新建：`backend/services/llm_insight.py`
- 修改：`backend/services/screening_service.py`（仅在指定集成点插入调用，不重排既有逻辑）
- 修改：`backend/agents/layer3_recommendation/notifier.py`（仅新增渲染函数与插入点，不改既有区块）
- 修改：`backend/agents/layer3_recommendation/agent.py`（仅给 execute 增加 backtest 参数透传）
- 修改：`config/settings.py`、`backend/services/runtime_config.py`（仅新增配置项，不改既有默认值）
- 修改：`requirements.txt`（仅新增 anthropic 一行）

### 严禁触碰（黑名单）
1. 严禁修改评分逻辑与权重：`scoring.py`、`layer2_signal_engine/skills/` 全部文件、`factorWeights` 配置。
2. 严禁修改审核逻辑：`layer4_audit/` 全部文件。
3. 严禁修改数据采集：`layer1_data_collector/` 全部文件、`events/engine.py`。
4. 严禁修改国家队区块、涨停池表、指数头部等既有邮件区块的 HTML/文案/样式。
5. 严禁修改任何 `plugins/` 下的代码。
6. 严禁修改前端 `frontend/`、`docs/`、`render.yaml`（本次纯后端）。
7. 严禁改动与本方案无关的任何函数签名、变量名、导入顺序、格式化。

### 行为约束
- 只做「新增」和「在指定锚点插入」，不做「重构」「优化」「清理」既有代码——即便你认为它写得不好。
- 既有函数如需扩展，只允许**新增可选参数并给默认值**（保持向后兼容），不改已有参数。
- 若发现方案本身有缺陷或与现有代码冲突，**停下来说明问题，不要自行扩大改动范围**。
- 提交时必须附「改动文件清单 + 每个文件的改动行数」，任何不在白名单的文件出现在 diff 里，即为越界。
- 每个修改点旁用注释标明 `# [邮件升级] xxx`，方便审查快速定位新增代码。
- `screening_service.py` 是 699 行核心编排文件，**只准在两处指定锚点插入调用，其余一行都不许动**。

### 自检问题（提交前必须回答“是”）
- [ ] 我的 diff 是否只涉及白名单文件？
- [ ] 我是否没有改动任何评分/审核/采集/国家队相关代码？
- [ ] 我是否只新增、没有重构或删除既有逻辑？
- [ ] 既有函数的原有参数和行为是否 100% 保持不变？

---

## 0. 背景与铁律

项目：A 股尾盘涨停回撤选股系统。三层 Agent + Layer2.5 审核。
本次目标：把每日推荐邮件从「看好」升级为「可执行 + 可解读 + 可信」。

**必须遵守的既有约定（违反即打回）：**
1. 时间一律用 `BEIJING_TZ = timezone(timedelta(hours=8))`，禁止裸 `datetime.now()`。
2. 所有外部 HTTP 调用必须带 timeout。
3. 独立请求用 `asyncio.gather` 并行，用 `Semaphore` 限流。
4. 每个新增能力必须有降级路径：任一新模块失败都不能阻断原邮件正常发送。
5. 不改动：评分权重、审核逻辑、涨停池采集、国家队区块。全部只做增量叠加。
6. 所有新增对外文案末尾保留免责声明「仅供参考，不构成投资建议」。
7. 本次纯后端，不动 JS/CSS。禁止 emoji，图形用文字/色块。

---

## 1. 现有关键接口（施工时依赖，勿改签名）

### 1.1 ScoredStock（`backend/agents/layer2_signal_engine/scoring.py`）
```python
@dataclass
class ScoredStock:
    code: str
    name: str
    zt_date: str
    ref_price: float
    current_price: float
    drop_pct: float
    factor_scores: Dict[str, SkillResult]   # key: pullback/volume_trend/.../event_bonus
    total_score: float
    event_impact: float
    adjusted_score: float      # 0-100，最终得分
    rank: int
    recommendation: str        # STRONG_BUY / BUY / WATCH / PASS
    extra: Dict[str, Any]      # 已含 added_date、price_history
```
`extra["price_history"]` 结构（每行）：
`{"date": "YYYY-MM-DD", "close": float, "change_pct": float, "drawdown_pct": float}`

### 1.2 主链路（`backend/services/screening_service.py::run_full_pipeline`）
- 约 425 行：`historical, events, principal_capital_map = await asyncio.gather(...)`
  → `historical: Dict[code, pd.DataFrame]`，K线列名为中文「收盘/最高/最低/成交量/开盘」。
- 约 592 行：`scored = engine.evaluate(...)` 产出 List[ScoredStock]。
- 约 598-614 行：审核 + 降级。
- 约 631-639 行：`recom.execute(scored, target_date, index_gain, zt_list=..., dry_run=..., audit_results=..., zt_meta=..., index_snapshot=..., national_team=...)`
- **新模块的挂载点：在第 614 行审核降级完成之后、第 631 行 recom.execute 之前**，对 scored 做增量填充（写入 `s.extra`）。

### 1.3 邮件渲染（`backend/agents/layer3_recommendation/notifier.py`）
- `_html_recommendation_section(level_stocks, label, color, audit_results, target_date)`：单只卡片渲染在此，约 793-859 行。新增区块插入到 `history_html` 之后、`factors_html` 之前。
- `_build_text_content(...)`：纯文本降级版，约 534-572 行的单只循环内同步补齐。
- `_build_html_content(...)`：约 617 行 `_html_screening_summary` 之后，插入「战法历史表现」卡片。

### 1.4 配置
- `config/settings.py`（Pydantic Settings）+ `backend/services/runtime_config.py::get_effective_config()["config"]`。
- runtime_config 已有 `strategy`（含 trackingDays）、`notification`、`ztSort`、`factorWeights` 等键。

---

## 2. 模块 A：可执行操作计划（纯规则，无外部依赖）

### 2.1 新建 `backend/services/indicators.py`
纯函数，输入 pd.DataFrame（中文列名），输出标量。全部对空/短数据返回 None，不抛异常。

```python
def atr(hist, period=14) -> float | None      # 真实波幅均值
def recent_low(hist, window=20) -> float | None    # 近 window 日最低
def recent_high(hist, window=20) -> float | None   # 近 window 日最高
def ma(hist, period) -> float | None               # 收盘均线
```
实现要点：DataFrame 少于 period+1 行返回 None；用 `收盘/最高/最低` 列，缺列返回 None。

### 2.2 新建 `backend/services/action_planner.py`
```python
def build_action_plan(stock: ScoredStock, hist: pd.DataFrame | None) -> dict | None:
    """返回操作计划 dict；数据不足返回 None（邮件端渲染为'数据不足，暂不给出操作计划'）"""
```
返回结构（字段名固定，邮件端依赖）：
```python
{
  "buy_low": float, "buy_high": float,      # 建议买入区间
  "support": float,                          # 参考支撑位
  "stop_loss": float,                        # 止损位
  "target_1": float, "target_2": float,      # 两档目标价
  "risk_reward": float,                      # 盈亏比 = (target_1 - buy_mid)/(buy_mid - stop_loss)
  "position": str,                           # "轻仓(≤10%)" / "半仓(20-30%)" / "重仓(40%+)"
  "atr_pct": float,                          # ATR/现价，波动率参考
  "warnings": list[str],                     # 如 ["盈亏比偏低(<1.5)"]
}
```
计算规则：
- `support = max(x for x in [recent_low(20), ma(10), ma(20)] if x and x < current_price)`，无则用 `current_price*0.97`。
- `buy_mid = current_price`；`buy_low = current_price*0.99`；`buy_high = current_price*1.01`。
- `stop_loss = max(support*0.98, current_price - 1.5*atr14)`；须 < buy_low，否则用 `current_price*0.95`。
- `target_1 = ref_price`（涨停参考价回补）；`target_2 = max(recent_high(60), ref_price*1.05)`。
- `risk_reward` 保留 2 位；<1.5 追加 warning。
- `position`：`score>=70 且 rr>=2 且 atr_pct<0.06` → 重仓；`score>=55 且 rr>=1.5` → 半仓；否则轻仓。

### 2.3 集成
在 screening_service 第 614 行后新增循环：
```python
# [邮件升级] 生成可执行操作计划
if runtime_config.get("actionPlan", {}).get("enabled", True):
    for s in scored:
        if s.recommendation in ("STRONG_BUY", "BUY", "WATCH"):
            try:
                plan = build_action_plan(s, historical.get(s.code))
                if plan:
                    s.extra["action_plan"] = plan
            except Exception as e:
                logger.warning(f"操作计划生成失败 {s.code}: {e}")
```

### 2.4 邮件渲染（notifier.py）
在 `_html_recommendation_section` 单只卡片内新增 `_html_action_plan(plan)`；在 `_build_text_content` 内新增文本行。缺失时渲染灰字提示。

---

## 3. 模块 B：策略胜率回测（滚动模拟，零持久化）

> Render 免费层文件系统临时，**禁止依赖历史推荐落库**。用历史 K 线现算，可复现。

### 3.1 新建 `backend/services/backtest.py`
```python
def run_rolling_backtest(historical: dict[str, pd.DataFrame],
                         strategy_cfg: dict,
                         hold_days_list=(1, 3, 5)) -> dict:
    """
    遍历每只股历史K线，识别所有'涨停(≈+9.8%~10%或+19.8%~20%)后回撤3-10%'触发点，
    模拟买入后 T+1/T+3/T+5 收益，汇总统计。
    """
```
返回结构（邮件端依赖）：
```python
{
  "sample_count": int,           # 触发样本数
  "win_rate": {1: float, 3: float, 5: float},   # 各持有期胜率(%)
  "avg_return": {1: float, 3: float, 5: float},  # 平均收益(%)
  "avg_max_drawdown": float,     # 平均最大回撤(%)
  "strong_buy_win_rate": float | None,  # 仅高分档，样本不足时 None
  "generated_at": "YYYY-MM-DD",
  "disclaimer": "基于历史K线样本统计，非未来收益承诺",
}
```
要点：
- **严禁前视偏差**：识别触发点只能用触发日当天及之前的数据，收益统计用触发日之后的 T+N，禁止用未来数据反推触发条件。
- 样本 < 20 时在 disclaimer 标注「样本偏少，仅供参考」。
- 缓存当日一次（模块级 dict，key=target_date）避免重复计算。

### 3.2 集成
在 screening_service Layer3 之前调用一次，结果透传给 `recom.execute(..., backtest=...)`（`RecommendationAgent.execute` 与 `notifier.send_notification` 增加 `backtest` 参数，默认 None）。

### 3.3 邮件渲染
`_html_screening_summary` 之后插入 `_html_backtest_card(backtest)`：展示样本数、T+1/T+3/T+5 胜率与均收益、最大回撤、免责。text 版同步。

---

## 4. 模块 C：LLM 智能解读（Claude API + 规则降级）

### 4.1 新建 `backend/services/llm_insight.py`
```python
async def generate_insights(stocks: list[ScoredStock],
                            backtest: dict | None) -> dict[str, dict]:
    """
    仅对 STRONG_BUY / BUY 生成。返回 {code: {"reason":..,"risk":..,"operation":..}}
    """
```
实现要点：
- 模型默认 `claude-haiku-4-5-20251001`，从 runtime_config `llm.model` 读取。
- 用官方 `anthropic` 异步客户端；`Semaphore(3)` 并发；单请求 15s timeout。
- 输入 prompt 组装：因子明细（跳过 event_bonus）+ drop_pct + action_plan + audit 结果 + backtest 胜率。要求模型输出严格 JSON：`{"reason","risk","operation"}`，中文，每段 ≤60 字。
- **降级链**：无 `ANTHROPIC_API_KEY` / 抛异常 / 超时 / JSON 解析失败 → 调用 `_rule_based_insight(stock)`（用因子得分 top3 与 action_plan 拼模板文案），保证每只股都有解读。
- 同日同 code 缓存。

### 4.2 集成
在 screening_service 模块 A、B 完成之后：
```python
# [邮件升级] LLM 智能解读（带规则降级）
insights = {}
if runtime_config.get("llm", {}).get("enabled", False):
    insights = await generate_insights(scored, backtest_result)
for s in scored:
    if s.code in insights:
        s.extra["insight"] = insights[s.code]
    elif s.recommendation in ("STRONG_BUY", "BUY"):
        s.extra["insight"] = rule_based_insight(s)  # 兜底
```
注意：`generate_insights` 是 async，须在 `run_full_pipeline`（已是 async）内 await。

### 4.3 邮件渲染
卡片内 action_plan 下方新增 `_html_insight(insight)`：三段「推荐理由 / 风险提示 / 操作逻辑」。text 版同步。

---

## 5. 配置与依赖

### 5.1 requirements.txt
新增 `anthropic>=0.40.0`（DeepSeek 确认当时最新稳定版）。

### 5.2 config/settings.py + runtime_config 默认值
```python
"actionPlan": {"enabled": True},
"backtest": {"enabled": True, "holdDays": [1, 3, 5]},
"llm": {"enabled": False, "model": "claude-haiku-4-5-20251001"},
```
（llm 默认关闭，配好 key 再开）

### 5.3 环境变量（Render Dashboard）
`ANTHROPIC_API_KEY` —— LLM 解读用，缺失自动降级规则文案。

---

## 6. 验收标准（DeepSeek 自测）

1. `python -m backend.main --dry-run` 全链路跑通，无异常，日志显示三模块执行/降级状态。
2. 关闭 llm.enabled：邮件仍有规则版解读，不报错。
3. 断网/无 API KEY：LLM 降级、回测正常、操作计划正常，邮件照发。
4. 某只股历史 K 线缺失：action_plan=None、邮件渲染灰字提示，不崩。
5. text 版与 html 版内容一致（新增区块都补齐）。
6. 时区、timeout、Semaphore、try/except 四条铁律全覆盖。

---

## 7. 交付物清单

- 新增：`backend/services/indicators.py`、`action_planner.py`、`backtest.py`、`llm_insight.py`
- 修改：`screening_service.py`（集成点）、`notifier.py`（渲染）、`layer3_recommendation/agent.py`（透传 backtest 参数）、`config/settings.py`、`runtime_config.py`、`requirements.txt`
- 不改：评分/审核/采集/国家队相关文件
- 提交附：改动文件清单 + 每个文件改动行数（供越界核对）

---

## 附：验证方（Claude）审查重点

1. **降级链是否真闭环** — `llm_insight` 和 `action_planner` 的 `except` 是否吞掉异常并回落，而非把异常抛回主链路阻断邮件。
2. **回测口径是否诚实** — 涨停识别阈值、样本数下限、是否用了未来函数（前视偏差）。
3. **四条铁律机械核对** — 全局 grep `datetime.now()` 无参、无 timeout 的 requests/httpx、异步里的同步阻塞调用。
4. **集成点位置** — 是否在审核降级之后填充 extra（若在之前，仓位会基于未降级得分算）。
5. **text/html 一致性** — 新区块是否两版都补。
6. **成本护栏** — LLM 是否只对 STRONG_BUY/BUY 调用、Semaphore 是否生效。
7. **作用域核对** — diff 文件清单先比对白名单，任何越界文件直接打回。
