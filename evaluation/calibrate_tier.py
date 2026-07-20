"""Calibrate the auto-file rule OFFLINE against captured signals.

Reads evaluation/phase0_signals.json (from capture_signals.py) and evaluates
candidate auto-file rules with zero LLM cost. For each rule reports:
  - auto-file precision (of what it auto-files, % correct — the goal metric)
  - auto-file volume (how many of N it auto-files)
  - review rate (the price paid)

The goal: find the rule with the HIGHEST volume whose precision >= TARGET.
That is the sweet spot — clean KB, least review burden.

Run: python -m evaluation.calibrate_tier
"""

import json
from pathlib import Path

_SIG = Path(__file__).parent / "phase0_signals.json"
TARGET_PRECISION = 95.0

# "Correct" for auto-file precision = exact node match. (A fact auto-filed to a
# merely-in-the-right-section node is still misfiled from the KB's view.)
CORRECT_KEY = "exact"


def _rule_llm_high(r):
    """CURRENT behaviour: auto-file whenever confidence == high or medium+typed.

    Mirrors _determine_tier for source='alex_direct': high or medium -> auto-filed
    (auto_file / auto_file_flagged), only none_fit/low -> review.
    """
    s = r["signals"]
    if s.get("none_fit") or not r["got"] or s.get("confidence") == "low":
        return False
    return s.get("confidence") in ("high", "medium")


def _rule_validated_only(r):
    """Auto-file only when the LLM validation pass confirmed placement."""
    s = r["signals"]
    return bool(s.get("validated")) and not s.get("none_fit") and bool(r["got"])


def _rule_validated_and_domain(r):
    """Validated AND the pick's domain agrees with the domain router."""
    s = r["signals"]
    return (bool(s.get("validated")) and s.get("domain_agreement")
            and not s.get("none_fit") and bool(r["got"]))


def _rule_validated_domain_singlerouter(r):
    """Validated AND domain agrees AND router was NOT torn (1 domain only)."""
    s = r["signals"]
    router = s.get("domain_router_ids") or []
    return (bool(s.get("validated")) and s.get("domain_agreement")
            and len(router) == 1 and not s.get("none_fit") and bool(r["got"]))


def _rule_validated_domain_inpool(r):
    """Validated AND domain agrees AND the pick is in the embedding pool."""
    s = r["signals"]
    return (bool(s.get("validated")) and s.get("domain_agreement")
            and s.get("pick_in_embedding_pool")
            and not s.get("none_fit") and bool(r["got"]))


def _rule_all_signals(r):
    """Strictest: validated AND domain agrees AND single router AND in pool."""
    s = r["signals"]
    router = s.get("domain_router_ids") or []
    return (bool(s.get("validated")) and s.get("domain_agreement")
            and len(router) == 1 and s.get("pick_in_embedding_pool")
            and not s.get("none_fit") and bool(r["got"]))


RULES = {
    "CURRENT (conf>=medium)": _rule_llm_high,
    "validated_only": _rule_validated_only,
    "validated + domain_agree": _rule_validated_and_domain,
    "validated + domain + single_router": _rule_validated_domain_singlerouter,
    "validated + domain + in_pool": _rule_validated_domain_inpool,
    "ALL (val+dom+single+pool)": _rule_all_signals,
}


def evaluate(records, rule):
    af = [r for r in records if rule(r)]
    correct = sum(1 for r in af if r[CORRECT_KEY])
    n = len(records)
    prec = 100 * correct / len(af) if af else None
    return {
        "auto_filed": len(af),
        "correct": correct,
        "precision": prec,
        "review": n - len(af),
        "review_rate": 100 * (n - len(af)) / n,
    }


def main():
    data = json.loads(_SIG.read_text())
    records = data["records"]
    n = data["n"]

    print(f"Calibrating auto-file rules against {n} gold facts "
          f"(captured {data['run_at']})")
    print(f"Goal: max auto-file volume with precision >= {TARGET_PRECISION:.0f}%\n")
    print(f"{'RULE':40} {'auto-filed':>10} {'precision':>10} {'review%':>9}  meets?")
    print("-" * 82)

    best = None
    for name, rule in RULES.items():
        m = evaluate(records, rule)
        prec_str = f"{m['precision']:.0f}%" if m["precision"] is not None else "n/a"
        meets = m["precision"] is not None and m["precision"] >= TARGET_PRECISION
        flag = "  ** MEETS" if meets else ""
        print(f"{name:40} {m['auto_filed']:>3}/{n:<6} {prec_str:>10} "
              f"{m['review_rate']:>8.0f}%{flag}")
        if meets and (best is None or m["auto_filed"] > best[1]["auto_filed"]):
            best = (name, m)

    print("-" * 82)
    if best:
        name, m = best
        print(f"\nBEST rule meeting >= {TARGET_PRECISION:.0f}% precision: '{name}'")
        print(f"  auto-files {m['auto_filed']}/{n} at {m['precision']:.0f}% precision, "
              f"review rate {m['review_rate']:.0f}%")
    else:
        print(f"\nNo rule reaches {TARGET_PRECISION:.0f}% precision on this set.")
        print("  -> the classifier itself must improve (Phase 1b), OR accept a")
        print("     lower precision target, OR auto-file at SECTION level instead of leaf.")

    # Also show: what fraction of misfiles the CURRENT rule lets through vs best
    cur = evaluate(records, _rule_llm_high)
    print(f"\nCURRENT rule: auto-files {cur['auto_filed']}/{n} at "
          f"{cur['precision']:.0f}% precision "
          f"({cur['auto_filed'] - cur['correct']} misfiled into KB).")


if __name__ == "__main__":
    main()
