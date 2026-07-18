"""Merge my hand-adjudication with the embedding shortlists into a reviewable
gold-standard draft for Alex.

Produces:
  evaluation/gold_standard_draft.json  — machine-readable answer key
  evaluation/GOLD_STANDARD_DRAFT.md    — table for Alex to confirm/correct

Each fact carries a PROPOSED correct node + confidence + rationale, plus the
top alternatives from the embedding shortlist so Alex can pick a different node
without hunting through 746. Facts marked review=True are genuine judgment calls
where Alex's answer is the authority. Run: python -m scripts.build_gold_draft
"""

import json
import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_SHORT = _ROOT / "evaluation" / "gold_shortlists.json"
_ARCH = _ROOT / "ceo_data" / "bp_architecture.json"
_OUT_JSON = _ROOT / "evaluation" / "gold_standard_draft.json"
_OUT_MD = _ROOT / "evaluation" / "GOLD_STANDARD_DRAFT.md"

# My adjudication, keyed by fact index (1-based, matching the shortlist order).
# (proposed_node_id, confidence, needs_alex_review, rationale)
# review=True where the fact genuinely spans nodes or the shortlist misled.
ADJ = {
    1:  ("BP.1.1.2",   "high", False, "Defines the claim as the system's unit of analysis — a product-definition statement, not a process check."),
    2:  ("BP.6.6.1",   "med",  True,  "'Easiest first pilots' = initial adoption target / GTM sequencing; could also live under a BP.9 GTM node."),
    3:  ("BP.8.5.5",   "med",  True,  "'More defensible long term' = a defensibility assumption; the journals-vs-schools framing could also be market sequencing."),
    4:  ("BP.2",       "low",  True,  "This is the core demand/urgency problem (why researchers need this). Shortlist surfaced no BP.2 urgency node — Alex to place within BP.2."),
    5:  ("BP.9.1.4",   "med",  True,  "'Pay for risk containment not efficiency' = value-capture/WTP motivation; could also be a BP.2 problem-framing statement."),
    6:  ("BP.8.6.3",   "med",  True,  "'Buyers value auditability' = an institutional trust factor; could also be a buyer-value node under BP.5."),
    7:  ("BP.9.1.4",   "high", False, "Monetization model = value capture logic. Clear."),
    8:  ("BP.9.5.3",   "med",  True,  "'Unit of SALE' is a business-model concept (BP.9.5 unit economics). NOTE embedding trap: top hit BP.1.1.2 'Unit of Analysis' is the wrong 'unit'."),
    9:  ("BP.9.1.4",   "low",  True,  "'Value unit' mixes the validation unit (BP.1.1.2) and value capture (BP.9.1.4). Alex to disambiguate."),
    10: ("BP.9.2.1",   "med",  True,  "Researcher pricing tier = pricing model definition; could sit under value capture."),
    11: ("BP.9.2.1",   "med",  True,  "Doctoral pricing model = pricing model definition."),
    12: ("BP.1.5.1.1", "high", False, "Explicitly a category hypothesis → Category Hypothesis Formulation (not Product Identity)."),
    13: ("BP.8.2.4",   "med",  True,  "Turnitin/iThenticate as analogy = an institutional alternative/comparable; could be Competitor Registry (BP.8.1.3)."),
    14: ("BP.8.1.3",   "med",  True,  "'Need to identify direct competitors' = competitor registry/identification (an open gap, not evidence requirements)."),
    15: ("BP.8.1.5",   "high", False, "Adjacent tools = Adjacent and Substitute Solutions. Clear."),
    16: ("BP.7.2",     "med",  True,  "GDPR manuscript handling = data processing/privacy governance; specific child could be BP.7.4.4 (handling) — Alex to pick 7.2 vs 7.4."),
    17: ("BP.7.3.4",   "low",  True,  "Third-party LLM API use = an AI-governance/prohibited-use or data-processing risk. Shortlist weak — Alex to place in BP.7.3 vs BP.7.2."),
    18: ("BP.7.2.2",   "med",  True,  "Data transfer (likely cross-border) = data processing activity; could be BP.7.5.2 geographic compliance."),
    19: ("BP.7.4.1",   "med",  False, "Manuscript confidentiality = confidential data types under BP.7.4."),
    20: ("BP.7.1.4",   "med",  True,  "Copyright/IP ownership = a legal obligation. NOTE embedding trap: top hit BP.5.3.2 'Budget Ownership' is the wrong 'ownership'."),
    21: ("BP.9.1.1",   "med",  True,  "Describes the value proposition (reduce rejection risk / improve readiness). Could be BP.1.1.3 core function. 9.1.1 not in shortlist — verify."),
    22: ("BP.1.1.3",   "med",  True,  "Describes the product's core function/workflow (extract→map→check). Embedding trap: matched BP.3.4/BP.11 evidence-governance nodes on 'maps evidence'."),
    23: ("BP.1.3",     "low",  True,  "Use cases / jobs served (quality control, competitiveness, supervision). Shortlist weak — Alex to place in BP.1.3 workflow or BP.2 demand."),
    24: ("BP.9.1.1",   "low",  True,  "Product benefits / what it prevents = value proposition. Embedding trap: matched prohibited-claims governance nodes."),
    25: ("BP.6.1",     "med",  True,  "Discovery finding (spoke to dean, awareness confirmed, no budget). = BP.6 discovery evidence, not a BP.5 budget assumption."),
    26: ("BP.9.1.4",   "high", False, "Monetization model = value capture. Clear."),
    27: ("BP.5.1.1",   "med",  True,  "Target users = end-user definition; the classifier's BP.4.4.1 (ICP) is also defensible — Alex to pick users vs ICP."),
    28: ("BP.5.4.4",   "high", False, "CIO/IT veto over procurement = procurement blockers. Clear."),
    29: ("BP.6.1",     "low",  True,  "Discovery finding (dean awareness). Embedding matched acceptance-check nodes wrongly — Alex to place in BP.6 discovery."),
    30: ("BP.9.2.2",   "med",  False, "Per-department bundles = pricing structure."),
    31: ("BP.1.1.7.4", "high", False, "MVP readiness claim = MVP State Criteria. (This is the fact the live classifier MISSED with None.)"),
    32: ("BP.5.5.1",   "med",  True,  "Annual contracts vs academic procurement cycle = budget/procurement cycle."),
    33: ("BP.8.1.3",   "med",  True,  "A COMPETITOR's pricing = competitor registry (BP.8), not our pricing (BP.9). Alex to confirm 8 vs 9."),
    34: ("BP.5.3.2",   "high", False, "Who controls budget = Budget Ownership. Clear (top hit 0.45)."),
    35: ("BP.7.5.2",   "med",  True,  "Data residency in EU = geographic compliance requirement; could be BP.7.2.4 storage/retention."),
    36: ("BP.9.2.1",   "med",  True,  "Freemium = a pricing model. Embedding trap: top hit BP.7.4 confidentiality is unrelated."),
    37: ("BP.1.1.3",   "med",  True,  "Product's internal knowledge structure (11 categories) = core diagnostic function. Embedding trap: 'category' pulled market-category nodes."),
    38: ("BP.2.5.5",   "med",  True,  "'Researchers need X' = a demand hypothesis assumption (BP.2), not a product-function definition."),
    39: ("BP.6.5.3",   "med",  False, "Stat about distrust = trust friction inventory."),
    40: ("BP.5.3.5",   "med",  True,  "'Institutions will pay' = a funding-source/WTP assumption; could sit under BP.6.4 WTP signals."),
}


