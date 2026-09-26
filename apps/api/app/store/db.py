from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.models.schema import ScreeningSpec, StrategyRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrategyStore:
    def __init__(self, db_path: str | None = None):
        settings = get_settings()
        self.db_path = str(db_path or settings.sqlite_file)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategies (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    result_summary TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS monitor_drafts (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    note TEXT
                )
                """
            )
            conn.commit()

    def save_strategy(
        self,
        name: str,
        spec: ScreeningSpec,
        result_summary: dict[str, Any] | None = None,
        notes: str = "",
    ) -> StrategyRecord:
        sid = uuid4().hex[:12]
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO strategies (id, name, spec_json, result_summary, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sid,
                    name,
                    spec.model_dump_json(),
                    json.dumps(result_summary or {}, ensure_ascii=False),
                    notes,
                    now,
                    now,
                ),
            )
            conn.commit()
        return StrategyRecord(
            id=sid,
            name=name,
            spec=spec,
            result_summary=result_summary,
            notes=notes,
            created_at=now,
            updated_at=now,
        )

    def list_strategies(self) -> list[StrategyRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM strategies ORDER BY created_at DESC"
            ).fetchall()
        result: list[StrategyRecord] = []
        for row in rows:
            result.append(
                StrategyRecord(
                    id=row["id"],
                    name=row["name"],
                    spec=ScreeningSpec.model_validate_json(row["spec_json"]),
                    result_summary=json.loads(row["result_summary"] or "null"),
                    notes=row["notes"] or "",
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
        return result

    def get_strategy(self, strategy_id: str) -> StrategyRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM strategies WHERE id = ?", (strategy_id,)
            ).fetchone()
        if not row:
            return None
        return StrategyRecord(
            id=row["id"],
            name=row["name"],
            spec=ScreeningSpec.model_validate_json(row["spec_json"]),
            result_summary=json.loads(row["result_summary"] or "null"),
            notes=row["notes"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def save_monitor_draft(self, name: str, spec: ScreeningSpec, schedule: str) -> dict[str, Any]:
        mid = uuid4().hex[:12]
        now = _now()
        note = "监控任务仅保存草稿，未接入实盘推送。"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO monitor_drafts (id, name, spec_json, schedule, status, created_at, note)
                VALUES (?, ?, ?, ?, 'draft', ?, ?)
                """,
                (mid, name, spec.model_dump_json(), schedule, now, note),
            )
            conn.commit()
        return {
            "id": mid,
            "name": name,
            "spec": spec.model_dump(),
            "schedule": schedule,
            "status": "draft",
            "created_at": now,
            "note": note,
        }

    def list_monitor_drafts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM monitor_drafts ORDER BY created_at DESC"
            ).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "spec": json.loads(r["spec_json"]),
                "schedule": r["schedule"],
                "status": r["status"],
                "created_at": r["created_at"],
                "note": r["note"],
            }
            for r in rows
        ]
