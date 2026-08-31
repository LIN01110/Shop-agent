"""
Purpose: Taxonomy版本报告脚本：生成类目体系变更审计报告。
"""

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server.rag.taxonomy_governance import build_taxonomy_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Print taxonomy version, fingerprint, and coverage report.")
    parser.add_argument("--products", default=str(ROOT_DIR / "data" / "products_ref.json"))
    parser.add_argument("--fail-on-warning", action="store_true")
    args = parser.parse_args()

    manifest = build_taxonomy_manifest(product_data_path=Path(args.products))
    print(json.dumps(manifest.as_metadata(), ensure_ascii=False, indent=2))
    return 1 if args.fail_on_warning and manifest.warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
