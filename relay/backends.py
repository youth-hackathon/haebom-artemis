"""Model backends for the relay: Claude (Anthropic Messages API), the Letsur AI gateway (Responses API) or the
local Claude Code CLI.

A model is named `<backend>:<model code>[@effort]`, e.g. `letsur:gemini-3.8-flash`, `anthropic:claude-haiku-5-5@high` or
`cli:claude-sonnet-5-5`. All take the same input: a system prompt and content blocks in Anthropic shape
({"type":"text"} / {"type":"image","source":{base64}}), and return (output, meta).

Keys are read from the environment or relay/.env and are never logged or returned. The baseline needs one of:
  LETSUR_API_KEY                           your own Letsur key; calls go straight to https://gw.letsur.ai/v1
  AI_GATEWAY_TOKEN + LETSUR_GATEWAY_URL    a Cloudflare AI Gateway route that holds the Letsur key
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import anthropic

HERE = Path(__file__).resolve().parent
WORK_DIR = HERE / "work"
CLI = os.environ.get("GUIDE_CLI", "claude")
TIMEOUT_S = int(os.environ.get("GUIDE_CLI_TIMEOUT", "120"))
LETSUR_URL = "https://gw.letsur.ai/v1/responses"
# Optional: a Cloudflare AI Gateway route that holds the Letsur key, ending in /v1/responses. There is no default:
# the address belongs to whoever runs the gateway and comes from the environment or relay/.env.
# Room for adaptive thinking plus the reply; a non-streaming request stays well inside the SDK timeout.
CLAUDE_MAX_TOKENS = 16000


def _env_key(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}=") and line.split("=", 1)[1].strip():
                return line.split("=", 1)[1].strip()
    raise RuntimeError(f"{name} is not set (relay/.env; see relay/.env.example)")


def _letsur_key() -> str:
    return _env_key("LETSUR_API_KEY")


NO_KEY = ("no Letsur key: put LETSUR_API_KEY=<your key> in relay/.env (copy relay/.env.example) and start the server again. "
          "See README.md, \"키 넣기\".")


def _letsur_endpoint() -> tuple[str, dict]:
    """Where a Letsur call goes and how it is authorised: straight to Letsur with LETSUR_API_KEY, else through a
    gateway route (LETSUR_GATEWAY_URL) with its run token (AI_GATEWAY_TOKEN). Neither value is logged."""
    try:
        return LETSUR_URL, {"Authorization": "Bearer " + _letsur_key()}
    except RuntimeError:
        try:
            return _env_key("LETSUR_GATEWAY_URL"), {"cf-aig-authorization": "Bearer " + _env_key("AI_GATEWAY_TOKEN")}
        except RuntimeError:
            raise RuntimeError(NO_KEY) from None


def letsur_route() -> str:
    """How Letsur is reached from here: "direct", "gateway" or "missing". Says nothing about the key itself."""
    try:
        url, _ = _letsur_endpoint()
    except RuntimeError:
        return "missing"
    return "direct" if url == LETSUR_URL else "gateway"


def call(model: str, system: str, content: list[dict] | str, schema: dict | None = None,
         effort: str | None = None) -> tuple[object, dict]:
    backend, _, code = model.partition(":")
    if not code:
        backend, code = "cli", model
    code, _, at_effort = code.partition("@")  # `anthropic:claude-haiku-5-5@high` sets the effort in the name
    effort = at_effort or effort
    if backend not in ("anthropic", "cli", "letsur"):
        raise ValueError(f"unknown backend {backend!r}")
    for attempt in range(1 + TRANSIENT_RETRIES):
        try:
            if backend == "anthropic":
                out, meta = _call_anthropic(code, system, content, schema, effort)
            elif backend == "cli":
                out, meta = _call_cli(code, system, content, schema)
            else:
                out, meta = _call_letsur(code, system, content, schema, effort)
            break
        except Exception as exc:  # noqa: BLE001
            # A dropped connection or an overloaded provider is asked once more; anything else is the caller's to see.
            if attempt == TRANSIENT_RETRIES or NO_KEY in str(exc) or not _transient(exc):
                raise
            time.sleep(TRANSIENT_WAIT_S)
    meta["attempts"] = attempt + 1
    _count(meta)
    return out, meta


TRANSIENT_RETRIES = 1
TRANSIENT_WAIT_S = 2.0


def _transient(exc: Exception) -> bool:
    """A failure that says nothing about the request itself: the connection dropped, timed out, or the provider answered
    429 or 5xx (seen from the gateway: "Remote end closed connection without response")."""
    if isinstance(exc, (ConnectionError, TimeoutError, urllib.error.URLError, http.client.HTTPException)):
        return True
    text = str(exc)
    return any(mark in text for mark in ("connection error", " 429:", " 500:", " 502:", " 503:", " 504:", " 529:"))


# ---------------------------------------------------------------- usage (every model call goes through call())

_usage_lock = threading.Lock()
_usage: dict[str, dict] = {}


def tokens(meta: dict) -> dict:
    """Token counts and the provider's own cost estimate of one call, in one shape for every backend.

    Letsur reports `estimated_cost` in its own unit; the CLI reports dollars; the Messages API reports tokens only."""
    u = meta.get("usage") or {}
    details = u.get("input_tokens_details") or {}
    cost, currency = None, None
    if meta.get("cost_unit") is not None:
        cost, currency = meta["cost_unit"], "unit"
    elif meta.get("cost_usd") is not None:
        cost, currency = meta["cost_usd"], "usd"
    try:
        cost = float(cost) if cost is not None else 0.0
    except (TypeError, ValueError):
        cost = 0.0
    return {"input_tokens": int(u.get("input_tokens") or 0),
            "cached_input_tokens": int(details.get("cached_tokens") or u.get("cache_read_input_tokens") or 0),
            "output_tokens": int(u.get("output_tokens") or 0), "cost": cost, "cost_currency": currency}


def _count(meta: dict) -> None:
    t = tokens(meta)
    with _usage_lock:
        total = _usage.setdefault(meta.get("model", "?"), {"calls": 0, "input_tokens": 0, "cached_input_tokens": 0,
                                                           "output_tokens": 0, "cost": 0.0, "cost_currency": None,
                                                           "latency_s": 0.0, "gateway_cache_hits": 0})
        total["calls"] += 1
        for k in ("input_tokens", "cached_input_tokens", "output_tokens", "cost"):
            total[k] += t[k]
        total["cost_currency"] = t["cost_currency"] or total["cost_currency"]
        total["latency_s"] += float(meta.get("latency_s") or 0)
        total["gateway_cache_hits"] += 1 if meta.get("gateway_cache") == "HIT" else 0


def usage_totals() -> dict:
    """Per model, since this process started: calls, tokens, the providers' cost estimates, time spent waiting."""
    with _usage_lock:
        return {m: {**t, "cost": round(t["cost"], 8), "latency_s": round(t["latency_s"], 2)} for m, t in _usage.items()}


