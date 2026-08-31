"""
Purpose: 视觉Embedding索引构建脚本：为商品图片批量构建向量索引。
"""

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from server.config import get_settings  # noqa: E402
from server.inputs.visual_embedding import build_product_visual_index  # noqa: E402
from server.rag.embedding_cache import EmbeddingCache  # noqa: E402
from server.rag.vector_store import ArkMultimodalEmbeddingFunction  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a persistent product-image embedding index.")
    parser.add_argument("--product-data", default="")
    parser.add_argument("--image-dir", default="")
    parser.add_argument("--index-path", default="")
    parser.add_argument("--cache-path", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--checkpoint-interval", type=int, default=10)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.ark_api_key:
        raise RuntimeError("ARK_API_KEY is required to build the visual embedding index.")

    product_data = resolve_path(args.product_data, settings.product_data_file)
    image_dir = resolve_path(args.image_dir, settings.product_image_path)
    index_path = resolve_path(args.index_path, settings.visual_embedding_index_file)
    cache_path = resolve_path(args.cache_path, settings.embedding_cache_file)
    model = args.model.strip() or settings.visual_embedding_model_name

    embedder = ArkMultimodalEmbeddingFunction(
        api_key=settings.ark_api_key,
        base_url=settings.ark_base_url,
        model=model,
        timeout_seconds=settings.embedding_timeout_seconds,
        batch_size=1,
    )
    summary = build_product_visual_index(
        product_data_path=product_data,
        product_image_dir=image_dir,
        index_path=index_path,
        embedder=embedder,
        cache=EmbeddingCache(cache_path),
        model=model,
        limit=args.limit,
        checkpoint_interval=args.checkpoint_interval,
        force=args.force,
    )
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))
    return 0


def resolve_path(value: str, default: Path) -> Path:
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else ROOT_DIR / path


if __name__ == "__main__":
    raise SystemExit(main())
