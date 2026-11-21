"""artemis 서버 벤치마크: 기록해 둔 에뮬레이터 상태를 서버에 차례로 내밀고 지연시간, 비용, 정답 여부를 잰다.

폰도 에뮬레이터도 앱도 필요 없다. 파이썬과, 떠 있는 서버 주소만 있으면 된다. 이 도구가 가상 에뮬레이터 노릇을 한다.

    python bench/run.py                                    # bench/workloads/의 세션을 4개씩 동시에
    python bench/run.py --parallel 1                       # 세션을 하나씩 (지연시간을 정확히 볼 때)
    python bench/run.py --repeat 10 --label plan-cache     # 같은 워크로드를 10번. 회차별로 나눠 보여 준다
    python bench/run.py a.jsonl b.jsonl --parallel 1       # a를 끝낸 뒤 b (같은 작업을 다른 날, 다른 기기에서 기록한 것)
    python bench/run.py --against runs/bench-baseline      # 앞서 잰 결과와 나란히
    python bench/run.py --detail                           # 틀린 단계를 하나씩
    python bench/run.py --label baseline --resume          # 끊기거나 오류가 난 측정을 이어서
    python bench/run.py --check-context                    # 서버가 쌓은 이력이 실제 실행 때 모델에 들어간 것과 같은지 대조한다
    python bench/run.py --show-prompt bluetooth:3          # 그 단계에서 실제로 모델에 들어간 프롬프트를 보여 준다 (서버 필요 없음)
    python bench/run.py --mode baseline --label baseline   # 서버의 베이스라인 방식을 잰다
    python bench/run.py --mode cache --against runs/bench-baseline   # 같은 서버의 새 방식을 재서 나란히 본다
    python bench/run.py --replay prompt                    # 기록된 프롬프트를 그대로 넣는다. 모델 호출만 잰다 (아래)

워크로드는 실제 실행 하나를 서버가 기록한 파일이다(bench/workloads/*.jsonl). 서버를 GUIDE_RECORD_DIR을 주고 띄운 뒤
폰에서 한 번 따라 하면 생긴다. 단계마다 한 줄이고, 그 줄에 이것이 들어 있다.
    request    그 순간의 에뮬레이터 상태. 앱이 보낸 요청 그대로다 (화면 XML, 스크린샷, 직전 동작의 결과)
    response   그때 서버가 돌려준 답. 다음 줄의 event가 met이면 사용자가 해낸 것이고, 이것이 정답이다
    prompt     그 단계에서 실제로 모델에 들어간 입력 (시스템 글, 그때까지의 이력, 스크린샷들)
    session    그 프롬프트를 만든 서버의 세션 기억,  rounds  그 단계에서 모델을 부른 기록
스크린샷은 파일에 한 번씩만 들어 있고({"image": id, "jpeg_b64": ...}), 요청과 프롬프트는 id로 가리킨다.

서버는 답하는 방식을 여럿 가질 수 있고(docs/interface.md의 mode), baseline은 항상 있다. --mode로 고른 방식을 모든 요청에
적어 보낸다. 주지 않으면 서버의 기본 방식을 잰다. 같은 서버를 두 방식으로 차례로 재면 고치기 전과 후를 한자리에서 볼 수 있다.

재는 방법 (기본, --replay session)
  1. 세션마다 기록된 상태를 순서대로 서버에 보낸다. 세션 id만 새것으로 바꾼다. 세션끼리는 무관하므로 여러 세션을 동시에 돌린다.
  2. 서버의 답은 실행하지 않는다. 걸린 시간을 재고, 기록된 동작과 같은지 채점하고, 기록된 다음 상태로 넘어간다.
  3. 다음 요청에는 "직전 단계는 기록된 이 동작이었고, 해냈다"를 같이 보낸다(replay.previous). 서버는 자기가 낸 답 대신 그것을
     기억에 넣는다. 그 뒤의 이력 쌓기(요약, 줄이기, 배워 두기)는 서버가 자기 방식으로 한다. 재려는 것이 그 방식이다.
  4. 다음 단계를 보내기 전에 그 세션의 뒷일(요약)이 끝나기를 기다린다. 사람은 단계 사이에 몇 초를 쓰기 때문이다.

--replay prompt는 기록된 프롬프트를 그대로 모델에 넣게 한다(replay.prompt). 서버의 이력 쌓기는 건너뛰므로 같은 입력에 모델이나
프로바이더만 바꿔 비교할 때 쓴다. 단계끼리 서로 기대지 않아 모든 단계를 한꺼번에 보낸다.

채점하는 단계는 기록에서 사용자가 해낸 것이 확인된 단계다(다음 요청의 event가 met, 또는 마지막의 완료 보고).
정답은 기록된 동작과 종류가 같고 같은 요소를 가리키는 것이다. 가리킨 점이 기록된 요소 안에 있거나, 요소의 이름이 같으면
같은 요소로 본다(같은 이름으로 맞은 수는 따로 보여 준다). 요소 없이 좌표로 가리킨 경우는 화면 긴 변의 3% 안이면 같다.
다른 길로도 갈 수 있는 화면에서는 맞는 답이 틀림으로 나온다. 그래서 정답률은 고치기 전과 후의 차이로 본다.
서버가 답하지 못한 단계는 두 번 더 보내 보고, 그래도 안 되면 틀림이 아니라 오류로 센다. 응답이 앱과의 규약
(docs/interface.md)을 어기면 규약 위반으로 센다. 오류나 규약 위반이 있으면 종료 코드 2다.

서버가 지킬 것. 안이 어떻게 바뀌어도 이것만 지키면 이 벤치마크로 잴 수 있다.
  POST /v1/guide/next   요청과 응답의 형식 (docs/interface.md)
  replay.previous       요청에 있으면 그 세션의 직전 단계 기억을 그것으로 바꾼다. replay가 있는 요청은 기록하지 않는다
  응답의 meta           그 요청에 든 model_calls, input_tokens, output_tokens, cost. 저장해 둔 답으로 대답했으면 source에 적는다
  GET /v1/usage         모델 사용량의 누계. ?session=<id>를 주면 pending이 그 세션의 안 끝난 뒷일 수다
  mode                  요청에 있으면 그 방식으로 답하고 응답의 meta.mode에 적는다. baseline은 항상 있다

캐시를 재려면 같은 서버에 --repeat로 여러 번 넣는다. 1회차는 처음 보는 작업이고 2회차부터는 본 적 있는 작업이다.
처음부터 다시 재려면 서버를 다시 띄운다. 동시에 많이 보내면 모델 쪽이 밀려 지연시간이 늘 수 있으니, 지연시간을 비교할 때는
같은 --parallel로 잰 것끼리 본다.
"""