def parse_json_reply(text: str) -> dict:
    """The JSON object between the first '{' and the last '}'."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0:
        raise RuntimeError(f"no JSON in model output: {text[:200]}")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------- Claude (Messages API)

# JSON-schema keywords structured outputs do not take (numeric, string and array constraints)
_UNSUPPORTED = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems")


def claude_schema(node):
    """A copy of a JSON schema in the form structured outputs take: every object closed with
    additionalProperties false, unsupported constraints dropped. Key order is kept (the phone's port compares)."""
    if isinstance(node, list):
        return [claude_schema(x) for x in node]
    if not isinstance(node, dict):
        return node
    out = {k: claude_schema(v) for k, v in node.items() if k not in _UNSUPPORTED}
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
    return out


def messages_params(model: str, system: str, content: list[dict] | str, schema: dict | None = None,
                    effort: str | None = None) -> dict:
    """The Messages request: system prompt, one user turn of the content blocks, effort and, for a schema, a JSON
    output format. Thinking is left at the model default (adaptive on Claude Haiku 5.5); effort sets how much."""
    params = {"model": model, "max_tokens": CLAUDE_MAX_TOKENS, "system": system,
              "messages": [{"role": "user", "content": _blocks(content)}]}
    output_config = {}
    if effort:
        output_config["effort"] = effort
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": claude_schema(schema)}
    if output_config:
        params["output_config"] = output_config
    return params


_claude: anthropic.Anthropic | None = None


def _claude_client() -> anthropic.Anthropic:
    global _claude
    if _claude is None:
        _claude = anthropic.Anthropic(api_key=_env_key("ANTHROPIC_API_KEY"), timeout=TIMEOUT_S)
    return _claude


def _call_anthropic(model: str, system: str, content, schema, effort):
    started = time.time()
    try:
        r = _claude_client().messages.create(**messages_params(model, system, content, schema, effort))
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"anthropic {e.status_code}: {str(e.message)[:300]}") from None
    except anthropic.APIConnectionError as e:
        raise RuntimeError(f"anthropic connection error: {e}") from None
    meta = {"model": f"anthropic:{model}", "latency_s": round(time.time() - started, 2),
            "usage": {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}}
    if r.stop_reason == "refusal":
        category = r.stop_details.category if r.stop_details else None
        raise RuntimeError(f"model declined (refusal, {category})")
    if r.stop_reason == "max_tokens":
        raise RuntimeError(f"reply cut off at max_tokens={CLAUDE_MAX_TOKENS}")
    text = "".join(b.text for b in r.content if b.type == "text")  # thinking blocks come first; skip them
    return (parse_json_reply(text) if schema else text), meta


