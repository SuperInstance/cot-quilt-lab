"""Deterministic stub providers for cot_decompose (lane C).

Three stubs, all seeded, all recording calls into ``self.calls``:

- StubLLM:     canned chat responses routed by prompt markers; optionally
               burns the very first call (finish='length', no content) so
               call_with_guards' retry-bigger path is exercised (pin P3).
- StubEntropy: canned bitstrings first, then deterministic PRNG bits
               (random.Random(seed)), one bit per call, delivered as
               heads/tails like the coin-toss entropy API.
- StubScorer:  canned per-question answers dict (score_<id> / dep_<id>),
               with a deterministic fallback for unknown question keys.

Also here: DetClock, a deterministic wall clock for Pipeline receipts, and
stub_config(), which wires the three stubs into the config dict that
cot_decompose.build()/build_cell() expect. No network, no keys, no disk.
"""
import json
import random

from ._engine import Provider, Result

LENS_KEYS = ('skeptic', 'engineer', 'teacher')


def _j(obj):
    return json.dumps(obj, ensure_ascii=False)


# --- canned LLM responses, routed by prompt markers -------------------------

SAMPLES = {
    'skeptic': {
        'answer': '42 — but only the claim that survives checking against the stated premises.',
        'cot': ('Premise check: only the three stated premises may enter; everything else is suspect. '
                'Mechanism: fold the premises into one working claim. '
                'Check: test that claim against each premise; nothing contradicts. '
                'Decision: keep the smallest surviving claim and drop the rest. '
                'Conclusion: the defensible answer is 42.'),
    },
    'engineer': {
        'answer': '42, produced by the smallest mechanism that could work: one fold over the premises.',
        'cot': ('Constraint: the mechanism must be the smallest one that could work. '
                'Mechanism: a single fold over the premise set produces the candidate. '
                'Conclusion: the minimal mechanism yields 42; anything larger is decoration.'),
    },
    'teacher': {
        'answer': '42, built one small piece at a time from the premises.',
        'cot': ('Piece one: restate the three premises in order. '
                'Piece two: combine the first two premises into a partial claim. '
                'Piece three: extend the partial claim with the third premise. '
                'Check: verify each piece before building the next one. '
                'Conclusion: assemble the verified pieces; the answer is 42.'),
    },
    'default': {
        'answer': '42.',
        'cot': 'Restate the premises. Fold them once. Conclude: 42.',
    },
}

# skeptic's split comes back fenced, to exercise parse_json_loose's fence strip
STEPS = {
    'skeptic': '```json\n' + _j({'steps': [
        {'id': 's1', 'kind': 'PREMISE', 'text': 'only the stated premises may enter the reasoning'},
        {'id': 's2', 'kind': 'INFERENCE', 'text': 'fold the premises into one working claim'},
        {'id': 's3', 'kind': 'CHECK', 'text': 'test the working claim against every premise for contradiction'},
        {'id': 's4', 'kind': 'CONCLUSION', 'text': 'keep the smallest claim that survives; the answer is 42'},
    ]}) + '\n```',
    'engineer': _j({'steps': [
        {'id': 's1', 'kind': 'PREMISE', 'text': 'the mechanism must be the smallest one that could work'},
        {'id': 's2', 'kind': 'INFERENCE', 'text': 'one fold over the premises produces the candidate answer'},
        {'id': 's3', 'kind': 'CONCLUSION', 'text': 'the minimal mechanism yields 42'},
    ]}),
    'teacher': _j({'steps': [
        {'id': 's1', 'kind': 'PREMISE', 'text': 'start from the three given premises and nothing else'},
        {'id': 's2', 'kind': 'INFERENCE', 'text': 'combine the first two premises into a partial claim'},
        {'id': 's3', 'kind': 'INFERENCE', 'text': 'extend the partial claim with the third premise'},
        {'id': 's4', 'kind': 'CHECK', 'text': 'verify each piece before building the next'},
        {'id': 's5', 'kind': 'CONCLUSION', 'text': 'assemble the verified pieces into the answer 42'},
    ]}),
    'default': _j({'steps': [
        {'id': 's1', 'kind': 'PREMISE', 'text': 'restate the premises'},
        {'id': 's2', 'kind': 'CONCLUSION', 'text': 'fold once; conclude 42'},
    ]}),
}

