"""Capture classifier signals + correctness for every gold fact — ONCE.

Runs the real classifier live and dumps, per fact, the full signal set plus
whether the pick was correct (exact / section / domain / lenient). This lets us
calibrate the auto-file rule OFFLINE (evaluation/calibrate_tier.py) without
re-spending Bedrock calls on every rule change.

Run: python -m evaluation.capture_signals   (live, ~6 min)
Writes evaluation/phase0_signals.json
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.disable(logging.WARNING)

_GOLD = Path(__file__).parent / "gold_standard.json"
_OUT = Path(__file__).parent / "phase0_signals.json"


def _domain(nid):
    return ".".join(nid.split(".")[:2]) if nid else None


def _section(nid):
    p = nid.split(".") if nid else []
    return ".".join(p[:3]) if len(p) >= 3 else nid


def main():
    from web.handlers.feed_handler import classify_and_match_node

    gold = json.loads(_GOLD.read_text())
    n = len(gold)
    records = []

    for i, g in enumerate(gold, 1):
        exp = g["proposed_node_id"]
        alts = {a["node_id"] for a in g.get("alternatives", []) if a.get("node_id")}
        acceptable = {exp} | alts
        try:
            result = classify_and_match_node(g["fact"])
        except Exception as e:  # noqa: BLE001
            result = {"node_id": None, "error": str(e)[:60]}
        got = result.get("node_id")
        rec = {
            "id": g["id"],
            "fact": g["fact"],
            "expected": exp,
            "acceptable": sorted(acceptable),
            "got": got,
            "exact": got == exp,
            "section_ok": _section(got) == _section(exp),
            "domain_ok": _domain(got) == _domain(exp),
            "lenient": got in acceptable,
            "signals": result.get("signals", {}),
        }
        records.append(rec)
        s = rec["signals"]
        print(f"  [{i}/{n}] got={str(got):14} exact={rec['exact']!s:5} "
              f"conf={s.get('confidence')} val={s.get('validated')} "
              f"dom_agree={s.get('domain_agreement')} in_pool={s.get('pick_in_embedding_pool')} "
              f"sim={s.get('pick_embedding_sim')}", flush=True)

    _OUT.write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(),
        "n": n,
        "records": records,
    }, indent=2))
    print(f"\nWrote {_OUT} ({n} records)")


if __name__ == "__main__":
    main()
