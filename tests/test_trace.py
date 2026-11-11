"""Append-only JSONL audit trace persistence."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from app.trace import append_trace


def read_lines(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_append_trace_creates_missing_parent_directories(tmp_path):
    trace_path = tmp_path / "nested" / "deeper" / "audit_trace.jsonl"

    append_trace(trace_path, {"execution_id": "E1"})

    assert trace_path.is_file()
    assert read_lines(trace_path) == [{"execution_id": "E1"}]


def test_append_trace_writes_one_json_line_per_record(tmp_path):
    trace_path = tmp_path / "trace.jsonl"

    append_trace(trace_path, {"execution_id": "E1", "step": 1})
    append_trace(trace_path, {"execution_id": "E2", "step": 2})
    append_trace(trace_path, {"execution_id": "E3", "step": 3})

    lines = read_lines(trace_path)
    assert lines == [
        {"execution_id": "E1", "step": 1},
        {"execution_id": "E2", "step": 2},
        {"execution_id": "E3", "step": 3},
    ]
    assert trace_path.read_text(encoding="utf-8").count("\n") == 3


def test_append_trace_appends_without_overwriting_existing_content(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text('{"existing": true}\n', encoding="utf-8")

    append_trace(trace_path, {"execution_id": "new"})

    lines = read_lines(trace_path)
    assert lines[0] == {"existing": True}
    assert lines[1] == {"execution_id": "new"}


def test_append_trace_keeps_unicode_characters_readable(tmp_path):
    trace_path = tmp_path / "trace.jsonl"

    append_trace(trace_path, {"texto": "ñandú, canción y ¡éxito!"})

    raw = trace_path.read_text(encoding="utf-8")
    assert "ñandú" in raw
    assert "\\u00f1" not in raw
    assert read_lines(trace_path)[0]["texto"] == "ñandú, canción y ¡éxito!"


def test_append_trace_serializes_non_json_values_with_str(tmp_path):
    trace_path = tmp_path / "trace.jsonl"

    append_trace(
        trace_path,
        {
            "moment": datetime(2026, 11, 11, tzinfo=timezone.utc),
            "path": Path("data/trace.jsonl"),
            "custom": object(),
        },
    )

    record = read_lines(trace_path)[0]
    assert "2026-11-11" in record["moment"]
    assert record["path"] == "data/trace.jsonl"
    assert isinstance(record["custom"], str)


def test_append_trace_supports_nested_records(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    record = {
        "execution_id": "EXE-1",
        "input_payload": {"input": {"copy": {"caption": "Compra ya"}}},
        "output_payload": {"veredicto": "APROBADO", "checks": [{"resultado": "PASSED"}]},
        "data_sources": [{"source": "perfil_negocio.facts", "ref": "FACT-1"}],
    }

    append_trace(trace_path, record)

    assert read_lines(trace_path) == [record]


def test_append_trace_is_thread_safe_and_never_interleaves_lines(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    total = 120

    def write(index: int) -> None:
        append_trace(trace_path, {"index": index, "padding": "x" * 500})

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(write, range(total)))

    lines = read_lines(trace_path)
    assert len(lines) == total
    assert {line["index"] for line in lines} == set(range(total))
    assert all(len(line["padding"]) == 500 for line in lines)
    assert trace_path.read_text(encoding="utf-8").count("\n") == total


def test_append_trace_writes_flushed_content_immediately(tmp_path):
    trace_path = tmp_path / "trace.jsonl"

    append_trace(trace_path, {"execution_id": "E1"})

    # No explicit close/flush is performed by the test, so the function itself
    # must have flushed the handle.
    assert read_lines(trace_path) == [{"execution_id": "E1"}]
