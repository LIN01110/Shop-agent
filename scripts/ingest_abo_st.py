"""
Purpose: ABO 大规模入库加速脚本：先用 sentence-transformers 批量预计算 embedding，
         再以 embeddings 直写 Chroma（跳过 EF 逐批回调），支持断点续跑。

用法（在 rag-shopping-agent-main 目录下，hh_neuron 环境）:
  HF_ENDPOINT=https://hf-mirror.com python scripts/ingest_abo_st.py \
    --products data/benchmarks/abo_full/products.json \
    --persist-dir data/benchmarks/abo/chroma --collection-name abo_products \
    --st-model BAAI/bge-small-zh-v1.5
"""

import argparse
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from server.rag.embeddings import LocalSTEmbeddingFunction  # noqa: E402
from server.rag.identifiers import safe_identifier  # noqa: E402
from server.rag.vector_store import load_product_documents  # noqa: E402
from server.rag.chroma_metadata import to_chroma_metadata  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast ABO ingest with precomputed sentence-transformers embeddings.")
    parser.add_argument("--products", required=True)
    parser.add_argument("--persist-dir", required=True)
    parser.add_argument("--collection-name", default="abo_products")
    parser.add_argument("--st-model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--embed-batch-size", type=int, default=256)
    parser.add_argument("--upsert-chunk-size", type=int, default=10000)
    args = parser.parse_args()

    import chromadb
    from chromadb.config import Settings

    documents = load_product_documents(Path(args.products))
    client = chromadb.PersistentClient(
        path=args.persist_dir, settings=Settings(anonymized_telemetry=False)
    )
    embedder = LocalSTEmbeddingFunction(args.st_model, batch_size=args.embed_batch_size)
    collection_name = f"{args.collection_name}_{safe_identifier(embedder.name())}"
    collection = client.get_or_create_collection(name=collection_name)

    done = collection.count()
    total = len(documents)
    print(f"collection={collection_name} resume_from={done} total={total}", flush=True)
    if done >= total:
        print("already complete", flush=True)
        return 0

    started = time.perf_counter()
    for chunk_start in range(done, total, args.upsert_chunk_size):
        chunk = documents[chunk_start : chunk_start + args.upsert_chunk_size]
        texts = [doc.text if doc.text.strip() else "<empty>" for doc in chunk]
        vectors = embedder(texts)
        collection.upsert(
            ids=[doc.id for doc in chunk],
            embeddings=vectors,
            documents=[doc.text for doc in chunk],
            metadatas=[to_chroma_metadata(doc.metadata) for doc in chunk],
        )
        elapsed = time.perf_counter() - started
        finished = min(chunk_start + args.upsert_chunk_size, total)
        rate = (finished - done) / max(elapsed, 1e-6)
        print(f"progress {finished}/{total} rate={rate:.1f}/s", flush=True)

    print(f"done in {time.perf_counter() - started:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
