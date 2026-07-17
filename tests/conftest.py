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
from unittest.mock import Mock

import pytest

logger = logging.getLogger(__name__)


def _bind_real_clients_before_any_test_module_mocks_them():
    """Import the real service modules before any test module can hijack them.

    test_phase2_new_agents, test_phase2_e2e and test_full_pipeline_e2e each build a
    fully mocked world at import time — `sys.modules["boto3"] = MagicMock()`, same
    for supabase, upstash_redis and spade. pytest imports every test module during
    collection, so from that point boto3 is a MagicMock for the whole process and
    every later test that really embeds dies on "the JSON object must be str,
    bytes or bytearray, not MagicMock". That is why this suite passed file-by-file
    but lost ~42 tests when run as `pytest tests/`, and why it has never been a
    trustworthy regression signal.

    A module binds `boto3` at its own import time, so importing the real services
    here — conftest is imported before any test module — makes them hold the real
    boto3 and supabase permanently. A later sys.modules swap then only affects
    modules imported after it, which is exactly the isolation those files want.

    Those files go further, though, and overwrite attributes on the real modules
    outright (`supa_mod.supabase = mock_supabase`). No import ordering defends
    against that, so capture the real objects here and hand them to
    restore_real_clients below.
    """
    try:
        import boto3
        import supabase

        import services.embedding_service  # noqa: F401
        import services.rag_service  # noqa: F401
        import memory.redis_client as redis_mod
        import memory.supabase_client as supa_mod

        _REAL["supabase"] = supa_mod.supabase
        _REAL["redis_client"] = getattr(redis_mod, "redis_client", None)
        _REAL["RedisClient"] = getattr(redis_mod, "RedisClient", None)
        # The real third-party modules, so sys.modules can be put back. rag_service
        # does `from supabase import create_client` inside _get_supabase(), i.e. it
        # resolves out of sys.modules on every call.
        _REAL["modules"] = {"boto3": boto3, "supabase": supabase}
    except Exception as e:
        logger.warning("[conftest] Could not pre-bind real clients: %s", e)


_REAL: dict = {}

# These build a fully mocked world at import time and must keep it.
_MOCK_WORLD_MODULES = {
    "test_phase2_new_agents",
    "test_phase2_e2e",
    "test_full_pipeline_e2e",
}

_bind_real_clients_before_any_test_module_mocks_them()


@pytest.fixture(autouse=True)
def restore_real_clients(request):
    """Give every non-mock-world test the real clients back.

    The mock-world modules assign their mocks straight onto memory.supabase_client
    and memory.redis_client at import time, which leaks to every test in the
    session. Restore the real objects for tests outside those modules, and leave
    the mocks alone for tests inside them.
    """
    module_name = getattr(request.module, "__name__", "").split(".")[-1]
    if module_name in _MOCK_WORLD_MODULES or not _REAL:
        yield
        return

    import sys

    import memory.redis_client as redis_mod
    import memory.supabase_client as supa_mod

    saved = (supa_mod.supabase, redis_mod.redis_client, redis_mod.RedisClient)
    saved_modules = {n: sys.modules.get(n) for n in _REAL["modules"]}

    supa_mod.supabase = _REAL["supabase"]
    if _REAL.get("redis_client") is not None:
        redis_mod.redis_client = _REAL["redis_client"]
    if _REAL.get("RedisClient") is not None:
        redis_mod.RedisClient = _REAL["RedisClient"]
    for name, module in _REAL["modules"].items():
        sys.modules[name] = module

    _drop_mocked_singletons()
    try:
        yield
    finally:
        supa_mod.supabase, redis_mod.redis_client, redis_mod.RedisClient = saved
        for name, module in saved_modules.items():
            if module is not None:
                sys.modules[name] = module


def _drop_mocked_singletons() -> None:
    """Clear cached clients that were built while the world was mocked.

    The mocks are plain classes (MockSupabaseClient), not unittest.mock.Mock
    subclasses, so an isinstance check misses them — match on the name instead.
    A real cached client is left in place so tests don't pay a fresh TLS handshake.
    """
    for module_name, attr in (
        ("services.embedding_service", "_bedrock_client"),
        ("services.rag_service", "_supabase_client"),
    ):
        try:
            module = __import__(module_name, fromlist=[attr])
        except Exception:
            continue
        cached = getattr(module, attr, None)
        if cached is None:
            continue
        if isinstance(cached, Mock) or type(cached).__name__.startswith("Mock"):
            setattr(module, attr, None)
            logger.debug("[conftest] Dropped mocked %s.%s", module_name, attr)


# Tables the suite writes into, and the dependent rows that must go first so
# foreign keys don't block the delete.
_TRACKED_TABLES = ("knowledge_base", "pipeline_runs", "bp12_register")


def _client():
    """The real Supabase client, captured before any test module could swap it.

    Reading memory.supabase_client.supabase here would hand back a mock: collection
    runs before fixtures, so the mocked-world modules have already assigned their
    MockSupabaseClient onto that attribute by the time this runs. The cleanup then
    silently no-ops and the test rows stay in the CEO's knowledge base.
    """
    client = _REAL.get("supabase")
    if client is None:
        logger.warning("[conftest] No real Supabase client captured, skipping cleanup")
    return client


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
