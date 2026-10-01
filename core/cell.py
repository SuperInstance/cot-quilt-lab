# core/cell.py
"""Pure z_in -> z_out. Config injected at construction. No disk, no env.

The Quilt-facing adapter: wrap any Pipeline in a Cell and it becomes a
deterministic function with receipts and one fingerprint for the whole run.

Determinism is the default, not the caller's job: invoke() runs the
pipeline on a fixed clock, so stage receipts carry ms=0 and the same z_in
always yields the same receipt_hash. Pass clock=time.time only if you want
real wall-clock ms in stage receipts and accept a hash that varies run to
run. Stages that put nondeterministic fields in their own receipts (a live
provider's ms, say) still shift the hash — purity is every stage's job;
the Cell only removes the engine's own jitter.
"""
import hashlib
import json


def _fixed_clock():
    return 0.0


class Cell:
    def __init__(self, pipeline, config=None, name=None, clock=None):
        self.pipeline = pipeline
        self.config = dict(config or {})
        self.name = name or pipeline.name
        self.clock = clock

    def invoke(self, z_in):
        ctx = dict(z_in or {})
        ctx['_config'] = self.config
        clock = self.clock if self.clock is not None else _fixed_clock
        out, receipts = self.pipeline.run(ctx, clock=clock)
        return {
            'z_out': {k: v for k, v in out.items() if not k.startswith('_')},
            'receipts': receipts,
            'receipt_hash': _hash_receipts(receipts),
        }

    def signature(self):
        return {
            'name': self.name,
            'stages': [s.name for s in self.pipeline.stages],
            'requires': sorted({r for s in self.pipeline.stages for r in s.requires}),
            'produces': sorted({p for s in self.pipeline.stages for p in s.produces}),
        }


def _hash_receipts(receipts):
    return hashlib.sha1(
        json.dumps(receipts, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
