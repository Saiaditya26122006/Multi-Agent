"""Shared pytest fixtures.

Several suites (rag_service, conversation_store, rag_hooks, assumption_tracker,
build_handler) write to the live Supabase rather than a fixture database, so a
plain `pytest tests/` used to leave its scratch data behind permanently. A single
session added ~191 chunks — batch_test_item_0, dedup_test_..., DECISION [KILL]
fixtures — into the CEO's real knowledge base, where retrieve() is free to hand
them to an agent as evidence for a business plan section. web/handlers/
auto_handler.py already filters one such leak ("unique_retrieval_test") by hand,
which is the symptom of exactly this.

The right fix is a fixture database, but until then: snapshot the row ids that
exist before the run and delete whatever is new afterwards. Only rows that
appeared during the session are touched, so pre-existing data is never at risk.
"""

import logging

import pytest

logger = logging.getLogger(__name__)

# Tables the suite writes into, and the dependent rows that must go first so
# foreign keys don't block the delete.
_TRACKED_TABLES = ("knowledge_base", "pipeline_runs", "bp12_register")


def _client():
    try:
        from memory.supabase_client import supabase

        return supabase
    except Exception as e:  # offline / no creds — nothing to clean
        logger.warning("[conftest] Supabase unavailable, skipping cleanup: %s", e)
        return None


_PAGE = 1000


def _ids(supabase, table: str):
    """Every id in `table`, paginated. None if the snapshot could not be completed.

    PostgREST caps an unbounded select at 1000 rows. Taking that truncated list as
    a snapshot would make the diff wrong in both directions: new rows past the cap
    go unnoticed, and — far worse — a pre-existing row absent from the first page
    but present in the second looks new and gets deleted. Page explicitly.

    Failure returns None rather than an empty set: an empty "before" would make the
    diff every row in the table, and cleanup would delete the lot.
    """
    ids = set()
    try:
        start = 0
        while True:
            rows = (
                supabase.table(table)
                .select("id")
                .range(start, start + _PAGE - 1)
                .execute()
                .data
                or []
            )
            ids.update(r["id"] for r in rows)
            if len(rows) < _PAGE:
                return ids
            start += _PAGE
    except Exception as e:
        logger.warning("[conftest] Could not snapshot %s, skipping cleanup: %s", table, e)
        return None


def _purge(supabase, table: str, ids: list) -> None:
    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        if table == "knowledge_base":
            for col in ("chunk_id_a", "chunk_id_b"):
                try:
                    supabase.table("chunk_relationships").delete().in_(col, batch).execute()
                except Exception:
                    logger.debug("[conftest] no chunk_relationships rows for %s", col)
            try:
                supabase.table("evidence_links").delete().in_("chunk_id", batch).execute()
            except Exception:
                logger.debug("[conftest] no evidence_links rows to clear")
        try:
            supabase.table(table).delete().in_("id", batch).execute()
        except Exception as e:
            logger.error("[conftest] Failed purging %s: %s", table, e)


@pytest.fixture(scope="session", autouse=True)
def purge_rows_created_by_tests():
    """Delete rows the test session added to the live database."""
    supabase = _client()
    if supabase is None:
        yield
        return

    before = {t: _ids(supabase, t) for t in _TRACKED_TABLES}

    yield

    for table in _TRACKED_TABLES:
        baseline = before.get(table)
        after = _ids(supabase, table)
        if baseline is None or after is None:
            # Without both snapshots there is no safe way to tell new rows from
            # pre-existing ones. Leave the data alone.
            logger.warning("[conftest] Incomplete snapshot for %s — not cleaning up", table)
            continue

        new_ids = list(after - baseline)
        if not new_ids:
            continue
        logger.info("[conftest] Removing %d test row(s) from %s", len(new_ids), table)
        _purge(supabase, table, new_ids)
