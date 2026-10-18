# Track 02 retrieval report

## Scope

This report measures complete evidence retrieval, not answer accuracy. No fresh LLM answers were generated in this round. No billing, production latency, or LLM savings claim is supported.

The frozen fixture contains 1,595 redacted messages and 445 notes from Claude, Codex, and Antigravity. It has 12 dev and 18 holdout cases, eight genuine multi-source cases, four session-tail user-request cases, and ability labels covering temporal, state, retrieval, extraction, multi-session, knowledge-updates, and abstention. Fixture SHA256: `8d7a6caebba34ba3e79c2522671132c224b5d570e63f8719b0454fc580c5d662`.

The authoritative local backend is FalkorDB. The Python `ContextGraph` is a derived snapshot index, not native Cypher retrieval. The local DB run imported 1,595 records and 445 notes at revision 1. Runtime proof covered persistence, authenticated access, MCP search/open, and frozen-source preservation.

## Final local DB metrics

The held-out split has 18 cases and 33 evidence groups.

| backend | mode | budget | complete cases | groups | context tokens |
|---|---|---:|---:|---:|---:|
| local FalkorDB | hybrid | 800 | 10/18 | 21/33 | 14,068 |
| local FalkorDB | flat | 800 | 5/18 | 12/33 | 14,059 |
| local FalkorDB | flat | 1200 | 6/18 | 15/33 | 21,393 |

Hybrid at 800 tokens uses 34.24% fewer aggregate context tokens than flat at 1200 while retrieving 21/33 groups versus 15/33. This compares different budgets and is reported as a retrieval tradeoff, not a quality-normalized speedup. At the same 800-token budget, hybrid covers 21/33 groups versus flat's 12/33 with nearly identical aggregate context tokens.

Latest-tail hits are reported separately. On dev, two designated latest cases produced 1/2 complete for flat 800 and hybrid 800; at flat 1200, 0/2; hybrid 1200, 1/2. On holdout, both flat and hybrid produced 0/2 latest cases at 800 and 1200. These misses remain visible.

## Backend comparison

The final local DB dev run reached 8/12 complete cases and 13/19 evidence groups with hybrid 800. Final local DB holdout reached 10/18 and 21/33 with hybrid 800. The frozen JSONL dev run from iteration 07 reached 9/12 and 14/19 with hybrid 800. The difference is expected: database snapshot metadata and canonical agent normalization affect card ordering and packing. The offline result is not a production substitute and is not selected as the headline.

## Reproduction

Run self-test:

```powershell
python scripts/benchmark_context_graph.py --self-test
```

Run the final local DB dev matrix:

```powershell
python scripts/benchmark_context_graph.py --directory D:\graph_hacks\artifacts\new-game-benchmark --cases D:\graph_hacks\artifacts\context-graph-hard\cases.json --output D:\graph_hacks\artifacts\context-graph-hard\final-db-dev-rerun --split dev --modes flat,hybrid --budgets 800,1200 --write-prompts --local-workspace new-game
```

Run the local DB holdout matrix into a new output directory:

```powershell
python scripts/benchmark_context_graph.py --directory D:\graph_hacks\artifacts\new-game-benchmark --cases D:\graph_hacks\artifacts\context-graph-hard\cases.json --output D:\graph_hacks\artifacts\context-graph-hard\final-db-holdout-rerun --split holdout --modes flat,hybrid --budgets 800,1200 --write-prompts --local-workspace new-game
```

Each result records the engine source hash, fixture hash, corpus fingerprint, local backend, workspace, revision, import counts, per-case selected sources, provenance, context tokens, and latency. The runner aborts if frozen input, engine source, or local revision changes during a run.

## Final verification

The repaired local API returned state 200, unauthenticated access returned 401, and authenticated search/open returned 200. The full Python suite passed with 13 tests, including the real FalkorDB Track02 tests; Node `tests/test_chat_graph.cjs` and `tests/test_explorer_content.cjs` also passed. Recomputing all 120 final dev and holdout contexts produced zero mismatches after the Store.state repair, and the frozen metrics above remained unchanged.

## Limits and next work

The benchmark does not test generated answer correctness, semantic equivalence beyond explicit evidence patterns, external multi-user authorization, cloud failover, or billing. FalkorDB is persistent authority for the local application; Python retrieval is a derived cache. The next retrieval experiments should use a newly sealed holdout; this holdout remains a regression check and must not guide tuning.