from __future__ import annotations

import argparse
import copy
import csv
import difflib
import json
import re
import statistics
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKLOADS = HERE / "workloads"
TARGET_ACTIONS = ("click", "long_press", "input_text")
RETRIES = 2        # a request the server could not answer is sent again this many times
RETRY_WAIT_S = 5.0
SETTLE_MAX_S = 20.0      # how long a step waits for the server's background work on the step before it
APP_HEALTH_WAIT_S = 1.5  # PlannerClient.health
ACTIONS = ("click", "long_press", "input_text", "swipe", "press_key", "manage_app", "wait_for_delay", "ask_user", "report_task_status")
SUMMARY = re.compile(r"--- Historical Visual Transition ---\n.*?(?=\nYour |\n# |\Z)", re.S)


# ---------------------------------------------------------------- 워크로드

def load(path: Path) -> dict:
    """{"name", "task", "images", "turns"}: the turns the server answered, in order, as recorded."""
    images: dict[str, str] = {}
    turns = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if "image" in rec and "jpeg_b64" in rec:
            images[rec["image"]] = rec["jpeg_b64"]
        elif "request" in rec and rec.get("status", 200) == 200 and isinstance((rec.get("response") or {}).get("action"), dict):
            turns.append(rec)
    task = next((t["request"].get("task") for t in turns if t["request"].get("task")), "")
    return {"name": Path(path).stem, "task": str(task).splitlines()[0][:60] if task else "", "images": images, "turns": turns}


def completed(action: dict) -> bool:
    return action.get("name") == "report_task_status" and action.get("status") == "completed"


def confirmed(turns: list[dict], i: int) -> bool:
    """Whether the recorded action of turn i is known to be right: the person did it, or it is the final "done"."""
    if i + 1 < len(turns):
        return turns[i + 1]["request"].get("event") == "met"
    return completed(turns[i]["response"]["action"])


def _restore(obj, images: dict):
    """The recorded request or prompt with its images put back where their ids stand."""
    if isinstance(obj, list):
        return [_restore(x, images) for x in obj]
    if not isinstance(obj, dict):
        return obj
    if obj.get("type") == "image" and "image" in obj:
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": images[obj["image"]]}}
    out = {}
    for k, v in obj.items():
        out[k] = images[v["image"]] if k == "screenshot_jpeg_b64" and isinstance(v, dict) and "image" in v else _restore(v, images)
    return out


SERVER_MODE: str | None = None  # --mode: the server's way of answering that is measured (None: its default)


def _mode(req: dict) -> dict:
    if SERVER_MODE:
        req["mode"] = SERVER_MODE
    else:
        req.pop("mode", None)  # a recorded request may name the mode it was made with; the server's default is measured
    return req


