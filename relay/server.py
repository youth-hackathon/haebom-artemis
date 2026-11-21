"""해봄 artemis 서버: 화면 상태 하나를 받아 다음 동작 하나를 돌려준다 (docs/interface.md).

The app posts what plain artemis would read off a device itself: the accessibility tree as artemis-helper XML, a
screenshot, the goal and what became of the last action. This server runs google/artemis's own observation, prompt,
history compression, step summarizer and explorer (artemis_bridge.py) on it and returns ONE ordinary artemis action,
its target resolved the way artemis would tap it (element centre). It never carries the action out: the person does,
and the next request says what happened.

Models come from a profile (GUIDE_PROFILE, default `artemis`):
  artemis  artemis's own default models (config/artemis.jsonc), reached through Letsur instead of Google. The baseline.
  luna     gpt-6-luna as the planner, the other two as in `artemis`
  haiku    Claude Haiku 5.5 for all three roles (`anthropic:`, ANTHROPIC_API_KEY)
GUIDE_MODEL, GUIDE_SUMMARY_MODEL and GUIDE_EXPLORER_MODEL override one role as `<backend>:<model>`.
The Letsur key comes from relay/.env or the environment (LETSUR_API_KEY); the server starts without one and says so
in GET /health and in the answer to the first request.

Run:  docker compose up -d --build artemis        (or, from relay/:  uv sync && ARTEMIS_HOME=.. uv run python server.py)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import artemis_bridge as ab
import backends
import jev

HERE = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("GUIDE_LOG_DIR", HERE / "logs"))
# Screenshots are written next to the turn log for offline evaluation; a deployed server turns this off.
LOG_SHOTS = os.environ.get("GUIDE_LOG_SHOTS", "on") != "off"
HOST = os.environ.get("GUIDE_HOST", "127.0.0.1")
PORT = int(os.environ.get("GUIDE_PORT", "8765"))
# When set, every turn is written there: the request as it came in, the prompt exactly as it went to the model, what
# the model answered and how long it took. One file per session; those files are the workloads of the benchmark
# (bench/run.py), which sends each recorded prompt again as it is.
RECORD_DIR = os.environ.get("GUIDE_RECORD_DIR", "")
# role -> (`<backend>:<model>`, effort); backends.py. Effort is the thinking level (anthropic: effort, letsur: reasoning.effort).
# artemis: summarizer = artemis config flash.step_summarizer.model (gemini-3.5-flash-lite); explorer: artemis wants
# gemini-robotics-er-2-preview, which the gateway does not list, so the operator model stands in;
# letsur:gpt-6-luna: 19/20 element matches, median 5.18 s in runs/eval (cli:claude-sonnet-5-5: 19/20, 8.62 s).
# haiku: one model for all three roles; the planner at the model's default effort, the two helpers at low.
PROFILES = {
    "haiku": {"planner": ("anthropic:claude-haiku-5-5", "medium"),
              "summary": ("anthropic:claude-haiku-5-5", "low"),
              "explorer": ("anthropic:claude-haiku-5-5", "low")},
    # config/artemis.jsonc at the pinned commit: default gemini-3.8-flash with thinking_level medium (the Flash runner
    # uses the operator node, which inherits it); agent.flash.step_summarizer.model gemini-3.5-flash-lite; the flash
    # explorer delegates to object_detector, set to gemini-robotics-er-2-preview. Letsur does not serve that model
    # (404 on 2026-11-21), so the default model stands in for it.
    "artemis": {"planner": ("letsur:gemini-3.8-flash", "medium"),
                "summary": ("letsur:gemini-3.5-flash-lite", None),
                "explorer": ("letsur:gemini-3.8-flash", "medium")},
    "luna": {"planner": ("letsur:gpt-6-luna", None),
             "summary": ("letsur:gemini-3.5-flash-lite", None),
             "explorer": ("letsur:gemini-3.8-flash", None)},
}
DEFAULT_PROFILE = "artemis"
PROFILE = os.environ.get("GUIDE_PROFILE", DEFAULT_PROFILE)
if PROFILE not in PROFILES:
    raise SystemExit(f"GUIDE_PROFILE={PROFILE!r}: one of {sorted(PROFILES)}")
PLANNER_MODEL = os.environ.get("GUIDE_MODEL", PROFILES[PROFILE]["planner"][0])
SUMMARY_MODEL = os.environ.get("GUIDE_SUMMARY_MODEL", PROFILES[PROFILE]["summary"][0])
EXPLORER_MODEL = os.environ.get("GUIDE_EXPLORER_MODEL", PROFILES[PROFILE]["explorer"][0])
PLANNER_EFFORT, SUMMARY_EFFORT, EXPLORER_EFFORT = (PROFILES[PROFILE][r][1] for r in ("planner", "summary", "explorer"))
MAX_INNER_ROUNDS = 3  # explorer / invalid-index loops answered without a round trip to the phone

# The ways this server can answer a request. `baseline` is plain artemis (its models, its prompt, its way of keeping
# history) and is always there: a request that says "mode": "baseline" gets it whatever else this server has learned
# to do. A new way of answering is added here under its own name, next to the baseline and not in place of it; it may
# call the baseline for what it does not handle. GUIDE_MODE is the mode of a request that names none.
#   jev  TypeSafe Jev picks the next action among the screen's options (relay/jev.py). Jev also says whether the step
#        needs typing; such a step goes to the baseline (GUIDE_JEV_FALLBACK=baseline) or Jev answers it anyway (none)
MODES = ("baseline", "jev")
# In the jev mode some steps always go to the LLM, whatever Jev would choose. Jev sees one screen and no history: it
# cannot tell a goal reached from a screen to tap through, cannot say a task is impossible here, and repeats itself when
# a screen comes back. So the LLM (which has the history) answers
#   - the first step of a task (it also says when the task cannot be done on this phone; GUIDE_ENVIRONMENT_FILE)
#   - a step on a screen this session has already been on (Jev went round in a circle)
#   - after GUIDE_JEV_CHECK_EVERY steps in a row answered by Jev (is the task done by now?)
#   - a step for which Jev's answer is "done" (relay/jev.py): the LLM decides that the task is complete
JEV_LLM_START = os.environ.get("GUIDE_JEV_LLM_START", "on") != "off"
JEV_LLM_REPEAT = os.environ.get("GUIDE_JEV_LLM_REPEAT", "on") != "off"
JEV_CHECK_EVERY = int(os.environ.get("GUIDE_JEV_CHECK_EVERY", "3"))  # 0: never for this reason
# What the phone has and cannot do, for the LLM's prompt (a text file; empty or unset: nothing is added). The demo puts
# the Cuttlefish's installed apps there, so that a task needing an app that is not there is turned down at once.
ENVIRONMENT_FILE = os.environ.get("GUIDE_ENVIRONMENT_FILE", "")
ENVIRONMENT = ""
if ENVIRONMENT_FILE:
    try:
        ENVIRONMENT = Path(ENVIRONMENT_FILE).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SystemExit(f"GUIDE_ENVIRONMENT_FILE={ENVIRONMENT_FILE!r}: {exc}")


def screen_key(package: str, elements_text: str) -> str:
    """The same screen gives the same key: the app and the listed elements, without their positions."""
    return package + "\n" + re.sub(r"\s*\|\s*(Bounds|Point):.*", "", elements_text)


def with_environment(system: str) -> str:
    return system if not ENVIRONMENT else system.rstrip() + "\n\n---\n\n# 10. THIS PHONE\n" + ENVIRONMENT + "\n"
DEFAULT_MODE = os.environ.get("GUIDE_MODE", "baseline")
if DEFAULT_MODE not in MODES:
    raise SystemExit(f"GUIDE_MODE={DEFAULT_MODE!r}: one of {list(MODES)}")

_sessions: dict[str, dict] = {}
_lock = threading.Lock()
# artemis dispatches summaries asynchronously with concurrency 2; GUIDE_SUMMARY_WORKERS raises it for a server that is
# replayed with many sessions side by side
_summary_pool = ThreadPoolExecutor(max_workers=int(os.environ.get("GUIDE_SUMMARY_WORKERS", "2")))
_pending = 0  # background summaries not finished yet (GET /v1/usage reports it; the benchmark waits for 0)
_pending_by_session: dict[str, int] = {}  # the same, per session (GET /v1/usage?session=<id>)


def call_cli(model: str, system: str, content: list[dict] | str, schema: dict | None = None,
             effort: str | None = None) -> tuple[object, dict]:
    """One tool-less model call through backends.py (Claude, the Letsur gateway or the Claude Code CLI)."""
    return backends.call(model, system, content, schema, effort)


# ---------------------------------------------------------------- summarizer (async, artemis lens)

def _norm_target(action: dict, width: int, height: int) -> dict:
    """The red marker needs a 0-1000 point; an index target is drawn at the center it resolved to."""
    a = dict(action)
    p = action.get("target_point_px")
    if p and width and height:
        a["target"] = [int(p[0] * 1000 / width), int(p[1] * 1000 / height)]
    return a


def _summarize(sid: str, turn: dict, post_b64: str | None, goal: str, recorded: bool = True) -> None:
    global _pending
    try:
        action = _norm_target(turn["resolved_action"], turn["width"], turn["height"])
        system, blocks = ab.summarizer_request(turn["step"], action, turn.get("outcome", ""),
                                               turn.get("screenshot_b64"), post_b64, goal, turn.get("memo", ""))
        text, meta = call_cli(SUMMARY_MODEL, system, blocks, effort=SUMMARY_EFFORT)
        with _lock:
            turn["summary"] = str(text).strip()
            turn["summary_meta"] = meta
        print(f"[relay] {sid} summary step {turn['step']} ({meta['latency_s']}s): {turn['summary'][:120]}", flush=True)
        if recorded:  # a replayed session is not a workload
            _record_lines(sid, [{"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "summary_step": turn["step"], "summary": turn["summary"],
                                 "latency_s": meta.get("latency_s")}])
        # recorded so offline replays (scripts/eval_planner.py) see the history the planner actually saw
        with _lock, open(LOG_DIR / f"{sid}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "summary_step": turn["step"],
                                 "summary": turn["summary"], "summary_latency_s": meta.get("latency_s")}, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 - a missing summary only keeps the image longer
        print(f"[relay] {sid} summary failed: {exc}", flush=True)
    finally:
        with _lock:
            _pending -= 1
            left = _pending_by_session.get(sid, 1) - 1
            if left > 0:
                _pending_by_session[sid] = left
            else:
                _pending_by_session.pop(sid, None)


# ---------------------------------------------------------------- explorer (flash tier)

def _explore(query: str, current: dict, elements: list[dict], helpers: list[dict] | None = None) -> str:
    found, unresolved = ab.explorer_prepass(query, elements)
    notes = [f"'{f['label']}' → [{f['index']}] {f['description']}" for f in found]
    if unresolved and current.get("screenshot_b64"):
        prompt = ab.explorer_pointing_prompt(unresolved)
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": current["screenshot_b64"]}},
                  {"type": "text", "text": prompt}]
        schema = {"type": "object", "properties": {"objects": {"type": "array", "items": {"type": "object", "properties": {
            "point": {"type": "array", "items": {"type": "integer"}}, "label": {"type": "string"}}, "required": ["point", "label"]}}},
            "required": ["objects"]}
        try:
            out, explorer_meta = call_cli(EXPLORER_MODEL, "You locate UI components on Android screenshots.", blocks, schema,
                                          effort=EXPLORER_EFFORT)
            if helpers is not None:
                helpers.append(explorer_meta)
            w, h = current["width"], current["height"]
            for obj in out.get("objects", []):
                y, x = obj["point"][0], obj["point"][1]  # artemis object_detector: [y, x] in 0-1000
                idx = max([e["index"] for e in elements] + [0]) + 1
                px, py = x * w // 1000, y * h // 1000
                elements.append({"index": idx, "center": [px, py], "text": obj["label"], "bounds": [px - 20, py - 20, px + 20, py + 20]})
                current["elements_text"] += f"\n[{idx}] Explorer: '{obj['label']}' | Point: [{x},{y}]"
                notes.append(f"'{obj['label']}' → [{idx}] located by vision at [{x},{y}]")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"vision locate failed: {exc}")
    elif unresolved:
        notes.append(f"not located (no screenshot): {', '.join(unresolved)}")
    return "; ".join(notes) or "nothing located"


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "ToolusGuideRelay/0.3-artemis"

    def log_message(self, fmt, *args):
        sys.stderr.write("[relay] " + (fmt % args) + "\n")

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "profile": PROFILE, "planner": PLANNER_MODEL, "summary": SUMMARY_MODEL,
                             "explorer": EXPLORER_MODEL, "recording": bool(RECORD_DIR),
                             "modes": list(MODES), "default_mode": DEFAULT_MODE,
                             # how the Letsur models are reached: "direct", "gateway" or "missing" (no key set)
                             "letsur": backends.letsur_route(),
                             # the jev mode: whether a Jev key is set ("direct" / "missing") and what answers a step
                             # Jev cannot
                             "jev": jev.route(), "jev_fallback": jev.FALLBACK})
        elif self.path.split("?", 1)[0] == "/v1/usage":
            # ?session=<id>: `pending` counts that session's background work only
            query = self.path.partition("?")[2]
            only = next((kv.partition("=")[2] for kv in query.split("&") if kv.startswith("session=")), None)
            with _lock:
                pending = _pending if only is None else _pending_by_session.get(only, 0)
            self._send(200, {"models": backends.usage_totals(), "pending": pending})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/guide/next":
            self._send(404, {"error": "not found"})
            return
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": f"bad request: {exc}"})
            return
        started = time.time()
        trace: dict = {}
        code, body = guide_next(req, trace)
        record(req, code, body, time.time() - started, trace)
        self._send(code, body)


# ---------------------------------------------------------------- recording (the benchmark's workloads)

_record_lock = threading.Lock()
_recorded_images: dict[str, set] = {}  # session file -> ids of the images already written to it


def _image_id(b64: str) -> str:
    return hashlib.sha1(b64.encode("ascii")).hexdigest()[:16]


def _refs(obj, images: dict):
    """A copy of a request or a prompt with each base64 image replaced by {"image": id}; the images go to `images`.

    A screenshot is in several prompts (the turn it was taken in and the next two). It is written to the file once.
    """
    if isinstance(obj, list):
        return [_refs(x, images) for x in obj]
    if not isinstance(obj, dict):
        return obj
    if obj.get("type") == "image" and isinstance(obj.get("source"), dict) and isinstance(obj["source"].get("data"), str):
        iid = _image_id(obj["source"]["data"])
        images[iid] = obj["source"]["data"]
        return {"type": "image", "image": iid}
    out = {}
    for k, v in obj.items():
        if k == "screenshot_jpeg_b64" and isinstance(v, str):
            iid = _image_id(v)
            images[iid] = v
            out[k] = {"image": iid}
        else:
            out[k] = _refs(v, images)
    return out


def _record_lines(sid: str, lines: list[dict], images: dict | None = None, fresh: bool = False) -> None:
    if not RECORD_DIR:
        return
    try:
        folder = Path(RECORD_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9_-]", "_", str(sid))[:60]
        with _record_lock:
            seen = _recorded_images.setdefault(name, set())
            if fresh:
                seen.clear()
            with open(folder / f"{name}.jsonl", "w" if fresh else "a", encoding="utf-8") as fh:
                for iid, data in (images or {}).items():
                    if iid not in seen:
                        seen.add(iid)
                        fh.write(json.dumps({"image": iid, "jpeg_b64": data}) + chr(10))
                for line in lines:
                    fh.write(json.dumps(line, ensure_ascii=False) + chr(10))
    except Exception as exc:  # noqa: BLE001 - a turn that was not recorded is still answered
        print(f"[relay] recording failed: {exc}", flush=True)


def record(req: dict, code: int, body: dict, latency_s: float, trace: dict) -> None:
    """One line per turn: the request as it came in, the prompt as it went to the model, the reply, the time it took.

    `prompt` is the model input of the turn's first planner call, word for word. `session` is this server's memory of
    the session at that moment (what the prompt was built from), `rounds` what the model calls of the turn returned.
    Replays are not recorded.
    """
    if not RECORD_DIR or "replay" in req:
        return
    images: dict = {}
    line = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "request": _refs(req, images), "status": code, "response": body,
            "latency_s": round(latency_s, 2)}
    if trace.get("prompt"):
        line["prompt"] = _refs(trace["prompt"], images)
    for key in ("rounds", "explorer", "session"):
        if key in trace:
            line[key] = trace[key]
    _record_lines(str(req.get("session_id", "default")), [line], images, fresh=req.get("event") == "start")


def _force_previous(session: dict, prev: dict) -> None:
    """Benchmark replay: the step before this request is the recorded one, and it worked, whatever this server answered.

    The recorded observation that comes next shows what followed the recorded action. This server's own answer to the
    previous request was never carried out, so leaving it in the memory would describe a step that did not happen.
    Everything after that is this server's own way of keeping a session: its memory, its summaries, what it learns.
    """
    reply = prev.get("reply") or {}
    action = reply.get("action") or {}
    turns = session["turns"]
    if turns and turns[-1].get("step") == prev.get("step"):
        turns[-1].update(action=action, resolved_action=action, memo=reply.get("memo", ""), explorer=list(prev.get("explorer") or []))
    else:  # this server did not answer that step (it failed): the recorded step takes its place
        turns.append({"step": prev.get("step"), "elapsed_s": int(prev.get("elapsed_s") or 0), "package": prev.get("package", ""),
                      "action": action, "resolved_action": action, "memo": reply.get("memo", ""),
                      "screenshot_b64": prev.get("screenshot_jpeg_b64"), "explorer": list(prev.get("explorer") or []),
                      "width": int(prev.get("width") or 1080), "height": int(prev.get("height") or 2400)})


def _snapshot(session: dict) -> dict:
    """The session memory a prompt was built from, with screenshots named by their id (the files hold them once)."""
    turns = []
    for t in session["turns"]:
        turn = {k: v for k, v in t.items() if k not in ("screenshot_b64", "summary_meta")}
        turn["image"] = _image_id(t["screenshot_b64"]) if t.get("screenshot_b64") else None
        turns.append(turn)
    return {"task": session["task"], "user_notes": list(session.get("user_notes", [])), "turns": turns}


def _with_notes(content: list[dict], notes: list[str]) -> list[dict]:
    """A recorded prompt plus what this turn's inner rounds added, placed where build_content puts it: at the end."""
    blocks = [dict(b) for b in content]
    for note in notes:
        line = f"ask_explorer result: {note}"
        if blocks and blocks[-1].get("type") == "text":
            blocks[-1]["text"] += chr(10) + line
        else:
            blocks.append({"type": "text", "text": line})
    return blocks


