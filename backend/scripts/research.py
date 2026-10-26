"""Run legal research from a terminal using the API service workflow."""

import argparse
import json

from app.services.research_runner import run_research

if __name__ == "__main__":
    # A user-supplied question replaces the old hardcoded sample case.
    parser = argparse.ArgumentParser(description="Research an Indian legal question")
    parser.add_argument("query")
    job = run_research(parser.parse_args().query)
    print(json.dumps(job, indent=2, ensure_ascii=False))
    raise SystemExit(0 if job["status"] == "completed" else 1)
