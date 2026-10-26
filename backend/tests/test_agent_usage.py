"""Check request accounting across SDKs, retries and concurrent stage contexts."""

import asyncio

import pytest

from app.services.run_context import RunContext, current_run, current_stage
from app.services.usage import model_request, summarize_usage


def test_mixed_models_and_missing_usage(tmp_path):
    """Keep failed requests visible and missing usage distinct from zero tokens."""
    context = RunContext(tmp_path / "case.db")
    token = current_run.set(context)
    stage = current_stage.set("case_manager")
    try:
        with model_request("google/gemini-2.5-flash", "completion") as entry:
            entry["usage"] = {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "completion_tokens_details": {"reasoning_tokens": 5},
            }
        with model_request("gemini-2.5-pro", "completion") as entry:
            entry["usage"] = {
                "prompt_token_count": 40,
                "candidates_token_count": 50,
                "thoughts_token_count": 10,
                "total_token_count": 100,
            }
        with pytest.raises(RuntimeError):
            with model_request("gemini-2.5-pro", "completion"):
                raise RuntimeError("Unavailable")
        summary = summarize_usage(context.metrics, "case_manager")
        assert summary["calls"] == 3
        flash, pro = summary["models"]
        assert flash["total_tokens"] == 30 and flash["reasoning_tokens"] == 5
        assert pro["calls"] == 2 and pro["failed_calls"] == 1 and pro["unreported_calls"] == 1
        assert pro["total_tokens"] == 100 and pro["output_tokens"] == 50
        assert summarize_usage(context.metrics, "judge")["calls"] == 0
    finally:
        current_stage.reset(stage)
        current_run.reset(token)


def test_parallel_stages_do_not_mix_usage(tmp_path):
    """Worker threads inherit their own stage rather than another agent's stage."""
    context = RunContext(tmp_path / "case.db")
    token = current_run.set(context)

    async def run(stage):
        """Run a fake request using the same context propagation as the runner."""
        stage_token = current_stage.set(stage)
        try:

            def request():
                """Capture the model request without calling a provider."""
                with model_request("gemini-2.5-flash", "completion"):
                    pass

            await asyncio.to_thread(request)
        finally:
            current_stage.reset(stage_token)

    async def both():
        """Exercise concurrent stage isolation."""
        await asyncio.gather(run("prosecution_research"), run("defense_research"))

    try:
        asyncio.run(both())
        assert summarize_usage(context.metrics, "prosecution_research")["calls"] == 1
        assert summarize_usage(context.metrics, "defense_research")["calls"] == 1
        assert summarize_usage(context.metrics, "defense_research")["models"][0]["total_tokens"] is None
    finally:
        current_run.reset(token)
