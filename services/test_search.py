"""
Test script for search service.
Tests real API call with section wrapper logging.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.search_service import search_for_section


def main():
    """Test search service with real API call."""
    print("=" * 80)
    print("SEARCH SERVICE TEST")
    print("=" * 80)
    print()

    query = "academic publishing market size Europe 2025"
    section = "3"

    print(f"Section: {section}")
    print(f"Query: {query}")
    print()
    print("Calling search_for_section()...")
    print("-" * 80)

    results = search_for_section(section, query)

    if not results:
        print("❌ No results returned (check TAVILY_API_KEY in .env)")
        return

    print(f"\n✓ Received {len(results)} results\n")
    print("=" * 80)

    for i, result in enumerate(results, 1):
        print(f"\nRESULT {i}:")
        print(f"  Title:     {result['title']}")
        print(f"  URL:       {result['url']}")
        print(f"  Date:      {result['date']}")
        print(f"  Freshness: {result['freshness']}")
        print(f"  Snippet:   {result['snippet'][:150]}...")

    print("\n" + "=" * 80)
    print("FRESHNESS DISTRIBUTION")
    print("=" * 80)

    freshness_counts = {}
    for result in results:
        freshness = result["freshness"]
        freshness_counts[freshness] = freshness_counts.get(freshness, 0) + 1

    for freshness in ["current", "aging", "stale", "unknown"]:
        count = freshness_counts.get(freshness, 0)
        if count > 0:
            print(f"  {freshness:10s}: {count}")

    print()


if __name__ == "__main__":
    main()
