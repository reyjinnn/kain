"""Package an allowlisted, scanned release without local data or credentials."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.security.secret_scan import ROOT, release_files, scan_files


def package(output, root=ROOT):
    files = list(release_files(root))
    findings = scan_files(files, root)
    if findings:
        raise ValueError(json.dumps({"status": "refused", "findings": findings}))
    output.parent.mkdir(parents=True, exist_ok=True)
    # Read the same file bytes that get scanned, so a changed source cannot slip
    # between the first check and archive creation.
    from scripts.security.secret_scan import inspect_content, local_secrets
    secrets = local_secrets(root)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for path in files:
                content = path.read_bytes()
                name = path.relative_to(root).as_posix()
                findings = inspect_content(name, content, secrets)
                if findings:
                    raise ValueError(json.dumps({"status": "refused", "findings": findings}))
                archive.writestr("SISA/" + name, content)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {"status": "passed", "files": len(files), "archive": str(output)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "release" / "SISA-upgrade-complete.zip")
    args = parser.parse_args()
    try:
        print(json.dumps(package(args.output), indent=2))
    except ValueError as exc:
        print(str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
