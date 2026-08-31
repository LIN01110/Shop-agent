# Visual Embedding Pipeline

Date: 2026-06-08

## Why This Exists

The previous image path used a lightweight 12x12 RGB signature. That is useful as an offline fallback, but it is not mature visual retrieval. The new path separates expensive multimodal embedding calls from request-time matching:

```text
Product images
  -> image normalization
  -> Ark multimodal embedding
  -> SQLite embedding cache
  -> persistent product-image vector index
  -> multipart image upload
  -> query image embedding
  -> cosine visual retrieval
  -> signature fallback when unavailable
```

## Runtime Switches

```env
USE_VISUAL_EMBEDDING=true
VISUAL_EMBEDDING_MODEL=ep-20260608193302-qztft
VISUAL_EMBEDDING_INDEX_PATH=server/runtime/product_image_vectors.json
EMBEDDING_CACHE_PATH=server/runtime/embedding_cache.sqlite3
```

`VISUAL_EMBEDDING_MODEL` should be the healthy Ark multimodal embedding endpoint ID from the console. The tested local endpoint is backed by `Doubao-embedding-vision-250615`.

## Build Or Resume The Image Index

Smoke build:

```powershell
python scripts\build_visual_embedding_index.py --model ep-20260608193302-qztft --limit 3 --checkpoint-interval 1 --force
```

Observed smoke result:

```json
{
  "products_seen": 3,
  "indexed_count": 3,
  "skipped_existing": 0,
  "failed_count": 0,
  "cache_count": 3
}
```

Full local catalog build:

```powershell
python scripts\build_visual_embedding_index.py --model ep-20260608193302-qztft --checkpoint-interval 10
```

The job is safe to interrupt and rerun:

- Embeddings are cached in SQLite by provider, model, modality, and image content hash.
- The vector index checkpoints to JSON periodically and uses atomic file replacement.
- Existing indexed images are skipped unless `--force` is passed.

## Query-Time Behavior

When `USE_VISUAL_EMBEDDING=true` and the vector index exists:

1. Android uploads the selected image to `POST /uploads/images` as multipart form data.
2. The backend validates MIME type, file size, and image integrity, then stores it under `server/runtime/uploads/images`.
3. `/chat` receives only `image_id`; base64 JSON remains supported only as a backward-compatible fallback.
4. The uploaded image is normalized to a bounded JPEG.
5. The query image embedding is read from cache or generated once.
6. Product image vectors are ranked by cosine similarity.
7. Matches are emitted in the existing `image_analysis` SSE event.
8. If the embedding path is unavailable, the old signature matcher is used as a fallback.

Manual verification with `p_beauty_001_live.jpg`:

```text
p_beauty_001  similarity=1.0  source=multimodal_embedding
p_beauty_002  similarity=0.6755
p_beauty_003  similarity=0.5704
```

## Engineering Notes

- Request-time chat no longer builds product image embeddings.
- External API calls are isolated in the offline index builder and one query-image embedding call.
- Android no longer sends full base64 images in the `/chat` JSON body; it uploads binary images once and references them by `image_id`.
- Uploaded images are TTL-scoped runtime files and are not committed to git.
- Unit tests use fake embedders and do not call external services.
- `server/tests/conftest.py` disables visual embedding during normal test runs.
- Runtime artifacts live under `server/runtime/`, which is gitignored.

## Remaining Production Upgrade

- Replace JSON vector index with Chroma/Qdrant/Milvus for large image catalogs.
- Add a proper multipart upload endpoint so Android does not need to send base64 JSON.
- Add rate limiting and retry/backoff around offline embedding jobs.
- Add model/version metadata checks before using an existing visual index.
