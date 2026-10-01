#!/usr/bin/env python3
"""Lane C pins: the cot_decompose pipeline running on deterministic stubs.

P1  full run on stubs: z_out keys + one ok=True receipt per executed stage
P2  determinism: identical stubs + z_in -> identical receipt_hash
P3  burn-retry guard exercised: sample call retried bigger, receipt carries it
P4  empty-degradation path: judge gaps==[] -> refine skipped, graph_v2==merged
P5  cell surface: z_out has no _config; signature() unions stage requires/produces

Plain python3 stdlib. Run from repo root: python3 tests/pins_pipeline.py
Exit 0 iff all pins pass.
"""
import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from pipelines import cot_decompose
    from pipelines.stubs import stub_config
    IMPORT_ERROR = None
except Exception as e:  # FAIL-first: pipelines/ does not exist yet
    cot_decompose = None
    stub_config = None
    IMPORT_ERROR = e

PROMPT = 'Design a minimal local algorithm that keeps a small set of premises coherent.'
Z_IN = {'prompt': PROMPT, 'n_seeds': 3}


def _need():
    assert IMPORT_ERROR is None, f'pipelines not importable: {IMPORT_ERROR!r}'


def run_cell(**stub_kwargs):
    cfg = stub_config(**stub_kwargs)
    cell = cot_decompose.build_cell(cfg)
    return cell.invoke(dict(Z_IN)), cfg


def flatten(receipts):
    """Inline Fan sub-receipts where they executed; drop the wrapper entry."""
    out = []
    for r in receipts:
        subs = r.get('sub_receipts')
        if subs is not None:
            for sub in subs:
                out.extend(flatten(sub))
        else:
            out.append(r)
    return out


def pin_p1():
    _need()
    res, _cfg = run_cell()
    z = res['z_out']
    need = {'seeds', 'samples', 'splits', 'graphs', 'merged', 'judge', 'graph_v2'}
    missing = sorted(need - set(z))
    assert not missing, f'missing z_out keys: {missing}'

    assert len(z['seeds']) == 3
    assert z['seeds'][0]['bits16'] == '0101101010011101', 'first seed must come from canned entropy'
    assert all(len(s['bits16']) == 16 and isinstance(s['seed'], int) for s in z['seeds'])

    assert len(z['samples']) == 3
    for s in z['samples']:
        assert {'lens', 'seed', 'answer', 'cot', 'steps', 'graph'} <= set(s)
        assert s['steps'], 'steps must be non-empty'
        assert len(s['graph']['nodes']) == len(s['steps'])
    assert z['splits'] == [s['steps'] for s in z['samples']]
    assert z['graphs'] == [s['graph'] for s in z['samples']]

    assert z['merged']['clusters'], 'merged must have clusters'
    assert z['merged']['divergent'], 'merged must have divergent entries'
    assert z['judge']['gaps'], 'default stub judge must return gaps so refine folds'
    assert len(z['graph_v2']['nodes']) > len(z['merged']['nodes']), 'refine must add nodes'

    top = res['receipts']
    assert [r['stage'] for r in top] == ['seeds', 'samples', 'merge', 'judge', 'refine'], \
        [r['stage'] for r in top]
    assert all(r.get('ok') is True for r in top), 'every top-level stage must be ok=True'
    assert top[0].get('degraded') is False, 'stub entropy must not degrade'

    flat = flatten(top)
    expected = ['seeds'] + ['sample', 'split', 'wire'] * 3 + ['merge', 'judge', 'refine']
    assert [r['stage'] for r in flat] == expected, [r['stage'] for r in flat]
    assert all(r.get('ok') is True for r in flat), 'every executed stage must be ok=True'
    return f'z_out has all 7 keys; 5 top-level receipts, {len(flat)} executed stages, all ok=True'


