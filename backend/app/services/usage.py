"""Count model requests and retain provider-reported usage for each stage."""

from contextlib import contextmanager
from time import monotonic

from app.services.run_context import current_run, current_stage


@contextmanager
def model_request(model, operation):
    """Record every attempted request, including failures without token metadata."""
    started = monotonic()
    entry = {
        "stage": current_stage.get(),
        "model": model.removeprefix("google/"),
        "operation": operation,
        "status": "running",
        "usage": {},
    }
    context = current_run.get()
    if context:
        context.metrics.append(entry)
    try:
        yield entry
        entry["status"] = "completed"
    except Exception:
        entry["status"] = "failed"
        raise
    finally:
        entry["seconds"] = round(monotonic() - started, 2)


def summarize_usage(metrics, stage):
    """Aggregate both SDK formats without inventing missing token counts."""
    calls = [m for m in metrics if m.get("stage") == stage]
    models = {}
    for call in calls:
        name = call["model"].removeprefix("google/")
        row = models.setdefault(
            name,
            {
                "model": name,
                "calls": 0,
                "failed_calls": 0,
                "unreported_calls": 0,
                "input_tokens": None,
                "output_tokens": None,
                "reasoning_tokens": None,
                "total_tokens": None,
            },
        )
        row["calls"] += 1
        row["failed_calls"] += call.get("status") == "failed"
        usage = call.get("usage") or {}
        values = {
            "input_tokens": usage.get("prompt_tokens", usage.get("prompt_token_count")),
            "output_tokens": usage.get("completion_tokens", usage.get("candidates_token_count")),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get(
                "reasoning_tokens", usage.get("thoughts_token_count")
            ),
            "total_tokens": usage.get("total_tokens", usage.get("total_token_count")),
        }
        if any(values[k] is None for k in ("input_tokens", "output_tokens", "total_tokens")):
            row["unreported_calls"] += 1
        for key, value in values.items():
            if value is not None:
                row[key] = (row[key] or 0) + value
    return {"calls": len(calls), "models": list(models.values())}
