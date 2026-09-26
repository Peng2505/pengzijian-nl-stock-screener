from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "") -> str:
    uid = uuid4().hex[:12]
    return f"{prefix}{uid}" if prefix else uid


class Operator(str, Enum):
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    BETWEEN = "between"
    EQ = "eq"


class UniverseType(str, Enum):
    INDEX = "index"
    CUSTOM = "custom"
    SAMPLE = "sample"


class UniverseSpec(BaseModel):
    type: UniverseType = UniverseType.SAMPLE
    index_code: Optional[str] = Field(
        default="000300.SH",
        description="指数 thscode，如 000300.SH",
    )
    tickers: list[str] = Field(default_factory=list)
    limit: int = Field(default=40, ge=5, le=200)


class Condition(BaseModel):
    id: str = Field(default_factory=lambda: new_id("c_"))
    intent_label: str = Field(..., description="对应自然语言意图片段")
    field: str = Field(..., description="可执行字段名，如 pe_ttm / revenue_yoy / volatility_60d")
    op: Operator
    value: float | list[float]
    unit: str = ""
    required: bool = True
    source_hint: str = Field(default="", description="数据来源提示")
    confidence: float = Field(default=0.7, ge=0, le=1)
    enabled: bool = True
    note: str = ""


class ConflictItem(BaseModel):
    condition_ids: list[str]
    reason: str
    suggestion: str


class ClarificationItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("q_"))
    question: str
    options: list[str] = Field(default_factory=list)
    default: Optional[str] = None
    related_intent: str = ""


class UnsupportedIntent(BaseModel):
    text: str
    reason: str


class SpecMeta(BaseModel):
    raw_query: str = ""
    model: str = ""
    generated_at: str = Field(default_factory=utc_now_iso)
    assumptions: list[str] = Field(default_factory=list)


class ScreeningSpec(BaseModel):
    id: str = Field(default_factory=lambda: new_id("spec_"))
    universe: UniverseSpec = Field(default_factory=UniverseSpec)
    conditions: list[Condition] = Field(default_factory=list)
    conflicts: list[ConflictItem] = Field(default_factory=list)
    clarifications: list[ClarificationItem] = Field(default_factory=list)
    unsupported: list[UnsupportedIntent] = Field(default_factory=list)
    meta: SpecMeta = Field(default_factory=SpecMeta)
    version: int = 1


class DataPointEvidence(BaseModel):
    field: str
    value: Any = None
    unit: str = ""
    as_of: Optional[str] = None
    source: str = ""
    request_id: Optional[str] = None
    status: Literal["ok", "missing", "error", "stale"] = "ok"
    message: str = ""


class ConditionEval(BaseModel):
    condition_id: str
    intent_label: str
    field: str
    passed: Optional[bool] = None
    expected: str = ""
    actual: DataPointEvidence
    kind: Literal["fact"] = "fact"


class StockResult(BaseModel):
    thscode: str
    ticker: str
    name: str
    selected: bool
    score: float = 0.0
    condition_evals: list[ConditionEval] = Field(default_factory=list)
    summary: str = ""
    data_quality: Literal["complete", "partial", "failed"] = "complete"


class ScreeningRunMeta(BaseModel):
    run_id: str = Field(default_factory=lambda: new_id("run_"))
    started_at: str = Field(default_factory=utc_now_iso)
    finished_at: Optional[str] = None
    universe_size: int = 0
    selected_count: int = 0
    excluded_count: int = 0
    data_mode: Literal["live", "mock"] = "live"
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "本产品仅提供基于公开/授权数据的条件筛选与解释，"
        "不构成投资建议，不承诺收益，不输出确定性涨跌预测。"
    )


class ScreeningResult(BaseModel):
    spec: ScreeningSpec
    meta: ScreeningRunMeta
    selected: list[StockResult] = Field(default_factory=list)
    excluded: list[StockResult] = Field(default_factory=list)
    excluded_sample_limit: int = 20


class ChatTurn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ParseIntentRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=2000)
    answers: dict[str, str] = Field(default_factory=dict)
    history: list[ChatTurn] = Field(default_factory=list)
    follow_up: str = Field(default="", max_length=1000)


class ParseIntentResponse(BaseModel):
    spec: ScreeningSpec
    ai_notes: list[str] = Field(default_factory=list)
    used_fallback: bool = False


class RunScreenRequest(BaseModel):
    spec: ScreeningSpec
    exclude_sample_limit: int = Field(default=20, ge=0, le=100)


class SaveStrategyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    spec: ScreeningSpec
    result_summary: Optional[dict[str, Any]] = None
    notes: str = ""


class StrategyRecord(BaseModel):
    id: str
    name: str
    spec: ScreeningSpec
    result_summary: Optional[dict[str, Any]] = None
    notes: str = ""
    created_at: str
    updated_at: str


class CompareRequest(BaseModel):
    left_spec: ScreeningSpec
    right_spec: ScreeningSpec


class CompareResponse(BaseModel):
    condition_diff: list[dict[str, Any]]
    left_result: ScreeningResult
    right_result: ScreeningResult
    overlap_selected: list[str]
    only_left: list[str]
    only_right: list[str]


class MonitorDraftRequest(BaseModel):
    name: str
    spec: ScreeningSpec
    schedule: str = "daily_close"


class MonitorDraft(BaseModel):
    id: str
    name: str
    spec: ScreeningSpec
    schedule: str
    status: Literal["draft"] = "draft"
    created_at: str = Field(default_factory=utc_now_iso)
    note: str = "监控任务仅保存草稿，未接入实盘推送。"


class HealthResponse(BaseModel):
    status: str
    fuyao_configured: bool
    llm_configured: bool
    ifind_configured: bool = False
    use_mock_data: bool
    time: str = Field(default_factory=utc_now_iso)


class ProbeResponse(BaseModel):
    ok: bool
    mode: Literal["live", "mock"]
    detail: dict[str, Any] = Field(default_factory=dict)


class EnrichStockRequest(BaseModel):
    thscode: str = Field(..., min_length=4, max_length=32)
    name: str = ""


class EnrichStockResponse(BaseModel):
    available: bool
    kind: Literal["reference"] = "reference"
    disclaimer: str
    thscode: str = ""
    quote: dict[str, Any] | None = None
    news: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
