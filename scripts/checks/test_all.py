"""One cross-platform entry point for the checks implemented so far."""
from __future__ import annotations

import subprocess
import sys
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = [
    [sys.executable, "-m", "unittest", "discover", "-s", "backend/tests", "-p", "test_*.py"],
    ["node", "scripts/checks/check_frontend.mjs"],
    ["node", "frontend/node_modules/typescript/bin/tsc", "--project", "frontend/tsconfig.json", "--noEmit"],
    ["node", "frontend/test/test_browser.cjs"],
    ["node", "frontend/test/test_ai_browser.cjs"],
    ["node", "frontend/test/test_request_browser.cjs"],
    ["node", "frontend/test/test_custom_colors.cjs"],
    ["node", "frontend/test/test_contrast.cjs"],
    ["node", "frontend/test/test_english.cjs"],
    ["node", "frontend/test/test_upgrade.cjs"],
    ["node", "frontend/test/test_3d.cjs"],
    ["node", "frontend/test/test_image_model.cjs"],
    ["node", "frontend/test/test_image_reconstruction.cjs"],
    ["node", "frontend/test/test_projects.cjs"],
    ["node", "frontend/test/test_runtime.cjs"],
    ["node", "frontend/test/test_overlays.cjs"],
    ["node", "frontend/test/test_ui_races.cjs"],
    [sys.executable, "scripts/security/secret_scan.py"],
]


def main():
    results = []
    for command in COMMANDS:
        started = time.monotonic()
        result = subprocess.run(command, cwd=ROOT)
        results.append({"command": command, "exit_code": result.returncode, "elapsed_s": round(time.monotonic()-started, 2)})
        destination = ROOT / "artifacts" / "phase-0-5-checks.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({"passed": all(row["exit_code"]==0 for row in results), "completed": len(results), "required": len(COMMANDS), "checks": results}, indent=2), encoding="utf-8")
        if result.returncode:
            return result.returncode
    print("All current phase checks passed (backend/PDF, browser, AI, accessibility, contrast, secrets).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
