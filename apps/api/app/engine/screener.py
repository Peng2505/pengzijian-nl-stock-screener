from __future__ import annotations

from typing import Any

from app.data.fuyao import FuyaoClient
from app.models.schema import (
    Condition,
    ConditionEval,
    DataPointEvidence,
    Operator,
    ScreeningResult,
    ScreeningRunMeta,
    ScreeningSpec,
    StockResult,
    UniverseType,
    utc_now_iso,
)


FIELD_LABELS = {
    "pe_ttm": "市盈率 TTM",
    "pe_mrq": "市盈率 MRQ",
    "pb_mrq": "市净率 MRQ",
    "ps_ttm": "市销率 TTM",
    "pcf_ttm": "市现率 TTM",
    "revenue_yoy": "营业收入同比",
    "net_profit_yoy": "净利润同比",
    "volatility_60d": "近60日波动率",
    "max_drawdown_60d": "近60日最大回撤",
}


def format_expected(cond: Condition) -> str:
    label = FIELD_LABELS.get(cond.field, cond.field)
    unit = cond.unit or ""
    if cond.op == Operator.BETWEEN and isinstance(cond.value, list) and len(cond.value) == 2:
        return f"{label} ∈ [{cond.value[0]}, {cond.value[1]}]{unit}"
    op_map = {
        Operator.LT: "<",
        Operator.LTE: "≤",
        Operator.GT: ">",
        Operator.GTE: "≥",
        Operator.EQ: "=",
    }
    return f"{label} {op_map.get(cond.op, cond.op.value)} {cond.value}{unit}"


def eval_operator(op: Operator, actual: float, expected: float | list[float]) -> bool:
    if op == Operator.BETWEEN:
        if not isinstance(expected, list) or len(expected) != 2:
            return False
        lo, hi = float(expected[0]), float(expected[1])
        return lo <= actual <= hi
    value = float(expected)  # type: ignore[arg-type]
    if op == Operator.LT:
        return actual < value
    if op == Operator.LTE:
        return actual <= value
    if op == Operator.GT:
        return actual > value
    if op == Operator.GTE:
        return actual >= value
    if op == Operator.EQ:
        return abs(actual - value) < 1e-9
    return False


def detect_conflicts(conditions: list[Condition]) -> list[dict[str, Any]]:
    """确定性冲突规则（不依赖 LLM）。"""
    conflicts: list[dict[str, Any]] = []
    by_field = {c.field: c for c in conditions if c.enabled}

    pe = by_field.get("pe_ttm")
    growth = by_field.get("revenue_yoy") or by_field.get("net_profit_yoy")
    if pe and growth:
        pe_high = False
        if pe.op in (Operator.GT, Operator.GTE) and isinstance(pe.value, (int, float)) and pe.value >= 40:
            pe_high = True
        if pe.op == Operator.BETWEEN and isinstance(pe.value, list) and pe.value[-1] >= 50:
            pe_high = True
        growth_high = False
        if growth.op in (Operator.GT, Operator.GTE) and isinstance(growth.value, (int, float)) and growth.value >= 20:
            growth_high = True
        # 估值上限很低 + 高成长：可能冲突
        pe_tight = False
        if pe.op == Operator.BETWEEN and isinstance(pe.value, list) and pe.value[-1] <= 15:
            pe_tight = True
        if pe.op in (Operator.LT, Operator.LTE) and isinstance(pe.value, (int, float)) and pe.value <= 12:
            pe_tight = True
        if pe_tight and growth_high:
            conflicts.append(
                {
                    "condition_ids": [pe.id, growth.id],
                    "reason": "要求较高成长同时严格压低估值，候选集可能极窄或为空。",
                    "suggestion": "放宽 PE 上限，或降低成长同比阈值后重跑。",
                }
            )
        if pe_high and growth and growth.op in (Operator.LT, Operator.LTE):
            conflicts.append(
                {
                    "condition_ids": [pe.id, growth.id],
                    "reason": "高估值偏好与偏低成长约束并存，逻辑不一致。",
                    "suggestion": "明确是成长风格还是价值风格，再编辑条件。",
                }
            )

    vol = by_field.get("volatility_60d")
    mdd = by_field.get("max_drawdown_60d")
    if vol and mdd:
        # 两者都极严
        strict = 0
        for c in (vol, mdd):
            if c.op in (Operator.LT, Operator.LTE) and isinstance(c.value, (int, float)) and c.value <= 0.05:
                strict += 1
        if strict == 2:
            conflicts.append(
                {
                    "condition_ids": [vol.id, mdd.id],
                    "reason": "波动率与最大回撤同时极严，可能过度过滤。",
                    "suggestion": "保留其一，或将阈值放宽到 0.15–0.25。",
                }
            )
    return conflicts