def _titles() -> dict:
    arch = json.loads(_ARCH.read_text()).get("nodes", [])
    return {
        n["node_id"]: n.get("node_title", "")
        for n in arch
        if re.match(r"^BP\.\d", str(n.get("node_id", "")))
    }


def main() -> None:
    shortlists = json.loads(_SHORT.read_text())
    titles = _titles()

    records = []
    for i, f in enumerate(shortlists, start=1):
        node_id, conf, review, rationale = ADJ[i]
        alts = [
            {"node_id": c["node_id"], "title": c["title"]}
            for c in f["candidates"][:3]
            if c["node_id"] != node_id
        ][:3]
        records.append(
            {
                "id": i,
                "fact": f["text"],
                "source": f["source"],
                "proposed_node_id": node_id,
                "proposed_title": titles.get(node_id, "<domain-level / verify>"),
                "confidence": conf,
                "needs_alex_review": review,
                "rationale": rationale,
                "alternatives": alts,
            }
        )

    _OUT_JSON.write_text(json.dumps(records, indent=2))

    n_review = sum(1 for r in records if r["needs_alex_review"])
    lines = [
        "# Gold-Standard Draft — Fact → Correct BP Node",
        "",
        "This is a **proposed answer key** for measuring classifier accuracy. It does",
        "NOT change any node, fact, or the architecture — it only records which node",
        "each fact *should* file under. Please confirm each row or correct the node.",
        "",
        f"- {len(records)} facts. **{n_review} marked for your review** (genuine judgment",
        "  calls or where the fact spans nodes).",
        "- 'Alternatives' are other plausible nodes if you disagree with the proposal.",
        "- Confidence is mine, not yours — even 'high' rows are yours to overrule.",
        "",
        "| # | Fact | Proposed node | Conf | Review? | Why |",
        "|---|------|---------------|------|---------|-----|",
    ]
    for r in records:
        alts = "; ".join(f"{a['node_id']} {a['title']}" for a in r["alternatives"])
        lines.append(
            f"| {r['id']} | {r['fact'][:70]} | **{r['proposed_node_id']}** "
            f"{r['proposed_title']} | {r['confidence']} | "
            f"{'YES' if r['needs_alex_review'] else ''} | {r['rationale']}"
            + (f" _Alt: {alts}_" if alts else "")
            + " |"
        )

    _OUT_MD.write_text("\n".join(lines))
    print(f"Wrote {_OUT_JSON}\nWrote {_OUT_MD}")
    print(f"{len(records)} facts, {n_review} flagged for Alex")


if __name__ == "__main__":
    main()