def state_request(workload: dict, i: int, sid: str, resend_shot: bool, return_prompt: bool) -> dict:
    """The recorded state of turn i, with what the step before it was in the recording (it counts as done)."""
    turn = workload["turns"][i]
    req = _mode(_restore(copy.deepcopy(turn["request"]), workload["images"]))
    req["session_id"] = sid
    req["replay"] = {}
    if i > 0:
        prev = workload["turns"][i - 1]
        p = _restore(copy.deepcopy(prev["request"]), workload["images"])
        scr = p.get("screen") or {}
        req["replay"]["previous"] = {
            "step": p.get("step"), "elapsed_s": p.get("elapsed_s"), "package": scr.get("package", ""),
            "width": scr.get("width"), "height": scr.get("height"), "explorer": prev.get("explorer") or [],
            "reply": {k: prev["response"].get(k) for k in ("memo", "explain", "action")}}
        if resend_shot:  # the server did not answer that step, so it never took its screenshot in
            req["replay"]["previous"]["screenshot_jpeg_b64"] = scr.get("screenshot_jpeg_b64")
    if return_prompt:
        req["replay"]["return_prompt"] = True
    return req


def prompt_request(workload: dict, i: int) -> dict:
    """Turn i with the model input recorded for it: the server answers from that, word for word."""
    turn = workload["turns"][i]
    req = _mode(_restore(copy.deepcopy(turn["request"]), workload["images"]))
    req["session_id"] = f"bench-{uuid.uuid4().hex[:10]}"
    req["replay"] = {"prompt": _restore(turn["prompt"], workload["images"]), "session": turn.get("session")}
    return req


# ---------------------------------------------------------------- 채점

def _norm(s) -> str:
    return " ".join(str(s or "").split()).casefold()


def same(got: dict, want: dict, screen: dict) -> str:
    """How the answered action matches the recorded one: "위치" (same place), "이름" (same element name), or "" (not the same)."""
    if got.get("name") != want.get("name"):
        return ""
    name = want.get("name")
    if name in TARGET_ACTIONS:
        if name == "input_text" and got.get("text") != want.get("text"):
            return ""
        gp, wp = got.get("target_point_px"), want.get("target_point_px")
        for point, box in ((gp, want.get("target_bounds_px")), (wp, got.get("target_bounds_px"))):
            if point and box and box[0] <= point[0] < box[2] and box[1] <= point[1] < box[3]:
                return "위치"
        if _norm(got.get("target_text")) and _norm(got.get("target_text")) == _norm(want.get("target_text")):
            return "이름"
        if gp and wp and not want.get("target_bounds_px") and not got.get("target_bounds_px"):
            # both pointed at a spot no listed element names: the app rings a box of 3 % of the long side around it
            reach = max(int(screen.get("width") or 1080), int(screen.get("height") or 2400)) * 3 / 100
            if abs(gp[0] - wp[0]) <= reach and abs(gp[1] - wp[1]) <= reach:
                return "위치"
        return ""
    if name == "report_task_status":
        return "위치" if got.get("status") == want.get("status") else ""
    return "위치" if all(got.get(k) == want.get(k) for k in ("key", "direction", "app_name")) else ""


def violations(body: dict, screen: dict) -> list[str]:
    """Where a reply breaks docs/interface.md, checked the way the app reads it (PlannerClient.kt, GuideCatalog.kt)."""
    out = []
    if not isinstance(body.get("explain"), str):
        out.append("explain이 글자가 아니다")
    a = body.get("action")
    if not isinstance(a, dict) or not isinstance(a.get("name"), str):
        return out + ["action에 name이 없다"]
    name = a["name"]
    if name not in ACTIONS:
        return out + [f"앱이 모르는 동작이다: {name}"]

    def text(key: str) -> bool:
        return isinstance(a.get(key), str) and a[key].strip() != ""

    if name in ("click", "long_press"):
        p = a.get("target_point_px")
        ok = isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in p)
        if not ok:
            t = a.get("target")  # the app also takes a 0-1000 point in `target`
            if not (isinstance(t, list) and len(t) == 2 and all(isinstance(v, (int, float)) for v in t)):
                out.append(f"{name}에 target_point_px가 없다")
        elif not (0 <= p[0] < int(screen.get("width") or 1080) and 0 <= p[1] < int(screen.get("height") or 2400)):
            out.append(f"{name}의 target_point_px {p}가 화면 밖이다")
    elif name == "input_text" and not text("text"):
        out.append("input_text에 text가 없다")
    elif name == "swipe" and a.get("direction") not in ("up", "down", "left", "right"):
        out.append(f"swipe의 direction이 {a.get('direction')!r}이다")
    elif name == "press_key" and str(a.get("key", "")).upper() not in ("BACK", "HOME", "APP_SWITCH", "ENTER"):
        out.append(f"press_key의 key가 {a.get('key')!r}이다")
    elif name == "manage_app" and not text("app_name"):
        out.append("manage_app에 app_name이 없다")
    elif name == "report_task_status" and a.get("status") not in ("completed", "failed"):
        out.append(f"report_task_status의 status가 {a.get('status')!r}이다")
    return out


