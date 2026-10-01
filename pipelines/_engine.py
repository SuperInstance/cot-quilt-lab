"""Vendored minimal engine surface for pipelines/ (lane C).

Import-complete on its own: lane A builds core/ in parallel on another branch
and may not be merged yet, so this module vendors ONLY the surface
cot_decompose needs, sized from the agent-321 design doc listings:
Result/Provider, call_with_guards, parse_json_loose/structured,
Pipeline/Fn/Fan, Cell. When core/ lands, pipelines/ should switch its
imports over and this file should shrink to a shim (or disappear).

Two deliberate deviations from the doc listings, both forced by the
stub-only, receipts-are-golden contract of this lane:

- No HTTP transport. The doc's Provider.call() shells out to a urllib
  helper; here call() is abstract-ish and stub providers override it.
  This lane makes no network calls and reads no keys.
- Pipeline takes an injectable ``clock`` (default time.time). Real
  wall-clock milliseconds would make receipt_hash unstable across runs;
  a deterministic clock keeps the golden-vector envelope contract (pin P2).
"""
import hashlib
import json
import time


class Result(dict):
    """Normalized call outcome. Plain dict so it serializes everywhere."""

    @property
    def ok(self):
        return int(self.get('status', -1)) == 200 and self.get('content') is not None

    def sha1(self, n=12):
        return hashlib.sha1((self.get('content') or '').encode()).hexdigest()[:n]


class Provider:
    """Base. Subclass build() and parse(); override call() for stubs."""

    name = 'provider'

    def __init__(self, url='stub://local', key=None, timeout=180, headers=None, **defaults):
        self.url, self.key, self.timeout = url, key, timeout
        self.headers = headers or {}
        self.defaults = defaults          # e.g. {'model': 'gpt-4o'}

    def build(self, **kw):
        body = dict(self.defaults)
        body.update({k: v for k, v in kw.items() if v is not None})
        return body

    def parse(self, body):
        return {'content': None}

    def call(self, **kw):
        raise NotImplementedError(
            'stub-only vendored engine: subclass Provider and override call()')


def call_with_guards(provider, max_tokens=4000, burn_retry=True, **kw):
    """One call. Reasoner burn guard: if finish=length and no content, retry bigger."""
    r = provider.call(max_tokens=max_tokens, **kw)
    if burn_retry and r.get('finish') == 'length' and not r.get('content'):
        r2 = provider.call(max_tokens=max(max_tokens * 3, 16000), **kw)
        if r2.ok and r2.get('content'):
            r2['retried_bigger'] = True
            r2['ms'] += r.get('ms', 0)
            return r2
        r['retry_error'] = r2.get('error')
    return r


def parse_json_loose(text):
    if not text:
        return None
    t = text.strip()
    if t.startswith('```'):
        t = t.strip('`')
        if t.startswith('json'):
            t = t[4:]
    a, b = t.find('{'), t.rfind('}')
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(t[a:b + 1])
    except Exception:
        return None


def structured(provider, system, user, repair=True, **kw):
    """Guarantee JSON output (or None) with one repair retry."""
    r = call_with_guards(provider, messages=[
        {'role': 'system', 'content': system + ' Respond with a single JSON object.'},
        {'role': 'user', 'content': user}], **kw)
    j = parse_json_loose(r.get('content'))
    if j is None and repair and r.get('content'):
        r2 = call_with_guards(provider, messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user + '\n\nYour previous reply was not parseable JSON. Return ONLY the JSON object, nothing else.'}],
            **kw)
        j2 = parse_json_loose(r2.get('content'))
        if j2 is not None:
            r2['repaired_json'] = True
            return j2, r2
    return j, r


class Stage:
    name = 'stage'
    requires = ()
    produces = ()

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
    """Ordered stages. ctx flows through. Receipts accumulate. No I/O.

    ``clock`` is injectable so deterministic stubs yield bit-identical
    receipts (see module docstring); it defaults to the real clock.
    """

    def __init__(self, stages, name='pipeline', stop_on_error=True, clock=time.time):
        self.stages = list(stages)
        self.name = name
        self.stop_on_error = stop_on_error
        self.clock = clock

    def run(self, ctx=None, receipts=None):
        ctx = dict(ctx or {})
        receipts = receipts if receipts is not None else []
        for s in self.stages:
            missing = [k for k in s.requires if k not in ctx]
            if missing:
                receipts.append({'stage': s.name, 'ok': False, 'error': f'missing: {missing}'})
                if self.stop_on_error:
                    break
                continue
            t0 = self.clock()
            try:
                fields = s.run(ctx, self) or {}
                receipts.append({'stage': s.name, 'ok': True,
                                 'ms': int((self.clock() - t0) * 1000), **fields})
            except Exception as e:
                receipts.append({'stage': s.name, 'ok': False, 'error': repr(e),
                                 'ms': int((self.clock() - t0) * 1000)})
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


class Cell:
    """Pure z_in -> z_out. Config injected at construction. No disk, no env."""

    def __init__(self, pipeline, config=None, name=None):
        self.pipeline = pipeline
        self.config = dict(config or {})
        self.name = name or pipeline.name

    def invoke(self, z_in):
        ctx = dict(z_in)
        ctx['_config'] = self.config
        out, receipts = self.pipeline.run(ctx)
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
