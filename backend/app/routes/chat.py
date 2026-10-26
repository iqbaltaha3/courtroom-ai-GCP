"""HTTP endpoints for chat."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException

from app.ai.llm import call_llm
from app.config import STORAGE_DIR
from app.database.cases import execute_readonly, get_all_reports, get_connection, get_schema_description
from app.schemas import ChatIn, ChatOut
from app.services.task_registry import registry

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post("/cases/{case_id}/chat", response_model=ChatOut)
def chat(case_id: str, body: ChatIn):
    """Answer a completed-case question using stored reports and read-only SQL results."""
    state = registry.get(case_id)
    if not state:
        raise HTTPException(404, "Case not found")
    if state.status != "completed":
        raise HTTPException(400, "Case Chat is available only after the Judge completes the run")

    schema = get_schema_description()
    db_path = STORAGE_DIR / case_id / "case.db"
    conn = get_connection(db_path)
    reports = get_all_reports(conn)
    conn.close()
    reports_summary = "\n".join(f"- {k}: {v[:200]}..." for k, v in reports.items())

    system = f"""You are the Simulation Chat Agent for a completed Indian Sessions Court case simulation.
You have full knowledge of the SQLite database schema below.
You may generate and reason about SELECT queries. When you need data, write a clear SELECT statement.

SCHEMA:
{schema}

AVAILABLE REPORTS (summaries):
{reports_summary}

Rules:
1. Answer only from the database contents and the stored reports.
2. Never invent facts, names, sections, dates, or case law.
3. If the information is not in the database or reports, reply: Not present in the simulation record.
4. Only SELECT queries. Never write or modify data.
5. If you need to run SQL, place the query alone inside a sql code fence so it can be executed.
6. After seeing results, give a clear natural-language answer in plain formal English.
7. Do not use asterisks, markdown emphasis, or decorative bullet symbols in your final answer. Use numbered points or continuous prose.
"""
    reply = call_llm(system, body.message, max_tokens=1500)
    sql = None
    rows = None
    sql_blocks = re.findall(r"```sql\s*(.*?)```", reply, re.DOTALL | re.IGNORECASE)
    if sql_blocks:
        sql = sql_blocks[0].strip()
        try:
            rows = execute_readonly(sql, db_path=db_path)[:30]
            follow = (
                f"SQL executed successfully. Sample rows:\n{rows}\n\n"
                f"Now answer the user question using only these results and the reports."
            )
            reply = call_llm(system, body.message + "\n\n" + follow, max_tokens=1200)
        except Exception as e:
            reply = f"The query could not be executed: {e}\n\n{reply}"

    return ChatOut(answer=reply, sql=sql, rows=rows)
