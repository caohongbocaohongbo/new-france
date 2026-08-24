"""pytdx 数据源封装。"""
import asyncio
from datetime import datetime, timedelta

from ..config import BEIJING_TZ, CONFIG


class TdxUnavailable(Exception):
    """TDX 行情源不可用。"""


def market_of(code: str) -> int:
    return 1 if str(code or "").zfill(6).startswith("6") else 0


class TdxPool:
    def __init__(self, servers=None, connect_timeout=None, socket_timeout=None):
        self.servers = servers or CONFIG["tdx_servers"]
        self.connect_timeout = connect_timeout or CONFIG["tdx_connect_timeout_s"]
        self.socket_timeout = socket_timeout or CONFIG["tdx_socket_timeout_s"]
        self.api = None
        self.server_index = 0
        self.failures = 0
        self.circuit_until = None

    def _server_tuple(self, raw: str):
        host, _, port = str(raw).partition(":")
        return host, int(port or 7709)

    def connect(self) -> bool:
        try:
            from pytdx.hq import TdxHq_API
        except ImportError as exc:
            raise TdxUnavailable("pytdx 未安装，请先安装 pytdx>=1.72") from exc

        for offset in range(len(self.servers)):
            idx = (self.server_index + offset) % len(self.servers)
            host, port = self._server_tuple(self.servers[idx])
            api = TdxHq_API(heartbeat=True, auto_retry=False, raise_exception=True)
            try:
                ok = api.connect(host, port, time_out=self.connect_timeout)
                if ok:
                    self.api = api
                    client = getattr(api, "client", None)
                    if client is not None and hasattr(client, "settimeout"):
                        try:
                            client.settimeout(self.socket_timeout)
                        except Exception:
                            pass
                    self.server_index = idx
                    self.failures = 0
                    return True
            except Exception:
                try:
                    api.disconnect()
                except Exception:
                    pass
                continue
        raise TdxUnavailable("全部 TDX 服务器连接失败")

    def ensure_alive(self):
        now = datetime.now(BEIJING_TZ)
        if self.circuit_until and now < self.circuit_until:
            raise TdxUnavailable("TDX 熔断中")
        if self.api is None:
            return self.connect()
        try:
            self.api.get_security_count(0)
            return True
        except Exception:
            self.disconnect()
            self.failures += 1
            if self.failures >= int(CONFIG["failure_threshold"]):
                self.circuit_until = now + timedelta(minutes=int(CONFIG["circuit_break_minutes"]))
                raise TdxUnavailable("TDX 连续失败，进入熔断")
            return self.connect()

    def fetch_stock(self, market: int, code: str) -> dict:
        self.ensure_alive()
        quote = self.api.get_security_quotes([(market, code)])
        txs = self.api.get_transaction_data(market, code, 0, int(CONFIG["tx_count"]))
        if not quote:
            raise TdxUnavailable(f"{code} 无报价")
        return {
            "code": code,
            "quote": quote[0],
            "txs": txs or [],
            "servertime": quote[0].get("servertime"),
            "fetched_at": datetime.now(BEIJING_TZ).isoformat(),
        }

    def fetch_bars(self, market, code, category, n) -> list:
        self.ensure_alive()
        return self.api.get_security_bars(category, market, code, 0, n) or []

    def fetch_finance(self, market, code) -> dict:
        self.ensure_alive()
        return self.api.get_finance_info(market, code) or {}

    def disconnect(self):
        if self.api is not None:
            try:
                self.api.disconnect()
            finally:
                self.api = None


async def poll_pool_once(pool: TdxPool, watch_pool: list, cfg: dict = None) -> list:
    cfg = cfg or CONFIG
    semaphore = asyncio.Semaphore(max(1, int(cfg.get("fetch_concurrency", 1))))

    async def fetch(item):
        async with semaphore:
            code = str(item.get("code") or "").zfill(6)
            try:
                payload = await asyncio.wait_for(
                    asyncio.to_thread(pool.fetch_stock, market_of(code), code),
                    timeout=float(cfg.get("tdx_socket_timeout_s", 3)),
                )
                payload.update({"name": item.get("name"), "pool_item": item})
                return payload
            except asyncio.TimeoutError:
                return {"code": code, "name": item.get("name"), "error": "TDX 读取超时", "pool_item": item}
            except Exception as exc:
                return {"code": code, "name": item.get("name"), "error": str(exc), "pool_item": item}

    return await asyncio.gather(*(fetch(item) for item in watch_pool), return_exceptions=False)


def verify_tdx() -> dict:
    pool = TdxPool()
    try:
        pool.connect()
        payload = pool.fetch_stock(market_of("600000"), "600000")
        return {"ok": True, "server": pool.servers[pool.server_index], "sample": payload.get("quote", {})}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        pool.disconnect()
