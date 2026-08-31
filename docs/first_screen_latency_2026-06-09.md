# First-Screen Latency Upgrade 2026-06-09

## Goal

Reduce perceived and useful first-screen latency for `/chat` recommendation turns while keeping product facts grounded in the catalog.

## Engineering Change

- Emit `status: thinking` before session loading and downstream planning work.
- Move recommendation LLM generation out of the default first-screen critical path.
- Add `RECOMMENDATION_LLM_BUDGET_SECONDS`.
  - Default: `0.0`
  - Meaning: recommendation turns use deterministic grounded answers and product cards immediately.
  - If set above zero, LLM output may be used only when it completes within that deadline and passes `GroundingGuard`.
- Keep LLM guardrails:
  - Fast safe LLM output can still replace the grounded fallback when an explicit budget is configured.
  - Unsafe LLM output still falls back to catalog-grounded copy.
  - Slow LLM output is cancelled instead of blocking product cards.

This follows the production pattern of keeping search results and structured product facts on the first-screen path, with generative language as an optional enhancement rather than a dependency for rendering.

## Current Benchmark

Command:

```powershell
python scripts\benchmark_first_token.py --url http://127.0.0.1:8017/chat --runs 2 --warmup 1 --threshold-ms 1000 --output docs\first_token_check_current.json
```

Result:

| Case | P95 First Useful Token | P95 First Product Card | P95 Done | Pass |
| --- | ---: | ---: | ---: | --- |
| eye_cream | 334.49 ms | 340.27 ms | 340.39 ms | yes |
| sports_shoes | 343.98 ms | 349.76 ms | 350.02 ms | yes |
| sanya_bundle | 509.17 ms | 515.18 ms | 515.33 ms | yes |
| phone_clarify | 342.42 ms | n/a | 346.26 ms | yes |

## Operational Note

For demos that prioritize natural LLM prose over first-screen latency, set a small explicit budget, for example:

```env
RECOMMENDATION_LLM_BUDGET_SECONDS=0.6
```

For strict first-screen SLA, keep the default `0.0`.
