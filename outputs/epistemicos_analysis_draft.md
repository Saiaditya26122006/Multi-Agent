# EpistemicOS — First-Pass Analysis (Draft)

**Date:** 2026-06-02  
**Basis:** Grounded evaluation run against CEO-provided source-of-truth data  
**Status:** Honest assessment of what's known, what's assumed, and where the critical gaps are

---

## Executive Summary

EpistemicOS addresses documented academic quality problems — unsupported claims, weak inference chains, citation gaps in manuscripts — but the buyer thesis is unvalidated. The institutional governance layer (research deans buying audit-ready tools at €15k–€25k per school) rests on an accreditation trigger that does not currently exist: epistemic scoring is not an AACSB/EQUIS criterion. The primary forcing function cited in your materials is not yet operational. Competitive overlap with Scite.ai (citation classification) and commoditization by Claude/GPT-4o at €20/month for individual researchers are the two risks the available data could not resolve. Year 1 revenue is €0–€30k (one Spanish network contract; zero cold pipeline closes). The recommendation is conditional: fund only if 10 research dean interviews within 60 days confirm €15–€25k budget authority exists and tie to a specific governance metric. Kill if five calls reveal <€5k budgets, existing Scite/Elsevier adoption, or zero departmental tooling spend. All confidence ratings are low because input data is sparse. This is what the system knows with confidence, what it's treating as hypothesis, and where the gaps are.

---

## What We Know With Confidence

**Confirmed facts from your source-of-truth data:**

1. **Individual researcher use case is commoditized.** Claude and GPT-4o accept manuscript uploads and produce claim-evidence critique today at $20/month. No technical barrier exists. Any positioning that competes on individual manuscript review at institutional pricing is indefensible on this axis.

2. **The product definition is strong.** EpistemicOS is clearly scoped as pre-submission manuscript diagnostics: claim-evidence alignment, methodological justification, inference transparency, citation grounding, reviewer-readiness assessment. The conceptual clarity is confirmed. Category is claim-level epistemic validation, not plagiarism detection or grammar checking.

3. **The primary problem exists.** Manuscripts contain unsupported claims, weak inference, method-conclusion mismatch, citation gaps, and reviewer-facing risks. This is a documented pain point in academic research.

4. **Geography strategy is clear.** Spain/EU first, global later pending GDPR compliance, trust establishment, and multilingual capability. This is a coherent sequencing decision.

5. **Business model is institutional SaaS.** Annual contracts, per-school or per-department licensing. This matches the procurement patterns of universities that already buy tools like Turnitin at the institutional level.

6. **Your Spanish academic network is real.** Access to 5 FT-ranked Spanish business schools (IESE, ESADE, IE, ESIC, Deusto) is confirmed. This is the highest-confidence go-to-market asset in the entire plan. Network access structurally lowers customer acquisition cost compared to cold institutional sales.

7. **Accreditation frameworks include research governance audits.** AACSB and EQUIS 5th Edition (2023) do audit research-governance processes. The institutional anxiety about research quality is real.

8. **Scite.ai performs citation classification.** Scite.ai classifies citations as supporting, contrasting, or mentioning — this is direct functional overlap with inferential chain validity. Whether buyers perceive EpistemicOS as distinct from Scite.ai is unknown, but the technical overlap is confirmed.

9. **EU GDPR applies.** Manuscript data processing requires Data Processing Agreements, and GDPR Article 28 compliance is non-negotiable for EU institutional contracts. This is table stakes, not a competitive advantage.

10. **Rankings pressure is real.** QS and FT rankings weight publication volume and citations. Business schools face declining enrollments and prioritize research output in their positioning.

---

## What We're Treating as Hypothesis (Key Assumptions)

**Every confidence level on these is LOW. Not one has been validated by a buyer interview, pilot, or external evidence.**

### Buyer & Willingness-to-Pay

1. **Institutions will pay €15k–€25k annually for governance-layer epistemic audit tooling distinct from individual researcher LLM use.**  
   **Source:** Assumption — zero interview evidence; anchored loosely to Turnitin/iThenticate procurement records but those are per-submission models sold to librarians, not governance contracts sold to research deans.  
   **What would validate it:** 10 research dean interviews confirming budget authority, discretionary spend range, and procurement tier.

