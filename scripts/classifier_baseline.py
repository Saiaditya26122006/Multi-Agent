"""Measure real classifier accuracy against the labeled pilot set.

Runs the live classify_and_match_node() (real Bedrock calls) against the
20-fact labeled set in tests/test_precision_mapping_pilot.py and reports exact
node-match and domain-match accuracy. This is the Phase 2 baseline — the number
to improve. Writes results to scripts/classifier_baseline_result.txt.
"""

import ast
import logging
from pathlib import Path

logging.disable(logging.WARNING)

_PILOT = Path(__file__).parent.parent / "tests" / "test_precision_mapping_pilot.py"
_OUT = Path(__file__).parent / "classifier_baseline_result.txt"


def _load_facts() -> list[dict]:
    """Extract PILOT_FACTS from the pilot test module without importing it."""
    for node in ast.parse(_PILOT.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "PILOT_FACTS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    return []


def _domain(node_id: str | None) -> str | None:
    """Return the level-1 domain of a node id, e.g. BP.1.1.1 -> BP.1."""
    return ".".join(node_id.split(".")[:2]) if node_id else None


def main() -> None:
    from web.handlers.feed_handler import classify_and_match_node

    facts = _load_facts()
    exact = section = scored = 0
    rows: list[str] = []

    for f in facts:
        exp = f.get("expected_node_id")
        if not exp:
            continue
        scored += 1
        try:
            got = classify_and_match_node(f["text"]).get("node_id")
        except Exception as e:  # noqa: BLE001 — record failures, don't abort the run
            got = f"ERR:{str(e)[:40]}"
        if got == exp:
            exact += 1
        if _domain(got) == _domain(exp):
            section += 1
        rows.append(
            f"  [{'x' if got == exp else ' '}exact]"
            f"[{'x' if _domain(got) == _domain(exp) else ' '}dom] "
            f"exp={exp:10} got={str(got):14} | {f['text'][:55]}"
        )

    summary = [
        *rows,
        "",
        f"BASELINE on {scored} labeled facts:",
        f"  exact node match : {exact}/{scored} = {100 * exact / scored:.0f}%",
        f"  domain match     : {section}/{scored} = {100 * section / scored:.0f}%",
    ]
    text = "\n".join(summary)
    _OUT.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
