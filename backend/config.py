from __future__ import annotations

import os
from pathlib import Path


def load_local_config():
    """Load server-only settings at startup; explicit process settings win."""
    path = Path(__file__).resolve().parent.parent / ".env.local"
    if not path.is_file():
        return
    allowed = {"NINEROUTER_URL", "NINEROUTER_KEY", "NINEROUTER_IMAGE_KEY", "SISA_AI_MODEL", "SISA_TEXT_MODEL", "SISA_IMAGE_MODEL", "SISA_AI_RESPONSE_FORMAT", "SISA_AI_DIAGNOSTICS", "SISA_DATA_DIR"}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() in allowed:
            os.environ.setdefault(name.strip(), value.strip())
