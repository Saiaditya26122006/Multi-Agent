"""Does LLM extraction (vs regex splitting) produce facts that autofill better?

Splits raw prose two ways, classifies each resulting fact through the real
pipeline, and reports node + confidence + tier — so we can see whether
decontextualized facts land in sensible nodes and auto-file more/with higher
confidence than regex fragments.

Run: python -m evaluation.split_compare   (live)
"""

import logging
logging.disable(logging.WARNING)

RAW_INPUTS = [
    "We spoke to a research dean at IESE last month. He said manuscript quality is a "
    "real problem, and confirmed that ANECA accreditation pressure is pushing "
    "institutions to adopt systematic assessment. Pricing is still open but we think it "
    "should be around 5000 euros per institution annually. It could go higher for "
    "research-intensive universities. The main risk is that procurement cycles in "
    "academia are slow — often 6-9 months — which could delay revenue.",
]

AUTO = {"auto_file", "auto_file_flagged"}


def _classify_all(facts):
    from web.handlers.feed_handler import classify_and_match_node, _determine_tier
    rows = []
    autofilled = 0
    for f in facts:
        r = classify_and_match_node(f["text"])
        tier = _determine_tier(r, "alex_direct")
        if tier in AUTO:
            autofilled += 1
        rows.append((f["text"], r.get("node_id"), r.get("confidence"), tier,
                     r.get("signals", {}).get("domain_agreement")))
    return rows, autofilled


def main():
    from web.handlers.feed_handler import (
        extract_atomic_facts, split_into_atomic_facts, detect_format,
    )

    for raw in RAW_INPUTS:
        fmt = detect_format(raw)
        regex_facts = split_into_atomic_facts(raw, fmt)
        llm_facts = extract_atomic_facts(raw) or []

        print("=" * 78)
        print(f"RAW ({len(raw)} chars, format={fmt})\n")

        print(f"--- REGEX split: {len(regex_facts)} facts ---")
        r_rows, r_auto = _classify_all(regex_facts)
        for txt, node, conf, tier, agree in r_rows:
            print(f"  [{tier:18}] {str(node):12} conf={str(conf):6} | {txt[:60]}")
        print(f"  -> auto-filed: {r_auto}/{len(regex_facts)}")

        print(f"\n--- LLM extraction: {len(llm_facts)} facts ---")
        l_rows, l_auto = _classify_all(llm_facts)
        for txt, node, conf, tier, agree in l_rows:
            print(f"  [{tier:18}] {str(node):12} conf={str(conf):6} | {txt[:60]}")
        print(f"  -> auto-filed: {l_auto}/{len(llm_facts)}")

        print(f"\nSUMMARY: regex auto-filed {r_auto}/{len(regex_facts)}, "
              f"LLM auto-filed {l_auto}/{len(llm_facts)}")


if __name__ == "__main__":
    main()
