from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings, get_settings
from app.engine.screener import detect_conflicts
from app.models.schema import (
    ClarificationItem,
    Condition,
    ConflictItem,
    Operator,
    ScreeningSpec,
    SpecMeta,
    UniverseSpec,
    UniverseType,
    UnsupportedIntent,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

SUPPORTED_FIELDS = {
    "pe_ttm",
    "pe_mrq",
    "pb_mrq",
    "ps_ttm",
    "pcf_ttm",
    "revenue_yoy",
    "net_profit_yoy",
    "volatility_60d",
    "max_drawdown_60d",
}

SYSTEM_PROMPT = """你是投研产品中的「意图结构化」模块，不是荐股顾问。
任务：把投资者的自然语言选股意图，转成可执行的 ScreeningSpec JSON。
硬性规则：
1. 只输出 JSON，不要 Markdown，不要买卖建议或涨跌预测。
2. 只能使用这些 field：pe_ttm, pe_mrq, pb_mrq, ps_ttm, pcf_ttm, revenue_yoy, net_profit_yoy, volatility_60d, max_drawdown_60d。
3. op 只能是：lt, lte, gt, gte, between, eq。between 的 value 必须是长度为 2 的数组。
4. 模糊词要落到可编辑默认阈值，并在 meta.assumptions 用大白话写明假设（避免 pe_ttm、∈、volatility_60d 等术语；改说「市盈率」「介于」「近两个月股价波动」）。
5. 无法映射的意图放入 unsupported，不要编造字段。
6. 需要用户确认时放入 clarifications（问题也用大白话）。
7. universe 默认 type=sample, limit=40；若提到沪深300可用 type=index, index_code=000300.SH。

推荐映射：
- 估值合理 / 估值不高 → pe_ttm between [5, 35]
- 经营改善 / 业绩改善 → revenue_yoy gte 5 且/或 net_profit_yoy gte 0
- 走势稳定 / 波动不大 → volatility_60d lte 0.25 或 max_drawdown_60d lte 0.15

JSON schema:
{
  "universe": {"type":"sample|index|custom","index_code":"000300.SH","tickers":[],"limit":40},
  "conditions":[{"intent_label":"","field":"","op":"","value":0,"unit":"","required":true,"source_hint":"","confidence":0.8,"note":""}],
  "clarifications":[{"question":"","options":[],"default":null,"related_intent":""}],
  "unsupported":[{"text":"","reason":""}],
  "assumptions":[""]
}
"""


def _extract_followup_answers(text: str) -> dict[str, str]:
    """把多轮补充话术映射到已有澄清答案键，便于规则引擎吸收。"""
    t = text.strip()
    out: dict[str, str] = {}
    if not t:
        return out
    if re.search(r"更严格|收紧|PE.?5.?25|估值更严", t, re.I):
        out["pe_range"] = "更严格(5-25)"
    elif re.search(r"更宽松|放宽|PE.?5.?50", t, re.I):
        out["pe_range"] = "更宽松(5-50)"
    elif re.search(r"默认.*估值|估值.*默认", t):
        out["pe_range"] = "默认(5-35)"

    if re.search(r"更稳|波动更低|≤\s*0\.18|<=\s*0\.18", t):
        out["vol"] = "更稳(≤0.18)"
    elif re.search(r"适中|≤\s*0\.30|<=\s*0\.30", t):
        out["vol"] = "适中(≤0.30)"

    if re.search(r"净利润", t):
        out["growth"] = "净利润同比≥0%"
    elif re.search(r"营收.*10|增长.*10", t):
        out["growth"] = "营收同比≥10%"
    elif re.search(r"营收", t) and re.search(r"改善|成长", t):
        out["growth"] = "营收同比≥5%"

    if re.search(r"估值合理", t) and "pe_range" not in out:
        out["intent_clarify"] = "估值合理"
    if re.search(r"经营改善|业绩改善", t) and "growth" not in out:
        out["intent_clarify"] = "经营改善"
    if re.search(r"走势|稳定|波动", t) and "vol" not in out and "intent_clarify" not in out:
        out.setdefault("intent_clarify", "走势相对稳定")
    return out


def _compose_query(base: str, history: list[dict[str, str]] | None, follow_up: str) -> str:
    parts = [base.strip()]
    for turn in history or []:
        if turn.get("role") == "user" and turn.get("content"):
            parts.append(turn["content"].strip())
    if follow_up.strip():
        parts.append(follow_up.strip())
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return "；".join(uniq)


def _rule_based_parse(query: str, answers: dict[str, str] | None = None) -> ScreeningSpec:
    """无 LLM 时的确定性回退解析，保证主链路可演示。"""
    answers = answers or {}
    q = query.strip()
    conditions: list[Condition] = []
    clarifications: list[ClarificationItem] = []
    unsupported: list[UnsupportedIntent] = []
    assumptions: list[str] = []

    # 应用澄清答案覆盖
    pe_range = [5.0, 35.0]
    if answers.get("pe_range") == "更严格(5-25)":
        pe_range = [5.0, 25.0]
    elif answers.get("pe_range") == "更宽松(5-50)":
        pe_range = [5.0, 50.0]

    vol_th = 0.25
    if answers.get("vol") == "更稳(≤0.18)":
        vol_th = 0.18
    elif answers.get("vol") == "适中(≤0.30)":
        vol_th = 0.30

    growth_th = 5.0
    if answers.get("growth") == "净利润同比≥0%":
        growth_th = 0.0
    elif answers.get("growth") == "营收同比≥10%":
        growth_th = 10.0

    if re.search(r"估值|PE|市盈|便宜|合理", q, re.I):
        conditions.append(
            Condition(
                intent_label="估值合理",
                field="pe_ttm",
                op=Operator.BETWEEN,
                value=pe_range,
                unit="倍",
                source_hint="fuyao valuations/snapshot pe_ttm",
                confidence=0.85,
                note="默认将「估值合理」映射为 PE_TTM 区间，可在条件工作台修改",
            )
        )
        assumptions.append(
            f"你说的「估值合理」，我们先按：市盈率（近一年盈利）大概在 {pe_range[0]}～{pe_range[1]} 倍之间；不满意可在右侧直接改数字。"
        )
        if "pe_range" not in answers:
            clarifications.append(
                ClarificationItem(
                    id="pe_range",
                    question="「估值合理」你更希望市盈率落在哪一档？",
                    options=["默认(5-35)", "更严格(5-25)", "更宽松(5-50)"],
                    default="默认(5-35)",
                    related_intent="估值合理",
                )
            )

    if re.search(r"经营|业绩|改善|成长|增收|盈利", q):
        field = "net_profit_yoy" if answers.get("growth") == "净利润同比≥0%" else "revenue_yoy"
        label = "经营改善"
        conditions.append(
            Condition(
                intent_label=label,
                field=field,
                op=Operator.GTE,
                value=growth_th,
                unit="%",
                source_hint="fuyao financials income-statements YoY",
                confidence=0.8,
            )
        )
        field_cn = "净利润同比增速" if field == "net_profit_yoy" else "营业收入同比增速"
        assumptions.append(
            f"你说的「经营改善」，我们先按：{field_cn} 不低于 {growth_th}%；可在右侧改条件或换指标。"
        )
        if "growth" not in answers:
            clarifications.append(
                ClarificationItem(
                    id="growth",
                    question="「经营改善」你更看重哪一项？",
                    options=["营收同比≥5%", "净利润同比≥0%", "营收同比≥10%"],
                    default="营收同比≥5%",
                    related_intent="经营改善",
                )
            )

    if re.search(r"稳定|波动|回撤|平稳|走势", q):
        conditions.append(
            Condition(
                intent_label="走势相对稳定",
                field="volatility_60d",
                op=Operator.LTE,
                value=vol_th,
                unit="ratio",
                source_hint="derived from fuyao prices kline",
                confidence=0.75,
            )
        )
        assumptions.append(
            f"你说的「走势相对稳定」，我们先按：近约 60 个交易日的股价波动幅度不超过 {vol_th:.0%}；可在右侧放宽或收紧。"
        )
        if "vol" not in answers:
            clarifications.append(
                ClarificationItem(
                    id="vol",
                    question="股价波动你更能接受哪一档？",
                    options=["更稳(≤0.18)", "默认(≤0.25)", "适中(≤0.30)"],
                    default="默认(≤0.25)",
                    related_intent="走势相对稳定",
                )
            )

    # 检测无法映射片段
    known = bool(conditions)
    leftover_patterns = [
        (r"龙头|涨停|必涨|翻倍|推荐买入", "涉及荐股/收益承诺语义，产品拒绝执行该类意图"),
        (r"北向|外资|龙虎榜", "当前 MVP 未接入对应数据字段，已标记为 unsupported"),
    ]
    for pat, reason in leftover_patterns:
        m = re.search(pat, q)
        if m:
            unsupported.append(UnsupportedIntent(text=m.group(0), reason=reason))

    if not known and not unsupported:
        clarifications.append(
            ClarificationItem(
                id="intent_clarify",
                question="未能识别可执行指标。请选择一个方向以便生成条件：",
                options=["估值合理", "经营改善", "走势相对稳定"],
                related_intent=q[:40],
            )
        )
        # 若用户在 answers 里直接选了方向
        choice = answers.get("intent_clarify")
        if choice == "估值合理":
            conditions.append(
                Condition(
                    intent_label="估值合理",
                    field="pe_ttm",
                    op=Operator.BETWEEN,
                    value=[5.0, 35.0],
                    unit="倍",
                    source_hint="fuyao valuations/snapshot pe_ttm",
                )
            )
        elif choice == "经营改善":
            conditions.append(
                Condition(
                    intent_label="经营改善",
                    field="revenue_yoy",
                    op=Operator.GTE,
                    value=5.0,
                    unit="%",
                    source_hint="fuyao financials",
                )
            )
        elif choice == "走势相对稳定":
            conditions.append(
                Condition(
                    intent_label="走势相对稳定",
                    field="volatility_60d",
                    op=Operator.LTE,
                    value=0.25,
                    unit="ratio",
                    source_hint="fuyao kline",
                )
            )

    universe = UniverseSpec(type=UniverseType.SAMPLE, limit=40)
    if re.search(r"沪深.?300|000300", q):
        universe = UniverseSpec(type=UniverseType.INDEX, index_code="000300.SH", limit=50)
        assumptions.append("股票范围按沪深300成分来（若暂时取不到成分，会自动换成样本池并提示）。")
    else:
        assumptions.append("你没指定股票范围时，先在约 40 只样本股票里试筛；也可改成沪深300等范围。")

    raw_conflicts = detect_conflicts(conditions)
    conflicts = [ConflictItem(**c) for c in raw_conflicts]

    return ScreeningSpec(
        universe=universe,
        conditions=conditions,
        conflicts=conflicts,
        clarifications=clarifications if not answers else [c for c in clarifications if c.id not in answers],
        unsupported=unsupported,
        meta=SpecMeta(
            raw_query=query,
            model="rule-based-fallback",
            generated_at=utc_now_iso(),
            assumptions=assumptions,
        ),
    )


def _validate_and_normalize(payload: dict[str, Any], query: str, model: str) -> ScreeningSpec:
    conditions: list[Condition] = []
    for raw in payload.get("conditions") or []:
        field = raw.get("field")
        if field not in SUPPORTED_FIELDS:
            continue
        op = raw.get("op")
        try:
            operator = Operator(op)
        except Exception:  # noqa: BLE001
            continue
        value = raw.get("value")
        if operator == Operator.BETWEEN:
            if not isinstance(value, list) or len(value) != 2:
                continue
            value = [float(value[0]), float(value[1])]
        else:
            value = float(value)
        conditions.append(
            Condition(
                intent_label=raw.get("intent_label") or field,
                field=field,
                op=operator,
                value=value,
                unit=raw.get("unit") or "",
                required=bool(raw.get("required", True)),
                source_hint=raw.get("source_hint") or "",
                confidence=float(raw.get("confidence") or 0.7),
                note=raw.get("note") or "",
            )
        )

    clarifications = [
        ClarificationItem(
            question=c.get("question") or "",
            options=c.get("options") or [],
            default=c.get("default"),
            related_intent=c.get("related_intent") or "",
        )
        for c in (payload.get("clarifications") or [])
        if c.get("question")
    ]
    unsupported = [
        UnsupportedIntent(text=u.get("text") or "", reason=u.get("reason") or "")
        for u in (payload.get("unsupported") or [])
    ]

    uni_raw = payload.get("universe") or {}
    try:
        uni_type = UniverseType(uni_raw.get("type") or "sample")
    except Exception:  # noqa: BLE001
        uni_type = UniverseType.SAMPLE
    universe = UniverseSpec(
        type=uni_type,
        index_code=uni_raw.get("index_code") or "000300.SH",
        tickers=uni_raw.get("tickers") or [],
        limit=int(uni_raw.get("limit") or 40),
    )

    conflicts = [ConflictItem(**c) for c in detect_conflicts(conditions)]
    return ScreeningSpec(
        universe=universe,
        conditions=conditions,
        conflicts=conflicts,
        clarifications=clarifications,
        unsupported=unsupported,
        meta=SpecMeta(
            raw_query=query,
            model=model,
            generated_at=utc_now_iso(),
            assumptions=list(payload.get("assumptions") or []),
        ),
    )


class IntentInterpreter:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @property
    def llm_ready(self) -> bool:
        return bool(self.settings.llm_api_key)

    async def parse(
        self,
        query: str,
        answers: dict[str, str] | None = None,
        history: list[dict[str, str]] | None = None,
        follow_up: str = "",
    ) -> tuple[ScreeningSpec, list[str], bool]:
        notes: list[str] = []
        merged_answers = dict(answers or {})
        # 多轮补充 → 澄清答案
        for turn in history or []:
            if turn.get("role") == "user":
                merged_answers.update(_extract_followup_answers(turn.get("content") or ""))
        if follow_up.strip():
            extracted = _extract_followup_answers(follow_up)
            merged_answers.update(extracted)
            if extracted:
                notes.append(f"已从补充话术吸收澄清：{extracted}")
            else:
                notes.append("已接收多轮补充，并入意图上下文。")

        composed = _compose_query(query, history, follow_up)
        if composed != query.strip():
            notes.append("已合并多轮对话上下文后解析。")

        if merged_answers:
            notes.append("已应用用户澄清答案。")

        if not self.llm_ready:
            notes.append("未配置 LLM_API_KEY，使用规则回退解析（仍生成可编辑 ScreeningSpec）。")
            spec = _rule_based_parse(composed, merged_answers)
            return spec, notes, True

        try:
            client = AsyncOpenAI(
                api_key=self.settings.llm_api_key,
                base_url=self.settings.llm_base_url,
            )
            user_content = {
                "query": query,
                "composed_query": composed,
                "answers": merged_answers,
                "history": history or [],
                "follow_up": follow_up,
            }
            resp = await client.chat.completions.create(
                model=self.settings.llm_model,
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "system",
                        "content": "若提供 history/follow_up，必须吸收其中对阈值的修正，与 answers 一并体现在 conditions。",
                    },
                    {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
                ],
            )
            text = resp.choices[0].message.content or "{}"
            payload = json.loads(text)
            spec = _validate_and_normalize(payload, composed, self.settings.llm_model)
            if merged_answers:
                ruled = _rule_based_parse(composed, merged_answers)
                if ruled.conditions:
                    spec.conditions = ruled.conditions
                    spec.conflicts = ruled.conflicts
                    spec.clarifications = ruled.clarifications
                    spec.meta.assumptions = list(dict.fromkeys(spec.meta.assumptions + ruled.meta.assumptions))
            if not spec.conditions and not spec.clarifications:
                notes.append("模型未产出有效条件，已回退规则解析。")
                return _rule_based_parse(composed, merged_answers), notes, True
            notes.append("AI 已完成意图结构化；入选判定仍由确定性引擎执行。")
            return spec, notes, False
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM parse failed")
            notes.append(f"LLM 调用失败，已回退规则解析：{exc}")
            return _rule_based_parse(composed, merged_answers), notes, True

    def reject_compliance(self, query: str) -> str | None:
        """合规边界：拒绝确定性预测/买卖建议类请求。"""
        patterns = [
            r"保证(挣钱|赚钱|收益)",
            r"一定会涨",
            r"推荐(买入|卖出)",
            r"明天涨停",
            r"稳赚",
        ]
        for p in patterns:
            if re.search(p, query):
                return (
                    "该请求涉及收益承诺或直接买卖建议，产品按合规边界拒绝生成选股结论。"
                    "请改为可验证的财务/估值/波动类条件描述。"
                )
        return None
