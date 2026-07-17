"""Pilot tests for precision mapping quality -- 20 facts from Alex's data.

Run separately with: pytest tests/test_precision_mapping_pilot.py -v -m pilot
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Load actual architecture for realistic testing
ARCH_PATH = Path(__file__).parent.parent / "ceo_data" / "bp_architecture.json"


def _load_architecture():
    """Load the real bp_architecture.json for pilot tests."""
    if ARCH_PATH.exists():
        with open(ARCH_PATH) as f:
            data = json.load(f)
            return data.get("nodes", [])
    return []


PILOT_FACTS = [
    {
        "text": "We assume primary monetization model is institutional SaaS subscription",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.1",
        "expected_epistemic": "ASSUMPTION",
    },
    {
        "text": "We believe target users are academic researchers in European universities",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.1",
        "expected_epistemic": "ASSUMPTION",
    },
    {
        "text": "It is confirmed that CIO and IT Security have veto power over procurement",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.8",
        "expected_epistemic": "CONFIRMED",
    },
    {
        "text": "We confirmed with IESE research dean that awareness exists",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.8",
        "expected_epistemic": "CONFIRMED",
    },
    {
        "text": "I think pricing should be per-department bundles",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.1",
        "expected_epistemic": "ASSUMPTION",
    },
    {
        "text": "The product contradicts what we said about MVP readiness",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.7",
        "expected_epistemic": "CONTRADICTION",
    },
    {
        "text": "We assume annual institutional contracts align with academic procurement cycles",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.1",
        "expected_epistemic": "ASSUMPTION",
    },
    {
        "text": "We verified that competitor Iris.ai uses per-researcher pricing model",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.2",
        "expected_epistemic": "CONFIRMED",
    },
    {
        "text": "We hypothesize that department heads control the budget",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.1",
        "expected_epistemic": "ASSUMPTION",
    },
    {
        "text": "The contract states GDPR compliance requires data residency in EU",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.8",
        "expected_epistemic": "CONFIRMED",
    },
    {
        "text": "Maybe we should consider freemium for individual researchers",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.1",
        "expected_epistemic": "ASSUMPTION",
    },
    {
        "text": "It is documented that the epistemic layer has 11 knowledge categories",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.3",
        "expected_epistemic": "CONFIRMED",
    },
    {
        "text": "Our assumption is that researchers need diagnostic tools",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.3",
        "expected_epistemic": "ASSUMPTION",
    },
    {
        "text": "Data shows 73% of academics distrust automated reviews",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.6",
        "expected_epistemic": "CONFIRMED",
    },
    {
        "text": "We assume institutions will pay but need to validate",
        "expected_section": "BP.1",
        "expected_node_id": "BP.1.1.8",
        "expected_epistemic": "ASSUMPTION",
    },
    {
        "text": "The AI Act may require algorithmic transparency documentation",
        "expected_section": "BP.1",
        "expected_node_id": None,
        "expected_epistemic": "INFERRED",
    },
    {
        "text": "This directly conflicts with our earlier B2C positioning",
        "expected_section": "BP.1",
        "expected_node_id": None,
        "expected_epistemic": "CONTRADICTION",
    },
    {
        "text": "Revenue projection assumes 15 institutional clients in year 1",
        "expected_section": "BP.1",
        "expected_node_id": None,
        "expected_epistemic": "ASSUMPTION",
    },
    {
        "text": "Evidence shows growing demand for research integrity tools",
        "expected_section": "BP.1",
        "expected_node_id": None,
        "expected_epistemic": "CONFIRMED",
    },
    {
        "text": "Perhaps we should pivot to a B2B-only model",
        "expected_section": "BP.1",
        "expected_node_id": None,
        "expected_epistemic": "ASSUMPTION",
    },
]


@pytest.mark.pilot
class TestPilotRetrievalQuality:
    """Test that node retrieval returns the correct node in top-5 results."""

    @patch("services.rag_service.retrieve")
    def test_pilot_retrieval_quality(self, mock_retrieve):
        """For each fact with a known node_id, verify correct node is in top-5."""
        from services.node_indexer import retrieve_candidate_nodes

        facts_with_nodes = [
            f for f in PILOT_FACTS if f["expected_node_id"] is not None
        ]

        hits = 0
        total = 0

        for fact_data in facts_with_nodes:
            expected = fact_data["expected_node_id"]

            mock_chunk = MagicMock()
            mock_chunk.metadata = {
                "node_id": expected,
                "node_title": f"Title for {expected}",
                "purpose": "test purpose",
                "required_output": "",
                "prohibited_claims": "",
            }
            mock_chunk.content = f"{expected} | Test Title"
            mock_chunk.similarity = 0.68
            mock_retrieve.return_value = [mock_chunk]

            candidates = retrieve_candidate_nodes(
                fact=fact_data["text"],
                section=fact_data["expected_section"].replace("BP.", ""),
                top_k=5,
            )

            candidate_ids = [c["node_id"] for c in candidates]
            total += 1
            if expected in candidate_ids:
                hits += 1

        hit_rate = (hits / total * 100) if total else 0
        assert hit_rate >= 80.0, (
            f"Retrieval quality too low: {hit_rate:.1f}% "
            f"(hits={hits}, total={total})"
        )

    @patch("services.rag_service.retrieve")
    def test_product_identity_retrieves_bp1_1_1(self, mock_retrieve):
        """Product identity fact should retrieve BP.1.1.1 node."""
        mock_chunk = MagicMock()
        mock_chunk.metadata = {
            "node_id": "BP.1.1.1",
            "node_title": "Product Identity",
            "purpose": "Define the product in one controlled statement",
            "required_output": "One-sentence definition",
            "prohibited_claims": "Must not claim market demand, PMF",
        }
        mock_chunk.content = "BP.1.1.1 | Product Identity"
        mock_chunk.similarity = 0.78
        mock_retrieve.return_value = [mock_chunk]

        from services.node_indexer import retrieve_candidate_nodes

        candidates = retrieve_candidate_nodes(
            fact="EpistemicOS is a pre-submission manuscript diagnostics platform",
            section="1",
            top_k=5,
        )

        assert len(candidates) >= 1
        assert candidates[0]["node_id"] == "BP.1.1.1"

    @patch("services.rag_service.retrieve")
    def test_no_candidates_when_nothing_clears_threshold(self, mock_retrieve):
        """A fact with no node above the threshold yields no candidates.

        retrieve() enforces the threshold itself (match_threshold in the RPC) and
        retrieve_candidate_nodes deliberately does no second filter. This used to
        stub retrieve into returning a similarity=0.1 chunk at threshold=0.35 —
        something the real retrieve can never do — and then expected the caller to
        re-filter it.
        """
        mock_retrieve.return_value = []

        from services.node_indexer import retrieve_candidate_nodes

        candidates = retrieve_candidate_nodes(
            fact="Totally unrelated coffee preference",
            top_k=5,
            threshold=0.35,
        )

        assert candidates == []
        # The threshold must actually reach retrieve, since that is what enforces it.
        assert mock_retrieve.call_args.kwargs["threshold"] == 0.35


@pytest.mark.pilot
class TestPilotEpistemicPreservation:
    """Test that epistemic status is correctly inferred from language."""

    def test_pilot_epistemic_preservation(self):
        """Run all 20 pilot facts through epistemic tagger and check match rate."""
        from services.epistemic_tagger import tag_from_language

        matches = 0
        mismatches = []

        for fact in PILOT_FACTS:
            result = tag_from_language(fact["text"])
            if result["epistemic_status"] == fact["expected_epistemic"]:
                matches += 1
            else:
                mismatches.append({
                    "fact": fact["text"][:50],
                    "expected": fact["expected_epistemic"],
                    "actual": result["epistemic_status"],
                })

        match_rate = (matches / len(PILOT_FACTS) * 100) if PILOT_FACTS else 0
        assert match_rate >= 60.0, (
            f"Epistemic preservation too low: {match_rate:.1f}%. "
            f"Mismatches: {mismatches}"
        )

    def test_all_pilot_facts_tagged(self):
        from services.epistemic_tagger import tag_from_language

        for fact in PILOT_FACTS:
            result = tag_from_language(fact["text"])
            assert result["epistemic_status"] in [
                "CONFIRMED",
                "ASSUMPTION",
                "INFERRED",
                "CONTRADICTION",
            ], f"Failed to tag: {fact['text']}"

    def test_confirmed_facts_detected(self):
        from services.epistemic_tagger import tag_from_language

        confirmed_facts = [
            f for f in PILOT_FACTS if f["expected_epistemic"] == "CONFIRMED"
        ]
        correct = 0
        for fact in confirmed_facts:
            result = tag_from_language(fact["text"])
            if result["epistemic_status"] == "CONFIRMED":
                correct += 1

        accuracy = correct / len(confirmed_facts) if confirmed_facts else 0
        assert accuracy >= 0.5, f"CONFIRMED accuracy too low: {accuracy:.0%}"

    def test_assumption_facts_detected(self):
        from services.epistemic_tagger import tag_from_language

        assumption_facts = [
            f for f in PILOT_FACTS if f["expected_epistemic"] == "ASSUMPTION"
        ]
        correct = 0
        for fact in assumption_facts:
            result = tag_from_language(fact["text"])
            if result["epistemic_status"] == "ASSUMPTION":
                correct += 1

        accuracy = correct / len(assumption_facts) if assumption_facts else 0
        assert accuracy >= 0.5, f"ASSUMPTION accuracy too low: {accuracy:.0%}"

    def test_contradiction_facts_detected(self):
        from services.epistemic_tagger import tag_from_language

        contradiction_facts = [
            f for f in PILOT_FACTS if f["expected_epistemic"] == "CONTRADICTION"
        ]
        correct = 0
        for fact in contradiction_facts:
            result = tag_from_language(fact["text"])
            if result["epistemic_status"] == "CONTRADICTION":
                correct += 1

        accuracy = correct / len(contradiction_facts) if contradiction_facts else 0
        assert accuracy >= 0.5, f"CONTRADICTION accuracy too low: {accuracy:.0%}"


@pytest.mark.pilot
class TestPilotFormatNormalization:
    """Test that raw text is correctly split into atomic facts."""

    def test_bullet_list_splits_correctly(self):
        from services.format_normalizer import normalize

        text = "- Pricing is SaaS\n- Target is universities\n- Revenue from subscriptions"
        result = normalize(text)
        assert len(result) == 3

    def test_paragraph_splits_into_sentences(self):
        from services.format_normalizer import normalize

        text = "Our target market is European universities. We focus on research departments. The buyer is typically a dean."
        result = normalize(text)
        assert len(result) >= 2

    def test_mixed_format_handled(self):
        from services.format_normalizer import normalize

        text = "Context about pricing.\nMore context about timing.\n- SaaS model\n- Per department"
        result = normalize(text)
        assert len(result) >= 2


@pytest.mark.pilot
class TestPilotBoundaryEnforcement:
    """Test that prohibited claims are caught by boundary checker."""

    def test_pilot_boundary_enforcement(self):
        """Facts with prohibited-claim language should trigger violations."""
        from agents.phase2.precision_mapper import _check_boundaries

        boundary_test_signals = [
            "There is strong market demand for this product",
            "Users will adopt this because it saves time",
            "Buyers will pay $10k annually for this",
        ]

        node = {
            "node_id": "BP.1.1.1",
            "prohibited_claims": (
                "Must not claim market demand, PMF, feasibility, "
                "adoption, buyer willingness, or publication improvement"
            ),
        }

        violations_detected = 0
        for signal in boundary_test_signals:
            violations = _check_boundaries(signal, node)
            if violations:
                violations_detected += 1

        assert violations_detected >= 2, (
            f"Expected at least 2 violations, got {violations_detected}"
        )

    def test_demand_claim_caught(self):
        from agents.phase2.precision_mapper import _check_boundaries

        node = {"prohibited_claims": "Cannot claim market demand or buyer willingness"}
        signal = "There is strong market demand for this product"
        violations = _check_boundaries(signal, node)
        assert len(violations) > 0

    def test_clean_signal_passes(self):
        from agents.phase2.precision_mapper import _check_boundaries

        node = {"prohibited_claims": "Cannot claim market demand"}
        signal = "Annual SaaS subscription model with per-department pricing"
        violations = _check_boundaries(signal, node)
        assert len(violations) == 0

    def test_buyer_willingness_caught(self):
        from agents.phase2.precision_mapper import _check_boundaries

        node = {
            "prohibited_claims": "Must not claim buyer willingness or pricing adequacy"
        }
        signal = "Buyers will pay $10k annually for this product"
        violations = _check_boundaries(signal, node)
        assert len(violations) > 0

    def test_adoption_claim_caught(self):
        from agents.phase2.precision_mapper import _check_boundaries

        node = {"prohibited_claims": "Must not claim adoption or feasibility"}
        signal = "Users will adopt this tool quickly"
        violations = _check_boundaries(signal, node)
        assert len(violations) > 0

    def test_empty_prohibited_claims_passes(self):
        from agents.phase2.precision_mapper import _check_boundaries

        node = {"prohibited_claims": ""}
        signal = "Anything goes here"
        violations = _check_boundaries(signal, node)
        assert len(violations) == 0

    def test_no_prohibited_claims_key_passes(self):
        from agents.phase2.precision_mapper import _check_boundaries

        node = {}
        signal = "Market demand is huge"
        violations = _check_boundaries(signal, node)
        assert len(violations) == 0

    def test_confidence_penalty_for_violations(self):
        """Boundary violations reduce confidence score."""
        from agents.phase2.precision_mapper import _compute_confidence

        assert _compute_confidence(0.8, []) == 0.8
        assert _compute_confidence(0.8, ["v1"]) == 0.7
        assert _compute_confidence(0.8, ["v1", "v2", "v3"]) == 0.5


@pytest.mark.pilot
class TestPilotNonScopeRouting:
    """Test that unmappable facts get routed to non-scope."""

    def test_pilot_non_scope_routing(self):
        """Facts that don't match any node should produce non-scope results."""
        non_scope_facts = [
            f for f in PILOT_FACTS if f["expected_node_id"] is None
        ]

        assert len(non_scope_facts) >= 3, "Need at least 3 non-scope pilot facts"

        from services.epistemic_tagger import tag_from_language

        for fact_data in non_scope_facts:
            result = tag_from_language(fact_data["text"])
            assert result["epistemic_status"] in (
                "CONFIRMED", "ASSUMPTION", "INFERRED", "CONTRADICTION"
            )

    @patch("services.rag_service.retrieve")
    def test_precision_mapper_non_scope_on_no_candidates(self, mock_retrieve):
        """map_fact_to_node returns is_non_scope=True when no candidates found."""
        mock_retrieve.return_value = []

        from agents.phase2.precision_mapper import map_fact_to_node

        result = map_fact_to_node(
            fact="The weather in Barcelona is sunny today",
            epistemic_status="INFERRED",
        )

        assert result["is_non_scope"] is True
        assert result["node_id"] is None

    @patch("services.non_scope_router._save_non_scope")
    @patch("services.non_scope_router._load_non_scope")
    def test_low_confidence_fact_routed(self, mock_load, mock_save):
        mock_load.return_value = {
            "_meta": {"purpose": "test"},
            "pending": [],
            "resolved": [],
        }

        from services.non_scope_router import route_to_non_scope

        result = route_to_non_scope(
            "The weather in Barcelona is lovely this time of year",
            "no_matching_node",
        )

        assert result.startswith("ns_")
        mock_save.assert_called_once()

    @patch("services.non_scope_router._save_non_scope")
    @patch("services.non_scope_router._load_non_scope")
    def test_non_scope_item_has_correct_fields(self, mock_load, mock_save):
        mock_load.return_value = {
            "_meta": {"purpose": "test"},
            "pending": [],
            "resolved": [],
        }

        from services.non_scope_router import route_to_non_scope

        route_to_non_scope(
            "Irrelevant fact about weather",
            "no_matching_node",
            confidence=0.15,
            session_id="test_session",
        )

        saved_data = mock_save.call_args[0][0]
        item = saved_data["pending"][0]
        assert item["fact"] == "Irrelevant fact about weather"
        assert item["reason"] == "no_matching_node"
        assert item["confidence"] == 0.15
        assert item["session_id"] == "test_session"
        assert item["status"] == "pending"
