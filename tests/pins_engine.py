#!/usr/bin/env python3
"""FAIL-first pins for the engine-core lane (branch engine-core).

Stdlib only, no pytest, no network: every provider seam is stubbed
(MockLLM injects a canned 200 at the http seam; SequenceLLM feeds canned
Results through .call()). Run from repo root:

    python3 tests/pins_engine.py

Exit 0 with '6/6 pins PASS' only when every pin holds; any failure
prints 'FAIL Pn' + traceback and exits 1. This file was written BEFORE
core/ existed; the first run's ModuleNotFoundError is the fail-first
receipt (pins/failfirst-engine.log).

  P1 MockLLM(OpenAICompat-shaped) returns canned content through the REAL
     Provider.call wiring (build -> http -> parse); 200 -> ok=True plus
     parsed fields (content/cot/model/finish/usage); RawJSON round-trip.
  P2 call_with_guards burn-retry: finish='length' + no content -> retry at
     max(3x, 16000) tokens; retried_bigger=True; ms accumulated. No burn
     -> no retry.
  P3 parse_json_loose: fenced ```json blocks, prose-wrapped objects, None
     for non-JSON; structured() repairs exactly one unparseable reply
     (repaired_json flag), no repair when the first reply parses.
  P4 Pipeline receipt order + stop_on_error: a failing stage stops the run
     (continue_on_error=False by default), receipts carry ok=False + error;
     explicit stop_on_error=False continues; missing requires -> receipt
     then break.
  P5 Fan over 3 items: sub-pipeline runs 3x, projects named keys,
     sub_receipts length 3; non-dict items wrap as {'item': ...}; Fold
     combines fan output.
  P6 Cell.invoke purity + determinism: a default-constructed Cell (no clock
     scaffolding) gives identical receipts AND receipt_hash for the same
     z_in twice, z_in unmutated, no _-prefixed keys in z_out, config
     injected through ctx['_config']; signature() unions requires/produces
     across stages.
"""
import hashlib
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print(f'pins_engine on python {sys.version.split()[0]}')

from core import providers  # noqa: E402
from core.providers import OpenAICompat, RawJSON, Result  # noqa: E402
from core.guards import call_with_guards, parse_json_loose, structured  # noqa: E402
from core.stages import Fan, Fn, Fold, Pipeline  # noqa: E402
from core.cell import Cell  # noqa: E402

CANNED_OK = {
    'model': 'mock-1',
    'choices': [{'message': {'content': 'hello', 'reasoning_content': 'think...'},
                 'finish_reason': 'stop'}],
    'usage': {'prompt_tokens': 3, 'completion_tokens': 2},
}


class MockLLM(OpenAICompat):
    """OpenAICompat-shaped stub: canned 200 body injected at the http seam,
    so Provider.call's real build() -> http() -> parse() wiring runs with
    zero network."""

    def __init__(self, canned=CANNED_OK, **kw):
        super().__init__('mock://llm', **kw)
        self.canned = canned
        self.sent = []

    def _fake_http(self, url, key=None, body=None, timeout=None, method=None, headers=None):
        self.sent.append(body)
        return {'status': 200, 'body': self.canned, 'ms': 5}

    def call(self, **kw):
        saved = providers.http
        providers.http = self._fake_http
        try:
            return super().call(**kw)  # the REAL Provider.call
        finally:
            providers.http = saved


