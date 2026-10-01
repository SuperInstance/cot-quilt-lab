"""sheet.model — the only object is a Sheet; a sheet is a list of cells.

Cell = (id, kind, label, 16 dials, 4 neighbors, listens, emits, body, hash).
The hash is derived, never input: `cell_hash` runs FNV-1a-64 over the
canonical pipe-joined serialization

    id|kind|label|dial0..15|n0..n3|listens_csv|emits_csv|body

(utf-8, 26 fields, 25 pipes) and `sheet_hash` runs FNV-1a-64 over the
per-cell hash lines joined with "\\n" in sheet order. Any deviation from
that serialization is a protocol change, not a refactor.
"""
from dataclasses import dataclass, replace

FNV1A64_OFFSET_BASIS = 14695981039346656037
FNV1A64_PRIME = 1099511628211
UINT64_MASK = (1 << 64) - 1

N_DIALS = 16
N_NEIGHBORS = 4


def fnv1a64(data: bytes) -> str:
    """FNV-1a-64 of `data` as 16 lowercase hex chars."""
    h = FNV1A64_OFFSET_BASIS
    for byte in data:
        h ^= byte
        h = (h * FNV1A64_PRIME) & UINT64_MASK
    return format(h, "016x")


@dataclass(frozen=True)
class Cell:
    id: str
    kind: str
    label: str = ""
    dials: tuple = (0,) * N_DIALS
    neighbors: tuple = ("",) * N_NEIGHBORS
    listens: tuple = ()
    emits: tuple = ()
    body: str = ""
    hash: str = ""

    def __post_init__(self):
        # accept lists from pens/JSON, normalize to tuples for hashability
        object.__setattr__(self, "dials", tuple(self.dials))
        object.__setattr__(self, "neighbors", tuple(self.neighbors))
        object.__setattr__(self, "listens", tuple(self.listens))
        object.__setattr__(self, "emits", tuple(self.emits))
        for name in ("id", "kind", "label", "body", "hash"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"cell field {name!r} must be str, "
                                 f"got {type(getattr(self, name)).__name__}")
        if len(self.dials) != N_DIALS:
            raise ValueError(f"dials must have {N_DIALS} entries, "
                             f"got {len(self.dials)}")
        if len(self.neighbors) != N_NEIGHBORS:
            raise ValueError(f"neighbors must have {N_NEIGHBORS} slots, "
                             f"got {len(self.neighbors)}")
        for i, d in enumerate(self.dials):
            if not isinstance(d, int) or isinstance(d, bool):
                raise ValueError(f"dial {i} must be int, got {d!r}")


def canonical(cell: Cell) -> str:
    """The normative serialization hashed by cell_hash (no hash field)."""
    parts = [cell.id, cell.kind, cell.label]
    parts.extend(str(d) for d in cell.dials)
    parts.extend(cell.neighbors)
    parts.append(",".join(cell.listens))
    parts.append(",".join(cell.emits))
    parts.append(cell.body)
    return "|".join(parts)


def cell_hash(cell: Cell) -> str:
    return fnv1a64(canonical(cell).encode("utf-8"))


def sheet_hash(cells) -> str:
    """FNV-1a-64 over the per-cell hash lines joined with \\n, in order."""
    return fnv1a64("\n".join(cell_hash(c) for c in cells).encode("utf-8"))


def seal(cell: Cell) -> Cell:
    """Return the cell with its derived hash stored (content unchanged)."""
    return replace(cell, hash=cell_hash(cell))


def seal_sheet(cells):
    return [seal(c) for c in cells]
