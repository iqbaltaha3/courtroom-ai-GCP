import asyncio
from threading import Barrier, Event

import pytest

from app.database.cases import get_all_reports, get_connection, save_report
from app.services import runner
from app.services.run_context import current_run
from app.services.task_registry import Registry


def configure(monkeypatch, tmp_path):
    """Isolate the runner and disable pacing for deterministic lifecycle tests."""
    monkeypatch.setenv("STAGE_SERIAL", "0")
    monkeypatch.setattr(runner, "STAGE_GAP_SECONDS", 0)
    monkeypatch.setattr(runner, "JUDGE_GAP_SECONDS", 0)
    registry = Registry()
    monkeypatch.setattr(runner, "registry", registry)
    monkeypatch.setattr(runner, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(runner.library_store, "upsert_case", lambda *a, **k: None)
    return registry


@pytest.mark.parametrize("serial", ["0", "1"])
def test_production_runner_parallel_handoffs_isolation_and_early_reports(monkeypatch, tmp_path, serial):
    """Verify production runner parallel handoffs isolation and early reports."""
    registry = configure(monkeypatch, tmp_path)
    monkeypatch.setenv("STAGE_SERIAL", serial)
    names = [(title, key) for title, key, _ in runner.PIPELINE_STEPS]
    barriers = {
        (case, phase): Barrier(2, timeout=5)
        for case in ("first", "second")
        for phase in ("consultant", "research")
    }
    prerequisites = {
        "case_manager": ["clarify_extract"],
        "prosecution_consultant": ["case_manager"],
        "defense_consultant": ["case_manager"],
        "prosecution_research": ["prosecution_consultant"],
        "defense_research": ["defense_consultant"],
        "pros_r1": ["prosecution_research", "defense_research"],
        "def_r1": ["pros_r1"],
        "pros_r2": ["def_r1"],
        "def_r2": ["pros_r2"],
        "judge": ["def_r2"],
    }

    def stage(key):
        """Build a fake stage that checks prerequisites before saving its report."""

        def run():
            """Execute the fake stage and verify case-local prerequisites."""
            case = current_run.get().db_path.parent.name
            if serial == "0" and key.endswith(("_consultant", "_research")):
                barriers[(case, key.rsplit("_", 1)[1])].wait()
            conn = get_connection()
            reports = get_all_reports(conn)
            assert all(p in reports for p in prerequisites.get(key, []))
            assert all(value.startswith(case) for value in reports.values())
            save_report(conn, key, f"{case}: {key}")
            conn.close()
            return {"ok": True}

        return run

    monkeypatch.setattr(runner, "PIPELINE_STEPS", [(title, key, stage(key)) for title, key in names])

    async def run_both():
        """Run two cases concurrently and verify report events and case isolation."""
        queues = {}
        for name in ("first", "second"):
            state = registry.create(name)
            queues[name] = asyncio.Queue()
            state.subscribers.add(queues[name])
        await asyncio.gather(
            *[runner.run_pipeline(name, None, [], [], [], "A supplied narrative.") for name in queues]
        )
        for name, queue in queues.items():
            assert registry.get(name).status == "completed"
            events = []
            while not queue.empty():
                events.append(queue.get_nowait())
            completed = next(i for i, e in enumerate(events) if e["type"] == "run_completed")
            report_events = [i for i, e in enumerate(events) if e["type"] == "stage_report"]
            assert len(report_events) == len(names)
            assert all(i < completed for i in report_events)
            conn = get_connection(tmp_path / name / "case.db")
            assert len(get_all_reports(conn)) == len(names)
            conn.close()

    asyncio.run(run_both())


def test_cancellation_drains_workers_and_blocks_downstream(monkeypatch, tmp_path):
    """Verify cancellation drains workers and blocks downstream."""
    registry = configure(monkeypatch, tmp_path)
    started, release, settled = Event(), Event(), Event()
    called = []

    def blocking():
        """Wait until cancellation is requested before attempting a report write."""
        started.set()
        assert release.wait(5)
        try:
            conn = get_connection()
            try:
                save_report(conn, "clarify_extract", "must not publish after termination")
            finally:
                conn.close()
        finally:
            settled.set()
        return {"ok": True}

    def downstream():
        """Record unexpected downstream execution after cancellation."""
        called.append(True)
        return {"ok": True}

    names = [(title, key) for title, key, _ in runner.PIPELINE_STEPS]
    monkeypatch.setattr(
        runner,
        "PIPELINE_STEPS",
        [(title, key, blocking if key == "clarify_extract" else downstream) for title, key in names],
    )

    async def cancel():
        """Request cancellation while a stage worker is still active."""
        state = registry.create("cancelled")
        task = asyncio.create_task(
            runner.run_pipeline("cancelled", None, [], [], [], "A supplied narrative.")
        )
        assert await asyncio.to_thread(started.wait, 5)
        state.cancel_requested = True
        state.runtime.cancelled.set()
        release.set()
        await task
        assert settled.is_set()
        assert state.status == "terminated"
        assert not called
        assert all(stage.status != "running" for stage in state.stages)
        conn = get_connection(tmp_path / "cancelled" / "case.db")
        assert not get_all_reports(conn)
        conn.close()

    asyncio.run(cancel())
