# ESCI Small Embedding Evaluation

Date: 2026-06-08

## Goal

Use the local ESCI small subset to run a formal retrieval-quality comparison across:

- `local`: local JSON lexical baseline.
- `chroma + hashing`: Chroma vector-store path with local hashing embedding.
- `chroma + ark`: Chroma vector-store path with Ark/Doubao real text embedding.

The benchmark is retrieval-only. ESCI does not provide real product price, inventory, promotion, or image fields, so those product-experience dimensions are intentionally excluded.

## Commands

```powershell
python scripts\evaluate_esci_retrieval.py --store local --top-k 10 --output data\benchmarks\esci_small\retrieval_report_local_top10.json

python scripts\evaluate_esci_retrieval.py --store chroma --embedding hashing --top-k 10 --output data\benchmarks\esci_small\retrieval_report_chroma_hashing_top10.json

python scripts\evaluate_esci_retrieval.py --store chroma --embedding ark --embedding-batch-size 64 --top-k 10 --output data\benchmarks\esci_small\retrieval_report_chroma_ark_top10.json
```

## Results

| Mode | Recall@10 | MRR@10 | NDCG@10 | P50 latency | P95 latency | Status |
|---|---:|---:|---:|---:|---:|---|
| local lexical | 0.4737 | 0.8714 | 0.7406 | 30.60 ms | 52.98 ms | complete |
| Chroma + hashing embedding | 0.4607 | 0.8971 | 0.7322 | 370.51 ms | 419.79 ms | complete |
| Chroma + Ark/Doubao embedding | - | - | - | - | - | blocked |

## Ark/Doubao Embedding Blocker

The Ark embedding preflight failed before ingestion:

```text
ARK embedding request failed: HTTP 404 for https://ark.cn-beijing.volces.com/api/v3/embeddings; model=doubao-embedding-text-240515
```

The provider response says the model or endpoint does not exist or the current key does not have access. This means the current local key can call the configured LLM path, but cannot yet run the configured embedding model.

## Engineering Changes Made

- `scripts/evaluate_esci_retrieval.py` now supports `--store local|chroma` and `--embedding hashing|ark`.
- The evaluator writes store metadata, label-level recall, and low-NDCG miss examples into the JSON report.
- ESCI Chroma evaluation uses a dedicated benchmark persist directory and collection name.
- Chroma collection names are now bounded to the provider limit and get a stable hash suffix when needed.
- Ark embedding HTTP failures now raise a sanitized `RuntimeError` instead of leaking through Chroma internals.

## Interpretation

- `Chroma + hashing` is not a production semantic embedding benchmark. It is useful only to exercise the Chroma path.
- In this run, hashing did not improve quality over the local lexical baseline and was much slower on the small local setup.
- A real embedding quality conclusion requires enabling an accessible embedding model or endpoint, then rerunning `--embedding ark`.

## Next Check Before Rerun

Confirm in the Ark/Volcengine console that:

- `doubao-embedding-text-240515` or another text-embedding model is enabled for the current API key.
- The configured base URL remains `https://ark.cn-beijing.volces.com/api/v3`.
- The embedding model name is an embedding model name, not a chat endpoint/model.

After that, rerun:

```powershell
python scripts\evaluate_esci_retrieval.py --store chroma --embedding ark --embedding-batch-size 64 --top-k 10 --output data\benchmarks\esci_small\retrieval_report_chroma_ark_top10.json
```
