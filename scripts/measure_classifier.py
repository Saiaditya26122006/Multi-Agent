"""Measure the live classifier against the CORRECT gold standard.

Runs classify_and_match_node() on each of the 40 gold facts and scores against
the Alex-accepted answer key (evaluation/gold_standard.json) — the correct
labels, not the pilot's known-bad ones. Reports exact-node and domain-level
accuracy, overall and split by confidence/review flag.

Run AFTER scripts/reembed_architecture.py finishes (needs the canonical node
embeddings). Writes evaluation/classifier_measurement.txt.
"""

import json
import logging
from pathlib import Path

logging.disable(logging.WARNING)

_GOLD = Path(__file__).parent.parent / "evaluation" / "gold_standard.json"
_OUT = Path(__file__).parent.parent / "evaluation" / "classifier_measurement.txt"


def _domain(node_id: str | None) -> str | None:
    return ".".join(node_id.split(".")[:2]) if node_id else None


def main() -> None:
    from web.handlers.feed_handler import classify_and_match_node

    gold = json.loads(_GOLD.read_text())
    rows, exact, dom = [], 0, 0
    # accuracy on the subset I was confident about (not flagged for Alex review)
    conf_total = conf_exact = 0

    for g in gold:
        exp = g["proposed_node_id"]
        try:
            got = classify_and_match_node(g["fact"]).get("node_id")
        except Exception as e:  # noqa: BLE001
            got = f"ERR:{str(e)[:30]}"
        is_exact = got == exp
        is_dom = _domain(got) == _domain(exp)
        exact += is_exact
        dom += is_dom
        if not g["needs_alex_review"]:
            conf_total += 1
            conf_exact += is_exact
        rows.append(
            f"  [{'x' if is_exact else ' '}exact][{'x' if is_dom else ' '}dom]"
            f"{'(rev)' if g['needs_alex_review'] else '     '} "
            f"exp={exp:12} got={str(got):14} | {g['fact'][:48]}"
        )

    n = len(gold)
    summary = [
        *rows,
        "",
        f"CLASSIFIER MEASUREMENT vs canonical gold ({n} facts):",
        f"  exact node match : {exact}/{n} = {100*exact/n:.0f}%",
        f"  domain match     : {dom}/{n} = {100*dom/n:.0f}%",
        f"  exact on high-confidence (non-review) subset: "
        f"{conf_exact}/{conf_total} = {100*conf_exact/conf_total:.0f}%"
        if conf_total
        else "  (no non-review facts)",
    ]
    text = "\n".join(summary)
    _OUT.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
