"""sheet.pen_tsv — the spreadsheet pen.

One line per cell:

    id<TAB>kind<TAB>label<TAB>dials (16, comma-joined)<TAB>neighbors (4,
    comma-joined)<TAB>listens<TAB>emits<TAB>body

Body is the last field; newlines (and tabs, CRs, backslashes) inside it are
written escaped (\\n, \\t, \\r, \\\\) so one cell is always one line. `#`-
prefixed lines are comments and empty lines are group separators; both are
legal and round-trip through the sheet meta as a document skeleton
[("comment", line) | ("blank",) | ("cell", id), ...] — comments live in the
meta, never on the cells. Dial values are integers (the doc's 16 signed Q1.15
dials); ids must not start with "#" and none of the comma-joined fields may
contain a comma.
"""
from .model import N_DIALS, N_NEIGHBORS, Cell, seal

_ESCAPES = {"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_UNESCAPES = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\"}


def _escape(body: str) -> str:
    return "".join(_ESCAPES.get(ch, ch) for ch in body)


def _unescape(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] in _UNESCAPES:
            out.append(_UNESCAPES[text[i + 1]])
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _cell_line(cell: Cell) -> str:
    return "\t".join([
        cell.id, cell.kind, cell.label,
        ",".join(str(d) for d in cell.dials),
        ",".join(cell.neighbors),
        ",".join(cell.listens),
        ",".join(cell.emits),
        _escape(cell.body),
    ]) + "\n"


def from_tsv(text: str):
    """Parse TSV text into (cells, meta). Cells come back sealed."""
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()  # final newline is the terminator, not a blank line
    cells, meta = [], []
    for lineno, line in enumerate(lines, 1):
        if line == "":
            meta.append(("blank",))
            continue
        if line.startswith("#"):
            meta.append(("comment", line))
            continue
        fields = line.split("\t")
        if len(fields) != 8:
            raise ValueError(f"tsv line {lineno}: expected 8 tab-separated "
                             f"fields, got {len(fields)}")
        cid, kind, label, dials_s, nbrs_s, listens_s, emits_s, body_s = fields
        dial_parts = dials_s.split(",")
        if len(dial_parts) != N_DIALS:
            raise ValueError(f"tsv line {lineno}: expected {N_DIALS} dials, "
                             f"got {len(dial_parts)}")
        try:
            dials = tuple(int(p) for p in dial_parts)
        except ValueError:
            raise ValueError(f"tsv line {lineno}: dials must be integers: "
                             f"{dials_s!r}") from None
        nbrs = tuple(nbrs_s.split(","))
        if len(nbrs) != N_NEIGHBORS:
            raise ValueError(f"tsv line {lineno}: expected {N_NEIGHBORS} "
                             f"neighbor slots, got {len(nbrs)}")
        cell = Cell(
            id=cid, kind=kind, label=label, dials=dials, neighbors=nbrs,
            listens=() if listens_s == "" else tuple(listens_s.split(",")),
            emits=() if emits_s == "" else tuple(emits_s.split(",")),
            body=_unescape(body_s),
        )
        cells.append(seal(cell))
        meta.append(("cell", cid))
    return cells, meta


def to_tsv(cells, meta=None) -> str:
    """Render cells as TSV. With meta (from from_tsv) the original comments
    and blank group separators are reproduced verbatim; cells missing from
    the skeleton are appended so no content is lost."""
    if meta is None:
        return "".join(_cell_line(c) for c in cells)
    by_id = {}
    for c in cells:
        if c.id in by_id:
            raise ValueError(f"to_tsv: duplicate cell id {c.id!r}")
        by_id[c.id] = c
    out, used = [], set()
    for entry in meta:
        tag = entry[0]
        if tag == "comment":
            out.append(entry[1] + "\n")
        elif tag == "blank":
            out.append("\n")
        elif tag == "cell":
            if entry[1] not in by_id:
                raise ValueError(f"to_tsv: meta references unknown cell "
                                 f"{entry[1]!r}")
            out.append(_cell_line(by_id[entry[1]]))
            used.add(entry[1])
        else:
            raise ValueError(f"to_tsv: bad meta entry {entry!r}")
    out.extend(_cell_line(c) for c in cells if c.id not in used)
    return "".join(out)