def _blocks(content: list[dict] | str) -> list[dict]:
    return [{"type": "text", "text": content}] if isinstance(content, str) else content


def _call_cli(model: str, system: str, content, schema):
    WORK_DIR.mkdir(exist_ok=True)
    args = [CLI, "-p", "--model", model, "--tools", "", "--no-session-persistence", "--system-prompt", system,
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    if schema:
        args += ["--json-schema", json.dumps(schema)]
    stdin = json.dumps({"type": "user", "message": {"role": "user", "content": _blocks(content)}}) + "\n"
    started = time.time()
    proc = subprocess.run(args, input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_S, cwd=str(WORK_DIR))
    meta = {"model": f"cli:{model}", "latency_s": round(time.time() - started, 2)}
    results = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "result":
                results.append(obj)
    if not results:
        raise RuntimeError(f"cli exit {proc.returncode}: {proc.stderr[:300] or proc.stdout[-300:]}")
    env = results[-1]
    meta["cost_usd"] = env.get("total_cost_usd")
    if schema:
        out = env.get("structured_output")
        if not isinstance(out, dict):
            raise RuntimeError(f"no structured output: {str(env.get('result'))[:300]}")
        return out, meta
    return env.get("result") or "", meta


def _call_letsur(model: str, system: str, content, schema, effort=None):
    parts = []
    for b in _blocks(content):
        if b["type"] == "text":
            parts.append({"type": "input_text", "text": b["text"]})
        elif b["type"] == "image":
            src = b["source"]
            parts.append({"type": "input_image", "image_url": f"data:{src.get('media_type', 'image/jpeg')};base64,{src['data']}"})
    body = {"model": model, "instructions": system, "input": [{"role": "user", "content": parts}]}
    if schema:
        body["text"] = {"format": {"type": "json_schema", "name": "reply", "schema": schema, "strict": False}}
    if effort:
        body["reasoning"] = {"effort": effort}  # artemis config `thinking_level`
    url, auth = _letsur_endpoint()
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        **auth, "Content-Type": "application/json", "User-Agent": "toolus-guide-poc/0.3"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            gateway_cache = r.headers.get("cf-aig-cache-status")  # HIT when the AI Gateway answered from its cache
            j = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"letsur {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from None
    meta = {"model": f"letsur:{model}", "latency_s": round(time.time() - started, 2),
            "cost_unit": (j.get("estimated_cost") or {}).get("amount"), "usage": j.get("usage"), "gateway_cache": gateway_cache}
    text = "".join(c.get("text", "") for o in j.get("output", []) if o.get("type") == "message"
                   for c in o.get("content", []) if c.get("type") in ("output_text", "text"))
    if not schema:
        return text, meta
    return parse_json_reply(text), meta
