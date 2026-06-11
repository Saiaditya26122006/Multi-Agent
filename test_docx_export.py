"""
Test DOCX export with sample data to verify styling.
"""

import json
import logging
from pathlib import Path
from evaluation.export_docx import export_to_docx

logging.basicConfig(level=logging.INFO)

# Create sample test data
test_data = {
    "idea_name": "EpistemicOS — Pre-Submission Manuscript Diagnostics",
    "grounded": True,
    "total_input_tokens": 125000,
    "total_output_tokens": 35000,
    "total_latency_seconds": 45.2,
    "sections": {
        "executive_summary": {
            "output": {
                "executive_summary": (
                    "EpistemicOS addresses a critical gap in academic publishing: "
                    "manuscript rejection due to preventable formatting and "
                    "methodological errors. Our AI-powered pre-submission diagnostic "
                    "platform helps researchers validate their work before journal "
                    "submission, reducing rejection rates and accelerating publication "
                    "timelines."
                ),
                "confidence_score": "medium-high",
                "key_metrics": [
                    "Target market: 50,000 European business schools",
                    "Expected revenue Year 1: €250K",
                    "Customer acquisition cost: €180",
                    "Gross margin: 85%"
                ],
                "uncertainties": [
                    {
                        "statement": "Market size for B2B academic tools in Spain unverified",
                        "severity": "high"
                    },
                    {
                        "statement": "Pricing sensitivity unclear for institutional buyers",
                        "severity": "medium"
                    }
                ]
            }
        },
        "1": {
            "output": {
                "section_number": "1",
                "confidence_score": "high",
                "customer_problem": (
                    "Academic researchers face high manuscript rejection rates "
                    "(40-60%) due to formatting errors, methodological gaps, and "
                    "journal-specific requirements that could be caught pre-submission."
                ),
                "market_size": "€12M TAM in European academic publishing tools",
                "assumptions_used": [
                    {
                        "statement": "Average manuscript takes 3-6 months to prepare",
                        "confidence": "high",
                        "source": "CEO experience"
                    },
                    {
                        "statement": "Rejection costs researchers 2-4 weeks of rework",
                        "confidence": "medium",
                        "source": "industry reports"
                    }
                ],
                "uncertainties": [
                    {
                        "statement": "No data on willingness-to-pay for B2B academic tools",
                        "severity": "high"
                    }
                ]
            }
        },
        "8": {
            "output": {
                "section_number": "8",
                "confidence_score": "medium",
                "go_to_market_strategy": [
                    "Phase 1: Direct outreach to EADA and IE Business School (Q1-Q2)",
                    "Phase 2: Conference presence at EAM and AIB Europe (Q3)",
                    "Phase 3: Partner with academic publishers (Q4)"
                ],
                "customer_acquisition": {
                    "channel_1": "LinkedIn targeting research deans",
                    "channel_2": "Conference booths and demos",
                    "channel_3": "Publisher co-marketing"
                },
                "assumptions_used": [
                    {
                        "statement": "EADA network provides 20+ warm leads",
                        "confidence": "high",
                        "source": "CEO network"
                    }
                ],
                "uncertainties": [
                    {
                        "statement": "CAC unknown — assuming €180 based on SaaS benchmarks",
                        "severity": "high"
                    }
                ],
                "_unresolved_challenges": [
                    "No competitive pricing data available",
                    "Publisher partnership terms uncertain"
                ]
            }
        },
        "12": {
            "output": {
                "section_number": "12",
                "confidence_score": "low",
                "revenue_projections": [
                    "Year 1: €250K (50 institutional customers @ €5K/year)",
                    "Year 2: €600K (100 customers + upsell)",
                    "Year 3: €1.2M (expansion to 200 customers)"
                ],
                "cost_structure": {
                    "cogs": "€37.5K (15% — cloud + API costs)",
                    "sales_marketing": "€75K (30%)",
                    "product_dev": "€62.5K (25%)",
                    "overhead": "€25K (10%)"
                },
                "assumptions_used": [
                    {
                        "statement": "Gross margin: 85% based on SaaS benchmarks",
                        "confidence": "medium",
                        "source": "industry average"
                    }
                ],
                "uncertainties": [
                    {
                        "statement": "No Monte Carlo simulation run — all projections deterministic",
                        "severity": "critical"
                    },
                    {
                        "statement": "Churn rate unknown — using 10% industry default",
                        "severity": "high"
                    }
                ],
                "_unresolved_challenges": [
                    "Financial model not validated with comparable companies",
                    "No sensitivity analysis performed"
                ]
            }
        }
    }
}

# Write test JSON
test_file = Path("outputs/test_results.json")
test_file.parent.mkdir(exist_ok=True)
with open(test_file, 'w') as f:
    json.dump(test_data, f, indent=2)

print(f"Created test file: {test_file}")

# Export to DOCX
try:
    docx_path = export_to_docx(str(test_file))
    print(f"✅ SUCCESS: Generated styled DOCX at {docx_path}")
    print("\nOpen the file to verify:")
    print(f"  - Colorful cover page with branding")
    print(f"  - Confidence badges with visual meters")
    print(f"  - Section icons and colored headers")
    print(f"  - Styled boxes for uncertainties and challenges")
    print(f"  - Professional tables and formatting")
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