def guide_next(req: dict, trace: dict | None = None) -> tuple[int, dict]:
    """One artemis turn: an observation (helper XML + screenshot) and the goal in, one action out.

    This is all the artemis side knows. Who calls it and where it runs do not change the input or the output.

    Benchmark replay (bench/run.py) sends recorded requests again, in order, one session at a time:
    - `replay.previous` says what the step before this one was in the recording. It replaces this server's own answer
      in the session memory and counts as done. The memory itself is kept by this server in its own way.
    - `replay.prompt` (the other mode) is a recorded model input: the request is answered from it word for word and
      the session memory is neither read nor changed, so such requests can come in any order and all at once.
    - `replay.return_prompt` asks for the prompt this turn was answered from, to compare with the recorded one.
    """
    global _pending
    mode = str(req.get("mode") or DEFAULT_MODE)
    if mode not in MODES:
        return 400, {"error": f"unknown mode {mode!r}; this server has {list(MODES)}"}
    sid = req.get("session_id", "default")
    screen = req.get("screen", {})
    width, height = int(screen.get("width", 1080)), int(screen.get("height", 2400))
    try:
        elements_text, elements = ab.observe(screen.get("xml", ""), width, height)
    except Exception as exc:  # noqa: BLE001
        elements_text, elements = f"Not available due to hierarchy error: {exc}", []
    current = {
        "step": req.get("step"), "elapsed_s": int(req.get("elapsed_s", 0)), "event": req.get("event", ""),
        "event_detail": req.get("event_detail", ""), "screenshot_b64": screen.get("screenshot_jpeg_b64"),
        "screenshot_error": screen.get("screenshot_error"), "elements_text": elements_text,
        "width": width, "height": height, "explorer": [],
    }
    replay = req.get("replay") if isinstance(req.get("replay"), dict) else None
    given = (replay or {}).get("prompt")
    session = None
    if isinstance(given, dict):
        system, recorded = str(given.get("system", "")), list(given.get("content") or [])
    else:
        with _lock:
            session = _sessions.setdefault(sid, {"task": req.get("task", ""), "turns": [], "user_notes": []})
            if req.get("event") == "start":
                session.update(task=req.get("task", session["task"]), turns=[], user_notes=[], seen=[], jev_run=0)
            if req.get("event") == "user_message":
                session["user_notes"].append(req.get("event_detail", ""))
            forced = (replay or {}).get("previous")
            if forced and req.get("event") != "start":
                _force_previous(session, forced)
            if session["turns"]:
                prev = session["turns"][-1]
                prev["outcome"] = current["event"] + (f" ({current['event_detail']})" if current["event_detail"] else "")
                _pending += 1
                _pending_by_session[sid] = _pending_by_session.get(sid, 0) + 1
                _summary_pool.submit(_summarize, sid, prev, current["screenshot_b64"], session["task"], replay is None)
            goal = session["task"]
        system = with_environment(ab.system_prompt(goal))

    first_prompt = None
    metas: list[dict] = []    # planner calls of this turn
    helpers: list[dict] = []  # explorer calls of this turn
    rounds: list[dict] = []
    out = None
    jev_info = None  # mode jev: what Jev chose this turn and whether it was used
    decided = None   # mode jev: (out, action, resolved) when Jev's choice answers the request
    key = screen_key(screen.get("package", ""), elements_text)
    to_llm = ""  # mode jev: why this step goes to the LLM without asking Jev
    if mode == "jev" and session is not None:
        with _lock:
            if JEV_LLM_START and current["event"] == "start":
                to_llm = "first_step"
            elif JEV_LLM_REPEAT and key in session.get("seen", []):
                to_llm = "screen_seen_before"
            elif JEV_CHECK_EVERY and session.get("jev_run", 0) >= JEV_CHECK_EVERY:
                to_llm = "check_progress"
        if to_llm:
            jev_info = {"route": "llm", "route_reason": to_llm, "used": False}
    if mode == "jev" and not to_llm:
        if session is not None:
            with _lock:
                turns, goal = [dict(t) for t in session["turns"]], session["task"]
                if trace is not None:
                    trace.setdefault("session", _snapshot(session))
        else:  # --replay prompt: the recorded session memory stands in for this server's
            snap = (replay or {}).get("session") or {}
            turns, goal = list(snap.get("turns") or []), req.get("task") or snap.get("task", "")
        try:
            d = jev.decide(goal, turns, current, elements, screen.get("xml", ""))
        except Exception as exc:  # noqa: BLE001
            if jev.FALLBACK == "none":
                if session is not None:
                    _log(sid, req, current, None, {"error": str(exc), "mode": mode})
                return 502, {"error": str(exc)[:300]}
            jev_info = {"error": str(exc)[:300], "used": False}  # the baseline answers this step
        else:
            # who answers (relay/jev.py): a step Jev's answers do not cover goes to the LLM, and
            # GUIDE_JEV_FALLBACK=none lets Jev answer it anyway when it has an action
            jmeta = d["meta"]
            jev_info = {**d["info"], "route": d["route"], "route_reason": d["why"], "used": False, "meta": jmeta}
            rounds.append({"role": "jev", "model": jmeta["model"], "meta": jmeta, "output": d["info"]})
            if d["action"] and (d["route"] == "jev" or jev.FALLBACK == "none"):
                j_resolved = ab.resolve_target(d["action"], elements, width, height)
                if not j_resolved.get("target_error"):
                    decided = ({"memo": "", "explain": d["explain"], "action": d["action"]}, d["action"], j_resolved)
                    jev_info["used"] = True

    if decided is not None:
        out, action, resolved = decided
    else:
        for _ in range(MAX_INNER_ROUNDS):
            if session is None:
                content = _with_notes(recorded, current["explorer"])
            else:
                with _lock:
                    content = ab.build_content(session, current)
                    if trace is not None and "prompt" not in trace:
                        trace.update(prompt={"system": system, "content": content}, session=_snapshot(session))
                    if first_prompt is None:
                        first_prompt = content
            try:
                out, meta = call_cli(PLANNER_MODEL, system, content, ab.SCHEMA, effort=PLANNER_EFFORT)
            except Exception as exc:  # noqa: BLE001
                if session is not None:
                    _log(sid, req, current, None, {"error": str(exc)})
                return 502, {"error": str(exc)[:300]}
            metas.append(meta)
            rounds.append({"role": "planner", "model": PLANNER_MODEL, "effort": PLANNER_EFFORT, "output": out, "meta": meta})
            action = out.get("action") or {"name": "ask_user", "question": "다음에 무엇을 할지 알려 주세요."}
            if action.get("name") == "ask_explorer":
                before = len(helpers)
                result = _explore(action.get("query", ""), current, elements, helpers)
                current["explorer"].append(f"{action.get('query')!r}: {result}")
                rounds.append({"role": "explorer", "model": EXPLORER_MODEL, "query": action.get("query", ""), "result": result,
                               "meta": helpers[before] if len(helpers) > before else None})
                continue
            resolved = ab.resolve_target(action, elements, width, height)
            if resolved.get("target_error"):
                current["explorer"].append(f"your {action['name']} was not carried out: {resolved['target_error']}")
                continue
            break
        else:
            resolved = {"name": "ask_user", "question": "화면에서 안내할 곳을 찾지 못했어요. 어떻게 할지 알려 주세요."}
            action = resolved

    if session is not None:
        with _lock:
            session["turns"].append({
                "step": current["step"], "elapsed_s": current["elapsed_s"], "package": screen.get("package", ""),
                "action": action, "resolved_action": resolved, "memo": out.get("memo", "") if out else "",
                "screenshot_b64": current["screenshot_b64"], "explorer": current["explorer"],
                "width": width, "height": height,
            })
            session.setdefault("seen", []).append(key)
            session["jev_run"] = session.get("jev_run", 0) + 1 if (jev_info or {}).get("used") else 0
    used = [backends.tokens(m) for m in metas + helpers]
    meta = {"rounds": len(metas), "latency_s": round(sum(m["latency_s"] for m in metas + helpers), 2),
            "cost_usd": round(sum((m.get("cost_usd") or 0) for m in metas), 5), "elements": len(elements),
            # what answering this request cost by itself (planner and explorer calls); the summaries made in the
            # background are in GET /v1/usage
            "model_calls": len(metas) + len(helpers),
            "input_tokens": sum(u["input_tokens"] for u in used), "cached_input_tokens": sum(u["cached_input_tokens"] for u in used),
            "output_tokens": sum(u["output_tokens"] for u in used), "cost": round(sum(u["cost"] for u in used), 8),
            "cost_currency": next((u["cost_currency"] for u in used if u["cost_currency"]), None),
            "source": "model", "mode": mode}
    if jev_info is not None:
        # Jev's call is counted in model_calls and latency_s; its tokens and dollars stay apart from the LLM's
        # (input_tokens, cost in Letsur units) so the two modes' LLM use can be compared as it is
        jm = jev_info.get("meta")
        if jm:
            meta["latency_s"] = round(meta["latency_s"] + jm["latency_s"], 2)
            meta["model_calls"] += 1
        meta["decider"] = "jev" if jev_info["used"] else "llm"
        meta["jev"] = {k: v for k, v in jev_info.items() if k != "meta"}
        if jm:
            meta["jev"].update(latency_s=jm["latency_s"], options=jm["options"], cost_usd=jm.get("cost_usd"),
                               input_tokens=(jm.get("usage") or {}).get("input_tokens"), served_by=jm.get("served_by"))
    if trace is not None:
        trace.update(rounds=rounds, explorer=list(current["explorer"]))
    if (replay or {}).get("return_prompt") and first_prompt is not None:
        meta["prompt"] = _refs(first_prompt, {})  # the text as it is, each screenshot as its id
    if session is not None:
        _log(sid, req, current, out, meta)
    print(f"[relay] {sid} step {current['step']} {current['event']} -> {ab.describe_action(action)} "
          f"[{meta['rounds']} round(s), {meta['latency_s']}s{', replay' if session is None else ''}]", flush=True)
    return 200, {"memo": out.get("memo", "") if out else "", "explain": out.get("explain", "") if out else "",
                 "action": resolved, "meta": meta}


