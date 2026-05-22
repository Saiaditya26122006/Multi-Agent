# Competitive Analysis Skill

## Purpose
Map the competitive landscape to validate positioning, pricing, and differentiation claims made in the business plan.

## When to Apply
- Always — every business plan section that references competition should be grounded in this analysis
- Primary consumer: marketing_strategy agent (Section 8) and opportunity_analyst (Section 1)
- Secondary consumer: financial_modelling agent (to validate pricing assumptions)

## Methodology

### Step 1: Identify Competitors
- Direct competitors: same product, same customer, same market
- Indirect competitors: different product solving same problem
- Potential entrants: companies one pivot away from competing
- Substitutes: non-obvious alternatives the customer uses today

### Step 2: Competitive Dimensions
For each competitor, assess:
- Positioning: how they describe themselves and their value proposition
- Pricing: model (subscription/one-time/usage), price points, discounting
- Target customer: who they sell to (ICP overlap with our target)
- Strengths: what they do well, moat, defensibility
- Weaknesses: gaps, complaints, underserved segments
- Traction: funding raised, team size, customer count (if known)
- Trajectory: growing, stagnant, declining

### Step 3: Competitive Positioning Map
- Choose 2 axes most relevant to the market:
  - Price vs. Complexity
  - Self-serve vs. Enterprise
  - Breadth vs. Depth
  - Speed vs. Accuracy
- Place competitors and the target business on the map
- Identify white space (underserved quadrants)

### Step 4: Differentiation Assessment
- For each claimed competitive advantage, assess:
  - Is it real? (evidence-based or assumed)
  - Is it defensible? (moat: network effects, switching costs, IP, data, brand)
  - Is it valued? (does the ICP actually care about this differentiator)
  - Is it sustainable? (how long before competitors copy it)

### Step 5: Competitive Response Scenarios
- If we succeed, how will competitors respond?
  - Price war risk
  - Feature matching timeline
  - Acquisition risk (acqui-hire or competitive acquisition)
- What is our response to their likely moves?

## Output Format
```json
{
  "competitors": [
    {
      "name": str,
      "type": "direct" | "indirect" | "potential" | "substitute",
      "positioning": str,
      "pricing": str,
      "strengths": [str],
      "weaknesses": [str],
      "threat_level": "high" | "medium" | "low"
    }
  ],
  "positioning_map": {
    "x_axis": str,
    "y_axis": str,
    "our_position": {"x": str, "y": str},
    "white_space_identified": bool
  },
  "differentiation_assessment": [
    {
      "claimed_advantage": str,
      "evidence_level": "validated" | "assumed",
      "defensibility": "high" | "medium" | "low",
      "customer_value": "high" | "medium" | "low"
    }
  ],
  "primary_competitive_risk": str,
  "recommended_response": str
}
```

## Rules
- Never claim "no competitors" — there is always a substitute (even doing nothing)
- If competitor data is unavailable, state what is inferred vs. what is known
- Pricing intelligence must be labelled with confidence and source
- Competitive advantages must be testable — "better UX" is not specific enough
- Flag any differentiation claim that relies on execution speed alone (not defensible)
