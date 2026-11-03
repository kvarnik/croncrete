"""Run one email through the compiled workflow. Example-owned email loading.

Usage: run_email.py <email_id> --workflow <triage.py> --base-url <mocks>
Exit 0 on completed, 1 on halted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from croncrete.runtime.executor import load_workflow, run  # noqa: E402
from croncrete.runtime.tools_client import ToolsClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("email_id")
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--state-db", default=".croncrete/state.db")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    email = httpx.get(f"{base}/inbox/{args.email_id}", timeout=10).json()
    mod = load_workflow(args.workflow)
    client = ToolsClient(base, list(mod.SCOPES))
    result = run(args.workflow, email, tools=client, state_db=args.state_db)
    print(f"status: {result.status}")
    print(f"run_id: {result.run_id}")
    if result.status == "completed":
        print(json.dumps(result.output, indent=2, sort_keys=True))
        return 0
    print(f"failed_step: {result.failed_step}")
    print(json.dumps(result.error, indent=2, sort_keys=True, default=str)[:2000])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
