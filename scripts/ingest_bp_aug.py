"""Full rebuild of the augmented BP-node retrieval layer.

Thin wrapper around services.bp_aug_index.reindex_all() (single source of
truth for the aug-layer logic, also used for per-node upserts when Alex creates
a node in Feed).

Run: python -m scripts.ingest_bp_aug
"""

import logging

logging.basicConfig(level=logging.INFO)


def main() -> None:
    from services.bp_aug_index import reindex_all

    result = reindex_all()
    print(f"Done: {result}")


if __name__ == "__main__":
    main()
