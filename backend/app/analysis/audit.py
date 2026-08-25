from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.config import ROOT_DIR


AUDIT_FILE = ROOT_DIR / "data" / "stage1-audit.jsonl"
_LOCK = Lock()


def append_stage1_audit(record: dict[str, Any]) -> None:
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with AUDIT_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        os.chmod(AUDIT_FILE, 0o600)