def label(action: dict) -> str:
    what = action.get("target_text") or action.get("key") or action.get("direction") or action.get("app_name") or action.get("status") or ""
    return " ".join(f"{action.get('name', '?')} {what}".split())


def context_match(recorded: list[dict] | None, built: list[dict] | None) -> tuple[str, str]:
    """How the prompt the server built in the replay compares with the one that really went to the model.

    Returns ("같음" | "요약 글만 다름" | "다름" | "", a short diff). Summaries are written anew by the server's
    summarizer each time, so their wording differs between two runs of the same session; anything else should not.
    """
    if not recorded or not built:
        return "", ""

    def flat(blocks: list[dict], mask: bool) -> str:
        parts = []
        for b in blocks:
            if b.get("type") == "text":
                parts.append(SUMMARY.sub("--- Historical Visual Transition ---\n<요약>", b.get("text", "")) if mask else b.get("text", ""))
            else:
                parts.append(f"<image {b.get('image')}>")
        return "\n".join(parts)

    if flat(recorded, False) == flat(built, False):
        return "같음", ""
    a, b = flat(recorded, True), flat(built, True)
    if a == b:
        return "요약 글만 다름", ""
    diff = [ln for ln in difflib.unified_diff(a.splitlines(), b.splitlines(), "실제 실행", "재생", lineterm="", n=0) if not ln.startswith(("---", "+++", "@@"))]
    return "다름", " | ".join(d[:110] for d in diff[:4])


# ---------------------------------------------------------------- 서버와 주고받기

def http(method: str, url: str, body: dict | None = None, timeout: float = 240.0) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        with e:
            raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"error": raw[:300]}


def usage(base: str) -> dict | None:
    """The server's own count of model calls so far, when it reports one (GET /v1/usage)."""
    try:
        code, body = http("GET", base + "/v1/usage", timeout=10)
    except (urllib.error.URLError, OSError):
        return None
    return body["models"] if code == 200 and isinstance(body.get("models"), dict) else None


def settle(base: str, sid: str) -> None:
    """Wait until the server has finished its background work for this session (the person took seconds here)."""
    deadline = time.time() + SETTLE_MAX_S
    while time.time() < deadline:
        try:
            code, body = http("GET", f"{base}/v1/usage?session={sid}", timeout=10)
        except (urllib.error.URLError, OSError):
            return
        if code != 200 or not body.get("pending"):
            return
        time.sleep(0.2)


def ask(base: str, req: dict) -> tuple[int, dict, float, int]:
    for attempt in range(1 + RETRIES):
        started = time.perf_counter()
        try:
            code, body = http("POST", base + "/v1/guide/next", req)
        except (urllib.error.URLError, OSError) as exc:
            code, body = 0, {"error": repr(exc)}
        took = time.perf_counter() - started
        if code == 200 or attempt == RETRIES:
            break
        time.sleep(RETRY_WAIT_S)  # a model call that failed for a moment (network, rate limit) is asked again
    return code, body, took, attempt + 1


def score(workload: dict, run: int, i: int, req: dict, code: int, body: dict, took: float, attempts: int) -> dict:
    turn = workload["turns"][i]
    want = turn["response"]["action"]
    got = body.get("action") if code == 200 and isinstance(body.get("action"), dict) else None
    meta = body.get("meta") or {}
    scored = confirmed(workload["turns"], i)
    match = same(got, want, req.get("screen") or {}) if got is not None else ""
    context, context_diff = context_match((turn.get("prompt") or {}).get("content"), meta.get("prompt"))
    return {
        "workload": workload["name"], "run": run, "index": i, "step": turn["request"].get("step"), "event": turn["request"].get("event"),
        "want": label(want), "got": label(got) if got else f"오류 {code}: {str(body.get('error'))[:80]}",
        "scored": scored, "correct": bool(match) if scored else None, "match": match if scored else "",
        "error": got is None, "attempts": attempts,
        "violations": violations(body, req.get("screen") or {}) if code == 200 else [],
        "latency_ms": round(took * 1000), "recorded_ms": round(float(turn.get("latency_s") or 0) * 1000),
        "status": code, "source": meta.get("source") or "", "mode": meta.get("mode") or "", "rounds": meta.get("rounds"),
        # jev 방식: 이 단계를 Jev가 골랐는지(jev) LLM에 넘겼는지(llm), Jev가 고른 보기와 그 확률
        "decider": meta.get("decider") or "", "jev_choice": (meta.get("jev") or {}).get("choice") or "",
        "jev_p": (meta.get("jev") or {}).get("p"),
        # jev 방식의 라우터: 글자 입력이 필요하다고 본 확률과, 그래서 정한 쪽(jev 또는 llm)
        "jev_typing_p": (meta.get("jev") or {}).get("typing_p"), "jev_route": (meta.get("jev") or {}).get("route") or "",
        "jev_route_reason": (meta.get("jev") or {}).get("route_reason") or "",
        "model_calls": meta.get("model_calls"), "input_tokens": meta.get("input_tokens"), "output_tokens": meta.get("output_tokens"),
        "cost": meta.get("cost"), "cost_currency": meta.get("cost_currency"), "context": context, "context_diff": context_diff,
    }