def _log(sid, req, current, out, meta):
    LOG_DIR.mkdir(exist_ok=True)
    shot = current.get("screenshot_b64")
    shot_file = None
    if shot and LOG_SHOTS:
        p = LOG_DIR / sid / f"step{int(current.get('step') or 0):02d}.jpg"
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(base64.b64decode(shot))
        shot_file = str(p.relative_to(LOG_DIR))
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "session": sid, "task": req.get("task"), "step": current.get("step"),
           "event": current.get("event"), "event_detail": current.get("event_detail"),
           "package": req.get("screen", {}).get("package"), "elements_text": current.get("elements_text"),
           "explorer": current.get("explorer"), "screenshot_file": shot_file, "reply": out, "meta": meta}
    with open(LOG_DIR / f"{sid}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> None:
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[relay] listening on {HOST}:{PORT} profile={PROFILE} planner={PLANNER_MODEL} summary={SUMMARY_MODEL} "
          f"explorer={EXPLORER_MODEL} modes={list(MODES)} default_mode={DEFAULT_MODE} letsur={backends.letsur_route()} "
          f"jev={jev.route()} jev_fallback={jev.FALLBACK} jev_route_p={jev.ROUTE_P}", flush=True)
    if "letsur:" in PLANNER_MODEL and backends.letsur_route() == "missing":
        print("[relay] " + backends.NO_KEY, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