2. **AACSB/EQUIS accreditation pressure creates a specific budget line for research quality governance tooling.**  
   **Source:** Inferred from AACSB Assurance of Learning standards documentation; no dean confirmation.  
   **The conflict:** Section 5 explicitly states epistemic scoring is **not currently a scored accreditation criterion.** Accreditation audits focus on ethics committees, COI disclosure, and data retention — not claim-validation tooling. This removes the primary forcing function.  
   **What would validate it:** Interviews with 3–5 AACSB/EQUIS peer review team members confirming whether manuscript-level claim-validation appears in audit checklists or scoring weightings.

3. **Research deans have budget authority to procure €15k–€25k tools without multi-committee approval.**  
   **Source:** Role inference only; no procurement pathway documented.  
   **The gap:** Budget authority location entirely unvalidated. Deans may only recommend; procurement may require IT, finance, and faculty committee sign-off. Spanish university procurement threshold for committee review is estimated ~€15k but varies by institution.  
   **What would validate it:** Named contacts at 5 target schools providing org charts, budget tiers, and approval workflows.

### Product & Differentiation

4. **Institutional governance use case has an 18–24 month differentiation window vs. LLM wrappers before incumbents enter.**  
   **Source:** Estimate based on institutional procurement lag and accreditation-body familiarity requirements; unverified.  
   **The risk:** Elsevier and Springer have internal integrity tooling under development with unknown timelines. Both publishers have existing institutional contracts, journal access, brand trust, and distribution. If they release claim-validation modules, the window closes before EpistemicOS has a working artifact.  
   **What would validate it:** Standing monthly monitoring of Elsevier Research Integrity and Springer Nature product announcements; competitive intelligence interviews with 2–3 ed-tech vendors.

5. **EpistemicOS will achieve inter-rater reliability kappa ≥ 0.65 against senior journal reviewers on claim-support ratings.**  
   **Source:** Landis & Koch 1977 threshold for substantial agreement; whether accreditation bodies accept this standard is unverified.  
   **The gap:** No accuracy benchmarks, no pilot data, no inter-rater studies exist. Product stage is pre-artifact. Every differentiation claim against Scite.ai, Turnitin, or Claude is currently unfalsifiable without a working prototype.  
   **What would validate it:** Benchmark study: 100–200 peer-reviewed manuscripts with human-expert annotations, comparative accuracy vs. GPT-4o/Claude/Scite.ai, published results.

### Market Sizing & Revenue

6. **EU addressable market is ~130 unique unduplicated schools (AACSB, EQUIS, AMBA members with doctoral programs).**  
   **Source:** Directory-based deduplication estimate subtracting dual/triple-accredited institutions and teaching-only schools. Not field-validated against procurement budgets.  
   **Confidence:** Medium on the count; low on whether these schools have relevant budgets.

7. **TAM range is €1.5M–€8.5M (midpoint ~€4.5M).**  
   **Source:** 130 schools × €10k–€50k ARPU range. Compounded uncertainty from two low-confidence inputs produces ~6× spread.  
   **The gap:** No directly comparable institutional SaaS benchmark exists for research governance tooling sold to research deans. iThenticate removed as proxy — it's per-submission sold to librarians. SciVal/Pure removed as proxy — sold to VPs of Research at university-wide level, different buyer tier.  
   **What would validate it:** Survey 20–30 business schools on actual budget allocation for research-support tools; identify ceiling spend on analogous governance platforms.

8. **Year 1 revenue: 1 contract from Spanish network at €15k; 0 contracts from cold sales.**  
   **Source:** Section 8 base case. Cold institutional sales cycle is 9–18 months; sales function starts Month 6; earliest cold close is Month 15 (outside Year 1). Network path may compress to 4–8 months via warm intro.  
   **The gap:** Network conversion intent is unknown. Warm access confirmed; procurement simplification via network must be explicitly confirmed with each named account before assuming compressed cycle.  
   **What would validate it:** Named LOIs (non-binding pilot commitments) from 1–2 Spanish schools by Month 3.

### Operations & Costs

9. **LLM token cost per manuscript: 500 tokens/claim extraction + 200 tokens/evidence alignment = 700 tokens total.**  
   **Source:** Internal estimation; no pilot token-count data.  
   **The gap:** Actual overhead depends on manuscript length distribution and claim density (management papers 4–8 claims; STEM 15–25 claims). Rework token cost unknown. Token efficiency may be 2–3× estimate if claim boundaries remain ambiguous.  
   **What would validate it:** Pilot processing on 50 real manuscripts; measure token usage distribution across disciplines.