class ScreeningEngine:
    """确定性筛选引擎：AI 不参与入选判定。"""

    def __init__(self, client: FuyaoClient | None = None):
        self.client = client or FuyaoClient()

    async def run(self, spec: ScreeningSpec, exclude_sample_limit: int = 20) -> ScreeningResult:
        meta = ScreeningRunMeta(data_mode="mock" if self.client.mode == "mock" else "live")
        warnings: list[str] = []

        universe = spec.universe
        if universe.type == UniverseType.CUSTOM and universe.tickers:
            env = await self.client.list_universe(tickers=universe.tickers, limit=universe.limit)
        elif universe.type == UniverseType.INDEX:
            env = await self.client.list_universe(index_code=universe.index_code, limit=universe.limit)
        else:
            env = await self.client.list_universe(limit=universe.limit)

        if env.status == "error":
            warnings.append(f"股票池取数异常：{env.message}；已尽最大努力回退。")
        if env.message and env.source.startswith("mock"):
            warnings.append(env.message)

        stocks = env.data or []
        thscodes = [s["thscode"] for s in stocks if s.get("thscode")]
        name_map = {s["thscode"]: s for s in stocks}

        metrics_map = await self.client.build_stock_metrics(thscodes)
        active_conditions = [c for c in spec.conditions if c.enabled]

        selected: list[StockResult] = []
        excluded: list[StockResult] = []

        for code in thscodes:
            pack = metrics_map.get(code, {})
            info = name_map.get(code, {})
            evals: list[ConditionEval] = []
            passed_all = True
            missing_required = False
            failed_data = False
            score = 0.0

            for cond in active_conditions:
                evidence_raw = (pack.get("evidence") or {}).get(cond.field) or {
                    "field": cond.field,
                    "value": None,
                    "status": "missing",
                    "source": "unknown",
                    "message": "field not computed",
                }
                evidence = DataPointEvidence(
                    field=cond.field,
                    value=evidence_raw.get("value"),
                    unit=evidence_raw.get("unit") or cond.unit,
                    as_of=evidence_raw.get("as_of"),
                    source=evidence_raw.get("source") or "",
                    request_id=evidence_raw.get("request_id"),
                    status=evidence_raw.get("status") or "missing",
                    message=evidence_raw.get("message") or "",
                )

                actual_val = evidence.value
                passed: bool | None
                if evidence.status == "error":
                    failed_data = True
                    passed = False if cond.required else None
                    if cond.required:
                        passed_all = False
                elif actual_val is None or evidence.status == "missing":
                    missing_required = missing_required or cond.required
                    passed = False if cond.required else None
                    if cond.required:
                        passed_all = False
                else:
                    try:
                        passed = eval_operator(cond.op, float(actual_val), cond.value)
                    except (TypeError, ValueError):
                        passed = False
                        failed_data = True
                    if passed:
                        score += 1.0 * cond.confidence
                    else:
                        passed_all = False

                evals.append(
                    ConditionEval(
                        condition_id=cond.id,
                        intent_label=cond.intent_label,
                        field=cond.field,
                        passed=passed,
                        expected=format_expected(cond),
                        actual=evidence,
                    )
                )

            if failed_data:
                quality = "failed"
            elif missing_required or any(e.actual.status == "missing" for e in evals):
                quality = "partial"
            else:
                quality = "complete"

            # 必填条件未通过则排除；数据失败且必填 → 排除并标注
            is_selected = passed_all and not (failed_data and any(c.required for c in active_conditions))

            fact_bits = []
            for e in evals:
                mark = "命中" if e.passed else ("缺失" if e.passed is None else "未命中")
                fact_bits.append(f"{e.intent_label}:{mark}")
            summary = "；".join(fact_bits) if fact_bits else "无启用条件"

            stock = StockResult(
                thscode=code,
                ticker=pack.get("ticker") or info.get("ticker") or code.split(".")[0],
                name=pack.get("name") or info.get("name") or code,
                selected=is_selected,
                score=round(score, 4),
                condition_evals=evals,
                summary=summary,
                data_quality=quality,  # type: ignore[arg-type]
            )
            if is_selected:
                selected.append(stock)
            else:
                excluded.append(stock)

        selected.sort(key=lambda s: (-s.score, s.thscode))
        excluded.sort(key=lambda s: (s.thscode,))

        meta.finished_at = utc_now_iso()
        meta.universe_size = len(thscodes)
        meta.selected_count = len(selected)
        meta.excluded_count = len(excluded)
        meta.warnings = warnings

        return ScreeningResult(
            spec=spec,
            meta=meta,
            selected=selected,
            excluded=excluded[:exclude_sample_limit],
            excluded_sample_limit=exclude_sample_limit,
        )