MERGE_JSON = _j({
    'clusters': [
        {'label': 'premise intake',
         'members': [{'sample': 0, 'id': 's1'}, {'sample': 1, 'id': 's1'}, {'sample': 2, 'id': 's1'}],
         'text': 'restate the given premises before reasoning'},
        {'label': 'core mechanism',
         'members': [{'sample': 0, 'id': 's2'}, {'sample': 1, 'id': 's2'}],
         'text': 'fold the premises into one working claim'},
        {'label': 'final conclusion',
         'members': [{'sample': 0, 'id': 's4'}, {'sample': 2, 'id': 's5'}],
         'text': 'assemble the surviving claim into the answer'},
    ],
    'divergent': [
        {'label': 'verification detour',
         'samples': [0, 2],
         'text': 'only the skeptic and teacher variants pause to check the claim mid-reasoning'},
    ],
})

JUDGE = {
    'thoroughness': 7,
    'gaps': ['no explicit falsification move against the premises',
             'no complexity bound on the folding mechanism'],
    'verdict': 'The spine is solid but two moves of the actual reasoning are missing.',
}

REFINE_JSON = _j({'added': [
    {'id': 'g1', 'kind': 'CHECK',
     'text': 'state one falsification test the mechanism must survive',
     'bridges': ['core mechanism']},
    {'id': 'g2', 'kind': 'INFERENCE',
     'text': 'bound the mechanism to a single pass over the premises',
     'bridges': ['premise intake', 'core mechanism']},
]})

USAGE = {
    'sample': {'prompt_tokens': 96, 'completion_tokens': 640, 'total_tokens': 736},
    'split': {'prompt_tokens': 512, 'completion_tokens': 220, 'total_tokens': 732},
    'merge': {'prompt_tokens': 640, 'completion_tokens': 180, 'total_tokens': 820},
    'judge': {'prompt_tokens': 720, 'completion_tokens': 140, 'total_tokens': 860},
    'refine': {'prompt_tokens': 200, 'completion_tokens': 90, 'total_tokens': 290},
    'other': {'prompt_tokens': 8, 'completion_tokens': 8, 'total_tokens': 16},
    'burn': {'prompt_tokens': 11, 'completion_tokens': 0, 'total_tokens': 11},
}

# --- canned scorer answers, keyed by the question ids the wire stage builds -

SCORER_ANSWERS = {
    'score_s1': {'score': 'load-bearing', 'probabilities': {'0': 0.05, '1': 0.10, '2': 0.60, '3': 0.25}},
    'score_s2': {'score': 'pivotal', 'probabilities': {'0': 0.00, '1': 0.05, '2': 0.15, '3': 0.80}},
    'score_s3': {'score': 'load-bearing', 'probabilities': {'0': 0.10, '1': 0.20, '2': 0.50, '3': 0.20}},
    'score_s4': {'score': 'minor', 'probabilities': {'0': 0.30, '1': 0.40, '2': 0.20, '3': 0.10}},
    'score_s5': {'score': 'pivotal', 'probabilities': {'0': 0.05, '1': 0.05, '2': 0.10, '3': 0.80}},
    'dep_s2': {'choice': 's1', 'probabilities': {'s1': 0.90}},
    'dep_s3': {'choice': 's2', 'probabilities': {'s1': 0.20, 's2': 0.70}},
    'dep_s4': {'choice': 's1', 'probabilities': {'s1': 0.60, 's2': 0.20, 's3': 0.20}},
    'dep_s5': {'choice': 's4', 'probabilities': {'s1': 0.10, 's2': 0.10, 's3': 0.20, 's4': 0.60}},
}
FALLBACK_SCORE = {'score': 'minor', 'probabilities': {'0': 0.25, '1': 0.50, '2': 0.25, '3': 0.00}}


