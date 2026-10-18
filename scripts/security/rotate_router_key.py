"""Rotate only the local 9Router credential currently referenced by SISA.

Uses installed 9Router 0.5.99's /api/keys contract. No credential is printed or
written to reports. An inaccessible dashboard or unmatched key stops the job.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.services.ai import NoRedirect


def rotate(execute=False):
    root = Path(__file__).resolve().parents[2]
    env_file = root / ".env.local"
    original = env_file.read_text(encoding="utf-8")
    settings = {name.strip(): value.strip() for line in original.splitlines()
                for name, sep, value in [line.partition("=")] if sep and not name.startswith("#")}
    old_key = settings.get("NINEROUTER_KEY", "")
    url = urlsplit(settings.get("NINEROUTER_URL", "http://127.0.0.1:20128/v1"))
    if url.hostname not in ("localhost", "127.0.0.1", "::1") or url.scheme not in ("http", "https") or url.username or url.password or not old_key:
        return {"status": "pending", "reason": "LOCAL_ROUTER_REQUIRED"}
    origin = f"{url.scheme}://{url.netloc}"
    opener = urllib.request.build_opener(NoRedirect())

    def request(path, method="GET", body=None, key=old_key):
        req = urllib.request.Request(origin + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method=method)
        with opener.open(req, timeout=15) as response:
            return json.loads(response.read(1024 * 1024))

    keys = request("/api/keys").get("keys", [])
    matching = [item for item in keys if item.get("key") == old_key]
    if len(matching) != 1:
        return {"status": "pending", "reason": "EXACT_KEY_NOT_FOUND"}
    old_record = matching[0]
    if not execute:
        return {"status": "ready", "reason": "EXACT_KEY_MATCHED"}
    new_record = request("/api/keys", "POST", {"name": "SISA rotated 2026-10-13"})
    new_key, new_id = new_record.get("key"), new_record.get("id")
    if not new_key or not new_id or new_key == old_key:
        return {"status": "pending", "reason": "KEY_CREATION_FAILED"}
    if "access" in old_record:
        request("/api/keys/" + quote(new_id, safe=""), "PUT", {"access": old_record["access"]})
    # Non-billable access check before replacing the working local credential.
    request("/v1/models", key=new_key)
    updated_lines = ["NINEROUTER_KEY=" + new_key if line.partition("=")[0].strip() == "NINEROUTER_KEY" else line
                     for line in original.splitlines()]
    temporary = env_file.with_name(".env.local.tmp")
    temporary.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    os.replace(temporary, env_file)
    # Disable the exact leaked record. Do not touch any other application's key.
    request("/api/keys/" + quote(old_record["id"], safe=""), "PUT", {"isActive": False}, key=new_key)
    status = request("/api/keys/" + quote(old_record["id"], safe=""), key=new_key).get("key", {})
    if status.get("isActive") is not False:
        return {"status": "pending", "reason": "OLD_KEY_REVOCATION_UNVERIFIED", "local_key_updated": True}
    return {"status": "complete", "new_key_access_checked": True, "old_key_disabled": True, "local_key_updated": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        result = rotate(args.execute)
    except urllib.error.HTTPError as exc:
        result = {"status": "pending", "reason": "ROUTER_HTTP_" + str(exc.code)}
    except Exception:
        # Exception text/tracebacks can contain request credentials.
        result = {"status": "pending", "reason": "ROUTER_OPERATION_FAILED"}
    output = Path(__file__).resolve().parents[2] / "artifacts" / "phase-0" / "key-rotation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