10. **Gross margin assumption: 70% (30% COGS for human-in-loop validation labor).**  
    **Source:** Analyst estimate; Section 5 confirms 100% GM is indefensible because manual validation labor is required pre-deployment.  
    **The gap:** Actual COGS depends on validation scope not yet scoped. Break-even range is 21 contracts (100% GM) to 30+ contracts (70% GM). Year 2 model projects 3–6 contracts vs. 30 required — structurally impossible without cost restructure or price increase.  
    **What would validate it:** Define validation workflow; scope FTE requirements; measure validation hours per manuscript in pilot.

---

## Section Highlights

**Confidence note:** Every section output carries a "low" confidence rating. This is not a system failure — it's the correct honest answer given sparse input data, zero buyer validation, and pre-artifact product stage. The analysis measures what was provided; low confidence signals where external validation is required before building.

### Section 1: Opportunity Analysis (Opportunity Analyst)

The strategic pivot from individual researcher tool to institutional governance platform is the only defensible positioning. Individual manuscript critique is commoditized by Claude/GPT-4o at €20/month. The institutional layer — standardized audit-ready reports, versioned scoring rubrics, exportable artifacts for AACSB/EQUIS documentation — could occupy a governance niche if the buying trigger is validated. The beachhead is ~220 AACSB + ~180 EQUIS EU-accredited schools; Year 1 SAM 10–20 schools yields €200K–€800K ARR if WTP holds. **Most important gap:** Zero buyer interviews conducted. Every buyer persona, pain intensity, and procurement pathway is assumption status. The individual-vs-institutional bifurcation is strategically essential, but the institutional buyer does not yet exist in validated form.

### Section 3: Environment Research (PEST/Porter/Five Forces)

EpistemicOS enters a high-rivalry, low-barrier ed-tech market dominated by incumbents with 80%+ plagiarism-detection share (Turnitin). Institutional buyers are price-sensitive and have free/cheap substitutes (consumer LLMs, human reviewers). Schools prioritize publication volume over manuscript integrity, misaligning incentives with EpistemicOS value. Regulatory tailwinds (AI disclosure policies from Nature, Science) exist but do not yet translate to SaaS procurement. LLM reasoning capacity is proven, but domain accuracy vs. human reviewers is unvalidated. **Most important gap:** No buyer interviews on procurement drivers. Do schools allocate budget to manuscript-validation tools to improve ranking outcomes, or do they treat this as a process-training problem solvable without software? Market size and institutional WTP remain unquantified.

### Section 4: Organisation Design (Team & Capabilities)

MVP prototype is missing; CTO hire timeline conflicts with prototype delivery. Resolution: hire contract ML engineer (weeks 0–2) to build throwaway prototype in parallel with CTO search; CTO validates/productizes by week 12. Buyer validation scope is unclear — 15 research dean interviews targeting realistic 1–2 pilot LOIs with 40–60% probability of converting to paid pilot. Kill gate: if <1 LOI by Month 4, pivot or reset. Year 1 headcount: 4 (CTO €90k, contract ML €30k, Validation Lead €40k, external counsel €20k); total cost €180k. Year 1 revenue base case €15k from 1 pilot. **Most important gap:** Pilot conversion probability is unquantified. Validation scope requires 10+ buyer interviews to confirm institutions will pay for governance-layer tooling vs. mandating individual LLM use.

### Section 5: SWOT Synthesis (Strategic Positioning)

Plausible but entirely unvalidated product concept. B2B institutional revenue thesis depends on a buying trigger explicitly confirmed as not a scored accreditation criterion. Scite.ai represents unacknowledged functional overlap — citation classification (supporting/contrasting/mentioning) directly overlaps with inferential chain validity scoring. Elsevier and Springer are simultaneously the strongest market-anxiety signals and the highest-severity competitive threats; both have resources, distribution, and internal development capacity to self-serve the solution. Pre-artifact stage makes every differentiation claim unfalsifiable. **Most important gap:** No validated institutional buying trigger exists. AACSB compliance hook is an inference, not a documented requirement. Until at least one research dean confirms a budget-relevant trigger in a structured interview using Scite.ai as the competitive anchor, the B2B model is a hypothesis chain with no grounded link.

### Section 8: Marketing Strategy (Go-to-Market)

