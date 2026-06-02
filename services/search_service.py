"""
Search Service — shared retrieval function for outward agents.

Provides live market data retrieval via Tavily AI Search API.
Returns structured results with source URL, date, and freshness flags.
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


def _calculate_freshness(date_str: Optional[str]) -> str:
    """Calculate freshness flag from ISO date string.

    Args:
        date_str: ISO format date string (YYYY-MM-DD)

    Returns:
        "current" (<6 months), "aging" (6-18 months), "stale" (>18 months), "unknown"
    """
    if not date_str:
        return "unknown"

    try:
        result_date = datetime.fromisoformat(date_str.split("T")[0])
        today = datetime.now()
        age_days = (today - result_date).days

        if age_days < 0:
            return "unknown"
        elif age_days < 180:
            return "current"
        elif age_days < 540:
            return "aging"
        else:
            return "stale"
    except (ValueError, AttributeError) as e:
        logger.warning(f"Failed to parse date '{date_str}': {e}")
        return "unknown"


def search(query: str, context: str = "", max_results: int = 5) -> list[dict]:
    """Search for live market facts via Tavily AI Search API.

    Args:
        query: Search query string
        context: Optional context to guide search relevance
        max_results: Maximum number of results to return (default 5)

    Returns:
        List of result dicts with keys: title, snippet, url, date, freshness, query_used
        Returns empty list on any error (never raises exceptions)
    """
    if not TAVILY_API_KEY or TAVILY_API_KEY.startswith("tvly-your-key"):
        logger.error("TAVILY_API_KEY not configured or is placeholder value")
        return []

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=TAVILY_API_KEY)

        search_params = {
            "query": query,
            "max_results": max_results,
            "search_depth": "advanced",
            "include_answer": False,
            "include_raw_content": False,
        }

        if context:
            search_params["include_domains"] = []

        response = client.search(**search_params)

        results = []
        for item in response.get("results", []):
            result = {
                "title": item.get("title", ""),
                "snippet": item.get("content", ""),
                "url": item.get("url", ""),
                "date": item.get("published_date", ""),
                "freshness": _calculate_freshness(item.get("published_date")),
                "query_used": query,
            }
            results.append(result)

        logger.info(
            f"Search completed: query='{query[:50]}...', results={len(results)}"
        )
        return results

    except ImportError:
        logger.error("tavily-python not installed. Run: pip install tavily-python")
        return []
    except Exception as e:
        logger.error(f"Search failed for query '{query}': {e}")
        return []


def search_for_section(
    section_number: str, query: str, max_results: int = 5
) -> list[dict]:
    """Search wrapper with section-specific logging.

    Called by outward agents (Opportunity §1, Environment §3, Marketing §8, Financial §12).

    Args:
        section_number: Section number calling the search (e.g., "1", "3", "8", "12")
        query: Search query string
        max_results: Maximum number of results to return (default 5)

    Returns:
        List of result dicts (same format as search())
    """
    logger.info(f"[Section {section_number}] Search initiated: '{query}'")

    results = search(query, max_results=max_results)

    if results:
        freshness_counts = {}
        for result in results:
            freshness = result["freshness"]
            freshness_counts[freshness] = freshness_counts.get(freshness, 0) + 1

        freshness_dist = ", ".join(
            f"{k}={v}" for k, v in sorted(freshness_counts.items())
        )
        logger.info(
            f"[Section {section_number}] Search complete: {len(results)} results, "
            f"freshness: {freshness_dist}"
        )
    else:
        logger.warning(f"[Section {section_number}] Search returned 0 results")

    return results