def run_session(base: str, workload: dict, run: int, check_context: bool) -> list[dict]:
    """One recorded session, state after state, the way it happened. The server keeps the history its own way."""
    sid = f"bench-{uuid.uuid4().hex[:10]}"
    rows, failed = [], False
    for i in range(len(workload["turns"])):
        if i > 0:
            settle(base, sid)
        req = state_request(workload, i, sid, resend_shot=failed, return_prompt=check_context)
        code, body, took, attempts = ask(base, req)
        rows.append(score(workload, run, i, req, code, body, took, attempts))
        failed = rows[-1]["error"]
    return rows


def run_step(base: str, workload: dict, run: int, i: int) -> dict:
    req = prompt_request(workload, i)
    return score(workload, run, i, req, *ask(base, req))


# ---------------------------------------------------------------- 집계

def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def total(rows: list[dict]) -> dict:
    answered = [r for r in rows if not r.get("error")]
    scored = [r for r in answered if r["scored"]]  # a step the server did not answer is not a wrong answer
    lat = [r["latency_ms"] for r in answered]
    sessions = {(r["workload"], r["run"]) for r in rows}
    out = {"sessions": len(sessions), "steps": len(rows), "errors": len(rows) - len(answered),
           "violations": sum(1 for r in rows if r.get("violations")),
           "scored": len(scored), "correct": sum(1 for r in scored if r["correct"]), "by_name": sum(1 for r in scored if r.get("match") == "이름"),
           "accuracy": round(sum(1 for r in scored if r["correct"]) / len(scored), 3) if scored else None,
           "latency_ms_median": round(statistics.median(lat)) if lat else None, "latency_ms_p90": round(pct(lat, 0.9)) if lat else None,
           "latency_ms_max": max(lat) if lat else None,
           "session_s_mean": round(sum(lat) / 1000 / len(sessions), 1) if sessions else None,
           "from_saved": sum(1 for r in answered if r.get("source") not in ("", "model", None))}
    known = [r for r in answered if r.get("model_calls") is not None]
    if known and len(known) == len(answered):
        out["model_calls"] = sum(r["model_calls"] or 0 for r in known)
        out["input_tokens"] = sum(r["input_tokens"] or 0 for r in known)
        out["output_tokens"] = sum(r["output_tokens"] or 0 for r in known)
        out["cost"] = round(sum(r["cost"] or 0 for r in known), 8)
        out["cost_currency"] = next((r["cost_currency"] for r in known if r.get("cost_currency")), None)
    return out


def _pad(s: str, width: int, right: bool = False) -> str:
    shown = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)
    gap = " " * max(0, width - shown)
    return gap + s if right else s + gap


COLUMNS = [("단계", 5), ("정답", 9), ("정답률", 7), ("오류", 5), ("규약 위반", 9), ("단계 지연 중앙/90%/최대", 24), ("세션당", 8), ("모델 호출", 9),
           ("입력 토큰", 10), ("출력 토큰", 9), ("비용", 14)]


def line(name: str, t: dict, name_width: int) -> str:
    acc = "-" if t["accuracy"] is None else f"{t['accuracy'] * 100:.0f}%"
    lat = "-" if t["latency_ms_median"] is None else f"{t['latency_ms_median']}/{t['latency_ms_p90']}/{t['latency_ms_max']}ms"
    cells = [str(t["steps"]), f"{t['correct']}/{t['scored']}", acc, str(t["errors"]), str(t["violations"]), lat,
             "-" if t["session_s_mean"] is None else f"{t['session_s_mean']}s"]
    if "model_calls" in t:
        cost = f"{t['cost']:.5f} {t['cost_currency']}" if t.get("cost_currency") else "-"
        cells += [str(t["model_calls"]), str(t["input_tokens"]), str(t["output_tokens"]), cost]
    else:
        cells += ["-", "-", "-", "-"]
    return "  " + _pad(name, name_width) + "".join(_pad(c, w + 2, right=True) for c, (_, w) in zip(cells, COLUMNS))