Primary segment: ~130 unique unduplicated EU business schools with AACSB/EQUIS accreditation. TAM range €1.5M–€8.5M with 6× spread due to compounded uncertainty (school count ±40%, ARPU €10k–€50k ±60%). No directly comparable pricing benchmark exists. Spanish network is highest-confidence GTM asset: warm access to 5 FT-ranked schools reduces CAC materially and is the only advantage with high confidence. First 3 pilots must come from this segment. Year 1 revenue: €0–€30k (0 from cold sales; 1–2 from Spanish network only). Cold institutional sales cycle 9–18 months means earliest cold close is Month 15 (outside Year 1). **Most important gap:** Whether claim-evidence scoring maps to any scored AACSB/EQUIS criterion is unvalidated. Buying trigger is plausible but unconfirmed. Spanish network contacts may have access but not budget authority — conversion intent unknown.

### Section 10: Operations (Delivery & Cost Structure)

Production process: API intake → LLM claim extraction → Multi-class annotation → Evidence-alignment scoring → Audit-trail JSON → SaaS dashboard. Target <3min per 50-page manuscript. Latency unvalidated at scale. Year 1 LLM API spend €45k assumes 500 tokens/claim + 200 tokens/evidence alignment, but token overhead is unvalidated and could be 2–3× estimate if claim boundaries remain ambiguous. Manual validation labor (human-in-loop pre-deployment claims audit) is unbounded; no estimate exists for validator cost if extraction confidence <80%. Cloud infrastructure €12k Year 1 scales linearly with volume. GDPR/DPA compliance €20k Year 1 is table stakes. **Most important gap:** Inter-rater reliability not yet measured. Claim boundary detection accuracy across disciplines (management vs. STEM) is unvalidated. Product may not generalize; market TAM reduction risk if domain-specific tuning is required per vertical.

### Section 12: Financial Modelling (Three-Statement Model)

Break-even analysis: baseline month 42 (3.5 years), optimistic month 30, pessimistic never in current cost structure. Year 2 break-even requires 30 contracts at 70% gross margin vs. model projection of 3–6 contracts — structurally impossible without cost restructure or price increase. Year 1 P&L: €8,750 revenue (7-month ratable recognition from 1 contract signed Month 6), €180k costs, net loss €171k. Year 2: €75k–€175k revenue, €520k costs, net loss €395k–€445k. Seed funding required: €750k minimum (Y1 €180k + Y2 €520k + €50k buffer). Runway at €750k seed: ~28 months at blended burn. Exit-multiple valuation: €7.2M exit value at 4× ARR (Year 5 revenue €1.8M from 72 contracts) discounted at 35% = €1.6M gross PV before deducting interim Y1–Y4 losses of ~€800k–€1.1M. **Most important gap:** All figures are analyst-constructed. CEO sections 11–12 are empty headers; zero financial data provided. Every number is derived from stated assumptions in prior sections, not empirical grounding. Revenue model has two irreconcilable projections (bottom-up contract count vs. CAGR); CAGR assumption discarded.

### Section 13: Launch & Contingency

*Section failed to parse due to Bedrock connection error. No output available.*

### Executive Summary

Buyer thesis unvalidated. Accreditation cited as forcing function, but epistemic scoring is not currently a criterion — eliminating primary urgency driver. Competitors (Scite.ai, Elsevier, Claude) remain unanalyzed. Year 1 revenue €0–€30k (1 Spanish contract; zero cold pipeline). Recommendation: fund only contingent on 10 research dean interviews within 60 days confirming €15–€25k budget authority AND specific audit/governance metric tied to epistemic quality. Kill if 5+ calls show sub-€5k budgets, Elsevier/Scite adoption, or zero departmental tooling spend. Pilot proof-of-concept is prerequisite for Year 2 VP-Sales hire. Current €180k Year 1 cost plan unsustainable without revenue validation.

---

## The Three Most Important Gaps

These are ranked by impact: the degree to which validating (or invalidating) each gap would change the analysis most.

### 1. **No evidence that research deans have discretionary budget authority for governance tooling at the €15k–€25k price point, separate from library/IT procurement.**

**Why it matters:** The entire B2B revenue model depends on this being true. If deans can only recommend, and procurement requires IT/finance/faculty committee approval over a 9–18 month cycle, then the "warm Spanish network" advantage collapses to the same procurement friction as cold sales. If typical research-support tool budgets are €5k–€10k and €25k requires executive sign-off, the price point is structurally misaligned with buyer tier.

