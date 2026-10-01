# NOTES — provenance and plan

- Source design doc: `downloads/1a0f90b2-fd62-8234-8000-0000b381f5a8_321.md` in the
  fleet workspace (a sibling agent's writeup). Two coherent cuts: a typed
  `cot_quilt` framework (httpx, dataclasses, Protocols) and a general-purpose
  stdlib-only cut. This repo implements the stdlib cut first — no deps, runs
  anywhere a python3 exists, same envelope/receipt semantics.
- Also from the doc: quilt sheet model (cell = id/kind/label/16 dials/4
  neighbors/listens/emits/body/hash; three pens; TUI designed for tmux agents;
  sheet algebra) and a tools.json-style registry convention. `sheet/` takes
  the deterministic parts; the TUI is a later lane (needs a live quilt host).
- What we deliberately defer: real provider keys (stubs only), the embedding /
  semantic canon sort (W2.3 lives in quilt-edge-lab), the TUI, Mothquantum /
  typesafe adapters (interfaces land in core; adapters land when a key does).
- Attribution carried from the doc: cites zai-org GLM-4.6 line + AgentGym and
  "zed/jay" tools; some referenced repos are unknown to us — treated as
  INFERRED until verified.

## Lanes
| lane | branch | delivers |
|---|---|---|
| A engine-core | `engine-core` | core/{providers,guards,stages,cell}.py + pins |
| B sheet-pens | `sheet-pens` | sheet/{model,pen_json,pen_tsv,algebra}.py + pins |
| C pipeline-stubs | `pipeline-stubs` | pipelines/cot_decompose.py on stubs + envelope pin |
