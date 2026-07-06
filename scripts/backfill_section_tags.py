"""
Backfill script — corrects `section` tags on existing knowledge_base rows
that were stored under the OLD, broken SECTION_MAP in services/ingestion_pipeline.py.

Background
----------
The old SECTION_MAP invented its own section numbering (going up to "19")
that had no relationship to the real BP.1-BP.11 domain structure defined in
ceo_data/bp_architecture.json. This caused coverage_calculator.py's
per-section fill check to permanently miss most content: seven files were
tagged sections 12-19, which don't exist as domains at all, and several
single-digit sections pointed at the wrong domain entirely (e.g. customers.json
was tagged "3" — Evidence Base and Source Governance — when its content,
problem/JTBD, actually belongs in BP.2).

services/ingestion_pipeline.py has already been fixed so all FUTURE ingestion
tags content correctly. This script fixes rows that are already sitting in
Supabase from before that fix, by re-deriving the correct section from each
row's OLD section value plus its content/topic_tags — mirroring exactly the
same classification logic now baked into the corrected chunker functions.

Scope: only touches source_type='ceo_doc' rows with superseded_by IS NULL.
Other source types (conversation, decision, agent_insight, etc.) were never
part of this bug and are left untouched.

Usage:
    python -m scripts.backfill_section_tags            # dry run, prints plan
    python -m scripts.backfill_section_tags --apply     # actually updates rows

Safe to re-run: rows already matching their target section are skipped.
"""

import argparse
import logging
from collections import Counter

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _classify(old_section: str, content: str, topic_tags: list) -> str | None:
    """Return the corrected section for a row, or None if we can't confidently
    classify it (script will skip and log those rather than guess)."""
    tags = set(topic_tags or [])
    content = content or ""

    if old_section == "1":
        return "1"  # product_definition.json — already correct, no-op

    if old_section == "2":
        return None  # nothing was ever tagged "2" under the old scheme

    if old_section == "3":
        # customers.json only. Interview-gap chunks move to BP.6; everything
        # else (the main facts list) moves to BP.2.
        if content.startswith("EVIDENCE GAP:") or "evidence-gap" in tags:
            return "6"
        return "2"

    if old_section == "4":
        # Shared by buyers_icp.json (buyer_personas / icp / facts) and
        # team.json — disambiguate by content prefix / topic tags.
        if content.startswith("Buyer:"):
            return "5"
        if content.startswith("ICP:"):
            return "4"  # unchanged
        if "team" in tags or "organization" in tags:
            return "11"
        return "4"  # generic buyers_icp facts — unchanged default

    if old_section == "5":
        return "8"  # value_proposition.json

    if old_section == "6":
        return "1"  # capabilities.json

    if old_section == "7":
        return "3"  # knowledge_architecture.json

    if old_section == "8":
        # Shared by competitors.json (stays 8) and market_research.json (-> 4).
        if "tam" in tags or "geography" in tags:
            return "4"
        return "8"  # unchanged (competitors.json)

    if old_section == "9":
        return "9"  # gtm_sales.json — already correct, no-op

    if old_section == "10":
        return "9"  # pricing_model.json

    if old_section == "11":
        return "9"  # financials.json

    if old_section == "12":
        return "10"  # validation_requirements.json

    if old_section == "13":
        return "7"  # compliance_risks.json

    if old_section == "15":
        # Shared by assumptions_register.json and constraints.json.
        if content.startswith("Compliance requirement:"):
            return "7"
        return "10"  # strategic_assumptions, uncertainty, assumptions_register facts

    if old_section == "16":
        return "11"  # decision_register.json

    if old_section == "17":
        return "10"  # open_questions.json

    if old_section == "18":
        return "3"  # contradictions.json

    if old_section == "19":
        return "11"  # tasks_register.json

    if old_section == "governance":
        return "governance"  # bp_architecture/prohibited_claims/bp_dependencies — unchanged

    return None  # unrecognized old value — don't guess, flag it


def run(apply: bool = False) -> dict:
    from services.rag_service import _get_supabase, TABLE_NAME

    supabase = _get_supabase()

    result = (
        supabase.table(TABLE_NAME)
        .select("id, section, content, topic_tags")
        .eq("source_type", "ceo_doc")
        .is_("superseded_by", "null")
        .execute()
    )

    rows = result.data or []
    logger.info("[Backfill] Fetched %d ceo_doc rows to inspect.", len(rows))

    plan: list[dict] = []
    unclassified: list[dict] = []
    unchanged = 0

    for row in rows:
        old_section = row.get("section")
        if not old_section:
            continue

        new_section = _classify(old_section, row.get("content", ""), row.get("topic_tags") or [])

        if new_section is None:
            unclassified.append(row)
            continue

        if new_section == old_section:
            unchanged += 1
            continue

        plan.append({
            "id": row["id"],
            "old_section": old_section,
            "new_section": new_section,
            "content_preview": (row.get("content") or "")[:70],
        })

    by_transition = Counter(f"{p['old_section']} -> {p['new_section']}" for p in plan)

    logger.info("[Backfill] %d rows already correct, no change needed.", unchanged)
    logger.info("[Backfill] %d rows need remapping:", len(plan))
    for transition, count in sorted(by_transition.items()):
        logger.info("  %s : %d row(s)", transition, count)

    if unclassified:
        logger.warning(
            "[Backfill] %d rows had an unrecognized old section value and were "
            "left untouched (need manual review): %s",
            len(unclassified),
            sorted(set(r.get("section") for r in unclassified)),
        )

    if not apply:
        logger.info("[Backfill] Dry run only — no rows updated. Re-run with --apply to write changes.")
        return {"planned": len(plan), "unchanged": unchanged, "unclassified": len(unclassified), "applied": False}

    updated = 0
    for item in plan:
        try:
            supabase.table(TABLE_NAME).update({"section": item["new_section"]}).eq("id", item["id"]).execute()
            updated += 1
        except Exception as e:
            logger.error("[Backfill] Failed to update row %s: %s", item["id"], e)

    logger.info("[Backfill] Updated %d/%d rows.", updated, len(plan))

    try:
        from services.coverage_calculator import invalidate_dashboard_cache
        invalidate_dashboard_cache()
        logger.info("[Backfill] Dashboard cache invalidated so coverage recomputes fresh.")
    except Exception as e:
        logger.warning("[Backfill] Could not invalidate dashboard cache: %s", e)

    return {"planned": len(plan), "unchanged": unchanged, "unclassified": len(unclassified), "applied": True, "updated": updated}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill knowledge_base section tags to match the real BP.1-BP.11 domain structure.")
    parser.add_argument("--apply", action="store_true", help="Actually write the changes. Without this flag, only prints the plan.")
    args = parser.parse_args()

    summary = run(apply=args.apply)
    print(summary)