**What would change if validated:** Confirmed budget authority at 5+ schools + procurement tier mapping would unlock the institutional sales model and justify the Year 2 VP Sales hire. Pricing could be anchored to actual departmental spend ceiling rather than analogies to Turnitin contracts sold through national consortia.

**What would change if invalidated:** Kill or restructure. If budget authority sits with CIOs and pricing must drop to €5k–€8k to match IT tool budgets, gross margin collapses (€5k at 70% GM = €3,500 contribution margin; break-even moves from 30 contracts to 150 contracts in Year 2). Alternatively, reframe as a top-down university-wide sale to VPs of Research, which puts EpistemicOS in direct competition with SciVal/Pure and requires enterprise sales motion, not founder-led pilots.

**How to resolve:** 10 buyer interviews with named research deans at AACSB/EQUIS schools. Ask: (a) What is your discretionary budget for research-support tools? (b) At what price point does a new tool require committee approval vs. your signature alone? (c) Which vendors do you currently contract with for manuscript/research quality tooling, and at what ACV? (d) Who owns the budget line for research governance software — your office, IT, library, or a shared committee?

---

### 2. **Whether buyers perceive Scite.ai as solving the same problem EpistemicOS addresses, making the "categorical uniqueness" claim false.**

**Why it matters:** Scite.ai already classifies citations as supporting, contrasting, or mentioning — direct overlap with inferential chain validity. If research deans describe Scite.ai as their current citation-validation tool and perceive it as "good enough" for governance/audit purposes, then EpistemicOS is a feature addition, not a new category. Scite.ai could pivot to institutional licensing with audit-trail outputs faster than EpistemicOS can build a working prototype. The differentiation moat may not exist in buyer perception, even if it exists technically.

**What would change if validated:** If buyers confirm Scite.ai does not produce audit-ready outputs aligned to AACSB/EQUIS frameworks, and that citation classification ≠ claim-evidence coherence scoring, then EpistemicOS occupies a distinct governance niche. The pitch becomes: "Scite.ai tells you if citations are used correctly; EpistemicOS tells you if the claim-evidence chain is methodologically valid before submission." This positions EpistemicOS as a pre-submission governance layer vs. Scite.ai as a post-publication citation intelligence tool.

**What would change if invalidated:** If buyers say "Scite.ai already does this" or "We'd just ask Scite.ai to add audit exports," the category collapses. EpistemicOS becomes a feature request for an incumbent with distribution, not a standalone company. The strategic response is either (a) pivot to a different buyer segment Scite.ai doesn't serve, (b) partner with Scite.ai as a white-label scoring module, or (c) abandon institutional B2B and reframe as a regulatory compliance play for publishers/journals (different buyer, different use case).

**How to resolve:** Structured competitive interview with 5 research deans. Show them a Scite.ai citation report and an EpistemicOS mockup side-by-side. Ask: (a) Does Scite.ai meet your manuscript governance needs, or is something missing? (b) Would you pay €15k–€25k for audit-ready claim-evidence scoring if Scite.ai doesn't provide it, or would you ask Scite.ai to add that feature? (c) If both tools existed, which would you adopt, and why? Record whether they describe the tools as substitutes or complements.

---

### 3. **No inter-rater reliability study exists, so the core product claim — "achieves senior-reviewer-level reliability on claim-support ratings" — is unfalsifiable.**

**Why it matters:** The governance positioning requires demonstrable consistency. If EpistemicOS cannot produce a kappa ≥ 0.65 against domain experts using a fixed versioned rubric, the audit-trail claim is theater. Accreditation bodies require reproducibility; one-off LLM outputs with variable interpretations do not meet that standard. Without this study, pilot schools have no basis to trust that EpistemicOS scores are defensible in an accreditation audit. Competitors (or skeptical deans) will ask: "What's your inter-rater reliability?" If the answer is "We don't have that data yet," the sale stalls.

**What would change if validated:** Published kappa ≥ 0.65 on 100–200 manuscripts becomes the lead qualification artifact in every pilot negotiation. It shifts the pitch from "We think this will work" to "This demonstrably matches senior reviewer judgment." The study also surfaces where the product fails — certain claim types, methodologies, or disciplines where accuracy is <65% — which informs product roadmap and market segmentation (e.g., launch in management research only, expand to STEM later).