def pin_p2():
    _need()
    r1, _ = run_cell()
    r2, _ = run_cell()
    h1, h2 = r1['receipt_hash'], r2['receipt_hash']
    assert isinstance(h1, str) and len(h1) == 16, f'receipt_hash must be 16 hex chars: {h1!r}'
    assert h1 == h2, f'receipt_hash differs across identical runs: {h1} != {h2}'
    assert json.dumps(r1['z_out'], sort_keys=True) == json.dumps(r2['z_out'], sort_keys=True), \
        'z_out differs across identical runs'
    return f'receipt_hash stable across identical runs: {h1}'


def pin_p3():
    _need()
    res, cfg = run_cell(burn_first=True)
    flat = flatten(res['receipts'])
    hits = [r for r in flat
            if r.get('stage') == 'sample'
            and (r.get('receipt') or {}).get('retried_bigger') is True]
    assert hits, 'no sample receipt carries retried_bigger'
    calls = cfg['deepseek'].calls
    assert calls[0].get('finish') == 'length', 'first stub LLM call must burn (finish=length)'
    assert calls[1]['max_tokens'] > calls[0]['max_tokens'], 'guard must retry with bigger budget'
    assert calls[0]['roles'] == ['user'] and calls[0]['model'] == 'deepseek-reasoner', \
        'the burned call must be a sample call'
    return (f'{len(hits)} sample receipt(s) carry retried_bigger=True; '
            f'burn call {calls[0]["max_tokens"]} -> retry {calls[1]["max_tokens"]} tokens')


def pin_p4():
    _need()
    res, _ = run_cell(judge_gaps=[])
    z = res['z_out']
    assert z['judge']['gaps'] == [], 'judge_gaps=[] must reach ctx judge'
    ref = [r for r in flatten(res['receipts']) if r.get('stage') == 'refine'][0]
    assert ref.get('skipped') is True, f'refine must record skipped=True, got {ref}'
    a = json.dumps(z['graph_v2'], sort_keys=True)
    b = json.dumps(z['merged'], sort_keys=True)
    assert a == b, 'graph_v2 must equal merged when judge finds no gaps'
    return 'judge gaps empty -> refine skipped=True, graph_v2 == merged (JSON compare)'


def pin_p5():
    _need()
    cfg = stub_config()
    cell = cot_decompose.build_cell(cfg)
    res = cell.invoke(dict(Z_IN))
    z = res['z_out']
    assert '_config' not in z, 'z_out must not leak _config'
    assert not [k for k in z if k.startswith('_')], 'z_out must not leak underscore keys'
    assert 'receipts' in res and 'receipt_hash' in res

    sig = cell.signature()
    p = cell.pipeline
    assert sig['name'] == 'cot-decompose'
    assert sig['stages'] == [s.name for s in p.stages]
    assert sig['requires'] == sorted({r for s in p.stages for r in s.requires}), \
        'signature requires must be the sorted union of stage requires'
    assert sig['produces'] == sorted({q for s in p.stages for q in s.produces}), \
        'signature produces must be the sorted union of stage produces'
    return (f"requires={sig['requires']}, produces={sig['produces']}; "
            f'z_out carries no underscore keys')


PINS = [
    ('P1', 'full-run-on-stubs', pin_p1),
    ('P2', 'determinism-receipt-hash', pin_p2),
    ('P3', 'burn-retry-guard', pin_p3),
    ('P4', 'empty-gaps-degradation', pin_p4),
    ('P5', 'cell-surface', pin_p5),
]


def main():
    results = []
    for tag, name, fn in PINS:
        try:
            detail = fn()
            results.append(True)
            print(f'PASS {tag} {name} — {detail}')
        except Exception as e:
            results.append(False)
            print(f'FAIL {tag} {name} — {type(e).__name__}: {e}')
            traceback.print_exc()
    n = sum(results)
    print(f'pins: {n}/{len(results)} passed')
    print('RESULT:', 'PASS' if n == len(results) else 'FAIL')
    return 0 if n == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
