# core/stages.py
"""Pipelines as data.

A stage mutates ctx (the data plane) and returns a dict of receipt fields
(the control plane). That one rule keeps everything composable. Pipeline,
Fan (fan-out), and Fold (combine) are the only composites you need.

Time is injectable two ways: Pipeline(..., clock=...) sets the pipeline's
clock, and run(..., clock=...) overrides it per run — a Cell passes a fixed
clock so receipt_hash stays deterministic. No I/O, no env, no globals in
the hot path.
"""
import time


class Stage:
    name = 'stage'
    requires = ()
    produces = ()

    def check(self, ctx):
        """Required keys missing from ctx (sorted -> deterministic receipts)."""
        return [k for k in sorted(self.requires) if k not in ctx]

    def run(self, ctx, rt):
        raise NotImplementedError


class Fn(Stage):
    """Wrap a callable (ctx, rt) -> dict-of-receipt-fields."""

    def __init__(self, name, fn, requires=(), produces=()):
        self.name, self.fn = name, fn
        self.requires, self.produces = set(requires), set(produces)

    def run(self, ctx, rt):
        return self.fn(ctx, rt) or {}


class Pipeline:
    """Ordered stages. ctx flows through. Receipts accumulate. No I/O."""

    def __init__(self, stages, name='pipeline', stop_on_error=True, clock=time.time):
        self.stages = list(stages)
        self.name = name
        self.stop_on_error = stop_on_error
        self.clock = clock

    def run(self, ctx=None, receipts=None, clock=None):
        """clock overrides self.clock for this run (Cells pass a fixed one)."""
        clock = clock if clock is not None else self.clock
        ctx = dict(ctx or {})
        receipts = receipts if receipts is not None else []
        for s in self.stages:
            missing = s.check(ctx)
            if missing:
                receipts.append({'stage': s.name, 'ok': False, 'error': f'missing: {missing}'})
                if self.stop_on_error:
                    break
                continue
            t0 = clock()
            try:
                fields = s.run(ctx, self) or {}
                receipts.append({'stage': s.name, 'ok': True,
                                 'ms': int((clock() - t0) * 1000), **fields})
            except Exception as e:
                receipts.append({'stage': s.name, 'ok': False, 'error': repr(e),
                                 'ms': int((clock() - t0) * 1000)})
                if self.stop_on_error:
                    break
        return ctx, receipts


class Fan(Stage):
    """Run a sub-pipeline once per item in ctx[items_key]. Project named keys."""

    def __init__(self, name, items_key, sub, out_key, project=None):
        self.name, self.items_key, self.sub, self.out_key = name, items_key, sub, out_key
        self.project = project
        self.requires = {items_key}
        self.produces = {out_key}

    def run(self, ctx, rt):
        items = ctx[self.items_key]
        results, sub_receipts = [], []
        for i, item in enumerate(items):
            sub_ctx = dict(ctx)
            sub_ctx.update(item if isinstance(item, dict) else {'item': item})
            sub_ctx['_index'] = i
            out, rec = self.sub.run(sub_ctx)
            keys = self.project or list(out.keys())
            results.append({k: out.get(k) for k in keys})
            sub_receipts.append(rec)
        ctx[self.out_key] = results
        return {'n_items': len(items), 'sub_receipts': sub_receipts}


class Fold(Stage):
    """Combine ctx[items_key] into ctx[out_key] via fn(items, ctx) -> value."""

    def __init__(self, name, items_key, fn, out_key):
        self.name, self.items_key, self.fn, self.out_key = name, items_key, fn, out_key
        self.requires = {items_key}
        self.produces = {out_key}

    def run(self, ctx, rt):
        ctx[self.out_key] = self.fn(ctx[self.items_key], ctx)
        return {'folded': True}
