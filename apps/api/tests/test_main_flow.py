import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Force mock mode for tests
os.environ["USE_MOCK_DATA"] = "true"
os.environ["FUYAO_API_KEY"] = ""
os.environ["LLM_API_KEY"] = ""
os.environ["SQLITE_PATH"] = str(Path(__file__).parent / "test_strategies.db")

from app.main import app  # noqa: E402
from app.config import get_settings

get_settings.cache_clear()

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["use_mock_data"] is True


def test_probe_mock():
    r = client.get("/api/probe")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["mode"] == "mock"


def test_main_pipeline_parse_and_screen():
    parse = client.post(
        "/api/intent/parse",
        json={"query": "经营改善、估值合理、走势相对稳定"},
    )
    assert parse.status_code == 200
    data = parse.json()
    spec = data["spec"]
    assert len(spec["conditions"]) >= 2
    assert data["used_fallback"] is True

    run = client.post("/api/screen/run", json={"spec": spec, "exclude_sample_limit": 10})
    assert run.status_code == 200
    result = run.json()
    assert result["meta"]["universe_size"] > 0
    assert "disclaimer" in result["meta"]
    # evidence present
    pool = result["selected"] + result["excluded"]
    assert pool
    for stock in pool[:3]:
        assert stock["condition_evals"]
        for ev in stock["condition_evals"]:
            assert "actual" in ev
            assert ev["actual"]["source"]
            assert ev["kind"] == "fact"


def test_data_missing_required_excludes():
    """模拟必填字段缺失：构造 pe_ttm 条件，对无估值票应排除。"""
    spec = {
        "universe": {"type": "custom", "tickers": ["601012.SH"], "limit": 5},
        "conditions": [
            {
                "id": "c1",
                "intent_label": "估值",
                "field": "pe_ttm",
                "op": "between",
                "value": [1, 10],
                "unit": "倍",
                "required": True,
                "enabled": True,
                "confidence": 1,
                "source_hint": "test",
            }
        ],
        "conflicts": [],
        "clarifications": [],
        "unsupported": [],
        "meta": {"raw_query": "test", "model": "test", "assumptions": []},
    }
    run = client.post("/api/screen/run", json={"spec": spec})
    assert run.status_code == 200
    result = run.json()
    # 601012 mock pe is 55, should be excluded
    assert result["meta"]["selected_count"] == 0


def test_compliance_reject():
    r = client.post("/api/intent/parse", json={"query": "保证挣钱，推荐买入明天涨停的股票"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "compliance_reject"


def test_save_and_compare():
    parse = client.post("/api/intent/parse", json={"query": "估值合理"})
    spec = parse.json()["spec"]
    save = client.post(
        "/api/strategies",
        json={"name": "测试策略", "spec": spec, "result_summary": {"selected_count": 0}},
    )
    assert save.status_code == 200
    assert save.json()["name"] == "测试策略"

    # tighter PE
    right = json_deepcopy(spec)
    for c in right["conditions"]:
        if c["field"] == "pe_ttm":
            c["value"] = [5, 15]

    cmp = client.post("/api/compare", json={"left_spec": spec, "right_spec": right})
    assert cmp.status_code == 200
    body = cmp.json()
    assert "condition_diff" in body
    assert "overlap_selected" in body


def json_deepcopy(obj):
    import copy

    return copy.deepcopy(obj)


def test_clarification_answers_applied():
    r = client.post(
        "/api/intent/parse",
        json={
            "query": "估值合理",
            "answers": {"pe_range": "更严格(5-25)"},
        },
    )
    assert r.status_code == 200
    conds = r.json()["spec"]["conditions"]
    pe = next(c for c in conds if c["field"] == "pe_ttm")
    assert pe["value"] == [5.0, 25.0]


def test_enrich_without_ifind_token():
    r = client.post("/api/enrich/stock", json={"thscode": "600519.SH", "name": "贵州茅台"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "reference"
    assert "不参与" in body["disclaimer"] or "跳过" in body["disclaimer"]


def test_multiturn_followup_tightens_pe():
    r = client.post(
        "/api/intent/parse",
        json={
            "query": "估值合理、经营改善",
            "answers": {},
            "history": [{"role": "user", "content": "估值合理、经营改善"}],
            "follow_up": "估值再严一点，PE用5到25",
        },
    )
    assert r.status_code == 200
    pe = next(c for c in r.json()["spec"]["conditions"] if c["field"] == "pe_ttm")
    assert pe["value"] == [5.0, 25.0]
    assert any("多轮" in n or "澄清" in n for n in r.json()["ai_notes"])