class StubLLM(Provider):
    """Canned chat-completions provider. Routes by prompt markers.

    burn_first=True makes the very first call return finish='length' with no
    content, so call_with_guards takes its retry-bigger path (pin P3).
    judge_gaps overrides the canned judge gaps (None = canned default,
    a list = use it verbatim, [] = the empty-degradation path of pin P4).
    """

    name = 'stub-llm'

    def __init__(self, seed=0, burn_first=True, judge_gaps=None, **defaults):
        super().__init__(**defaults)
        self.seed = seed
        self.judge_gaps = judge_gaps
        self._rng = random.Random(seed)
        self._burn_pending = bool(burn_first)
        self.calls = []

    def _ms(self):
        return 20 + self._rng.randrange(90)

    def _route(self, msgs):
        system = next((m.get('content') or '' for m in msgs if m.get('role') == 'system'), None)
        text = '\n'.join(m.get('content') or '' for m in msgs)
        lens = next((k for k in LENS_KEYS if k in text), 'default')
        if system is None:
            return ('sample' if text.startswith('Lens: ') else 'other'), lens
        for marker, kind in (('reasoning cells', 'split'),
                             ('Align reasoning steps', 'merge'),
                             ('input-output simulator', 'judge'),
                             ('Fold critique gaps', 'refine')):
            if marker in system:
                return kind, lens
        return 'other', lens

    def call(self, **kw):
        body = self.build(**kw)
        msgs = body.get('messages') or []
        rec = {'i': len(self.calls), 'model': body.get('model'),
               'max_tokens': body.get('max_tokens'),
               'roles': [m.get('role') for m in msgs]}
        if self._burn_pending:
            self._burn_pending = False
            rec['finish'] = 'length'
            self.calls.append(rec)
            return Result(status=200, ms=self._ms(), request=body, content=None, cot=None,
                          model=body.get('model'), finish='length', usage=dict(USAGE['burn']))
        kind, lens = self._route(msgs)
        rec.update(kind=kind, finish='stop')
        self.calls.append(rec)
        out = Result(status=200, ms=self._ms(), request=body, model=body.get('model'),
                     finish='stop', usage=dict(USAGE[kind]))
        if kind == 'sample':
            d = SAMPLES.get(lens, SAMPLES['default'])
            out['content'], out['cot'] = d['answer'], d['cot']
        elif kind == 'split':
            out['content'] = STEPS.get(lens, STEPS['default'])
        elif kind == 'merge':
            out['content'] = MERGE_JSON
        elif kind == 'judge':
            out['content'] = self._judge_json()
        elif kind == 'refine':
            out['content'] = REFINE_JSON
        else:
            out['content'] = '{}'
        return out

    def _judge_json(self):
        if self.judge_gaps is None:
            return _j(JUDGE)
        gaps = list(self.judge_gaps)
        if gaps:
            return _j({'thoroughness': JUDGE['thoroughness'], 'gaps': gaps,
                       'verdict': JUDGE['verdict']})
        return _j({'thoroughness': 9, 'gaps': [],
                   'verdict': 'Complete: the graph covers the reasoning.'})


class StubEntropy(Provider):
    """One bit per call, as heads/tails. Canned bitstrings first, then
    deterministic PRNG bits — so runs are reproducible forever."""

    name = 'stub-entropy'

    def __init__(self, canned=('0101101010011101',), seed=7, **defaults):
        super().__init__(**defaults)
        self._canned = ''.join(canned)
        self._rng = random.Random(seed)
        self._i = 0
        self.calls = []

    def call(self, **kw):
        body = self.build(**kw)
        i = self._i
        self._i += 1
        bit = self._canned[i] if i < len(self._canned) else str(self._rng.getrandbits(1))
        output = 'heads' if bit == '1' else 'tails'
        self.calls.append({'i': len(self.calls), 'shots': (body.get('params') or {}).get('shots')})
        return Result(status=200, ms=1 + self._rng.randrange(5), request=body,
                      content=_j({'result': {'output': output}}),
                      data={'result': {'output': output}})


class StubScorer(Provider):
    """Canned per-question answers for the wire stage's scorer questions."""

    name = 'stub-scorer'

    def __init__(self, seed=0, answers=None, **defaults):
        super().__init__(**defaults)
        self._rng = random.Random(seed)
        self.canned = dict(SCORER_ANSWERS)
        if answers:
            self.canned.update(answers)
        self.calls = []

    def call(self, **kw):
        body = self.build(**kw)
        questions = body.get('questions') or {}
        answers = {}
        for k in questions:
            if k in self.canned:
                answers[k] = self.canned[k]
            elif k.startswith('score_'):
                answers[k] = FALLBACK_SCORE
            else:
                answers[k] = {'choice': None, 'probabilities': {}}
        self.calls.append({'i': len(self.calls), 'questions': sorted(questions)})
        return Result(status=200, ms=10 + self._rng.randrange(40), request=body,
                      content=_j({'answers': answers}), data={'answers': answers})


class DetClock:
    """Deterministic wall clock: start + n*step per call. Exact in floats
    when step is a negative power of two, so receipt ms values are stable."""

    def __init__(self, start=1000000.0, step=0.25):
        self._start, self._step = start, step
        self._n = 0

    def __call__(self):
        t = self._start + self._n * self._step
        self._n += 1
        return t


def stub_config(seed=0, burn_first=True, judge_gaps=None, entropy_seed=7, clock=None):
    """The config dict cot_decompose expects, wired entirely with stubs."""
    return {
        'deepseek': StubLLM(seed=seed, burn_first=burn_first, judge_gaps=judge_gaps),
        'typesafe': StubScorer(seed=seed),
        'moth': StubEntropy(seed=entropy_seed),
        'clock': clock or DetClock(),
    }
