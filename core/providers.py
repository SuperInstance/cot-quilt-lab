# core/providers.py
"""Provider layer: any HTTP API becomes a normalized Result.

A Provider knows how to build a request body (build) and parse a response
body (parse); call() wires build -> http -> parse and wraps the outcome in
a Result. Retries, guards, JSON repair, and receipts compose on top.

Stdlib only. http() is module-level so tests (and pins) stub the transport
seam instead of the network; nothing here reads env or disk.
"""
import hashlib
import json
import time
import urllib.error
import urllib.request


class Result(dict):
    """Normalized call outcome. Plain dict so it serializes everywhere."""

    @property
    def ok(self):
        return int(self.get('status', -1)) == 200 and self.get('content') is not None

    def sha1(self, n=12):
        return hashlib.sha1((self.get('content') or '').encode()).hexdigest()[:n]


def http(url, key=None, body=None, timeout=180, method=None, headers=None):
    """One urllib request. Never raises: failures come back as status/-1 bodies."""
    t0 = time.time()
    m = method or ('POST' if body is not None else 'GET')
    data = json.dumps(body).encode() if body is not None else None
    h = {'Content-Type': 'application/json', 'User-Agent': 'cell/1.0'}
    if key:
        h['Authorization'] = f'Bearer {key}'
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=m, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {'status': 200, 'body': json.load(r), 'ms': int((time.time() - t0) * 1000)}
    except urllib.error.HTTPError as e:
        try:
            b = json.loads(e.read().decode() or '{}')
        except Exception:
            b = {}
        return {'status': e.code, 'body': b, 'ms': int((time.time() - t0) * 1000)}
    except Exception as e:
        return {'status': -1, 'body': {'error': str(e)}, 'ms': int((time.time() - t0) * 1000)}


class Provider:
    """Base. Subclass build() and parse(); call() is the wiring."""

    name = 'provider'

    def __init__(self, url, key=None, timeout=180, headers=None, **defaults):
        self.url, self.key, self.timeout = url, key, timeout
        self.headers = headers or {}
        self.defaults = defaults  # e.g. {'model': 'gpt-4o'}

    def build(self, **kw):
        """Defaults merged with caller kwargs; None values never reach the
        body, whichever side they came from (P1 pin: a default captured as
        None must not be sent as an explicit JSON null)."""
        body = {k: v for k, v in self.defaults.items() if v is not None}
        body.update({k: v for k, v in kw.items() if v is not None})
        return body

    def parse(self, body):
        return {'content': None}

    def call(self, **kw):
        body = self.build(**kw)
        r = http(self.url, self.key, body, timeout=self.timeout, headers=self.headers)
        out = Result(status=r['status'], ms=r['ms'], raw=r['body'], request=body)
        if r['status'] == 200:
            out.update(self.parse(r['body']))
        else:
            out['error'] = json.dumps(r['body'])[:400]
        return out


class OpenAICompat(Provider):
    """DeepSeek, OpenAI, Groq, Together, Fireworks, Ollama, LM Studio, vLLM, ..."""

    name = 'openai-compat'

    def parse(self, body):
        choices = body.get('choices') or [{}]
        ch = choices[0].get('message', {}) or {}
        return {
            'content': ch.get('content'),
            'cot': ch.get('reasoning_content') or ch.get('reasoning'),
            'model': body.get('model'),
            'finish': choices[0].get('finish_reason'),
            'usage': body.get('usage'),
        }


class RawJSON(Provider):
    """For non-chat APIs (typesafe systemone, mothquantum, custom)."""

    name = 'raw-json'

    def parse(self, body):
        return {'content': json.dumps(body), 'data': body}
