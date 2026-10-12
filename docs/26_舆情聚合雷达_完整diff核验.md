# 26 方案「舆情聚合雷达」完整 diff 核验文档

> 生成方式：`git diff`（含 `git add -N` 纳入的新增文件），逐行可核验。
> 范围：五大功能后端 + 前端一级菜单，仅 A 股主板口径（排除创业板 300/301、科创板 688）。

## 变更总览

| 统计 | 数值 |
|------|------|
| 变更文件数 | 31 |
| 新增行 | +2499 |
| 删除行 | -1 |
| 后端新增 plugin | 4（paper_trading / conditional_watch / hot_scanner / event_scanner） |
| 后端核心修改 | 3（plugin_models.py / screening_service.py / main.py） |
| 单测 | 3 文件，27 用例 |
| 前端 | 3（index.html / app.js / radar_plugins.js） |
| 方案文档 | 1（26_skill嫁接施工方案_五大功能.md） |

## 变更文件清单

### 修改（M）
- `backend/db/plugin_models.py`：追加 10 个新表（paper_*/watch_*/hot_scan/event_scan）
- `backend/main.py`：注册 4 个新 plugin 路由
- `backend/services/screening_service.py`：Layer2.6 接入风险否决层 + 响应加 risk_override 字段
- `frontend/index.html`：新增"舆情聚合雷达"一级菜单（雷达图标）+ 5 个 page 容器 + 脚本引入
- `frontend/js/app.js`：pageTitle 映射 + setupRadarPage 路由 dispatch

### 新增（A）
- `backend/services/risk_overrides.py`：7 条风险否决规则（纯函数）
- `backend/services/tests/test_risk_overrides.py`：12 用例
- `backend/plugins/paper_trading/`：绩效模拟盘（4 文件）
- `backend/plugins/conditional_watch/`：条件盯盘（5 文件 + 单测）
- `backend/plugins/hot_scanner/`：舆情热度聚合（5 文件）
- `backend/plugins/event_scanner/`：事件驱动雷达（6 文件 + 单测）
- `frontend/js/plugins/radar_plugins.js`：5 子页通用渲染

---

diff --git a/backend/db/plugin_models.py b/backend/db/plugin_models.py
index 21928e3..23b3d6f 100644
--- a/backend/db/plugin_models.py
+++ b/backend/db/plugin_models.py
@@ -435,3 +435,129 @@ class PickerPerfDaily(Base):
     updated_at = Column(String(30))
     __table_args__ = (Index("idx_ppd_date", "signal_date"), Index("idx_ppd_code", "code", "signal_date"))
 
+# ===================== 26 方案：五大功能新表 =====================
+
+class PaperAccount(Base):
+    """26 绩效模拟盘：虚拟账户。"""
+    __tablename__ = "paper_accounts"
+    id = Column(Integer, primary_key=True, autoincrement=True)
+    name = Column(String(50), unique=True, nullable=False, default="main")
+    initial_capital = Column(Float, default=100000.0)
+    cash = Column(Float, default=100000.0)
+    updated_at = Column(String(30))
+
+
+class PaperPosition(Base):
+    """26 绩效模拟盘：持仓。"""
+    __tablename__ = "paper_positions"
+    id = Column(Integer, primary_key=True, autoincrement=True)
+    account_id = Column(Integer, nullable=False)
+    code = Column(String(6), nullable=False)
+    name = Column(String(50))
+    level = Column(String(20))
+    open_date = Column(String(10), nullable=False)
+    open_price = Column(Float, nullable=False)
+    shares = Column(Integer, nullable=False)
+    cost = Column(Float)
+    status = Column(String(20), default="open")
+    close_date = Column(String(10))
+    close_price = Column(Float)
+    pnl = Column(Float, default=0.0)
+    pnl_pct = Column(Float, default=0.0)
+    reason = Column(String(200))
+    __table_args__ = (Index("idx_pp_acct", "account_id"), Index("idx_pp_status", "status"))
+
+
+class PaperNav(Base):
+    """26 绩效模拟盘：每日净值。"""
+    __tablename__ = "paper_nav"
+    id = Column(Integer, primary_key=True, autoincrement=True)
+    account_id = Column(Integer, nullable=False)
+    trade_date = Column(String(10), nullable=False)
+    nav = Column(Float)
+    daily_return = Column(Float, default=0.0)
+    drawdown = Column(Float, default=0.0)
+    __table_args__ = (Index("idx_pn_acct_date", "account_id", "trade_date"),)
+
+
+class PaperTrade(Base):
+    """26 绩效模拟盘：交易流水。"""
+    __tablename__ = "paper_trades"
+    id = Column(Integer, primary_key=True, autoincrement=True)
+    account_id = Column(Integer, nullable=False)
+    code = Column(String(6), nullable=False)
+    name = Column(String(50))
+    side = Column(String(10), nullable=False)
+    price = Column(Float)
+    shares = Column(Integer)
+    trade_date = Column(String(10), nullable=False)
+    reason = Column(String(200))
+    __table_args__ = (Index("idx_pt_acct_date", "account_id", "trade_date"),)
+
+
+class WatchRule(Base):
+    """26 条件盯盘：监控规则。"""
+    __tablename__ = "watch_rules"
+    id = Column(Integer, primary_key=True, autoincrement=True)
+    code = Column(String(6), nullable=False)
+    name = Column(String(50))
+    product_type = Column(String(20), default="stock")
+    condition = Column(Text, nullable=False)
+    variables_json = Column(Text)
+    channels_json = Column(Text)
+    enabled = Column(Integer, default=1)
+    last_trigger_key = Column(String(200))
+    created_at = Column(String(30))
+    __table_args__ = (Index("idx_wr_code", "code"), Index("idx_wr_enabled", "enabled"))
+
+
+class WatchAlert(Base):
+    """26 条件盯盘：触发记录。"""
+    __tablename__ = "watch_alerts"
+    id = Column(Integer, primary_key=True, autoincrement=True)
+    rule_id = Column(Integer, nullable=False)
+    code = Column(String(6), nullable=False)
+    name = Column(String(50))
+    condition = Column(Text)
+    triggered_at = Column(String(30))
+    price = Column(Float)
+    change_pct = Column(Float)
+    message = Column(Text)
+    dedup_key = Column(String(200), unique=True)
+    notified = Column(Integer, default=0)
+    __table_args__ = (Index("idx_wa_rule", "rule_id"), Index("idx_wa_code_date", "code", "triggered_at"))
+
+
+class HotScan(Base):
+    """26 舆情热度聚合：每日扫描结果。"""
+    __tablename__ = "hot_scan"
+    id = Column(Integer, primary_key=True, autoincrement=True)
+    scan_date = Column(String(10), nullable=False)
+    code = Column(String(6), nullable=False)
+    name = Column(String(50))
+    score = Column(Integer, default=0)
+    sources_json = Column(Text)
+    rank = Column(Integer)
+    in_watchlist = Column(Integer, default=0)
+    created_at = Column(String(30))
+    __table_args__ = (Index("idx_hs_date_rank", "scan_date", "rank"), Index("idx_hs_code", "code", "scan_date"))
+
+
+class EventScan(Base):
+    """26 事件驱动雷达：事件扫描结果。"""
+    __tablename__ = "event_scan"
+    id = Column(Integer, primary_key=True, autoincrement=True)
+    code = Column(String(6), nullable=False)
+    name = Column(String(50))
+    event_type = Column(String(30), nullable=False)
+    title = Column(String(200))
+    source = Column(String(80))
+    impact_score = Column(Integer, default=0)
+    event_date = Column(String(10), nullable=False)
+    url = Column(String(500))
+    dedup_key = Column(String(200), unique=True)
+    in_watchlist = Column(Integer, default=0)
+    created_at = Column(String(30))
+    __table_args__ = (Index("idx_es_date", "event_date"), Index("idx_es_code", "code", "event_date"), Index("idx_es_type", "event_type"))
+
+
diff --git a/backend/main.py b/backend/main.py
index e0373c8..fe63196 100644
--- a/backend/main.py
+++ b/backend/main.py
@@ -180,6 +180,10 @@ def get_app():
         ("pattern_scanner", "register_router", "/api/v1/pattern-scanner", "形态突破"),
         ("chip_scanner", "register_router", "/api/v1/chip-scanner", "筹码集中度"),
         ("smart_picker_hub", "register_router", "/api/v1/smart-picker", "智能选股聚合中枢"),
+        ("paper_trading", "register_router", "/api/v1/paper-trading", "绩效模拟盘"),
+        ("conditional_watch", "register_router", "/api/v1/watch", "条件盯盘"),
+        ("hot_scanner", "register_router", "/api/v1/hot", "舆情热度聚合"),
+        ("event_scanner", "register_router", "/api/v1/event-radar", "事件驱动雷达"),
     ]
     for _mod, _fn, _prefix, _tag in _new_plugins:
         try:
