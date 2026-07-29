"""Fill the 23 canonical nodes that have no purpose/required_output.

Per Alex: fill them meaningfully so the classifier can use them. Titled nodes get
a purpose/required_output derived from the title + parent context; the few fully
blank placeholders get a coherent title inferred from the parent's pattern.
Updates ceo_data/bp_architecture.json and re-embeds just these nodes.

Run: python -m scripts.fill_empty_nodes
"""

import json
import logging
import re
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ARCH = Path(__file__).parent.parent / "ceo_data" / "bp_architecture.json"

# node_id -> (title_or_None_to_keep, purpose, required_output)
FILLS = {
    # BP.1.9 — actor workflows
    "BP.1.9.1": (None, "Define the end-to-end journey a researcher follows to upload a manuscript and receive a diagnostic, from submission through result delivery.", "A stepwise description of the researcher upload journey: entry point, upload, processing, result retrieval, and follow-up actions."),
    "BP.1.9.2": (None, "Define how the product is used within a doctoral programme, including supervisor and candidate touchpoints.", "A workflow for doctoral-programme usage: who initiates, who reviews, and how diagnostics feed supervision."),
    "BP.1.9.3": (None, "Define how a university research office uses the product for institutional oversight of manuscript readiness and integrity.", "A workflow for research-office usage: intake, batch review, institutional reporting, and escalation."),
    "BP.1.9.4": (None, "Define how journals and editors interact with the product during editorial screening and reviewer-readiness assessment.", "A workflow for journal/editor usage: submission screening, diagnostic review, and editorial decision support."),
    "BP.1.9.5": (None, "Define the permission and sharing model governing who can view, edit, or act on a manuscript's diagnostics across collaborating actors.", "A permission matrix specifying roles, access levels, and sharing rules for collaborators."),
    "BP.1.9.6": (None, "Define when and how the system notifies actors about diagnostic events, status changes, and required actions.", "A notification specification: triggers, recipients, channels, and timing for each event type."),
    # BP.9.7 — team & execution
    "BP.9.7.1": (None, "Identify the capability, domain, and role gaps in the founding team relative to what execution requires.", "A gap analysis mapping required competencies to current team coverage and identified shortfalls."),
    "BP.9.7.2": (None, "Define the sequenced engineering plan required to reach and extend the product's technical milestones.", "A phased engineering roadmap with milestones, dependencies, and resourcing assumptions."),
    "BP.9.7.3": (None, "Define how advisors are selected, engaged, and governed, and what decisions they inform.", "An advisor governance model: selection criteria, engagement terms, scope of input, and review cadence."),
    "BP.9.7.4": (None, "Define the legal and Data Protection Officer support required for compliant operation and deployment.", "A specification of legal/DPO support needs: responsibilities, coverage model, and engagement approach."),
    "BP.9.7.5": (None, "Define how design partners are selected, engaged, and governed during early validation and co-development.", "A design-partner governance model: selection, commitments, data use, and exit rules."),
    "BP.9.7.6": (None, "Define the order and timing of key hires required to execute the plan against funding and milestones.", "A prioritised hiring sequence tied to milestones, budget, and capability gaps."),
    "BP.9.7.7": (None, "Define which capabilities to build in-house versus buy or outsource, with the rationale for each.", "A build-vs-buy decision table across key capabilities with rationale and dependencies."),
    "BP.9.7.8": (None, "Define the recurring operating rhythm — planning, review, and decision cycles — by which the team executes.", "An operating-cadence specification: review types, frequency, owners, and decision rights."),
    # BP.11.7
    "BP.11.7": (None, "Define the role of public grant funding in the capital strategy, including eligibility, targets, and constraints.", "A public-grant funding plan: candidate programmes, eligibility, amounts, timelines, and non-dilutive fit."),
    # Fully blank placeholders — title inferred from parent pattern
    "BP.1.1.10": ("Supplementary Product Definition", "Capture additional product-definition elements that extend BP.1.1 beyond the core identity, unit, and diagnostic nodes.", "A record of any supplementary product-definition element and how it relates to the core definition."),
    "BP.1.1.10.1": ("Supplementary Definition Element", "Hold a specific supplementary product-definition element under BP.1.1.10.", "A single supplementary product-definition element with its rationale."),
    "BP.1.1.10.2": ("Supplementary Definition Element", "Hold a specific supplementary product-definition element under BP.1.1.10.", "A single supplementary product-definition element with its rationale."),
    "BP.3.4.10": ("Supplementary Claim-Support Mapping", "Capture additional claim-support mapping elements that extend BP.3.4 beyond the core inventory and mapping nodes.", "A record of any supplementary claim-to-evidence mapping and its sufficiency status."),
    "BP.3.4.10.1": ("Supplementary Mapping Element", "Hold a specific supplementary claim-support mapping element under BP.3.4.10.", "A single supplementary claim-support mapping with its rationale."),
    "BP.3.4.10.2": ("Supplementary Mapping Element", "Hold a specific supplementary claim-support mapping element under BP.3.4.10.", "A single supplementary claim-support mapping with its rationale."),
    # BP.4.3.10 children mirror BP.4.3's inventory/criteria pattern
    "BP.4.3.10.1": ("Use-Case Candidate Inventory", "Compile the inventory of candidate use cases considered under the Use-Case Selection Architecture.", "An inventory of candidate use cases with their defining characteristics."),
    "BP.4.3.10.2": ("Use-Case Selection Criteria", "Define the criteria used to evaluate and select among candidate use cases.", "A set of explicit, comparable criteria for use-case selection."),
}


