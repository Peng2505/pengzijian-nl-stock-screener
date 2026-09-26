from __future__ import annotations

import json
import logging
import math
import warnings
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# iFinD MCP 网关证书链在部分环境会校验失败，与官方 skill 一致关闭 verify
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

BASE = "https://api-mcp.51ifind.com:8643/ds-mcp-servers"
SERVERS = {
    "stock": f"{BASE}/hexin-ifind-ds-stock-mcp",
    "news": f"{BASE}/hexin-ifind-ds-news-mcp",
}


class IFindClient:
    """iFinD MCP 客户端：仅用于解释增强，不参与确定性筛选。"""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._sessions: dict[str, str] = {}
        self._req_ids: dict[str, int] = {}
        self._tool_sets: dict[str, set[str]] = {}

    @property
    def configured(self) -> bool:
        return bool(self.settings.ifind_mcp_token.strip())

    def _next_id(self, t: str) -> int:
        self._req_ids[t] = self._req_ids.get(t, 0) + 1
        return self._req_ids[t]

    def _headers(self, t: str | None = None) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": self.settings.ifind_mcp_token.strip(),
        }
        if t and t in self._sessions:
            h["Mcp-Session-Id"] = self._sessions[t]
        return h

    def _post(self, t: str, payload: dict[str, Any], timeout: float = 60.0) -> tuple[httpx.Response, Any]:
        with httpx.Client(verify=False, timeout=timeout) as client:
            resp = client.post(SERVERS[t], json=payload, headers=self._headers(t))
        data: Any = None
        if resp.text.strip():
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = resp.text
        return resp, data

    def _init(self, t: str) -> None:
        if t in self._sessions:
            return
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(t),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "nl-screener-enrich", "version": "1.0.0"},
            },
        }
        resp, data = self._post(t, payload, timeout=30.0)
        resp.raise_for_status()
        session_id = resp.headers.get("Mcp-Session-Id")
        if not session_id:
            raise RuntimeError(f"iFinD initialize 未返回 Mcp-Session-Id: {data}")
        self._sessions[t] = session_id
        with httpx.Client(verify=False, timeout=10.0) as client:
            client.post(
                SERVERS[t],
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=self._headers(t),
            )

    def list_tools(self, server_type: str) -> dict[str, Any]:
        if server_type not in SERVERS:
            raise ValueError(f"unknown server_type: {server_type}")
        self._init(server_type)
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(server_type),
            "method": "tools/list",
            "params": {},
        }
        resp, data = self._post(server_type, payload)
        if isinstance(data, dict) and "error" in data:
            return {"ok": False, "status_code": resp.status_code, "error": data["error"], "raw": data}
        resp.raise_for_status()
        return {"ok": True, "status_code": resp.status_code, "data": data}

    def call(self, server_type: str, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        if server_type not in SERVERS:
            raise ValueError(f"unknown server_type: {server_type}")
        self._validate_params(params)
        if server_type not in self._tool_sets:
            listed = self.list_tools(server_type)
            tools = (((listed.get("data") or {}).get("result") or {}).get("tools")) or []
            self._tool_sets[server_type] = {
                t.get("name") for t in tools if isinstance(t, dict) and t.get("name")
            }
        if tool_name not in self._tool_sets[server_type]:
            raise ValueError(f"tool not allowed: {tool_name}")
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(server_type),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": params},
        }
        resp, data = self._post(server_type, payload)
        if isinstance(data, dict) and "error" in data:
            return {"ok": False, "status_code": resp.status_code, "error": data["error"], "raw": data}
        resp.raise_for_status()
        return {"ok": True, "status_code": resp.status_code, "data": data}

    @staticmethod
    def _validate_params(params: dict[str, Any]) -> None:
        blocked = {"__proto__", "prototype", "constructor"}

        def walk(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in blocked:
                        raise TypeError("blocked field")
                    walk(item)
                return
            if isinstance(value, float) and not math.isfinite(value):
                raise TypeError("invalid number")
            if not isinstance(value, (str, int, float, bool)):
                raise TypeError("unsupported value type")

        walk(params)
        json.dumps(params, allow_nan=False)

    @staticmethod
    def _extract_text_payload(result: dict[str, Any]) -> Any:
        try:
            content = (((result.get("data") or {}).get("result") or {}).get("content") or [])
            if not content:
                return None
            text = content[0].get("text")
            if not isinstance(text, str):
                return text
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw_text": text}
        except Exception:  # noqa: BLE001
            return None

    def probe(self) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "reason": "IFIND_MCP_TOKEN 未配置"}
        try:
            listed = self.list_tools("stock")
            return {
                "ok": bool(listed.get("ok")),
                "status_code": listed.get("status_code"),
                "tool_count": len(
                    (((listed.get("data") or {}).get("result") or {}).get("tools")) or []
                ),
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("ifind probe failed")
            return {"ok": False, "error": str(exc)}

    def enrich_stock(self, thscode: str, name: str = "") -> dict[str, Any]:
        """返回解释增强包：实时快照 + 相关新闻（均为 reference，不参与筛选）。"""
        if not self.configured:
            return {
                "available": False,
                "kind": "reference",
                "disclaimer": "iFinD 未配置，跳过解释增强。",
                "quote": None,
                "news": [],
                "errors": ["IFIND_MCP_TOKEN missing"],
            }

        symbol = thscode.split(".")[0] if thscode else ""
        display = name or thscode
        errors: list[str] = []
        quote_block: dict[str, Any] | None = None
        news_items: list[dict[str, Any]] = []

        try:
            raw = self.call(
                "stock",
                "stock_highfreq_quotes",
                {
                    "symbols": symbol,
                    "indicators": "最新价,涨跌幅,开盘价,最高价,最低价,成交额,成交量,换手率,总市值,市盈率TTM",
                    "data_mode": "real_time",
                },
            )
            payload = self._extract_text_payload(raw)
            quote_block = _parse_quote_table(payload, source="iFinD MCP stock_highfreq_quotes")
            if quote_block is None:
                errors.append("行情解析为空或失败")
        except Exception as exc:  # noqa: BLE001
            logger.warning("ifind quote failed: %s", exc)
            errors.append(f"行情增强失败: {exc}")

        try:
            from datetime import date, timedelta

            end = date.today()
            start = end - timedelta(days=90)
            raw_news = self.call(
                "news",
                "search_news",
                {
                    "query": f"{display} {symbol}",
                    "time_start": start.isoformat(),
                    "time_end": end.isoformat(),
                    "size": 3,
                },
            )
            news_payload = self._extract_text_payload(raw_news)
            news_items = _parse_news(news_payload, source="iFinD MCP search_news")
            if not news_items and isinstance(news_payload, dict):
                # 保留原始摘要片段便于排查
                raw_data = news_payload.get("data")
                if isinstance(raw_data, str) and raw_data.strip():
                    news_items = [
                        {
                            "title": "资讯检索结果",
                            "summary": raw_data[:500],
                            "source": "iFinD MCP search_news",
                            "as_of": None,
                        }
                    ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("ifind news failed: %s", exc)
            errors.append(f"新闻增强失败: {exc}")

        return {
            "available": quote_block is not None or bool(news_items),
            "kind": "reference",
            "disclaimer": (
                "以下信息来自 iFinD MCP，仅作解释参考，不参与入选/排除判定，"
                "不构成投资建议或收益承诺。"
            ),
            "thscode": thscode,
            "quote": quote_block,
            "news": news_items,
            "errors": errors,
        }


def _parse_quote_table(payload: Any, source: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("code") not in (1, "1", 0, "0"):
        # 部分成功包 code=1
        if payload.get("msg") and "success" not in str(payload.get("msg")).lower():
            if payload.get("data") is None:
                return {
                    "status": "error",
                    "source": source,
                    "message": str(payload.get("msg") or payload.get("data") or payload),
                    "fields": {},
                    "as_of": None,
                }
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    tables = data.get("tables")
    if not isinstance(tables, list) or not tables:
        return None
    header = tables[0]
    if len(tables) < 2 or not isinstance(header, list):
        return None
    row = tables[1]
    fields = {}
    as_of = None
    for i, key in enumerate(header):
        if i >= len(row):
            break
        if key in ("证券代码", "证券简称"):
            fields[key] = row[i]
        elif key == "time":
            as_of = str(row[i])
        else:
            fields[str(key)] = row[i]
    return {
        "status": "ok",
        "source": source,
        "as_of": as_of,
        "fields": fields,
        "message": "",
    }


def _parse_news(payload: Any, source: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if payload is None:
        return items
    if isinstance(payload, dict):
        if payload.get("code") not in (1, "1", 0, "0", None) and not payload.get("data"):
            return items
        data = payload.get("data", payload)
        candidates = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            for key in ("items", "list", "news", "results", "data"):
                if isinstance(data.get(key), list):
                    candidates = data[key]
                    break
            if not candidates and ("title" in data or "content" in data):
                candidates = [data]
        elif isinstance(data, str):
            return [{"title": data[:120], "summary": data[:400], "source": source, "as_of": None}]
        for it in candidates[:5]:
            if not isinstance(it, dict):
                if isinstance(it, str):
                    items.append({"title": it[:120], "summary": it[:400], "source": source, "as_of": None})
                continue
            title = it.get("title") or it.get("headline") or it.get("name") or "相关资讯"
            summary = (
                it.get("summary")
                or it.get("content")
                or it.get("snippet")
                or it.get("text")
                or ""
            )
            items.append(
                {
                    "title": str(title)[:200],
                    "summary": str(summary)[:500],
                    "source": source,
                    "as_of": it.get("time") or it.get("datetime") or it.get("publish_time"),
                    "url": it.get("url") or it.get("link"),
                }
            )
    elif isinstance(payload, str):
        items.append({"title": payload[:120], "summary": payload[:400], "source": source, "as_of": None})
    return items
