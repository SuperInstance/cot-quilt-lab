"""pipelines/cot_decompose.py — the original worked example, ported onto the
engine, per the agent-321 design doc (section of the same name).

Same semantics as the doc listing: no disk writes, no env reads, no module
globals; each stage mutates ctx (data plane) and returns receipt fields
(control plane). Deltas from the doc listing, all deliberate:

- imports come from pipelines._engine (vendored, lane-local) because lane
  A's core/ is not merged yet;
- _brief() also surfaces retried_bigger / repaired_json so guard activity
  is visible in receipts (pin P3 needs retried_bigger);
- the merge stage additionally materializes ctx['splits'] and ctx['graphs']
  from the fan-out results — the z_out contract for this lane (pin P1);
- both Pipelines take an injectable clock via config['clock'] so stub runs
  produce bit-identical receipts (pin P2, the golden-vector contract).
"""
import hashlib
import json
import os
import time

from ._engine import Cell, Fan, Fn, Pipeline, call_with_guards, structured

DEFAULT_LENSES = [
    'a skeptic who trusts only what can be checked against the stated premises',
    'an engineer who wants the smallest mechanism that could work',
    'a teacher who explains by building one small piece at a time',
]
RUBRIC_LOAD = ['decorative: could be cut with no effect',
               'minor: touches the answer slightly',
               'load-bearing: removing it breaks the path',
               'pivotal: the step that makes the answer']


def _brief(r):
    return {'status': r.get('status'), 'ms': r.get('ms'), 'model': r.get('model'),
            'usage': r.get('usage'), 'finish': r.get('finish'),
            'content_sha1': hashlib.sha1((r.get('content') or '').encode()).hexdigest()[:12],
            'retried_bigger': r.get('retried_bigger'), 'repaired_json': r.get('repaired_json')}


def _extract_graph(steps, answers):
    nodes, edges = [], []
    for s in steps:
        sc = answers.get(f'score_{s["id"]}') or {}
        probs = sc.get('probabilities')
        exp = None
        if isinstance(probs, dict):
            try:
                exp = round(sum(float(p) * float(i) for i, p in probs.items()), 3)
            except Exception:
                exp = None
        nodes.append({'id': s['id'], 'kind': s.get('kind', 'INFERENCE'),
                      'text': s.get('text', '')[:220],
                      'load_score': sc.get('score'), 'load_expected': exp})
        dep = answers.get(f'dep_{s["id"]}') or {}
        tgt = dep.get('choice')
        if tgt:
            edges.append({'from': tgt, 'to': s['id'],
                          'p': (dep.get('probabilities') or {}).get(tgt)})
    return {'nodes': nodes, 'edges': edges}


def _merge_graphs(j, samples):
    merged = {'nodes': [], 'edges': [], 'clusters': j.get('clusters', []),
              'divergent': j.get('divergent', [])}
    for c in merged['clusters']:
        mem = c.get('members') or []
        samples_seen = sorted({m.get('sample') for m in mem
                               if isinstance(m, dict) and 'sample' in m})
        scores = []
        for m in mem:
            gi = m.get('sample')
            if isinstance(gi, int) and 0 <= gi < len(samples):
                for n in (samples[gi].get('graph') or {}).get('nodes', []):
                    if n['id'] == m.get('id') and n.get('load_expected') is not None:
                        scores.append(n['load_expected'])
        merged['nodes'].append({'label': c.get('label'), 'text': c.get('text'),
                                'samples': samples_seen, 'occurrence': len(samples_seen),
                                'max_load': max(scores) if scores else None})
    merged['edges'] = [e for s in samples for e in (s.get('graph') or {}).get('edges', [])]
    return merged


