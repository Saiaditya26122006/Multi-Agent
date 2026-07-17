"""Tests for the shared knowledge graph."""

from agents.phase2.knowledge_graph import KnowledgeGraph


class TestFacts:
    def test_add_fact(self):
        kg = KnowledgeGraph()
        fid = kg.add_fact("TAM is 5B", "env_research", 0.8, "3")
        assert fid == "fact_1"
        node = kg.get_fact(fid)
        assert node.content == "TAM is 5B"
        assert node.valid is True

    def test_dependency_edges(self):
        kg = KnowledgeGraph()
        f1 = kg.add_fact("TAM is 5B", "env_research", 0.8, "3")
        f2 = kg.add_fact("Revenue based on 5B TAM", "financial", 0.7, "12", depends_on=[f1])
        assert f2 in kg.get_fact(f1).dependents
        assert f1 in kg.get_fact(f2).dependencies

    def test_invalidation_cascades(self):
        kg = KnowledgeGraph()
        f1 = kg.add_fact("Fact A", "agent1", 0.9, "1")
        f2 = kg.add_fact("Fact B depends on A", "agent2", 0.8, "2", depends_on=[f1])
        f3 = kg.add_fact("Fact C depends on B", "agent3", 0.7, "3", depends_on=[f2])

        invalidated = kg.invalidate_fact(f1)
        assert f2 in invalidated
        assert f3 in invalidated
        assert kg.get_fact(f2).valid is False
        assert kg.get_fact(f3).valid is False

    def test_update_fact_invalidates_dependents(self):
        kg = KnowledgeGraph()
        f1 = kg.add_fact("Price is $120/mo", "marketing", 0.9, "8")
        f2 = kg.add_fact("Break-even at month 18 given $120/mo", "financial", 0.8, "12", depends_on=[f1])

        invalidated = kg.update_fact(f1, "Price is $80/mo", 0.85, "marketing")
        assert f2 in invalidated

    def test_confidence_ceiling(self):
        kg = KnowledgeGraph()
        f1 = kg.add_fact("Weak assumption", "agent1", 0.3, "1")
        f2 = kg.add_fact("Based on weak assumption", "agent2", 0.9, "2", depends_on=[f1])
        ceiling = kg.get_confidence_ceiling(f2)
        assert ceiling == 0.3

    def test_get_facts_by_section(self):
        kg = KnowledgeGraph()
        kg.add_fact("A", "x", 0.5, "3")
        kg.add_fact("B", "y", 0.6, "3")
        kg.add_fact("C", "z", 0.7, "5")
        assert len(kg.get_facts_by_section("3")) == 2


class TestHypotheses:
    def test_add_hypothesis(self):
        kg = KnowledgeGraph()
        hid = kg.add_hypothesis("ICP is mid-market B2B", "icp", "opportunity_analyst")
        assert hid == "hyp_1"
        h = kg.get_hypothesis(hid)
        assert h.status == "unvalidated"

    def test_evidence_updates_status(self):
        kg = KnowledgeGraph()
        hid = kg.add_hypothesis("Pricing at $120/mo works", "pricing")
        f1 = kg.add_fact("Survey shows willingness to pay $120", "marketing", 0.8, "8")
        f2 = kg.add_fact("Competitor charges $150", "env_research", 0.9, "3")
        f3 = kg.add_fact("Churn at $120 is 8%", "financial", 0.7, "12")

        kg.add_evidence(hid, f1, supports=True)
        kg.add_evidence(hid, f2, supports=True)
        kg.add_evidence(hid, f3, supports=True)

        h = kg.get_hypothesis(hid)
        assert h.status == "supported"
        assert h.confidence >= 0.7

    def test_challenged_hypothesis(self):
        kg = KnowledgeGraph()
        hid = kg.add_hypothesis("TAM is 5B", "market")
        f1 = kg.add_fact("Report says 2B", "env", 0.9, "3")
        f2 = kg.add_fact("Niche too small", "env", 0.8, "3")
        f3 = kg.add_fact("Adjacent market tiny", "env", 0.7, "3")
        f4 = kg.add_fact("One report says 5B", "env", 0.5, "3")

        kg.add_evidence(hid, f1, supports=False)
        kg.add_evidence(hid, f2, supports=False)
        kg.add_evidence(hid, f3, supports=False)
        kg.add_evidence(hid, f4, supports=True)

        h = kg.get_hypothesis(hid)
        assert h.status == "challenged"
        assert h.confidence <= 0.3
        assert len(kg.get_challenged_hypotheses()) == 1

    def test_query_for_agent(self):
        kg = KnowledgeGraph()
        kg.add_fact("Market size 5B", "env_research", 0.8, "3", "market")
        kg.add_fact("ICP is B2B mid-market", "opportunity", 0.9, "1", "icp")
        result = kg.query_for_agent("financial_modelling", "12")
        assert "section_facts" in result
        assert "challenged_hypotheses" in result
