from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers.api import router

settings = get_settings()

app = FastAPI(
    title="自然语言智能选股与策略解释器",
    description=(
        "AI 负责意图澄清与结构化；确定性引擎负责筛选；扶摇负责金融数据。"
        "不提供投资建议、收益承诺或涨跌预测。"
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
async def root():
    return {
        "product": "自然语言智能选股与策略解释器",
        "docs": "/docs",
        "health": "/api/health",
    }
