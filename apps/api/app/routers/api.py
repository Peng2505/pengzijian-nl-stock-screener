from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.ai.interpreter import IntentInterpreter
from app.config import get_settings
from app.data.fuyao import FuyaoClient
from app.engine.screener import ScreeningEngine, detect_conflicts, format_expected
from app.models.schema import (
    CompareRequest,
    CompareResponse,
    ConflictItem,
    EnrichStockRequest,
    EnrichStockResponse,
    HealthResponse,
    MonitorDraftRequest,
    ParseIntentRequest,
    ParseIntentResponse,
    ProbeResponse,
    RunScreenRequest,
    SaveStrategyRequest,
    ScreeningResult,
    StrategyRecord,
)
from app.data.ifind import IFindClient
from app.store.db import StrategyStore

router = APIRouter()


def _client() -> FuyaoClient:
    return FuyaoClient(get_settings())


def _ifind() -> IFindClient:
    return IFindClient(get_settings())


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    s = get_settings()
    return HealthResponse(
        status="ok",
        fuyao_configured=bool(s.fuyao_api_key),
        llm_configured=bool(s.llm_api_key),
        ifind_configured=bool(s.ifind_mcp_token),
        use_mock_data=s.use_mock_data or not s.fuyao_api_key,
    )


@router.get("/probe", response_model=ProbeResponse)
async def probe() -> ProbeResponse:
    client = _client()
    detail = await client.probe()
    ifind_probe = _ifind().probe()
    merged = dict(detail.get("detail") or {})
    merged["ifind"] = ifind_probe
    return ProbeResponse(
        ok=bool(detail.get("ok")),
        mode=detail.get("mode", "mock"),  # type: ignore[arg-type]
        detail=merged if merged else (detail.get("detail") or {"ifind": ifind_probe}),
    )


@router.post("/enrich/stock", response_model=EnrichStockResponse)
async def enrich_stock(body: EnrichStockRequest) -> EnrichStockResponse:
    """iFinD 解释增强：不改变筛选结果。"""
    import asyncio

    client = _ifind()
    data = await asyncio.to_thread(client.enrich_stock, body.thscode, body.name)
    return EnrichStockResponse(**data)


@router.post("/intent/parse", response_model=ParseIntentResponse)
async def parse_intent(body: ParseIntentRequest) -> ParseIntentResponse:
    interpreter = IntentInterpreter()
    rejection = interpreter.reject_compliance(body.query)
    if rejection:
        raise HTTPException(
            status_code=400,
            detail={"code": "compliance_reject", "message": rejection},
        )

    spec, notes, used_fallback = await interpreter.parse(
        body.query,
        body.answers,
        history=[t.model_dump() for t in body.history],
        follow_up=body.follow_up,
    )
    spec.conflicts = [ConflictItem(**c) for c in detect_conflicts(spec.conditions)]
    return ParseIntentResponse(spec=spec, ai_notes=notes, used_fallback=used_fallback)


@router.post("/screen/run", response_model=ScreeningResult)
async def run_screen(body: RunScreenRequest) -> ScreeningResult:
    engine = ScreeningEngine(_client())
    body.spec.conflicts = [ConflictItem(**c) for c in detect_conflicts(body.spec.conditions)]
    return await engine.run(body.spec, exclude_sample_limit=body.exclude_sample_limit)


@router.post("/strategies", response_model=StrategyRecord)
async def save_strategy(body: SaveStrategyRequest) -> StrategyRecord:
    store = StrategyStore()
    return store.save_strategy(body.name, body.spec, body.result_summary, body.notes)


@router.get("/strategies", response_model=list[StrategyRecord])
async def list_strategies() -> list[StrategyRecord]:
    return StrategyStore().list_strategies()


@router.get("/strategies/{strategy_id}", response_model=StrategyRecord)
async def get_strategy(strategy_id: str) -> StrategyRecord:
    rec = StrategyStore().get_strategy(strategy_id)
    if not rec:
        raise HTTPException(status_code=404, detail="strategy not found")
    return rec


@router.post("/compare", response_model=CompareResponse)
async def compare(body: CompareRequest) -> CompareResponse:
    engine = ScreeningEngine(_client())
    left = await engine.run(body.left_spec)
    right = await engine.run(body.right_spec)

    left_map = {c.field: c for c in body.left_spec.conditions if c.enabled}
    right_map = {c.field: c for c in body.right_spec.conditions if c.enabled}
    fields = sorted(set(left_map) | set(right_map))
    diffs = []
    for f in fields:
        left_c = left_map.get(f)
        right_c = right_map.get(f)
        left_text = format_expected(left_c) if left_c else None
        right_text = format_expected(right_c) if right_c else None
        diffs.append(
            {
                "field": f,
                "left": left_text,
                "right": right_text,
                "changed": left_text != right_text,
            }
        )

    left_set = {s.thscode for s in left.selected}
    right_set = {s.thscode for s in right.selected}
    return CompareResponse(
        condition_diff=diffs,
        left_result=left,
        right_result=right,
        overlap_selected=sorted(left_set & right_set),
        only_left=sorted(left_set - right_set),
        only_right=sorted(right_set - left_set),
    )


@router.post("/monitor/drafts")
async def create_monitor_draft(body: MonitorDraftRequest):
    return StrategyStore().save_monitor_draft(body.name, body.spec, body.schedule)


@router.get("/monitor/drafts")
async def list_monitor_drafts():
    return StrategyStore().list_monitor_drafts()


@router.post("/backtest/lite")
async def backtest_lite(body: RunScreenRequest):
    result = await ScreeningEngine(_client()).run(body.spec)
    selected = result.selected
    if not selected:
        return {
            "kind": "condition_stability",
            "disclaimer": "本接口不提供收益回测或收益承诺，仅描述当前时点条件命中分布。",
            "message": "当前条件无入选标的，无法描述命中分布。",
            "result_meta": result.meta,
        }

    field_hit: dict[str, int] = {}
    field_total: dict[str, int] = {}
    for stock in selected + result.excluded:
        for ev in stock.condition_evals:
            field_total[ev.field] = field_total.get(ev.field, 0) + 1
            if ev.passed:
                field_hit[ev.field] = field_hit.get(ev.field, 0) + 1

    distribution = [
        {
            "field": f,
            "hit_rate": round(field_hit.get(f, 0) / field_total[f], 4) if field_total.get(f) else None,
            "hits": field_hit.get(f, 0),
            "total": field_total.get(f, 0),
        }
        for f in field_total
    ]
    return {
        "kind": "condition_stability",
        "disclaimer": "本接口不提供收益回测或收益承诺，仅描述当前时点条件命中分布。",
        "selected_count": result.meta.selected_count,
        "universe_size": result.meta.universe_size,
        "distribution": distribution,
        "result_meta": result.meta,
    }
