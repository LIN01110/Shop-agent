"""
Purpose: 数据灌入脚本：将 products_ref.json 灌入 Chroma 向量库，命令行入口 python -m server.rag.ingest。
"""

from server.config import get_settings
from server.rag.vector_store import (
    ChromaStore,
    build_chroma_embedding_function,
    load_product_documents,
)


def main() -> None:
    settings = get_settings()
    documents = load_product_documents(settings.product_data_file)
    embedding_function, collection_name = build_chroma_embedding_function(
        use_ark_embedding=settings.use_ark_embedding,
        embedding_api=settings.ark_embedding_api,
        api_key=settings.ark_api_key,
        base_url=settings.ark_base_url,
        model=settings.ark_embedding_model,
        timeout_seconds=settings.embedding_timeout_seconds,
        batch_size=settings.embedding_batch_size,
        collection_name=settings.chroma_collection_name,
    )
    if settings.use_ark_embedding and not settings.ark_api_key:
        print("USE_ARK_EMBEDDING=true but ARK_API_KEY is empty; using local hashing embedding.")
    store = ChromaStore(
        settings.chroma_path,
        collection_name=collection_name,
        embedding_function=embedding_function,
    )
    store.add(documents)
    print(f"Ingested {len(documents)} product documents into {settings.chroma_path}")
    print(f"Collection: {collection_name}")
    print(f"Collection count: {store.count()}")


if __name__ == "__main__":
    main()