def main() -> None:
    data = json.loads(_ARCH.read_text())
    by_id = {n["node_id"]: n for n in data["nodes"]}
    filled = []
    for nid, (title, purpose, required) in FILLS.items():
        n = by_id.get(nid)
        if not n:
            logger.warning("node %s not found — skipping", nid)
            continue
        if title and not n.get("node_title"):
            n["node_title"] = title
        n["purpose"] = purpose
        n["required_output"] = required
        filled.append(nid)
    _ARCH.write_text(json.dumps(data, indent=2))
    logger.info("Filled %d nodes in bp_architecture.json", len(filled))

    # verify none remain empty
    remaining = [
        n["node_id"]
        for n in data["nodes"]
        if re.match(r"^BP\.\d", n["node_id"]) and not (n.get("purpose") and n.get("required_output"))
    ]
    logger.info("Nodes still lacking purpose/required_output: %d %s", len(remaining), remaining[:10])

    # re-embed just the filled nodes: delete their arch rows, re-insert
    from services.rag_service import store, _get_supabase, TABLE_NAME

    sb = _get_supabase()
    for nid in filled:
        rows = (
            sb.table(TABLE_NAME)
            .select("id")
            .eq("metadata->>layer", "bp_architecture")
            .eq("metadata->>node_id", nid)
            .execute()
            .data
        )
        for r in rows:
            sb.table(TABLE_NAME).delete().eq("id", r["id"]).execute()
        node = by_id[nid]
        content = (
            f"BP Node {nid}: {node.get('node_title') or ''}; "
            f"Purpose: {node['purpose']}; Required output: {node['required_output']}"
        )
        result = store(
            content=content,
            source_type="ceo_doc",
            section=nid,
            epistemic_status="CONFIRMED",
            confidence=1.0,
            metadata={
                "layer": "bp_architecture",
                "node_id": nid,
                "node_title": node.get("node_title") or "",
                "level": node.get("level", 0),
                "parent_node": node.get("parent_node") or "",
                "purpose": node["purpose"],
                "required_output": node["required_output"],
                "prohibited_claims": node.get("prohibited_claims_inference_patterns") or "",
                "sync_batch": "fill-empty-nodes",
            },
        )
        if not result:
            logger.warning("Node %s not re-embedded: %s", nid, result.outcome.value)
    logger.info("Re-embedded %d filled nodes", len(filled))


if __name__ == "__main__":
    main()
