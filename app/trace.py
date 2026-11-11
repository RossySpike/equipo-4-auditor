"""Append-only JSONL audit trace persistence."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_WRITE_LOCK = threading.Lock()


def append_trace(trace_path: Path, record: dict[str, Any]) -> None:
    """Append one JSON line, creating the parent directory when needed.

    A process-level lock serializes writes so concurrent requests cannot
    interleave partial lines.
    """

    line = json.dumps(record, ensure_ascii=False, default=str)
    with _WRITE_LOCK:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