class SequenceLLM:
    """Provider-shaped stub without network: .call() pops canned Result
    payloads in order. A call past the canned list raises IndexError, which
    doubles as a call-count check."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def call(self, **kw):
        self.calls.append(kw)
        r = self.results.pop(0)
        out = Result(status=200, ms=r.get('ms', 0), raw={}, request=kw)
        out.update(r)
        return out


def p1():
    m = MockLLM(model='mock-1', temperature=None)  # None must not reach the body
    r = m.call(messages=[{'role': 'user', 'content': 'hi'}])
    assert r.ok is True, dict(r)
    assert r['status'] == 200 and r['ms'] == 5
    assert r['content'] == 'hello'
    assert r['cot'] == 'think...'
    assert r['model'] == 'mock-1'
    assert r['finish'] == 'stop'
    assert r['usage'] == {'prompt_tokens': 3, 'completion_tokens': 2}
    assert r['request'] == {'model': 'mock-1',
                            'messages': [{'role': 'user', 'content': 'hi'}]}
    assert m.sent and m.sent[0] == r['request']  # build() fed http() verbatim
    assert r.sha1() == hashlib.sha1(b'hello').hexdigest()[:12]
    # RawJSON: non-chat API -> content is the serialized body, data the body
    raw = RawJSON('mock://raw')
    saved = providers.http
    providers.http = lambda *a, **k: {'status': 200, 'body': {'x': 1}, 'ms': 1}
    try:
        rr = raw.call()
    finally:
        providers.http = saved
    assert rr.ok and rr['data'] == {'x': 1} and json.loads(rr['content']) == {'x': 1}


def p2():
    seq = SequenceLLM([
        {'finish': 'length', 'content': None, 'ms': 100},  # burned: no room left
        {'finish': 'stop', 'content': 'x', 'ms': 50},      # retry succeeds
    ])
    r = call_with_guards(seq, max_tokens=4000)
    assert r.get('retried_bigger') is True, dict(r)
    assert r['ms'] == 150, r['ms']  # 50 + 100 accumulated across the retry
    assert r['content'] == 'x'
    assert len(seq.calls) == 2
    assert seq.calls[0]['max_tokens'] == 4000
    assert seq.calls[1]['max_tokens'] == 16000  # max(4000 * 3, 16000)
    # no burn -> exactly one call, no retry flag
    seq2 = SequenceLLM([{'finish': 'stop', 'content': 'hi', 'ms': 7}])
    r2 = call_with_guards(seq2, max_tokens=512)
    assert r2['content'] == 'hi' and len(seq2.calls) == 1
    assert 'retried_bigger' not in r2


def p3():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {'a': 1}
    assert parse_json_loose('```\n{"x": "y"}\n```') == {'x': 'y'}
    assert parse_json_loose('Sure! Here: {"b": [1, 2], "c": {"d": 3}} hope it helps') \
        == {'b': [1, 2], 'c': {'d': 3}}
    assert parse_json_loose('no json here at all') is None
    assert parse_json_loose('') is None
    assert parse_json_loose(None) is None
    assert parse_json_loose('{ broken') is None
    # structured(): one repair retry on an unparseable first reply
    seq = SequenceLLM([
        {'finish': 'stop', 'content': 'sorry, no json', 'ms': 10},
        {'finish': 'stop', 'content': '{"a": 1}', 'ms': 10},
    ])
    j, r = structured(seq, system='You are a JSON machine.', user='give me a')
    assert j == {'a': 1}, j
    assert r.get('repaired_json') is True
    assert len(seq.calls) == 2  # exactly ONE repair
    # clean first reply -> no repair
    seq2 = SequenceLLM([{'finish': 'stop', 'content': '{"ok": true}', 'ms': 1}])
    j2, r2 = structured(seq2, system='s', user='u')
    assert j2 == {'ok': True} and 'repaired_json' not in r2 and len(seq2.calls) == 1


def p4():
    ran = []

    def ok1(ctx, rt):
        ctx['x'] = 1
        ran.append('ok1')
        return {'n': 1}

    def boom(ctx, rt):
        raise ValueError('kaboom')

    def ok3(ctx, rt):
        ran.append('ok3')
        ctx['y'] = 2
        return {}

    stages = [Fn('ok1', ok1, produces=('x',)), Fn('boom', boom),
              Fn('ok3', ok3, produces=('y',))]
    ctx, recs = Pipeline(stages).run({'seed': 0})  # stop_on_error default True
    assert [r['stage'] for r in recs] == ['ok1', 'boom'], recs
    assert recs[0]['ok'] is True and recs[0]['n'] == 1 and 'ms' in recs[0]
    assert recs[1]['ok'] is False and 'kaboom' in recs[1]['error']
    assert 'ok3' not in ran  # the failing stage stopped the run
    # continue_on_error=False is the default; explicit continue runs the rest
    ctx2, recs2 = Pipeline(stages, stop_on_error=False).run({'seed': 0})
    assert [r['stage'] for r in recs2] == ['ok1', 'boom', 'ok3']
    assert 'ok3' in ran and ctx2['y'] == 2
    # missing requires -> receipt then break
    ctx3, recs3 = Pipeline([Fn('ok1', ok1), Fn('ghost', ok1, requires=('ghost',)),
                            Fn('ok3', ok3)]).run({})
    assert len(recs3) == 2 and recs3[1]['ok'] is False
    assert 'ghost' in recs3[1]['error'] and 'missing' in recs3[1]['error']
    assert 'ok3' not in [r['stage'] for r in recs3]


def p5():
    def double(ctx, rt):
        ctx['doubled'] = ctx['n'] * 2

    sub = Pipeline([Fn('double', double, requires=('n',), produces=('doubled',))],
                   name='doubler')
    fan = Fan('fanx', 'seeds', sub, 'results', project=['doubled'])
    ctx, recs = Pipeline([fan], name='top').run({'seeds': [{'n': 1}, {'n': 2}, {'n': 3}]})
    assert ctx['results'] == [{'doubled': 2}, {'doubled': 4}, {'doubled': 6}], ctx['results']
    assert recs[0]['stage'] == 'fanx' and recs[0]['ok'] is True
    assert recs[0]['n_items'] == 3
    assert len(recs[0]['sub_receipts']) == 3  # sub-pipeline ran 3x
    assert all(sr and sr[0]['stage'] == 'double' and sr[0]['ok']
               for sr in recs[0]['sub_receipts'])
    # non-dict items wrap as {'item': item}
    def dbl_item(ctx, rt):
        ctx['doubled'] = ctx['item'] * 2

    fan2 = Fan('f2', 'nums', Pipeline([Fn('dbl_item', dbl_item)]), 'out2',
               project=['doubled'])
    ctx2, _ = Pipeline([fan2]).run({'nums': [1, 2, 3]})
    assert ctx2['out2'] == [{'doubled': 2}, {'doubled': 4}, {'doubled': 6}]
    # Fold: combine fan output back into one value
    fold = Fold('sum', 'results', lambda items, ctx: sum(i['doubled'] for i in items),
                'total')
    ctx3, recs3 = Pipeline([fan, fold]).run({'seeds': [{'n': 1}, {'n': 2}, {'n': 3}]})
    assert ctx3['total'] == 12 and recs3[1]['folded'] is True


def p6():
    def s1(ctx, rt):
        ctx['b'] = ctx['a'] * 2
        return {'note': 's1'}

    def s2(ctx, rt):
        ctx['c'] = ctx['b'] * ctx['_config'].get('factor', 1)  # config injection
        return {'note': 's2'}

    # default construction, default clock: determinism must be the Cell's job,
    # not something the test scaffolds in
    pipe = Pipeline([Fn('double', s1, requires=('a',), produces=('b',)),
                     Fn('scale', s2, requires=('b',), produces=('c',))],
                    name='p6')
    cell = Cell(pipe, config={'factor': 10}, name='p6cell')
    z_in = {'a': 2}
    snap = dict(z_in)
    r1, r2 = cell.invoke(z_in), cell.invoke(z_in)
    assert z_in == snap, z_in  # purity: caller's dict unmutated
    expect_out = {'a': 2, 'b': 4, 'c': 40}
    assert r1['z_out'] == expect_out == r2['z_out']
    assert all(not k.startswith('_') for k in r1['z_out'])  # no _-prefixed keys
    assert '_config' not in r1['z_out']
    assert r1['receipts'] == r2['receipts']  # deterministic receipts, not just hash
    assert r1['receipt_hash'] == r2['receipt_hash'], (r1['receipt_hash'], r2['receipt_hash'])
    assert len(r1['receipt_hash']) == 16
    int(r1['receipt_hash'], 16)  # hex
    assert [r['stage'] for r in r1['receipts']] == ['double', 'scale']
    sig = cell.signature()
    assert sig == {'name': 'p6cell', 'stages': ['double', 'scale'],
                   'requires': ['a', 'b'], 'produces': ['b', 'c']}, sig


PINS = [p1, p2, p3, p4, p5, p6]


def main():
    for i, pin in enumerate(PINS, 1):
        try:
            pin()
        except Exception:
            print(f'FAIL P{i}:')
            traceback.print_exc()
            sys.exit(1)
        print(f'PASS P{i}')
    print('6/6 pins PASS')
    sys.exit(0)


if __name__ == '__main__':
    main()
