"""Dependency checker service for business plan node dependencies.

Loads bp_dependencies.json and provides functions to analyze
upstream/downstream dependencies, cascade risk, and blocking nodes.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CEO_DATA_DIR = Path(__file__).parent.parent / "ceo_data"


def _load_dependencies_file() -> dict[str, Any]:
    """Load bp_dependencies.json and return full parsed content."""
    filepath = _CEO_DATA_DIR / "bp_dependencies.json"
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("bp_dependencies.json not found at %s", filepath)
        return {}
    except json.JSONDecodeError as e:
        logger.error("Failed to parse bp_dependencies.json: %s", e)
        return {}


def _nodes_in_section(section_id: str, all_node_ids: list[str]) -> set[str]:
    """Return all node IDs that belong to a given section.

    A node belongs to a section if its ID equals the section_id
    or starts with section_id followed by a dot.
    """
    prefix = section_id + "."
    return {
        nid for nid in all_node_ids
        if nid == section_id or nid.startswith(prefix)
    }


def get_dependency_graph() -> dict[str, list[str]]:
    """Load bp_dependencies.json and return the full dependencies dict.

    Returns:
        Dict mapping node_id -> list of node_ids it depends on.
        Empty dict if file is missing or malformed.
    """
    data = _load_dependencies_file()
    deps = data.get("dependencies", {})
    if not isinstance(deps, dict):
        logger.error("dependencies field is not a dict")
        return {}
    return deps


def get_blockers_for(section_id: str) -> list[str]:
    """Find all upstream nodes outside this section that it depends on.

    Given a section like "BP.1.1", finds all nodes within that section,
    collects their dependencies, and returns those that live outside
    the section boundary.

    Args:
        section_id: A section identifier like "BP.1" or "BP.1.2".

    Returns:
        Sorted list of external node IDs that this section depends on.
    """
    graph = get_dependency_graph()
    if not graph:
        return []

    all_node_ids = list(graph.keys())
    section_nodes = _nodes_in_section(section_id, all_node_ids)

    external_deps: set[str] = set()
    for node_id in section_nodes:
        deps = graph.get(node_id, [])
        for dep in deps:
            if dep not in section_nodes:
                external_deps.add(dep)

    return sorted(external_deps)


def get_downstream_impact(section_id: str) -> list[str]:
    """Find all nodes outside this section that depend on nodes within it.

    Reverse walk: identifies which external nodes have dependencies
    pointing into this section.

    Args:
        section_id: A section identifier like "BP.1" or "BP.1.1".

    Returns:
        Sorted list of external node IDs that depend on this section.
    """
    graph = get_dependency_graph()
    if not graph:
        return []

    all_node_ids = list(graph.keys())
    section_nodes = _nodes_in_section(section_id, all_node_ids)

    dependents: set[str] = set()
    for node_id, deps in graph.items():
        if node_id in section_nodes:
            continue
        for dep in deps:
            if dep in section_nodes:
                dependents.add(node_id)
                break

    return sorted(dependents)


def get_cascade_risk(node_id: str) -> int:
    """Count how many nodes transitively depend on this one.

    Builds a reverse dependency graph and performs a breadth-first
    traversal from node_id to count all transitive dependents.

    Args:
        node_id: A specific node like "BP.1.1.3".

    Returns:
        Number of nodes that transitively depend on node_id.
        Returns 0 if node not found or graph is empty.
    """
    graph = get_dependency_graph()
    if not graph:
        return 0

    # Build reverse graph: node -> list of nodes that depend on it
    reverse_graph: dict[str, list[str]] = {}
    for nid, deps in graph.items():
        for dep in deps:
            if dep not in reverse_graph:
                reverse_graph[dep] = []
            reverse_graph[dep].append(nid)

    # BFS from node_id through reverse graph
    visited: set[str] = set()
    queue: list[str] = [node_id]

    while queue:
        current = queue.pop(0)
        for dependent in reverse_graph.get(current, []):
            if dependent not in visited:
                visited.add(dependent)
                queue.append(dependent)

    return len(visited)


def get_reopen_triggers() -> dict[str, str]:
    """Load reopen_triggers from bp_dependencies.json.

    Returns:
        Dict mapping node_id -> trigger description string.
        Empty dict if file is missing or field absent.
    """
    data = _load_dependencies_file()
    triggers = data.get("reopen_triggers", {})
    if not isinstance(triggers, dict):
        logger.error("reopen_triggers field is not a dict")
        return {}
    return triggers


def get_downstream_sections_to_reopen(
    changed_section: str, section_dependency_map: Optional[dict[str, Any]] = None
) -> list[str]:
    """Given a section that changed, return section IDs that must be reopened.

    Uses two sources:
    1. Node-level: bp_dependencies.json graph (nodes in other sections depending
       on nodes in the changed section).
    2. Section-level: dependency_map.yaml (sections whose depends_on includes
       the changed section).

    Args:
        changed_section: Section number like "1" or "3".
        section_dependency_map: Optional dict from dependency_map.yaml with
            structure {"sections": {"1": {"depends_on": [...]}, ...}}.
            If not provided, only node-level dependencies are used.

    Returns:
        Sorted list of section numbers (as strings) that should be reopened.
    """
    downstream_sections: set[str] = set()

    # Source 1: node-level graph from bp_dependencies.json
    graph = get_dependency_graph()
    if graph:
        bp_prefix = f"BP.{changed_section}"
        triggers = get_reopen_triggers()
        changed_nodes = {
            nid for nid in list(graph.keys()) + list(triggers.keys())
            if nid == bp_prefix or nid.startswith(bp_prefix + ".")
        }

        for node_id, deps in graph.items():
            for dep in deps:
                if dep in changed_nodes and node_id not in changed_nodes:
                    section_num = node_id.split(".")[1] if "." in node_id else ""
                    if section_num and section_num != changed_section:
                        downstream_sections.add(section_num)

    # Source 2: section-level depends_on from dependency_map.yaml
    if section_dependency_map:
        sections_config = section_dependency_map.get("sections", {})
        for sec_num, sec_config in sections_config.items():
            deps_on = sec_config.get("depends_on", [])
            if str(changed_section) in [str(d) for d in deps_on]:
                if str(sec_num) != str(changed_section):
                    downstream_sections.add(str(sec_num))

    return sorted(downstream_sections)


def get_dependency_chain(section_id: str) -> dict[str, Any]:
    """Return full dependency chain for a section.

    Combines upstream dependencies, downstream dependents, and
    identifies which upstream nodes are themselves blocked (have
    their own unresolved dependencies).

    Args:
        section_id: A section identifier like "BP.1" or "BP.1.2".

    Returns:
        Dict with keys: section_id, upstream, downstream, blocked_by.
        blocked_by contains upstream nodes that themselves have dependencies.
    """
    graph = get_dependency_graph()
    upstream = get_blockers_for(section_id)
    downstream = get_downstream_impact(section_id)

    # blocked_by = upstream nodes that themselves have dependencies
    # (meaning they might not be resolved yet)
    blocked_by: list[str] = []
    for node_id in upstream:
        node_deps = graph.get(node_id, [])
        if node_deps:
            blocked_by.append(node_id)

    return {
        "section_id": section_id,
        "upstream": upstream,
        "downstream": downstream,
        "blocked_by": sorted(blocked_by),
    }
