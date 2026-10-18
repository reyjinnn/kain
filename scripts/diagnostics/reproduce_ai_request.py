"""Record a single request against the actual API boundary, with secrets removed.

Only --live permits a provider call; without it this records the offline path.
Run from the repository root. Diagnostic output is local and excluded from releases.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.services import ai
from fastapi.testclient import TestClient
from backend import app as service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--format", choices=["prompt", "json_object", "json_schema"], default=None)
    parser.add_argument("--text", default="depan diutamakan hitam, 160×120 mm, boleh satu sambungan")
    parser.add_argument("--output", type=Path, default=Path("artifacts/phase-0/reproduction"))
    args = parser.parse_args()
    if not args.live:
        os.environ["NINEROUTER_KEY"] = ""
    if args.format:
        os.environ["SISA_AI_RESPONSE_FORMAT"] = args.format
    os.environ["SISA_AI_DIAGNOSTICS"] = "1"
    args.output.mkdir(parents=True, exist_ok=True)
    raw_responses = []
    call_errors = []
    original_call = ai._call_chat_api

    def record_call(*call_args, **kwargs):
        try:
            result = original_call(*call_args, **kwargs)
        except Exception as exc:
            call_errors.append({"type": type(exc).__name__, "reason": ai.redact_text(str(exc))[:300]})
            raise
        raw_responses.append(ai.redact_text(result))
        return result

    # Isolated TestClient session: do not reset somebody's running browser session.
    with TestClient(service.app) as client, mock.patch.object(ai, "_call_chat_api", side_effect=record_call):
        state = client.get("/api/state").json()
        response = client.post("/api/ai/request", json={
            "text": args.text, "ai_consent": True,
            "expected_generation_token": state["generation_token"],
        })
        body = response.json()
    (args.output / "model-response.txt").write_text(
        "\n".join(raw_responses) if raw_responses else "Tidak ada balasan model yang diterima.\n", encoding="utf-8")
    (args.output / "api-response.json").write_text(ai.redact_text(json.dumps({
        "live_requested": args.live, "http_status": response.status_code,
        "input": args.text, "call_errors": call_errors, "response": body,
    }, ensure_ascii=False, indent=2)), encoding="utf-8")
    print(json.dumps({"http_status": response.status_code, "mode": body.get("trace", {}).get("mode"),
                      "reason": body.get("trace", {}).get("reason"), "artifacts": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