**What would change if invalidated:** If kappa <0.5 (poor agreement), the product does not work at the claimed level of reliability. The strategic response depends on why: (a) If rubric design is weak, iterate and re-test. (b) If LLM models are inherently inconsistent on epistemic reasoning, this is a fundamental technology limitation and the product cannot ship in its proposed form. (c) If accuracy varies widely by discipline (high in management, low in biology), reframe as a vertical-specific tool and narrow TAM accordingly.

**How to resolve:** Annotate 100–200 published management research manuscripts with 3 senior journal reviewers (PhD-level domain experts). Each reviewer scores 20 claims per manuscript on a fixed rubric: claim-support strength (0–3 scale), evidence sufficiency (0–3), methodological coherence (0–3). Run the same manuscripts through EpistemicOS prototype. Calculate Cohen's kappa or Krippendorff's alpha between EpistemicOS and consensus human scores. Publish results. If kappa ≥ 0.65, use it in sales. If kappa <0.65, diagnose failure modes and iterate before pilot deployments.

---

## What to Send Next

This list is specific, not generic. These are the data points the system flagged as missing that would most directly unlock the blocked assumptions.

**Buyer validation (highest priority):**

1. **Named contacts and interview readiness for 10 research deans at AACSB/EQUIS EU business schools.** At least 3 non-Spanish, at least 2 outside your existing network. Include org chart, budget tier, procurement approval workflow for each. The system needs to know whether discretionary budget authority exists at the dean level or whether IT/finance committees control procurement.

2. **Buyer interview script outcomes for first 5 calls.** Specifically: (a) What is your current annual spend on research-support/manuscript-quality tooling? (b) At what ACV threshold does a new tool require committee vs. your signature? (c) Do you currently use Scite.ai, Turnitin, SciVal, or Elsevier tools for manuscript governance? (d) If epistemic validation scoring were available, would it map to a compliance/audit requirement you currently report to AACSB/EQUIS, or is it a nice-to-have? Record whether they confirm or reject the governance-layer buying trigger.

**Competitive intelligence:**

3. **Scite.ai feature comparison.** Does Scite.ai produce institutional audit-ready outputs aligned to AACSB/EQUIS frameworks? What is their current institutional licensing model (if any)? Have they announced governance-layer features on their roadmap? This resolves whether EpistemicOS is a distinct category or a feature gap.

4. **Elsevier/Springer integrity tooling development timeline.** What internal manuscript-validation products are in development at Elsevier Research Integrity or Springer Nature? What is their go-to-market timeline? This quantifies the competitive threat that could close the market window before you have a working artifact.

**Product & accuracy:**

5. **Inter-rater reliability study design approval.** External validation that the proposed rubric, manuscript sample, and annotator pool will yield a defensible kappa ≥ 0.65 result. Requires review by a management research methods expert or a journal editor who understands accreditation reporting standards. This de-risks the core product claim before you build.

6. **Token usage benchmark on 50 real manuscripts.** Actual LLM API cost per manuscript across Claude/GPT-4o. Measure token distribution by manuscript length, claim density, and discipline. This resolves the 2–3× cost uncertainty in the operations model and informs gross margin assumptions.

**Financial & procurement:**

7. **Actual AACSB/EQUIS audit checklists.** Not summary documents — the scored rubrics used by peer review teams during accreditation site visits. Confirm whether manuscript-level epistemic validation appears as a criterion or whether the governance audit focuses on ethics committees, COI, and data retention only. This resolves the forcing-function contradiction flagged in Section 5 and the executive summary.

8. **Confirmed procurement pathway for 2 named Spanish network schools.** Not just warm intro — org chart showing who signs contracts at what ACV tier, approval timeline, DPA review process, and whether GDPR Article 35 DPIA applies. This validates whether the Spanish network advantage compresses procurement cycle to 4–8 months or whether it still requires 9–18 months due to institutional process.

**Regulatory:**

9. **AI Act risk tier classification for research-assistance tools.** External EU AI Act counsel opinion: is EpistemicOS "limited risk" (minimal compliance) or "high risk" (conformity assessment required)? This determines legal overhead before first institutional data contact. Budget €8k–€12k for counsel; deliver opinion by Month 3 before pilot DPA negotiation.

---

**End of draft analysis.**