def build(config):
    """config: {'deepseek': LLM provider, 'typesafe': scorer provider,
    'moth': entropy provider, optional 'lenses', optional 'clock'}."""
    ds, ts, moth = config['deepseek'], config['typesafe'], config['moth']
    lenses = config.get('lenses') or DEFAULT_LENSES
    clock = config.get('clock') or time.time

    # ---- stages -----------------------------------------------------------
    def seeds(ctx, rt):
        n = int(ctx.get('n_seeds', 3))
        out, degraded = [], False
        for _ in range(n):
            bits = []
            for _ in range(16):
                r = moth.call(params={'shots': 1})
                # provider-specific polling elided; falls back per-bit
                b = ((r.get('data') or {}).get('result') or {}).get('output')
                if b in ('heads', 'tails'):
                    bits.append('1' if b == 'heads' else '0')
                else:
                    bits.append(str(int.from_bytes(os.urandom(1), 'big') & 1))
                    degraded = True
            out.append({'seed': int(''.join(bits), 2), 'bits16': ''.join(bits)})
        ctx['seeds'] = out
        ctx['lenses'] = lenses[:n]
        ctx['sample_plan'] = [{'lens': l, 'seed': s['seed']}
                              for l, s in zip(ctx['lenses'], out)]
        return {'degraded': degraded, 'seeds': [s['seed'] for s in out]}

    def sample(ctx, rt):
        lens = ctx['lens']
        r = call_with_guards(ds, model='deepseek-reasoner', max_tokens=16000,
                             seed=ctx.get('seed'),
                             messages=[{'role': 'user',
                                        'content': f'Lens: you are {lens}.\n\n{ctx["prompt"]}'}])
        ctx['answer'], ctx['cot'] = r.get('content'), r.get('cot')
        return {'receipt': _brief(r), 'lens': lens}

    def split(ctx, rt):
        cot = ctx.get('cot') or ctx.get('answer') or ''
        j, r = structured(ds,
            system=('You compress a chain-of-thought into reasoning cells. Return ONLY JSON: '
                    '{"steps":[{"id":"s1","kind":"PREMISE|INFERENCE|CHECK|DECISION|CONCLUSION",'
                    '"text":"one line, self-contained"}]}. Max 8 steps. Keep the logical spine; '
                    'drop rhetoric. A CHECK is a self-verification move; a DECISION is a fork taken.'),
            user=f'Chain-of-thought (lens: {ctx.get("lens")}):\n"""\n{cot[:9000]}\n"""',
            model='deepseek-flash', max_tokens=1500)
        ctx['steps'] = (j or {}).get('steps', [])[:8] if j else []
        return {'receipt': _brief(r), 'n_steps': len(ctx['steps'])}

    def wire(ctx, rt):
        steps = ctx.get('steps') or []
        if not steps:
            ctx['graph'] = {'nodes': [], 'edges': []}
            return {'skipped': True}
        ids = [s['id'] for s in steps]
        q = {}
        for i, s in enumerate(steps):
            q[f'score_{s["id"]}'] = {
                'type': 'score',
                'instructions': f'How load-bearing is this step for reaching the answer? Step: {s["text"]}',
                'criteria': RUBRIC_LOAD}
            earlier = ids[:i]
            if earlier:
                q[f'dep_{s["id"]}'] = {
                    'type': 'choice',
                    'instructions': f'Which earlier step does "{s["text"]}" most depend on?',
                    'criteria': {e: next(x['text'] for x in steps if x['id'] == e)[:160]
                                 for e in earlier[:6]}}
        r = ts.call(model='jev-latest',
                    state=f'Prompt: {ctx["prompt"]}\nFinal answer: {ctx.get("answer","")}\nReasoning steps under judgment.',
                    questions=q)
        answers = (r.get('data') or {}).get('answers') or {}
        ctx['graph'] = _extract_graph(steps, answers)
        return {'receipt': _brief(r), 'ts_status': r.get('status'),
                'n_nodes': len(ctx['graph']['nodes']), 'n_edges': len(ctx['graph']['edges'])}

    def merge(ctx, rt):
        samples = ctx.get('samples') or []
        ctx['splits'] = [s.get('steps') or [] for s in samples]
        ctx['graphs'] = [s.get('graph') or {} for s in samples]
        lst = [{'lens': s.get('lens'), 'answer': (s.get('answer') or '')[:200],
                'steps': [{'id': n['id'], 'text': n['text'], 'kind': n['kind']}
                          for n in (s.get('graph') or {}).get('nodes', [])]}
               for s in samples]
        j, r = structured(ds,
            system=('Align reasoning steps across variants of the same reasoning. Return ONLY JSON: '
                    '{"clusters":[{"label":"short concept name","members":[{"sample":0,"id":"s1"}],'
                    '"text":"one line merging the members"}],'
                    '"divergent":[{"label":"alternate route only in some samples","samples":[0,2],'
                    '"text":"one line"}]}. Cluster steps that express the same logical move. '
                    'Keep genuinely different routes as divergent entries.'),
            user=json.dumps(lst, ensure_ascii=False)[:12000],
            model='deepseek-flash', max_tokens=2000)
        ctx['merged'] = _merge_graphs(j or {}, samples)
        return {'receipt': _brief(r), 'clusters': len(ctx['merged']['clusters']),
                'divergent': len(ctx['merged']['divergent'])}

    def judge(ctx, rt):
        m = ctx['merged']
        compact = [{'label': n['label'], 'occurrence': n['occurrence'],
                    'max_load': n['max_load'], 'text': n['text']} for n in m['nodes']]
        divs = [{'label': d.get('label'), 'samples': d.get('samples'), 'text': d.get('text')}
                for d in m['divergent']]
        j, r = structured(ds,
            system=('You are the input-output simulator: judge how thorough a decomposition of '
                    'YOUR OWN reasoning is. Return ONLY JSON: {"thoroughness": 0-10, '
                    '"gaps": ["missing move", ...max 5], "verdict": "one sentence"}.'),
            user=(f'Original prompt: {ctx["prompt"]}\n\n'
                  f'Your answers: {json.dumps([s.get("answer") for s in ctx["samples"]])[:600]}\n\n'
                  f'Decomposed graph: {json.dumps(compact, ensure_ascii=False)[:6000]}\n'
                  f'Alternate routes: {json.dumps(divs, ensure_ascii=False)[:1500]}\n'
                  'What logical moves of your actual reasoning does this graph miss?'),
            model='deepseek-reasoner', max_tokens=4000)
        ctx['judge'] = {'score': (j or {}).get('thoroughness'),
                        'gaps': ((j or {}).get('gaps') or [])[:5],
                        'verdict': (j or {}).get('verdict')}
        return {'receipt': _brief(r), 'score': ctx['judge']['score'],
                'gaps': len(ctx['judge']['gaps'])}

    def refine(ctx, rt):
        gaps = (ctx.get('judge') or {}).get('gaps') or []
        m = ctx['merged']
        if not gaps:
            ctx['graph_v2'] = m
            return {'skipped': True}
        j, r = structured(ds,
            system=('Fold critique gaps into a reasoning graph. Return ONLY JSON: '
                    '{"added":[{"id":"g1","kind":"INFERENCE|CHECK|DECISION","text":"one line",'
                    '"bridges":["label of cluster it connects to"]}]}.'),
            user=(f'Existing clusters: {json.dumps([n["label"] for n in m["nodes"]])}\n'
                  f'Judge gaps: {json.dumps(gaps)}'),
            model='deepseek-flash', max_tokens=1200)
        added = [{'id': a.get('id'), 'kind': a.get('kind', 'INFERENCE'),
                  'text': a.get('text'), 'bridges': a.get('bridges', []),
                  'origin': 'critique'} for a in ((j or {}).get('added') or [])]
        g3 = dict(m)
        g3['nodes'] = m['nodes'] + added
        ctx['graph_v2'] = g3
        return {'receipt': _brief(r), 'added': len(added)}

    # ---- assemble ---------------------------------------------------------
    sample_pipe = Pipeline([
        Fn('sample', sample, produces=('answer', 'cot')),
        Fn('split',  split,  produces=('steps',)),
        Fn('wire',   wire,   produces=('graph',)),
    ], name='sample', clock=clock)

    return Pipeline([
        Fn('seeds', seeds, produces=('seeds', 'lenses', 'sample_plan')),
        Fan('samples', 'sample_plan', sample_pipe, 'samples',
            project=['lens', 'seed', 'answer', 'cot', 'steps', 'graph']),
        Fn('merge',  merge,  produces=('merged', 'splits', 'graphs')),
        Fn('judge',  judge,  produces=('judge',)),
        Fn('refine', refine, produces=('graph_v2',)),
    ], name='cot-decompose', clock=clock)


def build_cell(config):
    """The Quilt-facing adapter: invoke -> {z_out, receipts, receipt_hash}."""
    return Cell(build(config), config=config, name='cot-decompose')
