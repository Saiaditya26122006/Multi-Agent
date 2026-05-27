"""
Test business ideas for evaluation runs.

Each idea simulates what Phase 1 would produce: idea_summary, ceo_assumptions,
and an approved_decision. These are fed into the Phase 2 pipeline to benchmark
output quality, latency, and cost.
"""

TEST_IDEAS = [
    {
        "id": "eval_saas_crm",
        "name": "AI-Powered CRM for Freelancers",
        "idea_summary": (
            "A lightweight CRM tool for freelance designers and developers that uses AI "
            "to auto-categorize leads, predict project close probability, and generate "
            "follow-up emails. Target price $29/month. Competing against HoneyBook and "
            "Dubsado but differentiated by AI automation and simplicity."
        ),
        "ceo_assumptions": [
            {"question": "Who is your target customer?", "answer": "Freelance web designers and developers making $50K-$150K/year, solo practitioners"},
            {"question": "What's your pricing model?", "answer": "$29/month flat rate, no per-seat pricing, annual discount at $290/year"},
            {"question": "How will you acquire customers?", "answer": "Content marketing on Twitter/LinkedIn, SEO for 'freelance CRM', ProductHunt launch"},
            {"question": "What's your timeline to first revenue?", "answer": "MVP in 3 months, beta users month 4, paid launch month 6"},
            {"question": "What's your budget?", "answer": "Bootstrapping with $15K savings, no external funding initially"},
        ],
        "approved_decision": {
            "decision": "approved",
            "rationale": "Clear niche, validated pain point from 20 freelancer interviews, achievable technical scope",
            "risk_flags": ["crowded market", "low price point limits growth"],
        },
        "business_type": "saas",
    },
    {
        "id": "eval_ecommerce_coffee",
        "name": "Specialty Coffee Subscription Box",
        "idea_summary": (
            "Monthly subscription delivering 3 bags of single-origin specialty coffee from "
            "micro-roasters. Each box includes tasting notes, brewing guides, and a QR code "
            "linking to a video of the roaster's story. Target price $45/month. Competing with "
            "Trade Coffee and Atlas Coffee but differentiated by micro-roaster exclusivity and "
            "storytelling angle."
        ),
        "ceo_assumptions": [
            {"question": "Who is your target customer?", "answer": "Coffee enthusiasts aged 28-45, urban professionals, currently buying $15+ bags from local roasters"},
            {"question": "How many micro-roasters have you partnered with?", "answer": "LOI from 8 roasters across 4 countries, confirmed 3 for launch"},
            {"question": "What are your unit economics?", "answer": "COGS $18/box (coffee + packaging + shipping), target margin 60%"},
            {"question": "Distribution strategy?", "answer": "Direct-to-consumer via Shopify, Instagram/TikTok marketing, influencer partnerships"},
            {"question": "What's your churn expectation?", "answer": "Industry average is 10-12% monthly for subscription boxes, targeting 8% with quality"},
        ],
        "approved_decision": {
            "decision": "approved",
            "rationale": "Strong unit economics, differentiated positioning, existing supplier relationships",
            "risk_flags": ["high churn category", "logistics complexity", "seasonal demand"],
        },
        "business_type": "ecommerce_subscription",
    },
    {
        "id": "eval_consulting_ai",
        "name": "AI Implementation Consultancy for Mid-Market",
        "idea_summary": (
            "Boutique consultancy helping mid-market companies ($10M-$100M revenue) implement "
            "AI automation in their operations. Focus on document processing, customer service "
            "automation, and predictive analytics. Fixed-price engagements of $50K-$200K per project. "
            "Team of 3 senior consultants to start."
        ),
        "ceo_assumptions": [
            {"question": "What's your competitive advantage?", "answer": "Deep technical skills + business strategy — competitors are either pure tech (can't communicate value) or pure consulting (can't build)"},
            {"question": "How do you find clients?", "answer": "Referral network from previous roles, LinkedIn thought leadership, 2 anchor clients already interested"},
            {"question": "What's your capacity?", "answer": "3 consultants can run 2 projects simultaneously, so max 8-10 projects per year"},
            {"question": "Pricing validation?", "answer": "Two verbal commitments at $75K and $120K, both for document processing automation"},
            {"question": "What's the growth plan?", "answer": "Stay lean year 1, hire 2 more consultants year 2 if utilization exceeds 70%"},
        ],
        "approved_decision": {
            "decision": "approved",
            "rationale": "Proven demand from anchor clients, high margins, low startup cost",
            "risk_flags": ["key person dependency", "scaling requires hiring", "project-based revenue is lumpy"],
        },
        "business_type": "professional_services",
    },
    {
        "id": "eval_hardware_iot",
        "name": "Smart Irrigation Controller for Small Farms",
        "idea_summary": (
            "IoT device + SaaS platform for small farms (5-50 acres) that automates irrigation "
            "based on soil moisture sensors, weather forecasts, and crop-specific water needs. "
            "Hardware unit at $499, SaaS at $19/month. Reduces water usage by 30-40%. "
            "Competing against larger systems from Rachio and Hydrawise but targeting the "
            "underserved small farm segment."
        ),
        "ceo_assumptions": [
            {"question": "Hardware manufacturing?", "answer": "Partner with Shenzhen manufacturer, MOQ 500 units, $120 BOM cost per unit"},
            {"question": "Target market size?", "answer": "2.1M small farms in US, 15% currently use any smart irrigation = 315K addressable"},
            {"question": "Go-to-market?", "answer": "Agricultural trade shows, partnerships with local farm supply stores, direct sales via website"},
            {"question": "What's the timeline?", "answer": "Prototype done, field testing with 5 farms now, manufacturing run Q3, launch Q4"},
            {"question": "Funding needs?", "answer": "Need $150K for first manufacturing run + working capital, seeking angel investment"},
        ],
        "approved_decision": {
            "decision": "approved",
            "rationale": "Working prototype with field validation, clear ROI for customers (water savings pay for device in 1 season)",
            "risk_flags": ["hardware margins tight", "seasonal sales cycle", "requires capital for inventory"],
        },
        "business_type": "hardware_saas",
    },
    {
        "id": "eval_marketplace_tutoring",
        "name": "Peer Tutoring Marketplace for University Students",
        "idea_summary": (
            "Two-sided marketplace connecting university students who excel in specific courses "
            "with students who need help. Tutors set their own rates ($15-$40/hour), platform takes "
            "20% commission. Verified by university enrollment. Launching at 3 universities first. "
            "Competing with Wyzant and Chegg Tutors but differentiated by peer-only model and "
            "university verification."
        ),
        "ceo_assumptions": [
            {"question": "How do you solve cold start?", "answer": "Partner with student orgs, offer first 3 sessions free to tutors (we pay), launch during exam season"},
            {"question": "What's your revenue model?", "answer": "20% commission on completed sessions, average session $25, so $5 per transaction"},
            {"question": "How many transactions to break even?", "answer": "Fixed costs $3K/month (hosting, marketing), need 600 sessions/month across 3 campuses"},
            {"question": "Expansion plan?", "answer": "Prove model at 3 schools, expand to 20 universities by end of year 2"},
            {"question": "Trust and safety?", "answer": "University email verification, session ratings, dispute resolution, background checks for tutors"},
        ],
        "approved_decision": {
            "decision": "approved",
            "rationale": "Low capital requirements, proven demand from student surveys, clear expansion playbook",
            "risk_flags": ["marketplace cold start", "seasonal (academic calendar)", "low transaction value"],
        },
        "business_type": "marketplace",
    },
]