def header(first: str, name_width: int) -> str:
    return "  " + _pad(first, name_width) + "".join(_pad(h, w + 2, right=True) for h, w in COLUMNS)


def report(rows: list[dict], repeat: int, detail: bool) -> dict:
    names = list(dict.fromkeys(r["workload"] for r in rows))
    width = max([len(n) for n in names] + [10]) + 2
    by_run = {r: total([x for x in rows if x["run"] == r]) for r in range(1, repeat + 1)}
    by_workload = {n: total([x for x in rows if x["workload"] == n]) for n in names}
    overall = total(rows)
    if repeat > 1:
        print("\n회차별 (모든 워크로드를 합친 것. 1회차는 처음 보는 작업, 그 뒤는 본 적 있는 작업)")
        print(header("회차", width))
        for r, t in by_run.items():
            print(line(f"{r}회차", t, width))
    print("\n워크로드별" + (" (모든 회차를 합친 것)" if repeat > 1 else "") + "   세션당 = 그 세션의 단계 지연을 더한 것. 모델 호출과 비용은 답을 내는 데 든 것")
    print(header("워크로드", width))
    for n, t in by_workload.items():
        print(line(n, t, width))
    print(line("전체", overall, width))
    if overall["by_name"]:
        print(f"\n정답 가운데 {overall['by_name']}개는 가리킨 자리가 아니라 요소의 이름이 같아서 맞은 것입니다.")
    if overall["from_saved"]:
        print(f"모델을 부르지 않고 답한 단계: {overall['from_saved']}개")
    decided = [r for r in rows if r.get("decider") and not r.get("error")]
    if decided:
        by_jev = [r for r in decided if r["decider"] == "jev"]
        scored_jev = [r for r in by_jev if r["scored"]]
        print(f"\nJev가 고른 단계 {len(by_jev)}개(그중 채점한 {len(scored_jev)}개에서 정답 {sum(1 for r in scored_jev if r['correct'])}개), "
              f"LLM에 넘긴 단계 {len(decided) - len(by_jev)}개")
    checked = [r for r in rows if r.get("context")]
    if checked:
        n = {k: sum(1 for r in checked if r["context"] == k) for k in ("같음", "요약 글만 다름", "다름")}
        overall["context"] = n
        print(f"\n이력 대조 (서버가 재생에서 쌓은 프롬프트 ↔ 실제 실행 때 모델에 들어간 프롬프트), {len(checked)}단계: "
              f"같음 {n['같음']}, 요약 글만 다름 {n['요약 글만 다름']}, 다름 {n['다름']}")
        for r in [r for r in checked if r["context"] == "다름"][:5 if detail else 2]:
            print(f"  {r['workload']} {r['step']}단계: {r['context_diff']}")
    failed = [r for r in rows if r.get("error")]
    if failed:
        print(f"\n서버가 답하지 못한 단계 {len(failed)}개. 이 측정은 믿을 수 없습니다. 원인을 고친 뒤 --resume으로 다시 잽니다.")
        for r in failed[:5]:
            print(f"  {r['workload']} {r['run']}회차 {r['step']}단계: {r['got']}")
    broken = [r for r in rows if r.get("violations")]
    if broken:
        print(f"\n규약(docs/interface.md)을 어긴 응답 {len(broken)}개. 앱이 이 응답으로는 안내하지 못합니다.")
        for r in broken[:10]:
            print(f"  {r['workload']} {r['run']}회차 {r['step']}단계: {'; '.join(r['violations'])}")
    wrong = [r for r in rows if r["scored"] and not r.get("error") and not r["correct"]]
    if wrong:
        print(f"\n틀린 단계 {len(wrong)}개" + ("" if detail else "  (--detail로 하나씩 봅니다)"))
        if detail:
            for r in wrong:
                print(f"  {r['workload']} {r['run']}회차 {r['step']}단계: 기록 = {r['want']}, 서버 = {r['got']}")
    return {"overall": overall, "by_run": by_run, "by_workload": by_workload}


def compare(now: dict, then_path: Path) -> None:
    p = then_path / "result.json" if then_path.is_dir() else then_path
    then = json.loads(p.read_text(encoding="utf-8"))
    print(f"\n비교: {then.get('label') or p.parent.name} (방식 {then.get('mode', '?')}, 동시에 {then.get('parallel', '?')}개) → 이번")

    def show(name: str, a: dict | None, b: dict | None) -> None:
        if not a or not b:
            return

        def cell(t: dict) -> str:
            acc = "-" if t.get("accuracy") is None else f"{t['accuracy'] * 100:.0f}%"
            cost = f"{t['cost']:.5f}" if t.get("cost_currency") else "-"
            return (f"정답률 {acc:>4}  단계 지연 중앙값 {t.get('latency_ms_median')}ms  세션당 {t.get('session_s_mean')}s  "
                    f"모델 호출 {t.get('model_calls', '-')}  비용 {cost}")
        print(f"  {name}\n    전: {cell(a)}\n    후: {cell(b)}")

    show("전체", then["summary"]["overall"], now["overall"])
    for r, t in now["by_run"].items():
        show(f"{r}회차", then["summary"]["by_run"].get(str(r)), t)


