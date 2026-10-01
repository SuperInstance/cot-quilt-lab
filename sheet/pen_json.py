"""sheet.pen_json — the transport pen.

Byte-stable JSON: sort_keys=True, separators=(",", ":"), ensure_ascii=False,
one trailing newline. Reading is lenient (missing optional fields default),
but a stored `hash` that does not match the recomputed canonical hash is a
corrupted transport and raises.
"""
import json

from .model import Cell, N_DIALS, N_NEIGHBORS, cell_hash, seal


def _cell_to_obj(cell: Cell) -> dict:
    return {
        "id": cell.id,
        "kind": cell.kind,
        "label": cell.label,
        "dials": list(cell.dials),
        "neighbors": list(cell.neighbors),
        "listens": list(cell.listens),
        "emits": list(cell.emits),
        "body": cell.body,
        "hash": cell_hash(cell),  # always the recomputed truth
    }


def to_json(cells) -> str:
    doc = {"cells": [_cell_to_obj(c) for c in cells]}
    return json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False) + "\n"


def _obj_to_cell(row: dict, where: str) -> Cell:
    for required in ("id", "kind"):
        if required not in row:
            raise ValueError(f"{where}: cell missing required field {required!r}")
    dials = row.get("dials", [0] * N_DIALS)
    neighbors = row.get("neighbors", [""] * N_NEIGHBORS)
    cell = Cell(
        id=row["id"],
        kind=row["kind"],
        label=row.get("label", ""),
        dials=dials,
        neighbors=neighbors,
        listens=row.get("listens", []),
        emits=row.get("emits", []),
        body=row.get("body", ""),
    )
    stored = row.get("hash")
    if stored is not None and stored != cell_hash(cell):
        raise ValueError(f"{where}: cell {cell.id!r} hash mismatch: "
                         f"stored {stored}, computed {cell_hash(cell)}")
    return seal(cell)


def from_json(text) -> list:
    """Parse JSON text (object with `cells`, or a bare array) into Cells."""
    doc = json.loads(text)
    rows = doc.get("cells") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise ValueError("from_json: expected {\"cells\": [...]} or [...]")
    return [_obj_to_cell(row, f"from_json[{i}]") for i, row in enumerate(rows)]
