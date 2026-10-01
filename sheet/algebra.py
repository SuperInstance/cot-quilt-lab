"""sheet.algebra — merge, diff, patch, select, fan, chain, refactor.

diff/patch are exact inverses on the sheet-hash level: patch(a, diff(a, b))
reproduces b (P5). patch preserves a's order for surviving cells and appends
additions in diff order, so pure re-orderings of unchanged cells are outside
what a diff can express. fan/chain/refactor always return sealed cells —
any rewritten cell is re-hashed, never left carrying a stale fingerprint.
"""
from dataclasses import replace

from .model import Cell, cell_hash, seal


def merge(a, b):
    """Union by id, b wins on collision. Returns (merged_cells, collisions)."""
    by_id = {c.id: c for c in a}
    order = [c.id for c in a]
    collisions = []
    for c in b:
        if c.id in by_id:
            collisions.append(c.id)
        else:
            order.append(c.id)
        by_id[c.id] = c  # b wins, collision or not
    return [by_id[i] for i in order], collisions


def diff(a, b):
    """Deltas for cells whose hash differs or whose id is on one side only:
    {"id": str, "from": Cell|None, "to": Cell|None}. Changes and deletions
    in a-order first, then additions in b-order."""
    bmap = {c.id: c for c in b}
    deltas = []
    for ca in a:
        cb = bmap.get(ca.id)
        if cb is None:
            deltas.append({"id": ca.id, "from": ca, "to": None})
        elif cell_hash(cb) != cell_hash(ca):
            deltas.append({"id": ca.id, "from": ca, "to": cb})
    amap = {c.id: c for c in a}
    for cb in b:
        if cb.id not in amap:
            deltas.append({"id": cb.id, "from": None, "to": cb})
    return deltas


def patch(a, deltas):
    """Apply diff deltas: to=None deletes, to=cell sets/adds. Surviving cells
    keep a's order; additions append. Raises on deltas stale against a."""
    by_id = {c.id: c for c in a}
    order = [c.id for c in a]
    added = []
    for d in deltas:
        cid, frm, to = d["id"], d["from"], d["to"]
        if frm is not None and cid in by_id and cell_hash(by_id[cid]) != cell_hash(frm):
            raise ValueError(f"patch: stale delta for cell {cid!r}: sheet "
                             f"changed since diff")
        if to is None:
            if cid not in by_id:
                raise ValueError(f"patch: delta deletes unknown cell {cid!r}")
            del by_id[cid]
            order.remove(cid)
        elif cid in by_id:
            by_id[cid] = to
        else:
            by_id[cid] = to
            added.append(cid)
    return [by_id[i] for i in order] + [by_id[i] for i in added]


def select(cells, kind=None, id_prefix=None):
    """Cells matching all given filters, in sheet order."""
    out = []
    for c in cells:
        if kind is not None and c.kind != kind:
            continue
        if id_prefix is not None and not c.id.startswith(id_prefix):
            continue
        out.append(c)
    return out


def fan(template, n):
    """Replicate a template cell (or list of cells) n times with suffixed
    ids id_1..id_n, re-sealed so each replica hashes its own content."""
    templates = template if isinstance(template, (list, tuple)) else [template]
    return [seal(replace(c, id=f"{c.id}_{i}"))
            for c in templates for i in range(1, n + 1)]


def chain(cells, order):
    """Wire neighbor slots as a linear chain: slot 0 = previous, slot 1 =
    next ("" at the ends), slots 2-3 untouched. `order` is ids or cells;
    cells outside it pass through unchanged."""
    seq = [c.id if isinstance(c, Cell) else str(c) for c in order]
    by_id = {c.id: c for c in cells}
    for cid in seq:
        if cid not in by_id:
            raise ValueError(f"chain: unknown cell id {cid!r}")
    wired = {}
    for i, cid in enumerate(seq):
        prev = seq[i - 1] if i > 0 else ""
        nxt = seq[i + 1] if i + 1 < len(seq) else ""
        old = by_id[cid]
        wired[cid] = seal(replace(
            old, neighbors=(prev, nxt, old.neighbors[2], old.neighbors[3])))
    return [wired.get(c.id, c) for c in cells]


def refactor(cells, id_map):
    """Rename ids and rewrite references consistently: neighbor slots and
    listen entries matching an old id become the new id (emits are event
    names, a separate namespace, so they are left alone)."""
    def ren(x):
        return id_map.get(x, x)

    return [seal(replace(c,
                         id=ren(c.id),
                         neighbors=tuple(ren(n) for n in c.neighbors),
                         listens=tuple(ren(x) for x in c.listens)))
            for c in cells]
