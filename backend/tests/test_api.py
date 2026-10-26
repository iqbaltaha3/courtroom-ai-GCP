import pytest
from fastapi.testclient import TestClient

from app.database import library
from app.main import app
from app.routes import cases
from app.services.task_registry import registry

client = TestClient(app)


@pytest.fixture(autouse=True)
def temporary_case_storage(tmp_path, monkeypatch):
    """Keep API test uploads and SQLite writes outside real case storage."""
    monkeypatch.setattr(cases, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(library, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(library, "LIBRARY_DB", tmp_path / "library.db")
    registry.cases.clear()
    yield
    registry.cases.clear()


def test_root():
    """Verify root."""
    r = client.get("/")
    assert r.status_code == 200
    assert "Courtroom" in r.json()["name"]


def test_health():
    """Verify health."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "judges" in body


def test_list_judges():
    """Verify list judges."""
    r = client.get("/api/v1/judges")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "id" in data[0]
    assert "display_name" in data[0]


def test_create_case():
    """Verify create case."""
    r = client.post("/api/v1/cases")
    assert r.status_code == 200
    body = r.json()
    assert "case_id" in body
    assert body["status"] == "created"
    assert not list(cases.STORAGE_DIR.rglob("meta.json"))

    cid = body["case_id"]
    r2 = client.get(f"/api/v1/cases/{cid}")
    assert r2.status_code == 200
    assert r2.json()["case_id"] == cid


def test_terminate_created_case_is_noop():
    """Verify terminate created case is noop."""
    r = client.post("/api/v1/cases")
    cid = r.json()["case_id"]

    term = client.post(f"/api/v1/cases/{cid}/terminate")

    assert term.status_code == 200
    assert term.json()["ok"] is True
    assert term.json()["status"] == "created"


def test_case_not_found():
    """Verify case not found."""
    r = client.get("/api/v1/cases/does-not-exist")
    assert r.status_code == 404


def test_statute_search():
    """Verify statute search."""
    r = client.get("/api/v1/statutes/search", params={"q": "theft", "act": "BNS", "limit": 3})
    assert r.status_code == 200
    body = r.json()
    assert "results" in body


def test_library_empty_or_list():
    """Verify library empty or list."""
    r = client.get("/api/v1/library/cases")
    assert r.status_code == 200
    body = r.json()
    assert "cases" in body
    assert "total" in body


def test_library_update_missing():
    """Verify library update missing."""
    r = client.patch(
        "/api/v1/library/cases/no-such-id",
        json={"title": "X"},
    )
    assert r.status_code == 404


def test_health_reports_failed_statute_database(monkeypatch):
    """A failed data check must not be reported as healthy just because it returned an error dictionary."""
    from app.database import statutes

    monkeypatch.setattr(statutes, "get_db_stats", lambda: {"ok": False, "error": "Missing database"})
    assert client.get("/health").json()["statute_ok"] is False


def test_agent_details_live_and_saved():
    """Usage remains accessible after live state is removed, with older metrics unavailable."""
    from app.database.cases import get_connection, init_schema, save_artifact
    from app.services.run_context import RunContext
    from app.services.task_registry import StageState

    cid = client.post("/api/v1/cases").json()["case_id"]
    path = cases.STORAGE_DIR / cid / "case.db"
    conn = get_connection(path)
    init_schema(conn)
    metrics = [
        {
            "stage": "case_manager",
            "model": "gemini-2.5-pro",
            "status": "completed",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
    ]
    state = registry.get(cid)
    state.stages = [StageState("case_manager", "Case Manager", status="done")]
    state.runtime = RunContext(path, metrics=metrics)
    save_artifact(conn, "case_manager_audit_1", {"acceptable": True})
    result = client.get(f"/api/v1/cases/{cid}/agent-details").json()["agents"]
    assert result["case_manager"]["calls"] == 1
    assert "1 factual review" in result["case_manager"]["steps"][-1]
    assert result["judge"]["available"] is False
    save_artifact(conn, "llm_metrics", metrics)
    save_artifact(conn, "run_state", state.to_dict())
    registry.cases.clear()
    assert client.get(f"/api/v1/cases/{cid}/agent-details").json()["agents"] == result
    save_artifact(conn, "llm_metrics", [{"model": "gemini-2.5-pro", "usage": {}}])
    assert not client.get(f"/api/v1/cases/{cid}/agent-details").json()["agents"]["case_manager"]["available"]
    conn.close()
    assert client.get("/api/v1/cases/invalid/agent-details").status_code == 404
