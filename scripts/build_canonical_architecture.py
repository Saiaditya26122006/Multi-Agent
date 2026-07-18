"""Build a clean, de-duplicated canonical architecture from Alex's Google Sheet.

The sheet (gid=0) is the source of truth but contains 64 node_ids that each name
two nodes: one fully-populated real node and one 7-field stub from an incomplete
secondary draft. De-dup rule: for each node_id keep the row with the most filled
fields (the real node); drop the stub. Tiebreak: bare-parent style, then first.

Outputs:
  ceo_data/bp_architecture_from_sheet.json  — canonical nodes (bp_architecture.json shape)
  evaluation/ARCHITECTURE_SYNC_REPORT.md    — what changed vs the local file

Does NOT overwrite the live bp_architecture.json — that's the sync step, done
only after this report is reviewed. Run: python -m scripts.build_canonical_architecture
"""

import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

_CSV = Path("/tmp/claude-1000/-home-saiaditya26122006-multi-agent-system/6b6137bc-6e1b-4862-ad8f-668bebc0a5e9/scratchpad/alex_gold.csv")
_ROOT = Path(__file__).parent.parent
_OUT_JSON = _ROOT / "ceo_data" / "bp_architecture_from_sheet.json"
_OUT_MD = _ROOT / "evaluation" / "ARCHITECTURE_SYNC_REPORT.md"

_BP = re.compile(r"^BP\.\d")


def _clean(s: str) -> str:
    return "".join(c for c in (s or "") if not unicodedata.category(c).startswith("C")).strip()


def _fill(row: dict) -> int:
    return sum(1 for v in row.values() if _clean(v))


def _titled_parent(row: dict) -> bool:
    return bool(re.match(r"^BP\.[\d.]+\s+\S", _clean(row.get("parent_node", ""))))


def _node_sort_key(node_id: str) -> list:
    return [int(p) for p in node_id[3:].split(".")]


def load_sheet_rows() -> list[dict]:
    return [
        {_clean(k): _clean(v) for k, v in r.items()}
        for r in csv.DictReader(_CSV.open(encoding="utf-8"))
    ]


def dedupe(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (kept_nodes, dropped_stubs). One row per node_id, fullest wins."""
    by_id: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        nid = _clean(r.get("node_id", ""))
        if _BP.match(nid):
            by_id[nid].append(r)

    kept, dropped = [], []
    for nid, group in by_id.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        # fullest first; tiebreak bare-parent over titled-parent
        ranked = sorted(group, key=lambda r: (_fill(r), not _titled_parent(r)), reverse=True)
        kept.append(ranked[0])
        dropped.extend(ranked[1:])
    return kept, dropped


def to_node(row: dict) -> dict:
    """Map a sheet row to the bp_architecture.json node shape (all columns kept).

    Normalizes `level` to a float derived from node-id depth (BP.1 -> 1.0,
    BP.1.1 -> 2.0). The sheet stores level as an inconsistent string ("1.0",
    "1", "3.0", "3"); the classifier requires a numeric level (it checks
    `level == 1.0` for domains and computes `parent_level + 1`).
    """
    node = {k: (v if v else None) for k, v in row.items()}
    node_id = _clean(row.get("node_id", ""))
    node["node_id"] = node_id
    node["level"] = float(node_id.count("."))
    return node


def main() -> None:
    rows = load_sheet_rows()
    kept, dropped = dedupe(rows)

    # sanity: zero duplicate ids remain
    ids = [_clean(r["node_id"]) for r in kept]
    assert len(ids) == len(set(ids)), "duplicates remain after dedupe!"

    nodes = sorted((to_node(r) for r in kept), key=lambda n: _node_sort_key(n["node_id"]))
    _OUT_JSON.write_text(json.dumps({"nodes": nodes}, indent=2))

    # completeness of the canonical set
    def has_purpose(n):
        return bool(_clean(n.get("purpose") or "") and _clean(n.get("required_output") or ""))

    complete = sum(1 for n in nodes if has_purpose(n))
    per_domain = Counter(".".join(n["node_id"].split(".")[:2]) for n in nodes)

    # diff vs local architecture
    local = {
        n["node_id"]
        for n in json.loads((_ROOT / "ceo_data" / "bp_architecture.json").read_text())["nodes"]
        if _BP.match(str(n.get("node_id", "")))
    }
    new_ids = sorted(set(ids) - local, key=_node_sort_key)
    phantom = sorted(local - set(ids), key=_node_sort_key)

    lines = [
        "# Architecture Sync Report — Sheet → Canonical",
        "",
        f"- Sheet rows parsed: {len(rows)}",
        f"- **Canonical nodes after de-dup: {len(nodes)}** (0 duplicate IDs)",
        f"- Stub rows dropped: {len(dropped)}",
        f"- Nodes with purpose + required_output (classifier-usable): {complete}/{len(nodes)}",
        "",
        "## Per-domain node counts",
        "",
        "| Domain | Nodes |",
        "|---|---|",
        *(
            f"| {d} | {per_domain[d]} |"
            for d in sorted(per_domain, key=lambda x: int(x.split('.')[1]))
        ),
        "",
        f"## New nodes not in local bp_architecture.json ({len(new_ids)})",
        "",
        "First 40: " + ", ".join(new_ids[:40]),
        "",
        f"## Phantom local nodes NOT in sheet ({len(phantom)}) — to be dropped on sync",
        "",
        ", ".join(phantom) if phantom else "(none)",
        "",
        "## Dropped stubs (node_id — kept title | dropped title)",
        "",
        "| node_id | KEPT (real) | DROPPED (stub) |",
        "|---|---|---|",
    ]
    kept_by_id = {_clean(n["node_id"]): n for n in nodes}
    for d in sorted(dropped, key=lambda r: _node_sort_key(_clean(r["node_id"]))):
        nid = _clean(d["node_id"])
        lines.append(
            f"| {nid} | {_clean(kept_by_id[nid].get('node_title') or '')} "
            f"| {_clean(d.get('node_title',''))} |"
        )

    _OUT_MD.write_text("\n".join(lines))
    print(f"Canonical nodes: {len(nodes)} | complete: {complete} | stubs dropped: {len(dropped)}")
    print(f"New vs local: {len(new_ids)} | phantoms to drop: {len(phantom)}")
    print(f"Wrote {_OUT_JSON}\nWrote {_OUT_MD}")
    # flag any stub-only (unusable) nodes that survived
    stub_only = [n["node_id"] for n in nodes if not has_purpose(n)]
    if stub_only:
        print(f"WARNING: {len(stub_only)} canonical nodes lack purpose/required_output: {stub_only[:15]}")


if __name__ == "__main__":
    main()
