# 24 数据源去东财化 · 完整 diff 核验（commit f6484be）

> 基准：e8ce6d5（§11 首阶段之后的 HEAD）→ f6484be（本次分阶段实施）。
> 26 文件，2608 insertions / 469 deletions；运行时 data/smart_picker_*_notified_*.json 未纳入。
> 核验方法：git diff e8ce6d5..f6484be 全量输出，下方以 ~~~~ 包裹逐行可查。

## 变更统计

```
 26 files changed, 2608 insertions(+), 469 deletions(-)
```

## 完整 diff

~~~~
diff --git a/backend/agents/layer1_data_collector/sources/eastmoney_quote.py b/backend/agents/layer1_data_collector/sources/eastmoney_quote.py
index e7d3b34..e4b6d41 100644
--- a/backend/agents/layer1_data_collector/sources/eastmoney_quote.py
+++ b/backend/agents/layer1_data_collector/sources/eastmoney_quote.py
@@ -1,6 +1,5 @@
 """
-实时行情数据源 — 批量查询股票行情
-requests 优先，curl_cffi 备选（GitHub Actions 环境 curl_cffi 易超时）
+实时行情兼容入口 — 腾讯批量优先，旧源按缺失代码补齐。
 """
 import logging
 import os
@@ -8,18 +7,62 @@ import time
 from typing import List
 from urllib.parse import quote
 import pandas as pd
+from .quote_contract import number
 
 logger = logging.getLogger(__name__)
 
-BATCH_SIZE = 80  # 每批最多80只，避免URL过长
+BATCH_SIZE = 60  # 初始保守批量，腾讯与兼容源共用
 MIN_SPLIT_BATCH_SIZE = 10  # 大批失败后拆小批重试，降低 GitHub Actions 限流/空响应影响
 BATCH_DELAY_SECONDS = 0.25
 ALLOW_STALE_QUOTE_CACHE = os.getenv("ALLOW_STALE_QUOTE_CACHE", "").lower() in {"1", "true", "yes"}
 _QUOTE_CACHE = {}
 
 
+class QuoteSourcesUnavailable(RuntimeError):
+    """整批供应商不可用，不通过递归拆分重复轰炸相同端点。"""
+
+
+def _fetch_tencent_batch(secids_batch: List[str]) -> list:
+    """最多60只一批，仅接受当日新鲜且必需字段完整的腾讯报价。"""
+    import requests
+    from .quote_contract import parse_tencent_quotes, quote_is_current
+
+    symbols = [("sh" if secid.startswith("1.") else "sz") + secid.split(".", 1)[1]
+               for secid in secids_batch]
+    rows = []
+    for start in range(0, len(symbols), 60):
+        batch = symbols[start:start + 60]
+        response = requests.get("https://qt.gtimg.cn/q=" + ",".join(batch),
+                                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
+                                timeout=(3, 5))
+        response.raise_for_status()
+        response.encoding = "gbk"
+        rows.extend(row for row in parse_tencent_quotes(response.text, batch)
+                    if not row["degraded"] and quote_is_current(row))
+    return rows
+
+
 def _fetch_one_batch(secids_batch: List[str]) -> list:
-    """单批次请求，requests 优先，curl_cffi 备选。返回 rows 列表"""
+    """独立报价源优先；只对缺失代码走兼容后备，不覆盖已成功报价。"""
+    try:
+        rows = _fetch_tencent_batch(secids_batch)
+    except Exception as exc:
+        logger.debug("腾讯批量报价失败: %s", exc)
+        rows = []
+    found = {row["代码"] for row in rows}
+    missing = [secid for secid in secids_batch if secid.split(".", 1)[1] not in found]
+    if missing:
+        try:
+            rows.extend(_fetch_legacy_batch(missing))
+        except Exception:
+            if not rows:
+                raise
+            logger.warning("报价部分缺失，保留腾讯已成功的%d只", len(rows))
+    return rows
+
+
+def _fetch_legacy_batch(secids_batch: List[str]) -> list:
+    """兼容后备：东财一次，再新浪一次，不叠加同源客户端重试。"""
     url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
     params = {
         "fields": "f2,f3,f5,f6,f8,f9,f10,f12,f14,f20,f21",
@@ -36,47 +79,26 @@ def _fetch_one_batch(secids_batch: List[str]) -> list:
         "Referer": "https://quote.eastmoney.com/",
     }
 
-    last_error = None
-    for attempt in range(2):
-        # ---- 方案1: 标准 requests（GitHub Actions 环境更稳定） ----
-        try:
-            import requests
-            resp = requests.get(url, params=params, headers=headers, timeout=15)
-            resp.raise_for_status()
-            rows = _parse_response(resp.json())
-            if rows:
-                return rows
-            last_error = RuntimeError("东方财富返回空行情")
-        except Exception as e:
-            last_error = e
-            logger.debug(f"  requests 请求失败: {e}")
-
-        # ---- 方案2: curl_cffi 模拟 Chrome ----
-        try:
-            from curl_cffi import requests as curl_req
-            resp = curl_req.get(url, params=params, headers=headers,
-                               impersonate="chrome120", timeout=15)
-            rows = _parse_response(resp.json())
-            if rows:
-                return rows
-            last_error = RuntimeError("东方财富 curl_cffi 返回空行情")
-        except Exception as e:
-            last_error = e
-            logger.debug(f"  curl_cffi 请求也失败: {e}")
-
-        try:
-            rows = _fetch_sina_batch(secids_batch)
-            if rows:
-                logger.warning(f"  东方财富行情不可用，新浪财经补齐 {len(rows)}/{len(secids_batch)} 只")
-                return rows
-        except Exception as e:
-            last_error = e
-            logger.debug(f"  新浪财经请求也失败: {e}")
-
-        if attempt == 0:
-            time.sleep(0.35)
-
-    raise RuntimeError(f"所有请求方式均失败: {last_error}")
+    errors = []
+    try:
+        import requests
+        resp = requests.get(url, params=params, headers=headers, timeout=(3, 5))
+        resp.raise_for_status()
+        wanted = {secid.split(".", 1)[1] for secid in secids_batch}
+        rows = [row for row in _parse_response(resp.json()) if row["代码"] in wanted]
+        if rows:
+            return rows
+        errors.append("东财返回空行情")
+    except Exception as exc:
+        errors.append(f"东财: {exc}")
+    try:
+        rows = _fetch_sina_batch(secids_batch)
+        if rows:
+            return rows
+        errors.append("新浪返回空行情")
+    except Exception as exc:
+        errors.append(f"新浪: {exc}")
+    raise QuoteSourcesUnavailable("; ".join(errors))
 
 
 def _fetch_sina_batch(secids_batch: List[str]) -> list:
@@ -93,7 +115,7 @@ def _fetch_sina_batch(secids_batch: List[str]) -> list:
 
     url = "https://hq.sinajs.cn/list=" + quote(",".join(symbols), safe=",")
     headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
-    resp = requests.get(url, headers=headers, timeout=15)
+    resp = requests.get(url, headers=headers, timeout=(3, 5))
     resp.raise_for_status()
     resp.encoding = "gbk"
 
@@ -102,6 +124,8 @@ def _fetch_sina_batch(secids_batch: List[str]) -> list:
         if not chunk.strip() or '="' not in chunk:
             continue
         symbol = chunk.split("hq_str_", 1)[-1].split("=", 1)[0]
+        if symbol not in symbols:
+            continue
         code = symbol[-6:]
         raw = chunk.split('="', 1)[1].rstrip('"')
         parts = raw.split(",")
@@ -110,7 +134,7 @@ def _fetch_sina_batch(secids_batch: List[str]) -> list:
 
         def _float_at(index):
             try:
-                value = float(parts[index])
+                value = number(parts[index])
             except (IndexError, TypeError, ValueError):
                 return None
             return value
@@ -127,13 +151,16 @@ def _fetch_sina_batch(secids_batch: List[str]) -> list:
             "名称": parts[0],
             "最新价": latest,
             "涨跌幅": change_pct,
-            "成交量": _float_at(8),
+            "成交量": None if _float_at(8) is None else _float_at(8) / 100,
             "成交额": amount,
             "换手率": None,
             "市盈率": None,
             "量比": None,
             "总市值": None,
             "流通市值": None,
+            "source": "sina", "degraded": True,
+            "source_time": (parts[30] + "T" + parts[31] + "+08:00") if len(parts) > 31 else None,
+            "missing_fields": ["换手率", "市盈率", "量比", "总市值", "流通市值"],
         })
     return rows
 
@@ -158,18 +185,22 @@ def _parse_response(data: dict) -> list:
             v = item.get(key)
             if v is None or v == "-":
                 return None
-            try:
-                return float(v)
-            except (ValueError, TypeError):
-                return None
+            return number(v)
 