def show_prompt(spec: str, paths: list[Path]) -> int:
    """Print what the model was given at one recorded step: the text as it is, each image as one line."""
    name, _, n = spec.partition(":")
    path = next((p for p in paths if p.stem == name), None)
    if path is None:
        print(f"워크로드 {name}이 없습니다")
        return 1
    w = load(path)
    turn = next((t for t in w["turns"] if str(t["request"].get("step")) == (n or "1")), None)
    if turn is None or "prompt" not in turn:
        print(f"{name}에 {n or 1}단계의 프롬프트가 없습니다")
        return 1
    print(f"[시스템 글 {len(turn['prompt']['system'])}자: 앞 300자]\n{turn['prompt']['system'][:300]}\n...\n\n[내용]")
    for b in turn["prompt"]["content"]:
        if b.get("type") == "text":
            print(b["text"])
        else:
            print(f"<스크린샷 {b.get('image')} {len(w['images'].get(b.get('image'), '')) * 3 // 4 // 1024}KB>")
    print(f"\n[그때의 답] {json.dumps(turn['response'].get('action'), ensure_ascii=False)}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="기록한 에뮬레이터 상태를 artemis 서버에 차례로 내밀고 지연시간, 비용, 정답 여부를 잰다")
    ap.add_argument("workloads", nargs="*", type=Path, help="워크로드 파일(.jsonl). 없으면 bench/workloads/ 전부")
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--mode", default="", help="서버가 답하는 방식(baseline 또는 서버가 가진 다른 이름). 없으면 서버의 기본 방식")
    ap.add_argument("--replay", choices=("session", "prompt"), default="session",
                    help="session: 상태를 차례로 내밀고 이력은 서버가 쌓는다(기본). prompt: 기록된 프롬프트를 그대로 넣는다")
    ap.add_argument("--parallel", type=int, default=4, help="동시에 돌리는 세션 수 (--replay prompt에서는 단계 수)")
    ap.add_argument("--repeat", type=int, default=1, help="워크로드 전체를 이만큼 되풀이한다 (회차는 차례로)")
    ap.add_argument("--label", default="", help="이번 측정의 이름. 결과는 runs/bench-<이름>/에 남는다")
    ap.add_argument("--out", type=Path, help="결과를 둘 폴더")
    ap.add_argument("--against", type=Path, help="비교할 앞선 결과(폴더 또는 result.json)")
    ap.add_argument("--detail", action="store_true", help="틀린 단계를 하나씩 보여 준다")
    ap.add_argument("--resume", action="store_true", help="같은 이름의 앞선 측정에서 오류 없이 끝난 세션은 다시 재지 않는다")
    ap.add_argument("--check-context", action="store_true", help="서버가 쌓은 프롬프트를 실제 실행 때의 것과 대조한다 (session 모드)")
    ap.add_argument("--show-prompt", metavar="워크로드:단계", help="그 단계의 기록된 프롬프트를 보여 주고 끝낸다")
    args = ap.parse_args()
    base = args.url.rstrip("/")
    global SERVER_MODE
    SERVER_MODE = args.mode or None

    paths = args.workloads or sorted(WORKLOADS.glob("*.jsonl"))
    if args.show_prompt:
        return show_prompt(args.show_prompt, paths)
    workloads = [w for w in (load(p) for p in paths) if w["turns"]]
    if args.replay == "prompt":
        for w in workloads:
            w["turns"] = [t for t in w["turns"] if "prompt" in t]
        workloads = [w for w in workloads if w["turns"]]
    if not workloads:
        print(f"워크로드가 없습니다. 서버를 GUIDE_RECORD_DIR을 주고 띄운 뒤 한 번 따라 하면 {WORKLOADS}에 생깁니다.")
        return 1
    try:
        code, health = http("GET", base + "/health", timeout=10)
    except (urllib.error.URLError, OSError) as exc:
        print(f"{base}에 닿지 못했습니다: {exc}")
        return 1
    print(f"서버 {base}  {json.dumps({k: v for k, v in health.items() if k != 'ok'}, ensure_ascii=False)}")
    started = time.perf_counter()
    http("GET", base + "/health", timeout=10)
    took = time.perf_counter() - started
    print(f"/health {took * 1000:.0f}ms" + ("" if code == 200 and took <= APP_HEALTH_WAIT_S else
                                             f"  ← 규약 위반: 앱은 {APP_HEALTH_WAIT_S}초 안에 200이 와야 연결됐다고 본다"))
    modes = health.get("modes") or []
    measured = args.mode or health.get("default_mode") or "(서버의 기본)"
    if args.mode and modes and args.mode not in modes:
        print(f"서버에 '{args.mode}' 방식이 없습니다. 있는 것: {', '.join(modes)}")
        return 1
    print(f"재는 방식: {measured}" + (f"   (서버가 가진 방식: {', '.join(modes)})" if modes else ""))
    steps = sum(len(w["turns"]) for w in workloads)
    what = "세션" if args.replay == "session" else "단계"
    print(f"워크로드 {len(workloads)}개, {steps}단계 × {args.repeat}회, 동시에 {what} {args.parallel}개" + ("" if args.replay == "session" else "  (기록된 프롬프트 그대로)"))
    for w in workloads:
        print(f"  {w['name']}: {len(w['turns'])}단계  {w['task']}")

    label_ = args.label or time.strftime("%m%d-%H%M%S")
    out = args.out or HERE.parent / "runs" / f"bench-{label_}"
    out.mkdir(parents=True, exist_ok=True)
    partial = out / "rows.partial.json"
    rows: list[dict] = []
    if args.resume and partial.exists():
        names = {w["name"] for w in workloads}
        kept = [r for r in json.loads(partial.read_text(encoding="utf-8")) if r["workload"] in names and r["run"] <= args.repeat]
        bad = {(r["workload"], r["run"]) for r in kept if r.get("error")}  # a session with an error is measured again whole
        rows = [r for r in kept if (r["workload"], r["run"]) not in bad]
        print(f"앞선 측정에서 {len(rows)}단계를 이어받습니다")
    have = {(r["workload"], r["run"]) for r in rows}
    before = usage(base)
    wall = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        for run in range(1, args.repeat + 1):
            todo = [w for w in workloads if (w["name"], run) not in have]
            began = time.perf_counter()
            if args.replay == "session":
                done = [r for session in pool.map(lambda w: run_session(base, w, run, args.check_context), todo) for r in session]
            else:
                jobs = [(w, i) for w in todo for i in range(len(w["turns"]))]
                done = list(pool.map(lambda job: run_step(base, job[0], run, job[1]), jobs))
            rows += done
            # kept as it goes: a run that is stopped half-way still leaves what it measured
            partial.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            if done:
                right = sum(1 for r in done if r["correct"])
                scored = sum(1 for r in done if r["scored"] and not r["error"])
                print(f"  {run}회차: {len(done)}단계, 정답 {right}/{scored}, 오류 {sum(1 for r in done if r['error'])}, "
                      f"{time.perf_counter() - began:.1f}s", flush=True)
    wall = time.perf_counter() - wall
    time.sleep(3)  # the last summaries are still being written
    after = usage(base)

    order = {w["name"]: k for k, w in enumerate(workloads)}
    rows.sort(key=lambda r: (r["run"], order[r["workload"]], r["index"]))
    summary = report(rows, args.repeat, args.detail)
    other = sorted({r["mode"] for r in rows if not r["error"] and args.mode and r["mode"] and r["mode"] != args.mode})
    if other:
        print(f"\n'{args.mode}' 방식을 재려 했는데 서버가 {', '.join(other)}(으)로 답한 단계가 있습니다. 이 측정은 그 방식의 것이 아닙니다.")
    print(f"\n걸린 시간 {wall:.0f}초 (동시에 {what} {args.parallel}개, 방식 {measured})")
    server_side = None
    if before is not None and after is not None:
        server_side = {m: {k: round((after[m].get(k) or 0) - (before.get(m, {}).get(k) or 0), 8) for k in ("calls", "input_tokens", "output_tokens", "cost")}
                       for m in after if (after[m].get("calls") or 0) - (before.get(m, {}).get("calls") or 0)}
        calls = sum(v["calls"] for v in server_side.values())
        cost = sum(v["cost"] for v in server_side.values())
        print(f"서버가 센 전체 사용량 (요약 같은 뒷일 포함, GET /v1/usage): 모델 호출 {calls}회, 비용 {cost:.5f}  "
              + ", ".join(f"{m} {v['calls']}회" for m, v in server_side.items()))
    result = {"label": label_, "url": base, "server": health, "mode": measured, "replay": args.replay, "repeat": args.repeat, "parallel": args.parallel,
              "wall_s": round(wall, 1), "workloads": [w["name"] for w in workloads], "summary": summary, "server_usage": server_side, "rows": rows}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(out / "turns.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows([{**r, "violations": "; ".join(r.get("violations") or [])} for r in rows])
    print(f"결과: {out}")
    if args.against:
        compare(summary, args.against)
    return 2 if summary["overall"]["errors"] or summary["overall"]["violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
