# cot-quilt-lab

PoC engine for the agent-321 experiment ideas (a sibling agent's design doc):
build the smallest runtime where **any API is a Provider, any pipeline is a
list of stages, any cell is pure `z_in -> z_out`** — then prove each seam
with FAIL-first pins instead of trusting prose.

Three subpackages, three lanes, one culture:
- `core/` — stdlib-only engine: providers, guards, stages (Pipeline/Fan/Fold), Cell adapter.
- `sheet/` — the quilt sheet model: one object, three pens (python/JSON/TSV), FNV-1a-64 hashes, sheet algebra.
- `pipelines/` — the worked example: cot_decompose running end-to-end on stub providers.

Culture (from the fleet's receipts-over-claims rule): every behavioral claim
names its command and output; pins must FAIL before the implementation exists
(`ENOENT` receipt), and PASS only after; a sealed FAIL beats a heroic maybe.

Status: skeleton. Lanes build `core/`, `sheet/`, `pipelines/` on branches.