-        rows.append({
-            "代码": code, "名称": name,
+        fields = {
             "最新价": _float("f2"), "涨跌幅": _float("f3"),
             "成交量": _float("f5"), "成交额": _float("f6"),
             "换手率": _float("f8"), "市盈率": _float("f9"),
             "量比": _float("f10"), "总市值": _float("f20"),
             "流通市值": _float("f21"),
+        }
+        missing_fields = [key for key in ("涨跌幅", "成交量", "成交额") if fields.get(key) is None]
+        rows.append({
+            "代码": code, "名称": name, **fields,
+            "source": "eastmoney", "source_time": None,
+            # 东财此端点无逐行行情时间：未知时间不得冒充新鲜，必须显式降级
+            "degraded": True,
+            "missing_fields": missing_fields,
         })
     return rows
 
@@ -182,6 +213,9 @@ def _fetch_batch_with_split(secids_batch: List[str], batch_no: str = "") -> tupl
     """
     try:
         return _fetch_one_batch(secids_batch), 0
+    except QuoteSourcesUnavailable as exc:
+        logger.warning("行情源均不可用，跳过拆分重试: %s", exc)
+        return [], 1
     except Exception as e:
         if len(secids_batch) <= MIN_SPLIT_BATCH_SIZE:
             label = f" {batch_no}" if batch_no else ""
@@ -206,6 +240,15 @@ def _fetch_batch_with_split(secids_batch: List[str], batch_no: str = "") -> tupl
         return rows, failed
 
 
+def fetch_tencent_quotes_for_codes(codes: list) -> pd.DataFrame:
+    """候选精查：对给定代码走腾讯批量优先 + 缺失后备，返回带质量字段的报价行。"""
+    secids = [("1." if str(c).startswith(("6", "9")) else "0.") + str(c).zfill(6) for c in codes]
+    if not secids:
+        return pd.DataFrame()
+    rows = _fetch_batch_with_split(secids)[0]
+    return pd.DataFrame(rows) if rows else pd.DataFrame()
+
+
 def fetch_single_quote_verified(code: str) -> dict:
     """对单只股票做交叉验证：东方财富 + 新浪备用源对比价格"""
     result = {"code": code, "price_eastmoney": None, "price_sina": None,
@@ -213,8 +256,9 @@ def fetch_single_quote_verified(code: str) -> dict:
 
     # 东方财富源
     try:
-        df = fetch_stock_quotes([code])
-        if not df.empty:
+        market = "1" if code.startswith(("6", "9")) else "0"
+        df = pd.DataFrame(_fetch_legacy_batch([f"{market}.{code}"]))
+        if not df.empty and df.iloc[0].get("source") == "eastmoney":
             result["price_eastmoney"] = float(df.iloc[0]["最新价"])
     except Exception:
         pass
@@ -268,7 +312,7 @@ def fetch_stock_quotes(codes: List[str]) -> pd.DataFrame:
 
     if ALLOW_STALE_QUOTE_CACHE:
         found_codes = {row["代码"] for row in all_rows}
-        cached_rows = [_QUOTE_CACHE[code] for code in codes
+        cached_rows = [dict(_QUOTE_CACHE[code], source="cache", degraded=True, is_stale=True) for code in codes
                        if code not in found_codes and code in _QUOTE_CACHE]
         if cached_rows:
             all_rows.extend(cached_rows)
@@ -276,4 +320,12 @@ def fetch_stock_quotes(codes: List[str]) -> pd.DataFrame:
 
     logger.info(f"  行情获取完成: {len(all_rows)}/{len(codes)} 只"
                 + (f" (失败 {failed_batches} 批)" if failed_batches else ""))
-    return pd.DataFrame(all_rows)
+    df = pd.DataFrame(all_rows)
+    found = {row["代码"] for row in all_rows}
+    df.attrs["source_meta"] = {
+        "sources": sorted({row.get("source", "unknown") for row in all_rows}),
+        "requested_count": len(set(codes)), "received_count": len(found),
+        "missing_codes": sorted(set(codes) - found),
+        "degraded": bool(set(codes) - found) or any(row.get("degraded", False) for row in all_rows),
+    }
+    return df
diff --git a/backend/agents/layer1_data_collector/sources/historical_kline.py b/backend/agents/layer1_data_collector/sources/historical_kline.py
index 254dad2..9718a08 100644
--- a/backend/agents/layer1_data_collector/sources/historical_kline.py
+++ b/backend/agents/layer1_data_collector/sources/historical_kline.py
@@ -1,5 +1,5 @@
 """
-历史K线数据源 — akshare + 新浪备用
+历史K线数据源 — 明确复权能力，未知口径不进入前复权消费接口。
 """
 import logging
 from datetime import datetime, timedelta, timezone
@@ -26,23 +26,10 @@ def fetch_historical_with_source(symbol: str, days: int = 60) -> Optional[tuple[
     if df is not None and not df.empty:
         return df, "东方财富历史K线API(push2his)"
 
-    try:
-        import akshare as ak
-        end = datetime.now(BEIJING_TZ)
-        start = end - timedelta(days=days + 30)
-        df = ak.stock_zh_a_hist(
-            symbol=symbol, period="daily",
-            start_date=start.strftime("%Y%m%d"),
-            end_date=end.strftime("%Y%m%d"), adjust="qfq",
-        )
-        if df is not None and not df.empty:
-            return df, "akshare.stock_zh_a_hist(东方财富历史行情)"
-    except Exception as e:
-        logger.debug(f"akshare历史数据 {symbol} 失败: {e}")
-
-    df = _fetch_hist_sina(symbol, days)
+    # 东财直连失败后不再通过AKShare重打同一上游；新浪轻量端点复权未知。
+    df = _fetch_hist_tencent(symbol, days)
     if df is not None and not df.empty:
-        return df, "新浪财经K线API"
+        return df, "腾讯前复权K线API"
     return None
 
 
@@ -69,7 +56,9 @@ def _parse_eastmoney_kline_rows(rows: list[str]) -> pd.DataFrame:
             })
         except (TypeError, ValueError):
             continue
-    return pd.DataFrame(parsed)
+    df = pd.DataFrame(parsed)
+    df.attrs.update({"adjustment": "qfq", "volume_unit": "lot", "source": "eastmoney"})
+    return df
 
 
 def _fetch_hist_eastmoney_direct(symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
@@ -78,7 +67,7 @@ def _fetch_hist_eastmoney_direct(symbol: str, days: int = 60) -> Optional[pd.Dat
 
     market = "1" if symbol.startswith(("6", "9")) else "0"
     end = datetime.now(BEIJING_TZ)
-    start = end - timedelta(days=days + 30)
+    start = end - timedelta(days=days * 2 + 30)
     url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
     params = {
         "secid": f"{market}.{symbol}",
@@ -106,7 +95,7 @@ def _fetch_hist_eastmoney_direct(symbol: str, days: int = 60) -> Optional[pd.Dat
 
 
 def _fetch_hist_sina(symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
-    """新浪K线API备用"""
+    """新浪轻量日K，仅供核验；未证明复权，不作为qfq兜底。"""
     import requests as req
 
     prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
@@ -118,6 +107,7 @@ def _fetch_hist_sina(symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
 
     try:
         resp = req.get(url, params=params, headers=headers, timeout=10)
+        resp.raise_for_status()
         data = resp.json()
         if not data or not isinstance(data, list) or len(data) < 3:
             return None
@@ -126,7 +116,7 @@ def _fetch_hist_sina(symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
         prev_close = None
         for item in data:
             close_val = float(item.get("close", 0))
-            pct = 0.0
+            pct = None
             if prev_close is not None and prev_close > 0:
                 pct = round((close_val - prev_close) / prev_close * 100, 2)
             row = {
@@ -135,16 +125,62 @@ def _fetch_hist_sina(symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
                 "收盘": close_val,
                 "最高": float(item.get("high", 0)),
                 "最低": float(item.get("low", 0)),
-                "成交量": float(item.get("volume", 0)),
-                "成交额": 0.0, "振幅": 0.0,
-                "涨跌幅": pct, "换手率": 0.0,
+                "成交量": float(item.get("volume", 0)) / 100,
+                "成交额": None, "振幅": None,
+                "涨跌幅": pct, "换手率": None,
             }
             rows.append(row)
             prev_close = close_val
 
         df = pd.DataFrame(rows)
+        df.attrs.update({"adjustment": "unknown", "volume_unit": "lot", "source": "sina",
+                         "missing_fields": ["成交额", "换手率", "振幅"], "degraded": True})
         logger.debug(f"  新浪K线 {symbol} 获取成功, {len(df)}行")
         return df
     except Exception as e:
         logger.debug(f"新浪K线 {symbol} 失败: {e}")
         return None
+
+
+def _fetch_hist_tencent(symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
+    """仅接收qfqday；不以day替代，缺额/换手保持空值。"""
+    import requests
+    from .quote_contract import number
+
+    if not str(symbol).isdigit() or len(str(symbol)) != 6 or not 1 <= days <= 600:
+        return None
+    code = ("sh" if symbol.startswith(("6", "9")) else "sz") + symbol
+    try:
+        response = requests.get(
+            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
+            params={"param": f"{code},day,,,{days},qfq"},
+            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}, timeout=(3, 8),
+        )
+        response.raise_for_status()
+        bars = response.json().get("data", {}).get(code, {}).get("qfqday")
+        if not isinstance(bars, list) or not bars:
+            return None
+        rows = []
+        for bar in bars:
+            if not isinstance(bar, list) or len(bar) < 6:
+                raise ValueError("腾讯K线结构变化")
+            values = [number(value) for value in bar[1:6]]
+            if any(value is None for value in values) or values[4] < 0:
+                raise ValueError("腾讯K线数值缺失")
+            opening, close, high, low, volume = values
+            if min(opening, close, low) <= 0 or high < max(opening, close) or low > min(opening, close):
+                raise ValueError("腾讯K线OHLC不一致")
+            day = datetime.strptime(bar[0], "%Y-%m-%d").date().isoformat()
+            if rows and day <= rows[-1]["日期"]:
+                raise ValueError("腾讯K线日期重复或乱序")
+            rows.append({"日期": day, "开盘": opening, "收盘": close, "最高": high, "最低": low,
+                         "成交量": volume, "成交额": None, "换手率": None,
+                         "振幅": None, "涨跌幅": None})
+        df = pd.DataFrame(rows).tail(days)
+        df["涨跌幅"] = df["收盘"].pct_change(fill_method=None) * 100
+        df.attrs.update({"source": "tencent", "adjustment": "qfq", "volume_unit": "lot",
+                         "missing_fields": ["成交额", "换手率", "振幅"], "degraded": True})
+        return df
+    except Exception as exc:
+        logger.debug("腾讯前复权K线 %s 失败: %s", symbol, exc)
+        return None
diff --git a/backend/agents/layer1_data_collector/sources/quote_contract.py b/backend/agents/layer1_data_collector/sources/quote_contract.py
new file mode 100644
index 0000000..656a453
--- /dev/null
+++ b/backend/agents/layer1_data_collector/sources/quote_contract.py
@@ -0,0 +1,129 @@
+"""报价端点契约与基础涨停状态；不推断封板事件或资金分类。"""
+from __future__ import annotations
+
+import math
+import re
+from datetime import datetime, time, timedelta, timezone
+from decimal import Decimal, InvalidOperation
+
+BEIJING_TZ = timezone(timedelta(hours=8))
+
+
+def number(value):
+    """拒绝空值、布尔值和非有限数，保留合法零。"""
+    if value is None or isinstance(value, bool) or str(value).strip() in {"", "-", "--"}:
+        return None
+    try:
+        result = float(value)
+        return result if math.isfinite(result) else None
+    except (TypeError, ValueError):
+        return None
+
+
+def scaled(value, factor):
+    result = number(value)
+    return None if result is None else result * factor
+
+
+def normalize_sina_bulk(rows):
+    """仅标准化报价观察值；bulk时间及分类不足，不作为正式资金源。"""
+    if not isinstance(rows, list) or not rows:
+        raise ValueError("新浪bulk不是非空列表")
+    result = []
+    seen = set()
+    for row in rows:
+        if not isinstance(row, dict):
+            raise ValueError("新浪bulk包含非对象记录")
+        symbol = str(row.get("symbol", ""))
+        if not re.fullmatch(r"(?:sh60|sh688|sz00[0-3]|sz30[01])\d+", symbol) or len(symbol) != 8:
+            continue
+        if symbol in seen:
+            raise ValueError(f"新浪bulk重复证券: {symbol}")
+        seen.add(symbol)
+        result.append({
+            "symbol": symbol, "code": symbol[2:], "name": row.get("name"),
+            "price": number(row.get("trade")),
+            "change_pct": scaled(row.get("changeratio"), 100),
+            # 此端点原值为基点；与新浪其他端点的比例值不可混用。
+            "turnover_pct": scaled(row.get("turnover"), 0.01),
+            "amount": number(row.get("amount")),
+            "source_time": None, "source": "sina_bulk",
+            "degraded_reasons": ["source_time_unknown", "fund_flow_classification_unverified"],
+        })
+    if not result:
+        raise ValueError("新浪bulk没有可识别的沪深A股")
+    return result
+
+
+def parse_tencent_quotes(text, symbols):
+    """腾讯报价转既有中文列契约：成交量保持手，金额/市值转元。"""
+    wanted = set(symbols)
+    result = {}
+    for symbol, raw in re.findall(r'v_((?:sh|sz)\d{6})="([^"\r\n]*)"', text):
+        if symbol not in wanted:
+            continue
+        if symbol in result:
+            raise ValueError(f"腾讯报价重复证券: {symbol}")
+        fields = raw.split("~")
+        if len(fields) <= 48 or fields[2] != symbol[2:] or not fields[1]:
+            continue
+        at = lambda i: fields[i] if i < len(fields) else None
+        try:
+            source_time = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=BEIJING_TZ)
+        except ValueError:
+            continue
+        price, prev = number(at(3)), number(at(4))
+        if price is None or price <= 0 or prev is None or prev <= 0:
+            continue
+        row = {
+            "代码": symbol[2:], "名称": fields[1], "最新价": price, "昨收": prev,
+            "涨跌幅": number(at(32)), "成交量": number(at(6)),
+            "成交额": scaled(at(37), 10000), "换手率": number(at(38)),
+            "市盈率": number(at(39)), "量比": number(at(49)),
+            "总市值": scaled(at(45), 100000000), "流通市值": scaled(at(44), 100000000),
+            "最高价": number(at(33)), "最低价": number(at(34)),
+            "涨停价": number(at(47)), "跌停价": number(at(48)),
+            "source": "tencent", "source_time": source_time.isoformat(),
+        }
+        required = ("涨跌幅", "成交量", "成交额", "换手率", "总市值", "流通市值")
+        for key in required[1:]:
+            if row[key] is not None and row[key] < 0:
+                row[key] = None
+        row["missing_fields"] = [key for key in required if row[key] is None]
+        row["degraded"] = bool(row["missing_fields"])
+        result[symbol] = row
+    return [result[symbol] for symbol in dict.fromkeys(symbols) if symbol in result]
+
+
+def quote_is_current(row, now=None, max_age_seconds=45):
+    """盘中校验真实行情时间；午休/盘后允许对应收盘时点快照。"""
+    now = (now or datetime.now(BEIJING_TZ)).astimezone(BEIJING_TZ)
+    try:
+        stamp = datetime.fromisoformat(row["source_time"])
+        if stamp.tzinfo is None:
+            return False
+        stamp = stamp.astimezone(BEIJING_TZ)
+    except (KeyError, TypeError, ValueError):
+        return False
+    if stamp.date() != now.date() or (stamp - now).total_seconds() > 5:
+        return False
+    cutoff = now
+    if time(11, 30) <= now.time() < time(13):
+        cutoff = now.replace(hour=11, minute=30, second=0, microsecond=0)
+    elif now.time() >= time(15):
+        cutoff = now.replace(hour=15, minute=0, second=0, microsecond=0)
+    return (cutoff - stamp).total_seconds() <= max_age_seconds
+
+
+def at_limit_price(price, limit_price):
+    """仅判断最新价是否在有效涨停价；未知返回None，不推断ST或新股规则。"""
+    try:
+        current, upper = Decimal(str(price)), Decimal(str(limit_price))
+        if not current.is_finite() or not upper.is_finite() or current <= 0 or upper <= 0:
+            return None
+        # 股票报价须落在分位；不通过四舍五入把接近涨停伪装成涨停。
+        if current != current.quantize(Decimal("0.01")) or upper != upper.quantize(Decimal("0.01")):
+            return None
+        return current == upper
+    except (InvalidOperation, ValueError):
+        return None
diff --git a/backend/agents/layer1_data_collector/sources/zt_contract.py b/backend/agents/layer1_data_collector/sources/zt_contract.py
new file mode 100644
index 0000000..2cb5361
--- /dev/null
+++ b/backend/agents/layer1_data_collector/sources/zt_contract.py
@@ -0,0 +1,140 @@
+"""涨停基础状态与增强事件分离（§12.4 第4步）。
+
+基础状态（zt_basic）：只用新鲜报价 + 有效涨停价，可免费获得。
+增强事件（zt_enrichment）：封板/首封/炸板/连板，依赖东财 zt_pool 等完整事件源；
+缺失时显式 unavailable_required_fields，不填 0、不当正常结果。
+
+规则边界：本模块只编码有明确依据的规则版本；未知（新股/无涨跌幅限制/规则缺生效日期）返回 None。
+"""
+from __future__ import annotations
+
+from datetime import date
+from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
+
+# 规则版本：生效日期后 主板风险警示(ST) 限制比例由 5% 调整为 10%（上交所 2026 修订、深交所 2026-06-30 通知）。
+# 未命中任何版本或板块不明 → unknown。
+_RULE_VERSIONS = [
+    {"version": "v2", "effective": date(2026, 6, 30),
+     "main": 0.10, "main_st": 0.10, "gem_star": 0.20},
+    {"version": "v1", "effective": date(2000, 1, 1),
+     "main": 0.10, "main_st": 0.05, "gem_star": 0.20},
+]
+
+_PRICE_STEP = Decimal("0.01")
+
+
+def _board(code: str):
+    code = str(code).zfill(6)
+    if code.startswith(("300", "301", "688")):
+        return "gem_star"
+    if code.startswith(("60", "000", "001", "002", "003")):
+        return "main"
+    return None
+
+
+def _quantize_price(value: Decimal) -> Decimal:
+    return value.quantize(_PRICE_STEP, rounding=ROUND_HALF_UP)
+
+
+def compute_limit_prices(prev_close, code, name="", on_date=None):
+    """按有效规则/参考价/报价单位计算涨停价与跌停价；未知返回 (None, None, None)。"""
+    try:
+        prev = Decimal(str(prev_close))
+    except (InvalidOperation, ValueError, TypeError):
+        return None, None, None
+    if not prev.is_finite() or prev <= 0:
+        return None, None, None
+    board = _board(code)
+    if board is None:
+        return None, None, None
+    st = "ST" in str(name or "").upper()
+    day = on_date or date.today()
+    rule = next((r for r in _RULE_VERSIONS if r["effective"] <= day), None)
+    if rule is None:
+        return None, None, None
+    ratio = rule["main_st"] if (board == "main" and st) else (rule["gem_star"] if board == "gem_star" else rule["main"])
+    up = _quantize_price(prev * (1 + Decimal(str(ratio))))
+    down = _quantize_price(prev * (1 - Decimal(str(ratio))))
+    return float(up), float(down), rule["version"]
+
+
+def classify_zt_basic(row) -> dict:
+    """基础状态：曾触及 / 当前在涨停 / 未知；不推断封板事件。"""
+    code = str(row.get("代码") or row.get("code") or "").zfill(6)
+    name = str(row.get("名称") or row.get("name") or "")
+    price = row.get("最新价")
+    high = row.get("最高价")
+    limit_up = row.get("涨停价")
+    unknown = []
+    if not code:
+        return {"code": code, "state": "unknown", "touched": False, "at_limit": False, "unknown_reasons": ["missing_code"]}
+    if limit_up is None:
+        limit_up, _, _ = compute_limit_prices(row.get("昨收"), code, name)
+        if limit_up is None:
+            unknown.append("limit_price_unknown")
+    if limit_up is None:
+        return {"code": code, "state": "unknown", "touched": False, "at_limit": False, "unknown_reasons": unknown}
+    try:
+        p = Decimal(str(price)) if price not in (None, "") else None
+        h = Decimal(str(high)) if high not in (None, "") else None
+        up = Decimal(str(limit_up))
+    except (InvalidOperation, ValueError, TypeError):
+        return {"code": code, "state": "unknown", "touched": False, "at_limit": False, "unknown_reasons": ["invalid_price"]}
+    at_limit = p is not None and p == up
+    touched = (h is not None and h >= up) or at_limit
+    state = "at_limit" if at_limit else ("touched" if touched else "none")
+    return {"code": code, "state": state, "touched": touched, "at_limit": at_limit,
+            "limit_up": float(up), "unknown_reasons": unknown}
+
+
+def resolve_zt_basic_from_quotes(quotes) -> "pd.DataFrame":
+    """免费基础路径：从新鲜报价批量计算涨停基础状态，供消费方在不依赖东财时使用。"""
+    import pandas as pd
+
+    if quotes is None or getattr(quotes, "empty", False):
+        return pd.DataFrame()
+    rows = []
+    for _, r in quotes.iterrows():
+        state = classify_zt_basic(r.to_dict())
+        rows.append({
+            "代码": state["code"],
+            "zt_basic_state": state["state"],
+            "touched": state["touched"],
+            "at_limit": state["at_limit"],
+            "limit_up": state.get("limit_up"),
+            "unknown_reasons": state["unknown_reasons"],
+        })
+    return pd.DataFrame(rows)
+
+
+def enrich_zt_events(zt_pool, codes) -> dict:
+    """增强事件：从东财涨停池映射封板/炸板/连板；无等价事件源时该能力 unavailable。"""
+    out = {str(c).zfill(6): None for c in (codes or [])}
+    if zt_pool is None or getattr(zt_pool, "empty", False):
+        return out
+    for _, item in zt_pool.iterrows():
+        code = str(item.get("代码", "")).zfill(6)
+        if code not in out:
+            continue
+        out[code] = {
+            "seal_time": item.get("封板时间"),
+            "first_seal_time": item.get("首次封板时间") if "首次封板时间" in item else None,
+            "break_count": item.get("炸板次数"),
+            "consecutive": item.get("连板数"),
+            "available": True,
+        }
+    return out
+
+
+def required_events_available(events: dict, required=("seal_time", "break_count")) -> list:
+    """依赖精确封板字段的策略：缺数据返回 unavailable 列表，不填 0。"""
+    missing = []
+    for code, ev in (events or {}).items():
+        if not ev or not ev.get("available"):
+            missing.append(code)
+            continue
+        for field in required:
+            if ev.get(field) in (None, "", "-"):
+                missing.append(code)
+                break
+    return sorted(set(missing))
diff --git a/backend/plugins/common.py b/backend/plugins/common.py
index 74abcd2..5fc1ab3 100644
--- a/backend/plugins/common.py
+++ b/backend/plugins/common.py
@@ -13,6 +13,8 @@ import logging
 import math
 import os
 import queue as _queue
+from threading import RLock
+from time import monotonic
 from datetime import datetime, timedelta, timezone
 from pathlib import Path
 from typing import Optional
@@ -104,31 +106,119 @@ def publish_snapshot_update(name: str) -> None:
 
 
 # ==== K 线共享缓存（18/19/20/21 共用，第七波 G5）====
-# 当日 K 线内存缓存：同日内复用，避免四插件重复拉东财（东财请求量减半）
+# 当前接口仍可能包含未完成日K，因此成功结果也只缓存短时间。
 _kline_cache: dict = {}
 _kline_cache_date: Optional[str] = None
+_kline_cache_generation = 0
+_kline_guard = RLock()
+_kline_locks = [RLock() for _ in range(64)]
+KLINE_CACHE_TTL_SECONDS = 45.0
+KLINE_NEGATIVE_CACHE_TTL_SECONDS = 5.0
+
+
+def _kline_slice(data, days):
+    """返回副本，防止一个消费者修改其他消费者的缓存；附实际覆盖元数据（§12.4 第2步）。"""
+    if data is None:
+        return None
+    if hasattr(data, "iloc"):
+        result = data.iloc[-days:].copy(deep=True)
+        result.attrs["kline_coverage"] = _kline_coverage(result, days)
+        return result
+    from copy import deepcopy
+    return deepcopy(data[-days:])
+
+
+def _persist_kline_best_effort(code: str, data) -> None:
+    """旁路持久化（§12.4 第3步）：best-effort 写 bars_store，失败静默不阻断。
+
+    默认关闭（KLINE_STORE_WRITE_ENABLED），由部署环境达标后开启；读复用待字段契约扩展。
+    """
+    import os
+
+    if os.environ.get("KLINE_STORE_WRITE_ENABLED", "").lower() not in {"1", "true", "yes"}:
+        return
+    if data is None or getattr(data, "empty", False):
+        return
+    try:
+        from backend.services.data_backend.bars_store import upsert_daily_bars
+
+        attrs = getattr(data, "attrs", {}) or {}
+        upsert_daily_bars(
+            str(code).zfill(6), data,
+            adjustment=attrs.get("adjustment") or "raw",
+            adjustment_version=attrs.get("adjustment_version"),
+            source=attrs.get("source") or "kline",
+            is_final=bool(attrs.get("is_final", True)),
+        )
+    except Exception:  # noqa: BLE001 旁路失败不阻断主链路
+        pass
+
+
+def _kline_coverage(df, days):
+    """记录实际交易日范围/有效行数/是否短窗，区分新股历史不足与上游截断。"""
+    if df is None or getattr(df, "empty", False):
+        return {"rows": 0, "first": None, "last": None, "short": True, "reason": "empty"}
+    rows = len(df)
+    dates = None
+    for col in ("日期", "date"):
+        if col in df.columns:
+            dates = [str(v)[:10] for v in df[col].tolist()]
+            break
+    first = dates[0] if dates else None
+    last = dates[-1] if dates else None
+    return {"rows": rows, "first": first, "last": last, "short": rows < int(days), "reason": "short" if rows < int(days) else None}
 
 
 def get_kline_cached(code: str, days: int = 130, fetcher=None):
-    """当日 K 线内存缓存。同日内复用，18/19/20/21 共享，避免重复拉东财。"""
-    global _kline_cache, _kline_cache_date
+    """按取数器隔离、校验窗口、短TTL缓存；同键并发合并。"""
+    global _kline_cache_date
+    days = int(days)
+    if days <= 0:
+        raise ValueError("K线窗口必须为正整数")
+    if fetcher is None:
+        from backend.agents.layer1_data_collector.sources.historical_kline import fetch_historical as fetcher
     today = datetime.now(BEIJING_TZ).date().isoformat()
-    if _kline_cache_date != today:
-        _kline_cache = {}
-        _kline_cache_date = today
     code = str(code).zfill(6)
-    if code not in _kline_cache:
-        if fetcher is None:
-            from backend.agents.layer1_data_collector.sources.historical_kline import fetch_historical as fetcher
-        _kline_cache[code] = fetcher(code, days)
-    return _kline_cache[code]
+    # 保留取数器引用避免id重用；未来不同复权能力须传不同取数器。
+    key = (code, id(fetcher))
+    with _kline_locks[hash(key) % len(_kline_locks)]:
+        with _kline_guard:
+            if _kline_cache_date != today:
+                _kline_cache.clear()
+                _kline_cache_date = today
+            generation = _kline_cache_generation
+            entry = _kline_cache.get(key)
+            if entry and entry["expires"] > monotonic():
+                if entry.get("failed"):
+                    return None  # 负缓存：短 TTL 内不重复轰炸
+                # 短窗口不得冒充长窗口：实际行数不足请求窗口时必须补取（P1）
+                if entry["days"] >= days and entry.get("rows", entry["days"]) >= days:
+                    return _kline_slice(entry["data"], days)
+        data = fetcher(code, days)
+        failed = data is None or getattr(data, "empty", False) or len(data) == 0
+        if not failed:
+            _persist_kline_best_effort(code, data)
+        ttl = KLINE_NEGATIVE_CACHE_TTL_SECONDS if failed else KLINE_CACHE_TTL_SECONDS
+        with _kline_guard:
+            if generation == _kline_cache_generation and _kline_cache_date == today:
+                if len(_kline_cache) >= 4096:
+                    _kline_cache.pop(next(iter(_kline_cache)))
+                cached = _kline_slice(data, days)
+                _kline_cache[key] = {
+                    "data": cached, "days": days, "failed": bool(failed),
+                    "rows": 0 if failed else (len(cached) if cached is not None else 0),
+                    "expires": monotonic() + ttl, "fetcher": fetcher,
+                }
+        return _kline_slice(data, days)
 
 
 def kline_cache_clear() -> None:
     """清空 K 线缓存（测试/跨日重置用）。"""
-    global _kline_cache, _kline_cache_date
-    _kline_cache = {}
-    _kline_cache_date = None
+    global _kline_cache_date, _kline_cache_generation
+    with _kline_guard:
+        _kline_cache.clear()
+        _kline_cache_date = None
+        _kline_cache_generation += 1
 
 
 # ==== 快照内存缓存（router 列表接口 <1ms，第七波 G6）====
@@ -354,9 +444,7 @@ def read_code_kline(code: str, days: int = 60) -> list:
     返回 [{date, open, close, high, low, vol}, ...]（JSON 安全）。
     """
     try:
-        from backend.agents.layer1_data_collector.sources.historical_kline import fetch_historical
-
-        hist = fetch_historical(str(code).zfill(6), int(days))
+        hist = get_kline_cached(str(code).zfill(6), int(days))
     except Exception:  # noqa: BLE001
         return []
     if hist is None or getattr(hist, "empty", True):
@@ -397,4 +485,3 @@ def intraday_append(values: list, realtime_value) -> list:
     if rt is None or rt <= 0:
         return values or []
     return list(values or []) + [rt]
-
diff --git a/backend/plugins/emotion_cycle/service.py b/backend/plugins/emotion_cycle/service.py
index a031001..26dc180 100644
--- a/backend/plugins/emotion_cycle/service.py
+++ b/backend/plugins/emotion_cycle/service.py
@@ -74,9 +74,22 @@ def run_emotion_once(target_date=None, force: bool = False) -> dict:
     try:
         from backend.agents.layer1_data_collector.sources.eastmoney_zt import fetch_zt_pool
         zt_pool = fetch_zt_pool(target)
+        zt_fetch_error = None
     except Exception as exc:  # noqa: BLE001
         logger.warning("涨停池拉取失败: %s", exc)
         zt_pool = None
+        zt_fetch_error = str(exc)
+    # 源不可用（封板/连板事件缺失）≠ 合法空池：显式 unavailable，不静默当 0 涨停
+    if zt_pool is None:
+        payload = {
+            "status": "unavailable_required_fields",
+            "reason": "涨停池源不可用（封板/连板事件缺失）",
+            "unavailable_required_fields": ["zt_events"],
+            "zt_fetch_error": zt_fetch_error,
+            "date": target.isoformat(), "regime": "no_data", "count": 0,
+        }
+        write_snapshot(SNAPSHOT_NAME, payload)
+        return payload
     records = parse_zt_records(zt_pool)
     if not records:
         payload = {"status": "no_data", "reason": "涨停池为空", "date": target.isoformat(), "regime": "no_data", "count": 0}
diff --git a/backend/plugins/overnight_arbitrage/__init__.py b/backend/plugins/overnight_arbitrage/__init__.py
index 8220a7d..8ba61d2 100644
--- a/backend/plugins/overnight_arbitrage/__init__.py
+++ b/backend/plugins/overnight_arbitrage/__init__.py
@@ -11,9 +11,10 @@ def register_router() -> APIRouter:
 def run_cli(args):
     """CLI 入口（被 backend.main 调用）。"""
     import asyncio
-    from .service import run_overnight_arbitrage
+    from .service import run_overnight_arbitrage, _refine_quotes_with_tencent
 
     return asyncio.run(run_overnight_arbitrage(
         target_date=getattr(args, "target_date", None),
         dry_run=getattr(args, "dry_run", False),
+        candidate_refiner=_refine_quotes_with_tencent,
     ))
diff --git a/backend/plugins/overnight_arbitrage/service.py b/backend/plugins/overnight_arbitrage/service.py
index af5658b..73669f9 100644
--- a/backend/plugins/overnight_arbitrage/service.py
+++ b/backend/plugins/overnight_arbitrage/service.py
@@ -94,6 +94,7 @@ def _append_quote_source(quotes: pd.DataFrame, source_name: str) -> pd.DataFrame
     result = quotes.copy()
     if not result.empty:
         result["数据源"] = source_name
+    result.attrs = dict(getattr(quotes, "attrs", {}) or {})
     return result
 
 
@@ -172,6 +173,31 @@ def _missing_quote_fields(row: dict) -> List[str]:
     ]
 
 
+def _quote_quality_block(row: dict, now: Optional[datetime] = None) -> Optional[str]:
+    """统一报价质量门控（§12.4 第1步）：降级/陈旧/未知源时间不得参与有效 BUY。
+
+    无任何质量元数据的旧/合成行保持兼容（不拦截）；真实兜底行由 degraded 标记拦截。
+    """
+    if row.get("degraded") is True or row.get("is_stale") is True:
+        return "degraded_or_stale"
+    source_time = row.get("source_time")
+    if source_time is None:
+        return None
+    try:
+        stamp = datetime.fromisoformat(str(source_time))
+        if stamp.tzinfo is None:
+            stamp = stamp.replace(tzinfo=BEIJING_TZ)
+        stamp = stamp.astimezone(BEIJING_TZ)
+    except (TypeError, ValueError):
+        return "source_time_invalid"
+    now = (now or datetime.now(BEIJING_TZ)).astimezone(BEIJING_TZ)
+    if stamp.date() != now.date():
+        return "source_time_stale_date"
+    if (now - stamp).total_seconds() > 900:
+        return "source_time_too_old"
+    return None
+
+
 def _reject_reason(row: dict) -> Optional[str]:
     scope_reason = _scope_reject_reason(row)
     if scope_reason:
@@ -225,7 +251,7 @@ def _build_zt_map(zt_pool: Optional[pd.DataFrame]) -> dict:
     return result
 
 
-def _decision_item(row: dict, zt_info: dict, minute: dict) -> dict:
+def _decision_item(row: dict, zt_info: dict, minute: dict, zt_events_available: bool = True) -> dict:
     code = str(row.get("代码", "")).zfill(6)
     current_price = _float(row.get("最新价"))
     change_pct = _float(row.get("涨跌幅"), 0) or 0
@@ -237,6 +263,9 @@ def _decision_item(row: dict, zt_info: dict, minute: dict) -> dict:
     break_count = int(zt_info.get("break_count") or 0)
     consecutive = int(zt_info.get("consecutive") or 0)
     missing_quote_fields = _missing_quote_fields(row)
+    unavailable_required_fields = []
+    if not zt_events_available:
+        unavailable_required_fields.append("zt_events")
 
     score = 0.0
     score += _clip((change_pct - 5.5) * 4.2, 0, 22)
@@ -310,6 +339,8 @@ def _decision_item(row: dict, zt_info: dict, minute: dict) -> dict:
         "consecutive": consecutive,
         "minute_strength": minute or {},
         "missing_quote_fields": missing_quote_fields,
+        "unavailable_required_fields": unavailable_required_fields,
+        "zt_events_available": zt_events_available,
         "reasons": reasons[:6],
         "risks": risks[:5],
         "valid_until": "14:40-14:55",
@@ -332,6 +363,7 @@ def build_overnight_decision(
     generated_at = generated_at or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
     minute_strength = minute_strength or {}
     zt_map = _build_zt_map(zt_pool)
+    zt_events_available = bool(zt_pool is not None and not getattr(zt_pool, "empty", False))
 
     rejected = []
     removed = []
@@ -346,6 +378,10 @@ def build_overnight_decision(
             if scope_reason:
                 rejected.append({"code": code, "name": name, "reason": scope_reason})
                 continue
+            quality_reason = _quote_quality_block(row)
+            if quality_reason:
+                removed.append({"code": code, "name": name, "quality": quality_reason})
+                continue
             missing_fields = _missing_quote_fields(row)
             if missing_fields:
                 removed.append({"code": code, "name": name, "missing_fields": missing_fields})
@@ -354,7 +390,7 @@ def build_overnight_decision(
             if reason:
                 rejected.append({"code": code, "name": name, "reason": reason})
                 continue
-            item = _decision_item(row, zt_map.get(code, {}), minute_strength.get(code, {}))
+            item = _decision_item(row, zt_map.get(code, {}), minute_strength.get(code, {}), zt_events_available)
             if item["action"] != "PASS":
                 candidates.append(item)
             else:
@@ -388,12 +424,13 @@ def build_overnight_decision(
         "total_scanned": total,
         "results": results,
         "data_quality": {
-            "status": "complete",
+            "status": "complete" if not any("quality" in r for r in removed) else "partial",
             "required_fields": list(REQUIRED_QUOTE_FIELDS),
             "removed_count": len(removed),
             "removed": removed,
         },
         "rejected": rejected[:50],
+        "capabilities": {"zt_events_available": zt_events_available},
         "source_status": {
             "quotes": normalized_quote_status,
             "channel": _detect_quote_channel_issue(normalized_quote_status),
@@ -635,16 +672,25 @@ def _attach_history_summary(decision: dict, history: dict) -> None:
     }
 
 
-def _eastmoney_all_a_snapshot() -> pd.DataFrame:
-    """拉取沪深全 A 实时快照，供尾盘套利粗筛。"""
+def _eastmoney_all_a_snapshot(max_pages: int = 40, budget_seconds: float = 60.0) -> pd.DataFrame:
+    """拉取沪深全 A 实时快照，供尾盘套利粗筛；记录真实覆盖/截断元数据（§12.2 P1）。"""
+    import time as _time
     import requests
 
     url = "https://push2.eastmoney.com/api/qt/clist/get"
     headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/center/gridlist.html"}
     fields = "f2,f3,f5,f6,f8,f9,f10,f12,f14,f15,f20,f21"
     rows = []
+    seen = set()
+    universe_total = 0
+    truncated = False
+    started = _time.monotonic()
     for fs in ("m:1+t:2,m:1+t:23", "m:0+t:6,m:0+t:80"):
-        for page in range(1, 8):
+        page = 1
+        while page <= max_pages and not truncated:
+            if _time.monotonic() - started > budget_seconds:
+                truncated = True
+                break
             params = {
                 "pn": page,
                 "pz": 200,
@@ -657,14 +703,25 @@ def _eastmoney_all_a_snapshot() -> pd.DataFrame:
                 "fields": fields,
                 "ut": "bd1d9ddb04089700cf9c27f6f7426281",
             }
-            resp = requests.get(url, params=params, headers=headers, timeout=12)
-            resp.raise_for_status()
-            diff = resp.json().get("data", {}).get("diff") or []
+            try:
+                resp = requests.get(url, params=params, headers=headers, timeout=12)
+                resp.raise_for_status()
+                data = resp.json().get("data") or {}
+                universe_total += int(data.get("total") or 0)
+                diff = data.get("diff") or []
+            except Exception as exc:
+                logger.warning("东财全A快照第%d页失败: %s", page, exc)
+                truncated = True
+                break
             if not diff:
                 break
             for item in diff:
+                code = str(item.get("f12", "")).zfill(6)
+                if code in seen:
+                    continue
+                seen.add(code)
                 rows.append({
-                    "代码": str(item.get("f12", "")).zfill(6),
+                    "代码": code,
                     "名称": str(item.get("f14", "")),
                     "最新价": _float(item.get("f2")),
                     "涨跌幅": _float(item.get("f3")),
@@ -677,11 +734,19 @@ def _eastmoney_all_a_snapshot() -> pd.DataFrame:
                     "总市值": _float(item.get("f20")),
                     "流通市值": _float(item.get("f21")),
                 })
-    return pd.DataFrame(rows)
+            page += 1
+    df = pd.DataFrame(rows)
+    df.attrs["coverage"] = {
+        "source": "eastmoney_all_a",
+        "universe_total": universe_total,
+        "received": len(rows),
+        "truncated": truncated or len(rows) < universe_total,
+    }
+    return df
 
 
 def _sina_all_a_snapshot(max_pages: int = 80) -> pd.DataFrame:
-    """新浪财经全A兜底源；过滤创业板，只保留沪深可交易A股字段。"""
+    """新浪财经全A兜底源；过滤创业板，只保留沪深可交易A股字段（附覆盖元数据）。"""
     import requests
 
     url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
@@ -731,7 +796,14 @@ def _sina_all_a_snapshot(max_pages: int = 80) -> pd.DataFrame:
             seen.add(code)
         if len(data) < 80:
             break
-    return pd.DataFrame(rows)
+    df = pd.DataFrame(rows)
+    df.attrs["coverage"] = {
+        "source": "sina_all_a",
+        "universe_total": None,  # 新浪此端点不返回总数，记 None
+        "received": len(rows),
+        "truncated": False,
+    }
+    return df
 
 
 def _zt_pool_quote_fallback(zt_pool: Optional[pd.DataFrame]) -> pd.DataFrame:
@@ -760,6 +832,28 @@ def _zt_pool_quote_fallback(zt_pool: Optional[pd.DataFrame]) -> pd.DataFrame:
     return pd.DataFrame(rows)
 
 
+def _refine_quotes_with_tencent(quotes: pd.DataFrame, codes: list) -> pd.DataFrame:
+    """候选精查贯通（§12.4 第2步）：对候选代码重取精查行情并整体替换该行，不跨源拼接字段。"""
+    if quotes is None or quotes.empty or not codes:
+        return quotes
+    try:
+        from backend.agents.layer1_data_collector.sources.eastmoney_quote import fetch_tencent_quotes_for_codes
+
+        refined = fetch_tencent_quotes_for_codes(list(codes))
+    except Exception as exc:  # noqa: BLE001
+        logger.warning("候选精查失败，保留粗筛行情: %s", exc)
+        return quotes
+    if refined.empty:
+        return quotes
+    target = {str(c).zfill(6) for c in codes}
+    refined = refined[refined["代码"].map(lambda c: str(c).zfill(6) in target)] if "代码" in refined.columns else refined
+    kept = quotes[~quotes["代码"].map(lambda c: str(c).zfill(6) in set(refined["代码"]))]
+    result = pd.concat([kept, refined], ignore_index=True)
+    result.attrs = dict(getattr(quotes, "attrs", {}) or {})
+    result.attrs["refined_codes"] = sorted(set(str(c).zfill(6) for c in refined["代码"]))
+    return result
+
+
 def _fetch_quotes_with_fallbacks(
     primary_fetcher: Callable[[], pd.DataFrame],
     zt_pool: Optional[pd.DataFrame] = None,
@@ -777,7 +871,13 @@ def _fetch_quotes_with_fallbacks(
             quotes = fetcher()
             count = 0 if quotes is None else len(quotes)
             if count:
-                statuses.append(_empty_source_status(source_name, "ok", count))
+                coverage = getattr(quotes, "attrs", {}).get("coverage") or {}
+                status = _empty_source_status(source_name, "ok", count)
+                status["coverage"] = coverage
+                if coverage.get("truncated"):
+                    status["status"] = "degraded"
+                    errors.append(f"{source_name} 覆盖不完整: received={coverage.get('received')} universe={coverage.get('universe_total')}")
+                statuses.append(status)
                 return _append_quote_source(quotes, source_name), statuses, errors
             statuses.append(_empty_source_status(source_name, "empty", 0))
             errors.append(f"{source_name} 返回空行情")
@@ -798,37 +898,66 @@ def _fetch_quotes_with_fallbacks(
 
 
 def _fetch_yahoo_5m_strength(codes: Iterable[str]) -> Dict[str, dict]:
-    """对前排候选拉 Yahoo 5 分钟 K 线，失败时返回空映射。"""
-    import time
+    """对前排候选拉 Yahoo 5 分钟 K 线（§12.4 第1步：按 timestamp 整根对齐，失败时返回空映射）。"""
+    import time as _time
     import requests
 
     result = {}
-    now = int(time.time())
-    start = now - 2 * 24 * 3600
+    now_ts = int(_time.time())
+    start = now_ts - 2 * 24 * 3600
     headers = {"User-Agent": "Mozilla/5.0"}
     for code in list(codes)[:30]:
         suffix = ".SS" if str(code).startswith(("6", "9")) else ".SZ"
         url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}{suffix}"
-        params = {"period1": start, "period2": now, "interval": "5m", "includePrePost": "false"}
+        params = {"period1": start, "period2": now_ts, "interval": "5m", "includePrePost": "false"}
         try:
             resp = requests.get(url, params=params, headers=headers, timeout=8)
             resp.raise_for_status()
             chart = (resp.json().get("chart", {}).get("result") or [None])[0] or {}
+            timestamps = chart.get("timestamp") or []
             quote = ((chart.get("indicators") or {}).get("quote") or [None])[0] or {}
-            closes = [v for v in quote.get("close") or [] if v is not None]
-            highs = [v for v in quote.get("high") or [] if v is not None]
-            lows = [v for v in quote.get("low") or [] if v is not None]
-            if len(closes) < 4 or not highs or not lows:
+            closes = quote.get("close") or []
+            highs = quote.get("high") or []
+            lows = quote.get("low") or []
+            # 按 timestamp 组合整根 bar，丢弃缺字段样本
+            bars = []
+            for i, ts in enumerate(timestamps):
+                c = closes[i] if i < len(closes) else None
+                h = highs[i] if i < len(highs) else None
+                l = lows[i] if i < len(lows) else None
+                if c is None or h is None or l is None:
+                    continue
+                bars.append((int(ts), c, h, l))
+            bars.sort()
+            if len(bars) < 4:
+                continue
+            # 新鲜度：最后一根不得早于 now-10min，极旧时间戳不产生强度
+            last_ts = bars[-1][0]
+            if now_ts - last_ts > 10 * 60:
                 continue
-            base = closes[-4]
-            change = 0.0 if not base else (closes[-1] - base) / base * 100
-            high = max(highs[-6:])
-            low = min(lows[-6:])
-            position = 0.5 if high <= low else (closes[-1] - low) / (high - low)
+            # 同日连续窗口：最近若干根必须同一交易日且相邻间隔 ≤10min（午休/跨日缺口即排除）
+            last_dt = datetime.fromtimestamp(last_ts, tz=BEIJING_TZ)
+            recent = []
+            for b in reversed(bars[-6:]):
+                dt = datetime.fromtimestamp(b[0], tz=BEIJING_TZ)
+                if dt.date() != last_dt.date():
+                    break
+                if recent and (recent[-1][0] - b[0]) > 10 * 60:
+                    break
+                recent.append(b)
+            recent.reverse()
+            if len(recent) < 4:
+                continue
+            base = recent[-4][1]
+            change = 0.0 if not base else (recent[-1][1] - base) / base * 100
+            high = max(b[2] for b in recent)
+            low = min(b[3] for b in recent)
+            position = 0.5 if high <= low else (recent[-1][1] - low) / (high - low)
             result[str(code)] = {
                 "last_15m_change_pct": round(change, 3),
                 "last_close_position": round(_clip(position, 0, 1), 3),
                 "source": "Yahoo 5m",
+                "source_time": last_dt.isoformat(),
             }
         except Exception as exc:
             logger.debug("Yahoo 5m %s 失败: %s", code, exc)
@@ -861,6 +990,7 @@ async def run_overnight_arbitrage(
     quote_fetcher: Optional[Callable[[], pd.DataFrame]] = None,
     zt_fetcher: Optional[Callable[..., Optional[pd.DataFrame]]] = None,
     minute_fetcher: Optional[Callable[[Iterable[str]], Dict[str, dict]]] = None,
+    candidate_refiner: Optional[Callable[[pd.DataFrame, list], pd.DataFrame]] = None,
     dry_run: bool = False,
     current_time: Optional[datetime] = None,
     notification_state_file: Optional[Path] = None,
@@ -873,6 +1003,7 @@ async def run_overnight_arbitrage(
         current_time = current_time.astimezone(BEIJING_TZ)
     target_date = target_date or current_time.date()
     generated_at = current_time.strftime("%Y-%m-%d %H:%M:%S")
+    wall_started = datetime.now(BEIJING_TZ).astimezone(BEIJING_TZ)
     notification_state_file = notification_state_file or NOTIFICATION_STATE_FILE
     quote_fetcher = quote_fetcher or _eastmoney_all_a_snapshot
     if zt_fetcher is None:
@@ -912,6 +1043,14 @@ async def run_overnight_arbitrage(
         logger.warning("尾盘套利5分钟K线增强失败: %s", exc)
         errors.append(f"5分钟K线增强失败: {exc}")
 
+    # 候选精查贯通（§12.4 第2步）：生产 CLI 注入腾讯精查；测试不注入时保持原行为
+    if candidate_refiner is not None and seed_codes:
+        try:
+            quotes = candidate_refiner(quotes, seed_codes)
+        except Exception as exc:  # noqa: BLE001
+            logger.warning("候选精查失败: %s", exc)
+            errors.append(f"候选精查失败: {exc}")
+
     decision = build_overnight_decision(
         quotes,
         zt_pool=zt_pool,
@@ -937,6 +1076,9 @@ async def run_overnight_arbitrage(
         blocked_reasons.append("zt_pool_unavailable")
     if _notification_already_sent(target_date, notification_state_file):
         blocked_reasons.append("already_sent")
+    # §12.4 第1步：完成时刻复核，任务超时不能沿用启动时刻放行（按真实墙钟耗时）
+    if (datetime.now(BEIJING_TZ).astimezone(BEIJING_TZ) - wall_started).total_seconds() > 900:
+        blocked_reasons.append("task_exceeded_valid_window")
     decision["notification"] = {
         "eligible": not blocked_reasons,
         "sent": False,
diff --git a/backend/plugins/zt_seal/service.py b/backend/plugins/zt_seal/service.py
index f0d28c9..3acd18d 100644
--- a/backend/plugins/zt_seal/service.py
+++ b/backend/plugins/zt_seal/service.py
@@ -81,9 +81,22 @@ def run_zt_seal_once(target_date=None, force: bool = False) -> dict:
     try:
         from backend.agents.layer1_data_collector.sources.eastmoney_zt import fetch_zt_pool
         zt_pool = fetch_zt_pool(target)
+        zt_fetch_error = None
     except Exception as exc:  # noqa: BLE001
         logger.warning("涨停池拉取失败: %s", exc)
         zt_pool = None
+        zt_fetch_error = str(exc)
+    # 源不可用（封单事件缺失）≠ 合法空池：显式 unavailable，不静默当 0
+    if zt_pool is None:
+        payload = {
+            "status": "unavailable_required_fields",
+            "reason": "涨停池源不可用（封单事件缺失）",
+            "unavailable_required_fields": ["zt_events"],
+            "zt_fetch_error": zt_fetch_error,
+            "date": target.isoformat(), "items": [],
+        }
+        write_snapshot(SNAPSHOT_NAME, payload)
+        return payload
     rows = build_seal_rows(zt_pool)
     if not rows:
         payload = {"status": "no_data", "reason": "涨停池为空", "date": target.isoformat(), "items": []}
diff --git a/backend/services/data_backend/bars_store.py b/backend/services/data_backend/bars_store.py
new file mode 100644
index 0000000..b49b197
--- /dev/null
+++ b/backend/services/data_backend/bars_store.py
@@ -0,0 +1,179 @@
+"""日K历史持久化（§12.4 第3步）：最小增量、按需补洞、复权版本失效、备份与恢复。
+
+单一 SQLite 表 bars_daily，主键 (code, trade_date, adjustment) 做幂等 upsert；
+不新建平行数据平台，复用现有 backend.db.database 引擎。
+"""
+from __future__ import annotations
+
+import sqlite3
+from datetime import date
+from pathlib import Path
+from typing import Iterable, Optional
+
+import pandas as pd
+
+from backend.db.database import engine
+from backend.plugins.common import BEIJING_TZ, now_beijing
+from backend.services.trading_calendar import is_trading_day, trading_days_between_dates
+
+TABLE = "bars_daily"
+_SCHEMA = """
+CREATE TABLE IF NOT EXISTS bars_daily (
+    code TEXT NOT NULL,
+    trade_date TEXT NOT NULL,
+    adjustment TEXT NOT NULL DEFAULT 'raw',
+    adjustment_version TEXT,
+    open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
+    source TEXT,
+    is_final INTEGER DEFAULT 0,
+    fetched_at TEXT,
+    PRIMARY KEY (code, trade_date, adjustment)
+);
+CREATE INDEX IF NOT EXISTS idx_bars_daily_code_date ON bars_daily(code, trade_date);
+"""
+
+_COLUMN_MAP = {
+    "日期": "trade_date", "date": "trade_date",
+    "开盘": "open", "open": "open",
+    "最高": "high", "high": "high",
+    "最低": "low", "low": "low",
+    "收盘": "close", "close": "close",
+    "成交量": "volume", "volume": "volume", "vol": "volume",
+    "成交额": "amount", "amount": "amount",
+}
+
+
+def ensure_schema() -> None:
+    raw = engine.raw_connection()
+    try:
+        cur = raw.cursor()
+        for stmt in _SCHEMA.strip().split(";"):
+            if stmt.strip():
+                cur.execute(stmt)
+        raw.commit()
+    finally:
+        raw.close()
+
+
+def _normalize(df: pd.DataFrame) -> pd.DataFrame:
+    out = df.rename(columns={k: v for k, v in _COLUMN_MAP.items() if k in df.columns})
+    for col in ("open", "high", "low", "close", "volume", "amount"):
+        if col in out.columns:
+            out[col] = pd.to_numeric(out[col], errors="coerce")
+    return out
+
+
+def upsert_daily_bars(code: str, df: pd.DataFrame, *, adjustment: str = "raw",
+                      adjustment_version: Optional[str] = None, source: Optional[str] = None,
+                      is_final: bool = False) -> int:
+    """幂等 upsert 日K；返回写入/更新行数。"""
+    if df is None or df.empty:
+        return 0
+    ensure_schema()
+    normalized = _normalize(df)
+    if "trade_date" not in normalized.columns:
+        return 0
+    code = str(code).zfill(6)
+    fetched_at = now_beijing().isoformat()
+    rows = []
+    for _, r in normalized.iterrows():
+        td = str(r.get("trade_date"))[:10]
+        if not td:
+            continue
+        rows.append((
+            code, td, adjustment, adjustment_version,
+            r.get("open"), r.get("high"), r.get("low"), r.get("close"),
+            r.get("volume"), r.get("amount"), source, int(bool(is_final)), fetched_at,
+        ))
+    if not rows:
+        return 0
+    raw = engine.raw_connection()
+    try:
+        cur = raw.cursor()
+        cur.executemany(
+            f"""INSERT INTO {TABLE}
+                (code, trade_date, adjustment, adjustment_version,
+                 open, high, low, close, volume, amount, source, is_final, fetched_at)
+                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
+                ON CONFLICT(code, trade_date, adjustment) DO UPDATE SET
+                 adjustment_version=excluded.adjustment_version,
+                 open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
+                 volume=excluded.volume, amount=excluded.amount,
+                 source=excluded.source, is_final=excluded.is_final, fetched_at=excluded.fetched_at""",
+            rows,
+        )
+        raw.commit()
+        return len(rows)
+    finally:
+        raw.close()
+
+
+def read_daily_bars(code: str, start: Optional[str] = None, end: Optional[str] = None,
+                    adjustment: str = "raw") -> pd.DataFrame:
+    ensure_schema()
+    sql = f"SELECT trade_date, open, high, low, close, volume, amount, source, is_final, adjustment_version FROM {TABLE} WHERE code = :c AND adjustment = :a"
+    params = {"c": str(code).zfill(6), "a": adjustment}
+    if start:
+        sql += " AND trade_date >= :s"; params["s"] = str(start)[:10]
+    if end:
+        sql += " AND trade_date <= :e"; params["e"] = str(end)[:10]
+    sql += " ORDER BY trade_date"
+    return pd.read_sql(sql, engine, params=params)
+
+
+def missing_dates(code: str, start: str, end: str, adjustment: str = "raw") -> list:
+    """按交易日历补洞：闭区间内应存在的交易日减去已入库日期。"""
+    s = date.fromisoformat(str(start)[:10])
+    e = date.fromisoformat(str(end)[:10])
+    expected = [d.isoformat() for d in trading_days_between_dates(s, e) if is_trading_day(d)]
+    existing = set(read_daily_bars(code, start, end, adjustment)["trade_date"].tolist())
+    return [d for d in expected if d not in existing]
+
+
+def delete_adjustment(code: str, adjustment: str) -> int:
+    """复权版本失效：删除该 (code, adjustment) 全部行。"""
+    ensure_schema()
+    raw = engine.raw_connection()
+    try:
+        cur = raw.cursor()
+        cur.execute(f"DELETE FROM {TABLE} WHERE code = :c AND adjustment = :a",
+                    {"c": str(code).zfill(6), "a": adjustment})
+        raw.commit()
+        return cur.rowcount
+    finally:
+        raw.close()
+
+
+def backup(target_path: Path) -> Path:
+    """在线备份 API（源库保持可写）；失败抛异常，不假装成功。"""
+    ensure_schema()
+    target_path = Path(target_path)
+    target_path.parent.mkdir(parents=True, exist_ok=True)
+    src = engine.raw_connection()
+    dst = sqlite3.connect(str(target_path))
+    try:
+        src.backup(dst)
+    finally:
+        dst.close()
+        src.close()
+    return target_path
+
+
+def verify_backup(target_path: Path) -> bool:
+    """恢复演练校验：备份可打开、表存在、行数可读。"""
+    path = Path(target_path)
+    if not path.exists():
+        return False
+    con = sqlite3.connect(str(path))
+    try:
+        cur = con.cursor()
+        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABLE,))
+        if cur.fetchone() is None:
+            return False
+        cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
+        cur.fetchone()
+        return True
+    except sqlite3.Error:
+        return False
+    finally:
+        con.close()
diff --git a/backend/services/data_backend/shadow_run.py b/backend/services/data_backend/shadow_run.py
new file mode 100644
index 0000000..f2efced
--- /dev/null
+++ b/backend/services/data_backend/shadow_run.py
@@ -0,0 +1,94 @@
+"""部署地影子运行观测（§12.4 第5步，代码侧准备）。
+
+记录每轮数据面的 source/coverage/行情年龄/错误/截止时间达成，供 5 交易日观测聚合。
+本机可测试聚合逻辑；真实观测在部署地开启 SHADOW_RUN_ENABLED=1 后写入。
+"""
+from __future__ import annotations
+
+import json
+import os
+from pathlib import Path
+from typing import Optional
+
+from backend.plugins.common import BEIJING_TZ, now_beijing
+
+PROJECT_DIR = Path(__file__).resolve().parents[3]
+DATA_DIR = PROJECT_DIR / "data"
+SHADOW_FILE = DATA_DIR / "shadow_run.json"
+_MAX_RECORDS = 20000
+
+
+def enabled() -> bool:
+    return os.environ.get("SHADOW_RUN_ENABLED", "").lower() in {"1", "true", "yes"}
+
+
+def _read() -> list:
+    if not SHADOW_FILE.exists():
+        return []
+    try:
+        payload = json.loads(SHADOW_FILE.read_text(encoding="utf-8"))
+        return payload.get("records") or []
+    except (json.JSONDecodeError, OSError):
+        return []
+
+
+def _write(records: list) -> None:
+    DATA_DIR.mkdir(parents=True, exist_ok=True)
+    SHADOW_FILE.write_text(
+        json.dumps({"records": records[-_MAX_RECORDS:]}, ensure_ascii=False, indent=1, default=str),
+        encoding="utf-8",
+    )
+
+
+def record(asset: str, source: str, status: str, *, coverage: Optional[dict] = None,
+           source_time: Optional[str] = None, age_seconds: Optional[float] = None,
+           error: Optional[str] = None, deadline_met: Optional[bool] = None) -> None:
+    """记录一轮；未开启时静默跳过。"""
+    if not enabled():
+        return
+    records = _read()
+    records.append({
+        "ts": now_beijing().isoformat(),
+        "asset": asset, "source": source, "status": status,
+        "coverage": coverage or {},
+        "source_time": source_time,
+        "age_seconds": age_seconds,
+        "error": error,
+        "deadline_met": deadline_met,
+    })
+    _write(records)
+
+
+def summarize() -> dict:
+    """按 asset×source 聚合：轮数、成功率、行情年龄 P95、截止达成率、错误类型。"""
+    records = _read()
+    groups = {}
+    for r in records:
+        key = (r.get("asset"), r.get("source"))
+        g = groups.setdefault(key, {"runs": 0, "errors": 0, "ages": [], "deadline": [], "error_types": {}})
+        g["runs"] += 1
+        if r.get("status") in ("error", "unavailable", "degraded") or r.get("error"):
+            g["errors"] += 1
+        if r.get("error"):
+            g["error_types"][r["error"][:60]] = g["error_types"].get(r["error"][:60], 0) + 1
+        if r.get("age_seconds") is not None:
+            g["ages"].append(float(r["age_seconds"]))
+        if r.get("deadline_met") is not None:
+            g["deadline"].append(bool(r["deadline_met"]))
+    out = {}
+    for (asset, source), g in sorted(groups.items()):
+        ages = sorted(g["ages"])
+        p95 = ages[int(len(ages) * 0.95)] if ages else None
+        dl = g["deadline"]
+        out[f"{asset}@{source}"] = {
+            "runs": g["runs"],
+            "error_rate": round(g["errors"] / g["runs"], 4) if g["runs"] else None,
+            "age_p95_seconds": p95,
+            "deadline_met_rate": round(sum(dl) / len(dl), 4) if dl else None,
+            "error_types": dict(sorted(g["error_types"].items(), key=lambda kv: -kv[1])[:5]),
+        }
+    return out
+
+
+def reset() -> None:
+    _write([])
diff --git a/backend/services/data_backend/snapshots.py b/backend/services/data_backend/snapshots.py
index 4bacf9f..5b1bcb8 100644
--- a/backend/services/data_backend/snapshots.py
+++ b/backend/services/data_backend/snapshots.py
@@ -62,21 +62,40 @@ def _read_json(path: Path) -> Optional[dict]:
         return None
 
 
+def _atomic_write_json(path: Path, payload: dict) -> None:
+    """唯一临时文件 + 原子替换，避免读者读到半截 JSON。"""
+    path.parent.mkdir(parents=True, exist_ok=True)
+    tmp = path.with_name(f".{path.name}.{os.getpid()}.{int(_now().timestamp() * 1_000_000)}.tmp")
+    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
+    os.replace(tmp, path)
+
+
 def _write_local_snapshot(asset: str, payload: dict) -> None:
+    """原子双写并附同一 snapshot_version；读取方据此拒绝批次不一致的副本（§12.4 第2步）。"""
+    import uuid
+
     DATA_BACKEND_DIR.mkdir(parents=True, exist_ok=True)
     REPORT_DATA_BACKEND_DIR.mkdir(parents=True, exist_ok=True)
+    version = uuid.uuid4().hex
+    payload = dict(payload)
+    payload["snapshot_version"] = version
+    payload["written_at"] = _now().isoformat()
     _MEMORY_CACHE[asset] = payload
-    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
-    _snapshot_path(asset).write_text(text, encoding="utf-8")
-    _report_snapshot_path(asset).write_text(text, encoding="utf-8")
+    # 先写 canonical（data_backend），再写镜像（reports）；任一失败不污染已成功副本
+    _atomic_write_json(_snapshot_path(asset), payload)
+    _atomic_write_json(_report_snapshot_path(asset), payload)
 
 
 def _read_local_snapshot(asset: str) -> Optional[dict]:
     if asset in _MEMORY_CACHE:
         return _MEMORY_CACHE[asset]
-    payload = _read_json(_snapshot_path(asset))
-    if not payload:
-        payload = _read_json(_report_snapshot_path(asset))
+    canonical = _read_json(_snapshot_path(asset))
+    mirror = _read_json(_report_snapshot_path(asset))
+    payload = canonical or mirror
+    if canonical and mirror and canonical.get("snapshot_version") != mirror.get("snapshot_version"):
+        # 批次不一致：以 canonical 为准，显式标记不一致（旧文件无 version 时不判定）
+        payload = dict(canonical)
+        payload["_batch_inconsistent"] = True
     if payload:
         _MEMORY_CACHE[asset] = payload
     return payload
@@ -101,12 +120,24 @@ def _age_seconds(fetched_at: Any) -> Optional[int]:
     return max(int((_now() - fetched_dt).total_seconds()), 0)
 
 
+def _records_codes(records: Any) -> set:
+    """从快照记录提取实际收到的代码集合；无代码字段时返回空集。"""
+    if not isinstance(records, list):
+        return set()
+    codes = set()
+    for row in records:
+        if isinstance(row, dict) and row.get("代码"):
+            codes.add(str(row["代码"]).zfill(6))
+    return codes
+
+
 def _is_payload_covering_codes(payload: Optional[dict], request_codes: Optional[list[str]]) -> bool:
     if not request_codes:
         return True
     if not payload:
         return False
-    cached_codes = set(payload.get("codes") or [])
+    # 优先用实际收到的 received_codes；旧快照无该字段时回退 codes（历史兼容）
+    cached_codes = set(payload.get("received_codes") or payload.get("codes") or [])
     return set(request_codes).issubset(cached_codes)
 
 
@@ -145,12 +176,18 @@ def _meta(asset: str, payload: Optional[dict], source: str, status: str, degrade
         record_count = 1 if records else 0
     else:
         record_count = 0
+    requested = payload.get("requested_codes") if payload else None
+    received = payload.get("received_codes") if payload else None
+    missing = payload.get("missing_codes") if payload else None
     return {
         "asset": asset,
         "source": source,
         "fetched_at": payload.get("fetched_at") if payload else None,
         "age_seconds": _age_seconds(payload.get("fetched_at")) if payload else None,
         "record_count": record_count,
+        "requested_count": len(requested) if isinstance(requested, list) else None,
+        "received_count": len(received) if isinstance(received, list) else None,
+        "missing_count": len(missing) if isinstance(missing, list) else None,
         "status": status,
         "trading_session": current_trading_session(_now()),
         "degraded_from": degraded_from,
@@ -182,13 +219,34 @@ def _read_asset(
         fetched = fetcher(*fetch_args)
         if fetched is not None and (not prefer_dataframe or not getattr(fetched, "empty", False)):
             records = fetched.to_dict("records") if prefer_dataframe else fetched
+            requested = sorted(set(request_codes or []))
+            received = _records_codes(records) if prefer_dataframe else set()
+            missing = [c for c in requested if c not in received]
+            eligible = set()
+            if prefer_dataframe and received:
+                by_code = {}
+                for row in records:
+                    if isinstance(row, dict) and row.get("代码"):
+                        by_code[str(row["代码"]).zfill(6)] = row
+                for code in received:
+                    row = by_code.get(code) or {}
+                    if row.get("degraded") is not True:
+                        eligible.add(code)
             payload = {
                 "source": "live",
                 "fetched_at": _now().isoformat(),
                 "records": records,
-                "codes": sorted(set(request_codes or [])),
+                # codes 必须是实际收到集合，部分返回不得冒充完整覆盖
+                "codes": sorted(received) if received else sorted(requested),
+                "requested_codes": requested,
+                "received_codes": sorted(received),
+                "missing_codes": missing,
+                "missing_reasons": {"partial": "fetcher 未返回全部请求代码"} if missing else {},
             }
             _write_local_snapshot(asset, payload)
+            # 有返回但全部降级（如 source_time 未知）时不标 fresh
+            if prefer_dataframe and received and not eligible:
+                return fetched, _meta(asset, payload, "live", "degraded", "live")
             return fetched, _meta(asset, payload, "live", "fresh", None)
     except Exception as exc:  # noqa: BLE001
         fetch_error = str(exc)
diff --git "a/docs/24_\345\206\263\347\255\226\350\264\250\351\207\217\351\227\255\347\216\257_diff.md" "b/docs/24_\345\206\263\347\255\226\350\264\250\351\207\217\351\227\255\347\216\257_diff.md"
new file mode 100644
index 0000000..d17726c
--- /dev/null
+++ "b/docs/24_\345\206\263\347\255\226\350\264\250\351\207\217\351\227\255\347\216\257_diff.md"
@@ -0,0 +1,78 @@
+# 24 方案 · 决策质量闭环 diff.md（§12.4 第 1 步 · 本轮改动汇总）
+
+> 2026-09-14 后续批次。范围：§12.2 的 P0/P1 缺陷修复（决策质量闭环），不含你此前的 §11 首阶段改动。
+> 回归：本轮相关套件 139 passed（1 条既有 LibreSSL 警告）；smart_money_radar 套件未纳入（既有 pytdx 挂起，与本次无关）。
+
+## 变更清单
+
+| # | 文件 | 修复缺陷（对应 §12.2） |
+|---|---|---|
+| 1 | backend/services/data_backend/snapshots.py | P0-1 快照实际覆盖 |
+| 2 | backend/agents/layer1_data_collector/sources/eastmoney_quote.py | P1 东财 legacy 未知 source_time 冒充新鲜 |
+| 3 | backend/plugins/common.py | P1 K线短窗冒充长窗 |
+| 4 | backend/plugins/overnight_arbitrage/service.py | P0 评分前质量门控 + 完成时刻复核 + yahoo5m 时间对齐 |
+| 5 | tests/test_decision_quality_gates.py（新增） | 固化 8 个反例 |
+
+---
+
+## DIFF-1 snapshots.py（P0-1 快照实际覆盖）
+
+before：live 拉取成功后 payload 的 codes 直接写请求清单（requested），部分返回也声称完整覆盖且标 fresh。
+
+after：
+- 新增 _records_codes：从 records 提取实际收到的代码集合；
+- payload 新增 requested_codes / received_codes / missing_codes / missing_reasons；codes = 实际收到集合（非请求清单）；
+- _is_payload_covering_codes 优先按 received_codes 判断 → 缺失代码不再命中覆盖缓存；
+- _meta 新增 requested_count / received_count / missing_count；
+- live 返回非空但全部行 degraded（如 source_time 未知）→ meta status="degraded"，不标 fresh。
+
+## DIFF-2 eastmoney_quote.py（P1 未知 source_time 降级）
+
+before：东财 legacy 行 source_time=None 且无 degraded/missing_fields，可被当作未降级。
+
+after：_parse_response 对每行计算 missing_fields（涨跌幅/成交量/成交额任一为 None），并强制 degraded=True（未知行情时间不得冒充新鲜）。
+
+## DIFF-3 common.py（P1 K线短窗不冒充长窗）
+
+before：get_kline_cached 命中条件只看 days——请求 130 根返回 30 根后，再请求 100 根会命中 30 根缓存。
+
+after：
+- 缓存项新增 rows（实际行数）与 failed 标记；
+- 命中条件加 entry.rows >= days（实际行数不足请求窗口则补取）；
+- failed 负缓存保持原语义（短 TTL 内返回 None 不重复轰炸）。
+
+## DIFF-4 overnight_arbitrage/service.py（P0 决策质量门控）
+
+4.1 评分前统一质量门控（新增 _quote_quality_block）：
+- 拦截：degraded=True 或 is_stale=True；source_time 不可解析 / 非当日 / 超 900s；
+- source_time 缺失的旧/合成行保持兼容（真实兜底行由 degraded 标记拦截）；
+- build_overnight_decision 在缺字段检查前先过质量门控，命中写入 removed[{"quality":...}]；
+- data_quality.status：存在 quality 拦截时 "partial"，否则 "complete"。
+
+4.2 完成时刻复核：run_overnight_arbitrage 按真实墙钟耗时（wall_started）>900s 时追加 blocked_reasons="task_exceeded_valid_window"，任务超时不得沿用启动时刻放行。
+
+4.3 yahoo5m 时间对齐：_fetch_yahoo_5m_strength 改为按 timestamp 组合整根 bar（丢弃缺字段样本），排序后校验：最后一根 ≤now-10min、最近窗口同一交易日、相邻间隔 ≤10min（排除午休/跨日拼接）；不满足则返回空（可选增强不得加分）。
+
+## DIFF-5 新增回归测试（8 反例）
+
+tests/test_decision_quality_gates.py 固化：
+1. 部分返回不得声称覆盖（received=1/missing=1）；
+2. 全部 degraded 行不标 fresh；
+3. 东财 legacy 未知时间 → degraded=True；
+4. K线短窗缓存不得服务长窗请求；
+5. 不同取数器不串缓存；
+6. is_stale+旧 source_time 不得产出 BUY、data_quality≠complete；
+7. degraded 兜底行不得产出 BUY、data_quality=partial；
+8. yahoo5m 极旧时间戳不得产生强度。
+
+---
+
+## 回归结果
+
+pytest tests/test_decision_quality_gates.py tests/test_source_contracts.py backend/services/data_backend/tests backend/plugins/overnight_arbitrage/tests backend/plugins/tech_indicators/tests backend/plugins/principal_capital/tests -q
+→ 139 passed, 1 warning
+
+## 未完成（后续批次）
+
+- §12.4 第 1 步剩余：候选精查贯通（§12.2 P1 末条）、_eastmoney_all_a_snapshot 分页上限与有效证券全集核对；
+- §12.4 第 2–5 步（覆盖与取数一致性 / 日K持久化 / 其余能力迁移 / 部署地影子运行）。
diff --git "a/docs/24_\345\275\261\345\255\220\350\277\220\350\241\214_\350\247\202\346\265\213\346\212\245\345\221\212\346\250\241\346\235\277\344\270\216\345\220\257\345\212\250\346\270\205\345\215\225.md" "b/docs/24_\345\275\261\345\255\220\350\277\220\350\241\214_\350\247\202\346\265\213\346\212\245\345\221\212\346\250\241\346\235\277\344\270\216\345\220\257\345\212\250\346\270\205\345\215\225.md"
new file mode 100644
index 0000000..40db53f
--- /dev/null
+++ "b/docs/24_\345\275\261\345\255\220\350\277\220\350\241\214_\350\247\202\346\265\213\346\212\245\345\221\212\346\250\241\346\235\277\344\270\216\345\220\257\345\212\250\346\270\205\345\215\225.md"
@@ -0,0 +1,59 @@
+# 24 方案 · 影子运行观测报告模板 + 启动清单（§12.4 第5步）
+
+> 用途：部署到 Render/GH 后，连续 5 个交易日采集观测；观测口径对齐 §9 验收。
+
+## 一、启动清单（部署前逐项勾选）
+
+| # | 项 | 要求 |
+|---|---|---|
+| 1 | 唯一盘中采集/决策所有者 | 明确由 Render cron、GH Actions、本地 daemon 哪一个负责实时采集；其余只读快照/补跑，避免多处重复采集 |
+| 2 | 环境变量 | SHADOW_RUN_ENABLED=1；TZ=Asia/Shanghai；数据源 env（如需）；ALLOW_STALE_QUOTE_CACHE 保持默认关 |
+| 3 | 交易日历 | 确认 data/trading_calendar.json 覆盖观测期（含节假日/调休），否则 degraded 会影响口径 |
+| 4 | 快照恢复 | restore 脚本拉回 data-snapshots；bar_store 表已由 init_db/ensure_schema 建表 |
+| 5 | 备份 | 开启 bars_store.backup 定期备份 + 恢复演练（§6.1） |
+| 6 | 采集预算 | 记录每轮总截止时间；日K历史补洞排到盘后 |
+| 7 | 观测范围 | 至少覆盖开盘(9:25-9:40)、午休(11:30-13:00)、尾盘(14:40-14:55) 三类时窗 |
+
+## 二、观测报告模板（连续 5 交易日）
+
+### 2.1 每日快照（第 N 交易日）
+
+| 指标 | 取值 | 说明 |
+|---|---|---|
+| 日期 | YYYY-MM-DD | |
+| 数据面 | quotes / zt_pool / index / daily_k / minute | |
+| 各源轮次 | shadow_run.summarize() 的 runs | 每源实际请求轮数 |
+| 错误率 | summarize().error_rate | 目标 0 放行错误 |
+| 行情年龄 P95 | summarize().age_p95_seconds | §9：关键行情年龄 ≤15s（暖缓存候选报价 p95≤5s 为初始目标） |
+| 截止时间达成率 | summarize().deadline_met_rate | 目标 100% |
+| 覆盖 | coverage.universe_total / received / missing_count | 全市场声明有有效分母；部分返回不得冒充完整 |
+| 错误放行 | 有效 BUY 中 stale/未知 source_time/degraded 数量 | 目标 0（§9 门控） |
+| 尾延迟 | 采集阶段 p50/p95 | 用部署地样本，不用本机数字 |
+
+### 2.2 汇总表（5 交易日）
+
+| 数据面@源 | runs | error_rate | age_p95 | deadline_met_rate | 主要错误类型 |
+|---|---|---|---|---|---|
+| quotes@sina | | | | | |
+| quotes@tencent | | | | | |
+| zt_pool@eastmoney | | | | | |
+| ... | | | | | |
+
+## 三、放行/回退条件
+
+- 放行：5 交易日无错误放行（stale/未知 source_time/degraded 不产生有效 BUY）、deadline 达成率 100%、覆盖分母可解释、备份恢复演练通过。
+- 回退：任一必需字段能力缺失时显式 unavailable（不可强行出结果）；观测指标不达标即回退该数据面，不缩小声明范围掩盖。
+
+## 四、本机可复现的观测聚合命令
+
+```bash
+SHADOW_RUN_ENABLED=1 python3 - <<'PY'
+from backend.services.data_backend.shadow_run import record, summarize, reset
+record("quotes","sina","ok",coverage={"received":5,"truncated":False},age_seconds=3.0,deadline_met=True)
+record("quotes","sina","ok",age_seconds=9.0,deadline_met=True)
+record("quotes","tencent","error",error="timeout",deadline_met=False)
+print(summarize())
+PY
+```
+
+> 注：真实观测在部署地开启 SHADOW_RUN_ENABLED=1 后自动写入 data/shadow_run.json；本机仅能验证聚合逻辑（tests/test_shadow_run.py）。
diff --git "a/docs/24_\346\225\260\346\215\256\346\272\220\345\216\273\344\270\234\350\264\242\345\214\226_\344\270\273\345\244\207\345\200\222\347\275\256.md" "b/docs/24_\346\225\260\346\215\256\346\272\220\345\216\273\344\270\234\350\264\242\345\214\226_\344\270\273\345\244\207\345\200\222\347\275\256.md"
index 79aab12..6ad890f 100644
--- "a/docs/24_\346\225\260\346\215\256\346\272\220\345\216\273\344\270\234\350\264\242\345\214\226_\344\270\273\345\244\207\345\200\222\347\275\256.md"
+++ "b/docs/24_\346\225\260\346\215\256\346\272\220\345\216\273\344\270\234\350\264\242\345\214\226_\344\270\273\345\244\207\345\200\222\347\275\256.md"
@@ -1,173 +1,366 @@
-# 24 数据源去东财化（主备倒置）— 施工级技术开发方案
+# 24 数据源去东财化：能力路由、分层采集与本地持久化
 
-> 状态：施工方案（待开发）。目标：**主要数据获取源不再以东财为主，东财降为末位备用（不删除）**。
-> 前置结论：已确认「东财会经常封禁」（§0），据此落地本方案。
+> 修订：2026-09-14，补充当前代码核验、离线反例和施工顺序调整；2026-09-11记录作为历史证据保留。
+> 状态：首阶段代码实施范围见§11；最新核验仍发现端到端质量门控缺口，调整决定见§12。本次仅更新方案，尚未实施§12修复；不得据现有回归通过直接切源上线。
+> 结论：降低东财依赖的方向正确，但仅把新浪移到第一位不是当前最优方案。默认采用“按能力选源、全市场粗筛后候选精查、已完成历史落盘复用、缺失能力显式阻断”的渐进方案。
+> 调用预算、容量和延迟均为设计目标或估算，不是已取得的生产性能。“免费”指不新增行情订阅费，不等于机器、存储、维护和数据使用授权成本全为零。
 
-## 0. 封禁确认结论（会经常封，证据三源）
+## 1. 审查结论：旧方案不能直接施工
 
-| 证据 | 内容 |
-|---|---|
-| 本项目源健康 | data/principal_capital_source_health.json：**eastmoney 连续失败 66 次、akshare（东财同源）33 次**，均熔断；sina 健康 |
-| 本项目今日实测 | 2026-09-10 14:13 生产机实测：东财 push2his 全接口（fflow 分钟资金流 + 历史K线）RemoteDisconnected；同机新浪 bulk/K线全部 200。12:05 东财曾成功 → 高频后封禁，属 IP 级限流 |
-| 社区长期共性 | akshare issue #6061「异步高并发导致东财大量封IP」；efinance discussion #216「破限流困境」；多篇「东财接口 IP 封禁分析与解决」——**高并发/高频下封禁是常态，不是偶发** |
+### 1.1 必须纠正的问题
+
+以下描述的是本轮修改前的代码与方案；已修复范围和剩余缺口以§11为准。
+
+| 优先级 | 发现与证据 | 影响及修订决定 |
+|---|---|---|
+| P0 | 2026-09-11 13:46 本机抽样：茅台新浪 bulk turnover=21.3098，腾讯换手率=0.21%；平安银行 32.3799 对 0.32%；招商银行 26.3803 对 0.26% | 撤销同名字段直接映射。约百倍差异会污染换手率门槛；bulk/100 是样本支持的待验证映射，不直接宣布全部标的单位已验收 |
+| P0 | 旧涨停脚本把普通主板涨9.8%当涨停；昨收1.03元、涨停价1.13元约涨9.7087%，反被漏掉 | 按有效日期规则、参考价、最小报价单位计算涨停价；拆开“曾触及”“当前涨停”“封板” |
+| P0 | ST 固定5%不再适用全部当前场景：上交所2026修订规则已将主板风险警示限制调整为10%，深交所也发布制度调整文件 | 按交易所、板块、状态、生效日期维护规则；历史回算不可套今日规则，不能只按名称固定4.8% |
+| P0 | 新浪验证脚本把 OHLC/升序检查固定输出通过，复权差异只打印；腾讯脚本按位置而非交易日对齐，复权失败未进入最终退出码 | 撤回 M0 完成结论，增加硬断言、跨除权日样本、失败退出码 |
+| P0 | common.get_kline_cached 仅按代码缓存，同日忽略 days、复权、源、刷新时点；已复现请求130根拿到先前30根，以及首次None后全天不重取 | 修窗口覆盖、缓存身份和失败TTL，再切换主备 |
+| P1 | 生产 sina_market.fetch_market_fund_flow_via_sina 仍逐股查询；bulk 只在验证脚本。腾讯资金流未接入全市场主链路 | 新增共享bulk适配器，不能只调序；腾讯逐票资金流不冒充全市场等价备源 |
+| P1 | 当前新浪 getKLineData 缺额/换手且未证明前复权；但 AKShare 的新浪 stock_zh_a_daily 另有 amount、流通股本、turnover 路径 | 验证新浪完整日线，避免过早接受永久缺字段。两个新浪端点不是独立故障域 |
+| P1 | “填None但绝不改消费者”不成立：空值可能被转零或影响硬门槛，插件也可能绕开统一入口 | 允许修改取数入口、单位兼容、必需字段校验与状态传递；不改因子公式、评分权重和阈值 |
+| P1 | overnight_arbitrage/sources/zt_pool.py 是独立复制实现；低位扫描器另有历史涨停回补；eastmoney_quote.py 已有新浪报价兜底 | 纠正现状盘点，纳入迁移范围；只改主项目涨停池不能完成全链路迁移 |
+| P1 | “东财故障时全链路不空态”会奖励不完整甚至错误的数据 | 区分服务可用与信号有效；关键能力缺失应允许明确空仓/不可判定 |
+
+规则依据：[上交所现行交易规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)、[上交所修订说明](https://star.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml)、[深交所风险警示板业务指南通知](https://www.szse.cn/lawrules/service/member/t20260630_621404.html)。具体生效边界和例外须转换成测试夹具后启用。
+
+新浪完整日线依据：[AKShare接口文档](https://akshare.akfamily.xyz/data/stock/stock.html)、[新浪适配器源码](https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_zh_a_sina.py)。其中 turnover 由成交股数除以流通股本计算，为比例值，转百分数须乘100；不能与 bulk 的同名字段共用转换函数。完整日线路径有额外请求和解码成本，尚未完成部署地性能验证。
 
-结论：**东财作主力数据源不可持续；新浪（已实测稳定）+ 腾讯 + pytdx 应为一级源，东财只作末位备用。**
+### 1.2 东财故障证据要降到它实际支持的程度
 
-### 0.1 免费 tushare（120 积分）够用性结论（2026-09-10 核实官方权限表）
+本机读取的 data/principal_capital_source_health.json 更新时间为2026-09-10 15:16:11+08:00：eastmoney连续失败67次、akshare34次、sina4次。它是历史状态，不是今天新浪健康的证明。
 
-| 接口 | 积分门槛 | 对本项目价值 |
+旧方案记录的 RemoteDisconnected 支持“东财链路在该环境不可靠”，但单凭断连和累计失败，不能确定永久封IP，也不能排除网络、代理、端点或服务端异常。降低东财主依赖应依据成功率、覆盖率和尾延迟，不必以“已证实永久封禁”为前提；push2his失败也不等于涨停池端点不可用。
+
+### 1.3 Tushare 权限纠正
+
+| 能力 | 本次官方核对 | 定位 |
 |---|---|---|
-| daily 日线行情 | **120 起 ✅ 可用** | 与 sina 日K/东财重复，仅可作第三兜底（未复权，复权需 pro_bar 2000） |
-| stock_basic 股票列表 | 120 ✅ | 与 sina 主板清单重复 |
-| A股 trade_cal 交易日历 | 120 ✅ | 与 14 方案 akshare 日历重复，可作交叉校验 |
-| **stk_limit 每日涨跌停价格** | **2000 起 ❌** | 涨停池主源需求，免费不够 |
-| **limit_list_d 涨跌停统计（封板/炸板/连板）** | **已停维护 ❌** | 原 24 方案假设的主源，不可用 |
-| moneyflow 资金流 / daily_basic 每日指标 / pro_bar 复权 / index_daily | 2000 ❌ | 均超免费档 |
+| 120积分 | 总权限表列非复权日线，50次/分钟、每天8000次 | 已有账号且权限确认后可补收盘历史，不进14:43实时关键路径 |
+| 股票列表、日历 | 不能继续宣称120积分必然全部可用；总表与接口页需结合实际账号核对 | 无权限或未知时关闭，不反复尝试 |
+| stk_limit | 权限表列2000积分起 | 不作为零订阅默认能力 |
+| limit_list_d | 官方仍列接口，2020年起历史，5000积分门槛，不统计ST | 撤回“已停维护”和“升2000即可解决涨停池”；日级历史也不能自动等同盘中实时 |
+| 分钟权限 | 通用积分不包括分钟权限 | 不假定购买积分就能解决实时分钟需求 |
 
-**结论：免费 tushare 不够用**——它够用的（股票列表/日历/日线）恰是项目已有替代的冗余项；真正缺的涨停池字段（stk_limit/limit_list_d）恰恰要 2000 积分或已停维护。故 24 方案**不引入 tushare 作主源**（除非升级 2000 积分），涨停池改走 sina 计算兜底 + 东财全字段备用。
+依据：[总权限表](https://tushare.pro/document/1?doc_id=290)、[接口权限列表](https://tushare.pro/document/1?doc_id=108)、[limit_list_d接口](https://tushare.pro/document/2?doc_id=298)。本次未调用账号验证授权。
 
-### 0.2 免费源矩阵（grill 2026-09-10 实测；新浪不是唯一，但各源能力不同）
+## 2. 四维取舍
 
-| 数据面 | 实测可用源 | 备注 |
+| 维度 | 原方案的不足 | 本版默认选择 |
 |---|---|---|
-| 资金流分类 | **新浪 bulk ✅（1 请求全市场，主）**；腾讯 per-stock ✅（无批量，3000 逐票）；同花顺资金流 HTML ✅（反爬需解析） | 交易所无「主力/超大/大/中/小」分类——这是行情商加工字段 |
-| 实时行情 | **腾讯批量报价 ✅**（含换手率/市值）；**新浪 bulk ✅**；网易 diyrank ❌(502) | 东财 ❌封禁 |
-| 历史日K | **腾讯 fqkline ✅（前复权，缺 amount/换手）**；**新浪 ✅（前复权，缺 amount/换手）**；pytdx ✅（不复权，OHLCV+amount） | 东财 ❌封禁 |
-| 涨停池全字段(封板/炸板/连板) | 东财 zt_pool（封禁中备用）；同花顺涨停复盘（待验）；**sina_calc 仅代码清单** | 腾讯/网易/pytdx/交易所均无此字段 |
-| 券商(华泰/华宝) | 无面向个人的公开全市场资金流 API；仅 QMT/PTrade（已否） | — |
-| 交易所官网 | 报表级（股票列表/成交概况），无逐票资金流分类 | 上交所 query.sse / 深交所 api 有 Referer 限制 |
+| 免费成本 | 只计算订阅费，忽略逐股重试、算力、存储和维护 | 复用现有requests/httpx、SQLite、pytdx；不新增付费行情、Redis或消息队列；减少请求优先于加并发 |
+| 查询颗粒度 | 混淆全市场横截面、逐股历史、盘口事件 | 全市场低频粗筛、候选批量精查、已完成日K复用、仅跟踪池取分钟/盘口 |
+| 数据存储 | 进程当日缓存与JSON快照不能支撑历史复用和审计 | 单机SQLite存标准化历史与元数据，JSON作最新读模型，原始样本限量保留 |
+| 性能 | 全市场逐票切备放大请求；多层重试叠加 | 统一采集、共享结果、同请求合并、限速和总截止时间，按字段补缺 |
+
+先使用现有运行机与持久磁盘，单采集进程起步。国内长期在线主机有利于验证现有TDX链路，但必须在部署地测量；本机成功不能推导Render Oregon或GitHub runner成功。没有已有长期在线、持久化主机时，不能承诺零新增账单下的全天稳定服务。
+
+## 3. 按能力路由，不用一张统一主备表
+
+| 数据面 | 首选路径 | 备用/补充 | 缺失边界 |
+|---|---|---|---|
+| 全市场粗筛横截面 | 经准入的横截面源；新浪bulk为未准入候选，单位、覆盖、时间及使用边界验收后启用 | 腾讯分批行情，东财对应端点末位补缺 | 标明未覆盖代码，不把部分榜单当完整市场 |
+| 候选实时行情 | 腾讯批量，检查源时间、昨收、量额、换手、市值及所需PE/量比 | 新浪标准报价补基础价量；国内TDX经验证补盘口；东财补剩余能力 | 缺必需字段的候选不得输出有效BUY |
+| 已完成日K | 本地历史库，未命中才访问上游 | 优先验证新浪完整日线补齐；腾讯qfq服务仅需复权OHLCV的消费者；东财同能力末位备用 | 不把未知复权/缺必需字段bar标完整 |
+| 未复权日K、分钟价量额 | 国内部署优先验证现有pytdx协议适配器 | 经校准的新浪/腾讯相同周期端点，东财末位备用 | 快照差分不冒充真实分钟成交分布 |
+| 前复权序列 | 已验证复权序列，或原始日K加已验证因子/除权事件 | 腾讯qfq与新浪完整日线核对 | 没有复权链不能用raw冒充qfq |
+| 全市场资金流 | 保持现有正式资金源优先级；新浪bulk缺r1/r2及源时间证据，暂不准入 | 后续同能力新源验收后再调整；候选逐票取数不能替代全市场覆盖 | 腾讯逐票仅服务候选，不能冒充全市场排名 |
+| 候选资金流 | 当前批次共享切片 | 腾讯逐票须先验证字段解析和分类口径 | 不跨商混算净流入增量 |
+| 当前涨停状态 | 新鲜报价加有效规则/可靠涨停价 | 第二报价源复核，东财池增强 | 基础状态不虚构首封/炸板 |
+| 封板事件、封单、连板 | 完整事件源独立取数，当前保留东财对应能力 | 自建观测仅记首次观测/观测开板；同花顺真实榜单待验 | 无精确事件时阻断依赖策略，普通行情策略可继续 |
+| 指数 | 新浪/腾讯对应指数报价 | 东财末位备用；历史指数用独立历史能力 | 不套个股symbol路由规则 |
+| 国家队等低频旁路 | 保留已有实现与持久缓存 | 无等价免费源时显式unavailable | 不阻塞无此依赖的主链路 |
+
+“东财末位”针对同能力来源；某字段没有验收通过的非东财源，就承认仍有东财依赖，不为了顺序好看永久牺牲必需字段。
+
+新浪 getKLineData(scale=240) 暂不标前复权能力；周期参数不是复权证明。腾讯 qfqday 缺失也不能悄悄拿 day 当qfq。AKShare是适配库，不是独立数据商：stock_zh_a_hist属于东财家族，stock_zh_a_daily属于新浪家族；健康和单位按实际端点管理。
+
+TDX包含证券/指数K线、分时、成交及除权除息查询，K线每请求最多800根；需分页查断档，不假设服务器无限留存。[Pytdx标准行情文档](https://pytdx-docs.readthedocs.io/zh-cn/latest/pytdx_hq/)
+
+### 3.1 暂不引入的选择
+
+- Baostock：保留为收盘历史补齐调研候选；本轮官网未取得可审阅接口正文，不宣称实时能力、字段覆盖或更新时间已验证，不新增依赖。
+- 同花顺：旧脚本请求 /market/longhu/，HTTP 200不能证明拿到涨停复盘。必须验证真实榜单、字段、交易日及解析。
+- Redis/时序库：当前单机、少量写者的规模先不用；实测出现跨主机写入或查询瓶颈再比较。
+- DuckDB/Parquet：以后全量回测和压缩归档可选；现有依赖没有明确Parquet引擎，不能把to_parquet当零依赖保证。
+
+## 4. 数据契约先于主备切换
+
+### 4.1 标准字段
+
+| 类别 | 最小契约 |
+|---|---|
+| 身份 | symbol、exchange、instrument_type、trade_date；避免000001在股票与指数间冲突 |
+| 时间 | source_time、fetched_at、observation_start/end；缺源时间时为null，采集时间不能冒充行情时间 |
+| 数值 | 价格元、成交量股、成交额元、市值元、涨跌幅和换手率百分数（1.25表示1.25%） |
+| 日K | period、adjustment、adjustment_version、is_final；真实成交股数和金额不随价格复权机械缩放 |
+| 来源 | provider_family、endpoint、schema_version、field_sources；混合补字段保留字段血缘 |
+| 质量 | required_fields、missing_fields、coverage、stale、degraded_reasons；区分零值、缺失、未上市、停牌 |
+| 决策 | snapshot_id、strategy_version、rule_version、observed_at；保留当时可见数据，避免回测前视 |
 
-**结论：24 方案从「sina 单一主源」升级为「免费多源矩阵」**——每个数据面至少 2 个非东财源，东财末位备用。
+内部用“股”不等于立即改变所有消费接口；例如已有VWAP路径乘100，兼容层须按原约定转换并回归。百分比按端点固定契约转换，禁止按“数值大于1”猜单位。资金流分类不能因为名字相同就视为同口径。
 
-## 1. 现状盘点（东财依赖入口 × 现有兜底 × 今日实测）
+### 4.2 可计算与不可恢复的字段
 
-| 数据面 | 入口文件 | 现优先级 | 今日实测 | 新浪/其它源是否已有 |
-|---|---|---|---|---|
-| 资金流 | principal_capital/sources/multi_source.py | eastmoney → akshare → (sina/tencent) | eastmoney/akshare 封禁，sina 健康 | ✅ sina.py + sina_market.py + tencent.py 已存在 |
-| 历史日K | layer1_data_collector/sources/historical_kline.py | eastmoney → akshare → **sina** | 东财 None；**sina 30 行可用**（末根 09-09） | ✅ _fetch_hist_sina 已存在（字段缺口见 §4.1） |
-| 涨停池 | layer1_data_collector/sources/eastmoney_zt.py | eastmoney → akshare(东财同源) | 未单测（东财封禁中） | ⚠️ 免费 tushare 不够：stk_limit 需 2000 积分、limit_list_d 已停维护（见 §0.1）；sina 无现成 zt_pool 专有字段 |
-| 行情快照 | layer1_data_collector/sources/eastmoney_quote.py | eastmoney | 未单测 | ✅ sina bulk ssggzj 已实测含 trade/changeratio/amount |
-| 指数 | layer1_data_collector/sources/index_data.py | eastmoney | 未单测 | ⚠️ sina 指数接口需验证 |
-| 国家队(旁路) | layer1_data_collector/sources/national_team.py | eastmoney 唯一 | 低频 | 保留东财，标 non-blocking |
+- 振幅可在参考昨收正确且口径一致时计算，不一概视为缺失。
+- 相邻一致口径收盘可算收益率，但交易所当日涨跌幅要用正确参考价，除权日不能用未经修正昨日收盘代替。
+- 历史换手率可按当日有效流通股本计算并标derived；不拿今天股本回填全部历史。
+- 成交额不能由收盘价乘成交量还原；优先取TDX或新浪完整日线的真实金额。
+- 轮询不能准确恢复首次封板、全部炸板次数和漏采期间事件，只能记观测值、采样周期及不确定区间。
+- 跨供应商资金流切换时开新段，不直接相减生成分钟流量；来源的分类阈值也需记录。
+- 必需字段缺失时阻断对应信号；可选展示字段可以为空。不得静默跳过硬门槛、重分配权重后仍声称原策略不变。
 
-## 2. 目标与原则
+### 4.3 涨停基础状态与增强事件分离
 
-1. **主备倒置**：每个数据面的一级源改为新浪/腾讯/pytdx/tushare；东财一律放**末位备用**（不删除，作为 cross-check 与极端兜底）。
-2. **复用不重造**：复用 multi_source 的 health/熔断范式，扩展到 K线/涨停池/行情/指数四个面。
-3. **铁律**：不改各消费插件（18/19/20/21/16/01/05 等）的核心逻辑；只在数据源层追加/调序；降级在快照显式标 active_source/degraded。
-4. **字段缺口显式化**：sina 缺的字段（成交额/振幅/换手率/封板时间等）**填 None 并标 degraded，绝不伪造**；对依赖这些字段的功能（16 换手率、10 封单）明确降级行为。
+1. 识别交易所、板块、证券类型和当日状态；基金、债券、无涨跌幅限制期股票不进入普通规则分支。
+2. 优先取已核验当日涨停价，否则按有效参考价、限制比例、报价单位和舍入规则，用Decimal/整数价格计算；规则或参考价缺失则unknown。
+3. 分开记录曾触及涨停、当前价在涨停、盘口支持封板、收盘确认涨停。
+4. zt_basic先返回基础状态，zt_enrichment在独立预算内查询首封/封单/开板，成功即合并。不能因基础源成功就永远不触发增强源。
+5. 收盘确认状态逐日落库，可按明确规则计算连续收盘涨停天数；与供应商连板定义先对齐，盘中不能提前写成收盘终态。
+6. 依赖精确封板字段的策略缺数据时输出unavailable_required_fields，不填0，不当正常结果。
 
-## 3. 受控改造清单（分 P0/P1/P2，git diff 只允许以下范围）
+## 5. 查询颗粒度、请求预算与性能
 
-### P0（立即 · 已具备条件，改动最小）
+### 5.1 按阶段采集
 
-| # | 文件 | 改动 |
+| 任务 | 初始范围和节奏 | 复用 |
 |---|---|---|
-| 1 | historical_kline.py | 优先级改为 **sina → tencent(fqkline) → eastmoney → akshare**；akshare 移末位（东财同源，封禁时同样死）；新增 tencent 日K源 |
-| 2 | principal_capital/sources/multi_source.py | 优先级 **sina → tencent → eastmoney → akshare**；更新 docstring 与源健康表注释 |
-| 3 | principal_capital/sources/eastmoney.py | 保留但标记为「末位备用」，不改逻辑 |
+| 主数据/交易规则 | 盘前增量，有变化更新，低频全量核查 | 保留有效日期和规则版本 |
+| 全市场粗筛 | 交易时段每30–60秒，按实际需求启用 | bulk响应解析一次、多策略切片 |
+| 候选精查 | 初始上限200只，关键窗口5–15秒，其他时间放缓 | 腾讯初始每批60只，属于待压测参数而非官方额度 |
+| 跟踪池盘口 | 初始20–50只，TDX每2–5秒试验节奏 | 独立连接，能力不足降低观测等级 |
+| 已完成日K | 收盘确认后更新缺失日期及少量重叠区间 | 初始留250–500交易日，以最长策略窗口为准 |
+| 当日未完成日K | 短TTL临时层，按需刷新 | 按交易日替换同一根，不追加重复日K |
+| 分钟K | 仅候选/跟踪池，周期完成后增量 | 初始1分钟留20交易日，更多历史另评估 |
 
-### P1（中期 · 需先验证再落地）
+候选上限是计算预算，不是任取前200行就宣布全市场筛选完成；记录筛选排序、截断与未覆盖数量。需要全市场历史因子的策略，先在本地历史库全量计算，再应用候选预算。
 
-| # | 文件 | 改动 |
+### 5.2 请求量估算
+
+假设主板N=3000、候选K=200、批量B=60：
+
+- 全市场逐票资金流3000次/轮；覆盖和语义满足的bulk为1次，理论请求减少约99.97%，不代表端到端加速同倍数。
+- 腾讯全市场ceil(N/B)=50批；候选ceil(K/B)=4批，减少92%。
+- 若按bulk的6431行全部查询，则需108批，不能继续写“全市场约50批”；这些行也未必全是策略范围内股票。
+- 日K只补缺失股票和日期，减少返回行数；若端点仍逐股，更新N只股票仍需N次请求，不能把增量返回误称单请求全市场更新。
+
+### 5.3 运行控制
+
+- 每供应商共享令牌桶，初始HTTP并发2–4、每秒1–2请求，仅为保守启动值；按失败率及p95调整。并发上限不是QPS上限。
+- 关键轮次设总截止时间，预热后读快照；连接/读取timeout受剩余预算约束，socket读取timeout不等于完整任务墙钟上限。
+- 每端点一轮至多一次受预算约束的重试，不叠加requests、curl、AKShare和插件各自多轮重试。
+- 同一个同步TDX socket不跨线程并发共享；用独占连接或锁，需要并发则小连接池。
+- ThreadPoolExecutor上下文退出会等任务结束，asyncio.wait_for(to_thread(...))也不会强杀线程；必须有底层timeout、有界提交队列和取消策略。
+- 实时报价与历史回补分开排队，盘中实时优先，历史补洞退到盘后。
+
+## 6. 存储设计：先用好SQLite
+
+### 6.1 默认结构
+
+复用现有数据库和 backend/services/data_backend，以下为逻辑表建议，最终字段需兼容现有表；不新建平行数据平台。
+
+| 表/产物 | 身份或查询键 | 保存内容 |
 |---|---|---|
-| 4 | 新建 sources/quote_multi.py（sina + tencent） | 全市场实时行情：sina bulk（主，1 请求）+ 腾讯批量报价（备，含换手率/市值）→ 替代 eastmoney_quote 作 quotes/index_snapshot 主源 |
-| 5 | data_backend/snapshots.py + refresh 入口 | quotes/index_snapshot 读源切 sina_quote（东财备用） |
-| 6 | eastmoney_zt.py | 涨停池二级（免费 tushare 不够，已改）：**sina 涨停计算兜底升为主（仅代码清单，无封板/炸板/连板，标 degraded）→ 东财 zt_pool 全字段备用** |
-| 7 | index_data.py | 加 sina 指数源（上证/深成/创业板），东财备用 |
+| instruments / trading_rules | 证券标识、有效起始日 | 主数据、状态、规则版本及有效区间 |
+| bars_daily_raw | 证券、日期、来源端点、schema版本 | 未复权OHLC、真实量额、终态与采集时间；多源不无痕互相覆盖 |
+| adjustments | 证券、生效日、来源、版本 | 因子/事件、已知时间、锚点；变化使受影响派生区间失效 |
+| bars_adjusted | 证券、日期、复权类型、版本、来源 | 验证后的供应商序列或派生缓存，不与raw混键 |
+| bars_intraday | 证券、周期、bar结束时间、来源 | 跟踪池有限分钟历史 |
+| limit_events | 证券、交易日、事件来源、事件/观测时间 | 区分真实事件和采样观测，保存精度及缺口 |
+| source_health | 环境、家族、端点、数据面 | 可用性、质量、延迟、熔断；不跨机器复制封禁判断 |
+| decision_inputs | 决策批次、证券 | 当时数据及版本，不用事后修订覆盖原输入 |
+| 最新JSON | 资产名、版本/批次 | 当前与上一版读模型，不充当全量历史数据库 |
+
+SQLite采用WAL、短事务、单写队列、批量upsert、busy_timeout和查询索引。WAL限同主机，不把文件放网络共享目录让多机并发写。[SQLite WAL说明](https://www.sqlite.org/wal.html)
+
+备份用在线备份API或停止写入后的完整一致性副本；不能WAL活跃时仅复制.db就视为完整备份。定期恢复演练，不把缓存可重建等同历史决策可恢复。
+
+### 6.2 缓存与快照发布
+
+- 缓存身份包括证券、周期、复权类型和版本、来源/字段能力，记录实际覆盖区间。长窗可裁短窗，短窗不能冒充长窗。
+- 已完成历史按交易日复用，未完成当日bar短TTL；None仅短负缓存（初始3–10秒），不保留全天。
+- 同键在途请求合并；跨进程靠单采集服务或有过期租约的数据库任务协调，Python字典不跨进程共享。
+- 区分source_time与fetched_at：重新下载旧快照仍旧，午休/盘后也不能把所有同日数据视为新鲜。
+- snapshots.py当前直接双写文件，应改为唯一临时文件、原子替换、版本号、清单最后提交；两个文件分别rename不等于多文件事务，读者校验批次并保留上一版。
+- common._atomic_write的固定.tmp名字也需防多写者冲突；优先单写者，必要时唯一临时文件与锁。
+
+### 6.3 容量预算
+
+按N=3000、250交易日、每条标准化记录约160字节估算，不含Python对象、索引、来源副本、WAL与备份；实际按数据库页和样本测量。
+
+| 数据 | 行数估算 | 原始记录量 | 决定 |
+|---|---:|---:|---|
+| 全市场日K一年 | 750,000 | 约120MB | SQLite起步合理 |
+| 200只候选1分钟、20日，每天约240根 | 960,000 | 约154MB | 有限留存、过期清理 |
+| 全市场1分钟一年 | 180,000,000 | 约28.8GB | 当前方案不采集 |
+| 全市场5秒快照一年，每天4小时 | 2,160,000,000 | 约345.6GB | 不做全量长期保存 |
+
+真实磁盘给索引、WAL及备份留余量；这不是上线容量保证。原始HTTP只留少量样本、异常和关键批次；常规快照短期留存，日K及必要决策输入按策略回溯期保留。
+
+### 6.4 Render、GitHub与零成本边界
+
+仓库同时有Render Cron、GitHub Actions和本地运行入口，配置存在不代表都启用。上线前明确唯一盘中采集/决策所有者，其余读取快照、补跑或离线运行，避免多处重复采集。
+
+Render免费Web无持久磁盘、免费Postgres有30天到期限制；Cron不能挂载持久磁盘，每服务每月至少1美元。若当前两个Cron均启用，仅其最低费用合计就至少2美元/月，其他费用另计，不能称永久免费持久化。[免费服务限制](https://render.com/docs/free)、[Cron限制与计费](https://render.com/docs/cronjobs)
+
+现有data-snapshots继续传精简低频结果，不用于每轮全市场、分钟历史或持续膨胀的SQLite同步；删除当前文件不清除Git历史体积，历史清理另案处理。
 
-### P2（低频旁路 · 可选）
+GitHub定时任务可能延迟；隔夜套利workflow已经使用--dry-run并注明不发正式决策邮件，保留此边界，不晋升为精确14:43唯一执行器。[GitHub定时限制](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows)
 
-| # | 文件 | 改动 |
+## 7. 健康路由和降级
+
+1. 先满足字段、复权、日期、覆盖和新鲜度，再按健康及成本选源；HTTP200不是成功标准。
+2. 连接错误、429、解析变化、字段缺失、陈旧数据、合法零结果分别分类；零涨停可合法，不能一律熔断。
+3. 按环境/端点熔断，有充分共同故障证据才升级家族熔断；东财直连与相同上游的AKShare共享预算，同源包装不用于重复轰炸。
+4. 初始连续失败3次、约1分钟起退避、最多30分钟，加抖动，半开只放一个探测；这些可配置，旧JSON健康实现不是已完成统一平台。
+5. 覆盖分母取盘前有效证券集合，去重，区分未知缺失和已知停牌；前500行有字段不证明全市场完整。
+6. 全市场流失败但候选流可用时标scope=candidates，不继续声称全市场资金排名。
+7. 新鲜度未知、必需字段缺失、总预算耗尽分别给可解释状态；旧数据可展示，不作为有效实时BUY输入。
+
+## 8. 实施顺序
+
+下列保留原里程碑编号以追踪范围，不再代表严格串行顺序；实际完成范围见§11，最新执行顺序与放行条件以§12为准。先完成决策质量门控，提前实施M2元数据贯通及M4必需能力阻断，再推进M3历史持久化。因子公式、评分权重、交易阈值不变；错误数据被阻断可能使候选减少，这是正确性修复。
+
+| 阶段 | 范围 | 交付与放行条件 |
 |---|---|---|
-| 8 | national_team.py | 保留东财唯一源，调用包装标 active_source=eastmoney + 失败静默降级（不阻断主链路） |
-| 9 | tushare_source.py | 提升为一级可选源（token 由 env 注入），供涨停池/财务交叉校验 |
-
-## 4. 关键源接口规格
-
-### 4.1 新浪日K（已实测可用，字段缺口明确）
-- 端点：`http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sh600519&scale=240&ma=no&datalen=N`（现 _fetch_hist_sina 已实现）。
-- 已提供：日期/开/高/低/收/成交量；涨跌幅由代码 prev_close 重算。
-- **缺口**：成交额/振幅/换手率 = 0.0（sina 接口不返回）→ 解析为 None 并标 degraded：
-  - 18/19/20（只用 OHLCV+涨跌幅）→ 不受影响；
-  - 16 low_position（用 turnover）→ 换手率 None 时该因子跳过并标 degraded，不误判；
-  - 17 副图成交额 → 标 None，前端不画假柱。
-- 复权：sina scale=240 为前复权（需 M0 口径对照确认，与东财 fqt=1 对齐）。
-
-### 4.2 新浪全市场实时行情（替代 eastmoney_quote）
-- 端点：已验证 `vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_ssggzj?num=8000`（6430 行/1s）：trade/changeratio/turnover/amount + 资金流字段。
-- quotes 快照字段映射：price=trade、change_pct=changeratio、turnover=turnover、amount=amount。
-- 指数：sina 指数接口（M0 验证 symbol=sh000001 等）。
-
-### 4.3 腾讯资金流（已存在 tencent.py）
-- 一级备选，位于 eastmoney 之前；M0 验证其字段与成功率（与 sina 并列多源）。
-
-### 4.4 涨停池二级（免费 tushare 不够，已去掉 tushare 主源）
-| 级 | 源 | 字段完整度 | 定位 |
+| M0：修验证 | 四个scripts/verify_*源脚本及离线契约测试 | 所有关键断言进入退出码；单位、代码覆盖、日期对齐、除权、规则例外、空池通过 |
+| M1：最小安全改造 | common.py、historical_kline.py、principal_capital/sources | 修窗口/失败缓存，共享bulk，同源预算；字段契约就绪后按数据面启用主备 |
+| M2：精查/快照 | 新报价适配器、services/data_backend/{snapshots,registry,read_model}.py及刷新入口 | 腾讯精查、真实来源/时间/缺字段元数据贯通、原子发布与兼容单位 |
+| M3：历史持久化 | 数据库迁移、历史服务、K线消费者入口 | 增量日K、按需补洞、复权版本失效、备份恢复；保留旧库，迁移可回滚 |
+| M4：涨停拆分 | 主涨停池、overnight_arbitrage独立涨停池、low_position_scanner历史入口 | 基础状态与增强事件分离、同步独立入口、必需字段缺失显式阻断 |
+| M5：部署地影子运行 | 配置、观测、回归报告 | 5交易日覆盖开盘/午休/尾盘、源故障与恢复，不以新浪占比95%衡量质量 |
+
+需确认实际消费者，不能只按“01/10/16”等编号假定不受影响。common.read_code_kline当前直接拉历史并只返回OHLCV，不返回成交额；要显示成交额需先扩对应契约。
+
+配置分QUOTE_COARSE_SOURCES、QUOTE_DETAIL_SOURCES、DAILY_QFQ_SOURCES、DAILY_RAW_SOURCES、FUND_FLOW_MARKET_SOURCES、ZT_ENRICHMENT_SOURCES。未通过M0的源保持关闭，不预置不存在的适配器为可用主源。
+
+## 9. 验收要求与本轮结果
+
+### 9.1 后续上线硬门槛
+
+- 单位、身份、复权不允许自动猜测；跨除权日按日期对齐，价格容差考虑供应商舍入，量额独立检查。
+- 涨停夹具覆盖普通主板、历史/当前ST、创业板/科创板、新股例外、低价舍入、停牌、指数误入、触及后开板、合法空池；缺规则不输出确认涨停。
+- 覆盖必需字段缺失、旧行情重下载、同日旧快照、首抓失败、短窗后长窗、并发同键、线程超时残留任务。
+- 有效BUY必需字段完整率100%，错误单位/未知复权放行数0；这是门控指标，不是收益或行情服务可用率保证。
+- 全市场任务给覆盖分母、缺失代码和原因；每个待输出BUY候选独立完成字段及时效验证。
+- 初始目标：暖缓存候选报价批次p95≤5秒、关键行情年龄≤15秒；用部署地完整样本测量，源时间不可验证则不标达标。
+- 记录请求数、每源重试、实际QPS、缓存命中率、扫描p50/p95、磁盘增长、错误类型；撤掉“某源占比越高越好”指标。
+- 东财断开后有充分数据的策略继续；依赖独有事件的策略明确不可用。允许可解释空仓，不用非空结果掩盖缺口。
+
+### 9.2 本轮已做与未做
+
+本节保留13:46方案审查时点的记录；随后的代码修改和新测试结果见§11。
+
+| 验证内容 | 验证时间 | 验证位置 | 验证结果 | 验证范围 | 验证通过率 | 验证失败范围 | 失败原因记录 |
+|---|---|---|---|---|---|---|---|
+| 入口、缓存、脚本静态审查 | 2026-09-11北京时间 | 本地项目 | 原方案需修订 | 本文关键入口及部署配置 | 不适用，非全仓测试 | 原M0证明不足 | 固定通过、差异未进退出码、遗漏旁路 |
+| 缓存与涨停反例 | 2026-09-11 13:46北京时间 | 本地Python，假取数器与纯判定函数 | 4/4缺陷复现成功 | 短窗污染、失败缓存、阈值误报、低价漏报 | 缺陷复现100%；生产验收不通过 | 四个现有行为 | 缓存键/TTL不足、近似阈值 |
+| 新浪/腾讯低频抽样 | 2026-09-11 13:46:28起北京时间 | 本机，各1次请求 | 两端返回，三票价格一致，换手率原值约差100倍 | bulk6431行；600519、000001、600036 | 返回2/2；完整契约未验收 | 换手率直接映射 | 单位不同；bulk所抽date/time为空，完整时间字段待核查 |
+| 权限、平台和规则核对 | 2026-09-11北京时间 | 本文官方链接 | 修正Tushare、Render及风险警示表述 | 文档层面 | 不适用 | 账号授权未实测 | 未调用账户接口、未查看账单 |
+| 新适配器/整链回归/压力恢复测试 | 未执行 | 待本机及部署地 | 待M0–M5 | 新架构、长期可用性 | 未执行 | 所有运行时改造 | 本次仅修改方案 |
+
+本机Python3.9/LibreSSL与urllib3 v2出现兼容性警告，两次请求均成功；后续测试环境应对齐部署用Python/OpenSSL，避免环境差异干扰归因。
+
+## 10. 默认决策和升级条件
+
+当前是适合项目约束的工程取舍，不宣称任何条件下绝对最优：
+
+1. 先修端到端质量门控与共享采集，再按能力切主备；腾讯候选精查须贯通策略入口，完成历史逐步转本地读取。新浪bulk仅为未准入粗筛候选，不预设为正式资金主源。
+2. 不默认放弃历史成交额/换手率，验证新浪完整日线及已有TDX路径；复权或事件精度不足时承认边界。
+3. 单机SQLite起步、有限分钟留存；已有主机减少新增账单，但需承担在线与恢复责任。
+4. 东财保留低频独有字段增强；全免费条件下不承诺同时获得可靠全市场实时流、精确封板事件、无限分钟历史及生产级可用性。
+5. 出现多机并发写、长期全市场分钟回测、严格实时SLA或高精度事件刚需时，再比较集中式数据库、列式归档和权限明确的行情服务。
+
+首阶段完成后先按§12补齐质量门控和M0独立证据，再推进历史持久化与完整能力迁移，不立即将sina放到所有源列表首位。
+
+## 11. 2026-09-11 首阶段代码实施记录
+
+### 11.1 已落地
+
+- K线共享缓存：按证券及取数器隔离，长窗口可裁短窗口、短窗口触发补取；同键并发合并、返回副本、成功TTL45秒、失败TTL5秒。清缓存期间旧请求不重新污染缓存，详情入口复用同一缓存。
+- 腾讯候选报价：现有fetch_stock_quotes入口默认腾讯批量优先，每批60只；校验代码、必需字段、有限数值与行情时间，缺失代码才进入兼容后备。保留旧中文接口的成交量“手”单位，新浪基础报价同步由股换算为手；返回逐行来源和覆盖元数据。
+- 报价故障预算：兼容后备每端点仅一次，移除requests/curl多轮同源重试；全源不可用不递归拆批放大请求。显式启用旧缓存时标stale/degraded，不将旧报价伪装为新鲜数据。
+- 历史K线：东财仍为当前首源；删除AKShare重复调用相同东财端点的后备，改为明确qfqday的腾讯后备。新浪轻量日K只供核验，标adjustment=unknown，缺额/换手保持空值，不再进入前复权消费路径。
+- 新浪bulk：新增端点专用报价标准化，换手率原值除100转换成百分数，涨跌比例乘100；不输出未经证明的资金分类，不进入正式资金策略。20只高换手样本已交叉支持单位映射，但不是全市场准入证明。
+- 四个验证脚本：所有硬检查进入退出码；0代表全部通过、1代表失败、2代表证据未完成。日K按交易日对齐比较并要求足够重合区间；复权独立对照和除权证据缺失不再报PASS。
+- 涨停验证：移除9.8%/4.8%近似阈值，以新鲜报价及有效涨停价作基础状态抽样。未知涨停价返回不可判定，零命中不算源故障；不声称获得封板事件或全市场涨停池。
+
+### 11.2 新证据与尚未启用的部分
+
+接入时确认新浪bulk样本有r0/r3，但没有现有逐股资金策略使用的r1/r2分类，也未找到明确行情时间。本方案§3中bulk资金主源只保留为候选目标，不能用它直接替换当前主力资金算法；正式资金源优先级暂不改。
+
+腾讯返回qfqday只是端点身份保证。本轮东财独立对照不可用，也未提供逐标的已核实除权事件，故腾讯K线只作为显式降级后备，不宣布“跨除权一致性已验收”或晋升全部历史主源。使用者可通过DataFrame.attrs读取来源、缺字段和复权元数据；跨API完整贯通仍属后续工作。
+
+本阶段没有实现SQLite历史增量层、统一跨进程采集器、所有插件独立涨停池迁移、完整规则版本库或5交易日影子运行，也未部署。当前缓存为短期内存缓存，不应当作方案中的持久历史库。
+
+### 11.3 实施后验证
+
+| 验证内容 | 验证时间 | 验证位置 | 验证结果 | 验证范围 | 验证通过率 | 验证失败范围 | 失败原因记录 |
+|---|---|---|---|---|---|---|---|
+| 针对性pytest回归 | 2026-09-11 14:12前后，北京时间 | 本地项目 | 112项通过 | 新契约/缓存、报价兼容、自选详情、聚合中枢、技术/形态/趋势/筹码/量价、数据后端测试 | 112/112，100% | 无 | 1条已有LibreSSL兼容性警告 |
+| 新浪bulk单位核验 | 2026-09-11 14:08–14:10，北京时间 | 本机接口 | 字段/现有清单覆盖通过，20只换手率对照通过；退出2 | 5491只识别后的沪深A股、20只高换手样本 | 样本20/20；整体准入未完成 | 时间、资金分类、当日主数据证明不足 | 不再误报整体PASS |
+| 腾讯报价/K线核验 | 2026-09-11 14:08–14:10，北京时间 | 本机接口 | 报价20/20；日K3/3结构通过；退出2 | 20只报价、3只180根日K请求 | 报价100%；复权对照未完成 | 东财独立对照及除权事件证据 | 东财不可用，未指定已核实事件 |
+| 新浪轻量日K核验 | 2026-09-11 14:10前后，北京时间 | 本机接口 | 5/5结构、日期、缺口标记通过；退出2 | 5只60根请求 | 结构100%；前复权准入未完成 | 复权未知 | 已从前复权后备链移除 |
+| 基础涨停抽样 | 2026-09-11 14:10前后，北京时间 | 本机接口 | 报价60/60；1只涨停价未知；退出2 | 最多60只样本，不是全市场池 | 报价100%；完整事件验收未完成 | 1只未知价格及全市场/事件能力 | 新股等例外不能用涨幅强行判断 |
+| 实际报价入口只读核验 | 2026-09-11 14:13:32起，北京时间 | 本机fetch_stock_quotes | 腾讯3/3，后备调用0，约0.363秒 | 600519、000001、600036单轮 | 3/3，100% | 无 | 单轮延迟不是p95或SLA |
+| 编译及diff格式 | 2026-09-11 14:13:32，北京时间 | 修改文件 | 通过 | compileall与git diff --check | 通过 | 无 | 不替代长期部署验证 |
+
+以上为2026-09-11实施时点记录。2026-09-14复核后，下一步改为优先修复端到端质量门控，随后验证完整日线及持久化路径；资金主源切换仍须解决分类等价性和源时间证据，不能跳过准入门槛。
+
+
+## 12. 2026-09-14核验后的调整决定
+
+### 12.1 结论与本轮边界
+
+按能力路由、候选精查、SQLite历史复用和东财独有能力增强的方向保留，但必须先解决“哪些数据可以参与决策”。适配器返回成功或已有测试通过，不代表快照、策略、输出及通知已完成一致的质量校验。
+
+本轮对照当前代码，执行针对性回归并构造离线反例；没有运行正式套利任务、发送通知、部署或实施以下生产修复。反例验证的是代码在模拟输入下的行为，不证明此前每个真实BUY均使用了旧数据。§11的112项历史回归与本轮56项测试范围不同，不相加为新的总通过数。
+
+### 12.2 缺口、修复范围与放行条件
+
+| 优先级 | 核验发现及位置 | 修复方向 | 放行条件 |
 |---|---|---|---|
-| 1 | sina bulk 计算涨停（changeratio ≥ 涨停阈值 + 主板/ST/20cm 判定，复用已验证 ssggzj 全市场 quote） | 仅代码清单 + 涨跌幅，无封板/首封/炸板/连板 | **主源**，标 degraded=true；01/10 依赖封板字段的功能静默跳过 |
-| 2 | 东财 zt_pool（现 fetch_zt_pool） | 全字段（封板/首封/炸板/连板） | 备用（东财可达时自动升级全字段，active_source 标 eastmoney_zt） |
+| P0 | `backend/services/data_backend/snapshots.py` 的 `_read_asset` 将成功返回标fresh，使用本轮fetched_at，codes写请求清单；离线返回一只旧报价却声称两只覆盖且新鲜 | 分别记录requested_codes、received_codes、eligible_codes及缺失原因；保留逐行source_time、来源和降级状态，读取与刷新入口共用质量判定 | 陈旧数据重下载仍为陈旧；部分返回不声称完整；缺失代码不能命中覆盖缓存；序列化前后质量信息一致 |
+| P0 | `backend/plugins/overnight_arbitrage/service.py` 的 `_missing_quote_fields` 仅检查数值缺失；注入上个交易日且is_stale=True的报价仍得BUY=1、data_quality=complete | 评分前统一校验交易日、源时间、年龄、字段、有限数值及来源能力；输出与通知前按实际完成时间复核 | 旧日期、未知源时间、超龄、必需字段缺失候选均不得输出有效实时BUY；任务超时后不能沿用启动时刻放行 |
+| P0 | 同文件 `_fetch_yahoo_5m_strength` 忽略timestamp，OHLC分别去空；极旧时间戳仍产生最近15分钟强度 | 按timestamp组合整根bar后过滤，验证交易日、顺序、间隔、最新时点和完成状态；不得跨午休、跨日拼接成连续15分钟 | 过期、错位、缺口或未完成bar按明确规则处理；无效增强不得加分，依赖必需增强的策略输出不可用 |
+| P1 | `eastmoney_quote.py` 的兼容后备未沿用腾讯质量验证；无源时间的东财行可被标为未降级；部分返回后提前结束补缺 | 所有源走同一验证器，按策略所需字段决定合格集合；逐源维护缺失集合，继续补剩余代码；补字段保留各自来源和时间 | 未知时间不能冒充新鲜；已有合格报价不被覆盖；不能拼接时间不一致或口径不同字段构造假完整行 |
+| P1 | `common.py::get_kline_cached` 用请求days代表覆盖；请求130根只返回30根后，再请求100根命中30根 | 记录实际交易日范围、有效行数、完整性和短缺原因；区分新股历史不足、停牌与上游截断；缓存身份逐步加入周期、复权版本和来源能力 | 短窗口不能通过长窗口覆盖判定；合法历史不足明确返回原因，不无限补取；上游截断按预算补洞 |
+| P1 | 套利 `_eastmoney_all_a_snapshot` 固定分页上限，未与有效证券全集核对；独立链路未接入腾讯候选精查 | 明确粗筛范围、分母、截断和未覆盖代码；根据实际分页结果及预算决定继续或降级；评分前统一候选精查 | 不把扫描1400只或某个固定行数当全市场验收；每个最终候选独立通过字段与时效检查 |
 
-### 4.5 腾讯源规格（免费，实测 200）
-- 批量报价：`https://qt.gtimg.cn/q=sh600519,sz000001,...`（每批 ~60 只，GBK，含名称/现价/昨收/今开/成交量/成交额/涨跌幅/换手率/总市值/流通市值）→ 全市场 ~50 批。
-- 前复权日K：`https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600519,day,,,N,qfq`（qfqday 字段 date/open/close/high/low/volume，缺 amount/换手 → 与 sina 同缺口处理）。
-- 资金流 per-stock：项目已有 tencent.py（无批量，仅作逐票精算备选）。
+前三项作为切源上线阻断项；其余缺口也必须在受影响能力晋升前完成。P0表示本方案中的优先修复和放行阻断级别，不代表已证实历史运行全部错误。
 
-## 5. 统一熔断/健康（扩展到全部数据面）
+### 12.3 统一策略能力与质量契约
 
-- 复用 principal_capital 的 SOURCE_HEALTH_FILE 范式，新增 `data/source_health_kline.json / _quote.json / _zt.json`（或统一 `data/source_health.json` 加 surface 维度）。
-- 每面：连续失败 ≥3 → 熔断 blocked_until（退避 10min 指数递增）；恢复探测成功 → 解除。
-- 优先级顺序写在各面 config；东财恒末位。
+建立策略能力清单：策略入口、证券范围、必需字段、可选增强、单位、复权要求、最大行情年龄、来源准入状态及缺失行为。先覆盖隔夜套利，再逐一迁移其它消费者，不能只修改共享取数器就宣称全链路完成。
 
-## 6. 配置参数（env 可覆盖）
+- 行情的source_time、采集的fetched_at、决策的started_at/completed_at分别保存；行情年龄用源时间计算，不能从报告生成时间推断。
+- 按实际交易日历和时段处理午休、盘后、停牌；展示可用与实时决策合格分开判定。同日数据并非始终新鲜。
+- 报价、日K、分钟和事件能力分别准入。腾讯qfqday的端点身份不等同复权一致性已验收；缺成交额或换手率时，只能服务明确不依赖这些字段且满足其余准入条件的消费者。
+- 可选增强缺失时仅执行原策略已明确允许的路径，不静默调整权重。必需能力缺失则阻断相应信号；通用适配器不得擅自决定策略降级算法。
+- 序列化、快照读取、API响应及最终输出贯通质量信息，不只存于DataFrame.attrs。决定有效BUY的校验集中实现，避免插件各自复制不同规则。
+- 新浪bulk仍是未准入候选：资金分类缺r1/r2、行情时间证据不足，保持正式资金源优先级；未来即使获准用于粗筛，也不自动获得资金策略准入。
 
-| 键 | 默认 | 说明 |
-|---|---|---|
-| KLINE_SOURCE_ORDER | sina,tencent,eastmoney,akshare | K线优先级 |
-| FUNDFLOW_SOURCE_ORDER | sina,tencent,eastmoney,akshare | 资金流优先级 |
-| ZT_SOURCE_ORDER | sina_calc,eastmoney | 涨停池优先级（免费 tushare 不够，不启用） |
-| TUSHARE_TOKEN | 空 | 可选：升级 2000 积分后再启用 tushare 交叉校验 |
-| SOURCE_FAIL_STREAK | 3 | 熔断连续失败阈值 |
-| SOURCE_BLOCK_MINUTES | 10 | 熔断退避基数 |
-
-## 7. 测试与验收
-
-- **M0 验证脚本（4 个，已实跑全 PASS，2026-09-10）**：
-  1. `scripts/verify_sina_kline.py` → **PASS**：sina 日K 5/5 只，OHLC>0+升序，末根当日；前复权 vs 东财 diff **0.0000**；缺口列（成交额/振幅/换手率）全 0 已显式确认；
-  2. `scripts/verify_tencent_quote_kline.py` → **PASS**：批量报价 20/20 字段全（含换手率/总市值）；前复权日K vs 新浪 diff **0.0000%**；
-  3. `scripts/verify_sina_quote.py` → **PASS**：bulk 6431 行，行情字段全，换手率 500/500 有值，主板覆盖 0 漏，指数接口 200；
-  4. `scripts/verify_zt_sources.py` → **PASS**：sina_calc 疑似涨停 22 只；东财 zt_pool 有数据（封禁解除）；同花顺涨停复盘 200（解析待做）。
-- 单测：优先级选择（源1失败→源2，熔断退避，恢复解除）；字段缺口 None/degraded 断言；各消费插件（18/19/20/21/16）在 sina 主源下的回归。
-- 灰度：主备倒置后连续 5 交易日，各快照 active_source 统计——sina 主占比 ≥95%，东财仅兜底命中；口径 diff（同一时刻双源）无显著偏差。
-- 验收硬指标：东财封禁期间，18/19/20/21/16/17/01/05/资金流全链路**不因东财不可用而空态**；active_source/degraded 显式标注；0 新付费依赖（tushare token 可选）。
-
-## 8. 风险与红线
-
-| # | 风险 | 缓解 |
-|---|---|---|
-| R1 | sina K线缺成交额/换手率 → 16 换手率因子失效 | 字段 None + 因子跳过 + degraded 标注；不伪造 |
-| R2 | sina 涨停计算缺封板时间/连板 → 01/10 部分功能降级 | 三级源降级链；东财恢复后自动回全字段 |
-| R3 | 免费 tushare 不够（stk_limit 需 2000 积分、limit_list_d 停维护） | 不引入 tushare 主源；涨停池 sina_calc 主 + 东财全字段备用；升级积分后可选交叉校验 |
-| R4 | sina 接口也有频率上限 | 全市场单请求已实测 1s；K线逐票走并发 20 + 当日缓存（已有 common.get_kline_cached） |
-| R5 | 复权口径差异污染指标 | M0 diff 对照 + 统一 fqt 语义，差异写快照 active_source |
+### 12.4 调整后的施工顺序
 
-## 9. 里程碑
+| 执行顺序 | 对应原里程碑 | 最小交付 | 放行与回退 |
+|---|---|---|---|
+| 1. 决策质量闭环 | M0及M2/M4前置部分 | 快照实际覆盖、统一报价质量校验、套利评分前与输出前门控、分钟时间对齐 | 将本轮反例固化为回归；不合格输入不产生有效BUY；回退不得关闭门控强行出结果 |
+| 2. 覆盖与取数一致性 | M1、M2剩余部分 | 后备按缺失集合补齐、实际K线覆盖、候选精查贯通、批次元数据与原子快照发布 | 全市场声明有有效分母；补取受总截止时间约束；读取方拒绝批次不一致的快照 |
+| 3. 日K持久化 | M3 | 最小日K增量、补洞、复权版本失效、备份及恢复 | 保留旧库和可回滚迁移；先证明数据可恢复，再扩充分钟及事件留存 |
+| 4. 其余能力迁移 | M4剩余部分及各源独立准入 | 涨停基础/增强拆分、完整规则边界、插件消费者迁移 | 无等价事件源时保留东财能力或明确不可用；单能力通过不带动其它能力自动晋升 |
+| 5. 部署地影子运行 | M5 | 唯一采集/决策所有者、5交易日观测、故障及恢复演练 | 比较覆盖、行情年龄、错误放行、截止时间达成率与尾延迟；不以某供应商占比作为成功标准 |
 
-| M | 内容 | 前置 |
-|---|---|---|
-| M0 | **✅ 已完成（4 验证脚本全 PASS，见 §7）** | — |
-| M1 | P0：K线 + 资金流优先级倒置 + 单测回归 | M0 |
-| M2 | P1：sina_quote/指数/涨停池三级 + data_backend 切换 | M0/M1 |
-| M3 | P2：tushare 可选源 + national_team 降级标注 | M2 |
-| M4 | 灰度 5 交易日 + 验收报告 | M1-M3 |
+现有单采集者、共享供应商预算、SQLite短事务和有限留存设计继续保留。先解决正确性与重复采集，再依据部署地实测决定并发、候选容量和历史回补速度，不同时引入新的数据库、队列和多机写入架构。
 
-## 10. 待拍板项
+### 12.5 本轮验证记录
 
-| # | 决策 | 默认 |
-|---|---|---|
-| D1 | 东财定位 | 末位备用（不删除，作 cross-check） |
-| D2 | 涨停池主源 | **sina_calc（免费 tushare 不够，已核实）→ 东财 zt_pool 全字段备用** |
-| D3 | sina K线缺字段 | 成交额/换手率 None + 因子降级（不伪造） |
-| D4 | 国家队数据 | 保留东财唯一源，non-blocking 降级 |
-| D5 | 灰度期 | 5 交易日 |
-| D6 | 免费多源矩阵 | 资金流 sina→tencent→东财；行情 sina→腾讯→东财；日K sina→腾讯→东财；涨停池 sina_calc→东财（同花顺涨停复盘 200 可达，解析列为 M2 可选） |
+| 验证内容 | 验证时间 | 验证位置 | 验证结果 | 验证范围 | 验证通过率 | 验证失败范围 | 失败原因记录 |
+|---|---|---|---|---|---|---|---|
+| 针对性回归 | 2026-09-14北京时间 | 本地项目，pytest | 56项通过 | 源契约、报价韧性、data_backend、套利service及pipeline测试 | 56/56，100% | 本轮选定测试无失败；不覆盖全部缺口 | 1条LibreSSL兼容性警告；不代表整链准入 |
+| 离线缺陷反例 | 2026-09-14北京时间（首批反例记录10:29:58） | 本地Python，模拟取数与内存对象 | 5类行为均复现 | 快照陈旧/虚报覆盖、后备未知时间未降级、旧分钟增强、旧报价BUY、实际K线覆盖不足 | 缺陷复现5/5；生产质量门控未通过 | 五类现有行为 | 元数据丢失、时效校验未贯通、缓存以请求量代替实际覆盖 |
+| 官方资料抽查 | 2026-09-14北京时间 | §1及§6所列官方资料 | Tushare权限表及上交所生效通知支持相关表述；读取Render Cron文档 | 文档抽查，非全部来源或账户验收 | 不适用 | 账户授权、实际账单、全部规则例外未验证 | 规则仍须细化条款生效及暂缓边界 |
+| 实网压测、部署恢复与影子运行 | 未执行 | 待实际部署地 | 未执行 | 性能、长期稳定性及5交易日验证 | 未执行 | 全部本轮运行验证范围 | 本轮为方案核验与修订，不做正式行情任务或部署 |
+
+后续回归至少补齐上述反例、不同来源混合补缺、午休/盘后新鲜度、节假日、任务执行期间过期、序列化质量保留，以及原子发布中断后的上一版读取。测试通过后才评估源晋升；不能通过缩小声明范围或降低字段要求掩盖仍存在的错误放行。
diff --git "a/docs/24_\346\225\260\346\215\256\346\272\220\345\216\273\344\270\234\350\264\242\345\214\226_\345\256\236\346\226\275diff.md" "b/docs/24_\346\225\260\346\215\256\346\272\220\345\216\273\344\270\234\350\264\242\345\214\226_\345\256\236\346\226\275diff.md"
new file mode 100644
index 0000000..f52e331
--- /dev/null
+++ "b/docs/24_\346\225\260\346\215\256\346\272\220\345\216\273\344\270\234\350\264\242\345\214\226_\345\256\236\346\226\275diff.md"
@@ -0,0 +1,89 @@
+# 24 方案 · 分阶段实施 diff.md（§12.4 第1–4步核心）
+
+> 2026-09-14 起，按 docs/24_数据源去东财化_主备倒置.md §12.4 施工顺序推进。
+> 本文汇总目标执行以来的所有改动（不含 §11 首阶段既有改动）。
+
+## 变更文件清单
+
+| 文件 | 批次 | 内容 |
+|---|---|---|
+| backend/services/data_backend/snapshots.py | 第1/2步 | P0-1 实际覆盖 + 原子发布 + 批次一致性 |
+| backend/agents/layer1_data_collector/sources/eastmoney_quote.py | 第1/2步 | 东财 legacy degraded + 候选精查入口 |
+| backend/plugins/common.py | 第1/2/3步 | K线行数覆盖/覆盖元数据 + 旁路写库 hook |
+| backend/plugins/overnight_arbitrage/service.py | 第1/2/4步 | 质量门控/完成复核/yahoo5m对齐/覆盖元数据/候选精查/zt事件能力标注 |
+| backend/plugins/overnight_arbitrage/__init__.py | 第2步 | CLI 注入候选精查 |
+| backend/services/data_backend/bars_store.py（新） | 第3步 | 日K持久化（upsert/补洞/复权失效/备份恢复） |
+| backend/agents/layer1_data_collector/sources/zt_contract.py（新） | 第4步 | 涨停基础/增强拆分 + 规则边界 |
+| tests/test_decision_quality_gates.py（新） | 第1/2/3/4步 | 决策质量 + 覆盖 + 精查 + zt 能力反例 |
+| tests/test_bars_store.py（新） | 第3步 | 日K持久化单测 |
+| tests/test_zt_contract.py（新） | 第4步 | 涨停规则/基础/增强单测 |
+
+## 各步修复摘要
+
+### 第1步 决策质量闭环
+- snapshots：received/requested/missing 覆盖诚实化；全 degraded 不标 fresh。
+- eastmoney_quote：东财 legacy source_time 未知 → degraded=True + missing_fields。
+- common：K线缓存命中加 rows>=days，短窗不冒充长窗，负缓存保持。
+- overnight：评分前 _quote_quality_block（degraded/is_stale/旧 source_time 不得 BUY）；完成时刻墙钟复核；yahoo5m 按 timestamp 整根对齐 + 新鲜度 + 同日连续窗口。
+
+### 第2步 覆盖与取数一致性
+- snapshots：唯一临时文件 + os.replace 原子写；双副本 snapshot_version；不一致以 canonical 为准标 _batch_inconsistent。
+- common：K线返回附 kline_coverage（rows/first/last/short/reason）。
+- overnight：_eastmoney_all_a_snapshot / _sina_all_a_snapshot 附 coverage 元数据，截断→degraded；候选精查贯通（fetch_tencent_quotes_for_codes + _refine_quotes_with_tencent + run CLI 注入）。
+
+### 第3步 日K持久化
+- 新增 bars_store：单表 bars_daily (code,trade_date,adjustment) 幂等 upsert、read、missing_dates（交易日历补洞）、delete_adjustment（复权失效）、backup（在线备份 API）、verify_backup。
+- common：get_kline_cached 成功取数后 _persist_kline_best_effort（KLINE_STORE_WRITE_ENABLED 默认关闭，旁路不阻断）。
+
+### 第4步 涨停拆分
+- 新增 zt_contract：compute_limit_prices（规则版本 v2 主板ST=10%、Decimal ROUND_HALF_UP、未知返回 None）、classify_zt_basic（touched/at_limit/unknown）、enrich_zt_events、required_events_available。
+- overnight：决策项/顶层暴露 zt_events_available + unavailable_required_fields（缺失显式标注，不静默当 0）。
+
+## 回归结果
+
+```
+pytest tests/test_zt_contract.py tests/test_bars_store.py tests/test_decision_quality_gates.py        tests/test_source_contracts.py backend/services/data_backend/tests        backend/plugins/overnight_arbitrage/tests backend/plugins/tech_indicators/tests        backend/plugins/principal_capital/tests backend/plugins/trend_strength/tests        backend/plugins/pattern_scanner/tests backend/plugins/chip_scanner/tests -q
+→ 170+ passed, 1 warning（既有 LibreSSL）
+```
+
+## 未完成（§12.4 后续）
+
+- 第4步剩余：01/10/16 三个消费方接入 zt_contract（本轮仅完成 overnight 消费方）。
+- 第5步：部署地影子运行（5 交易日覆盖/故障演练）——依赖真实部署环境，本机无法执行。
+
+## 状态
+
+所有改动仍未提交（叠加在 §11 首阶段未提交改动之上）；建议核验后统一 commit。
+
+---
+
+## 后续轮次补充（round 7-8）
+
+### 第4步补充
+- zt_contract.py 新增 resolve_zt_basic_from_quotes(quotes)：免费基础路径批量计算涨停基础状态，供 01/10/16 后续接入。
+- tests/test_zt_contract.py 新增 test_resolve_zt_basic_from_quotes（7 passed）。
+
+### 第5步补充（代码侧观测）
+- 新增 backend/services/data_backend/shadow_run.py：SHADOW_RUN_ENABLED=1 时 record() 每轮观测（asset/source/status/coverage/source_time/age/error/deadline_met），summarize() 聚合 错误率/行情年龄P95/截止达成率/错误类型Top5。
+- 新增 tests/test_shadow_run.py（record→summarize→reset，1 passed）。
+
+### 最新回归
+pytest shadow_run + zt_contract + bars_store + decision_quality_gates + source_contracts + data_backend + overnight_arbitrage + tech_indicators + principal_capital → 158 passed, 1 warning。
+
+---
+
+## 最终批次（授权后完成）
+
+### 第4步 01/10 消费方迁移（显式 unavailable）
+- emotion_cycle：涨停池源拉取失败（zt_pool=None）→ status="unavailable_required_fields" + unavailable_required_fields=["zt_events"]，区别于合法空池 no_data；不再把源不可用当"0 涨停"。
+- zt_seal：同上，封单事件缺失时显式 unavailable，不静默当 0。
+- 16 low_position_scanner 已有四级降级 + "不可用不剔除"语义，无需改动；免费基础路径 API（resolve_zt_basic_from_quotes）已就绪供后续接入。
+- tests/test_decision_quality_gates.py 新增 test_emotion_unavailable_on_zt_fetch_failure / test_zt_seal_unavailable_on_zt_fetch_failure。
+
+### 第5步 影子运行交付
+- 代码侧：backend/services/data_backend/shadow_run.py（record/summarize）。
+- 文档：docs/24_影子运行_观测报告模板与启动清单.md（启动清单 7 项 + 每日/5日报告模板 + 放行/回退条件 + 本机可复现命令）。
+- 真实部署观测按用户指示延后（部署环境就绪后开启 SHADOW_RUN_ENABLED=1 连续 5 交易日）。
+
+### 最终回归
+pytest shadow_run + zt_contract + bars_store + decision_quality_gates + source_contracts + data_backend + overnight_arbitrage + tech_indicators + principal_capital + trend_strength + pattern_scanner + chip_scanner → 182 passed, 1 warning。
diff --git a/scripts/source_verification.py b/scripts/source_verification.py
new file mode 100644
index 0000000..09717eb
--- /dev/null
+++ b/scripts/source_verification.py
@@ -0,0 +1,75 @@
+"""源验证共用硬断言：FAIL退出1，证据未完成退出2，全部通过才退出0。"""
+from __future__ import annotations
+
+import pandas as pd
+
+
+class Checks:
+    def __init__(self):
+        self.failed = []
+        self.pending = []
+
+    def check(self, label, ok, detail=""):
+        if not ok:
+            self.failed.append(label)
+        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")
+        return bool(ok)
+
+    def skip(self, label, detail):
+        self.pending.append(label)
+        print(f"[未完成] {label}: {detail}")
+
+    def finish(self):
+        code = 1 if self.failed else (2 if self.pending else 0)
+        print(f"结论: {'FAIL' if code == 1 else '未完成' if code == 2 else 'PASS'}；"
+              f"失败{len(self.failed)}项，未完成{len(self.pending)}项")
+        return code
+
+
+def validate_bars(df):
+    """验证每行OHLCV、日线身份、唯一性与升序；不证明复权类型。"""
+    if df is None or df.empty:
+        return False
+    required = ["日期", "开盘", "收盘", "最高", "最低", "成交量"]
+    if any(key not in df.columns for key in required):
+        return False
+    dates = pd.to_datetime(df["日期"], errors="coerce")
+    values = df[required[1:]].apply(pd.to_numeric, errors="coerce")
+    import numpy as np
+    return bool(dates.notna().all() and dates.is_unique and dates.is_monotonic_increasing
+                and np.isfinite(values.to_numpy()).all()
+                and (values[["开盘", "收盘", "最高", "最低"]] > 0).all().all()
+                and (values["成交量"] >= 0).all()
+                and (values["最高"] >= values[["开盘", "收盘", "最低"]].max(axis=1)).all()
+                and (values["最低"] <= values[["开盘", "收盘", "最高"]].min(axis=1)).all())
+
+
+def compare_closes(left, right, min_rows=20, tolerance_pct=0.1):
+    """按日期对齐，逐日相对误差；不允许空交集或zip截断假通过。"""
+    if not validate_bars(left) or not validate_bars(right):
+        return False, "K线结构校验失败"
+    a, b = left.copy(), right.copy()
+    a["日期"] = pd.to_datetime(a["日期"]).dt.strftime("%Y-%m-%d")
+    b["日期"] = pd.to_datetime(b["日期"]).dt.strftime("%Y-%m-%d")
+    matched = a.merge(b, on="日期", suffixes=("_a", "_b"), validate="one_to_one")
+    if len(matched) < min_rows:
+        return False, f"共同交易日不足: {len(matched)}/{min_rows}"
+    error = ((matched["收盘_a"] - matched["收盘_b"]).abs() / matched["收盘_b"].abs() * 100).max()
+    return bool(error <= tolerance_pct), f"共同{len(matched)}日，最大逐日偏差{error:.4f}%"
+
+
+def check_recency(checks, df, now):
+    """使用项目日历；日历降级不伪装完成交易日新鲜度验收。"""
+    from backend.services.trading_calendar import calendar_status, prev_trading_day
+    if df is None or df.empty:
+        checks.check("最新交易日", False, "空K线")
+        return
+    if calendar_status().get("degraded"):
+        checks.skip("最新交易日", "权威交易日历不可用")
+        return
+    previous = prev_trading_day(now.date())
+    if previous is None:
+        checks.skip("最新交易日", "日历范围不覆盖当前日期")
+        return
+    last = pd.to_datetime(df["日期"]).max().date()
+    checks.check("最新交易日", previous <= last <= now.date(), str(last))
diff --git a/scripts/verify_sina_kline.py b/scripts/verify_sina_kline.py
index 93b37ca..5674582 100644
--- a/scripts/verify_sina_kline.py
+++ b/scripts/verify_sina_kline.py
@@ -1,61 +1,30 @@
 #!/usr/bin/env python3
-"""M0 验证 A：新浪日K（24 方案去东财化 K线主源）。一次性脚本，不进主链路。
-断言：主板抽样 5 只 → rows>0、OHLC>0、日期升序、最近一根 ≤2 交易日；字段缺口（成交额/振幅/换手率=0）显式报告。
-东财可达时做前复权收盘 diff；不可达则标 WARN（东财封禁中，以新浪自身一致性为准）。
-"""
+"""新浪轻量日K硬校验；复权未知时退出2，不宣布可作前复权主源。"""
 import sys
 from pathlib import Path
 sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
-from datetime import datetime, timezone, timedelta
+from datetime import datetime
+from backend.agents.layer1_data_collector.sources.historical_kline import _fetch_hist_sina, BEIJING_TZ
+from scripts.source_verification import Checks, validate_bars, check_recency
 
-from backend.agents.layer1_data_collector.sources.historical_kline import _fetch_hist_sina, _fetch_hist_eastmoney_direct
-
-BEIJING = timezone(timedelta(hours=8))
 CODES = ["600519", "000001", "600036", "000858", "601318"]
 
 
-def report(name, ok, detail, hard=True):
-    print(f"[{'PASS' if ok else ('FAIL' if hard else 'WARN')}] {name}: {detail}")
-
-
 def main():
-    now = datetime.now(BEIJING)
-    ok_rows = 0
+    checks = Checks()
     for code in CODES:
-        df = _fetch_hist_sina(code, 60)
-        if df is None or df.empty:
-            print(f"  {code}: 空")
-            continue
-        ok_rows += 1
-        ohlc_ok = (df["开盘"] > 0).all() and (df["收盘"] > 0).all() and (df["最高"] > 0).all() and (df["最低"] > 0).all()
-        dates = df["日期"].tolist()
-        asc = dates == sorted(dates)
-        last = dates[-1] if dates else None
-        age = None
-        if last:
-            age = (now.date() - datetime.strptime(str(last)[:10], "%Y-%m-%d").date()).days
-        gap = {c: int((df[c] == 0).sum()) for c in ("成交额", "振幅", "换手率")}
-        print(f"  {code}: {len(df)}行 ohlc_ok={ohlc_ok} 升序={asc} 末根={last} 距今{age}日 缺口列零值={gap}")
-    report("新浪日K 可用(5/5)", ok_rows == len(CODES), f"{ok_rows}/{len(CODES)} 只")
-    report("OHLC>0 + 日期升序", True, "已随各票打印")
-    # 前复权口径对照（东财可达时）
-    em_ok = 0
-    for code in CODES[:2]:
-        em = _fetch_hist_eastmoney_direct(code, 30)
-        sn = _fetch_hist_sina(code, 30)
-        if em is None or em.empty or sn is None or sn.empty:
-            continue
-        em_ok += 1
-        merged = em.merge(sn, on="日期", suffixes=("_em", "_sn"))
-        if not merged.empty:
-            diff = (merged["收盘_em"] - merged["收盘_sn"]).abs().max()
-            print(f"  {code} 前复权收盘最大偏差: {diff:.4f}")
-    if em_ok:
-        report("前复权口径对照", True, f"{em_ok} 只东财可达已对照")
-    else:
-        report("前复权口径对照", False, "东财封禁中，无法对照（不影响新浪主源）", hard=False)
-    print("结论:", "PASS" if ok_rows == len(CODES) else "FAIL")
-    return 0 if ok_rows == len(CODES) else 1
+        try:
+            df = _fetch_hist_sina(code, 60)
+            if not checks.check(f"{code} OHLCV/日期", validate_bars(df)):
+                continue
+            check_recency(checks, df, datetime.now(BEIJING_TZ))
+            checks.check(f"{code} 缺口保持空值",
+                         all(df[key].isna().all() for key in ("成交额", "换手率", "振幅")))
+            checks.check(f"{code} 复权标记真实", df.attrs.get("adjustment") == "unknown")
+        except Exception as exc:
+            checks.check(code, False, str(exc))
+    checks.skip("前复权准入", "scale=240只表示周期，尚未取得可验证的复权契约")
+    return checks.finish()
 
 
 if __name__ == "__main__":
diff --git a/scripts/verify_sina_quote.py b/scripts/verify_sina_quote.py
index 6dbf139..82842dc 100644
--- a/scripts/verify_sina_quote.py
+++ b/scripts/verify_sina_quote.py
@@ -1,47 +1,58 @@
 #!/usr/bin/env python3
-"""M0 验证 C：新浪全市场实时行情 + 指数（24 方案 quotes/index_snapshot 主源）。一次性脚本。
-断言：ssggzj bulk 全市场行情字段（trade/changeratio/turnover/amount）非空；主板清单覆盖 0 漏；指数接口可达。
-"""
+"""新浪bulk单位、覆盖与腾讯交叉核验；缺时间戳时不授予实时准入。"""
 import json
 import sys
+from pathlib import Path
+sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
 import requests
+from backend.agents.layer1_data_collector.sources.quote_contract import (
+    normalize_sina_bulk, parse_tencent_quotes, quote_is_current,
+)
+from scripts.source_verification import Checks
 
 H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
 BULK = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_ssggzj"
-
-
-def report(name, ok, detail, hard=True):
-    print(f"[{'PASS' if ok else ('FAIL' if hard else 'WARN')}] {name}: {detail}")
+ROOT = Path(__file__).resolve().parents[1]
 
 
 def main():
-    r = requests.get(BULK, params={"num": "8000", "sort": "r0_net", "asc": "0"}, headers=H, timeout=20)
-    rows = r.json()
-    print("bulk rows:", len(rows), "http:", r.status_code)
-    sample = rows[:500]
-    need = ["symbol", "name", "trade", "changeratio", "turnover", "amount"]
-    missing = [c for c in need if sum(1 for x in sample if x.get(c) in (None, "")) > 450]
-    report("行情字段完整(trade/changeratio/turnover/amount)", not missing, f"缺失: {missing or '无'}")
-    # 换手率有值抽样
-    tvs = [x for x in sample if x.get("turnover") not in (None, "", "0")]
-    report("换手率有值", len(tvs) > 100, f"抽样500中 {len(tvs)} 只有换手率")
-    # 主板覆盖
-    cached = json.load(open("data/principal_capital_sina_codes.json", encoding="utf-8"))
-    want = {str(c).zfill(6) for c in (cached.get("codes") or [])}
-    got = {str(x.get("symbol"))[2:].zfill(6) for x in rows if str(x.get("symbol") or "")[:2] in ("sh", "sz")}
-    miss = sorted(want - got)
-    report("主板清单覆盖 0 漏", not miss, f"清单 {len(want)} / bulk {len(want & got)} / 漏 {len(miss)}")
-    # 指数接口试探
-    idx = "https://hq.sinajs.cn/list=s_sh000001,s_sz399001,s_sz399006"
+    checks = Checks()
     try:
-        ir = requests.get(idx, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}, timeout=8)
-        ir.encoding = "gbk"
-        report("新浪指数接口可达", ir.status_code == 200 and len(ir.text) > 50,
-               f"http {ir.status_code} / {ir.text[:80]}")
-    except Exception as e:
-        report("新浪指数接口可达", False, f"异常 {e}")
-    print("结论:", "PASS" if not missing and not miss else "FAIL")
-    return 0 if (not missing and not miss) else 1
+        response = requests.get(BULK, params={"num": 8000, "sort": "r0_net", "asc": 0}, headers=H, timeout=(3, 8))
+        response.raise_for_status()
+        rows = normalize_sina_bulk(response.json())
+        checks.check("全返回字段", all(all(row[k] is not None for k in
+                      ("price", "change_pct", "turnover_pct", "amount")) for row in rows), f"{len(rows)}行")
+        cache = ROOT / "data/principal_capital_sina_codes.json"
+        if cache.exists():
+            wanted = set(json.loads(cache.read_text(encoding="utf-8")).get("codes") or [])
+            got = {row["code"] for row in rows}
+            checks.check("现有清单覆盖", bool(wanted) and wanted <= got, f"缺{len(wanted - got)}只")
+            checks.skip("当日主数据完整性", "缓存清单不能证明当日新上市/停牌范围已核验")
+        else:
+            checks.skip("主板覆盖", "无已验证股票清单")
+        sample = sorted((r for r in rows if r["price"] and r["turnover_pct"] is not None),
+                        key=lambda r: r["turnover_pct"], reverse=True)[:20]
+        symbols = [row["symbol"] for row in sample]
+        if not symbols:
+            raise ValueError("没有可交叉验证的样本")
+        response = requests.get("https://qt.gtimg.cn/q=" + ",".join(symbols), headers=H, timeout=(3, 8))
+        response.raise_for_status()
+        response.encoding = "gbk"
+        tx = {r["代码"]: r for r in parse_tencent_quotes(response.text, symbols)}
+        for row in sample:
+            peer = tx.get(row["code"])
+            if not checks.check(row["code"] + " 独立报价", bool(peer)):
+                continue
+            checks.check(row["code"] + " 腾讯时效", quote_is_current(peer))
+            turn = peer["换手率"]
+            checks.check(row["code"] + " 换手率单位",
+                         turn is not None and abs(row["turnover_pct"] - turn) <= max(0.05, abs(turn) * 0.02),
+                         f"新浪{row['turnover_pct']:.4f}% / 腾讯{turn}%")
+        checks.skip("实时及资金策略准入", "bulk无明确源时间，且缺r1/r2分类；仅用于报价观察")
+    except Exception as exc:
+        checks.check("新浪bulk核验", False, str(exc))
+    return checks.finish()
 
 
 if __name__ == "__main__":
diff --git a/scripts/verify_tencent_quote_kline.py b/scripts/verify_tencent_quote_kline.py
index 5194961..0529439 100644
--- a/scripts/verify_tencent_quote_kline.py
+++ b/scripts/verify_tencent_quote_kline.py
@@ -1,72 +1,61 @@
 #!/usr/bin/env python3
-"""M0 验证 B：腾讯批量报价 + 前复权日K（24 方案去东财化备源/行情源）。一次性脚本。
-断言：批量报价 200 且字段完整（名称/现价/昨收/涨跌幅/成交额/换手率/总市值）；批量 60 只不超限；
-前复权日K 200 且与新浪前复权收盘 diff ≤0.1%。
-"""
+"""腾讯批量报价及日K验证；跨除权日比较必须真实通过才能完成M0。"""
+import argparse
 import sys
 from pathlib import Path
 sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
+from datetime import date, datetime
 import requests
+from backend.agents.layer1_data_collector.sources.quote_contract import parse_tencent_quotes, quote_is_current
+from backend.agents.layer1_data_collector.sources.historical_kline import (
+    _fetch_hist_tencent, _fetch_hist_eastmoney_direct, BEIJING_TZ,
+)
+from scripts.source_verification import Checks, validate_bars, compare_closes, check_recency
 
 H = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
-CODES = ["sh600519", "sz000001", "sh600036", "sz000858", "sh601318", "sh601398", "sz000333", "sh600000",
-         "sz002415", "sh601088", "sz000651", "sh600028", "sz000002", "sh601857", "sz000100",
+CODES = ["sh600519", "sz000001", "sh600036", "sz000858", "sh601318",
+         "sh601398", "sz000333", "sh600000", "sz002415", "sh601088",
+         "sz000651", "sh600028", "sz000002", "sh601857", "sz000100",
          "sh600900", "sz002594", "sh601166", "sz000725", "sh600050"]
 
 
-def report(name, ok, detail, hard=True):
-    print(f"[{'PASS' if ok else ('FAIL' if hard else 'WARN')}] {name}: {detail}")
-
-
-def main():
-    # 批量报价（20 只一次）
-    url = "https://qt.gtimg.cn/q=" + ",".join(CODES)
-    r = requests.get(url, headers=H, timeout=10)
-    r.encoding = "gbk"
-    print("报价 http:", r.status_code, "len:", len(r.text))
-    good = 0
-    for code in CODES:
-        line = r.text.split('v_' + code + '="')[1].split('";')[0] if ('v_' + code + '="') in r.text else ""
-        if not line:
-            continue
-        p = line.split("~")
-        # 腾讯字段: 1名称 2代码 3现价 4昨收 5今开 6成交量(手) 31涨跌 32涨跌幅 37成交额(万) 38换手率 43振幅 44流通市值 45总市值
-        if len(p) > 45:
-            name, price, prev, pct = p[1], p[3], p[4], p[32]
-            turnover, tot_mv = p[38], p[45]
-            good += 1
-            if good <= 3:
-                print(f"  {code}: {name} 现价={price} 昨收={prev} 涨跌幅={pct}% 换手率={turnover} 总市值={tot_mv}")
-    report("腾讯批量报价可用", good == len(CODES), f"{good}/{len(CODES)} 只字段完整")
-    # 前复权日K + 与新浪对照
-    from backend.agents.layer1_data_collector.sources.historical_kline import _fetch_hist_sina
-    import json
-    k_ok = diff_ok = 0
-    for code in CODES[:3]:
-        q = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,30,qfq" % code
+def main(argv=None):
+    parser = argparse.ArgumentParser(description=__doc__)
+    parser.add_argument("--ex-date", type=date.fromisoformat, help="已核实的样本除权日期")
+    args = parser.parse_args(argv)
+    checks = Checks()
+    try:
+        response = requests.get("https://qt.gtimg.cn/q=" + ",".join(CODES), headers=H, timeout=(3, 8))
+        response.raise_for_status()
+        response.encoding = "gbk"
+        rows = parse_tencent_quotes(response.text, CODES)
+        checks.check("批量代码覆盖", len(rows) == len(CODES), f"{len(rows)}/{len(CODES)}")
+        checks.check("必需字段完整", bool(rows) and all(not row["degraded"] for row in rows))
+        checks.check("报价时间", bool(rows) and all(quote_is_current(row) for row in rows))
+    except Exception as exc:
+        checks.check("腾讯报价", False, str(exc))
+    for symbol in CODES[:3]:
         try:
-            d = requests.get(q, headers=H, timeout=10).json()
-            day = (d.get("data") or {}).get(code, {}).get("qfqday") or []
-        except Exception as e:
-            print(f"  {code} 腾讯日K失败 {e}")
-            continue
-        if day:
-            k_ok += 1
-            closes_tx = [float(x[2]) for x in day]
-            sn = _fetch_hist_sina(code[2:], 30)
-            if sn is not None and not sn.empty:
-                closes_sn = sn["收盘"].astype(float).tolist()[-len(closes_tx):]
-                if closes_sn:
-                    diff = max(abs(a - b) for a, b in zip(closes_tx, closes_sn))
-                    diff_pct = diff / closes_sn[-1] * 100
-                    ok = diff_pct <= 0.1
-                    if ok:
-                        diff_ok += 1
-                    print(f"  {code}: 腾讯日K {len(day)}根 前复权收盘 vs 新浪 最大偏差 {diff_pct:.4f}%")
-    report("腾讯前复权日K可用", k_ok == 3, f"{k_ok}/3 只")
-    report("腾讯vs新浪前复权一致(≤0.1%)", diff_ok == k_ok and k_ok > 0, f"{diff_ok}/{k_ok} 只")
-    print("结论:", "PASS" if good == len(CODES) and k_ok == 3 else "FAIL")
-    return 0 if (good == len(CODES) and k_ok == 3) else 1
+            tx = _fetch_hist_tencent(symbol[2:], 180)
+            if not checks.check(symbol + " 日K", validate_bars(tx)):
+                continue
+            checks.check(symbol + " 复权身份", tx.attrs.get("adjustment") == "qfq")
+            check_recency(checks, tx, datetime.now(BEIJING_TZ))
+            em = _fetch_hist_eastmoney_direct(symbol[2:], 180)
+            if em is None or em.empty:
+                checks.skip(symbol + " 独立对照", "东财不可用，不能视作复权验证成功")
+                continue
+            ok, detail = compare_closes(tx, em, min_rows=120)
+            checks.check(symbol + " 按交易日比较", ok, detail)
+            if args.ex_date:
+                shared = sorted(set(tx["日期"].astype(str)) & set(em["日期"].astype(str)))
+                day = args.ex_date.isoformat()
+                checks.check(symbol + " 除权日期覆盖", day in shared and shared[0] < day < shared[-1])
+        except Exception as exc:
+            checks.check(symbol, False, str(exc))
+    if args.ex_date is None:
+        checks.skip("除权事件证据", "未指定已核实样本除权日期；普通重合区间不能证明复权")
+    return checks.finish()
 
 
 if __name__ == "__main__":
diff --git a/scripts/verify_zt_sources.py b/scripts/verify_zt_sources.py
index bc813fa..23bf0eb 100644
--- a/scripts/verify_zt_sources.py
+++ b/scripts/verify_zt_sources.py
@@ -1,68 +1,49 @@
 #!/usr/bin/env python3
-"""M0 验证 D：涨停池 sina_calc 计算 + 东财 zt_pool 可达性 + 同花顺涨停复盘试探（24 方案）。一次性脚本。
-sina_calc 判定：主板非ST changeratio≥9.8%；ST≥4.8%；创业/科创≥19.5%。changeratio 归一化为百分数。
-输出：疑似涨停清单数量 + 抽样；东财/同花顺可达性记录。
-"""
+"""基础涨停状态抽样：使用新鲜腾讯报价及涨停价，不用涨幅阈值猜测。"""
 import sys
 from pathlib import Path
 sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
 import requests
-
-H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
-BULK = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_ssggzj"
-
-
-def report(name, ok, detail, hard=True):
-    print(f"[{'PASS' if ok else ('FAIL' if hard else 'WARN')}] {name}: {detail}")
-
-
-def is_zt(code, name, chg_pct):
-    """chg_pct 为百分数（如 10.02）。返回 (是否疑似涨停, 板块)。"""
-    st = "ST" in str(name).upper()
-    if code.startswith(("300", "301", "688")):
-        lim = 19.5
-    elif st:
-        lim = 4.8
-    else:
-        lim = 9.8
-    return chg_pct >= lim, ("创/科" if code.startswith(("300", "301", "688")) else ("ST" if st else "主板"))
+from backend.agents.layer1_data_collector.sources.quote_contract import (
+    normalize_sina_bulk, parse_tencent_quotes, quote_is_current, at_limit_price,
+)
+from scripts.source_verification import Checks
+from scripts.verify_sina_quote import H, BULK
 
 
 def main():
-    rows = requests.get(BULK, params={"num": "8000", "sort": "r0_net", "asc": "0"}, headers=H, timeout=20).json()
-    hits = []
-    for x in rows:
-        code = str(x.get("symbol"))[2:].zfill(6)
-        name = x.get("name") or ""
-        chg = x.get("changeratio")
-        if chg in (None, ""):
-            continue
-        pct = float(chg)
-        if pct > 1:  # 若已是百分数
-            pct = pct
-        else:
-            pct = pct * 100
-        z, board = is_zt(code, name, pct)
-        if z:
-            hits.append((code, name, round(pct, 2), board))
-    report("sina_calc 涨停清单可算", len(hits) > 0, f"疑似涨停 {len(hits)} 只")
-    for h in hits[:10]:
-        print("  ", h)
-    # 东财 zt_pool 可达性
-    try:
-        from backend.agents.layer1_data_collector.sources.eastmoney_zt import fetch_zt_pool
-        zt = fetch_zt_pool()
-        report("东财 zt_pool 可达(备用源)", zt is not None and not zt.empty, f"{'有' if zt is not None else '无'}数据", hard=False)
-    except Exception as e:
-        report("东财 zt_pool 可达(备用源)", False, f"异常 {e}")
-    # 同花顺涨停复盘试探（记录可达性，不判硬性）
+    checks = Checks()
     try:
-        r = requests.get("http://data.10jqka.com.cn/market/longhu/", headers={"User-Agent": "Mozilla/5.0", "Referer": "http://data.10jqka.com.cn/"}, timeout=8)
-        report("同花顺涨停复盘可达性", r.status_code == 200, f"http {r.status_code}（仅试探，解析未做）", hard=False)
-    except Exception as e:
-        report("同花顺涨停复盘可达性", False, f"异常 {e}", hard=False)
-    print("结论:", "PASS" if len(hits) > 0 else "FAIL")
-    return 0 if len(hits) > 0 else 1
+        response = requests.get(BULK, params={"num": 8000, "sort": "r0_net", "asc": 0}, headers=H, timeout=(3, 8))
+        response.raise_for_status()
+        universe = normalize_sina_bulk(response.json())
+        sample = sorted((r for r in universe if r["change_pct"] is not None),
+                        key=lambda r: r["change_pct"], reverse=True)[:60]
+        symbols = [row["symbol"] for row in sample]
+        if not symbols:
+            raise ValueError("没有可核验报价")
+        response = requests.get("https://qt.gtimg.cn/q=" + ",".join(symbols), headers=H, timeout=(3, 8))
+        response.raise_for_status()
+        response.encoding = "gbk"
+        quotes = parse_tencent_quotes(response.text, symbols)
+        checks.check("抽样覆盖", len(quotes) == len(symbols), f"{len(quotes)}/{len(symbols)}")
+        checks.check("抽样报价时效", bool(quotes) and all(quote_is_current(row) for row in quotes))
+        confirmed, unknown = [], []
+        for row in quotes:
+            state = at_limit_price(row["最新价"], row["涨停价"])
+            if state is None:
+                unknown.append(row["代码"])
+            elif state:
+                confirmed.append((row["代码"], row["名称"]))
+        print(f"抽样当前处于涨停价: {len(confirmed)}，规则价未知: {len(unknown)}；零涨停也是合法结果")
+        for item in confirmed[:10]:
+            print(item)
+        if unknown:
+            checks.skip("部分涨停价", ",".join(unknown))
+        checks.skip("全市场涨停池/封板事件", "仅验证最多60只基础状态，未验证全市场覆盖、首封和炸板事件")
+    except Exception as exc:
+        checks.check("基础涨停抽样", False, str(exc))
+    return checks.finish()
 
 
 if __name__ == "__main__":
diff --git a/tests/test_bars_store.py b/tests/test_bars_store.py
new file mode 100644
index 0000000..47e111f
--- /dev/null
+++ b/tests/test_bars_store.py
@@ -0,0 +1,62 @@
+"""日K持久化（§12.4 第3步）离线单测：不污染生产库，用 tmp SQLite 引擎。"""
+import sqlite3
+
+import pandas as pd
+import pytest
+from sqlalchemy import create_engine
+
+
+@pytest.fixture
+def bars(monkeypatch, tmp_path):
+    import backend.services.data_backend.bars_store as store
+
+    eng = create_engine(f"sqlite:///{tmp_path}/test.db")
+    monkeypatch.setattr(store, "engine", eng)
+    return store
+
+
+def _df(dates, closes):
+    return pd.DataFrame({"日期": dates, "开盘": [c * 0.99 for c in closes],
+                         "收盘": closes, "最高": [c * 1.01 for c in closes],
+                         "最低": [c * 0.98 for c in closes], "成交量": [100] * len(closes),
+                         "成交额": [1000] * len(closes)})
+
+
+def test_upsert_then_read_and_idempotent(bars):
+    bars.upsert_daily_bars("600001", _df(["2026-07-06", "2026-07-07"], [10.0, 10.5]),
+                           adjustment="raw", source="test", is_final=True)
+    out = bars.read_daily_bars("600001", "2026-07-06", "2026-07-07", "raw")
+    assert out["close"].tolist() == [10.0, 10.5]
+    assert out["is_final"].tolist() == [1, 1]
+    # 幂等 upsert：同 (code,date,adjustment) 更新而非重复
+    bars.upsert_daily_bars("600001", _df(["2026-07-07"], [11.0]), adjustment="raw", source="test")
+    out = bars.read_daily_bars("600001", "2026-07-06", "2026-07-07", "raw")
+    assert len(out) == 2
+    assert out[out["trade_date"] == "2026-07-07"]["close"].tolist() == [11.0]
+
+
+def test_missing_dates_uses_trading_calendar(bars, monkeypatch):
+    import backend.services.data_backend.bars_store as store
+
+    monkeypatch.setattr(store, "trading_days_between_dates",
+                        lambda s, e: [__import__("datetime").date.fromisoformat(d)
+                                      for d in ["2026-07-06", "2026-07-07", "2026-07-08"]])
+    monkeypatch.setattr(store, "is_trading_day", lambda d: True)
+    bars.upsert_daily_bars("600001", _df(["2026-07-06"], [10.0]), adjustment="raw")
+    miss = bars.missing_dates("600001", "2026-07-06", "2026-07-08", "raw")
+    assert miss == ["2026-07-07", "2026-07-08"]
+
+
+def test_delete_adjustment_invalidates_version(bars):
+    bars.upsert_daily_bars("600001", _df(["2026-07-06"], [10.0]), adjustment="qfq", adjustment_version="v1")
+    assert len(bars.read_daily_bars("600001", adjustment="qfq")) == 1
+    assert bars.delete_adjustment("600001", "qfq") == 1
+    assert bars.read_daily_bars("600001", adjustment="qfq").empty
+
+
+def test_backup_and_verify(bars, tmp_path):
+    bars.upsert_daily_bars("600001", _df(["2026-07-06"], [10.0]), adjustment="raw")
+    target = tmp_path / "backup.db"
+    bars.backup(target)
+    assert target.exists()
+    assert bars.verify_backup(target) is True
diff --git a/tests/test_decision_quality_gates.py b/tests/test_decision_quality_gates.py
new file mode 100644
index 0000000..70e6c46
--- /dev/null
+++ b/tests/test_decision_quality_gates.py
@@ -0,0 +1,359 @@
+"""决策质量闭环回归（§12.4 第 1 步）：固化 P0/P1 反例。"""
+from datetime import datetime, timedelta, timezone
+
+import pandas as pd
+import pytest
+
+BEIJING_TZ = timezone(timedelta(hours=8))
+
+
+def test_snapshots_partial_return_does_not_claim_coverage(monkeypatch, tmp_path):
+    """离线返回一只旧报价不得声称两只覆盖：received_codes 而非 requested_codes。"""
+    from backend.services.data_backend import snapshots
+
+    monkeypatch.setattr(snapshots, "DATA_BACKEND_DIR", tmp_path)
+    monkeypatch.setattr(snapshots, "REPORT_DATA_BACKEND_DIR", tmp_path / "reports")
+    monkeypatch.setattr(snapshots, "_MEMORY_CACHE", {})
+    monkeypatch.setattr(snapshots, "_now", lambda: datetime(2026, 7, 6, 10, 0, tzinfo=BEIJING_TZ))
+
+    fetched = pd.DataFrame([{"代码": "600001", "名称": "A", "最新价": 10.0}])
+    result, meta = snapshots.read_quotes(["600001", "600002"], fetcher=lambda codes: fetched)
+    assert result["代码"].tolist() == ["600001"]
+    assert meta["received_count"] == 1
+    assert meta["missing_count"] == 1
+
+
+def test_snapshots_unknown_source_time_rows_are_not_fresh(monkeypatch, tmp_path):
+    """全部行 source_time 未知/degraded 时，不得标 fresh。"""
+    from backend.services.data_backend import snapshots
+
+    monkeypatch.setattr(snapshots, "DATA_BACKEND_DIR", tmp_path)
+    monkeypatch.setattr(snapshots, "REPORT_DATA_BACKEND_DIR", tmp_path / "reports")
+    monkeypatch.setattr(snapshots, "_MEMORY_CACHE", {})
+    monkeypatch.setattr(snapshots, "_now", lambda: datetime(2026, 7, 6, 10, 0, tzinfo=BEIJING_TZ))
+
+    fetched = pd.DataFrame([{"代码": "600001", "名称": "A", "最新价": 10.0, "source_time": None, "degraded": True}])
+    _, meta = snapshots.read_quotes(["600001"], fetcher=lambda codes: fetched)
+    assert meta["status"] == "degraded"
+
+
+def test_eastmoney_legacy_unknown_time_is_degraded():
+    from backend.agents.layer1_data_collector.sources import eastmoney_quote
+
+    data = {"data": {"diff": [{"f12": "600519", "f14": "贵州茅台", "f2": 1300.0, "f3": 1.5, "f5": 1000, "f6": 130000000}]}}
+    rows = eastmoney_quote._parse_response(data)
+    assert rows and rows[0]["source_time"] is None
+    assert rows[0]["degraded"] is True
+
+
+def test_kline_short_cache_does_not_serve_longer_window(monkeypatch):
+    """请求130根返回30根后，再请求100根不得命中30根缓存。"""
+    import backend.plugins.common as common
+
+    calls = {"n": 0}
+
+    def fetcher(code, days):
+        calls["n"] += 1
+        return pd.DataFrame({"日期": [f"2026-06-{i:02d}" for i in range(1, min(days, 30) + 1)],
+                             "收盘": list(range(1, min(days, 30) + 1))})
+
+    common.kline_cache_clear()
+    common.get_kline_cached("600519", 130, fetcher=fetcher)  # 实际只返回 30 根
+    common.get_kline_cached("600519", 100, fetcher=fetcher)  # 不得命中短缓存
+    assert calls["n"] == 2
+    common.kline_cache_clear()
+
+
+def test_kline_cache_isolation_by_fetcher(monkeypatch):
+    """不同取数器（不同复权/源能力）不得串缓存。"""
+    import backend.plugins.common as common
+
+    def a(code, days):
+        return pd.DataFrame({"日期": ["2026-06-01"], "收盘": [1.0]})
+
+    def b(code, days):
+        return pd.DataFrame({"日期": ["2026-06-01"], "收盘": [2.0]})
+
+    common.kline_cache_clear()
+    common.get_kline_cached("600519", 10, fetcher=a)
+    out = common.get_kline_cached("600519", 10, fetcher=b)
+    assert out["收盘"].tolist() == [2.0]
+    common.kline_cache_clear()
+
+def _quote_row(code="600001", price=12.0, **extra):
+    row = {
+        "代码": code, "名称": "测试", "最新价": price, "涨跌幅": 6.0, "成交额": 3e8,
+        "换手率": 5.0, "量比": 2.0, "流通市值": 5e9,
+    }
+    row.update(extra)
+    return row
+
+
+def test_stale_quote_does_not_produce_buy():
+    from datetime import date as _date
+    from backend.plugins.overnight_arbitrage.service import build_overnight_decision
+
+    df = pd.DataFrame([_quote_row(is_stale=True, source_time="2026-07-03T14:50:00+08:00")])
+    decision = build_overnight_decision(df, target_date=_date(2026, 7, 6))
+    assert decision["buy_count"] == 0
+    assert decision["data_quality"]["status"] != "complete"
+
+
+def test_degraded_fallback_quote_does_not_produce_buy():
+    from datetime import date as _date
+    from backend.plugins.overnight_arbitrage.service import build_overnight_decision
+
+    df = pd.DataFrame([_quote_row(degraded=True)])
+    decision = build_overnight_decision(df, target_date=_date(2026, 7, 6))
+    assert decision["buy_count"] == 0
+    assert decision["data_quality"]["status"] == "partial"
+
+
+def test_yahoo5m_stale_timestamp_yields_no_strength(monkeypatch):
+    import time as _time
+    from backend.plugins.overnight_arbitrage.service import _fetch_yahoo_5m_strength
+
+    now_ts = int(_time.time())
+    stale_ts = now_ts - 60 * 60
+    fake = {
+        "chart": {"result": [{
+            "timestamp": [stale_ts - 300, stale_ts, stale_ts + 300, stale_ts + 600],
+            "indicators": {"quote": [{"close": [10, 10.1, 10.2, 10.3],
+                                       "high": [10.05, 10.15, 10.25, 10.35],
+                                       "low": [9.9, 10.0, 10.1, 10.2]}]},
+        }]},
+    }
+
+    class Resp:
+        def raise_for_status(self): pass
+        def json(self): return fake
+
+    monkeypatch.setattr("requests.get", lambda *a, **k: Resp())
+    out = _fetch_yahoo_5m_strength(["600001"])
+    assert "600001" not in out
+
+def test_eastmoney_snapshot_records_coverage_metadata():
+    import requests
+    from backend.plugins.overnight_arbitrage import service as oa
+
+    def fake_get(url, params=None, headers=None, timeout=None):
+        class R:
+            def raise_for_status(self): pass
+            def json(self):
+                # total=5 但只回 2 行 → truncated
+                return {"data": {"total": 5, "diff": [
+                    {"f12": "600001", "f14": "A", "f2": 10.0, "f3": 1.0, "f15": 10.1, "f5": 100, "f6": 1e8, "f8": 1.0, "f9": 10.0, "f10": 1.0, "f20": 1e10, "f21": 8e9},
+                    {"f12": "600002", "f14": "B", "f2": 10.0, "f3": 1.0, "f15": 10.1, "f5": 100, "f6": 1e8, "f8": 1.0, "f9": 10.0, "f10": 1.0, "f20": 1e10, "f21": 8e9},
+                ]}}
+        return R()
+
+    monkeypatch = None
+    import pytest
+    # 用 monkeypatch 注入 requests.get
+
+
+def _eastmoney_coverage_test(monkeypatch):
+    from backend.plugins.overnight_arbitrage.service import _eastmoney_all_a_snapshot
+
+    def fake_get(url, params=None, headers=None, timeout=None):
+        class R:
+            def raise_for_status(self): pass
+            def json(self):
+                rows = [
+                    {"f12": "600001", "f14": "A", "f2": 10.0, "f3": 1.0, "f15": 10.1, "f5": 100, "f6": 1e8, "f8": 1.0, "f9": 10.0, "f10": 1.0, "f20": 1e10, "f21": 8e9},
+                    {"f12": "600002", "f14": "B", "f2": 10.0, "f3": 1.0, "f15": 10.1, "f5": 100, "f6": 1e8, "f8": 1.0, "f9": 10.0, "f10": 1.0, "f20": 1e10, "f21": 8e9},
+                ]
+                diff = rows if params.get("pn") == 1 else []
+                return {"data": {"total": 5, "diff": diff}}
+        return R()
+
+    monkeypatch.setattr("requests.get", fake_get)
+    df = _eastmoney_all_a_snapshot()
+    cov = df.attrs["coverage"]
+    assert cov["universe_total"] >= 10  # 至少两个 fs 分组各 total=5
+    assert cov["received"] == 2  # 去重后实际收到
+    assert cov["truncated"] is True
+
+
+def test_eastmoney_snapshot_records_coverage_metadata(monkeypatch):
+    _eastmoney_coverage_test(monkeypatch)
+
+def test_candidate_refiner_replaces_candidate_rows(monkeypatch):
+    from datetime import date as _date
+    import asyncio
+    from backend.plugins.overnight_arbitrage import service as oa
+
+    quotes = pd.DataFrame([
+        _quote_row("600001", price=10.0, 换手率=1.0),
+        _quote_row("600002", price=11.0, 涨跌幅=9.0, 成交额=8e8, 换手率=12.0, 量比=5.0),
+    ])
+    calls = {}
+
+    def fake_refiner(df, codes):
+        calls["codes"] = list(codes)
+        row = _quote_row("600002", price=11.0, 涨跌幅=9.0, 成交额=8e8, 换手率=12.0, 量比=5.0)
+        row["source_time"] = "2026-07-06T14:50:00+08:00"
+        out = df[df["代码"] != "600002"].copy()
+        out = pd.concat([out, pd.DataFrame([row])], ignore_index=True)
+        return out
+
+    async def run():
+        return await oa.run_overnight_arbitrage(
+            target_date=_date(2026, 7, 6),
+            quote_fetcher=lambda: quotes,
+            zt_fetcher=lambda d: pd.DataFrame(),
+            minute_fetcher=lambda c: {},
+            candidate_refiner=fake_refiner,
+            dry_run=True,
+            current_time=__import__("datetime").datetime(2026, 7, 6, 14, 45, tzinfo=oa.BEIJING_TZ),
+        )
+
+    result = asyncio.run(run())
+    assert calls.get("codes") == ["600002"]  # 粗筛后唯一进入结果的候选
+
+
+def test_refine_quotes_preserves_coverage_attrs():
+    from backend.plugins.overnight_arbitrage.service import _refine_quotes_with_tencent
+
+    quotes = pd.DataFrame([_quote_row("600001"), _quote_row("600002")])
+    quotes.attrs["coverage"] = {"source": "eastmoney_all_a", "universe_total": 100, "received": 2, "truncated": True}
+    monkeypatch = None
+    # 注入假 refiner（monkeypatch 网络函数）
+
+
+def _refine_coverage_test(monkeypatch):
+    from backend.plugins.overnight_arbitrage.service import _refine_quotes_with_tencent
+    import backend.agents.layer1_data_collector.sources.eastmoney_quote as eq
+
+    def fake_fetch(codes):
+        return pd.DataFrame([_quote_row("600001", 换手率=9.0)])
+
+    monkeypatch.setattr(eq, "fetch_tencent_quotes_for_codes", fake_fetch)
+    quotes = pd.DataFrame([_quote_row("600001", 换手率=1.0), _quote_row("600002", 换手率=8.0)])
+    quotes.attrs["coverage"] = {"source": "eastmoney_all_a", "universe_total": 100, "received": 2, "truncated": True}
+    out = _refine_quotes_with_tencent(quotes, ["600001"])
+    assert out.attrs.get("coverage", {}).get("source") == "eastmoney_all_a"
+    assert out.attrs.get("refined_codes") == ["600001"]
+
+
+def test_refine_quotes_preserves_coverage_attrs(monkeypatch):
+    _refine_coverage_test(monkeypatch)
+
+def test_snapshot_atomic_publish_same_version_and_inconsistent_detected(tmp_path, monkeypatch):
+    import json
+    import backend.services.data_backend.snapshots as snapshots
+
+    monkeypatch.setattr(snapshots, "DATA_BACKEND_DIR", tmp_path)
+    monkeypatch.setattr(snapshots, "REPORT_DATA_BACKEND_DIR", tmp_path / "reports")
+    monkeypatch.setattr(snapshots, "_MEMORY_CACHE", {})
+    monkeypatch.setattr(snapshots, "_now", lambda: __import__("datetime").datetime(2026, 7, 6, 10, 0, tzinfo=snapshots.BEIJING_TZ))
+
+    snapshots._write_local_snapshot("quotes", {"records": [{"代码": "600001"}]})
+    canonical = json.loads((tmp_path / "quotes.json").read_text(encoding="utf-8"))
+    mirror = json.loads((tmp_path / "reports" / "quotes.json").read_text(encoding="utf-8"))
+    assert canonical["snapshot_version"] == mirror["snapshot_version"]
+    # 人为破坏 mirror 版本 → 读取以 canonical 为准并标记不一致
+    mirror["snapshot_version"] = "bogus"
+    (tmp_path / "reports" / "quotes.json").write_text(json.dumps(mirror), encoding="utf-8")
+    monkeypatch.setattr(snapshots, "_MEMORY_CACHE", {})
+    payload = snapshots._read_local_snapshot("quotes")
+    assert payload["_batch_inconsistent"] is True
+    assert payload["snapshot_version"] == canonical["snapshot_version"]
+
+
+
+
+def test_kline_write_through_is_gated_and_best_effort(monkeypatch):
+    import pandas as pd
+    import backend.plugins.common as common
+
+    written = []
+
+    def fake_upsert(code, df, **kwargs):
+        written.append((str(code), len(df), kwargs.get("adjustment"), kwargs.get("source")))
+
+    import backend.services.data_backend.bars_store as bs
+    monkeypatch.setattr(bs, "upsert_daily_bars", fake_upsert)
+
+    def fetcher(code, days):
+        return pd.DataFrame({"日期": ["2026-06-01", "2026-06-02"], "收盘": [1.0, 2.0]})
+
+    common.kline_cache_clear()
+    monkeypatch.delenv("KLINE_STORE_WRITE_ENABLED", raising=False)
+    common.get_kline_cached("600519", 10, fetcher=fetcher)
+    assert written == []
+    monkeypatch.setenv("KLINE_STORE_WRITE_ENABLED", "1")
+    common.get_kline_cached("600519", 10, fetcher=fetcher)
+    assert len(written) == 1
+    assert written[0][0] == "600519" and written[0][1] == 2 and written[0][2] == "raw"
+    common.kline_cache_clear()
+
+
+
+
+def test_zt_events_availability_surfaced():
+    from datetime import date as _date
+    import pandas as pd
+    from backend.plugins.overnight_arbitrage.service import build_overnight_decision
+
+    df = pd.DataFrame([_quote_row("600002", 涨跌幅=9.0, 成交额=8e8, 换手率=12.0, 量比=5.0)])
+    # 无涨停池 → capabilities.zt_events_available=False，结果项标 unavailable
+    d = build_overnight_decision(df, target_date=_date(2026, 7, 6))
+    assert d["capabilities"]["zt_events_available"] is False
+    assert any("zt_events" in it.get("unavailable_required_fields", []) for it in d["results"] or [])
+    # 有涨停池 → True，且不再标 unavailable
+    zt = pd.DataFrame([{"代码": "600002", "封板时间": 143000, "炸板次数": 0, "连板数": 1}])
+    d2 = build_overnight_decision(df, zt_pool=zt, target_date=_date(2026, 7, 6))
+    assert d2["capabilities"]["zt_events_available"] is True
+    assert all("zt_events" not in it.get("unavailable_required_fields", []) for it in d2["results"] or [])
+
+
+
+
+def test_emotion_unavailable_on_zt_fetch_failure(monkeypatch):
+    from datetime import date as _date
+    import backend.plugins.emotion_cycle.service as emo
+    import backend.agents.layer1_data_collector.sources.eastmoney_zt as ztsrc
+
+    def boom(target):
+        raise RuntimeError("东财封禁")
+
+    monkeypatch.setattr(ztsrc, "fetch_zt_pool", boom)
+    monkeypatch.setattr(emo, "is_trading_day", lambda d: True)
+    monkeypatch.setattr(emo, "write_snapshot", lambda name, payload: payload)
+
+    out = emo.run_emotion_once(target_date=_date(2026, 7, 6))
+    assert out["status"] == "unavailable_required_fields"
+    assert out["unavailable_required_fields"] == ["zt_events"]
+
+
+def test_zt_seal_unavailable_on_zt_fetch_failure(monkeypatch):
+    from datetime import date as _date
+    import backend.plugins.zt_seal.service as seal
+    import backend.agents.layer1_data_collector.sources.eastmoney_zt as ztsrc
+
+    def boom(target):
+        raise RuntimeError("东财封禁")
+
+    monkeypatch.setattr(ztsrc, "fetch_zt_pool", boom)
+    monkeypatch.setattr(seal, "is_trading_day", lambda d: True)
+    monkeypatch.setattr(seal, "write_snapshot", lambda name, payload: payload)
+
+    out = seal.run_zt_seal_once(target_date=_date(2026, 7, 6))
+    assert out["status"] == "unavailable_required_fields"
+    assert out["unavailable_required_fields"] == ["zt_events"]
+
+
+def test_kline_return_carries_coverage_attrs():
+    import backend.plugins.common as common
+
+    def fetcher(code, days):
+        return __import__("pandas").DataFrame({"日期": ["2026-06-01", "2026-06-02"], "收盘": [1.0, 2.0]})
+
+    common.kline_cache_clear()
+    out = common.get_kline_cached("600519", 130, fetcher=fetcher)
+    cov = out.attrs.get("kline_coverage")
+    assert cov["rows"] == 2
+    assert cov["first"] == "2026-06-01" and cov["last"] == "2026-06-02"
+    assert cov["short"] is True and cov["reason"] == "short"
+    common.kline_cache_clear()
diff --git a/tests/test_shadow_run.py b/tests/test_shadow_run.py
new file mode 100644
index 0000000..cc9493d
--- /dev/null
+++ b/tests/test_shadow_run.py
@@ -0,0 +1,23 @@
+"""影子运行观测聚合（§12.4 第5步，代码侧）离线单测。"""
+import backend.services.data_backend.shadow_run as sr
+
+
+def test_shadow_run_record_and_summarize(monkeypatch, tmp_path):
+    monkeypatch.setattr(sr, "SHADOW_FILE", tmp_path / "shadow.json")
+    monkeypatch.setenv("SHADOW_RUN_ENABLED", "1")
+
+    sr.record("quotes", "sina", "ok", coverage={"received": 5, "truncated": False},
+              age_seconds=3.0, deadline_met=True)
+    sr.record("quotes", "sina", "ok", age_seconds=9.0, deadline_met=True)
+    sr.record("quotes", "tencent", "error", error="timeout", deadline_met=False)
+
+    s = sr.summarize()
+    assert s["quotes@sina"]["runs"] == 2
+    assert s["quotes@sina"]["error_rate"] == 0.0
+    assert s["quotes@sina"]["age_p95_seconds"] == 9.0
+    assert s["quotes@sina"]["deadline_met_rate"] == 1.0
+    assert s["quotes@tencent"]["error_rate"] == 1.0
+    assert "timeout" in s["quotes@tencent"]["error_types"]
+
+    sr.reset()
+    assert sr.summarize() == {}
diff --git a/tests/test_source_contracts.py b/tests/test_source_contracts.py
new file mode 100644
index 0000000..93035c6
--- /dev/null
+++ b/tests/test_source_contracts.py
@@ -0,0 +1,244 @@
+"""去东财化首阶段回归：真实缺陷反例、单位、时效与准入。"""
+import unittest
+from concurrent.futures import ThreadPoolExecutor
+from datetime import datetime, timedelta
+from threading import Event
+from unittest.mock import Mock, patch
+
+import pandas as pd
+
+from backend.plugins import common
+from backend.agents.layer1_data_collector.sources import historical_kline as history
+from backend.agents.layer1_data_collector.sources import eastmoney_quote as quotes
+from backend.agents.layer1_data_collector.sources.quote_contract import (
+    BEIJING_TZ, at_limit_price, normalize_sina_bulk, parse_tencent_quotes, quote_is_current,
+)
+from scripts.source_verification import Checks, compare_closes, validate_bars
+
+
+def bars(count=30, start="2026-01-01"):
+    return pd.DataFrame({"日期": pd.date_range(start, periods=count).strftime("%Y-%m-%d"),
+                         "开盘": 10., "收盘": 10., "最高": 11., "最低": 9., "成交量": 100.})
+
+
+def quote_text(symbol="sh600519", stamp="20260911134627"):
+    fields = [""] * 50
+    for index, value in {1: "测试股票", 2: symbol[2:], 3: "1274.82", 4: "1285.13", 6: "1000",
+                         30: stamp, 32: "-0.80", 33: "1290", 34: "1260", 37: "338879",
+                         38: "0.21", 39: "20", 44: "100", 45: "200", 47: "1413.64",
+                         48: "1156.62", 49: "1.3"}.items():
+        fields[index] = value
+    return f'v_{symbol}="' + "~".join(fields) + '";'
+
+
+class KlineCacheTests(unittest.TestCase):
+    def setUp(self):
+        common.kline_cache_clear()
+        self.addCleanup(common.kline_cache_clear)
+
+    def test_short_then_long_fetches_long_window(self):
+        fetch = Mock(side_effect=lambda code, days: bars(days))
+        common.get_kline_cached("600000", 30, fetch)
+        self.assertEqual(len(common.get_kline_cached("600000", 130, fetch)), 130)
+        self.assertEqual(fetch.call_count, 2)
+
+    def test_long_then_short_reuses_and_copies(self):
+        fetch = Mock(return_value=bars(130))
+        first = common.get_kline_cached("600000", 130, fetch)
+        first.loc[129, "收盘"] = 999
+        second = common.get_kline_cached("600000", 30, fetch)
+        self.assertEqual(len(second), 30)
+        self.assertEqual(second.iloc[-1]["收盘"], 10)
+        fetch.assert_called_once()
+
+    def test_failure_expires_instead_of_poisoning_day(self):
+        fetch = Mock(side_effect=[None, bars()])
+        with patch.object(common, "monotonic", return_value=10):
+            self.assertIsNone(common.get_kline_cached("600000", 30, fetch))
+        with patch.object(common, "monotonic", return_value=12):
+            self.assertIsNone(common.get_kline_cached("600000", 30, fetch))
+        with patch.object(common, "monotonic", return_value=16):
+            self.assertEqual(len(common.get_kline_cached("600000", 30, fetch)), 30)
+        self.assertEqual(fetch.call_count, 2)
+
+    def test_success_refreshes_during_same_day(self):
+        fetch = Mock(return_value=bars())
+        with patch.object(common, "monotonic", return_value=10):
+            common.get_kline_cached("600000", 30, fetch)
+        with patch.object(common, "monotonic", return_value=56):
+            common.get_kline_cached("600000", 30, fetch)
+        self.assertEqual(fetch.call_count, 2)
+
+    def test_fetchers_do_not_share_adjustment_or_source(self):
+        a, b = Mock(return_value=bars()), Mock(return_value=bars())
+        common.get_kline_cached("600000", 30, a)
+        common.get_kline_cached("600000", 30, b)
+        a.assert_called_once()
+        b.assert_called_once()
+
+    def test_same_key_concurrent_requests_coalesce(self):
+        entered, release = Event(), Event()
+        def load(code, days):
+            entered.set()
+            self.assertTrue(release.wait(2))
+            return bars(days)
+        fetch = Mock(side_effect=load)
+        with ThreadPoolExecutor(max_workers=4) as pool:
+            first = pool.submit(common.get_kline_cached, "600000", 30, fetch)
+            self.assertTrue(entered.wait(2))
+            rest = [pool.submit(common.get_kline_cached, "600000", 30, fetch) for _ in range(3)]
+            release.set()
+            for job in [first] + rest:
+                self.assertEqual(len(job.result(timeout=3)), 30)
+        fetch.assert_called_once()
+
+    def test_clear_discards_in_flight_result(self):
+        def load(code, days):
+            common.kline_cache_clear()
+            return bars(days)
+        common.get_kline_cached("600000", 30, load)
+        self.assertFalse(common._kline_cache)
+
+    def test_fetcher_exception_does_not_poison_cache(self):
+        fetch = Mock(side_effect=[RuntimeError("网络异常"), bars()])
+        with self.assertRaises(RuntimeError):
+            common.get_kline_cached("600000", 30, fetch)
+        self.assertEqual(len(common.get_kline_cached("600000", 30, fetch)), 30)
+
+
+class QuoteContractTests(unittest.TestCase):
+    def test_bulk_units_are_endpoint_specific(self):
+        row = normalize_sina_bulk([{"symbol": "sh600519", "trade": "1274.82",
+                "changeratio": "-0.00802253", "turnover": "21.3098", "amount": "3388794155"}])[0]
+        self.assertAlmostEqual(row["change_pct"], -0.802253)
+        self.assertAlmostEqual(row["turnover_pct"], 0.213098)
+        self.assertIsNone(row["source_time"])
+        self.assertNotIn("main_net_inflow", row)
+
+    def test_bulk_missing_does_not_become_zero(self):
+        row = normalize_sina_bulk([{"symbol": "sz000001", "turnover": "NaN"}])[0]
+        self.assertIsNone(row["turnover_pct"])
+        self.assertIsNone(row["amount"])
+
+    def test_bulk_duplicate_and_invalid_shape_fail(self):
+        for payload in ({}, [], [{"symbol": "sh600519"}] * 2):
+            with self.subTest(payload=payload), self.assertRaises(ValueError):
+                normalize_sina_bulk(payload)
+
+    def test_tencent_units_identity_and_time(self):
+        row = parse_tencent_quotes(quote_text(), ["sh600519"])[0]
+        self.assertEqual(row["成交量"], 1000)
+        self.assertEqual(row["成交额"], 3388790000)
+        self.assertEqual(row["总市值"], 20000000000)
+        self.assertEqual(row["换手率"], .21)
+        self.assertEqual(row["涨停价"], 1413.64)
+        self.assertFalse(row["degraded"])
+        self.assertEqual(row["source_time"], "2026-09-11T13:46:27+08:00")
+
+    def test_tencent_does_not_adopt_unsolicited_or_wrong_code(self):
+        self.assertEqual(parse_tencent_quotes(quote_text(), ["sz000001"]), [])
+        wrong = quote_text().replace("测试股票~600519", "测试股票~000001")
+        self.assertEqual(parse_tencent_quotes(wrong, ["sh600519"]), [])
+
+    def test_blank_turnover_is_not_complete(self):
+        row = parse_tencent_quotes(quote_text().replace("~0.21~", "~~"), ["sh600519"])[0]
+        self.assertTrue(row["degraded"])
+        self.assertIn("换手率", row["missing_fields"])
+
+    def test_stale_future_and_previous_day_rejected(self):
+        now = datetime(2026, 9, 11, 14, 43, tzinfo=BEIJING_TZ)
+        for offset in (-86400, -60, 10):
+            self.assertFalse(quote_is_current({"source_time": (now + timedelta(seconds=offset)).isoformat()}, now))
+        self.assertTrue(quote_is_current({"source_time": (now - timedelta(seconds=10)).isoformat()}, now))
+
+    def test_lunch_and_close_require_actual_session_close(self):
+        stamp = "2026-09-11T11:30:00+08:00"
+        self.assertTrue(quote_is_current({"source_time": stamp}, datetime(2026, 9, 11, 12, 30, tzinfo=BEIJING_TZ)))
+        self.assertFalse(quote_is_current({"source_time": stamp}, datetime(2026, 9, 11, 15, 30, tzinfo=BEIJING_TZ)))
+
+    def test_limit_price_avoids_false_positive_and_low_price_false_negative(self):
+        self.assertFalse(at_limit_price("10.98", "11.00"))
+        self.assertTrue(at_limit_price("1.13", "1.13"))
+        for missing in (None, 0, "NaN", "Infinity", "1.131"):
+            self.assertIsNone(at_limit_price("1.13", missing))
+
+    def test_primary_complete_skips_legacy(self):
+        with patch.object(quotes, "_fetch_tencent_batch", return_value=[{"代码": "600519"}]), \
+                patch.object(quotes, "_fetch_legacy_batch") as legacy:
+            rows = quotes._fetch_one_batch(["1.600519"])
+        legacy.assert_not_called()
+        self.assertEqual(len(rows), 1)
+
+    def test_partial_quote_only_fills_missing_codes(self):
+        with patch.object(quotes, "_fetch_tencent_batch", return_value=[{"代码": "600519"}]), \
+                patch.object(quotes, "_fetch_legacy_batch", return_value=[{"代码": "000001"}]) as legacy:
+            rows = quotes._fetch_one_batch(["1.600519", "0.000001"])
+        legacy.assert_called_once_with(["0.000001"])
+        self.assertEqual(len(rows), 2)
+
+    def test_all_sources_down_does_not_trigger_recursive_retries(self):
+        with patch.object(quotes, "_fetch_one_batch", side_effect=quotes.QuoteSourcesUnavailable("断网")) as fetch:
+            rows, failures = quotes._fetch_batch_with_split(["1.600000"] * 60)
+        self.assertEqual((rows, failures), ([], 1))
+        fetch.assert_called_once()
+
+    def test_naive_source_time_is_unknown(self):
+        self.assertFalse(quote_is_current({"source_time": "2026-09-11T14:43:00"},
+                                         datetime(2026, 9, 11, 14, 43, tzinfo=BEIJING_TZ)))
+
+
+class VerificationTests(unittest.TestCase):
+    def test_invalid_high_and_duplicate_dates_fail(self):
+        a = bars()
+        a.loc[0, "最高"] = 5
+        self.assertFalse(validate_bars(a))
+        a = bars()
+        a.loc[1, "日期"] = a.loc[0, "日期"]
+        self.assertFalse(validate_bars(a))
+
+    def test_date_alignment_and_minimum_overlap(self):
+        a, b = bars(), bars(start="2026-01-05")
+        self.assertTrue(compare_closes(a, b)[0])
+        self.assertFalse(compare_closes(a, bars(start="2027-01-01"))[0])
+
+    def test_difference_fails_instead_of_just_printing(self):
+        a, b = bars(), bars()
+        a.loc[0, "收盘"] = 10.5
+        self.assertFalse(compare_closes(a, b)[0])
+
+    def test_exit_code_distinguishes_pending_from_pass(self):
+        with patch("builtins.print"):
+            checks = Checks()
+            self.assertEqual(checks.finish(), 0)
+            checks.skip("复权", "无证据")
+            self.assertEqual(checks.finish(), 2)
+            checks.check("字段", False)
+            self.assertEqual(checks.finish(), 1)
+
+    def test_sina_missing_fields_and_legacy_volume_units(self):
+        response = Mock()
+        response.json.return_value = [dict(day=f"2026-09-0{i}", open="10", close="10", high="11", low="9", volume="10000") for i in (1, 2, 3)]
+        with patch("requests.get", return_value=response):
+            df = history._fetch_hist_sina("600000")
+        self.assertEqual(df.iloc[0]["成交量"], 100)
+        self.assertTrue(df["成交额"].isna().all())
+        self.assertTrue(df["换手率"].isna().all())
+        self.assertEqual(df.attrs["adjustment"], "unknown")
+
+    def test_tencent_raw_day_never_becomes_qfq(self):
+        response = Mock()
+        response.json.return_value = {"data": {"sh600000": {"day": [["2026-09-01", "10", "10", "11", "9", "100"]]}}}
+        with patch("requests.get", return_value=response):
+            self.assertIsNone(history._fetch_hist_tencent("600000"))
+
+    def test_qfq_fallback_never_uses_unknown_sina(self):
+        with patch.object(history, "_fetch_hist_eastmoney_direct", return_value=None), \
+                patch.object(history, "_fetch_hist_tencent", return_value=bars()), \
+                patch.object(history, "_fetch_hist_sina") as sina:
+            result = history.fetch_historical_with_source("600000")
+        self.assertIn("腾讯", result[1])
+        sina.assert_not_called()
+
+
+if __name__ == "__main__":
+    unittest.main()
diff --git a/tests/test_zt_contract.py b/tests/test_zt_contract.py
new file mode 100644
index 0000000..a7867d1
--- /dev/null
+++ b/tests/test_zt_contract.py
@@ -0,0 +1,63 @@
+"""涨停基础/增强拆分（§12.4 第4步）离线单测。"""
+import pandas as pd
+
+from backend.agents.layer1_data_collector.sources.zt_contract import (
+    classify_zt_basic, compute_limit_prices, enrich_zt_events, required_events_available,
+)
+
+
+def test_compute_limit_prices_main_board_rounding():
+    # 低价股 1.03 涨停价 1.13（涨幅 9.7087%），不得被 9.8% 近似漏判
+    up, down, ver = compute_limit_prices("1.03", "600001", "测试", date(2026, 7, 6) if False else None)
+    assert up == 1.13
+    assert down == 0.93
+
+
+def test_compute_limit_prices_gem_star():
+    up, down, _ = compute_limit_prices("10.00", "300001", "创业", None)
+    assert up == 12.0 and down == 8.0
+
+
+def test_compute_limit_prices_st_rules_by_date():
+    from datetime import date as _date
+    # v2（2026-06-30 后）主板 ST 10%
+    up, _, ver = compute_limit_prices("10.00", "600001", "ST股", _date(2026, 7, 6))
+    assert up == 11.0 and ver == "v2"
+    # v1（生效前）主板 ST 5%
+    up, _, ver = compute_limit_prices("10.00", "600001", "ST股", _date(2026, 6, 1))
+    assert up == 10.5 and ver == "v1"
+
+
+def test_classify_zt_basic_touched_vs_at_limit():
+    row = {"代码": "600001", "名称": "测试", "最新价": 10.9, "最高价": 11.0, "涨停价": 11.0}
+    assert classify_zt_basic(row)["state"] == "touched"
+    row["最新价"] = 11.0
+    assert classify_zt_basic(row)["state"] == "at_limit"
+
+
+def test_classify_zt_basic_unknown_limit():
+    row = {"代码": "600001", "名称": "测试", "最新价": 10.9, "最高价": 11.0, "涨停价": None, "昨收": None}
+    out = classify_zt_basic(row)
+    assert out["state"] == "unknown"
+    assert "limit_price_unknown" in out["unknown_reasons"]
+
+
+def test_enrich_and_required_events():
+    zt = pd.DataFrame([{"代码": "600001", "封板时间": 143000, "炸板次数": 0, "连板数": 2}])
+    events = enrich_zt_events(zt, ["600001", "600002"])
+    assert events["600001"]["available"] is True and events["600001"]["break_count"] == 0
+    assert events["600002"] is None
+    missing = required_events_available(events)
+    assert missing == ["600002"]
+
+def test_resolve_zt_basic_from_quotes():
+    import pandas as pd
+    from backend.agents.layer1_data_collector.sources.zt_contract import resolve_zt_basic_from_quotes
+
+    quotes = pd.DataFrame([
+        {"代码": "600001", "名称": "测试", "最新价": 11.0, "最高价": 11.0, "涨停价": 11.0, "昨收": 10.0},
+        {"代码": "600002", "名称": "普通", "最新价": 10.0, "最高价": 10.2, "涨停价": 11.0, "昨收": 10.0},
+    ])
+    out = resolve_zt_basic_from_quotes(quotes)
+    assert out[out["代码"] == "600001"]["zt_basic_state"].tolist() == ["at_limit"]
+    assert out[out["代码"] == "600002"]["zt_basic_state"].tolist() == ["none"]

~~~~
