"""jev 방식의 질문 구성을 서버 없이 비교한다: 채점 대상 단계마다 Jev에 묻고, Jev가 맡지 않는 단계는 baseline의 결과로 채운다.

    python bench/jev_eval.py --baseline runs/bench-baseline --baseline-logs-after 2026-11-21T02:50
    python bench/jev_eval.py ... --models typesafe/jev-1.13 typesafe/jev-1.14

relay/에서 `uv run python ../bench/jev_eval.py ...`로 돌린다(artemis 코드와 Pillow가 필요하다). 키는 relay/.env의 OPENROUTER_API_KEY.

비교하는 질문 구성은 둘이다. 상태(목표, 지난 단계, 직전 결과, 요소 목록, 스크린샷)는 둘이 같다.
  A  지금 서버(relay/jev.py): `needs_typing`(예/아니오) + `next_action`(묶은 요소 보기, 밀기, BACK, HOME, 기다리기, 끝남, 그 밖)
     글자 입력 확률이 0.5 이상이거나 '그 밖'이면 LLM. --a-min-p 값마다 "고른 보기의 확률이 그보다 낮으면 LLM"도 본다
  B  dev-benchmark의 설계: `kind`(tap/done/text/other), `action`, `target`(묶은 요소 보기), `target_listed`(예/아니오),
     `direction`, `key`, `goal_met`(예/아니오). 질문마다 기준을 넘어야 Jev가 답하고, 아니면 LLM.
     --b-min-conf 값마다 kind, action, target, direction, key의 confidence 기준을 같이 바꿔 본다.
     target_listed는 0.7, goal_met은 0.9 이상이어야 한다. 직전 결과가 unaimable이면 LLM.

정답은 bench/run.py의 same()과 같은 기준(지금 하네스)과, 앱처럼 두 자리가 같은 누를 수 있는 요소에 링을 그리면 정답으로 보는
기준(앱 기준) 둘로 센다. LLM이 맡는 단계는 --baseline의 그 단계 결과로 채운다. 앱 기준의 baseline 결과는 그 측정 때 서버 기록
(relay/logs)에서 답을 다시 읽어 계산한다(--baseline-logs-after: 그 측정을 시작한 시각).

Jev의 답은 runs/jev-eval/answers.json에 남기고, 다음에는 다시 묻지 않는다(--fresh로 다시 묻는다).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
os.environ.setdefault("ARTEMIS_HOME", str(REPO))
sys.path[:0] = [str(REPO / "relay"), str(HERE)]
import artemis_bridge as ab  # noqa: E402
import backends  # noqa: E402
import jev  # noqa: E402
import run as bench  # noqa: E402

A_MIN_P = (0.0, 0.3, 0.5)
B_MIN_CONF = (0.0, 0.2, 0.4, 0.6)
C_BOX_MIN = (0.6, 0.8, 0.9)  # design C = B + `target_box` (Jev 1.14 only): box confidence needed to tap its point


def ring(xml: str, pt) -> list[int] | None:
    """The bounds of the clickable element the app would ring for a tap at pt (the deepest clickable node holding it)."""
    try:
        root = ET.fromstring(xml)
    except Exception:  # noqa: BLE001
        return None
    best = None

    def walk(n, depth):
        nonlocal best
        b = jev._box(n)
        if b and b[0] <= pt[0] < b[2] and b[1] <= pt[1] < b[3] and n.get("clickable") == "true" and (best is None or depth >= best[0]):
            best = (depth, b)
        for c in n:
            walk(c, depth + 1)
    walk(root, 0)
    return best[1] if best else None


def judge(got: dict | None, want: dict, screen: dict) -> tuple[bool, bool]:
    """(correct by the current harness, correct by the app's way of aiming)."""
    if not got:
        return False, False
    ok = bool(bench.same(got, want, screen))
    if ok:
        return True, True
    gp, wp = got.get("target_point_px"), want.get("target_point_px")
    if got.get("name") == want.get("name") == "click" and gp and wp:
        a, b = ring(screen.get("xml", ""), wp), ring(screen.get("xml", ""), gp)
        return False, bool(a and b and a == b)
    return False, False


def steps() -> list[dict]:
    out = []
    for path in sorted((HERE / "workloads").glob("*.jsonl")):
        w = bench.load(path)
        for i, t in enumerate(w["turns"]):
            if not bench.confirmed(w["turns"], i):
                continue
            req = bench._restore(t["request"], w["images"])
            scr = req["screen"]
            W, H = int(scr["width"]), int(scr["height"])
            text, els = ab.observe(scr["xml"], W, H)
            out.append({"workload": w["name"], "step": t["request"].get("step"), "task": w["task"], "full_task": req.get("task", ""),
                        "req": req, "screen": scr, "W": W, "H": H, "text": text, "els": els, "want": t["response"]["action"],
                        "turns": (t.get("session") or {}).get("turns") or [],
                        "crit": jev.options(els, text, scr["xml"])})
    return out


def body_for(s: dict, design: str, model: str) -> dict:
    current = {"event": s["req"].get("event"), "event_detail": s["req"].get("event_detail"), "elements_text": s["text"],
               "screenshot_b64": s["screen"].get("screenshot_jpeg_b64")}
    return jev.request_body(s["full_task"], s["turns"], current, s["crit"], design="one" if design == "A" else "split",
                            model=model, box=design == "C")


def ask(body: dict) -> tuple[dict, float, float]:
    key = backends._env_key("OPENROUTER_API_KEY")
    req = urllib.request.Request(jev.URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    for attempt in range(3):
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                j = json.loads(r.read().decode("utf-8"))
            return j.get("answers") or {}, round(time.time() - started, 3), float((j.get("usage") or {}).get("cost") or 0)
        except (urllib.error.URLError, OSError) as exc:
            if attempt == 2:
                raise RuntimeError(f"jev: {str(exc).replace(key, '<KEY>')[:200]}") from None
            time.sleep(2)


def act_a(s: dict, ans: dict, min_p: float) -> dict | None:
    """Design A (relay/jev.py decide_one): the action Jev answers with, or None when the step goes to the LLM."""
    d = jev.decide_one(ans, s["crit"], s["els"], min_p=min_p)
    return d["action"] if d["route"] == "jev" else None


def act_c(s: dict, ans: dict, box_min: float) -> dict | None:
    """Design C (B with `target_box`): a step whose target is not listed is tapped at Jev's box when it is sure enough."""
    d = jev.decide_split(ans, s["crit"], s["els"], s["req"].get("event", ""), min_conf=0.0, box_min=box_min)
    return d["action"] if d["route"] == "jev" else None


def act_b(s: dict, ans: dict, c: float) -> dict | None:
    """Design B (relay/jev.py decide_split): the action Jev answers with, or None when the step goes to the LLM."""
    d = jev.decide_split(ans, s["crit"], s["els"], s["req"].get("event", ""), min_conf=c)
    return d["action"] if d["route"] == "jev" else None


def baseline_results(path: Path, logs_after: str) -> dict:
    """(workload, step) -> (correct by the harness, correct by the app's aim) of the baseline measurement."""
    rows = json.loads((path / "result.json" if path.is_dir() else path).read_text(encoding="utf-8"))["rows"]
    logs = {}
    for f in glob.glob(str(REPO / "relay/logs/*.jsonl")):
        L = [json.loads(x) for x in open(f, encoding="utf-8") if x.strip()]
        st = [x for x in L if "step" in x and (x.get("meta") or {}).get("mode") == "baseline"]
        if st and st[0]["t"] >= logs_after:
            logs[(st[0]["task"], len(st))] = {x["step"]: x for x in st}
    out = {}
    for r in rows:
        if not r["scored"]:
            continue
        w = bench.load(HERE / "workloads" / f"{r['workload']}.jsonl")
        t = w["turns"][r["index"]]
        app = bool(r["correct"])
        rec = (logs.get((t["request"]["task"], len(w["turns"]))) or {}).get(t["request"]["step"])
        if not app and rec and (rec.get("reply") or {}).get("action"):
            scr = bench._restore(t["request"], w["images"])["screen"]
            _, els = ab.observe(scr["xml"], int(scr["width"]), int(scr["height"]))
            got = ab.resolve_target(rec["reply"]["action"], els, int(scr["width"]), int(scr["height"]))
            app = judge(got, t["response"]["action"], scr)[1]
        out[(r["workload"], r["step"])] = (bool(r["correct"]), app)
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--baseline-logs-after", default="", help="그 baseline 측정을 시작한 시각(서버 기록에서 답을 찾는다)")
    ap.add_argument("--models", nargs="+", default=["typesafe/jev-1.13", "typesafe/jev-1.14"])
    ap.add_argument("--out", type=Path, default=REPO / "runs" / "jev-eval")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--parallel", type=int, default=8)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cache_path = args.out / "answers.json"
    cache = {} if args.fresh or not cache_path.exists() else json.loads(cache_path.read_text(encoding="utf-8"))
    S = steps()
    base = baseline_results(args.baseline, args.baseline_logs_after)
    designs = lambda m: ("A", "B", "C") if "1.14" in m else ("A", "B")
    jobs = [(s, d, m) for m in args.models for d in designs(m) for s in S if f"{d}|{m}|{s['workload']}|{s['step']}" not in cache]

    def run(job):
        s, d, m = job
        ans, lat, cost = ask(body_for(s, d, m))
        return f"{d}|{m}|{s['workload']}|{s['step']}", {"answers": ans, "latency_s": lat, "cost": cost}
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        for k, v in pool.map(run, jobs):
            cache[k] = v
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    b_h = sum(v[0] for v in base.values() if v is not None)
    b_a = sum(v[1] for v in base.values() if v is not None)
    print(f"채점 단계 {len(S)}개. baseline: 지금 하네스 {b_h}, 앱 기준 {b_a}")
    print("구성  모델              기준   Jev가 맡음  그중 정답(하네스/앱)  LLM이 맡음  전체 정답(하네스/앱)  Jev 지연(초)   Jev 비용($)")
    summary = []
    for m in args.models:
        for d, grid in (("A", A_MIN_P), ("B", B_MIN_CONF), ("C", C_BOX_MIN))[:len(designs(m))]:
            recs = [(s, cache[f"{d}|{m}|{s['workload']}|{s['step']}"]) for s in S]
            lats = sorted(r["latency_s"] for _, r in recs)
            cost = sum(r["cost"] for _, r in recs)
            for th in grid:
                took, ok_h, ok_a, tot_h, tot_a, wrong = 0, 0, 0, 0, 0, []
                for s, r in recs:
                    a = {"A": act_a, "B": act_b, "C": act_c}[d](s, r["answers"], th)
                    if a is None:
                        bh, ba = base.get((s["workload"], s["step"]), (False, False))
                        tot_h += bh
                        tot_a += ba
                        continue
                    took += 1
                    got = ab.resolve_target(a, s["els"], s["W"], s["H"])
                    if got.get("target_error"):
                        got = None
                    h, app = judge(got, s["want"], s["screen"])
                    ok_h += h
                    ok_a += app
                    tot_h += h
                    tot_a += app
                    if not app:
                        wrong.append(f"{s['workload']} {s['step']}")
                summary.append({"design": d, "model": m, "threshold": th, "jev_steps": took, "jev_ok": ok_h, "jev_ok_app": ok_a,
                                "llm_steps": len(S) - took, "total": tot_h, "total_app": tot_a, "jev_wrong_app": wrong,
                                "latency_s": [lats[0], lats[-1]], "cost_usd": round(cost, 5)})
                print(f"  {d}   {m:<18} {th:<5}  {took:>9}  {ok_h:>9}/{ok_a:<10}  {len(S) - took:>9}  {tot_h:>10}/{tot_a:<9}  "
                      f"{lats[0]:.2f}~{lats[-1]:.2f}   {cost:.4f}")
    (args.out / "summary.json").write_text(json.dumps({"baseline": [b_h, b_a], "steps": len(S), "rows": summary}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"결과: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