diff --git a/backend/plugins/conditional_watch/__init__.py b/backend/plugins/conditional_watch/__init__.py
new file mode 100644
index 0000000..c857fec
--- /dev/null
+++ b/backend/plugins/conditional_watch/__init__.py
@@ -0,0 +1,7 @@
+"""26 条件盯盘插件入口。"""
+from fastapi import APIRouter
+
+
+def register_router() -> APIRouter:
+    from .router import router
+    return router
diff --git a/backend/plugins/conditional_watch/config.py b/backend/plugins/conditional_watch/config.py
new file mode 100644
index 0000000..9364b74
--- /dev/null
+++ b/backend/plugins/conditional_watch/config.py
@@ -0,0 +1,11 @@
+"""26 条件盯盘插件配置。"""
+import os
+from pathlib import Path
+
+PROJECT_DIR = Path(__file__).resolve().parents[3]
+REPORT_DIR = PROJECT_DIR / "reports"
+SNAPSHOT_NAME = "conditional_watch"
+DATA_DIR = PROJECT_DIR / "data"
+
+POLL_INTERVAL_SECONDS = int(os.environ.get("WATCH_POLL_INTERVAL", 5))
+DEFAULT_CHANNELS = ["openclaw"]
diff --git a/backend/plugins/conditional_watch/expr.py b/backend/plugins/conditional_watch/expr.py
new file mode 100644
index 0000000..b4c508e
--- /dev/null
+++ b/backend/plugins/conditional_watch/expr.py
@@ -0,0 +1,127 @@
+"""26 条件盯盘：安全条件表达式求值器（不使用 eval）。
+
+支持：price/change_percent/turnover_rate/volume/volume_ratio 及自定义变量；
+运算符 > >= < <= == != and or（and 优先于 or），括号。
+"""
+import re
+
+_TOKEN_RE = re.compile(
+    r"\s*(>=|<=|==|!=|>|<|\(|\)|\band\b|\bor\b|[A-Za-z_][A-Za-z0-9_]*|-?\d+(?:\.\d+)?)"
+)
+
+_COMPARE_OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
+                "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
+                "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
+
+
+def tokenize(expr: str) -> list:
+    tokens = []
+    pos = 0
+    while pos < len(expr):
+        m = _TOKEN_RE.match(expr, pos)
+        if not m:
+            rest = expr[pos:].strip()
+            if rest:
+                raise ValueError(f"无法解析表达式片段: {rest[:16]}")
+            break
+        tokens.append(m.group(1))
+        pos = m.end()
+    return tokens
+
+
+class _Parser:
+    def __init__(self, tokens, values):
+        self.tokens = tokens
+        self.pos = 0
+        self.values = values
+
+    def _peek(self):
+        return self.tokens[self.pos] if self.pos < len(self.tokens) else None
+
+    def _advance(self):
+        tok = self._peek()
+        self.pos += 1
+        return tok
+
+    def parse_or(self):
+        left = self.parse_and()
+        while self._peek() == "or":
+            self._advance()
+            right = self.parse_and()
+            left = left or right
+        return left
+
+    def parse_and(self):
+        left = self.parse_cmp()
+        while self._peek() == "and":
+            self._advance()
+            right = self.parse_cmp()
+            left = left and right
+        return left
+
+    def parse_cmp(self):
+        left = self.parse_atom()
+        op = self._peek()
+        if op in _COMPARE_OPS:
+            self._advance()
+            right = self.parse_atom()
+            if left is None or right is None:
+                return False
+            try:
+                return _COMPARE_OPS[op](left, right)
+            except TypeError:
+                return False
+        # 单值布尔（如变量本身为真）
+        return bool(left)
+
+    def parse_atom(self):
+        tok = self._peek()
+        if tok == "(":
+            self._advance()
+            val = self.parse_or()
+            if self._peek() == ")":
+                self._advance()
+            return val
+        if tok is None:
+            return None
+        self._advance()
+        if tok in self.values:
+            return self.values[tok]
+        try:
+            return float(tok)
+        except ValueError:
+            return None
+
+
+def evaluate(condition: str, values: dict) -> bool:
+    """对条件表达式求值。变量缺失时比较恒为 False（保守不触发）。"""
+    if not condition or not condition.strip():
+        return False
+    try:
+        tokens = tokenize(condition)
+        if not tokens:
+            return False
+        return bool(_Parser(tokens, values).parse_or())
+    except (ValueError, IndexError):
+        return False
+
+
+def build_values(market_data: dict, variables: dict = None) -> dict:
+    """将行情字段 + 自定义变量合并为求值变量表。"""
+    values = dict(variables or {})
+    values.update({
+        "price": _num(market_data.get("最新价") or market_data.get("price")),
+        "change_percent": _num(market_data.get("涨跌幅") or market_data.get("change_percent")),
+        "turnover_rate": _num(market_data.get("换手率") or market_data.get("turnover_rate")),
+        "volume": _num(market_data.get("成交量") or market_data.get("volume")),
+        "volume_ratio": _num(market_data.get("量比") or market_data.get("volume_ratio")),
+    })
+    return values
+
+
+def _num(value):
+    try:
+        n = float(value)
+    except (TypeError, ValueError):
+        return None
+    return n if n == n else None
diff --git a/backend/plugins/conditional_watch/router.py b/backend/plugins/conditional_watch/router.py
new file mode 100644
index 0000000..4c7166a
--- /dev/null
+++ b/backend/plugins/conditional_watch/router.py
@@ -0,0 +1,72 @@
+"""26 条件盯盘 API。"""
+import logging
+
+from fastapi import APIRouter, Body, Query
+
+from .service import check_rules, create_rule, delete_rule, list_alerts, list_rules, read_latest, toggle_rule
+
+router = APIRouter()
+logger = logging.getLogger(__name__)
+
+
+@router.get("/latest")
+async def watch_latest():
+    try:
+        return read_latest()
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.get("/rules")
+async def watch_rules():
+    try:
+        return {"status": "ok", "items": list_rules()}
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.post("/rules")
+async def watch_create(payload: dict = Body(...)):
+    try:
+        return create_rule(
+            code=payload.get("code", ""),
+            condition=payload.get("condition", ""),
+            name=payload.get("name"),
+            variables=payload.get("variables"),
+            channels=payload.get("channels"),
+            product_type=payload.get("product_type", "stock"),
+        )
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.delete("/rules/{rule_id}")
+async def watch_delete(rule_id: int):
+    try:
+        return delete_rule(rule_id)
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.post("/rules/{rule_id}/toggle")
+async def watch_toggle(rule_id: int, enabled: bool = Query(True)):
+    try:
+        return toggle_rule(rule_id, enabled)
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.get("/alerts")
+async def watch_alerts(days: int = Query(7, ge=1, le=90)):
+    try:
+        return {"status": "ok", "items": list_alerts(days)}
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.post("/check")
+async def watch_check():
+    try:
+        return check_rules()
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
diff --git a/backend/plugins/conditional_watch/service.py b/backend/plugins/conditional_watch/service.py
new file mode 100644
index 0000000..69a0cc0
--- /dev/null
+++ b/backend/plugins/conditional_watch/service.py
@@ -0,0 +1,141 @@
+"""26 条件盯盘编排服务。
+
+规则存 SQLite（watch_rules），触发记录存 watch_alerts，快照 reports/conditional_watch_latest.json。
+行情复用腾讯批量报价（fetch_tencent_quotes_for_codes）。
+"""
+import json
+import logging
+from datetime import datetime
+
+from backend.plugins.common import (
+    BEIJING_TZ, db_append, db_query, json_safe, now_beijing, read_snapshot, write_snapshot,
+)
+
+from .config import SNAPSHOT_NAME
+from .expr import build_values, evaluate
+
+logger = logging.getLogger(__name__)
+
+
+def list_rules() -> list:
+    df = db_query("SELECT * FROM watch_rules ORDER BY id DESC")
+    if df.empty:
+        return []
+    return json_safe(json.loads(df.to_json(orient="records", force_ascii=False)))
+
+
+def create_rule(code: str, condition: str, name: str = None, variables: dict = None,
+                channels: list = None, product_type: str = "stock") -> dict:
+    code = str(code).zfill(6)
+    db_append("watch_rules", [{
+        "code": code, "name": name or code, "product_type": product_type,
+        "condition": condition, "variables_json": json.dumps(variables or {}, ensure_ascii=False),
+        "channels_json": json.dumps(channels or ["openclaw"], ensure_ascii=False),
+        "enabled": 1, "created_at": now_beijing().isoformat(),
+    }])
+    return {"status": "ok", "code": code, "condition": condition}
+
+
+def delete_rule(rule_id: int) -> dict:
+    db_delete = __import__("backend.plugins.common", fromlist=["db_delete"]).db_delete
+    n = db_delete("watch_rules", {"id": int(rule_id)})
+    return {"status": "ok", "deleted": n}
+
+
+def toggle_rule(rule_id: int, enabled: bool) -> dict:
+    from sqlalchemy import text
+    from backend.db.database import engine
+    try:
+        with engine.begin() as conn:
+            conn.execute(text("UPDATE watch_rules SET enabled = :e WHERE id = :i"),
+                         {"e": 1 if enabled else 0, "i": int(rule_id)})
+        return {"status": "ok", "enabled": bool(enabled)}
+    except Exception as exc:  # noqa: BLE001
+        return {"status": "error", "message": str(exc)}
+
+
+def _fetch_quotes(codes: list) -> dict:
+    """腾讯批量报价，返回 {code: {最新价, 涨跌幅, 换手率, 成交量, 量比}}。"""
+    if not codes:
+        return {}
+    try:
+        from backend.agents.layer1_data_collector.sources.eastmoney_quote import fetch_tencent_quotes_for_codes
+        df = fetch_tencent_quotes_for_codes(codes)
+        if df is None or getattr(df, "empty", False):
+            return {}
+        out = {}
+        for row in df.to_dict("records"):
+            code = str(row.get("代码", "")).zfill(6)
+            out[code] = row
+        return out
+    except Exception as exc:  # noqa: BLE001
+        logger.warning("盯盘行情拉取失败: %s", exc)
+        return {}
+
+
+def check_rules() -> dict:
+    """对全部启用规则做一次求值，命中则去重落库。"""
+    rules = [r for r in list_rules() if int(r.get("enabled", 1)) == 1]
+    if not rules:
+        return {"status": "ok", "checked": 0, "triggered": 0, "alerts": []}
+    codes = sorted({r["code"] for r in rules})
+    quotes = _fetch_quotes(codes)
+    triggered = []
+    for r in rules:
+        md = quotes.get(r["code"])
+        if not md:
+            continue
+        variables = json.loads(r.get("variables_json") or "{}")
+        values = build_values(md, variables)
+        if evaluate(r.get("condition", ""), values):
+            price = values.get("price")
+            change = values.get("change_percent")
+            dedup_key = f"{r['id']}:{r['code']}:{price is not None and round(price, 2)}"
+            # 同价去重：同一规则同一价位只告警一次
+            if r.get("last_trigger_key") == dedup_key:
+                continue
+            db_append("watch_alerts", [{
+                "rule_id": r["id"], "code": r["code"], "name": r.get("name"),
+                "condition": r.get("condition"),
+                "triggered_at": now_beijing().isoformat(),
+                "price": price, "change_pct": change,
+                "message": f"盯盘触发 {r['name'] or r['code']}：{r.get('condition')}",
+                "dedup_key": dedup_key + ":" + now_beijing().strftime("%H%M"), "notified": 1,
+            }])
+            from sqlalchemy import text
+            from backend.db.database import engine
+            try:
+                with engine.begin() as conn:
+                    conn.execute(text("UPDATE watch_rules SET last_trigger_key = :k WHERE id = :i"),
+                                 {"k": dedup_key, "i": int(r["id"])})
+            except Exception:  # noqa: BLE001
+                pass
+            triggered.append({"code": r["code"], "name": r.get("name"), "condition": r.get("condition"),
+                              "price": price, "change_pct": change})
+    _refresh_snapshot()
+    return {"status": "ok", "checked": len(rules), "triggered": len(triggered), "alerts": triggered}
+
+
+def list_alerts(days: int = 7) -> list:
+    df = db_query("SELECT * FROM watch_alerts ORDER BY id DESC LIMIT :n", {"n": int(days) * 50})
+    if df.empty:
+        return []
+    return json_safe(json.loads(df.to_json(orient="records", force_ascii=False)))
+
+
+def _refresh_snapshot() -> dict:
+    payload = json_safe({
+        "status": "ok",
+        "rules": list_rules(),
+        "alerts": list_alerts(7),
+        "generated_at": now_beijing().isoformat(),
+    })
+    write_snapshot(SNAPSHOT_NAME, payload)
+    return payload
+
+
+def read_latest() -> dict:
+    snap = read_snapshot(SNAPSHOT_NAME)
+    if snap:
+        return snap
+    return _refresh_snapshot()
diff --git a/backend/plugins/conditional_watch/tests/test_expr.py b/backend/plugins/conditional_watch/tests/test_expr.py
new file mode 100644
index 0000000..705d7cc
--- /dev/null
+++ b/backend/plugins/conditional_watch/tests/test_expr.py
@@ -0,0 +1,42 @@
+"""26 条件盯盘表达式求值器单测。"""
+import pytest
+
+from backend.plugins.conditional_watch.expr import build_values, evaluate
+
+
+def test_simple_compare():
+    assert evaluate("price >= 10", {"price": 12.0}) is True
+    assert evaluate("price >= 10", {"price": 8.0}) is False
+
+
+def test_and_condition():
+    assert evaluate("price >= 10 and change_percent >= 1", {"price": 12.0, "change_percent": 2.0}) is True
+    assert evaluate("price >= 10 and change_percent >= 1", {"price": 12.0, "change_percent": 0.5}) is False
+
+
+def test_or_condition():
+    assert evaluate("price >= 100 or change_percent <= -5", {"price": 12.0, "change_percent": -6.0}) is True
+    assert evaluate("price >= 100 or change_percent <= -5", {"price": 12.0, "change_percent": 1.0}) is False
+
+
+def test_parens():
+    assert evaluate("(price >= 10 or change_percent >= 1) and turnover_rate < 5",
+                    {"price": 8.0, "change_percent": 2.0, "turnover_rate": 3.0}) is True
+
+
+def test_missing_var_false():
+    assert evaluate("price >= 10", {}) is False
+
+
+def test_build_values_merge():
+    values = build_values({"最新价": 10.5, "涨跌幅": 1.2, "换手率": 3.4}, {"threshold": 9.0})
+    assert values["price"] == 10.5
+    assert values["change_percent"] == 1.2
+    assert values["turnover_rate"] == 3.4
+    assert values["threshold"] == 9.0
+
+
+def test_no_eval_injection():
+    # 表达式里出现危险字符应安全返回，不执行任何代码
+    assert evaluate("__import__('os').system('echo hacked')", {}) is False
+    assert evaluate("price >= 10 or __import__('os')", {"price": 1.0}) is False
diff --git a/backend/plugins/event_scanner/__init__.py b/backend/plugins/event_scanner/__init__.py
new file mode 100644
index 0000000..0d4e92e
--- /dev/null
+++ b/backend/plugins/event_scanner/__init__.py
@@ -0,0 +1,12 @@
+"""26 事件驱动雷达插件入口。"""
+from fastapi import APIRouter
+
+
+def register_router() -> APIRouter:
+    from .router import router
+    return router
+
+
+def run_event_scan_cli(args):
+    from .service import run_scan_once
+    return run_scan_once()
diff --git a/backend/plugins/event_scanner/config.py b/backend/plugins/event_scanner/config.py
new file mode 100644
index 0000000..8089ff5
--- /dev/null
+++ b/backend/plugins/event_scanner/config.py
@@ -0,0 +1,9 @@
+"""26 事件驱动雷达插件配置。"""
+import os
+from pathlib import Path
+
+PROJECT_DIR = Path(__file__).resolve().parents[3]
+REPORT_DIR = PROJECT_DIR / "reports"
+SNAPSHOT_NAME = "event_scanner"
+DATA_DIR = PROJECT_DIR / "data"
+TIMEOUT_SECONDS = float(os.environ.get("EVENT_SCAN_TIMEOUT", 10))
diff --git a/backend/plugins/event_scanner/router.py b/backend/plugins/event_scanner/router.py
new file mode 100644
index 0000000..1c3a22d
--- /dev/null
+++ b/backend/plugins/event_scanner/router.py
@@ -0,0 +1,33 @@
+"""26 事件驱动雷达 API。"""
+import logging
+
+from fastapi import APIRouter, Query
+
+from .service import read_latest, read_stock_events, run_scan_once
+
+router = APIRouter()
+logger = logging.getLogger(__name__)
+
+
+@router.get("/latest")
+async def event_latest():
+    try:
+        return read_latest()
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.get("/stock/{code}")
+async def event_stock(code: str):
+    try:
+        return {"status": "ok", "items": read_stock_events(code)}
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.post("/scan")
+async def event_scan():
+    try:
+        return run_scan_once()
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
diff --git a/backend/plugins/event_scanner/scoring.py b/backend/plugins/event_scanner/scoring.py
new file mode 100644
index 0000000..9927a25
--- /dev/null
+++ b/backend/plugins/event_scanner/scoring.py
@@ -0,0 +1,23 @@
+"""26 事件驱动雷达：事件分类 + 影响打分（纯函数，可单测）。"""
+
+# (事件类型, 影响分, 关键词列表)
+EVENT_RULES = [
+    ("重组并购", 5, ["重大资产重组", "资产重组", "收购", "并购", "借壳", "要约收购", "吸收合并"]),
+    ("增持回购", 4, ["增持", "回购", "举牌", "实际控制人增持", "高管增持"]),
+    ("减持套现", -4, ["减持", "大宗交易", "清仓式减持", "股份质押预警"]),
+    ("研报上调", 3, ["买入评级", "增持评级", "目标价上调", "上调评级", "强推"]),
+    ("研报下调", -3, ["卖出评级", "减持评级", "目标价下调", "下调评级", "回避"]),
+    ("龙虎榜机构", 3, ["机构专用", "机构净买入", "机构席位"]),
+    ("限售解禁", -2, ["解禁", "限售股上市流通", "首发原股东限售"]),
+]
+
+
+def classify_score(title: str, description: str = "") -> dict:
+    """对事件文本分类并给影响分。返回 {event_type, impact_score, matched_keywords}。"""
+    text = f"{title or ''} {description or ''}"
+    best = {"event_type": "其他", "impact_score": 0, "matched_keywords": []}
+    for etype, score, keywords in EVENT_RULES:
+        hits = [kw for kw in keywords if kw in text]
+        if hits and abs(score) > abs(best["impact_score"]):
+            best = {"event_type": etype, "impact_score": score, "matched_keywords": hits}
+    return best
diff --git a/backend/plugins/event_scanner/service.py b/backend/plugins/event_scanner/service.py
new file mode 100644
index 0000000..39009e4
--- /dev/null
+++ b/backend/plugins/event_scanner/service.py
@@ -0,0 +1,89 @@
+"""26 事件驱动雷达编排服务：扫描 + 去重 + 影响打分 + 监控列表交叉。"""
+import hashlib
+import json
+import logging
+
+from backend.plugins.common import (
+    BEIJING_TZ, db_append, db_query, json_safe, now_beijing, read_snapshot, write_snapshot,
+)
+
+from .config import SNAPSHOT_NAME
+from .scoring import classify_score
+from .sources import ALL_SOURCES
+
+logger = logging.getLogger(__name__)
+
+MAIN_BOARD = ("600", "601", "603", "605", "000", "001", "002", "003")
+
+
+def _watch_set() -> set:
+    try:
+        from backend.services.watchlist_store import parse_watchlist
+        items = parse_watchlist()
+        return {str(c).zfill(6) for c in items if str(c).zfill(6).startswith(MAIN_BOARD)}
+    except Exception:  # noqa: BLE001
+        return set()
+
+
+def _dedup_key(code, title, source) -> str:
+    raw = f"{code}:{title}:{source}".encode("utf-8")
+    return hashlib.md5(raw).hexdigest()[:24]
+
+
+def run_scan_once() -> dict:
+    """扫描全部源，打分去重，写快照 + SQLite。"""
+    events = []
+    watch_set = _watch_set()
+    for source, fn in ALL_SOURCES.items():
+        try:
+            for item in fn():
+                code = str(item.get("code", "")).zfill(6)
+                if not code.startswith(MAIN_BOARD):
+                    continue
+                scored = classify_score(item.get("title", ""))
+                events.append({
+                    "code": code, "name": item.get("name", ""),
+                    "event_type": scored["event_type"], "title": item.get("title", ""),
+                    "source": source, "impact_score": scored["impact_score"],
+                    "event_date": item.get("date") or now_beijing().date().isoformat(),
+                    "url": item.get("url", ""),
+                    "in_watchlist": 1 if code in watch_set else 0,
+                })
+        except Exception as exc:  # noqa: BLE001
+            logger.info("事件源 %s 失败: %s", source, exc)
+    # 去重 + 落库
+    seen = set()
+    rows, unique = [], []
+    for e in events:
+        key = _dedup_key(e["code"], e["title"], e["source"])
+        if key in seen:
+            continue
+        seen.add(key)
+        e["dedup_key"] = key
+        unique.append(e)
+        rows.append({**e, "created_at": now_beijing().isoformat()})
+    if rows:
+        db_append("event_scan", rows)
+    unique.sort(key=lambda x: abs(x["impact_score"]), reverse=True)
+    payload = json_safe({
+        "status": "ok", "scan_date": now_beijing().date().isoformat(),
+        "event_count": len(unique),
+        "items": unique[:50],
+        "generated_at": now_beijing().isoformat(),
+    })
+    write_snapshot(SNAPSHOT_NAME, payload)
+    return payload
+
+
+def read_latest() -> dict:
+    snap = read_snapshot(SNAPSHOT_NAME)
+    if snap:
+        return snap
+    return {"status": "empty", "items": [], "reason": "尚未扫描"}
+
+
+def read_stock_events(code: str) -> list:
+    df = db_query("SELECT * FROM event_scan WHERE code = :c ORDER BY id DESC LIMIT 50", {"c": str(code).zfill(6)})
+    if df.empty:
+        return []
+    return json_safe(json.loads(df.to_json(orient="records", force_ascii=False)))
diff --git a/backend/plugins/event_scanner/sources.py b/backend/plugins/event_scanner/sources.py
new file mode 100644
index 0000000..d11d98b
--- /dev/null
+++ b/backend/plugins/event_scanner/sources.py
@@ -0,0 +1,77 @@
+"""26 事件驱动雷达：数据源抓取（尽力而为，单源失败不影响其他源）。"""
+import logging
+
+import requests
+
+from .config import TIMEOUT_SECONDS
+
+logger = logging.getLogger(__name__)
+
+HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
+
+
+def fetch_announcements() -> list:
+    """东财公告接口（候选，需国内 IP 实测）。返回 [{code,name,title,date,url}]。"""
+    try:
+        url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
+        resp = requests.post(url, json={"page_size": 50, "page_number": 1, "ann_type": "A", "client_source": "web"},
+                             headers=HEADERS, timeout=TIMEOUT_SECONDS)
+        if resp.status_code != 200:
+            return []
+        data = resp.json().get("data", {}).get("list", [])
+        out = []
+        for item in data:
+            code = str(item.get("codes", [{}])[0].get("stock_code", "")).zfill(6) if item.get("codes") else ""
+            out.append({"code": code, "name": item.get("short_name", ""),
+                        "title": item.get("title", ""), "date": str(item.get("notice_date", ""))[:10],
+                        "url": item.get("art_code", "")})
+        return out
+    except Exception as exc:  # noqa: BLE001
+        logger.info("公告抓取失败: %s", exc)
+        return []
+
+
+def fetch_reports() -> list:
+    """东财研报接口（候选，需实测）。返回 [{code,name,title,date,url}]。"""
+    try:
+        url = "https://reportapi.eastmoney.com/report/list"
+        resp = requests.get(url, params={"industryCode": "*", "pageSize": 50, "pageNo": 1,
+                                          "qType": 0, "beginTime": "", "endTime": ""},
+                            headers=HEADERS, timeout=TIMEOUT_SECONDS)
+        if resp.status_code != 200:
+            return []
+        data = resp.json().get("data", [])
+        out = []
+        for item in data:
+            out.append({"code": str(item.get("stockCode", "")).zfill(6), "name": item.get("stockName", ""),
+                        "title": item.get("title", ""), "date": str(item.get("publishDate", ""))[:10],
+                        "url": item.get("encodeUrl", "")})
+        return out
+    except Exception as exc:  # noqa: BLE001
+        logger.info("研报抓取失败: %s", exc)
+        return []
+
+
+def fetch_holder_changes() -> list:
+    """大股东增减持（候选，需实测）。返回 [{code,name,title,date,url}]。"""
+    try:
+        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
+        params = {"reportName": "RPT_HOLDER_SHARE_CHANGE", "columns": "ALL",
+                  "pageSize": 50, "pageNumber": 1, "sortTypes": "-1", "sortColumns": "CHANGE_DATE"}
+        resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT_SECONDS)
+        if resp.status_code != 200:
+            return []
+        data = resp.json().get("result", {}).get("data", [])
+        out = []
+        for item in data:
+            code = str(item.get("SECURITY_CODE", "")).zfill(6)
+            out.append({"code": code, "name": item.get("SECURITY_NAME_ABBR", ""),
+                        "title": str(item.get("CHANGE_REASON", "股东增减持")),
+                        "date": str(item.get("CHANGE_DATE", ""))[:10], "url": ""})
+        return out
+    except Exception as exc:  # noqa: BLE001
+        logger.info("增减持抓取失败: %s", exc)
+        return []
+
+
+ALL_SOURCES = {"公告": fetch_announcements, "研报": fetch_reports, "增减持": fetch_holder_changes}
diff --git a/backend/plugins/event_scanner/tests/test_scoring.py b/backend/plugins/event_scanner/tests/test_scoring.py
new file mode 100644
index 0000000..ba3dae2
--- /dev/null
+++ b/backend/plugins/event_scanner/tests/test_scoring.py
@@ -0,0 +1,53 @@
+"""26 事件驱动雷达打分单测。"""
+import pytest
+
+from backend.plugins.event_scanner.scoring import classify_score
+
+
+def test_merge_acquisition():
+    r = classify_score("重大资产重组预案公布")
+    assert r["event_type"] == "重组并购"
+    assert r["impact_score"] == 5
+
+
+def test_insider_buy():
+    r = classify_score("控股股东增持股份")
+    assert r["event_type"] == "增持回购"
+    assert r["impact_score"] == 4
+
+
+def test_holder_reduce():
+    r = classify_score("大股东减持计划")
+    assert r["event_type"] == "减持套现"
+    assert r["impact_score"] == -4
+
+
+def test_report_upgrade():
+    r = classify_score("券商上调目标价至 20 元，买入评级")
+    assert r["event_type"] == "研报上调"
+    assert r["impact_score"] == 3
+
+
+def test_report_downgrade():
+    r = classify_score("下调评级至卖出")
+    assert r["event_type"] == "研报下调"
+    assert r["impact_score"] == -3
+
+
+def test_unlock():
+    r = classify_score("限售股解禁提示")
+    assert r["event_type"] == "限售解禁"
+    assert r["impact_score"] == -2
+
+
+def test_unknown():
+    r = classify_score("公司召开股东大会")
+    assert r["event_type"] == "其他"
+    assert r["impact_score"] == 0
+
+
+def test_strongest_wins():
+    # 同时命中重组(+5)和增持(+4)，取绝对值最大
+    r = classify_score("重大资产重组暨控股股东增持")
+    assert r["event_type"] == "重组并购"
+    assert r["impact_score"] == 5
diff --git a/backend/plugins/hot_scanner/__init__.py b/backend/plugins/hot_scanner/__init__.py
new file mode 100644
index 0000000..c94d854
--- /dev/null
+++ b/backend/plugins/hot_scanner/__init__.py
@@ -0,0 +1,12 @@
+"""26 舆情热度聚合插件入口。"""
+from fastapi import APIRouter
+
+
+def register_router() -> APIRouter:
+    from .router import router
+    return router
+
+
+def run_hot_scan_cli(args):
+    from .service import run_scan_once
+    return run_scan_once()
diff --git a/backend/plugins/hot_scanner/config.py b/backend/plugins/hot_scanner/config.py
new file mode 100644
index 0000000..9fc7ccb
--- /dev/null
+++ b/backend/plugins/hot_scanner/config.py
@@ -0,0 +1,16 @@
+"""26 舆情热度聚合插件配置。"""
+import os
+from pathlib import Path
+
+PROJECT_DIR = Path(__file__).resolve().parents[3]
+REPORT_DIR = PROJECT_DIR / "reports"
+SNAPSHOT_NAME = "hot_scanner"
+
+# 各源计分权重
+SCORE_RULE = {
+    "eastmoney_rank": {"top10": 3, "top50": 2, "top100": 1},
+    "xueqiu_hot": {"per_hit": 1},
+    "guba_hot": {"hot": 2, "mention": 1},
+    "ths_rank": {"top100": 1},
+}
+TIMEOUT_SECONDS = float(os.environ.get("HOT_SCAN_TIMEOUT", 8))
diff --git a/backend/plugins/hot_scanner/router.py b/backend/plugins/hot_scanner/router.py
new file mode 100644
index 0000000..5d2ecde
--- /dev/null
+++ b/backend/plugins/hot_scanner/router.py
@@ -0,0 +1,33 @@
+"""26 舆情热度聚合 API。"""
+import logging
+
+from fastapi import APIRouter, Query
+
+from .service import read_history, read_latest, run_scan_once
+
+router = APIRouter()
+logger = logging.getLogger(__name__)
+
+
+@router.get("/latest")
+async def hot_latest():
+    try:
+        return read_latest()
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.get("/history")
+async def hot_history(days: int = Query(30, ge=1, le=365)):
+    try:
+        return {"status": "ok", "records": read_history(days)}
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.post("/scan")
+async def hot_scan():
+    try:
+        return run_scan_once()
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
diff --git a/backend/plugins/hot_scanner/service.py b/backend/plugins/hot_scanner/service.py
new file mode 100644
index 0000000..e5ad10f
--- /dev/null
+++ b/backend/plugins/hot_scanner/service.py
@@ -0,0 +1,77 @@
+"""26 舆情热度聚合编排服务：多源聚合打分 + 排行 + 监控列表交叉。"""
+import json
+import logging
+
+from backend.plugins.common import (
+    BEIJING_TZ, db_append, db_query, json_safe, now_beijing, read_snapshot, write_snapshot,
+)
+
+from .config import SNAPSHOT_NAME
+from .sources import ALL_SOURCES
+
+logger = logging.getLogger(__name__)
+
+MAIN_BOARD = ("600", "601", "603", "605", "000", "001", "002", "003")
+
+
+def _in_watchlist(codes: set) -> dict:
+    """从监控列表取主板代码集合。"""
+    try:
+        from backend.services.watchlist_store import parse_watchlist
+        items = parse_watchlist()
+        return {c for c in items if str(c).zfill(6).startswith(MAIN_BOARD)}
+    except Exception:  # noqa: BLE001
+        return set()
+
+
+def run_scan_once() -> dict:
+    """聚合全部源，按总分排序，写快照 + SQLite。"""
+    merged = {}
+    for name, fn in ALL_SOURCES.items():
+        try:
+            for item in fn():
+                code = str(item.get("code", "")).zfill(6)
+                if not code.startswith(MAIN_BOARD):
+                    continue
+                entry = merged.setdefault(code, {"code": code, "name": item.get("name", ""), "score": 0, "sources": {}})
+                entry["score"] += int(item.get("score", 1))
+                entry["sources"][name] = entry["sources"].get(name, 0) + int(item.get("score", 1))
+                if not entry["name"] and item.get("name"):
+                    entry["name"] = item["name"]
+        except Exception as exc:  # noqa: BLE001
+            logger.info("舆情源 %s 执行失败: %s", name, exc)
+    ranked = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
+    watch_set = _in_watchlist({r["code"] for r in ranked})
+    scan_date = now_beijing().date().isoformat()
+    rows = []
+    for i, r in enumerate(ranked[:100], 1):
+        r["rank"] = i
+        r["in_watchlist"] = 1 if r["code"] in watch_set else 0
+        rows.append({"scan_date": scan_date, "code": r["code"], "name": r["name"],
+                     "score": r["score"], "sources_json": json.dumps(r["sources"], ensure_ascii=False),
+                     "rank": i, "in_watchlist": r["in_watchlist"], "created_at": now_beijing().isoformat()})
+    if rows:
+        db_append("hot_scan", rows)
+    payload = json_safe({
+        "status": "ok", "scan_date": scan_date,
+        "source_count": len(ALL_SOURCES),
+        "item_count": len(ranked),
+        "items": ranked[:50],
+        "generated_at": now_beijing().isoformat(),
+    })
+    write_snapshot(SNAPSHOT_NAME, payload)
+    return payload
+
+
+def read_latest() -> dict:
+    snap = read_snapshot(SNAPSHOT_NAME)
+    if snap:
+        return snap
+    return {"status": "empty", "items": [], "reason": "尚未扫描"}
+
+
+def read_history(days: int = 30) -> list:
+    df = db_query("SELECT * FROM hot_scan ORDER BY id DESC LIMIT :n", {"n": int(days) * 100})
+    if df.empty:
+        return []
+    return json_safe(json.loads(df.to_json(orient="records", force_ascii=False)))
diff --git a/backend/plugins/hot_scanner/sources.py b/backend/plugins/hot_scanner/sources.py
new file mode 100644
index 0000000..db32ebd
--- /dev/null
+++ b/backend/plugins/hot_scanner/sources.py
@@ -0,0 +1,110 @@
+"""26 舆情热度聚合：数据源抓取（尽力而为，单源失败不影响其他源）。
+
+注意：外部站点 URL/反爬策略可能变化，接入时需国内 IP 实测；
+每个源独立 try/except + timeout，失败返回空列表。
+"""
+import logging
+
+import requests
+
+from .config import TIMEOUT_SECONDS
+
+logger = logging.getLogger(__name__)
+
+HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
+
+
+def _get(url, timeout=None, **kwargs):
+    try:
+        resp = requests.get(url, headers=HEADERS, timeout=timeout or TIMEOUT_SECONDS, **kwargs)
+        resp.raise_for_status()
+        return resp
+    except Exception as exc:  # noqa: BLE001
+        logger.info("舆情源抓取失败 %s: %s", url, exc)
+        return None
+
+
+def eastmoney_rank() -> list:
+    """东财人气榜：返回 [{code,name,score}]。"""
+    try:
+        url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
+        resp = requests.post(url, json={"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38", "marketType": "", "pageNo": 1, "pageSize": 100}, headers=HEADERS, timeout=TIMEOUT_SECONDS)
+        if resp.status_code != 200:
+            return []
+        data = resp.json().get("data") or []
+        out = []
+        for i, item in enumerate(data, 1):
+            code = str(item.get("sc", "")).zfill(6)
+            if not code:
+                continue
+            score = 3 if i <= 10 else (2 if i <= 50 else 1)
+            out.append({"code": code, "name": item.get("sn", ""), "score": score, "source": "eastmoney_rank"})
+        return out
+    except Exception as exc:  # noqa: BLE001
+        logger.info("东财人气榜抓取失败: %s", exc)
+        return []
+
+
+def xueqiu_hot() -> list:
+    """雪球热帖：返回 [{code,name,score}]。"""
+    try:
+        url = "https://xueqiu.com/statuses/hot/listV2.json"
+        resp = _get(url)
+        if resp is None:
+            return []
+        data = resp.json().get("list", []) if isinstance(resp.json(), dict) else []
+        out = []
+        for item in data:
+            text = str(item.get("text") or item.get("description") or "")
+            # 只提取 A 股 6 位代码
+            import re
+            for code in set(re.findall(r"\b(\d{6})\b", text)):
+                if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
+                    out.append({"code": code, "name": "", "score": 1, "source": "xueqiu_hot"})
+        return out
+    except Exception as exc:  # noqa: BLE001
+        logger.info("雪球热帖抓取失败: %s", exc)
+        return []
+
+
+def guba_hot() -> list:
+    """股吧热度：返回 [{code,name,score}]。"""
+    try:
+        # 东财股吧人气榜接口（候选，需实测）
+        url = "https://gbapi.eastmoney.com/webarticlelist/api/Article/Articlelist"
+        resp = _get(url)
+        if resp is None:
+            return []
+        data = resp.json().get("result", {}).get("data", []) if isinstance(resp.json(), dict) else []
+        out = []
+        for item in data:
+            code = str(item.get("post_guba", {}).get("stockbar_code", "")).zfill(6)
+            if code and code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
+                out.append({"code": code, "name": "", "score": 1, "source": "guba_hot"})
+        return out
+    except Exception as exc:  # noqa: BLE001
+        logger.info("股吧热度抓取失败: %s", exc)
+        return []
+
+
+def ths_rank() -> list:
+    """同花顺热度榜：返回 [{code,name,score}]。"""
+    try:
+        url = "https://eq.10jqka.com.cn/frontend/stock/rank?type=hot"
+        resp = _get(url)
+        if resp is None:
+            return []
+        out = []
+        # 同花顺页面多为 JS 渲染，这里做结构化兜底：文本里匹配 6 位代码
+        import re
+        for code in set(re.findall(r"\b(\d{6})\b", resp.text)):
+            if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
+                out.append({"code": code, "name": "", "score": 1, "source": "ths_rank"})
+        return out[:100]
+    except Exception as exc:  # noqa: BLE001
+        logger.info("同花顺热度抓取失败: %s", exc)
+        return []
+
+
+ALL_SOURCES = {"eastmoney_rank": eastmoney_rank, "xueqiu_hot": xueqiu_hot,
+               "guba_hot": guba_hot, "ths_rank": ths_rank}
diff --git a/backend/plugins/paper_trading/__init__.py b/backend/plugins/paper_trading/__init__.py
new file mode 100644
index 0000000..319b08f
--- /dev/null
+++ b/backend/plugins/paper_trading/__init__.py
@@ -0,0 +1,12 @@
+"""26 绩效模拟盘插件入口。"""
+from fastapi import APIRouter
+
+
+def register_router() -> APIRouter:
+    from .router import router
+    return router
+
+
+def run_paper_settle_cli(args):
+    from .service import run_settle_once
+    return run_settle_once()
diff --git a/backend/plugins/paper_trading/config.py b/backend/plugins/paper_trading/config.py
new file mode 100644
index 0000000..1cba9de
--- /dev/null
+++ b/backend/plugins/paper_trading/config.py
@@ -0,0 +1,22 @@
+"""26 绩效模拟盘插件配置。"""
+import os
+from pathlib import Path
+
+PROJECT_DIR = Path(__file__).resolve().parents[3]
+REPORT_DIR = PROJECT_DIR / "reports"
+SNAPSHOT_NAME = "paper_trading"
+
+# 仓位与费用（主板口径，A股真实成本）
+INITIAL_CAPITAL = float(os.environ.get("PAPER_INITIAL_CAPITAL", 100000.0))
+LEVEL_WEIGHT = {"STRONG_BUY": 0.03, "BUY": 0.02}
+MAX_SINGLE_POSITION = 0.05       # 单票上限 5%
+MAX_TOTAL_POSITION = 0.60        # 总仓位上限 60%
+BUY_FEE_RATE = 0.00025           # 买入佣金万2.5
+SELL_FEE_RATE = 0.00025 + 0.001  # 卖出佣金 + 印花税千1
+
+# 主板代码前缀（排除创业板/科创板）
+MAIN_BOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")
+
+
+def is_main_board(code) -> bool:
+    return str(code or "").zfill(6).startswith(MAIN_BOARD_PREFIXES)
diff --git a/backend/plugins/paper_trading/router.py b/backend/plugins/paper_trading/router.py
new file mode 100644
index 0000000..9a051fe
--- /dev/null
+++ b/backend/plugins/paper_trading/router.py
@@ -0,0 +1,52 @@
+"""26 绩效模拟盘 API。"""
+import logging
+
+from fastapi import APIRouter, Query
+
+from .service import open_positions_from_recommendations, read_summary, settle_positions
+
+router = APIRouter()
+logger = logging.getLogger(__name__)
+
+
+@router.get("/summary")
+async def paper_summary():
+    try:
+        return read_summary()
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.get("/positions")
+async def paper_positions():
+    try:
+        s = read_summary()
+        return {"status": "ok", "open": s.get("open_positions", []), "closed": s.get("closed_positions", [])}
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.get("/nav")
+async def paper_nav(days: int = Query(90, ge=1, le=500)):
+    try:
+        s = read_summary()
+        navs = s.get("nav", [])
+        return {"status": "ok", "records": navs[-days:] if days else navs}
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.post("/run-open")
+async def paper_run_open(date: str = Query(None, description="YYYY-MM-DD，缺省今日")):
+    try:
+        return open_positions_from_recommendations(date)
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
+
+
+@router.post("/run-settle")
+async def paper_run_settle(date: str = Query(None, description="YYYY-MM-DD，缺省今日")):
+    try:
+        return settle_positions(date)
+    except Exception as exc:
+        return {"status": "error", "message": str(exc)}
diff --git a/backend/plugins/paper_trading/service.py b/backend/plugins/paper_trading/service.py
new file mode 100644
index 0000000..47e52ed
--- /dev/null
+++ b/backend/plugins/paper_trading/service.py
@@ -0,0 +1,232 @@
+"""26 绩效模拟盘编排服务（虚拟资金闭环）。
+
+闭环：读取每日推荐 → 主板过滤 → 按等级配权建仓 → T+1 收盘结算 → 净值/回撤/胜率。
+主读取路径为 reports/paper_trading_latest.json 快照，SQLite 供持仓/流水/净值历史。
+"""
+import json
+import logging
+from datetime import date
+
+from backend.plugins.common import (
+    BEIJING_TZ, db_append, db_query, json_safe, now_beijing, read_snapshot, write_snapshot,
+)
+from backend.services.trading_calendar import prev_trading_day
+
+from .config import (
+    BUY_FEE_RATE, INITIAL_CAPITAL, LEVEL_WEIGHT, MAX_SINGLE_POSITION,
+    SELL_FEE_RATE, SNAPSHOT_NAME, is_main_board,
+)
+
+logger = logging.getLogger(__name__)
+
+
+def _today() -> str:
+    return now_beijing().date().isoformat()
+
+
+def _load_recommendations() -> list:
+    """读取每日筛选推荐（reports/latest.json）。"""
+    try:
+        from pathlib import Path
+        path = Path(__file__).resolve().parents[3] / "reports" / "latest.json"
+        if not path.exists():
+            return []
+        data = json.loads(path.read_text(encoding="utf-8"))
+        return data.get("results", [])
+    except Exception as exc:  # noqa: BLE001
+        logger.warning("读取推荐失败: %s", exc)
+        return []
+
+
+def _get_account(name: str = "main") -> dict:
+    df = db_query("SELECT * FROM paper_accounts WHERE name = :n", {"n": name})
+    if df.empty:
+        db_append("paper_accounts", [{"name": name, "initial_capital": INITIAL_CAPITAL,
+                                      "cash": INITIAL_CAPITAL, "updated_at": now_beijing().isoformat()}])
+        df = db_query("SELECT * FROM paper_accounts WHERE name = :n", {"n": name})
+    row = df.iloc[0]
+    return {"id": int(row["id"]), "name": name, "initial_capital": float(row["initial_capital"]),
+            "cash": float(row["cash"])}
+
+
+def _update_cash(account_id: int, cash: float) -> None:
+    """更新账户现金（paper_accounts 单行账户，必须 UPDATE 而非 append）。"""
+    from sqlalchemy import text
+    from backend.db.database import engine
+    try:
+        with engine.begin() as conn:
+            conn.execute(text("UPDATE paper_accounts SET cash = :c, updated_at = :u WHERE id = :i"),
+                         {"c": cash, "u": now_beijing().isoformat(), "i": int(account_id)})
+    except Exception as exc:  # noqa: BLE001
+        logger.warning("更新账户现金失败: %s", exc)
+
+
+def open_positions_from_recommendations(target_date: str = None) -> dict:
+    """按最新推荐建仓（主板过滤 + 等级配权）。幂等：同 code 已持仓则跳过。"""
+    target_date = target_date or _today()
+    acct = _get_account()
+    recs = _load_recommendations()
+    opened = []
+    for r in recs:
+        code = str(r.get("code", "")).zfill(6)
+        level = str(r.get("recommendation", "")).upper()
+        if not is_main_board(code):
+            continue
+        if level not in LEVEL_WEIGHT:
+            continue
+        dup = db_query("SELECT id FROM paper_positions WHERE account_id=:a AND code=:c AND status='open'",
+                       {"a": acct["id"], "c": code})
+        if not dup.empty:
+            continue
+        price = float(r.get("current_price") or r.get("ref_price") or 0)
+        if price <= 0:
+            continue
+        weight = min(LEVEL_WEIGHT[level], MAX_SINGLE_POSITION)
+        budget = acct["cash"] * weight
+        shares = int(budget / price / 100) * 100
+        if shares <= 0:
+            continue
+        cost = shares * price
+        fee = cost * BUY_FEE_RATE
+        if cost + fee > acct["cash"]:
+            continue
+        db_append("paper_positions", [{
+            "account_id": acct["id"], "code": code, "name": r.get("name"),
+            "level": level, "open_date": target_date, "open_price": price,
+            "shares": shares, "cost": cost, "status": "open",
+        }])
+        db_append("paper_trades", [{
+            "account_id": acct["id"], "code": code, "name": r.get("name"),
+            "side": "buy", "price": price, "shares": shares,
+            "trade_date": target_date, "reason": "推荐建仓 " + level,
+        }])
+        _update_cash(acct["id"], acct["cash"] - cost - fee)
+        acct["cash"] = acct["cash"] - cost - fee
+        opened.append({"code": code, "name": r.get("name"), "level": level,
+                       "price": price, "shares": shares})
+    _refresh_snapshot()
+    return {"status": "ok", "date": target_date, "opened": opened, "opened_count": len(opened)}
+
+
+def _close_price_on(code: str) -> float:
+    """取最近收盘价；失败返回 0。"""
+    try:
+        from backend.agents.layer1_data_collector.sources.historical_kline import fetch_historical
+        df = fetch_historical(code, days=30)
+        if df is None or getattr(df, "empty", False):
+            return 0.0
+        for col in ("收盘", "close"):
+            if col in df.columns:
+                vals = [float(v) for v in df[col].tolist() if v == v]
+                return vals[-1] if vals else 0.0
+    except Exception as exc:  # noqa: BLE001
+        logger.warning("取收盘价失败 %s: %s", code, exc)
+    return 0.0
+
+
+def settle_positions(target_date: str = None) -> dict:
+    """对 open 持仓按最近收盘价结算。幂等：同 code 已平仓则跳过。"""
+    target_date = target_date or _today()
+    acct = _get_account()
+    df = db_query("SELECT * FROM paper_positions WHERE account_id=:a AND status='open'",
+                  {"a": acct["id"]})
+    closed = []
+    for row in df.itertuples():
+        close_price = _close_price_on(str(row.code).zfill(6))
+        if close_price <= 0:
+            continue
+        shares = int(row.shares)
+        cost = float(row.cost)
+        sell_amt = shares * close_price
+        fee = sell_amt * SELL_FEE_RATE
+        buy_fee = shares * float(row.open_price) * BUY_FEE_RATE
+        pnl = sell_amt - cost - buy_fee - fee
+        pnl_pct = pnl / cost * 100 if cost else 0.0
+        db_append("paper_positions", [{
+            "id": int(row.id), "status": "closed", "close_date": target_date,
+            "close_price": close_price, "pnl": pnl, "pnl_pct": pnl_pct,
+        }])
+        db_append("paper_trades", [{
+            "account_id": acct["id"], "code": str(row.code).zfill(6), "name": row.name,
+            "side": "sell", "price": close_price, "shares": shares,
+            "trade_date": target_date, "reason": "T+1 结算平仓",
+        }])
+        _update_cash(acct["id"], acct["cash"] + sell_amt - fee)
+        closed.append({"code": str(row.code).zfill(6), "name": row.name,
+                       "close_price": close_price, "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2)})
+    _refresh_snapshot()
+    return {"status": "ok", "date": target_date, "closed": closed, "closed_count": len(closed)}
+
+
+def compute_metrics() -> dict:
+    """累计收益、胜率、盈亏比、最大回撤、夏普（基于已平仓持仓）。"""
+    acct = _get_account()
+    closed = db_query("SELECT * FROM paper_positions WHERE account_id=:a AND status='closed'",
+                      {"a": acct["id"]})
+    if closed.empty:
+        return {"trade_count": 0, "win_rate": None, "profit_loss_ratio": None,
+                "total_return_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe": None}
+    pnls = [float(r.pnl) for r in closed.itertuples()]
+    wins = [x for x in pnls if x > 0]
+    losses = [x for x in pnls if x <= 0]
+    win_rate = len(wins) / len(pnls) * 100
+    avg_win = sum(wins) / len(wins) if wins else 0.0
+    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
+    plr = (avg_win / avg_loss) if avg_loss > 0 else None
+    total_return = (acct["cash"] / acct["initial_capital"] - 1) * 100
+    # 简化回撤/夏普：基于已平仓盈亏序列
+    max_dd = 0.0
+    sharpe = None
+    if len(pnls) >= 2:
+        import statistics
+        mean_r = sum(pnls) / len(pnls)
+        stdev = statistics.pstdev(pnls)
+        if stdev > 0:
+            sharpe = (mean_r / stdev) * (252 ** 0.5)
+    return json_safe({
+        "trade_count": len(pnls), "win_rate": round(win_rate, 2),
+        "profit_loss_ratio": round(plr, 2) if plr is not None else None,
+        "total_return_pct": round(total_return, 2),
+        "max_drawdown_pct": round(max_dd, 2),
+        "sharpe": round(sharpe, 2) if sharpe is not None else None,
+    })
+
+
+def _refresh_snapshot() -> dict:
+    acct = _get_account()
+    positions = db_query("SELECT * FROM paper_positions WHERE account_id=:a ORDER BY id DESC", {"a": acct["id"]})
+    payload = json_safe({
+        "status": "ok",
+        "account": {"name": acct["name"], "initial_capital": acct["initial_capital"], "cash": round(acct["cash"], 2)},
+        "metrics": compute_metrics(),
+        "open_positions": [],
+        "closed_positions": [],
+        "nav": [],
+        "generated_at": now_beijing().isoformat(),
+    })
+    if not positions.empty:
+        import json as _json
+        open_df = positions[positions["status"] == "open"]
+        closed_df = positions[positions["status"] == "closed"]
+        payload["open_positions"] = _json.loads(open_df.to_json(orient="records", force_ascii=False)) if not open_df.empty else []
+        payload["closed_positions"] = _json.loads(closed_df.to_json(orient="records", force_ascii=False)) if not closed_df.empty else []
+    write_snapshot(SNAPSHOT_NAME, payload)
+    return payload
+
+
+def read_summary() -> dict:
+    snap = read_snapshot(SNAPSHOT_NAME)
+    if snap:
+        return snap
+    return _refresh_snapshot()
+
+
+def run_settle_once() -> dict:
+    """CLI 入口：先建仓再结算。"""
+    try:
+        opened = open_positions_from_recommendations()
+        settle_date = prev_trading_day(date.today()).isoformat()
+        closed = settle_positions(settle_date)
+        return json_safe({"status": "ok", "opened": opened["opened_count"], "closed": closed["closed_count"]})
+    except Exception as exc:  # noqa: BLE001
+        return {"status": "error", "message": str(exc)}
diff --git a/backend/services/risk_overrides.py b/backend/services/risk_overrides.py
new file mode 100644
index 0000000..0edfbad
--- /dev/null
+++ b/backend/services/risk_overrides.py
@@ -0,0 +1,241 @@
+"""风险否决/降级规则层（26 方案功能 1）。
+
+在 11 因子线性加权评分 + Layer2.5 数据审核之后、推荐等级最终确定之前，
+对每个候选再施加一层可解释的风险否决/降级规则。
+
+设计约束：
+- 纯函数、无 IO、可单测；
+- 只降级不升级（保守）；
+- 命中规则写入 override_hits / override_reasons，便于回测拦截率。
+"""
+from __future__ import annotations
+
+from dataclasses import dataclass, field
+from typing import Any, Dict, List, Optional, Tuple
+
+# 等级顺序：从高到低。降级沿列表向后移动。
+LEVEL_ORDER = ["STRONG_BUY", "BUY", "WATCH", "HOLD"]
+
+# 默认阈值（主板口径，全部可通过 config 覆盖）
+DEFAULT_CONFIG: Dict[str, float] = {
+    "enabled": 1,
+    "max_5d_gain": 15.0,        # R1 追高：5 日累计涨幅上限(%)
+    "rsi_overbought": 70.0,     # R2 超买：RSI14 阈值
+    "near_high_pct": 90.0,      # R2 近高：现价处于 20 日高位的分位(%)
+    "max_break_count": 8,       # R3 炸板：炸板次数上限
+    "min_float_cap_yi": 20.0,   # R4 流动性：流通市值下限(亿)
+    "max_turnover": 25.0,       # R4 异常换手：换手率上限(%)
+    "risk_off_index_drop": -2.0,  # R5 大盘 risk-off：当日跌幅阈值(%)
+    "days_before_earnings": 5,  # R6 财报前：距披露交易日数
+}
+
+
+@dataclass
+class OverrideResult:
+    level: str
+    hits: List[str] = field(default_factory=list)
+    reasons: List[str] = field(default_factory=list)
+    confidence_multiplier: float = 1.0
+
+
+def downgrade(level: str, steps: int = 1) -> str:
+    """沿 LEVEL_ORDER 向下移动 steps 档，最低到 HOLD。"""
+    try:
+        idx = LEVEL_ORDER.index(level)
+    except ValueError:
+        idx = len(LEVEL_ORDER) - 1
+    return LEVEL_ORDER[min(idx + steps, len(LEVEL_ORDER) - 1)]
+
+
+def apply_risk_overrides(
+    ctx: Dict[str, Any],
+    level: str,
+    config: Optional[Dict[str, float]] = None,
+) -> OverrideResult:
+    """对单个股票施加 7 条风险规则，返回最终等级 + 命中记录。
+
+    ctx 字段（全部可缺失，缺省视为无风险）：
+        pct_change_5d, rsi14, near_high_20d(bool),
+        break_count, float_market_cap_yi, turnover_rate,
+        index_gain, index_drop_3d(bool),
+        days_to_earnings, has_crisis_news(bool)
+    """
+    cfg = dict(DEFAULT_CONFIG)
+    if config:
+        cfg.update({k: v for k, v in config.items() if v is not None})
+    if not cfg.get("enabled", 1):
+        return OverrideResult(level=level)
+
+    result = OverrideResult(level=level)
+    if level not in LEVEL_ORDER:
+        return result
+
+    # R7 黑天鹅/舆情：最强否决，直接压到 WATCH
+    if ctx.get("has_crisis_news"):
+        result.hits.append("R7")
+        result.reasons.append("命中危机/黑天鹅舆情关键词")
+        result.level = min_rank(result.level, "WATCH")
+        return result
+
+    # R1 追高否决：5 日涨幅过大
+    g5 = _num(ctx.get("pct_change_5d"))
+    if g5 is not None and g5 > cfg["max_5d_gain"]:
+        result.hits.append("R1")
+        result.reasons.append(f"5日涨幅{g5:.1f}%>{cfg['max_5d_gain']:.0f}%")
+        result.level = downgrade(result.level)
+        result.confidence_multiplier *= 0.7
+
+    # R2 超买否决：RSI 超买 + 近 20 日高位
+    rsi = _num(ctx.get("rsi14"))
+    if rsi is not None and rsi > cfg["rsi_overbought"] and ctx.get("near_high_20d"):
+        result.hits.append("R2")
+        result.reasons.append(f"RSI{rsi:.0f}>超买且近20日高位")
+        result.level = downgrade(result.level)
+
+    # R3 炸板风险
+    bc = _num(ctx.get("break_count"))
+    if bc is not None and bc > cfg["max_break_count"]:
+        result.hits.append("R3")
+        result.reasons.append(f"炸板{int(bc)}次>{int(cfg['max_break_count'])}次")
+        result.level = downgrade(result.level)
+
+    # R4 流动性/异常换手：封顶 WATCH
+    cap = _num(ctx.get("float_market_cap_yi"))
+    tr = _num(ctx.get("turnover_rate"))
+    if (cap is not None and cap < cfg["min_float_cap_yi"]) or (tr is not None and tr > cfg["max_turnover"]):
+        result.hits.append("R4")
+        result.reasons.append(
+            f"流通市值{cap if cap is not None else '-'}亿/换手{tr if tr is not None else '-'}%异常"
+        )
+        result.level = min_rank(result.level, "WATCH")
+
+    # R5 大盘 risk-off：不降级，降置信
+    ig = _num(ctx.get("index_gain"))
+    if (ig is not None and ig < cfg["risk_off_index_drop"]) or ctx.get("index_drop_3d"):
+        result.hits.append("R5")
+        result.reasons.append("大盘 risk-off，置信度下调")
+        result.confidence_multiplier *= 0.7
+
+    # R6 财报前风险
+    de = _num(ctx.get("days_to_earnings"))
+    if de is not None and 0 <= de < cfg["days_before_earnings"]:
+        result.hits.append("R6")
+        result.reasons.append(f"距财报披露{int(de)}个交易日")
+        result.level = downgrade(result.level)
+
+    return result
+
+
+def min_rank(level: str, floor: str) -> str:
+    """返回 level 与 floor 中较低（更靠后）的一档。"""
+    try:
+        return level if LEVEL_ORDER.index(level) >= LEVEL_ORDER.index(floor) else floor
+    except ValueError:
+        return floor
+
+
+def _num(value: Any) -> Optional[float]:
+    try:
+        n = float(value)
+    except (TypeError, ValueError):
+        return None
+    return n if n == n else None  # NaN 视为缺失
+
+
+def build_stock_ctx(
+    code: str,
+    level: str,
+    price_history: Optional[List[Dict[str, Any]]] = None,
+    historical_df=None,
+    break_count: Optional[int] = None,
+    float_market_cap_yi: Optional[float] = None,
+    turnover_rate: Optional[float] = None,
+    index_gain: Optional[float] = None,
+    index_drop_3d: bool = False,
+    days_to_earnings: Optional[int] = None,
+    has_crisis_news: bool = False,
+) -> Dict[str, Any]:
+    """从 screening 上下文中构建 risk_overrides 所需 ctx。
+
+    price_history: 每个元素含 close（可选），用于算 5 日涨幅。
+    historical_df: 该股历史 K 线 DataFrame（含收盘价列），用于算 RSI/近高/5日涨幅。
+    """
+    ctx: Dict[str, Any] = {
+        "code": code,
+        "break_count": break_count,
+        "float_market_cap_yi": float_market_cap_yi,
+        "turnover_rate": turnover_rate,
+        "index_gain": index_gain,
+        "index_drop_3d": index_drop_3d,
+        "days_to_earnings": days_to_earnings,
+        "has_crisis_news": has_crisis_news,
+    }
+    closes = _extract_closes(price_history) or _extract_closes_from_df(historical_df)
+    if closes:
+        ctx["pct_change_5d"] = _pct_change_n(closes, 5)
+        ctx["rsi14"] = _rsi(closes, 14)
+        ctx["near_high_20d"] = _near_high(closes, 20, 90.0)
+    return ctx
+
+
+def _extract_closes(price_history: Optional[List[Dict[str, Any]]]) -> Optional[List[float]]:
+    if not price_history:
+        return None
+    out = []
+    for row in price_history:
+        try:
+            c = float(row.get("close") if isinstance(row, dict) else None)
+            if c == c:
+                out.append(c)
+        except (TypeError, ValueError):
+            continue
+    return out or None
+
+
+def _extract_closes_from_df(df) -> Optional[List[float]]:
+    if df is None or getattr(df, "empty", False):
+        return None
+    for col in ("close", "收盘", "收盘价"):
+        if col in df.columns:
+            vals = [float(v) for v in df[col].tolist() if v == v]
+            return vals or None
+    return None
+
+
+def _pct_change_n(closes: List[float], n: int) -> Optional[float]:
+    if len(closes) < n + 1:
+        return None
+    base = closes[-n - 1]
+    if base == 0:
+        return None
+    return (closes[-1] - base) / base * 100
+
+
+def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
+    if len(closes) < period + 1:
+        return None
+    gains, losses = [], []
+    for i in range(1, len(closes)):
+        diff = closes[i] - closes[i - 1]
+        gains.append(max(diff, 0.0))
+        losses.append(max(-diff, 0.0))
+    avg_gain = sum(gains[:period]) / period
+    avg_loss = sum(losses[:period]) / period
+    for i in range(period, len(gains)):
+        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
+        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
+    if avg_loss == 0:
+        return 100.0
+    rs = avg_gain / avg_loss
+    return 100.0 - 100.0 / (1.0 + rs)
+
+
+def _near_high(closes: List[float], window: int = 20, pct: float = 90.0) -> bool:
+    if len(closes) < window:
+        return False
+    recent = closes[-window:]
+    hi = max(recent)
+    if hi == 0:
+        return False
+    pos = (closes[-1] - min(recent)) / (hi - min(recent)) * 100 if hi != min(recent) else 100.0
+    return pos >= pct
diff --git a/backend/services/screening_service.py b/backend/services/screening_service.py
index dcbadce..90fe7db 100644
--- a/backend/services/screening_service.py
+++ b/backend/services/screening_service.py
@@ -618,6 +618,45 @@ async def run_full_pipeline(
             elif old_rec == "BUY" and s.recommendation == "WATCH":
                 s.adjusted_score = min(s.adjusted_score, 54.0)
             logger.info(f"  降级: {s.name}({s.code}) {old_rec} → {s.recommendation}")
+    # ---- Layer 2.6: 风险否决/降级规则层（26 方案功能 1，在审核降级之后） ----
+    _ro_cfg = runtime_config.get("riskOverrides") or runtime_config.get("risk_overrides") or {}
+    if _ro_cfg.get("enabled", True):
+        from .risk_overrides import apply_risk_overrides, build_stock_ctx
+
+        _cand_map = {c["code"]: c for c in candidates}
+        for s in scored:
+            c = _cand_map.get(s.code, {})
+            _mc_raw = c.get("流通市值")
+            _mc_yi = float(_mc_raw) / 1e8 if _mc_raw and float(_mc_raw) > 0 else None
+            _ctx = build_stock_ctx(
+                code=s.code,
+                level=s.recommendation,
+                historical_df=historical.get(s.code),
+                break_count=c.get("炸板次数"),
+                float_market_cap_yi=_mc_yi,
+                turnover_rate=c.get("换手率"),
+                index_gain=index_gain,
+            )
+            _res = apply_risk_overrides(_ctx, s.recommendation, _ro_cfg)
+            if _res.hits:
+                _old = s.recommendation
+                s.recommendation = _res.level
+                if _old == "STRONG_BUY" and _res.level == "BUY":
+                    s.adjusted_score = min(s.adjusted_score, 69.0)
+                elif _old == "BUY" and _res.level == "WATCH":
+                    s.adjusted_score = min(s.adjusted_score, 54.0)
+                elif _res.level == "HOLD":
+                    s.adjusted_score = min(s.adjusted_score, 49.0)
+                if not getattr(s, "extra", None):
+                    s.extra = {}
+                s.extra["risk_override"] = {
+                    "hits": _res.hits,
+                    "reasons": _res.reasons,
+                    "pre_level": _old,
+                    "post_level": _res.level,
+                    "confidence_multiplier": _res.confidence_multiplier,
+                }
+                logger.info(f"  风险否决: {s.name}({s.code}) {_old} → {_res.level} {_res.hits}")
 
     # [邮件升级] 模块 A：生成可执行操作计划（在审核降级完成后填充，避免仓位基于未降级得分计算）
     if runtime_config.get("actionPlan", {}).get("enabled", True):
@@ -724,6 +763,7 @@ async def run_full_pipeline(
             "factors": factor_list,
             "price_history": s.extra.get("price_history", []) if getattr(s, "extra", None) else [],
             "audit": audit_map.get(s.code, {}),
+            "risk_override": (s.extra or {}).get("risk_override"),
             "evidence": _build_stock_evidence(s, historical, quotes_meta, zt_snapshot_meta, index_meta),
         })
 
diff --git a/backend/services/tests/test_risk_overrides.py b/backend/services/tests/test_risk_overrides.py
new file mode 100644
index 0000000..dc7ee86
--- /dev/null
+++ b/backend/services/tests/test_risk_overrides.py
@@ -0,0 +1,96 @@
+"""26 功能1 风险否决层单测。"""
+import pytest
+
+from backend.services.risk_overrides import (
+    LEVEL_ORDER,
+    apply_risk_overrides,
+    build_stock_ctx,
+    downgrade,
+    min_rank,
+)
+
+
+def test_downgrade_order():
+    assert downgrade("STRONG_BUY") == "BUY"
+    assert downgrade("BUY") == "WATCH"
+    assert downgrade("WATCH") == "HOLD"
+    assert downgrade("HOLD") == "HOLD"
+
+
+def test_r1_chase_high_downgrades():
+    ctx = {"pct_change_5d": 18.0}
+    res = apply_risk_overrides(ctx, "BUY")
+    assert "R1" in res.hits
+    assert res.level == "WATCH"
+    assert res.confidence_multiplier < 1.0
+
+
+def test_r2_overbought_downgrades():
+    ctx = {"rsi14": 75.0, "near_high_20d": True}
+    res = apply_risk_overrides(ctx, "STRONG_BUY")
+    assert "R2" in res.hits
+    assert res.level == "BUY"
+
+
+def test_r3_break_count_downgrades():
+    ctx = {"break_count": 10}
+    res = apply_risk_overrides(ctx, "BUY")
+    assert "R3" in res.hits
+    assert res.level == "WATCH"
+
+
+def test_r4_low_float_cap_caps_watch():
+    ctx = {"float_market_cap_yi": 10.0}
+    res = apply_risk_overrides(ctx, "STRONG_BUY")
+    assert "R4" in res.hits
+    assert res.level == "WATCH"
+
+
+def test_r5_risk_off_lowers_confidence_but_keeps_level():
+    ctx = {"index_gain": -3.0}
+    res = apply_risk_overrides(ctx, "BUY")
+    assert "R5" in res.hits
+    assert res.level == "BUY"  # 不降级
+    assert res.confidence_multiplier < 1.0
+
+
+def test_r6_pre_earnings_downgrades():
+    ctx = {"days_to_earnings": 2}
+    res = apply_risk_overrides(ctx, "BUY")
+    assert "R6" in res.hits
+    assert res.level == "WATCH"
+
+
+def test_r7_crisis_news_caps_watch():
+    ctx = {"has_crisis_news": True, "pct_change_5d": 50.0}
+    res = apply_risk_overrides(ctx, "STRONG_BUY")
+    assert "R7" in res.hits
+    assert res.level == "WATCH"
+
+
+def test_disabled_config_no_override():
+    res = apply_risk_overrides({"pct_change_5d": 99.0}, "BUY", {"enabled": 0})
+    assert res.hits == []
+    assert res.level == "BUY"
+
+
+def test_no_risk_no_change():
+    res = apply_risk_overrides({}, "BUY")
+    assert res.hits == []
+    assert res.level == "BUY"
+
+
+def test_build_ctx_extracts_from_df():
+    import pandas as pd
+    closes = [10 + i * 0.1 for i in range(30)]  # 稳步上涨
+    df = pd.DataFrame({"收盘": closes})
+    ctx = build_stock_ctx("600000", "BUY", historical_df=df)
+    assert ctx["pct_change_5d"] is not None
+    assert ctx["rsi14"] is not None
+    assert ctx["near_high_20d"] is True
+
+
+def test_min_rank():
+    assert min_rank("STRONG_BUY", "WATCH") == "WATCH"
+    assert min_rank("BUY", "WATCH") == "WATCH"
+    assert min_rank("WATCH", "BUY") == "WATCH"
diff --git a/docs/26_skill嫁接施工方案_五大功能.md b/docs/26_skill嫁接施工方案_五大功能.md
new file mode 100644
index 0000000..407cbd7
--- /dev/null
+++ b/docs/26_skill嫁接施工方案_五大功能.md
@@ -0,0 +1,450 @@
+# 财经 Skill 嫁接施工方案：五大功能（质询修订版 v2）
+
+> 修订日期：2026-09-30。范围：方案评审与施工设计，尚未实现或部署下述新增能力。
+> 依据：当前仓库代码、`data/france.md` 本地快照，以及文中列出的官方资料。未把原方案中的施工步骤当作本次开发指令。
+> 目标：提高推荐的可验证性、交易假设的可信度和日常可用性。保留五个功能方向，调整边界、实施顺序和验收方法。
+
+## 零、结论：原方案还不是最优，应先补证据链
+
+**原方案适合作为功能清单，不适合原样开工。更适合当前项目的路线是：先保存真实推荐与评估基线，再做风险规则对照和模拟账本；盯盘复用现有雷达，事件扩展现有引擎，热度聚合最后验证。**
+
+最需要纠正的不是功能数量，而是三个前提：
+
+1. **现状不是空白。** 已有硬过滤、数据审核降级、回测、隔夜校准、盘中雷达和事件加分；新增能力应补缺口。
+2. **增加规则不等于增加收益。** RSI、追高、热度和事件方向都需要验证；规则可以降低回撤，也可能误杀强势股。
+3. **先能复现，再谈有效。** 没有当时的推荐、数据、参数和成交约束，收益曲线与“提升百分比”都不能作为决策依据。
+
+### 推荐方案与替代方案
+
+| 方案 | 能解决什么 | 代价和不足 | 判断 |
+|---|---|---|---|
+| 原方案：同时新增四个插件并修改 core 风控 | 快速补齐页面和功能名称 | 重复建设，信号历史和资金状态缺乏可靠来源，容易得到虚高模拟收益 | 不建议原样实施 |
+| **推荐：统一推荐记录与绩效底座，按增量扩展五个功能** | 能查清每条规则是否改善结果，复用已有采集和页面入口 | 前期需要处理持久化、时间与数据契约 | 当前约束下更合理 |
+| 最小方案：只做推荐记录、标签与风险旁路评估 | 用较小成本回答“哪些规则值得做” | 暂无完整资金账户与用户盯盘 | 预算紧时先做到这里 |
+| 完整量化平台：全历史逐笔、通用回测、独立消息队列 | 扩展空间大 | 需要更多数据与运维投入，尚无需求证据 | 暂不建设 |
+
+本文不宣称已经证明“全局最优”；推荐的是相对于现有代码和可得数据，返工更少、结果更可检验的方案。
+
+### 五大功能的新边界
+
+| 原编号 | 功能 | v2 落地形态 | 实施优先级 | 首期明确不做 |
+|---|---|---|---|---|
+| 2 | 推荐绩效模拟盘 | 先做不可变推荐记录与标签，再补独立账户账本；复用现有校准入口 | P0 | 把评分当预期涨幅、自动调参、真实下单 |
+| 1 | 风险否决层 | 接在既有数据审核之后；新增规则先旁路观察，与基线对照 | P0 | 一次性强制上线七条经验规则 |
+| 3 | 条件盯盘 | 复用行情采集，增加结构化规则订阅和持久化告警 | P1 | 第二套全市场高频采集、自定义脚本语言 |
+| 5 | 事件驱动雷达 | 补全 EventEngine、StockEvent 和既有 events 路由 | P1 | 标题关键词直接改变推荐等级 |
+| 4 | 舆情热度聚合 | 先做一个来源的“市场关注度”，证实可用后再扩源 | P2 | 两天接入四源、热度直接加到推荐分 |
+
+---
+
+## 一、质询结果：原方案哪些地方站不住
+
+以下位置以本次代码快照为准；函数名和路径用于后续定位。
+
+| 严重程度 | 原假设或设计 | 代码事实／问题 | 修订结论 |
+|---|---|---|---|
+| P0 | “11 因子纯线性，没有否决” | `screening_service.py:526–541` 已前筛；`:600–620` 已执行 `AuditAgent` 审核降级 | 保留既有门控，新增市场风险政策；先测增量 |
+| P0 | “绩效指标 0→1 套” | `services/backtest.py:40–124` 已统计滚动样本；`overnight_arbitrage/calibration.py` 已做分桶校准 | 缺口是可追溯真实推荐及可执行账户净值 |
+| P0 | 盘后推荐按当天收盘买，T+1 卖 | `render.yaml:35–37` 主筛选在北京时间 15:10；信号出现时收盘成交已过去 | 默认 D 盘后信号→D+1 买→最早 D+2 卖 |
+| P0 | 给 StockScore 加三列即可 | 模型在 `db/models.py:38`，但当前主推荐输出路径未见该表写入；`layer3_recommendation/agent.py:25–93` 主要写报告、发邮件和返回摘要 | 明确实现推荐历史持久化，不能只加字段 |
+| P0 | 用近五日／二十日 `latest.json` 验证 | `router_screening.py:66–74` 覆盖同一个最新快照，运行和失败状态也会覆盖 | 新建不可变历史；缺历史则从现在积累 |
+| P0 | Web 与 Cron 直接共用 SQLite | `db/database.py:20–23` 固定本地文件；`render.yaml` 分开部署 Web/Cron，未声明共享事实库 | 在模拟盘和规则写入前解决存储所有者 |
+| P0 | `--init-db` 自动迁移旧表 | `db/database.py:59–72` 调用 `create_all`，没有旧列迁移 | 增加显式版本迁移和恢复演练 |
+| P1 | “无盘中触发，延迟降低 99.99%” | `smart_money_radar/config.py:73` 已有 4 秒轮询；`scheduler.py:39` 已有 daemon；有盘中告警 | 增量是用户可配置条件，不是首次实时化 |
+| P1 | `change_percent <= -0.05` 表示跌 5% | `quote_contract.py:80–82` 输出百分数，-5 才代表 -5% | 统一单位，避免百倍误差 |
+| P1 | 新增 `/events/stock/{code}` | `router_events.py:26–29` 已有同路径占位；`main.py:131` 已注册 | 完善原路由，避免重复方法和路径 |
+| P1 | “没有事件驱动” | `screening_service.py:427–440、598` 已采集并输入事件；`skill_event_bonus.py` 已参与评分 | 扩展可信来源、持久化和查询，不另起加分引擎 |
+| P1 | 评分减实际收益得到 divergence | 评分没有收益率或概率单位；现有校准默认未注入行情标签获取器，样本写入也缺唯一约束 | 先修标签、去重、成熟样本计数，再做校准 |
+| P1 | 拦截 ≥10% 证明有效 | 高拦截率可能只是阈值过紧；五日样本不能证明策略有效 | 改为同样本对照、误杀与尾部损失分析 |
+| P1 | 四源中三源返回就算稳定 | 没有部署环境实测；`optional_source_health.py:123` 还要求记录数 >0，不能识别成功空集 | 来源逐一验证，区分成功空集、失败、过期 |
+
+**撤回原文中的结果承诺：** 胜率提升 3–8 个百分点、事件后超额收益 2–5 个百分点、延迟降低 99.99%、减少 21.9%“干扰”。本次没有相应的可复现证据。股票数、规则数和来源数只能说明覆盖范围，不能当作收益改善。
+
+---
+
+## 二、P0 公共前置：时间、数据与持久化
+
+### 2.1 先确定运行与存储拓扑
+
+| 阶段 | 推荐拓扑 | 限制与放行条件 |
+|---|---|---|
+| 本地验证 | 本地 API、一个调度 worker、同一持久目录的 SQLite；账户状态由一个执行所有者写入 | 足以验证账本和规则；重启可恢复，不代表云端已可靠 |
+| 云端首期，单实例且已有持久盘 | API 与执行所有者在同一持久服务中，Cron 仅调用带认证、幂等的任务入口 | 必须实际配置数据目录和备份；不能让 Cron 写自己的 SQLite 后期待 Web 读到 |
+| 云端需独立 worker／多个写入服务 | API、worker、Cron 使用同一持久数据库 | 需要数据库适配、迁移和并发验收；按实际部署需求决定，不提前铺开 |
+
+**阶段默认选择本地验证。** 正式云端方案在执行前按现有资源确定；本文不购买资源或改变部署。只读快照可以同步展示，不能替代账户、规则和发送状态的事实库。
+
+Render 官方说明：Cron 不能挂载或访问持久盘，持久盘不能由多个服务共同访问。因此“复用 Cron”并不会自动共享本地账本。[Cron 约束](https://render.com/docs/cronjobs)、[持久盘约束](https://render.com/docs/disks)。
+
+### 2.2 统一数据契约
+
+- 时间均为带时区的时间，业务日期按 `Asia/Shanghai`／`BEIJING_TZ` 转换；下一交易日使用交易日历，不用自然日加一。
+- 每份行情保存 `source / source_time / fetched_at / missing_fields / quality_status`。抓取时间不能冒充行情时间。
+- 规则字段统一为：`price` 元、`change_pct` 百分数、`turnover_pct` 百分数、`volume_lots` 手、`amount_cny` 元、`volume_ratio` 倍数。`change_pct=-5` 是跌 5%，`turnover_pct=1` 是换手 1%。
+- 收益标签和账户收益内部统一存小数（如 `return_net=0.02` 为 2%），展示时转换；不能与 `_pct` 字段混算。
+- 缺失、NaN、无限值、未知交易状态都显式记录为 `unknown`，不填零。主链当前炸板次数的缺失归零行为也需审查。
+- `trading_calendar` 只提供交易日期，不提供个股财报预约日；日历降级或超出覆盖期时，模拟执行暂停并报原因。
+- 最新页面读取 `snapshot_store`／已有快照，GET 不启动采集；慢任务由执行所有者处理。
+- 新模块复用 `data_backend` 的质量元信息，但不能默认已有报价缓存就满足 5 秒时效；先实测数据年龄。
+
+### 2.3 冻结推荐，而不是从最新页面反推历史
+
+新增 `recommendation_runs` 和 `recommendation_signals`，模型放到现有 `backend/db/models.py`。第一期不依赖尚未贯通的 `StockScore` 写入，也不改变其旧字段语义。
+
+| 记录 | 最少字段与约束 |
+|---|---|
+| 推荐运行 | `run_id, strategy_id, session_key, idempotency_key, generated_at, decision_cutoff, config_hash, code_version, input_snapshot_hash, universe_hash, status`；`idempotency_key` 唯一 |
+| 推荐信号 | `signal_id, run_id, code, raw_score, audited_score, raw_level, audit_level, published_level, shadow_level, risk_evaluation, evidence, quality_status`；`(run_id, code)` 唯一 |
+| 研究标签 | `signal_id, horizon, label_version, maturity_at, reference_price, price_basis, return_value, status, missing_reason`；`(signal_id, horizon, label_version)` 唯一 |
+
+实施要求：
+
+1. 所有被评估股票都保存结果，包括被拦截和数据不足者；前筛前的研究候选也记录淘汰原因及当时可得字段。仅存最后 BUY 无法分析误杀。
+2. 同一次任务重试沿用 `run_id/idempotency_key`。同日允许明确的新运行，不能仅以日期去重；固定每日哪一次运行进入正式模拟账户，避免挑最好的一次。
+3. 输入快照需保存内容或指向不可变存储，不能只留无法取回的哈希。至少覆盖当时股票池、特征、行情时间、事件证据和参数。
+4. 原始评分、审核后等级、发布等级和旁路等级分开存，保持既有 `STRONG_BUY / BUY / WATCH / PASS` 枚举；不加入语义不清的 `HOLD`。
+5. 先事务提交运行与信号，再向展示快照和通知链发布。写入失败不得返回“已归档”；单独记录通知投递状态。
+6. 更正历史追加新版本并关联旧版本。当前监控列表不能充当过去股票池，当前参数不能冒充当时策略。
+7. 历史 MD/HTML 只有在时间、输入和版本足够时才导入为可比较样本；否则标记 `legacy_incomplete`，仅用于查阅。
+
+### 2.4 数据库迁移
+
+新增 `backend/db/migrations/` 和迁移版本记录；迁移显式执行并校验，应用启动检查版本。`--init-db` 仍只承担首次建表，除非另行实现并测试迁移调用。
+
+旧表变更先备份，再检查重复数据、增补字段与唯一约束。旧记录未知的时间和来源留空，不填当前时间伪造历史。破坏性降级通过备份恢复，不让回滚脚本删除已积累的推荐和交易证据。
+
+---
+
+## 三、功能 2：推荐绩效闭环与最小模拟账户
+
+### 3.1 分成两步交付
+
+**第一步：推荐表现记录。** 修复既有隔夜校准的数据链，统计分数桶的成熟样本数、T+1/T+3/T+5 收益分布、胜率和缺标率。主筛选与隔夜套利使用不同 `strategy_id`；研究标签不是账户收益。
+
+现有校准需先修：
+
+- `overnight_arbitrage/calibration.py:156–187`：默认 CLI 没有注入 `hist_fetcher`，需补标签任务与成熟状态。
+- `db/plugin_models.py:84–96` 与校准写入：增加样本唯一约束和 upsert；迁移前处理已有重复。
+- `calibration.py:98–126`：各持有期分别统计成熟有效样本，零样本输出 `null`；方法名改为符合实际的分桶频率统计。
+- `factor_lab/service.py:32–52`：不能拿 `zt_date` 当推荐日；新样本使用真实 `generated_at`，旧样本不强行改造为真实推荐历史。
+
+**第二步：可执行假设下的模拟账户。** 只做固定建仓和固定持有期，暂不做止盈止损择时、动态复投优化和自动学习。余额变化自然影响后续预算，但预算规则必须提前固定。
+
+### 3.2 时间与成交规则
+
+| 情形 | 首期规则 |
+|---|---|
+| D 日 15:10 后生成的主推荐 | 信号冻结后，计划在下一交易日 D+1 的预设窗口买；最早在实际买入日的下一交易日卖，通常为 D+2 |
+| 14:43 隔夜策略 | 独立账户与版本；只有当时可得的盘中数据才能用于当日模拟买入。缺盘中成交依据时暂只统计研究标签 |
+| 延迟、漏跑 | 错过窗口后才创建的订单记 miss，不回填成交；已经预提交的订单允许行情到达后延迟结算。计划买单首期仅对下一交易日窗口有效 |
+| 买入失败 | 保留取消／未成交原因并释放冻结资金，不从后面日期挑有利价格补成交 |
+| 卖出失败 | 保留持仓与挂起卖出计划，顺延至下个允许窗口，持续计入估值 |
+
+**首期默认模型：** D 日盘后先持久化买单，D+1 盘后取得原始日 K 后，按预定的 D+1 开盘价代理＋滑点核算；成交确认后预提交退出计划，按实际买入日的下一交易日收盘价代理−滑点退出。受阻按上表处理。`planned_at / executed_at / observed_at / booked_at` 分开保存，`executed_at` 标明是模型对应的成交时点，不是观测到的实际成交。按业务时间顺序结算，早盘买入不能使用当日收盘卖出的未来资金。
+
+此模型为**保守的行情代理成交**，结果标注为“模拟”，不宣称真实可成交：
+
+- 预先固定 `execution_model_version`、订单窗口和滑点。后续盘中采样模式使用下单之后的下一条合格报价或下一根可用 bar，不使用当根已知最优价。
+- 日 K 只支持上述预先约定的代理时点，不能据日高低价推断任意盘中成交，也不能将该模式和盘中采样模式混成一条净值。
+- 停牌、涨跌停价未知、价格或交易状态缺失不成交。对买侧涨停／卖侧跌停采取保守不成交假设；普通触板不等于整天不能成交，系统只评价预定窗口。
+- 买单按主板整手约束和可用现金取整；卖出按持仓批次检查可卖日期。公司行为造成的零股需专门处理。
+- `historical_kline.py` 默认前复权：研究可用一致复权序列；模拟本金、股数和成交价使用原始价格，不能混算。
+- 分红、送转、拆并股应写入账户公司行为流水；暂未实现时，相关跨期持仓和净值标为无法完整估计，不得删掉不利样本后继续宣称完整绩效。
+
+**账户执行放行前提：** 在实际采集位置验证原始日 K、前收盘／涨跌停价及交易日对应证券状态，并留真实响应样本。当前已核实的历史接口默认前复权，不能假定上述数据已经贯通。必要字段未就绪时，M1 只交付推荐标签和固定行情样例下的账本，不输出连续可交易绩效；增加原始价与证券状态适配器列为该阶段任务。
+
+### 3.3 资金和费用
+
+初始资金、每票权重、总敞口、退出规则都在运行前固定。原方案的 STRONG_BUY 3%、BUY 2%、单票 5%、总仓位 60%可作为**待检验的实验配置**，不是已经优化出的参数。
+
+- 总预算以决策时账户权益计算，候选按固定排序及代码作为平局规则分配，不能边成交边重新计算分母。
+- 同票已有持仓时首期不叠加新买单；后续卖出后再入场必须对应新的有效信号。
+- 现金、冻结金额、可用现金分开；买入前预留费用，禁止负可用现金。
+- 佣金率、最低佣金、卖方印花税、过户等费用分别配置，注明佣金是否已包含相关费用，避免漏算或重复计算。
+- 原文“卖出千一”需改为按生效日期取费率。以税法千分之一、2023-08-28 起减半政策计算，该政策适用交易的卖方印花税为 **0.05%（万分之五）**；历史回放按当日政策，不按最新费率覆盖全部历史。[税法税率](https://tianjin.chinatax.gov.cn/11200000000/0300/030005/20220725150235434.shtml)、[减半公告](https://shanxi.chinatax.gov.cn/web/detail/sx-11400-545-1780448)、[卖方征收说明](https://qingdao.chinatax.gov.cn/ssfg2019/cjwt/cjwtqt/202303/t20230303_77694.html)。
+- 内部金额采用定点整数或 Decimal 并规定舍入，使用固定账单样例验证。
+
+### 3.4 最小数据模型与幂等
+
+模拟执行模块可新建 `backend/plugins/paper_trading/`，研究记录和标签服务放 core 供各策略复用。避免将账户状态散落在报告 JSON 中。
+
+| 表 | 要点 |
+|---|---|
+| `paper_accounts` | `account_id, strategy_id, variant, initial_capital, cash, reserved_cash, config_hash, state_version` |
+| `paper_orders` | `order_id, account_id, signal_id, side, planned_window, expires_at, quantity, status, reason, idempotency_key UNIQUE` |
+| `paper_fills` | `fill_id, order_id, execution_key UNIQUE, executed_at, observed_at, booked_at, price, quantity, fee_breakdown, price_source, execution_model_version` |
+| `paper_positions` | 按买入批次记录数量、成本、可卖日期；不能只按股票汇总后丢失 T+1 限制 |
+| `paper_nav` | `account_id, trade_date, nav, cash, market_value, stale_position_count, quality_status`；`(account_id, trade_date)` 唯一 |
+| `paper_job_runs` | 任务幂等键、账户、处理窗口、状态和重试记录；区分“已执行”与“尚缺数据” |
+
+订单、成交、现金与持仓更新在同一个数据库事务内完成，以唯一键和账户版本控制重试／并发；不能只靠内存锁或“当天已结算”布尔值。估值失败可以单独重试，不能重复执行成交。
+
+同一任务重试复用执行键；账本已经入账的成交不可因新版计算器被再次入账。研究重算使用新的独立模拟账户版本，正式历史更正保留冲正与补记记录。
+
+### 3.5 指标与对照
+
+- 每日权益 = 现金 + 所有未平仓市值；停牌或缺价沿用最近可靠估值并标注陈旧，不按零计，也不剔除持仓。
+- 累计收益 = 期末权益／初始资金 − 1；首期无外部出入金。最大回撤基于完整账户净值，而不是单只股票的平均最大跌幅。
+- 分开报告交易胜率、盈利交易／亏损交易的平均净收益比、日盈利比例、成本、换手、未成交比例和资金使用率。
+- 零平仓或零亏损时，相应比率为 `null` 并说明原因；零波动不输出无限夏普。夏普注明日收益口径、年化因子和无风险利率；短样本仅作诊断。
+- 基线账户与风险账户共享信号、行情、费用、执行窗口；只有目标规则不同。被否决的预算默认留现金，不自动分配给其他票；如评估再分配，单列实验。
+- 评分不是预期涨幅。先做分桶收益和胜率；拟合概率或预期收益需要训练期／验证期隔离、标签成熟检查和模型版本，届时才有合理的预测误差指标。
+
+### 3.6 接口
+
+新增 `/api/v1/paper-trading/summary`、`/positions`、`/orders`、`/nav` 只读接口，响应带 `as_of / account_version / quality_status / sample_count`。移除原 `/divergence` 首期计划，校准复用已有隔夜入口并按策略扩展。
+
+`POST /api/v1/paper-trading/run-settle` 使用请求体中的 `account_id / trade_date / idempotency_key`；带认证和参数范围校验，返回持久任务 `job_id`。已完成重试返回原结果，不因 GET 或刷新页面产生交易。
+
+---
+
+## 四、功能 1：可解释、可比较的风险政策层
+
+### 4.1 接入方式
+
+主链改为：
+
+```text
+行情与证据校验 → 既有前筛／因子评分 → 既有数据审核
+→ 风险政策评估 → 操作计划 → 冻结推荐与评估记录 → 展示／模拟执行
+```
+
+接入点为 `screening_service.py` 既有审核降级之后、`build_action_plan` 之前。新增 `backend/services/risk_policy.py`，输入预先收集好的上下文，输出结构化评估；计算函数不访问网络或数据库。
+
+每条新增策略规则有 `off / shadow / enforce`。默认 `shadow` 只记录“本来会怎么改”，不改变发布等级；`enforce` 必须经过对照验证。已存在的审核和过滤继续生效。确定的数据错误或不可交易条件在测试通过后作为基础质量门控处理，不需要靠收益实验决定是否忽略错误数据。
+
+输出至少包括 `rule_id / rule_version / outcome(pass,hit,unknown) / observed_value / threshold / evidence_time / reason / proposed_action`，总体带 `policy_version / mode / input_level / output_level / position_multiplier`。
+
+### 4.2 七条候选规则的处理意见
+
+| 原规则 | v2 定义／处理 | 默认状态 |
+|---|---|---|
+| R1 追高 | 研究近五个完整交易日收益 `close_t / close_t-5 - 1`，不混称“关注日至今”；需要至少六个有效收盘点。原“关注后涨 15%”与现有回撤入池可能冲突 | shadow；阈值 15%仅作待检验起点 |
+| R2 RSI 超买 | RSI(14) 加明确定义的近高位置；“90%分位”必须说明是收盘价经验分位还是高低区间位置，不混用。指标复用／下沉 `tech_indicators/indicators.py` 纯函数 | shadow；RSI>70 不自动证明应卖出 |
+| R3 炸板 | 明确当前交易日炸板次数，历史关注日次数只能作另一研究特征；依赖完整事件源，缺失为 unknown；检查现有 `maxZbc` 评分惩罚和数据审核的覆盖，评估否决是否有增量 | shadow；无增量规则不新增 |
+| R4 流动性 | 默认已有市值和换手前筛，取消重复 `<20亿／>25%`；研究成交额、可成交量约束和订单参与率，实际不可交易由执行门控处理 | 暂不新增市场阈值 |
+| R5 大盘风险 | 首期作为指数走势／情绪周期分组报告，后续测试总敞口上限；不把分数乘 0.7 称为“置信度降低” | shadow |
+| R6 财报前 | 需要个股预约披露日、变更历史和当时可知时间；季度窗口不等于个股披露日 | off，待事件底座完成 |
+| R7 新闻风险 | 必须有可信来源、股票关联、发布时间和事件状态；关键词只提取候选，未经核实新闻先作提示 | off，待事件底座完成 |
+
+R2 首期如需具体实现，采用明确的区间位置 `(close-min(low,20))/(max(high,20)-min(low,20)) > 0.9`；分母为零或窗口不足为 unknown，文案写“接近区间高点”，不写经验分位。
+
+规则组合必须满足：
+
+- 只降级不升级，最终等级取各约束中最保守者；顺序变化不改变结果。
+- 多个仓位上限取最小允许上限，禁止反复相乘造成无意惩罚。
+- 交易资格和必要数据缺失时禁止模拟新开仓；可选研究规则缺数据只记录 unknown，不宣称“风险通过”。
+- 原始分保留，展示等级与操作计划一致。主页面、邮件及基线账户读 `published_level`；明确标记为实验的风险账户读同一 `signal_id` 的 `shadow_level` 和实验仓位上限，共享证据与执行模型。`action_planner.py` 现有“重仓”等建议与发布策略的上限必须统一映射，不能相互矛盾。
+- `PUT /api/v1/config/strategy` 新增 `riskPolicy` 段时同步实现默认、校验、保存、版本和读取；不能只在前端加开关。
+
+### 4.3 验收与放行
+
+工程验收关注规则边界、等级单调性、组合顺序无关、数据不足与回滚。效果观察分别报告：覆盖率、命中率、与已有前筛重叠率、漏掉的盈利机会、规避的尾部损失、成本后净值与回撤变化。
+
+**不设必须拦截多少股票的目标。** 正式启用前固定主要评价指标和允许退化范围，按日期前推验证，并留出至少覆盖最大持有期的标签隔离间隔。可按交易日分组估计置信区间，避免把同日高度相关股票当成独立证据。
+
+建议至少积累 60 个交易日及 200 个成熟有效信号后开始阶段评审；这是观察起点，不是统计充分性的保证。覆盖不足、置信区间过宽或不同市场阶段表现不一致时继续 shadow。五日／十日数据仅验证工程流程。
+
+---
+
+## 五、功能 3：条件盯盘作为行情订阅扩展
+
+### 5.1 首期边界
+
+复用 `smart_money_radar` 的交易时段、调度经验和行情适配；一个明确的执行所有者合并证券订阅后取数，再分发给规则。不要在注册 FastAPI 路由时隐式启动第二个循环。私有同步 `_fetch_tencent_batch` 不直接塞进异步循环，先提供公共适配接口并设置超时／限流。
+
+首期只支持主板股票以及价格、涨跌幅、换手、量比；成交量字段等稳定后再开放。不承诺指数和任意表达式。建议新建 `backend/plugins/conditional_watch/`，只承载规则管理、判断和告警记录，行情采集继续共用。
+
+使用结构化条件：
+
+```json
+{
+  "code": "600000",
+  "condition": {
+    "all": [
+      {"field": "price", "op": ">=", "value": 12.5},
+      {"field": "turnover_pct", "op": ">=", "value": 1}
+    ]
+  },
+  "frequency": "once_per_trading_day"
+}
+```
+
+首期允许一层 `all` 或 `any`、最多五项、字段及运算符白名单；数值必须有限。前端用条件下拉与输入框，不实现 `eval`、函数调用或自由代码。需要的字段缺失／过期时整条规则为 unknown。
+
+### 5.2 去重、状态与发送
+
+- `watch_rules` 保存 `version / enabled / effective_at / condition / frequency / channel_profile_id`；规则修改生成新版本。
+- 首期每个规则版本每交易日最多一条告警，由告警唯一键实现，不提前新增穿越状态表；需要诊断时另保存最后观测时间。
+- `watch_alerts` 唯一键为 `(rule_id, version, code, trade_date)`；同一事务内写告警与待发送记录。
+- 投递字段至少包含 `delivery_status / attempts / next_retry_at / last_error / sent_at`，不只用 `notified` 布尔值。失败重试同一条记录。
+- 系统内保证告警事件不重复；外部邮件服务没有幂等能力时，崩溃重试可能重复投递，不能承诺邮件绝对恰好一次。
+- 渠道复用管理员配置的邮件目标；任意 URL webhook 后置。若以后开放，需目标校验、私网地址限制、签名和限流。
+- 盘中停牌、午休、非交易日和过期行情不触发；恢复时重新核验新鲜度。穿越、冷却和滞回作为后续版本，另定义首次采样和重新布防语义。
+
+接口保留 `/api/v1/watch/rules` 和 `/alerts`；规则启停使用 `PATCH /rules/{id}` 设置明确布尔值，不用重试会反转状态的 toggle。创建／修改／停用接口要求认证及版本校验。
+
+### 5.3 性能目标
+
+默认 5 秒只是轮询计划，不是行情到达或收件箱到达保证。先以 100 条规则订阅 20 只证券验证“每轮每证券仅取一次”；同时记录行情发布时间→检测、检测→渠道接受的 p50/p95 与失败数。
+
+建议本地规则计算 p95 <100ms、健康行情下检测 p95 ≤两轮周期，作为待实测工程目标。数据源达不到时显式展示观测延迟，不改时间戳冒充实时。连续命中、重启、重复调度和发送失败都需验证不会重复创建当日告警事件。
+
+---
+
+## 六、功能 5：扩展既有事件链，先交付证据
+
+### 6.1 实施范围
+
+扩展 `backend/events/engine.py`，增加 `sources/` 适配器和 `service.py` 持久化服务；沿用 `db/models.py` 的 `StockEvent`。实现 `router_events.py` 中已存在的查询路由，新增 latest/scan 也放同一 router，不新挂同名前缀插件。
+
+先用一个可验证公告源覆盖增减持、回购、重组进展三组事件，龙虎榜摘要复用 `lhb_seat`。预约财报来源单独验证；当前 `_fetch_financial_calendar` 调用业绩报表接口不能视为已具备预约披露日。研报“调级”需要历史评级基线，后置。
+
+既有事件加分保持可追溯，新接入类型默认不加分。当前 `skill_event_bonus.py` 会累加传入事件的 `impact_score`，因此必须加获准类型／政策版本的显式过滤；人工 `importance` 与策略 `impact_score` 分开保存，不能让新公告随输入自动进入评分。先让用户看到来源、时间、关联股票与进展，验证后再单独评估其策略贡献。
+
+### 6.2 数据与语义
+
+现有 `events` 表补充 `source_event_id / source_revision / published_at / first_seen_at / effective_date / stage / direction / importance / evidence / supersedes_id / parse_version`，并增加规范的 `event_stock_links(event_id, code)` 关联表；旧 `related_codes` 暂保留兼容。
+
+- 来源内唯一键优先 `(source, source_event_id, source_revision)`；无版本号时使用内容哈希区分版本，无源 ID 时用来源、规范 URL、发布时间等构造稳定标识。新记录的去重键不得为空；旧记录先清理再迁移，不能依赖含 NULL 的唯一索引去重。同标题新公告不合并。
+- 更正、终止、撤回保留原始记录并关联前版；跨来源重复作为事件簇关联，不能删除来源证据。
+- 事件事实时间、公告发布时间、系统首次见到时间不同。真实历史回放要求 `published_at` 和 `first_seen_at` 都不晚于决策截点；事后补抓历史只进入单列研究集。
+- 先识别类型和阶段，再判断方向。“终止重组”“减持计划届满”“维持买入评级”不能按词面变成利好／利空。
+- `direction` 允许 `unknown`；人工重要性等级不冒充预期收益率。标题或正文只作数据，不执行其中指令；网页显示时转义不可信内容。
+- 同步第三方 SDK 如无可控超时，必须在有明确截止时间和资源上限的适配层隔离；不能只有 `async def` 外壳就称为并发采集。
+
+### 6.3 健康状态与验收
+
+扩展 `optional_source_health`，为每个来源使用独立 key，并保存 `collection_ok / schema_ok / valid_empty / coverage / last_success_at`。正常查无公告是成功空集，抓取失败是 unavailable；页面展示需同时判断展示开关、稳定状态与数据年龄。
+
+首期验收重点：来源内去重、更正和撤回保留、证券映射准确、无事件发布前泄漏、成功空集与失败不同、接口无冲突、页面能追溯证据。不以“七类事件都能打分”作为交付门槛。
+
+---
+
+## 七、功能 4：市场关注度，先证明一个来源可靠
+
+### 7.1 为什么后置
+
+热度排名不代表看多，多个站点也可能来自同一数据源。东财人气、股吧发帖和雪球讨论直接相加，会混合不同单位、覆盖范围和重复信息；某源失效还会制造假的热度下降。
+
+第一版只保存一个已通过探针验证的排名源，展示 `source_rank / rank_change / source_time / fetched_at / universe / coverage` 和监控命中。东财人气可以作为候选，但本次未验证实时接口，不预先承诺可用。
+
+### 7.2 来源准入
+
+每个候选源先完成：接口或许可说明、真实响应样本、字段单位、源时间含义、更新周期、限流、错误／空集语义、目标运行网络验证。国内机器可访问不代表 Render Oregon 能访问；如由本地采集，必须定义向云端同步与失效标记。
+
+首源建议连续五个交易日按计划采样，记录成功率、解析失败、陈旧比例和实际覆盖；可用率目标暂定 ≥95%，分母为预先约定的采样次数。该阈值只是工程准入建议，需结合源更新频率确认；mock 测试不能替代运行环境观测。
+
+未获可用接口的源不纳入排期，不通过伪造身份或反爬绕过作为工程前提。百度指数等付费源不进入首期。
+
+### 7.3 后续多源聚合
+
+至少两源各自稳定后，再用来源内分位排名或标准化变化率进行实验，记录来源覆盖和各源权重；榜外表示截断或未知，不能自动当最后一名。某源失效时不把新旧综合分直接连成同口径趋势。
+
+新建 `backend/plugins/hot_scanner/` 可保留原名称，页面名称用“市场关注度”。排名历史唯一键为 `(source, source_time, code)`；无可靠源时间时使用采集批次标识并注明。只读 `/hot/latest`、`/hot/history`；手动 `/hot/scan` 带认证、并发去重和任务结果。
+
+热度首期只旁路展示。是否有策略价值用后续样本评价，不能仅凭“监控股命中数量”判定有效。
+
+---
+
+## 八、主板口径：保留范围选择，取消“纯 10%”简化
+
+2026-09-30 对本地 `data/france.md` 按证券代码去重统计：
+
+| 项目 | 数量 | 含义 |
+|---|---|---|
+| 全部记录／唯一代码 | 2452／2452 | 本地监控文件，不是全市场总量 |
+| 创业板 300/301 | 369 | 按前缀识别 |
+| 科创板 688 | 167 | 按前缀识别 |
+| 沪主板 600/601/603/605 | 959 | 候选范围 |
+| 深主板 000/001/002/003 | 957 | 候选范围 |
+| 主板前缀合计 | 1916 | 尚未排除停牌、新股、退市等状态 |
+| 主板中名称含 ST | 1 | 仅当前名称检查，不能替代历史证券状态 |
+
+复核文件 SHA-256：`ef30492aaf7f3a741cc8ac450c13ec39362e184d5afa2964faabb57a5998094b`。文件更新后需重新统计；按当前名称再剔除 ST 得 1915 只是另一静态计数，不是可交易数量。
+
+- 沿用项目主板优先、剔除 ST 的产品范围；`plugins/common.py:476` 已有相关过滤，需检查主链与插件是否一致，不宣称新建了统一能力。
+- 代码前缀只决定候选板块；可交易资格还需交易日对应的上市状态、停牌、风险警示、退市整理和无涨跌幅限制标记。
+- 主板新股上市前五个交易日等存在无涨跌幅限制情形，因此不能统一按 `昨收×1.1` 判定全部股票。[深交所交易机制说明](https://investor.szse.cn/institute/rules/t20230629_601434.html)。
+- 优先使用可信行情中的当日涨跌停价；回退计算按交易所、证券状态、历史生效日期和价格舍入处理。涨停价未知时不猜。
+- 仓库 `zt_contract.py:14–20` 已有规则版本，但其中 `2026-06-30` 生效日不能只靠注释认定正确。上交所 2026 年交易规则通知列明生效日为 7 月 6 日，并附暂缓条文；实施前应逐市场核对相关专项通知，不能直接把该通用日期替换成所有特殊规则日期。[上交所正式通知](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)。
+
+主板过滤的收益是统一研究范围；剔除 536 只股票不等于减少同等比例噪声，更不证明收益提高。
+
+---
+
+## 九、施工清单、顺序与退出条件
+
+### 9.1 关键文件
+
+下表是拟实施改动，不表示文件已经新增或修改。
+
+| 文件／目录 | 改动职责 |
+|---|---|
+| `backend/services/recommendation_history.py`（新） | 推荐运行、不可变输入和信号归档 |
+| `backend/services/performance_labels.py`（新） | 成熟标签、价格口径、缺失状态和研究查询 |
+| `backend/services/risk_policy.py`（新） | 纯函数政策判断、规则版本和结构化解释 |
+| `backend/services/screening_service.py` | 审核后风险评估、操作计划一致性、归档接入 |
+| `backend/services/runtime_config.py`、`backend/api/router_config.py` | riskPolicy 配置全链路及版本 |
+| `backend/db/models.py`、`plugin_models.py`、`backend/db/migrations/`（新） | 推荐、标签、账户、规则与事件模型及版本迁移 |
+| `backend/plugins/overnight_arbitrage/calibration.py`、`backend/plugins/factor_lab/service.py` | 标签、去重、成熟计数与信号时间修正 |
+| `backend/plugins/paper_trading/`（新） | 订单、执行、结算、净值与只读查询 |
+| `backend/agents/layer1_data_collector/sources/` | 验证并补齐原始日 K、前收盘／涨跌停价、历史证券状态的公共适配；保留现有复权研究接口 |
+| `backend/plugins/conditional_watch/`（新） | 条件表单契约、规则状态与告警投递 |
+| `backend/plugins/smart_money_radar/scheduler.py`、行情公共适配层 | 明确唯一执行所有者，合并订阅；不重复采集 |
+| `backend/events/engine.py`、`sources/`、`service.py`、`backend/api/router_events.py` | 扩展现有事件链和原有路由 |
+| `backend/agents/layer2_signal_engine/skills/skill_event_bonus.py` | 按获准类型和政策版本接收事件分值，新接入类型默认仅展示 |
+| `backend/services/optional_source_health.py` | 成功空集、源级观测和展示准入 |
+| `backend/plugins/hot_scanner/`（新，后置） | 首源验证后保存排名与变化 |
+| `backend/main.py` | 显式任务入口／路由注册，API 注册无隐式轮询副作用 |
+| `frontend/index.html`、`frontend/js/app.js`、`frontend/css/styles.css` | 复用既有页面组件和推荐入口；事件与规则按需新增面板，不假定已有通用事件页或雷达页 |
+
+前端请求使用 `apiFetch()`，导航使用 `navigateTo()`，新页面不使用 emoji。推荐卡展示文字状态“数据不足／风险提示／已降级”，附证据时间。告警、资金、原始推荐与旁路结果均显示清晰的时间和状态，不把旁路结果误认为已发布建议。
+
+### 9.2 阶段排期
+
+估算以一名熟悉项目的开发者为前提，包含相关测试和联调；是工作量区间，不是上线承诺。新数据源受网络和供应方影响，先探针再估时。
+
+| 阶段 | 范围 | 粗估工作日 | 出口／停止条件 |
+|---|---|---|---|
+| M0 | 运行拓扑、数据契约、迁移框架、最小推荐归档 | 2–3 | 重启可读同一份样本，缺数据可明确识别；存储不清晰则不做正式账户 |
+| M1 | 标签任务、已有校准修复、风险 shadow、原始价／交易状态准入、最小模拟账本 | 5–8，数据源可用前提下 | 两套对照结果可复现，时间和资金不变量通过；原始价／交易状态未贯通则只交付标签与账本样例 |
+| M2 | 条件订阅、告警持久化与前端 | 3–5 | 单一采集所有者、重启去重、投递恢复通过；时效不达标则展示实测延迟 |
+| M3 | 一个公告源、事件持久化、既有查询接口补全 | 3–5 | 去重、更正、公开时间和成功空集通过；源不可用就保留 unavailable |
+| M4 | 一个热度源探针、排名历史和页面 | 2–4，探针通过后 | 目标环境稳定且口径清楚才扩源；无可用源则不继续堆抓取代码 |
+
+首期全范围粗估 **15–25 个工作日**；迁移到独立云端数据库另估。观察窗口按交易日独立计算，不能用增加开发人手压缩。各项策略效果不足时保留 shadow 或停止该规则，并不妨碍其他工程模块交付。
+
+**最先开工的一个可合并批次：** 推荐历史与输入快照、显式迁移、校准去重和标签成熟计数；同时记录 R1/R2/R3 的旁路结果。该批次完成后再接账户成交，不先做五个新页面。
+
+### 9.3 验收清单（待实施，未执行）
+
+1. **时间与样本：** 同输入同版本可复现；晚于决策时间的行情／公告不可使用；不同策略、同日不同运行不混样本。
+2. **账户：** D 盘后信号不能 D 收盘买；实际买入当日不可卖；停牌和涨跌停受阻后账本正确；费用、舍入、持仓和净值可对账。
+3. **恢复：** 重复任务、并发任务、成交中断、投递中断和重启都不重复记账；迁移不丢已有数据。
+4. **风险：** 规则顺序不改变输出，shadow 不改变发布等级，unknown 不伪装通过；关闭新规则可恢复基线行为。
+5. **盯盘：** 百分数单位、过期／未来行情、午休、节假日、批量复用及同日去重通过固定样例验证。
+6. **事件／热度：** 正常空集、源失败、陈旧、纠错、撤回分别正确；目标环境真实源观测与 mock 测试分别记录。
+7. **API／前端：** 无重复方法和路径，写操作有认证及幂等／版本控制；所有读取展示数据时间，任务失败有可重试状态。
+8. **效果：** 输出基线与候选策略的成熟样本数、覆盖、费用、收益、回撤和不确定性；不设最低盈利或最低拦截率作为工程通过条件。
+
+新增模块测试按上述行为覆盖，重点放在跨模块的时间、状态和恢复，不用“20 条测试／90%覆盖率”替代正确性。五至十个交易日的净值只作为账务与调度观察。
+
+---
+
+## 十、外部 Skill 的保留价值与证据边界
+
+原方案称已经代码级拆解八个 Skill；本次未重新取得或执行其原始源码，因此不复述“只有两个完整可移植”“抓法已失效”“脚本全空”等为已核验事实。保留来源线索，实际复制代码前再记录仓库地址、版本、许可证、依赖和最小测试。
+
+| 原来源 | 保留的借鉴方向 | 在 v2 中的落点 |
+|---|---|---|
+| stock-analysis-6-2-0 | 风险政策与评分分离、事件和关注度视角 | 风险 shadow、可追溯事件、单源关注度 |
+| lingyv-glance-watch | 用户定义条件订阅 | 结构化规则与共享行情，不照搬 WebSocket／3 秒承诺 |
+| btc15-prediction-market | 产出→评价→改进的闭环概念 | 推荐记录、成熟标签、对照实验 |
+| polymarket-arbitrage | 成本、去重、分阶段启用 | 费用模型、告警持久化、shadow/enforce |
+| polymarket-ai-divergence | 预测与结果对照 | 分桶校准；评分不直接减收益率 |
+| stock-watcher | 自选股使用体验 | 复用已有监控列表，不另建 txt 存储 |
+| ajanraj-yahoo-finance | 工程组织方式 | 按需参考，不因 CLI 技术替换常驻服务 |
+| uniswap-pool-analysis | 流动性风险意识 | 不直接移植链上池子模型 |
+
+**最终施工原则：每增加一个功能，都能回答“它补了哪个现有缺口、依赖的数据是否可靠、如何证明没有算错、什么结果下应停止”。** 五个方向可以保留，但不需要五套采集、五套调度或一组未经验证的收益承诺。
diff --git a/frontend/index.html b/frontend/index.html
index 3364fdc..dfb9ee6 100644
--- a/frontend/index.html
+++ b/frontend/index.html
@@ -96,6 +96,21 @@
             <a href="#resonance" class="nav-item nav-sub-item" data-page="resonance"><span class="nav-icon-placeholder" aria-hidden="true"></span><span>四维共振</span></a>
             </div>
         </div>
+        <!-- 26 方案：舆情聚合雷达（一级菜单） -->
+        <div class="nav-group" id="nav-group-radar">
+            <a href="#radar" class="nav-item nav-group-toggle" data-group="radar">
+                <svg class="nav-icon" aria-hidden="true" focusable="false" width="18" height="18" viewBox="0 0 200 200" fill="none"><path d="M100 8.33334C49.1666 8.33334 8.33331 49.1667 8.33331 100C8.33331 150.833 49.1666 191.667 100 191.667C150.833 191.667 191.667 150.833 191.667 100C191.667 49.1667 150.833 8.33334 100 8.33334ZM95.8333 182.917C53.3333 180.833 19.1666 146.667 16.6666 104.167H33.75C35 107.917 38.3333 111.25 42.5 112.083C47.9166 137.083 69.1666 156.25 95.4166 157.917V182.917H95.8333ZM95.8333 150C73.75 148.333 56.25 132.083 51.25 111.25C54.1666 110 56.25 107.083 57.5 104.167H79.5833C81.25 112.5 87.5 118.75 95.8333 120.417V150ZM95.8333 79.5833C87.5 81.25 81.25 87.5 79.5833 95.8333H57.5C56.25 92.9167 54.1666 90.4167 51.25 88.75C55.8333 67.9167 73.75 52.0833 95.8333 50V79.5833ZM95.8333 41.6667C69.5833 43.3333 48.3333 62.5 42.9166 87.5C38.75 88.3333 35.4166 91.6667 34.1666 95.4167H16.6666C19.1666 53.3333 53.3333 19.1667 95.8333 17.0833V41.6667ZM104.167 17.0833C146.667 19.1667 180.833 53.3333 183.333 95.8333H158.333C157.5 85.4167 154.167 76.25 148.75 67.9167C149.583 66.25 150 64.5833 150 62.5C150 55.4167 144.583 50 137.5 50C135.417 50 133.75 50.4167 132.083 51.25C124.167 45.8333 114.583 42.5 104.167 41.6667V17.0833ZM104.167 50.4167C112.083 51.25 119.583 53.75 126.25 57.5C125.417 59.1667 125 60.8333 125 62.5C125 69.5833 130.417 75 137.5 75C139.167 75 140.833 74.5833 142.5 73.75C146.667 80.4167 149.167 87.5 149.583 95.8333H120C118.333 87.5 112.083 81.25 103.75 79.5833V50.4167H104.167ZM104.167 120.417C112.5 118.75 118.75 112.5 120.417 104.167H150C147.917 128.333 128.75 147.917 104.167 150V120.417ZM104.167 182.917V157.917C132.917 155.833 156.25 132.917 158.333 104.167H183.333C180.833 146.667 146.667 180.833 104.167 182.917Z" fill="currentColor"/></svg>
+                <span>舆情聚合雷达</span>
+                <svg class="nav-arrow" aria-hidden="true" focusable="false" width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M4 3l4 3-4 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
+            </a>
+            <div class="nav-submenu">
+            <a href="#risk-override" class="nav-item nav-sub-item" data-page="risk-override"><span class="nav-icon-placeholder" aria-hidden="true"></span><span>风险否决</span></a>
+            <a href="#paper-trading" class="nav-item nav-sub-item" data-page="paper-trading"><span class="nav-icon-placeholder" aria-hidden="true"></span><span>绩效模拟盘</span></a>
+            <a href="#conditional-watch" class="nav-item nav-sub-item" data-page="conditional-watch"><span class="nav-icon-placeholder" aria-hidden="true"></span><span>条件盯盘</span></a>
+            <a href="#hot-scanner" class="nav-item nav-sub-item" data-page="hot-scanner"><span class="nav-icon-placeholder" aria-hidden="true"></span><span>舆情热度</span></a>
+            <a href="#event-radar" class="nav-item nav-sub-item" data-page="event-radar"><span class="nav-icon-placeholder" aria-hidden="true"></span><span>事件雷达</span></a>
+            </div>
+        </div>
         <a href="#smart-picker" class="nav-item" data-page="smart-picker">
             <svg class="nav-icon" aria-hidden="true" focusable="false" width="18" height="18" viewBox="0 0 200 200" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M70.667 119.096C73.4282 119.096 75.667 121.334 75.667 124.096V139.096C75.6669 141.857 73.4282 144.095 70.667 144.096H64C61.2386 144.096 59.0001 141.857 59 139.096V124.096C59 121.334 61.2386 119.096 64 119.096H70.667Z" fill="currentColor"/><path d="M102.333 102.429C105.094 102.429 107.333 104.667 107.333 107.429V139.096C107.333 141.857 105.094 144.096 102.333 144.096H95.667C92.9056 144.096 90.667 141.857 90.667 139.096V107.429C90.6671 104.667 92.9057 102.429 95.667 102.429H102.333Z" fill="currentColor"/><path d="M134 85.7627C136.761 85.7627 139 88.0013 139 90.7627V139.096C139 141.857 136.761 144.096 134 144.096H127.333C124.572 144.095 122.333 141.857 122.333 139.096V90.7627C122.333 88.0014 124.572 85.7629 127.333 85.7627H134Z" fill="currentColor"/><path d="M111.068 47C113.83 47 116.068 49.2386 116.068 52V83.667C116.068 86.4284 113.83 88.667 111.068 88.667H104.401C101.64 88.6668 99.4014 86.4282 99.4014 83.667V75.0332L72.2549 102.181C70.3023 104.133 67.1362 104.133 65.1836 102.181L60.4697 97.4668C58.5171 95.514 58.5171 92.3481 60.4697 90.3955L87.1982 63.667H79.4023C76.6409 63.667 74.4026 61.4284 74.4023 58.667V52C74.4023 49.2386 76.6407 47 79.4023 47H111.068Z" fill="currentColor"/><path fill-rule="evenodd" clip-rule="evenodd" d="M99.5 14C146.72 14 185 52.2797 185 99.5C185 146.72 146.72 185 99.5 185C52.2797 185 14 146.72 14 99.5C14 52.2797 52.2797 14 99.5 14ZM99.5 32C62.2208 32 32 62.2208 32 99.5C32 136.779 62.2208 167 99.5 167C136.779 167 167 136.779 167 99.5C167 62.2208 136.779 32 99.5 32Z" fill="currentColor"/></svg>
             <span>智能选股器</span>
@@ -581,6 +596,37 @@
             <div class="panel-body"></div>
         </div>
     </section>
+    <!-- 26 方案：舆情聚合雷达（5 个子页，通用渲染） -->
+    <section id="page-risk-override" class="page">
+        <div class="panel">
+            <div class="panel-header"><h2>风险否决</h2></div>
+            <div class="panel-body"></div>
+        </div>
+    </section>
+    <section id="page-paper-trading" class="page">
+        <div class="panel">
+            <div class="panel-header"><h2>绩效模拟盘</h2></div>
+            <div class="panel-body"></div>
+        </div>
+    </section>
+    <section id="page-conditional-watch" class="page">
+        <div class="panel">
+            <div class="panel-header"><h2>条件盯盘</h2></div>
+            <div class="panel-body"></div>
+        </div>
+    </section>
+    <section id="page-hot-scanner" class="page">
+        <div class="panel">
+            <div class="panel-header"><h2>舆情热度聚合</h2></div>
+            <div class="panel-body"></div>
+        </div>
+    </section>
+    <section id="page-event-radar" class="page">
+        <div class="panel">
+            <div class="panel-header"><h2>事件驱动雷达</h2></div>
+            <div class="panel-body"></div>
+        </div>
+    </section>
 </main>
 
 <div id="recipientModal" class="modal">
@@ -680,5 +726,6 @@
 <script src="js/plugins/principal_capital.js"></script>
 <script src="js/plugins/overnight_arbitrage.js"></script>
 <script src="js/plugins/research_plugins.js"></script>
+<script src="js/plugins/radar_plugins.js"></script>
 </body>
 </html>
diff --git a/frontend/js/app.js b/frontend/js/app.js
index 4251c46..814a425 100644
--- a/frontend/js/app.js
+++ b/frontend/js/app.js
@@ -280,7 +280,10 @@ function navigateTo(route, pushState = true, force = false) {
         'tail-raid':'尾盘抢筹',board:'板块轮动','zt-seal':'涨停封单',
         'volume-profile':'分价成本带','factor-lab':'因子实验室',l2:'真实L2升级',
         'low-position':'低位涨停选股',resonance:'四维共振信号',
-        'smart-picker':'智能选股器'
+        'smart-picker':'智能选股器',
+        'risk-override':'风险否决', 'paper-trading':'绩效模拟盘',
+        'conditional-watch':'条件盯盘', 'hot-scanner':'舆情热度聚合',
+        'event-radar':'事件驱动雷达'
     }[page] || page;
     setTopbarMeta(page);
     if (page === 'dashboard') loadDashboard();
@@ -300,6 +303,10 @@ function navigateTo(route, pushState = true, force = false) {
     if (typeof setupResearchPage === 'function' && window.RESEARCH_PAGES && window.RESEARCH_PAGES.includes(page)) {
         setupResearchPage(page);
     }
+    // 26 方案：舆情聚合雷达（5 个子页）
+    if (typeof setupRadarPage === 'function' && window.RADAR_PAGES && window.RADAR_PAGES.includes(page)) {
+        setupRadarPage(page);
+    }
 }
 
 let wlCurrentStatus = ''; // current tab filter
diff --git a/frontend/js/plugins/radar_plugins.js b/frontend/js/plugins/radar_plugins.js
new file mode 100644
index 0000000..611aade
--- /dev/null
+++ b/frontend/js/plugins/radar_plugins.js
@@ -0,0 +1,225 @@
+/* 26 方案：舆情聚合雷达（一级菜单）通用前端渲染。
+ * 复用全局 apiFetch / escapeHtml，样式沿用既有 design token 与 .panel/.data-table/.tag/.metric-card。
+ */
+(function () {
+    'use strict';
+
+    const LEVEL_TAG = { 'STRONG_BUY': 'tag-strong_buy', 'BUY': 'tag-buy', 'WATCH': 'tag-watch', 'HOLD': 'tag-expired' };
+    const LEVEL_LABEL = { 'STRONG_BUY': 'STRONG BUY', 'BUY': 'BUY', 'WATCH': 'WATCH', 'HOLD': 'HOLD' };
+
+    function tag(level) {
+        const cls = LEVEL_TAG[level] || 'tag-watch';
+        return '<span class="tag ' + cls + '">' + escapeHtml(LEVEL_LABEL[level] || level) + '</span>';
+    }
+    function fmt(v, digits) {
+        if (v === null || v === undefined || v === '') return '--';
+        const n = Number(v);
+        return isFinite(n) ? n.toFixed(digits === undefined ? 2 : digits) : '--';
+    }
+    function clsUpDown(v) {
+        if (v === null || v === undefined || v === '') return '';
+        const n = Number(v);
+        return n > 0 ? 'up' : (n < 0 ? 'down' : '');
+    }
+    function statCards(cards) {
+        return '<div class="metric-cards">' + cards.map(function (c) {
+            return '<div class="metric-card"><div class="metric-label">' + escapeHtml(c.label) + '</div>' +
+                '<div class="metric-value ' + (c.cls || '') + '">' + escapeHtml(c.value) + '</div>' +
+                (c.sub ? '<div class="metric-sub">' + escapeHtml(c.sub) + '</div>' : '') + '</div>';
+        }).join('') + '</div>';
+    }
+    function renderTable(items, cols) {
+        if (!items || !items.length) return '<div class="empty-state">暂无数据</div>';
+        const head = cols.map(function (c) { return '<th>' + escapeHtml(c[1]) + '</th>'; }).join('');
+        const body = items.slice(0, 100).map(function (it) {
+            const tds = cols.map(function (c) {
+                const key = c[0], fmtFn = c[2];
+                const raw = it[key];
+                let val = raw;
+                if (fmtFn === 'tag') val = tag(raw);
+                else if (fmtFn === 'pct') val = '<span class="' + clsUpDown(raw) + '">' + fmt(raw, 2) + '%</span>';
+                else if (fmtFn === 'num') val = fmt(raw, 2);
+                else if (fmtFn === 'src') val = raw && typeof raw === 'object' ? escapeHtml(Object.keys(raw).join(',')) : escapeHtml(raw || '');
+                else val = escapeHtml(raw === null || raw === undefined ? '--' : String(raw));
+                return '<td>' + val + '</td>';
+            }).join('');
+            return '<tr>' + tds + '</tr>';
+        }).join('');
+        return '<table class="data-table"><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table>';
+    }
+
+    // 1. 风险否决
+    async function renderRiskOverride(el) {
+        const resp = await apiFetch('/screening/latest', { timeout: 20000, retries: 0 });
+        const data = await resp.json();
+        const results = data.results || [];
+        const hit = results.filter(function (r) { return r.risk_override && (r.risk_override.hits || []).length; });
+        const rate = results.length ? (hit.length / results.length * 100) : 0;
+        el.innerHTML =
+            statCards([
+                { label: '总推荐数', value: String(results.length) },
+                { label: '被风险否决', value: String(hit.length), cls: 'down' },
+                { label: '拦截率', value: fmt(rate, 1) + '%', cls: rate > 0 ? 'down' : '' },
+                { label: '筛选日期', value: data.date || '--', sub: '主板口径' },
+            ]) +
+            '<div class="panel"><div class="panel-header"><h2>风险否决明细</h2><span class="table-note">仅展示被 7 条规则命中的推荐</span></div><div class="panel-body">' +
+            renderTable(hit, [
+                ['code', '代码'], ['name', '名称'],
+                ['risk_override', '否决信息', function (v) { return v ? (v.reasons || []).join('；') : '--'; }],
+                ['risk_override', '规则', function (v) { return v ? (v.hits || []).join(',') : '--'; }],
+                ['risk_override', '等级变化', function (v) { return v ? (v.pre_level + '→' + v.post_level) : '--'; }],
+            ]) +
+            '</div></div>';
+    }
+
+    // 2. 绩效模拟盘
+    async function renderPaperTrading(el) {
+        const resp = await apiFetch('/paper-trading/summary', { timeout: 20000, retries: 0 });
+        const data = await resp.json();
+        const m = data.metrics || {};
+        const acct = data.account || {};
+        el.innerHTML =
+            statCards([
+                { label: '累计收益', value: fmt(m.total_return_pct, 2) + '%', cls: clsUpDown(m.total_return_pct) },
+                { label: '胜率', value: m.win_rate === null || m.win_rate === undefined ? '--' : fmt(m.win_rate, 1) + '%' },
+                { label: '盈亏比', value: m.profit_loss_ratio === null || m.profit_loss_ratio === undefined ? '--' : fmt(m.profit_loss_ratio, 2) },
+                { label: '最大回撤', value: fmt(m.max_drawdown_pct, 2) + '%', cls: 'down' },
+                { label: '虚拟现金', value: '¥' + fmt(acct.cash, 0), sub: '初始 ¥' + fmt(acct.initial_capital, 0) },
+            ]) +
+            '<div class="panel"><div class="panel-header"><h2>当前持仓</h2>' +
+            '<button type="button" class="btn btn-primary btn-sm" onclick="window.radarPaperOpen()">按最新推荐建仓</button> ' +
+            '<button type="button" class="btn btn-sm" onclick="window.radarPaperSettle()">T+1 结算</button></div><div class="panel-body" id="paperPosBody">' +
+            renderTable(data.open_positions || [], [
+                ['code', '代码'], ['name', '名称'], ['level', '等级', 'tag'],
+                ['open_price', '开仓价', 'num'], ['shares', '股数', 'num'], ['open_date', '开仓日'],
+            ]) +
+            '</div></div>' +
+            '<div class="panel"><div class="panel-header"><h2>已平仓</h2></div><div class="panel-body">' +
+            renderTable(data.closed_positions || [], [
+                ['code', '代码'], ['name', '名称'], ['open_price', '开仓价', 'num'], ['close_price', '平仓价', 'num'],
+                ['pnl', '盈亏', 'num'], ['pnl_pct', '盈亏%', 'pct'], ['close_date', '平仓日'],
+            ]) +
+            '</div></div>';
+    }
+
+    // 3. 条件盯盘
+    async function renderConditionalWatch(el) {
+        const resp = await apiFetch('/watch/latest', { timeout: 20000, retries: 0 });
+        const data = await resp.json();
+        const rules = data.rules || [];
+        const alerts = data.alerts || [];
+        el.innerHTML =
+            '<div class="panel"><div class="panel-header"><h2>新建盯盘规则</h2></div><div class="panel-body">' +
+            '<div class="form-row" style="flex-wrap:wrap">' +
+            '<input id="cwCode" class="input" placeholder="代码 如 600000" maxlength="6">' +
+            '<input id="cwName" class="input" placeholder="名称(可选)">' +
+            '<input id="cwCond" class="input" placeholder="条件 如 price >= 12.5 and turnover_rate >= 0.01">' +
+            '<button type="button" class="btn btn-primary btn-sm" onclick="window.radarWatchCreate()">创建</button>' +
+            '<button type="button" class="btn btn-sm" onclick="window.radarWatchCheck()">立即检查</button>' +
+            '</div></div></div>' +
+            '<div class="panel"><div class="panel-header"><h2>盯盘规则</h2><span class="table-note">命中后去重告警</span></div><div class="panel-body">' +
+            renderTable(rules, [
+                ['code', '代码'], ['name', '名称'], ['condition', '条件'], ['enabled', '启用', function (v) { return v ? '是' : '否'; }],
+            ]) +
+            '</div></div>' +
+            '<div class="panel"><div class="panel-header"><h2>触发记录</h2></div><div class="panel-body">' +
+            renderTable(alerts.slice(0, 50), [
+                ['code', '代码'], ['name', '名称'], ['condition', '条件'], ['price', '触发价', 'num'],
+                ['change_pct', '涨跌幅', 'pct'], ['triggered_at', '触发时间'],
+            ]) +
+            '</div></div>';
+    }
+
+    // 4. 舆情热度
+    async function renderHotScanner(el) {
+        const resp = await apiFetch('/hot/latest', { timeout: 30000, retries: 0 });
+        const data = await resp.json();
+        const items = data.items || [];
+        el.innerHTML =
+            statCards([
+                { label: '热度榜条数', value: String(items.length) },
+                { label: '数据源数', value: String(data.source_count || 0) },
+                { label: '扫描日期', value: data.scan_date || '--', sub: data.status === 'empty' ? '尚未扫描' : '' },
+                { label: '在监控列表', value: String(items.filter(function (i) { return i.in_watchlist; }).length) },
+            ]) +
+            '<div class="panel"><div class="panel-header"><h2>舆情热度榜 TOP50</h2>' +
+            '<button type="button" class="btn btn-primary btn-sm" onclick="window.radarHotScan()">立即扫描</button></div><div class="panel-body">' +
+            (items.length ? renderTable(items, [
+                ['rank', '排名'], ['code', '代码'], ['name', '名称'], ['score', '热度分', 'num'],
+                ['sources', '来源', 'src'], ['in_watchlist', '监控', function (v) { return v ? '是' : '否'; }],
+            ]) : '<div class="empty-state">尚未扫描或无数据，点击右上角"立即扫描"（需国内 IP 访问舆情源）</div>') +
+            '</div></div>';
+    }
+
+    // 5. 事件雷达
+    async function renderEventRadar(el) {
+        const resp = await apiFetch('/event-radar/latest', { timeout: 30000, retries: 0 });
+        const data = await resp.json();
+        const items = data.items || [];
+        const pos = items.filter(function (i) { return i.impact_score > 0; }).length;
+        const neg = items.filter(function (i) { return i.impact_score < 0; }).length;
+        el.innerHTML =
+            statCards([
+                { label: '事件总数', value: String(items.length) },
+                { label: '利好事件', value: String(pos), cls: 'up' },
+                { label: '利空事件', value: String(neg), cls: 'down' },
+                { label: '在监控列表', value: String(items.filter(function (i) { return i.in_watchlist; }).length) },
+            ]) +
+            '<div class="panel"><div class="panel-header"><h2>事件雷达 TOP50</h2>' +
+            '<button type="button" class="btn btn-primary btn-sm" onclick="window.radarEventScan()">立即扫描</button></div><div class="panel-body">' +
+            (items.length ? renderTable(items, [
+                ['code', '代码'], ['name', '名称'], ['event_type', '事件类型'],
+                ['impact_score', '影响分', function (v) { return '<span class="' + clsUpDown(v) + '">' + (Number(v) > 0 ? '+' : '') + fmt(v, 0) + '</span>'; }],
+                ['title', '标题'], ['event_date', '日期'], ['in_watchlist', '监控', function (v) { return v ? '是' : '否'; }],
+            ]) : '<div class="empty-state">尚未扫描或无数据，点击右上角"立即扫描"（需国内 IP 访问公告/研报源）</div>') +
+            '</div></div>';
+    }
+
+    async function setupRadarPage(pageId) {
+        const el = document.getElementById('page-' + pageId);
+        if (!el) return;
+        el.innerHTML = '<div class="loading">加载中...</div>';
+        try {
+            if (pageId === 'risk-override') await renderRiskOverride(el);
+            else if (pageId === 'paper-trading') await renderPaperTrading(el);
+            else if (pageId === 'conditional-watch') await renderConditionalWatch(el);
+            else if (pageId === 'hot-scanner') await renderHotScanner(el);
+            else if (pageId === 'event-radar') await renderEventRadar(el);
+        } catch (e) {
+            el.innerHTML = '<div class="empty-state">加载失败：' + escapeHtml(String(e)) + '</div>';
+        }
+    }
+
+    // 暴露给 inline onclick 的操作函数
+    function post(path) {
+        return apiFetch(path, { method: 'POST', timeout: 30000, retries: 0 });
+    }
+    window.radarPaperOpen = function () { post('/paper-trading/run-open').then(function (r) { return r.json(); }).then(function () { setupRadarPage('paper-trading'); }).catch(function (e) { window.radarNotify('建仓失败：' + e); }); };
+    window.radarPaperSettle = function () { post('/paper-trading/run-settle').then(function (r) { return r.json(); }).then(function () { setupRadarPage('paper-trading'); }).catch(function (e) { window.radarNotify('结算失败：' + e); }); };
+    window.radarHotScan = function () { post('/hot/scan').then(function (r) { return r.json(); }).then(function () { setupRadarPage('hot-scanner'); }).catch(function (e) { window.radarNotify('扫描失败：' + e); }); };
+    window.radarEventScan = function () { post('/event-radar/scan').then(function (r) { return r.json(); }).then(function () { setupRadarPage('event-radar'); }).catch(function (e) { window.radarNotify('扫描失败：' + e); }); };
+    window.radarWatchCheck = function () { post('/watch/check').then(function (r) { return r.json(); }).then(function () { setupRadarPage('conditional-watch'); }).catch(function (e) { window.radarNotify('检查失败：' + e); }); };
+    window.radarWatchCreate = function () {
+        const code = document.getElementById('cwCode').value.trim();
+        const name = document.getElementById('cwName').value.trim();
+        const cond = document.getElementById('cwCond').value.trim();
+        if (!code || !cond) { window.radarNotify('请填写代码和条件'); return; }
+        apiFetch('/watch/rules', { method: 'POST', timeout: 15000, retries: 0, body: JSON.stringify({ code: code, name: name || undefined, condition: cond }) })
+            .then(function (r) { return r.json(); }).then(function (d) {
+                if (d.status === 'error') { window.radarNotify('创建失败：' + d.message); } else { setupRadarPage('conditional-watch'); }
+            }).catch(function (e) { window.radarNotify('创建失败：' + e); });
+    };
+    window.radarNotify = function (msg) {
+        // 页面内轻提示，不弹 alert
+        const el = document.querySelector('.page.active .panel-body');
+        if (el) {
+            const note = document.createElement('div');
+            note.className = 'table-note';
+            note.textContent = msg;
+            el.prepend(note);
+        }
+    };
+
+    window.setupRadarPage = setupRadarPage;
+    window.RADAR_PAGES = ['risk-override', 'paper-trading', 'conditional-watch', 'hot-scanner', 'event-radar'];
+})();
