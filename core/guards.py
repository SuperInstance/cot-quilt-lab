# core/guards.py
"""Guards composing on anything with a Provider-shaped .call():

- call_with_guards: one call, plus the reasoner burn guard (finish=length
  with no content -> retry with a bigger budget).
- parse_json_loose: fenced or prose-wrapped JSON, or None.
- structured: system+user in, guaranteed JSON object or None out, with at
  most one repair retry.

Stdlib only; no provider imports (guards are generic over .call()).
"""
import json


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
    """Fenced ```json blocks, prose-wrapped objects; None when there is no JSON."""
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
