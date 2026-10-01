#!/usr/bin/env python3
"""FAIL-first pins for the quilt sheet model (lane B, branch sheet-pens).

Pins the contract from the agent-321 design doc ("The only object: a Sheet",
"The three pens", "Sheet algebra") as cut by the lane spec:

  P1  JSON pen round-trip + byte stability
  P2  TSV pen round-trip incl. # comments and blank group separators preserved
  P3  cell_hash sensitivity (single dial flip, body change) + sheet_hash join
  P4  known-vector freeze: independent inline fnv1a, no sheet/ import
  P5  patch(diff(a,b)) identity, compared via sheet_hash
  P6  fan + chain + select

Stdlib only. Run from the repo root: python3 tests/pins_sheet.py
All sheet imports are lazy so the FAIL-first run (no sheet/ package yet)
reports one FAIL per pin instead of dying at import time.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- independent inline FNV-1a-64 (test-side; never imports sheet/) --------


def _t_fnv1a64(data: bytes) -> str:
    h = 14695981039346656037
    for byte in data:
        h ^= byte
        h = (h * 1099511628211) & ((1 << 64) - 1)
    return format(h, "016x")


# P4 known vector, computed before sheet/ existed via the inline fnv above.
# canonical = "c1|compute|t|" + "0|"*15 + "0" + "|n1||||||"  (26 fields, 25 pipes)
FROZEN_P4_HASH = "361d37158ec82d45"


def _demo_cells():
    """A small handmade sheet: non-ascii label, non-zero dials, wired cells."""
    from sheet.model import Cell, seal

    return [
        seal(Cell(id="P0", kind="constant", label="prompt",
                  body='{"text":"Design a minimal..."}')),
        seal(Cell(id="S1", kind="constant", label="seed1",
                  dials=tuple([102] + [0] * 15), body='{"value":102}')),
        seal(Cell(id="C1", kind="llm", label="cot-café→λ",
                  neighbors=("P0", "S1", "", ""),
                  listens=("ev/generate",), emits=("ev/done",),
                  body='{"model":"deepseek-reasoner","lens":"skeptic"}')),
        seal(Cell(id="D1", kind="formula", label="split1",
                  dials=tuple([0] * 7 + [5] + [0] * 8),
                  neighbors=("C1", "", "", ""), body='{"fn":"split(C1)"}')),
    ]


# --- pins ------------------------------------------------------------------


def pin_p1_json_roundtrip():
    from sheet.pen_json import to_json, from_json

    cells = _demo_cells()
    j1 = to_json(cells)
    assert j1.endswith("\n"), "json pen must end with a trailing newline"
    assert "café→λ" in j1, "ensure_ascii=False must keep non-ascii raw"
    rt = from_json(j1)
    assert rt == cells, "from_json(to_json(x)) must yield equal cells"
    j2 = to_json(rt)
    assert j2 == j1, "to_json(from_json(to_json(x))) must be byte-stable"
    canonical = json.dumps(json.loads(j1), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False) + "\n"
    assert j1 == canonical, "output must match the canonical dump settings"


def pin_p2_tsv_roundtrip():
    from sheet.pen_tsv import to_tsv, from_tsv

    def row(cid, kind, label, dials, nbrs, listens, emits, body):
        return "\t".join([
            cid, kind, label,
            ",".join(str(d) for d in dials),
            ",".join(nbrs),
            ",".join(listens),
            ",".join(emits),
            body,
        ])

    zeros = ",".join(["0"] * 16)
    # body with a literal \n must be written escaped and come back real
    text = "\n".join([
        "# lane B pin sheet",
        "",
        row("P0", "constant", "prompt", [0] * 16, ["", "", "", ""],
            [], [], '{"text":"Design"}'),
        row("C1", "llm", "cot1", [0] * 16, ["P0", "", "", ""],
            ["ev/g"], ["ev/d"], '{"model":"m1"}'),
        "# mid-sheet comment (group below)",
        "",
        row("D1", "formula", "split1", [0] * 7 + [5] + [0] * 8,
            ["C1", "", "", ""], [], [], '{"fn":"split(C1)"}'),
        "",
        row("E1", "http", "hooks", [0] * 16, ["", "", "", ""],
            [], [], "line1\\nline2"),
        "# trailing comment",
    ]) + "\n"
    assert zeros in text  # 16 dials on one line

    cells, meta = from_tsv(text)
    assert len(cells) == 4, f"expected 4 cells, got {len(cells)}"
    by = {c.id: c for c in cells}
    assert by["C1"].neighbors == ("P0", "", "", "")
    assert by["C1"].listens == ("ev/g",) and by["C1"].emits == ("ev/d",)
    assert by["D1"].dials == tuple([0] * 7 + [5] + [0] * 8)
    assert by["E1"].body == "line1\nline2", "escaped \\n must read back as newline"
    assert to_tsv(cells, meta) == text, "comments/blank groups must be preserved"
    # double round-trip: stable under re-parse of our own render
    cells2, meta2 = from_tsv(to_tsv(cells, meta))
    assert cells2 == cells and to_tsv(cells2, meta2) == text


def pin_p3_hash_sensitivity():
    from sheet.model import Cell, seal, cell_hash, sheet_hash

    base = dict(id="c1", kind="compute", label="t",
                neighbors=("n1", "", "", ""))
    c = seal(Cell(**base))
    h0 = cell_hash(c)
    assert c.hash == h0, "seal must store the canonical cell_hash"

    dials = [0] * 16
    dials[7] = 1
    c_flip = seal(Cell(**{**base, "dials": tuple(dials)}))
    assert cell_hash(c_flip) != h0, "flipping dial 7 must change the hash"

    c_body = seal(Cell(**{**base, "body": "x"}))
    assert cell_hash(c_body) not in (h0, cell_hash(c_flip)), \
        "body change must give a third, distinct hash"

    cells = [c, c_flip, c_body]
    joined = "\n".join(cell_hash(x) for x in cells)
    assert sheet_hash(cells) == _t_fnv1a64(joined.encode("utf-8")), \
        "sheet_hash must be fnv1a-64 over the joined per-cell hash lines"
    c_other = seal(Cell(**{**base, "body": "y"}))
    assert sheet_hash(cells) != sheet_hash([c, c_flip, c_other])


def pin_p4_known_vector():
    from sheet.model import Cell, cell_hash

    fields = (["c1", "compute", "t"] + ["0"] * 16
              + ["n1", "", "", ""] + ["", "", ""])
    canonical = "|".join(fields)
    expected = _t_fnv1a64(canonical.encode("utf-8"))
    assert expected == FROZEN_P4_HASH, "inline fnv must reproduce the frozen vector"
    got = cell_hash(Cell(id="c1", kind="compute", label="t",
                         dials=(0,) * 16, neighbors=("n1", "", "", ""),
                         listens=(), emits=(), body=""))
    print(f"  P4 known-vector hash: {got}")
    assert got == FROZEN_P4_HASH, \
        f"cell_hash {got} != frozen {FROZEN_P4_HASH} (canonical form drifted)"


def pin_p5_patch_diff_identity():
    from sheet.model import Cell, seal, sheet_hash
    from sheet.algebra import diff, patch, merge

    a = [
        seal(Cell(id="A1", kind="constant", label="keep", body='{"v":1}')),
        seal(Cell(id="A2", kind="llm", label="change", body='{"m":"old"}')),
        seal(Cell(id="A3", kind="scorer", label="drop", body='{"p":"x"}')),
    ]
    b = [
        seal(Cell(id="A1", kind="constant", label="keep", body='{"v":1}')),
        seal(Cell(id="A2", kind="llm", label="change", body='{"m":"new"}')),
        seal(Cell(id="A4", kind="formula", label="add", body='{"fn":"f"}')),
    ]
    d = diff(a, b)
    assert [e["id"] for e in d] == ["A2", "A3", "A4"]
    e2, e3, e4 = d
    assert e2["from"].body == '{"m":"old"}' and e2["to"].body == '{"m":"new"}'
    assert e3["from"].id == "A3" and e3["to"] is None
    assert e4["from"] is None and e4["to"].id == "A4"

    p = patch(a, d)
    assert sheet_hash(p) == sheet_hash(b), \
        "patch(a, diff(a,b)) must reproduce b's sheet_hash"
    assert diff(a, p) == d, "diff(a, patch(a,d)) must equal d"

    m, collisions = merge(a, b)
    assert collisions == ["A1", "A2"], "merge must report id collisions"
    mby = {c.id: c for c in m}
    assert mby["A2"].body == '{"m":"new"}', "b must win on collision"
    assert mby["A3"].body == '{"p":"x"}', "a-only cell must survive"
    assert mby["A4"].label == "add", "b-only cell must be added"


def pin_p6_chain_select():
    from sheet.model import Cell, seal, cell_hash
    from sheet.algebra import fan, chain, select

    tpl = seal(Cell(id="w", kind="scorer", label="wire",
                    body='{"provider":"typesafe"}'))
    reps = fan([tpl], 3)
    assert [c.id for c in reps] == ["w_1", "w_2", "w_3"], \
        "fan must suffix ids _1.._n"
    assert all(c.kind == "scorer" and c.hash == cell_hash(c) for c in reps)
    assert len({c.hash for c in reps}) == 3, "replicas must hash differently"
    assert [c.id for c in fan(tpl, 2)] == ["w_1", "w_2"], \
        "fan must accept a bare template cell too"

    chained = chain(reps, ["w_1", "w_2", "w_3"])
    by = {c.id: c for c in chained}
    assert by["w_1"].neighbors == ("", "w_2", "", "")
    assert by["w_2"].neighbors == ("w_1", "w_3", "", "")
    assert by["w_3"].neighbors == ("w_2", "", "", "")
    walk, cur = [], "w_1"
    while cur:
        walk.append(cur)
        cur = by[cur].neighbors[1]
    assert walk == ["w_1", "w_2", "w_3"], "slot-1 walk must trace the chain"

    assert len(select(chained, kind="scorer")) == 3
    assert len(select(chained, id_prefix="w_")) == 3
    assert select(chained, kind="llm") == []
    assert select(chained, kind="scorer", id_prefix="w_2") == [by["w_2"]]


# --- runner ----------------------------------------------------------------

PINS = [
    ("P1", pin_p1_json_roundtrip),
    ("P2", pin_p2_tsv_roundtrip),
    ("P3", pin_p3_hash_sensitivity),
    ("P4", pin_p4_known_vector),
    ("P5", pin_p5_patch_diff_identity),
    ("P6", pin_p6_chain_select),
]


def main() -> int:
    npass = 0
    for name, fn in PINS:
        try:
            fn()
            print(f"{name} PASS")
            npass += 1
        except Exception as exc:  # noqa: BLE001 - pins report, never crash
            print(f"{name} FAIL: {type(exc).__name__}: {exc}")
    print(f"pins: {npass}/{len(PINS)} PASS")
    return 0 if npass == len(PINS) else 1


if __name__ == "__main__":
    sys.exit(main())
