"""
Purpose: 商品Taxonomy标注脚本：为商品记录自动打product_type标签。
"""

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server.rag.taxonomy_governance import annotate_products, build_taxonomy_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Annotate product records with governed taxonomy fields.")
    parser.add_argument("--input", default=str(ROOT_DIR / "data" / "products_ref.json"))
    parser.add_argument("--output", default="", help="Optional output JSON path for annotated products.")
    parser.add_argument("--report", default=str(ROOT_DIR / "server" / "runtime" / "taxonomy_annotation_report.json"))
    parser.add_argument("--fail-under-product-type-coverage", type=float, default=0.0)
    args = parser.parse_args()

    input_path = Path(args.input)
    products = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(products, list):
        raise SystemExit("Input product file must contain a JSON array.")

    annotated, report = annotate_products([item for item in products if isinstance(item, dict)])
    manifest = build_taxonomy_manifest(product_data_path=input_path)
    report_payload = {
        "manifest": manifest.as_metadata(),
        "annotation": report,
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(annotated, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report_payload, ensure_ascii=False, indent=2))
    if report["product_type_coverage"] < args.fail_under_product_type_coverage:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
