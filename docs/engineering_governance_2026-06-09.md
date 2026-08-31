# Engineering Governance Upgrade 2026-06-09

## Scope

This pass focused on reducing module coupling and production risk before adding more product features.

## Completed

- Split RAG storage into focused modules:
  - `server/rag/types.py`
  - `server/rag/documents.py`
  - `server/rag/embeddings.py`
  - `server/rag/stores.py`
  - `server/rag/scoring.py`
  - `server/rag/chroma_metadata.py`
  - `server/rag/identifiers.py`
- Kept `server/rag/vector_store.py` as a compatibility facade.
- Moved backend dependency construction into `server/app_container.py`.
- Kept `server/agent/orchestrator.py` focused on chat turn orchestration.
- Bound uploaded images to `session_id`; chat can only read images uploaded by the same session.
- Split scenario bundles into catalog, models, matching, rendering, and utility modules.
- Split product search into retrieval wrapper, evidence/reason generation, and URL generation.
- Split product comparison into term parsing, candidate selection, and presentation modules.
- Added Android `ChatRepository`, `BackendConfig`, and `ImageAttachment` so `ChatViewModel` no longer owns network request details.
- Changed streaming text from per-character artificial sleeps to natural response chunks.
- Upgraded `scripts/benchmark_first_token.py` to measure first useful token, first product card, and done latency.
- Added warnings for LLM fallback and upload cleanup failures instead of silently swallowing failures.

## Verification

- `python -m pytest server\tests -q`
- `.\gradlew.bat :app:assembleDebug`

## Remaining Production Work

- Move trace storage from in-memory to Redis/database/log pipeline before multi-instance deployment.
- Run Chroma plus real embedding 10k/50k end-to-end benchmarks on target hardware.
- Replace demo visual matching with full visual embedding indexing for the target catalog size.
- Add auth/rate limits before exposing APIs beyond local or trusted network.
- Continue expanding structured citation and fact checking from product-level evidence toward sentence-level facts.
