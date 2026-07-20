"""Phase 0 scorecard — the stable baseline we design against.

Runs the REAL Feed classifier (classify_and_match_node, full 5-stage) plus the
REAL tier decision (_determine_tier) on the 40-fact Alex-confirmed gold set, and
reports the three numbers that actually matter:

  1. Domain accuracy   — right top-level domain (BP.x)          [routing works?]
  2. Section accuracy  — right section (BP.x.y)                 [narrowing works?]
  3. Auto-file precision — of facts the system files WITHOUT asking Alex,
                           what % are placed on the correct node?  [KB stays clean?]

Also reports exact-leaf accuracy (strict + lenient with alternatives), the
review rate, and a per-tier breakdown. Writes evaluation/phase0_scorecard.txt
and evaluation/phase0_scorecard.json for tracking across runs.

Run: python -m evaluation.phase0_scorecard
Makes live Bedrock calls (~40 facts x several LLM calls). Takes a few minutes.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.disable(logging.WARNING)

_GOLD = Path(__file__).parent / "gold_standard.json"
_OUT_TXT = Path(__file__).parent / "phase0_scorecard.txt"
_OUT_JSON = Path(__file__).parent / "phase0_scorecard.json"

# Tiers that result in the fact being STORED immediately without Alex
# confirming the placement first (see handle_raw_text: auto_file_batch and
# flagged_batch are stored; soft_ask/ask go to the review queue instead).
AUTO_FILED_TIERS = {"auto_file", "auto_file_flagged"}


def _domain(node_id):
    return ".".join(node_id.split(".")[:2]) if node_id else None


def _section(node_id):
    parts = node_id.split(".") if node_id else []
    return ".".join(parts[:3]) if len(parts) >= 3 else node_id


def main():
    from web.handlers.feed_handler import classify_and_match_node, _determine_tier

    gold = json.loads(_GOLD.read_text())
    n = len(gold)

    exact = section = domain = lenient = 0
    tier_counts = {"auto_file": 0, "auto_file_flagged": 0, "soft_ask": 0, "ask": 0}
    autofiled_total = autofiled_correct = 0
    af_domain_ok = af_section_ok = af_wrong_domain = 0
    rows = []
    detail = []

    for i, g in enumerate(gold, 1):
        exp = g["proposed_node_id"]
        alts = {a["node_id"] for a in g.get("alternatives", []) if a.get("node_id")}
        acceptable = {exp} | alts

        try:
            result = classify_and_match_node(g["fact"])
            got = result.get("node_id")
            tier = _determine_tier(result, source="alex_direct")
        except Exception as e:  # noqa: BLE001
            got, tier, result = f"ERR:{str(e)[:25]}", "ask", {}

        is_exact = got == exp
        is_section = _section(got) == _section(exp)
        is_domain = _domain(got) == _domain(exp)
        is_lenient = got in acceptable

        exact += is_exact
        section += is_section
        domain += is_domain
        lenient += is_lenient
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

        if tier in AUTO_FILED_TIERS:
            autofiled_total += 1
            autofiled_correct += is_exact  # correct = exact node (strict)
            af_domain_ok += is_domain
            af_section_ok += is_section
            af_wrong_domain += (0 if is_domain else 1)

        rows.append(
            f"  [{'x' if is_exact else ' '}exact][{'x' if is_section else ' '}sec]"
            f"[{'x' if is_domain else ' '}dom] {tier:18} "
            f"exp={exp:12} got={str(got):14} | {g['fact'][:44]}"
        )
        detail.append({
            "id": g["id"], "fact": g["fact"][:80], "expected": exp, "got": got,
            "tier": tier, "exact": is_exact, "section": is_section,
            "domain": is_domain, "lenient": is_lenient,
            "confidence": result.get("confidence"),
        })
        print(f"  [{i}/{n}] {tier:18} exp={exp:12} got={str(got):14}", flush=True)

    autofile_precision = (
        100 * autofiled_correct / autofiled_total if autofiled_total else None
    )
    review_rate = 100 * (tier_counts["soft_ask"] + tier_counts["ask"]) / n

    summary = [
        *rows,
        "",
        f"PHASE 0 SCORECARD — {n} Alex-confirmed gold facts",
        f"  run at: {datetime.now(timezone.utc).isoformat()}",
        "",
        "  ── The three numbers that matter ──",
        f"  1. Domain accuracy      : {domain}/{n} = {100*domain/n:.0f}%",
        f"  2. Section accuracy     : {section}/{n} = {100*section/n:.0f}%",
        f"  3. Auto-file precision (of {autofiled_total} auto-filed):",
        f"       domain  : "
        + (f"{af_domain_ok}/{autofiled_total} = {100*af_domain_ok/autofiled_total:.0f}%  <- wrong-domain pollution metric"
           if autofiled_total else "n/a"),
        f"       section : "
        + (f"{af_section_ok}/{autofiled_total} = {100*af_section_ok/autofiled_total:.0f}%"
           if autofiled_total else "n/a"),
        f"       exact   : "
        + (f"{autofiled_correct}/{autofiled_total} = {autofile_precision:.0f}%  (leaf — Phase 1b)"
           if autofiled_total else "n/a"),
        f"       wrong-domain auto-files: {af_wrong_domain}",
        "",
        "  ── Supporting numbers ──",
        f"  Exact-leaf (strict)     : {exact}/{n} = {100*exact/n:.0f}%",
        f"  Exact-leaf (w/ alts)    : {lenient}/{n} = {100*lenient/n:.0f}%",
        f"  Review rate (soft_ask+ask): {tier_counts['soft_ask']+tier_counts['ask']}/{n} = {review_rate:.0f}%",
        "",
        "  ── Tier breakdown ──",
        f"  auto_file (no review)   : {tier_counts['auto_file']}",
        f"  auto_file_flagged       : {tier_counts['auto_file_flagged']}",
        f"  soft_ask                : {tier_counts['soft_ask']}",
        f"  ask (Alex must place)   : {tier_counts['ask']}",
    ]
    text = "\n".join(summary)
    _OUT_TXT.write_text(text)
    _OUT_JSON.write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(),
        "n": n,
        "domain_accuracy": round(100*domain/n, 1),
        "section_accuracy": round(100*section/n, 1),
        "exact_leaf_strict": round(100*exact/n, 1),
        "exact_leaf_lenient": round(100*lenient/n, 1),
        "auto_file_precision": round(autofile_precision, 1) if autofile_precision is not None else None,
        "auto_filed_total": autofiled_total,
        "review_rate": round(review_rate, 1),
        "tier_counts": tier_counts,
        "detail": detail,
    }, indent=2))
    print("\n" + text)


if __name__ == "__main__":
    main()
