"""
Business Plan Node Registry Service

Links knowledge_base entries to business_plan_sections nodes.
Provides query and management operations on the BP node hierarchy.
"""

import logging
from typing import Dict, List, Optional, Tuple
from memory.supabase_client import supabase as db

logger = logging.getLogger(__name__)


class BPNodeRegistryError(Exception):
    """Raised when BP node registry operations fail"""
    pass


class BPNodeRegistry:
    """
    Manages business plan node registry and linking to knowledge_base.
    Provides hierarchical queries and evidence linkage.
    """

    def __init__(self):
        """Initialize with database client"""
        self.db = db

    def link_kb_to_bp_node(
        self,
        kb_id: str,
        section_id: str,
    ) -> Tuple[str, Optional[str]]:
        """
        Link a knowledge_base entry to its corresponding BP node by section_id.

        Returns: (kb_id, bp_section_id or None if no match)

        Example:
            registry = BPNodeRegistry()
            kb_id, bp_id = registry.link_kb_to_bp_node(
                kb_id="kb-123",
                section_id="BP.1.1.1",
            )
        """

        try:
            logger.info(f"[BP Node] Linking KB {kb_id} to BP node {section_id}")

            # Find BP node by section_id
            bp_search = self.db.table("business_plan_sections")\
                .select("id")\
                .eq("section_id", section_id)\
                .execute()

            if not bp_search.data or len(bp_search.data) == 0:
                logger.warning(f"[BP Node] No BP node found for section {section_id}")
                return kb_id, None

            bp_section_id = bp_search.data[0]["id"]
            logger.info(f"[BP Node] Found BP node {bp_section_id} for section {section_id}")

            # Update KB entry with BP section link
            self.db.table("knowledge_base").update({
                "bp_section_id": bp_section_id
            }).eq("id", kb_id).execute()

            logger.info(f"[BP Node] KB {kb_id} linked to BP node {bp_section_id}")

            return kb_id, bp_section_id

        except Exception as e:
            logger.error(f"[BP Node] Error linking KB to BP node: {e}")
            raise BPNodeRegistryError(f"Failed to link KB to BP node: {str(e)}")

    def link_kb_batch_by_section(
        self,
        session_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """
        Batch link all KB entries to BP nodes by matching section field.

        Returns: {
            "linked_count": int,
            "missing_count": int,
            "error_count": int,
        }

        Example:
            registry = BPNodeRegistry()
            results = registry.link_kb_batch_by_section(session_id="sess-123")
            # results["linked_count"] = 45 (45 KB entries linked)
        """

        try:
            logger.info("[BP Node] Starting batch KB-to-BP linking")

            linked_count = 0
            missing_count = 0
            error_count = 0

            # Query all KB entries that need linking
            query = self.db.table("knowledge_base")\
                .select("id, section")\
                .is_("bp_section_id", "null")

            if session_id:
                query = query.eq("session_id", session_id)

            kb_entries = query.execute()

            if not kb_entries.data:
                logger.info("[BP Node] No KB entries needing BP linking")
                return {
                    "linked_count": 0,
                    "missing_count": 0,
                    "error_count": 0,
                }

            logger.info(f"[BP Node] Found {len(kb_entries.data)} KB entries to link")

            for kb_entry in kb_entries.data:
                kb_id = kb_entry["id"]
                section = kb_entry["section"]

                if not section:
                    logger.debug(f"[BP Node] KB {kb_id} has no section, skipping")
                    missing_count += 1
                    continue

                try:
                    _, bp_id = self.link_kb_to_bp_node(kb_id, section)
                    if bp_id:
                        linked_count += 1
                    else:
                        missing_count += 1
                except Exception as e:
                    logger.error(f"[BP Node] Error linking KB {kb_id}: {e}")
                    error_count += 1

            result = {
                "linked_count": linked_count,
                "missing_count": missing_count,
                "error_count": error_count,
            }

            logger.info(f"[BP Node] Batch linking complete: {result}")
            return result

        except Exception as e:
            logger.error(f"[BP Node] Error in batch linking: {e}")
            raise BPNodeRegistryError(f"Failed to batch link KB to BP nodes: {str(e)}")

    def get_bp_node_hierarchy(
        self,
        section_id: str,
    ) -> Dict:
        """
        Get BP node and its parent/child hierarchy.

        Returns: {
            "node": {...node data...},
            "parent": {...parent node...} or None,
            "children": [...child nodes...],
            "evidence_count": int,
        }

        Example:
            registry = BPNodeRegistry()
            hierarchy = registry.get_bp_node_hierarchy("BP.1.1")
        """

        try:
            logger.info(f"[BP Node] Fetching hierarchy for {section_id}")

            # Get the node itself
            node_result = self.db.table("business_plan_sections")\
                .select("*")\
                .eq("section_id", section_id)\
                .execute()

            if not node_result.data or len(node_result.data) == 0:
                raise BPNodeRegistryError(f"BP node {section_id} not found")

            node = node_result.data[0]

            # Get parent (truncate section_id to one level up)
            parent = None
            parts = section_id.split(".")
            if len(parts) > 1:
                parent_id = ".".join(parts[:-1])
                parent_result = self.db.table("business_plan_sections")\
                    .select("*")\
                    .eq("section_id", parent_id)\
                    .execute()
                if parent_result.data and len(parent_result.data) > 0:
                    parent = parent_result.data[0]

            # Get children (section_id that starts with this node's ID + ".")
            children_result = self.db.table("business_plan_sections")\
                .select("*")\
                .like("section_id", f"{section_id}.%")\
                .execute()

            children = children_result.data if children_result.data else []

            # Count evidence linked to this node
            evidence_result = self.db.table("knowledge_base")\
                .select("id", count="exact")\
                .eq("bp_section_id", node["id"])\
                .execute()

            evidence_count = evidence_result.count if hasattr(evidence_result, 'count') else 0

            logger.info(f"[BP Node] Hierarchy for {section_id}: parent={bool(parent)}, children={len(children)}, evidence={evidence_count}")

            return {
                "node": node,
                "parent": parent,
                "children": children,
                "evidence_count": evidence_count,
            }

        except Exception as e:
            logger.error(f"[BP Node] Error fetching hierarchy: {e}")
            raise BPNodeRegistryError(f"Failed to fetch hierarchy: {str(e)}")

    def get_bp_domain_coverage(
        self,
        session_id: Optional[str] = None,
    ) -> Dict[str, Dict]:
        """
        Get coverage (evidence count) for each BP domain (BP.1, BP.2, ... BP.12).

        Returns: {
            "BP.1": {"total_nodes": 100, "linked_count": 45, "coverage": 0.45},
            "BP.2": {"total_nodes": 80, "linked_count": 30, "coverage": 0.375},
            ...
        }

        Example:
            registry = BPNodeRegistry()
            coverage = registry.get_bp_domain_coverage(session_id="sess-123")
        """

        try:
            logger.info("[BP Node] Computing domain coverage")

            # Get all domains (BP.1 through BP.12)
            domains_result = self.db.table("business_plan_sections")\
                .select("section_id")\
                .execute()

            if not domains_result.data:
                return {}

            # Extract unique domain prefixes (BP.1, BP.2, etc.)
            domains = set()
            for row in domains_result.data:
                section_id = row["section_id"]
                parts = section_id.split(".")
                if len(parts) >= 2:
                    domain = f"{parts[0]}.{parts[1]}"
                else:
                    domain = parts[0]
                domains.add(domain)

            domains = sorted(list(domains))
            logger.info(f"[BP Node] Found {len(domains)} domains: {domains}")

            coverage = {}

            for domain in domains:
                # Count total nodes in domain (BP.X.*)
                total_result = self.db.table("business_plan_sections")\
                    .select("id", count="exact")\
                    .like("section_id", f"{domain}%")\
                    .execute()

                total_count = total_result.count if hasattr(total_result, 'count') else 0

                # Count linked KB entries in this domain
                # Get all BP sections for this domain first
                sections_result = self.db.table("business_plan_sections")\
                    .select("id")\
                    .like("section_id", f"{domain}%")\
                    .execute()

                if sections_result.data and len(sections_result.data) > 0:
                    section_ids = [s["id"] for s in sections_result.data]

                    # Count KB entries linked to these sections
                    linked_query = self.db.table("knowledge_base")\
                        .select("id", count="exact")\
                        .in_("bp_section_id", section_ids)

                    if session_id:
                        linked_query = linked_query.eq("session_id", session_id)

                    linked_result = linked_query.execute()
                    linked_in_domain = linked_result.count if hasattr(linked_result, 'count') else 0
                else:
                    linked_in_domain = 0

                coverage_pct = linked_in_domain / total_count if total_count > 0 else 0
                # Cap at 1.0 (can exceed if KB entries link to multiple BP nodes)
                coverage_pct = min(coverage_pct, 1.0)

                coverage[domain] = {
                    "total_nodes": total_count,
                    "linked_count": linked_in_domain,
                    "coverage": coverage_pct,
                }

            logger.info(f"[BP Node] Coverage computed for {len(coverage)} domains")

            return coverage

        except Exception as e:
            logger.error(f"[BP Node] Error computing coverage: {e}")
            raise BPNodeRegistryError(f"Failed to compute coverage: {str(e)}")

    def verify_registry_integrity(self) -> Dict:
        """
        Verify BP node registry integrity.

        Returns: {
            "total_bp_nodes": int,
            "total_kb_linked": int,
            "orphaned_kb_count": int,
            "unlinked_kb_count": int,
            "integrity_valid": bool,
        }
        """

        try:
            logger.info("[BP Node] Verifying registry integrity")

            # Count BP nodes
            bp_count_result = self.db.table("business_plan_sections")\
                .select("id", count="exact")\
                .execute()

            total_bp_nodes = bp_count_result.count if hasattr(bp_count_result, 'count') else 0

            # Count KB entries linked to BP nodes
            linked_kb_result = self.db.table("knowledge_base")\
                .select("id", count="exact")\
                .not_.is_("bp_section_id", "null")\
                .execute()

            total_kb_linked = linked_kb_result.count if hasattr(linked_kb_result, 'count') else 0

            # Count KB entries with orphaned BP links (BP node doesn't exist)
            orphaned_result = self.db.table("knowledge_base")\
                .select("id", count="exact")\
                .not_.is_("bp_section_id", "null")\
                .execute()

            orphaned_kb_count = 0
            if orphaned_result.data:
                for kb_entry in orphaned_result.data:
                    bp_id = kb_entry.get("bp_section_id")
                    if bp_id:
                        bp_check = self.db.table("business_plan_sections")\
                            .select("id")\
                            .eq("id", bp_id)\
                            .single()\
                            .execute()
                        if not bp_check.data:
                            orphaned_kb_count += 1

            # Count unlinked KB entries (have section but no bp_section_id)
            unlinked_result = self.db.table("knowledge_base")\
                .select("id", count="exact")\
                .not_.is_("section", "null")\
                .is_("bp_section_id", "null")\
                .execute()

            unlinked_kb_count = unlinked_result.count if hasattr(unlinked_result, 'count') else 0

            integrity_valid = orphaned_kb_count == 0

            result = {
                "total_bp_nodes": total_bp_nodes,
                "total_kb_linked": total_kb_linked,
                "orphaned_kb_count": orphaned_kb_count,
                "unlinked_kb_count": unlinked_kb_count,
                "integrity_valid": integrity_valid,
            }

            logger.info(f"[BP Node] Integrity check: {result}")

            return result

        except Exception as e:
            logger.error(f"[BP Node] Error verifying integrity: {e}")
            return {
                "total_bp_nodes": 0,
                "total_kb_linked": 0,
                "orphaned_kb_count": 0,
                "unlinked_kb_count": 0,
                "integrity_valid": False,
            }
