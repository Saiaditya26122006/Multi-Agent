"""
Pytest configuration for database tests.

Provides admin access (bypasses RLS) and automatic cleanup of every
``knowledge_base`` row a test writes.

The suite runs against live Supabase, so without cleanup each run leaves its
fixtures behind in the production corpus — including plausible-looking fake CEO
facts and ``negative_knowledge`` rows that suppress real proposals. Every write
path used by tests is recorded here and deleted by row id when the test ends.

Long term the right isolation is a dedicated test Supabase project; see
PROJECT_STATE.md, "Test isolation". This fixture is the interim fix.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterator, Optional

import pytest
from dotenv import load_dotenv
from supabase import Client, create_client

# Load environment variables
dotenv_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=dotenv_path)

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# Tables whose test-written rows are removed after each test. Add a table here
# only if its rows can be deleted by id with no further cascade handling.
CLEANUP_TABLES: tuple[str, ...] = ("knowledge_base",)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_row_id(value: Any) -> bool:
    """Return True when value is a real row id rather than a mock or stub.

    Tests that patch ``_get_supabase`` with a MagicMock produce fake ids. Those
    must never reach a DELETE, so anything that is not a UUID string is ignored.
    """
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def _admin_client() -> Optional[Client]:
    """Return a service-role Supabase client, or None if it is not configured."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return None
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


class _RecordingQuery:
    """Wraps an insert/upsert builder and records the ids it writes."""

    def __init__(self, builder: Any, sink: list[str]) -> None:
        self._builder = builder
        self._sink = sink

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the write and record every returned row id."""
        response = self._builder.execute(*args, **kwargs)
        for row in getattr(response, "data", None) or []:
            if isinstance(row, dict) and _is_row_id(row.get("id")):
                self._sink.append(row["id"])
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._builder, name)


class _RecordingTable:
    """Wraps a table handle so inserts and upserts are recorded."""

    def __init__(self, table: Any, sink: list[str]) -> None:
        self._table = table
        self._sink = sink

    def insert(self, *args: Any, **kwargs: Any) -> _RecordingQuery:
        """Record ids written by an insert."""
        return _RecordingQuery(self._table.insert(*args, **kwargs), self._sink)

    def upsert(self, *args: Any, **kwargs: Any) -> _RecordingQuery:
        """Record ids written by an upsert."""
        return _RecordingQuery(self._table.upsert(*args, **kwargs), self._sink)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._table, name)


class _RecordingClient:
    """Wraps a Supabase client so writes to CLEANUP_TABLES are recorded."""

    def __init__(self, client: Client, sink: list[str]) -> None:
        self._client = client
        self._sink = sink

    def table(self, name: str) -> Any:
        """Return a recording handle for cleanup tables, else the real one."""
        table = self._client.table(name)
        if name in CLEANUP_TABLES:
            return _RecordingTable(table, self._sink)
        return table

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _record_store_result(result: Any, sink: list[str]) -> None:
    """Record a StoreResult's id, but only when it wrote a new row.

    SKIPPED_DUPLICATE carries ``duplicate_of`` — the id of a row that already
    existed and may be real corpus data. Deleting it would destroy data the test
    did not create, so only STORED results are recorded.
    """
    outcome = getattr(result, "outcome", None)
    if outcome is None:
        return
    if getattr(outcome, "value", outcome) != "stored":
        return
    if _is_row_id(getattr(result, "id", None)):
        sink.append(result.id)


def _delete_rows(row_ids: list[str]) -> int:
    """Delete rows from knowledge_base by id. Returns the number removed."""
    unique_ids = list(dict.fromkeys(row_ids))
    if not unique_ids:
        return 0

    client = _admin_client()
    if client is None:
        logger.warning(
            "[conftest] %d test rows left behind — SUPABASE_SERVICE_ROLE_KEY "
            "not configured, cannot clean up",
            len(unique_ids),
        )
        return 0

    removed = 0
    for start in range(0, len(unique_ids), 50):
        batch = unique_ids[start : start + 50]
        try:
            response = (
                client.table("knowledge_base").delete().in_("id", batch).execute()
            )
            removed += len(getattr(response, "data", None) or [])
        except Exception as exc:  # noqa: BLE001 — cleanup must report, not mask
            logger.error(
                "[conftest] cleanup failed for %d ids (%s...): %s",
                len(batch),
                batch[0],
                exc,
            )
            raise

    if removed != len(unique_ids):
        logger.warning(
            "[conftest] cleanup removed %d of %d recorded rows",
            removed,
            len(unique_ids),
        )
    return removed


@pytest.fixture
def written_row_ids() -> list[str]:
    """Row ids written to a cleanup table during the current test."""
    return []


@pytest.fixture(autouse=True)
def clean_written_rows(
    written_row_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> Iterator[list[str]]:
    """Record every knowledge_base row a test writes and delete it afterwards.

    Wraps ``rag_service.store`` and ``rag_service.batch_store``, plus every name
    already bound to them by an importing module, so a test does not have to
    register its writes by hand. Direct client writes are covered by the
    recording client returned from the ``admin_db`` fixture.
    """
    import services.rag_service as rag_service

    real_store = rag_service.store
    real_batch_store = rag_service.batch_store

    def recording_store(*args: Any, **kwargs: Any) -> Any:
        """Call the real store() and record the id when a row was written."""
        result = real_store(*args, **kwargs)
        _record_store_result(result, written_row_ids)
        return result

    def recording_batch_store(*args: Any, **kwargs: Any) -> Any:
        """Call the real batch_store() and record every written id."""
        results = real_batch_store(*args, **kwargs)
        for result in results or []:
            _record_store_result(result, written_row_ids)
        return results

    monkeypatch.setattr(rag_service, "store", recording_store)
    monkeypatch.setattr(rag_service, "batch_store", recording_batch_store)

    # Modules that did `from services.rag_service import store` at import time
    # hold their own reference, which the patch above does not reach.
    for module in list(sys.modules.values()):
        if module is None or module is rag_service:
            continue
        for name, original, replacement in (
            ("store", real_store, recording_store),
            ("batch_store", real_batch_store, recording_batch_store),
        ):
            if getattr(module, name, None) is original:
                monkeypatch.setattr(module, name, replacement, raising=False)

    yield written_row_ids

    _delete_rows(written_row_ids)


@pytest.fixture
def admin_db(written_row_ids: list[str]) -> Client:
    """
    Provide admin database client (bypasses RLS).
    Use this for tests that need to INSERT/UPDATE/DELETE test data.

    Writes to CLEANUP_TABLES are recorded and removed when the test ends.
    """
    client = _admin_client()
    if client is None:
        pytest.skip("SUPABASE_SERVICE_ROLE_KEY not configured for tests")

    return _RecordingClient(client, written_row_ids)  # type: ignore[return-value]
