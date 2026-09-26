from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class FuyaoError(Exception):
    def __init__(self, message: str, code: int | None = None, request_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


class DataEnvelope:
    """统一数据包装：携带口径元数据。"""

    def __init__(
        self,
        data: Any,
        *,
        source: str,
        field: str = "",
        as_of: str | None = None,
        unit: str = "",
        request_id: str | None = None,
        status: str = "ok",
        message: str = "",
    ):
        self.data = data
        self.source = source
        self.field = field
        self.as_of = as_of
        self.unit = unit
        self.request_id = request_id
        self.status = status
        self.message = message

    def to_meta(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "field": self.field,
            "as_of": self.as_of,
            "unit": self.unit,
            "request_id": self.request_id,
            "status": self.status,
            "message": self.message,
        }


# 演示用样本宇宙（真实 Key 不可用时）
MOCK_UNIVERSE = [
    {"thscode": "600519.SH", "ticker": "600519", "name": "贵州茅台"},
    {"thscode": "000858.SZ", "ticker": "000858", "name": "五粮液"},
    {"thscode": "601318.SH", "ticker": "601318", "name": "中国平安"},
    {"thscode": "600036.SH", "ticker": "600036", "name": "招商银行"},
    {"thscode": "000001.SZ", "ticker": "000001", "name": "平安银行"},
    {"thscode": "601166.SH", "ticker": "601166", "name": "兴业银行"},
    {"thscode": "300750.SZ", "ticker": "300750", "name": "宁德时代"},
    {"thscode": "002594.SZ", "ticker": "002594", "name": "比亚迪"},
    {"thscode": "600276.SH", "ticker": "600276", "name": "恒瑞医药"},
    {"thscode": "000333.SZ", "ticker": "000333", "name": "美的集团"},
    {"thscode": "600887.SH", "ticker": "600887", "name": "伊利股份"},
    {"thscode": "601012.SH", "ticker": "601012", "name": "隆基绿能"},
    {"thscode": "002415.SZ", "ticker": "002415", "name": "海康威视"},
    {"thscode": "600900.SH", "ticker": "600900", "name": "长江电力"},
    {"thscode": "601888.SH", "ticker": "601888", "name": "中国中免"},
    {"thscode": "300059.SZ", "ticker": "300059", "name": "东方财富"},
    {"thscode": "002475.SZ", "ticker": "002475", "name": "立讯精密"},
    {"thscode": "603259.SH", "ticker": "603259", "name": "药明康德"},
    {"thscode": "000568.SZ", "ticker": "000568", "name": "泸州老窖"},
    {"thscode": "601398.SH", "ticker": "601398", "name": "工商银行"},
]

MOCK_VALUATIONS = {
    "600519.SH": {"pe_ttm": 22.5, "pe_mrq": 21.0, "pb_mrq": 7.8, "ps_ttm": 11.2, "pcf_ttm": 28.0},
    "000858.SZ": {"pe_ttm": 16.2, "pe_mrq": 15.5, "pb_mrq": 3.2, "ps_ttm": 5.1, "pcf_ttm": 18.0},
    "601318.SH": {"pe_ttm": 9.5, "pe_mrq": 9.1, "pb_mrq": 1.1, "ps_ttm": 1.0, "pcf_ttm": 6.5},
    "600036.SH": {"pe_ttm": 7.2, "pe_mrq": 7.0, "pb_mrq": 1.0, "ps_ttm": 3.2, "pcf_ttm": 5.8},
    "000001.SZ": {"pe_ttm": 5.8, "pe_mrq": 5.5, "pb_mrq": 0.7, "ps_ttm": 1.8, "pcf_ttm": 4.2},
    "601166.SH": {"pe_ttm": 5.5, "pe_mrq": 5.3, "pb_mrq": 0.6, "ps_ttm": 2.0, "pcf_ttm": 4.0},
    "300750.SZ": {"pe_ttm": 28.0, "pe_mrq": 26.0, "pb_mrq": 4.5, "ps_ttm": 3.8, "pcf_ttm": 35.0},
    "002594.SZ": {"pe_ttm": 24.0, "pe_mrq": 22.0, "pb_mrq": 5.2, "ps_ttm": 1.9, "pcf_ttm": 22.0},
    "600276.SH": {"pe_ttm": 45.0, "pe_mrq": 42.0, "pb_mrq": 6.0, "ps_ttm": 10.0, "pcf_ttm": 50.0},
    "000333.SZ": {"pe_ttm": 14.0, "pe_mrq": 13.5, "pb_mrq": 3.0, "ps_ttm": 1.5, "pcf_ttm": 12.0},
    "600887.SH": {"pe_ttm": 18.0, "pe_mrq": 17.0, "pb_mrq": 4.0, "ps_ttm": 2.2, "pcf_ttm": 16.0},
    "601012.SH": {"pe_ttm": 55.0, "pe_mrq": None, "pb_mrq": 2.1, "ps_ttm": 2.5, "pcf_ttm": None},
    "002415.SZ": {"pe_ttm": 20.0, "pe_mrq": 19.0, "pb_mrq": 3.5, "ps_ttm": 4.0, "pcf_ttm": 18.0},
    "600900.SH": {"pe_ttm": 19.5, "pe_mrq": 19.0, "pb_mrq": 2.8, "ps_ttm": 8.0, "pcf_ttm": 14.0},
    "601888.SH": {"pe_ttm": 32.0, "pe_mrq": 30.0, "pb_mrq": 4.0, "ps_ttm": 3.0, "pcf_ttm": 25.0},
    "300059.SZ": {"pe_ttm": 35.0, "pe_mrq": 33.0, "pb_mrq": 4.8, "ps_ttm": 12.0, "pcf_ttm": 40.0},
    "002475.SZ": {"pe_ttm": 26.0, "pe_mrq": 24.0, "pb_mrq": 5.5, "ps_ttm": 2.0, "pcf_ttm": 30.0},
    "603259.SH": {"pe_ttm": 38.0, "pe_mrq": 36.0, "pb_mrq": 4.2, "ps_ttm": 6.0, "pcf_ttm": 32.0},
    "000568.SZ": {"pe_ttm": 15.0, "pe_mrq": 14.5, "pb_mrq": 4.5, "ps_ttm": 7.0, "pcf_ttm": 16.0},
    "601398.SH": {"pe_ttm": 6.0, "pe_mrq": 5.8, "pb_mrq": 0.6, "ps_ttm": 2.5, "pcf_ttm": 4.5},
}

# 经营改善：营收同比、净利润同比（百分比）
MOCK_FUNDAMENTALS = {
    "600519.SH": {"revenue_yoy": 12.5, "net_profit_yoy": 10.2, "volatility_60d": 0.18, "max_drawdown_60d": 0.08},
    "000858.SZ": {"revenue_yoy": 8.0, "net_profit_yoy": 5.5, "volatility_60d": 0.22, "max_drawdown_60d": 0.12},
    "601318.SH": {"revenue_yoy": 3.2, "net_profit_yoy": -2.1, "volatility_60d": 0.20, "max_drawdown_60d": 0.10},
    "600036.SH": {"revenue_yoy": 6.5, "net_profit_yoy": 4.0, "volatility_60d": 0.15, "max_drawdown_60d": 0.06},
    "000001.SZ": {"revenue_yoy": 2.0, "net_profit_yoy": 1.5, "volatility_60d": 0.19, "max_drawdown_60d": 0.09},
    "601166.SH": {"revenue_yoy": 4.5, "net_profit_yoy": 3.0, "volatility_60d": 0.17, "max_drawdown_60d": 0.07},
    "300750.SZ": {"revenue_yoy": 18.0, "net_profit_yoy": 22.0, "volatility_60d": 0.35, "max_drawdown_60d": 0.22},
    "002594.SZ": {"revenue_yoy": 25.0, "net_profit_yoy": 30.0, "volatility_60d": 0.32, "max_drawdown_60d": 0.18},
    "600276.SH": {"revenue_yoy": 15.0, "net_profit_yoy": 28.0, "volatility_60d": 0.28, "max_drawdown_60d": 0.15},
    "000333.SZ": {"revenue_yoy": 9.0, "net_profit_yoy": 11.0, "volatility_60d": 0.16, "max_drawdown_60d": 0.07},
    "600887.SH": {"revenue_yoy": 5.0, "net_profit_yoy": 3.5, "volatility_60d": 0.14, "max_drawdown_60d": 0.05},
    "601012.SH": {"revenue_yoy": -8.0, "net_profit_yoy": -25.0, "volatility_60d": 0.40, "max_drawdown_60d": 0.30},
    "002415.SZ": {"revenue_yoy": 7.5, "net_profit_yoy": 6.0, "volatility_60d": 0.21, "max_drawdown_60d": 0.11},
    "600900.SH": {"revenue_yoy": 4.0, "net_profit_yoy": 5.0, "volatility_60d": 0.12, "max_drawdown_60d": 0.04},
    "601888.SH": {"revenue_yoy": -5.0, "net_profit_yoy": -12.0, "volatility_60d": 0.30, "max_drawdown_60d": 0.20},
    "300059.SZ": {"revenue_yoy": 20.0, "net_profit_yoy": 15.0, "volatility_60d": 0.38, "max_drawdown_60d": 0.25},
    "002475.SZ": {"revenue_yoy": 16.0, "net_profit_yoy": 14.0, "volatility_60d": 0.27, "max_drawdown_60d": 0.14},
    "603259.SH": {"revenue_yoy": 11.0, "net_profit_yoy": 8.0, "volatility_60d": 0.29, "max_drawdown_60d": 0.16},
    "000568.SZ": {"revenue_yoy": 10.0, "net_profit_yoy": 9.0, "volatility_60d": 0.23, "max_drawdown_60d": 0.10},
    "601398.SH": {"revenue_yoy": 1.5, "net_profit_yoy": 1.0, "volatility_60d": 0.11, "max_drawdown_60d": 0.03},
}


def _ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


class FuyaoClient:
    """扶摇 REST 数据网关：统一错误、口径元数据、mock 回退。"""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._cache: dict[str, Any] = {}

    @property
    def mode(self) -> str:
        if self.settings.use_mock_data or not self.settings.fuyao_api_key:
            return "mock"
        return "live"

    def _headers(self) -> dict[str, str]:
        return {"X-api-key": self.settings.fuyao_api_key}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.settings.fuyao_base_url.rstrip('/')}{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params or {}, headers=self._headers())
            resp.raise_for_status()
            body = resp.json()
        code = body.get("code")
        if code not in (0, 200, None) and code != "0":
            # 扶摇业务成功码通常为 0
            if isinstance(code, int) and code != 0:
                raise FuyaoError(
                    body.get("message") or f"Fuyao business error code={code}",
                    code=code,
                    request_id=body.get("request_id"),
                )
        return body

    async def probe(self) -> dict[str, Any]:
        if self.mode == "mock":
            return {
                "ok": True,
                "mode": "mock",
                "detail": {
                    "reason": "USE_MOCK_DATA=true 或未配置 FUYAO_API_KEY，使用内置样本数据",
                    "universe_size": len(MOCK_UNIVERSE),
                },
            }
        try:
            body = await self._get(
                "/api/meta/tickers/search",
                {"q": "贵州茅台", "asset_type": "a-share", "limit": 1},
            )
            return {
                "ok": True,
                "mode": "live",
                "detail": {
                    "request_id": body.get("request_id"),
                    "sample": (body.get("data") or {}).get("item")
                    or (body.get("data") or {}).get("items")
                    or body.get("data"),
                },
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("fuyao probe failed")
            return {"ok": False, "mode": "live", "detail": {"error": str(exc)}}

    async def list_universe(
        self,
        *,
        index_code: str | None = None,
        tickers: list[str] | None = None,
        limit: int = 40,
    ) -> DataEnvelope:
        if tickers:
            items = [{"thscode": t if "." in t else f"{t}.SH", "ticker": t.split(".")[0], "name": t} for t in tickers]
            return DataEnvelope(items[:limit], source="custom", field="universe", status="ok")

        if self.mode == "mock":
            return DataEnvelope(
                MOCK_UNIVERSE[:limit],
                source="mock",
                field="universe",
                as_of=datetime.now(timezone.utc).isoformat(),
                message="mock universe sample",
            )

        # 优先尝试指数成分；失败则回退到标的列表抽样
        try:
            body = await self._get(
                "/api/a-share/index/constituents",
                {"thscode": index_code or "000300.SH"},
            )
            data = body.get("data") or {}
            items = data.get("item") or data.get("items") or data.get("constituents") or []
            if items:
                normalized = []
                for it in items[:limit]:
                    thscode = it.get("thscode") or it.get("constituent_thscode") or ""
                    if not thscode:
                        continue
                    normalized.append(
                        {
                            "thscode": thscode,
                            "ticker": it.get("ticker") or thscode.split(".")[0],
                            "name": it.get("name") or it.get("constituent_name") or thscode,
                        }
                    )
                if normalized:
                    return DataEnvelope(
                        normalized,
                        source="fuyao:/api/a-share/index/constituents",
                        field="universe",
                        as_of=_ms_to_iso(data.get("timestamp")),
                        request_id=body.get("request_id"),
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("index constituents failed, fallback to ticker list: %s", exc)

        try:
            body = await self._get(
                "/api/meta/tickers/list",
                {"asset_type": "a-share", "limit": limit, "offset": 0},
            )
            data = body.get("data") or {}
            items = data.get("item") or data.get("items") or []
            normalized = [
                {
                    "thscode": it.get("thscode"),
                    "ticker": it.get("ticker"),
                    "name": it.get("name") or it.get("thscode"),
                }
                for it in items
                if it.get("thscode")
            ]
            return DataEnvelope(
                normalized[:limit],
                source="fuyao:/api/meta/tickers/list",
                field="universe",
                request_id=body.get("request_id"),
                message="index constituents unavailable; used ticker list sample",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("ticker list failed, using mock")
            return DataEnvelope(
                MOCK_UNIVERSE[:limit],
                source="mock-fallback",
                field="universe",
                status="error",
                message=str(exc),
            )

    async def valuations_snapshot(self, thscodes: list[str]) -> DataEnvelope:
        if not thscodes:
            return DataEnvelope([], source="fuyao", field="valuations", status="missing")

        if self.mode == "mock":
            items = []
            for code in thscodes:
                base = next((u for u in MOCK_UNIVERSE if u["thscode"] == code), None)
                vals = MOCK_VALUATIONS.get(code, {})
                items.append(
                    {
                        "thscode": code,
                        "ticker": base["ticker"] if base else code.split(".")[0],
                        "name": base["name"] if base else code,
                        **vals,
                    }
                )
            return DataEnvelope(
                items,
                source="mock",
                field="valuations",
                as_of=datetime.now(timezone.utc).isoformat(),
                unit="ratio",
            )

        # API 单次最多约 100
        all_items: list[dict[str, Any]] = []
        request_ids: list[str] = []
        as_of: str | None = None
        for i in range(0, len(thscodes), 80):
            chunk = thscodes[i : i + 80]
            try:
                body = await self._get(
                    "/api/a-share/valuations/snapshot",
                    {"thscodes": ",".join(chunk)},
                )
                data = body.get("data") or {}
                all_items.extend(data.get("item") or [])
                if body.get("request_id"):
                    request_ids.append(body["request_id"])
                as_of = _ms_to_iso(data.get("timestamp")) or as_of
            except Exception as exc:  # noqa: BLE001
                logger.warning("valuations chunk failed: %s", exc)
                for code in chunk:
                    all_items.append({"thscode": code, "_error": str(exc)})

        return DataEnvelope(
            all_items,
            source="fuyao:/api/a-share/valuations/snapshot",
            field="valuations",
            as_of=as_of,
            request_id=",".join(request_ids) if request_ids else None,
            unit="ratio",
        )

    async def prices_snapshot(self, thscodes: list[str]) -> DataEnvelope:
        if self.mode == "mock":
            items = [{"thscode": c, "last_price": 100.0, "price_change_ratio_pct": 0.5} for c in thscodes]
            return DataEnvelope(items, source="mock", field="prices", unit="CNY")

        try:
            body = await self._get(
                "/api/a-share/prices/snapshot",
                {"thscodes": ",".join(thscodes[:100])},
            )
            data = body.get("data") or {}
            return DataEnvelope(
                data.get("item") or [],
                source="fuyao:/api/a-share/prices/snapshot",
                field="prices",
                as_of=_ms_to_iso(data.get("timestamp")),
                request_id=body.get("request_id"),
                unit="CNY",
            )
        except Exception as exc:  # noqa: BLE001
            return DataEnvelope([], source="fuyao", field="prices", status="error", message=str(exc))

    async def income_statements(self, thscode: str, limit: int = 4) -> DataEnvelope:
        if self.mode == "mock":
            fund = MOCK_FUNDAMENTALS.get(thscode, {})
            # 构造两期便于同比
            latest_rev = 100.0
            prev_rev = latest_rev / (1 + fund.get("revenue_yoy", 0) / 100) if fund.get("revenue_yoy") is not None else 90
            latest_np = 20.0
            prev_np = latest_np / (1 + fund.get("net_profit_yoy", 0) / 100) if fund.get("net_profit_yoy") is not None else 18
            items = [
                {
                    "thscode": thscode,
                    "fiscal_year": 2024,
                    "operating_income": latest_rev,
                    "net_profit": latest_np,
                    "parent_holder_net_profit": latest_np,
                },
                {
                    "thscode": thscode,
                    "fiscal_year": 2023,
                    "operating_income": prev_rev,
                    "net_profit": prev_np,
                    "parent_holder_net_profit": prev_np,
                },
            ]
            return DataEnvelope(items, source="mock", field="income_statements", unit="CNY")

        try:
            body = await self._get(
                "/api/a-share/financials/income-statements",
                {"thscode": thscode, "period": "annual", "limit": limit},
            )
            data = body.get("data") or {}
            return DataEnvelope(
                data.get("item") or [],
                source="fuyao:/api/a-share/financials/income-statements",
                field="income_statements",
                as_of=_ms_to_iso(data.get("timestamp")),
                request_id=body.get("request_id"),
                unit="CNY",
            )
        except Exception as exc:  # noqa: BLE001
            return DataEnvelope(
                [],
                source="fuyao",
                field="income_statements",
                status="error",
                message=str(exc),
            )

    async def price_history(self, thscode: str, limit: int = 60) -> DataEnvelope:
        if self.mode == "mock":
            fund = MOCK_FUNDAMENTALS.get(thscode, {})
            vol = fund.get("volatility_60d", 0.2)
            # 生成近似序列供波动率计算
            prices = [100.0]
            for i in range(1, limit):
                prices.append(prices[-1] * (1 + (0.001 if i % 2 == 0 else -0.001) * (vol * 10)))
            items = [{"close": p, "trade_date": i} for i, p in enumerate(prices)]
            return DataEnvelope(items, source="mock", field="kline", unit="CNY")

        try:
            body = await self._get(
                "/api/a-share/prices/kline",
                {"thscode": thscode, "period": "day", "limit": limit, "adjust": "forward"},
            )
            data = body.get("data") or {}
            items = data.get("item") or data.get("items") or data.get("klines") or []
            return DataEnvelope(
                items,
                source="fuyao:/api/a-share/prices/kline",
                field="kline",
                as_of=_ms_to_iso(data.get("timestamp")),
                request_id=body.get("request_id"),
                unit="CNY",
            )
        except Exception as exc:  # noqa: BLE001
            # 兼容可能的路径差异
            try:
                body = await self._get(
                    "/api/a-share/prices/history",
                    {"thscode": thscode, "limit": limit},
                )
                data = body.get("data") or {}
                items = data.get("item") or []
                return DataEnvelope(
                    items,
                    source="fuyao:/api/a-share/prices/history",
                    field="kline",
                    request_id=body.get("request_id"),
                    unit="CNY",
                )
            except Exception as exc2:  # noqa: BLE001
                return DataEnvelope(
                    [],
                    source="fuyao",
                    field="kline",
                    status="error",
                    message=f"{exc}; fallback: {exc2}",
                )

    async def build_stock_metrics(self, thscodes: list[str]) -> dict[str, dict[str, Any]]:
        """为筛选引擎准备每只股票的可执行指标字典。"""
        result: dict[str, dict[str, Any]] = {
            code: {"thscode": code, "metrics": {}, "evidence": {}} for code in thscodes
        }

        val_env = await self.valuations_snapshot(thscodes)
        val_by_code = {it.get("thscode"): it for it in (val_env.data or []) if isinstance(it, dict)}

        for code in thscodes:
            meta_base = {
                "source": val_env.source,
                "as_of": val_env.as_of,
                "request_id": val_env.request_id,
            }
            item = val_by_code.get(code)
            if not item or item.get("_error"):
                for f in ("pe_ttm", "pe_mrq", "pb_mrq", "ps_ttm", "pcf_ttm"):
                    result[code]["evidence"][f] = {
                        **meta_base,
                        "field": f,
                        "value": None,
                        "status": "error" if item and item.get("_error") else "missing",
                        "message": (item or {}).get("_error") or "valuation missing",
                        "unit": "ratio",
                    }
            else:
                result[code]["name"] = item.get("name") or code
                result[code]["ticker"] = item.get("ticker") or code.split(".")[0]
                for f in ("pe_ttm", "pe_mrq", "pb_mrq", "ps_ttm", "pcf_ttm"):
                    v = item.get(f)
                    result[code]["metrics"][f] = v
                    result[code]["evidence"][f] = {
                        **meta_base,
                        "field": f,
                        "value": v,
                        "status": "ok" if v is not None else "missing",
                        "message": "" if v is not None else "null from upstream",
                        "unit": "ratio",
                    }

        # 财务同比与波动：mock 批量；live 对每票取数（宇宙已限流）
        if self.mode == "mock":
            for code in thscodes:
                fund = MOCK_FUNDAMENTALS.get(code, {})
                name_info = next((u for u in MOCK_UNIVERSE if u["thscode"] == code), None)
                if name_info:
                    result[code]["name"] = name_info["name"]
                    result[code]["ticker"] = name_info["ticker"]
                for f, unit in (
                    ("revenue_yoy", "%"),
                    ("net_profit_yoy", "%"),
                    ("volatility_60d", "ratio"),
                    ("max_drawdown_60d", "ratio"),
                ):
                    v = fund.get(f)
                    result[code]["metrics"][f] = v
                    result[code]["evidence"][f] = {
                        "source": "mock",
                        "field": f,
                        "value": v,
                        "status": "ok" if v is not None else "missing",
                        "unit": unit,
                        "as_of": datetime.now(timezone.utc).isoformat(),
                        "message": "",
                    }
            return result

        for code in thscodes:
            income = await self.income_statements(code, limit=4)
            items = income.data or []
            rev_yoy = _yoy_from_statements(items, "operating_income")
            np_yoy = _yoy_from_statements(items, "parent_holder_net_profit") or _yoy_from_statements(
                items, "net_profit"
            )
            for f, v, unit in (
                ("revenue_yoy", rev_yoy, "%"),
                ("net_profit_yoy", np_yoy, "%"),
            ):
                result[code]["metrics"][f] = v
                result[code]["evidence"][f] = {
                    "source": income.source,
                    "field": f,
                    "value": v,
                    "status": "ok" if v is not None else ("error" if income.status == "error" else "missing"),
                    "unit": unit,
                    "as_of": income.as_of,
                    "request_id": income.request_id,
                    "message": income.message or ("" if v is not None else "insufficient periods"),
                }

            hist = await self.price_history(code, limit=60)
            closes = _extract_closes(hist.data or [])
            vol = _volatility(closes)
            mdd = _max_drawdown(closes)
            for f, v, unit in (
                ("volatility_60d", vol, "ratio"),
                ("max_drawdown_60d", mdd, "ratio"),
            ):
                result[code]["metrics"][f] = v
                result[code]["evidence"][f] = {
                    "source": hist.source,
                    "field": f,
                    "value": v,
                    "status": "ok" if v is not None else ("error" if hist.status == "error" else "missing"),
                    "unit": unit,
                    "as_of": hist.as_of,
                    "request_id": hist.request_id,
                    "message": hist.message or ("" if v is not None else "insufficient bars"),
                }

        return result


def _yoy_from_statements(items: list[dict[str, Any]], field: str) -> Optional[float]:
    if len(items) < 2:
        return None
    latest = items[0].get(field)
    prev = items[1].get(field)
    try:
        latest_f = float(latest)
        prev_f = float(prev)
        if prev_f == 0:
            return None
        return round((latest_f - prev_f) / abs(prev_f) * 100, 4)
    except (TypeError, ValueError):
        return None


def _extract_closes(items: list[dict[str, Any]]) -> list[float]:
    closes: list[float] = []
    for it in items:
        for key in ("close", "close_price", "adj_close", "last_price"):
            if key in it and it[key] is not None:
                try:
                    closes.append(float(it[key]))
                    break
                except (TypeError, ValueError):
                    continue
    # 若按时间降序，翻转为升序
    if len(closes) >= 2 and closes[0] != closes[-1]:
        # 无法可靠判断方向时保持原序；波动率对方向不敏感
        pass
    return closes


def _volatility(closes: list[float]) -> Optional[float]:
    if len(closes) < 5:
        return None
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] <= 0:
            continue
        rets.append(math.log(closes[i] / closes[i - 1]))
    if len(rets) < 4:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    # 年化近似
    return round(math.sqrt(var) * math.sqrt(252), 6)


def _max_drawdown(closes: list[float]) -> Optional[float]:
    if len(closes) < 5:
        return None
    peak = closes[0]
    max_dd = 0.0
    for p in closes:
        if p > peak:
            peak = p
        if peak > 0:
            dd = (peak - p) / peak
            if dd > max_dd:
                max_dd = dd
    return round(max_dd, 6)
