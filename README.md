# 해봄 artemis 서버

Android 폰의 화면 상태 하나를 받아 **다음에 할 동작 하나**를 돌려주는 서버입니다. 판단은 [google/artemis](https://github.com/google/artemis)가 합니다. 이 저장소는 google/artemis를 그대로 받아 그 위에 커밋을 쌓은 것이고, 밖에서 보면 artemis 그대로입니다. 달라진 것은 둘뿐입니다.

- 화면을 기기에서 직접 읽지 않고 **요청으로 받습니다.**
- 고른 동작을 실행하지 않고 **응답으로 돌려줍니다.** 그 동작은 사람이 자기 손으로 합니다.

해봄은 스마트폰이 낯선 사람 옆에서 다음에 누를 곳을 짚어 주는 앱입니다(2026 서울AI디지털배움터 청년AI해커톤, 팀 Toolus). 이 저장소에서 하는 일은 셋입니다.

1. **서버**: 앱이 붙는 artemis 서버를 띄웁니다.
2. **artemis 고치기**: `artemis/` 아래의 코드와 `relay/`의 서버 코드를 고칩니다. 고친 것이 서버에 바로 반영됩니다.
3. **벤치마크**: 실제 실행을 기록해 둔 화면 상태를 서버에 다시 내밀어 지연시간, 비용, 정답 여부를 잽니다. 폰이 필요 없습니다.

```
 해봄 앱 (Android) ──화면 상태──▶ artemis 서버 (이 저장소) ──▶ Letsur ──▶ 모델
        ▲           ◀──동작 하나──
        └──문장──▶ TTS 서버 ──▶ 소리
```

| 저장소 | 하는 일 |
|---|---|
| haebom-artemis (여기) | 화면 상태를 받아 다음 동작 하나를 고르는 서버 |
| [haebom-tts](https://github.com/youth-hackathon/haebom-tts) | 안내 문장을 소리로 바꾸는 서버 |
| haebom-app | 화면 위에 다음에 누를 곳을 안내하는 Android 앱 |

서로 아는 것은 주고받는 형식뿐입니다. 그 형식은 [`docs/interface.md`](docs/interface.md)에 있습니다.

## 빠르게 시작하기

준비물은 git, Python 3.12 이상, [uv](https://docs.astral.sh/uv/)입니다.

### 1. 키 넣기

모델은 [Letsur](https://gw.letsur.ai) 게이트웨이로 부릅니다. **키는 각자 자기 것을 자기 컴퓨터에 넣습니다.** 저장소에는 키가 없습니다.

```bash
cp relay/.env.example relay/.env
# relay/.env 를 열어 LETSUR_API_KEY= 뒤에 자기 키를 적는다
```

`relay/.env`는 `.gitignore`에 들어 있어 커밋되지 않습니다. 키를 화면에 출력하거나 다른 파일에 옮겨 적지 않습니다.

### 2. 서버 띄우기

```bash
cd relay
uv sync                                    # 처음 한 번. artemis의 의존성을 고정된 버전으로 설치한다
ARTEMIS_HOME=.. uv run python server.py    # http://127.0.0.1:8765
```

`ARTEMIS_HOME=..`은 설치된 artemis 패키지 대신 **이 저장소의 `artemis/` 코드를 쓰라**는 뜻입니다. `artemis/`나 `relay/`를 고친 뒤 서버를 껐다 켜면 바로 반영됩니다. Windows PowerShell에서는 `$env:ARTEMIS_HOME=".."; uv run python server.py`입니다.

### 3. 확인

```bash
curl http://127.0.0.1:8765/health
```

```json
{"ok": true, "profile": "artemis", "planner": "letsur:gemini-3.8-flash", "summary": "letsur:gemini-3.5-flash-lite",
 "explorer": "letsur:gemini-3.8-flash", "recording": false, "modes": ["baseline"], "default_mode": "baseline", "letsur": "direct"}
```

`letsur`가 `direct`면 키가 읽힌 것이고, `missing`이면 키가 없는 것입니다. 키가 없어도 서버는 뜨고, 첫 요청에 무엇이 빠졌는지 502로 알려 줍니다.

### 4. 벤치마크

다른 터미널에서, 저장소 맨 위에서 돌립니다. 파이썬 표준 라이브러리만 씁니다.

```bash
python bench/run.py bench/workloads/brightness.jsonl --mode baseline --label smoke   # 4단계, 30초쯤
python bench/run.py --mode baseline --label baseline                                  # 워크로드 전부
```

Windows에서 한글이 깨지면 `python -X utf8 bench/run.py ...`로 실행합니다.

### Docker로 띄우기 (선택)

코드를 고칠 일이 없고 서버만 필요하면 Docker가 편합니다([`docs/docker.md`](docs/docker.md)).

```bash
docker compose up -d --build artemis      # http://127.0.0.1:8765. 처음에는 몇 분 걸린다
docker compose logs -f artemis
```

키는 똑같이 `relay/.env`에서 읽고 이미지에는 들어가지 않습니다. 코드를 고쳤으면 같은 명령으로 다시 만듭니다. 에뮬레이터나 USB로 연결한 폰의 앱은 `adb reverse tcp:8765 tcp:8765`으로 이 서버에 닿습니다.

## 무엇을 주고받나

전체는 [`docs/interface.md`](docs/interface.md)에 있습니다. 요약입니다.

**요청** `POST /v1/guide/next`: plain artemis가 기기에서 직접 얻는 것을 앱이 대신 보냅니다.

```json
{"session_id": "d2fa3798", "task": "화면 밝기 설정 화면을 열어줘", "step": 2, "elapsed_s": 10,
 "event": "met", "event_detail": "the human tapped the highlighted element",
 "screen": {"package": "com.android.settings", "width": 1080, "height": 2400,
            "xml": "<hierarchy ...>", "screenshot_jpeg_b64": "/9j/..."}}
```

**응답**: artemis의 동작 하나입니다. 서버는 실행하지 않습니다.

```json
{"memo": "...", "explain": "화면 설정으로 들어가면 밝기를 바꿀 수 있어요.",
 "action": {"name": "click", "target": 24, "target_point_px": [354, 1761], "target_text": "디스플레이 및 터치",
            "target_bounds_px": [179, 1728, 529, 1794]},
 "meta": {"rounds": 1, "latency_s": 6.4, "model_calls": 1, "input_tokens": 6610, "output_tokens": 310,
          "cost": 0.0049, "cost_currency": "unit", "source": "model", "mode": "baseline"}}
```

- `event`는 직전 동작이 어떻게 끝났는지입니다: `start`, `met`(사용자가 해냄), `unaimable`(그 화면에서 안내할 수 없었음), `declared_stuck` 등.
- 동작은 `click`, `long_press`, `input_text`, `swipe`, `press_key`, `manage_app`, `wait_for_delay`, `ask_user`, `report_task_status` 아홉 가지입니다.
- 그 밖에 `GET /health`, `GET /v1/usage`(모델 사용량의 누계)가 있습니다.

## 모델

artemis의 기본 모델을 그대로 쓰고, 모델을 불러 주는 곳만 Google에서 Letsur로 바꿨습니다. 이것이 베이스라인입니다.

| 역할 | artemis의 기본값 (`config/artemis.jsonc`) | 이 서버 |
|---|---|---|
| 판단 | `gemini-3.8-flash`, 생각 수준 medium | 같음 (`letsur:gemini-3.8-flash`, `reasoning.effort` medium) |
| 단계 요약 | `gemini-3.5-flash-lite` | 같음 |
| 위치 찾기 | `gemini-robotics-er-2-preview` | `gemini-3.8-flash`. Letsur에 그 모델이 없습니다(404) |

- 모델 호출은 `relay/backends.py` 한 곳을 거칩니다. Letsur의 `/v1/responses`를 부르고 답은 JSON 스키마로 받습니다. 호출 수, 토큰, 비용도 거기서 셉니다.
- 호출 하나는 55초까지 기다리고, 연결이 끊기거나 5xx면 한 번 더 부릅니다.
- `GUIDE_PROFILE`로 다른 구성을 고를 수 있습니다: `luna`(판단만 `gpt-6-luna`), `haiku`(Claude Haiku 5.5, `ANTHROPIC_API_KEY`).

## 베이스라인과 새 방식

방식은 브랜치로 나누지 않습니다. **서버 하나가 여러 방식을 가지고, 요청이 이름으로 고릅니다.**

- `baseline`은 언제나 있습니다. plain artemis 그대로입니다. 요청에 `"mode": "baseline"`을 적으면 서버가 그 밖에 무엇을 할 줄 알든 이것으로 답합니다.
- 새 방식(예: 저장해 둔 계획을 쓰는 캐시)은 자기 이름으로 그 옆에 더합니다. `relay/server.py`의 `MODES`에 이름을 넣고, `guide_next`에서 그 이름일 때의 길을 만듭니다. 베이스라인의 길은 고치지 않고, 새 방식이 답을 못 낼 때 불러 쓰는 것은 됩니다.
- 요청에 `mode`가 없으면 환경 변수 `GUIDE_MODE`의 방식으로 답합니다(기본 `baseline`).
- 응답의 `meta.mode`에 어느 방식으로 답했는지 적습니다. 모르는 이름에는 400과 가진 이름들을 돌려줍니다.
- 베이스라인으로 답할 때는 새 방식이 저장해 둔 것을 읽지도 쓰지도 않습니다. 그래야 `--mode baseline`으로 잰 숫자가 언제 재도 같은 뜻입니다.

앱은 설정에서 방식을 고릅니다. 새 방식이 말썽이면 서버를 건드리지 않고 `baseline`으로 돌립니다.

### `jev` 방식

누를 곳을 고르는 일을 LLM 대신 TypeSafe Jev가 합니다(`relay/jev.py`). Jev는 글을 쓰는 모델이 아니라, 주어진 보기 가운데 하나를 고르고 보기마다 확률을 돌려주는 모델입니다. OpenRouter의 Decisions API로 부릅니다.

- 보기는 화면마다 새로 만듭니다. 누를 수 있는 요소마다 "이것을 누른다"가 하나씩 있고, 위로 밀기, 아래로 밀기, 뒤로, 홈, 기다리기, 끝남, 그 밖이 더해집니다. 설정 화면의 한 줄처럼 제목과 설명 글이 누를 수 있는 요소 하나에 들어 있으면 보기 하나로 묶고, 누를 수 있는 요소에 들지 않은 글은 보기에서 뺍니다. 앱은 누를 수 있는 요소에만 링을 그리기 때문입니다. 시계 다이얼처럼 글이 넷 이상 든 요소는 묶지 않습니다. `GUIDE_JEV_GROUP=off`면 요소마다 보기를 만듭니다. Jev는 이 보기와 함께 지금 스크린샷, 요소 목록, 지난 단계와 그 결과를 받습니다.
- `explain`은 정해 둔 틀로 만듭니다("'디스플레이 및 터치' 누르면 다음 단계로 넘어가요."). `memo`는 비워 둡니다.
- **라우터:** 같은 요청에 "다음 단계가 글자 입력인가"를 묻는 예/아니오 질문을 함께 넣습니다. Jev는 글을 쓰지 못하므로, 그 확률이 `GUIDE_JEV_ROUTE_P`(기본 0.5) 이상이면 그 단계는 `GUIDE_JEV_FALLBACK`이 정한 쪽이 답합니다. `baseline`(기본)이면 LLM이 답하고, `none`이면 그래도 Jev가 답합니다(Letsur 키 없이 잴 때). 라우터의 판단은 어느 쪽이든 `meta.jev`에 남습니다.
- 보기에는 "그 밖"(앱 이름으로 열기, 목록에 없는 곳 누르기, 길게 누르기, 사용자에게 묻기)도 있습니다. Jev가 이것을 고르면 그 단계도 글자 입력과 같은 쪽이 답합니다. `GUIDE_JEV_ROUTE_OTHER=off`면 Jev가 그다음으로 그럴듯하게 본 보기를 씁니다.
- **질문 구성 두 가지(`GUIDE_JEV_DESIGN`):** 위의 설명은 `one`입니다. 기본값인 `split`은 `dev-benchmark`의 설계대로 단계의 종류(`kind`: 누르기/끝남/글자 입력/그 밖), 동작, 누를 요소, "누를 곳이 목록에 있나"(예/아니오), 밀기 방향, 키, "목표가 이미 이뤄졌나"(예/아니오)를 한 요청에서 따로 묻습니다. 그 단계에 필요한 답이 모두 기준을 넘어야 Jev가 답합니다. 목록에 있을 확률은 0.7, 목표가 이뤄졌을 확률은 0.9 이상이어야 하고, 글자 입력·그 밖·앱 열기·`unaimable` 바로 다음 단계는 LLM이 답합니다. 2026-11-21에 Jev 1.14로 세 번씩 재 봤을 때 `split`이 가장 나아서 기본값으로 삼았습니다(아래 "jev 방식 측정"). 모델의 기본값도 `typesafe/jev-1.14`입니다.
- **목록에 없는 곳(`GUIDE_JEV_BOX=on`, `split`에서만):** 누를 곳이 목록에 없다고 보면 Jev가 스크린샷에서 그 자리를 상자로 찾습니다. 상자의 확신이 `GUIDE_JEV_BOX_MIN`(기본 0.8) 이상이면 그 가운데를 누르게 하고, 아니면 LLM이 답합니다. Jev 1.14가 있어야 합니다(`GUIDE_JEV_MODEL=typesafe/jev-1.14`).
- `GUIDE_JEV_MIN_P`(기본 0)를 주면, Jev가 고른 보기의 확률이 그보다 낮을 때도 LLM이 답합니다. 0.5로 재 봤을 때 효과가 없어서 기본값은 꺼 두었습니다.
- 그 밖의 단계는 모두 Jev가 답합니다.
- 키는 `relay/.env`의 `OPENROUTER_API_KEY`입니다.
- 응답의 `meta.decider`에 누가 골랐는지(`jev` 또는 `llm`), `meta.jev`에 Jev가 고른 보기, 확률, 글자 입력일 확률(`typing_p`), 라우터가 정한 쪽(`route`), 걸린 시간, 달러 비용을 적습니다. `meta.model_calls`와 `meta.latency_s`에는 Jev 호출도 들어가지만, `meta.input_tokens`와 `meta.cost`는 LLM의 것만 셉니다.

```bash
python bench/run.py --mode jev --label jev --against runs/bench-baseline   # LLM 방식과 나란히
GUIDE_JEV_FALLBACK=none docker compose up -d --build artemis               # Letsur 키 없이 Jev만 잴 때
```

## 벤치마크

`bench/run.py`가 가상 에뮬레이터 노릇을 합니다. 실제 실행 때의 화면 상태(화면 XML, 스크린샷, 직전 동작의 결과)를 단계마다 기록해 둔 파일이 워크로드이고, 그 상태를 차례로 서버에 내밉니다.

1. 세션마다 기록된 상태를 순서대로 보냅니다. 세션끼리는 무관하므로 여러 세션을 동시에 돌립니다(`--parallel`, 기본 4).
2. 서버의 답은 실행하지 않습니다. 걸린 시간을 재고, 기록된 동작과 같은지 채점하고, 기록된 다음 상태로 넘어갑니다.
3. 다음 요청에는 기록에서 실제로 한 동작을 "해낸 것"으로 같이 보냅니다(`replay.previous`). **이력을 쌓는 일은 서버가 자기 방식으로 합니다.** 재려는 것이 그 방식입니다.

```bash
python bench/run.py --mode baseline --label baseline                 # 기준을 만든다
python bench/run.py --mode <새 방식> --against runs/bench-baseline    # 새 방식을 재서 기준과 나란히 본다
python bench/run.py --repeat 10 --mode <새 방식>                      # 같은 워크로드를 10번. 1회차는 처음 보는 작업, 그 뒤는 본 적 있는 작업
python bench/run.py a.jsonl b.jsonl --parallel 1                     # a를 끝낸 뒤 b (같은 작업을 따로 기록한 것)
python bench/run.py --detail                                         # 틀린 단계를 하나씩
python bench/run.py --resume --label baseline                        # 끊기거나 오류가 난 측정을 이어서
python bench/run.py --check-context                                  # 서버가 쌓은 이력을 실제 실행 때 모델에 들어간 것과 대조
python bench/run.py --show-prompt bluetooth:3                        # 그 단계에서 실제로 모델에 들어간 프롬프트 (서버 필요 없음)
```

내는 것은 워크로드별, 회차별로 정답 수와 정답률, 오류, 규약 위반, 단계 지연시간(중앙값, 90%, 최대), 세션당 시간, 모델 호출 수, 토큰, 비용입니다. 결과는 `runs/bench-<이름>/`에 `result.json`과 `turns.csv`로 남습니다.

- **정답**은 기록된 동작과 종류가 같고 같은 요소를 가리키는 것입니다. 다른 길로도 갈 수 있는 화면에서는 맞는 답이 틀림으로 나오므로, 절대값보다 고치기 전과 후의 차이로 봅니다.
- **오류**는 서버가 답하지 못한 단계입니다. 틀림과 따로 세고, 오류가 있는 측정은 쓰지 않습니다.
- **규약 위반**은 응답이 `docs/interface.md`를 어긴 것입니다. 앱이 읽는 방식대로 검사합니다.

### 워크로드

`bench/workloads/`에 8개, 55단계가 있습니다. 2026-11-21에 에뮬레이터(Pixel, Android 16, 한국어, 1080×2400)에서 이 서버와 앱으로 끝까지 간 세션들입니다. 누른 것은 사람 역할을 하는 시험 도구입니다.

| 파일 | 요청 | 단계 |
|---|---|---|
| `alarm-0730.jsonl`, `alarm-0730-again.jsonl` | 시계 앱에서 오전 7시 30분 알람 추가해줘 | 9, 9 |
| `bluetooth.jsonl`, `bluetooth-again.jsonl` | 설정에서 블루투스 화면 열어줘 | 6, 5 |
| `brightness.jsonl` | 화면 밝기 설정 화면을 열어줘 | 4 |
| `data-saver.jsonl` | 설정에서 데이터 절약 모드 켜줘 | 12 |
| `font-size.jsonl` | 설정에서 글꼴 크기 설정 화면 열어줘 | 4 |
| `settings-search.jsonl` | 설정에서 battery를 검색해줘 | 7 |

`*-again`은 같은 작업을 한 번 더 따로 기록한 것입니다. 캐시가 "본 적 있는 작업"을 알아보는지 보는 데 씁니다.

단계마다 그 순간의 요청 그대로(`request`), 그때의 답(`response`), 그때 실제로 모델에 들어간 프롬프트(`prompt`), 서버의 세션 기억(`session`)이 들어 있습니다. 형식은 `docs/interface.md`의 "기록"에 있습니다.

새 워크로드는 서버를 `GUIDE_RECORD_DIR=../bench/workloads`(`relay/`에서 직접 띄울 때. Docker면 `/workloads`)로 띄우고 앱에서 한 번 따라 하면 생깁니다. 화면에 보인 것이 글자와 그림으로 그대로 들어가므로, 계정 이메일 같은 것이 찍힌 기록은 올리지 않습니다.

### 지금까지 잰 것

같은 구성의 서버로 세션 셋(`bluetooth`, `brightness`, `font-size`, 14단계)을 동시에 돌린 값입니다(2026-11-21).

| | |
|---|---|
| 정답 | 12 / 13 (92%) |
| 단계 지연 | 중앙값 7.2초, 90%는 13.7초 안, 최대 21.2초 |
| 답을 내는 데 든 것 | 모델 호출 14회, 입력 93,907 토큰, 출력 4,376 토큰, 0.068 unit |
| 단계 요약까지 합치면 | 모델 호출 25회, 0.083 unit |
| 이력 대조 | 14단계 가운데 같음 9, 요약 글만 다름 5, 다름 0 |

- 틀린 한 단계는 같은 서버가 같은 상태에서 다른 동작을 고른 것입니다(기록은 `click 설정`, 이번은 `manage_app 설정`). 같은 모델도 매번 같은 답을 내지는 않으므로, 이 정도가 재는 값의 흔들림입니다.
- 이력 대조는 재생에서 서버가 쌓은 프롬프트가 실제 실행 때 모델에 들어간 것과 같다는 뜻입니다. 요약은 매번 새로 쓰이므로 글만 다릅니다.
- **판단 한 번에 7초 넘게 걸립니다. 이것이 줄일 대상입니다.**
- 워크로드 8개 전체의 베이스라인은 2026-11-21에 쟀습니다. 정답 44/47이고, 단계 하나에 3.2~30.6초, 55단계를 합쳐 546초가 걸렸습니다(`runs/bench-baseline`, 아래 "jev 방식 측정").

비용의 단위(unit)는 Letsur가 응답에 실어 주는 추정치의 단위입니다.

### jev 방식 측정

2026-11-21, 워크로드 8개(55단계, 채점 47단계), `--parallel 4`. baseline은 한 번, jev 방식은 같은 설정으로 세 번(`--repeat 3`) 잰 값입니다. 결과는 `runs/bench-baseline`, `runs/bench-jev-A-r3`, `runs/bench-jev-B-r3`, `runs/bench-jev-C-r3`에 있습니다.

| 측정 | 설정 | 정답 | Jev가 답한 단계 | LLM이 답한 단계 | 55단계 판단 시간 합계 |
|---|---|---|---|---|---|
| baseline | LLM만 | 44 | 0 | 55 | 546초 |
| A | `one`, `GUIDE_JEV_MIN_P=0.5` | 40, 41, 41 | 43~47 | 8~12 | 104~150초 |
| B (기본값) | `split` | 43, 43, 43 | 34~35 | 20~21 | 215~258초 |
| C | `split`, `GUIDE_JEV_BOX=on` | 42, 42, 42 | 36~37 | 18~19 | 213~273초 |

- 모두 Jev 1.14입니다. jev 방식의 정답은 세 회차의 값을 차례로 적었습니다.
- B는 세 번 모두 baseline보다 한 단계 낮았습니다. 그 한 단계(data-saver 11)는 기록이 좌표로, Jev가 같은 줄의 요소로 가리킨 것이라 앱에서는 같은 곳에 링이 그려집니다.
- LLM 판단 비용은 회차 하나에 baseline 0.374 unit, B 0.126 unit이었습니다. 단계 요약은 둘 다 0.059 unit, Jev는 B에서 $0.014였습니다.
- C의 box는 font-size 2단계에서만 쓰였고 세 번 모두 맞았지만, 그 밖의 단계가 흔들려 전체는 B보다 낮았습니다. 목록에 없는 곳을 누르는 단계가 하나뿐이라 box는 기본으로 꺼 두었습니다.
- 설정과 기준값은 이 47단계를 보고 정했습니다. 새로 기록한 워크로드로 다시 재야 합니다.
- `bench/jev_eval.py`는 서버 없이 Jev만 불러 질문 구성을 비교합니다(LLM이 맡는 단계는 baseline의 결과로 채움).

## 앱과 붙여 본 결과

2026-11-21에 에뮬레이터의 앱과 붙여 확인했습니다.

- "설정에서 블루투스 화면 열어줘": 4단계가 모두 사용자가 해낸 것으로 확인되고 완료까지 63초.
- "화면 밝기 설정 화면을 열어줘": 6단계, 완료까지 104초. 이때 앱이 안내 문장 7개를 TTS 서버에서 0.04~0.13초에 받아 끝까지 재생했습니다.

## 저장소의 모양

google/artemis의 커밋 `351ca84` 위에 쌓았습니다. `git log 351ca84..HEAD`가 이 팀이 더한 것입니다.

| 자리 | 누구 것 | 무엇 |
|---|---|---|
| `artemis/`, `third_party/`, `config/` 등 | google/artemis | 그대로입니다. 고치면 서버에 반영됩니다(`ARTEMIS_HOME`) |
| `relay/server.py` | 이 팀 | HTTP 서버. 요청 하나를 artemis 한 턴으로 돌립니다. 기록, 재생, 방식 고르기 |
| `relay/artemis_bridge.py` | 이 팀 | artemis의 함수와 프롬프트를 부르는 곳. 사람이 직접 하기 때문에 바꾼 것(`PROMPT_EDITS`)이 이유와 함께 적혀 있습니다 |
| `relay/backends.py` | 이 팀 | 모델 호출 한 곳. Letsur, 다시 부르기, 사용량 세기 |
| `relay/pyproject.toml`, `relay/uv.lock` | 이 팀 | 서버의 파이썬 환경 |
| `relay/Dockerfile`, `docker-compose.yml` | 이 팀 | Docker로 띄우는 방법 |
| `bench/` | 이 팀 | 벤치마크와 워크로드 |
| `docs/interface.md` | 이 팀 | 세 저장소가 같이 가지는 규약 |
| `docs/prompt.md`, `docs/local.md`, `docs/docker.md` | 이 팀 | 이 저장소를 만들고 띄우는 데 쓴 프롬프트 |
| `README.artemis.md` | google/artemis | artemis의 원래 README |

artemis의 파일 가운데 고친 것은 `README.md`(→ `README.artemis.md`로 옮김), `.gitignore`와 `.dockerignore`(끝에 몇 줄 더함)입니다.

### artemis에서 그대로 쓰는 것

화면 XML을 요소 목록으로 바꾸는 과정(`parse_hierarchy_xml_to_elements` → `filter_ui_hierarchy` → `fuse_ocr_with_xml` → `format_minimal_list_with_elements`), 판단 프롬프트(`artemis/agents/flash/flash_runner.md`), 도구 설명(`artemis/mcp/action_specs.py`), 이력을 줄이는 기준(요소 목록은 지금 관찰에만, 스크린샷은 최근 셋까지, 그보다 오래된 것은 요약), 단계 요약의 프롬프트와 빨간 표시, 위치 찾기의 프롬프트입니다.

### 사람이 직접 하기 때문에 바꾼 것

- 도구에서 `click_sequence`를 뺐습니다(사람은 한 번에 한 곳을 누릅니다).
- 프롬프트의 세 문장을 고쳤습니다(생각을 길게 쓰는 대신 `memo`에 적기, `click_sequence` 설명 빼기, 앱 아이콘이 화면에 없으면 찾아가서 누르기).
- 사용자가 직접 동작한다는 것과 결과로 오는 말의 뜻을 프롬프트에 더했습니다.
- 답은 JSON 하나(`memo`, `explain`, `action`)로 받습니다.

## 환경 변수

| 이름 | 기본값 | 뜻 |
|---|---|---|
| `LETSUR_API_KEY` | (없음) | 자기 Letsur 키. `relay/.env`에 넣습니다 |
| `GUIDE_PROFILE` | `artemis` | 모델 구성. `artemis`, `luna`, `haiku` |
| `GUIDE_MODE` | `baseline` | 요청에 `mode`가 없을 때 답하는 방식 |
| `GUIDE_HOST`, `GUIDE_PORT` | `127.0.0.1`, `8765` | 듣는 주소 |
| `GUIDE_CLI_TIMEOUT` | `120`(Docker에서는 `55`) | 모델 호출 하나를 기다리는 시간(초) |
| `GUIDE_SUMMARY_WORKERS` | `2` | 단계 요약을 동시에 만드는 수. artemis의 값입니다 |
| `GUIDE_RECORD_DIR` | (없음) | 주면 단계마다 요청, 프롬프트, 응답을 그 폴더에 세션별 파일로 적습니다 |
| `ARTEMIS_HOME` | (없음) | artemis 소스가 있는 폴더. 주면 설치된 패키지 대신 그 코드를 씁니다 |

Cloudflare AI Gateway를 거쳐 Letsur를 부를 때는 `LETSUR_API_KEY` 대신 `LETSUR_GATEWAY_URL`과 `AI_GATEWAY_TOKEN`을 넣습니다(`relay/.env.example`).

## 안 될 때

| 증상 | 볼 것 |
|---|---|
| `/health`의 `letsur`가 `missing` | `relay/.env`에 키가 없습니다. 넣고 서버를 다시 띄웁니다 |
| 요청에 502와 `letsur 401`/`403` | 키가 틀렸습니다 |
| 요청에 502와 `Name or service not known` | Docker 컨테이너의 DNS입니다. Docker 없이 띄우거나(위 2번) 다시 시도합니다 |
| 벤치마크에 오류가 가끔 하나씩 | 모델 쪽 연결이 끊긴 것입니다. `--resume`으로 그 세션만 다시 잽니다. 잦으면 `--parallel 2` |
| 8765 포트를 이미 쓰고 있다 | 직접 띄운 서버와 Docker 컨테이너 가운데 하나만 켭니다 |
| 한글이 깨진다 | `python -X utf8 ...` |

## 알려진 한계

- 판단이 느립니다(단계당 7초 이상). 가끔 모델이 1분 넘게 답하지 않습니다.
- 지금 있는 방식은 `baseline` 하나입니다. 같은 작업이 다시 왔을 때 빠르고 싸게 답하는 방식을 더하는 것이 이 저장소의 남은 일입니다.
- 세션 기억은 메모리에만 있습니다. 서버를 다시 띄우면 따라 하던 세션은 처음부터 다시 시작해야 합니다.
- artemis 자체의 설정(`config/artemis.jsonc`)의 프로바이더는 바꾸지 않았습니다. 이 서버는 `relay/backends.py`로 Letsur를 직접 부릅니다. `artemis run` 같은 artemis 원래의 실행 방식은 Google 키가 있어야 돕니다.
- 워크로드는 에뮬레이터 한 대에서 기록한 8개입니다.

## 라이선스

google/artemis와 같은 Apache-2.0입니다([`LICENSE`](LICENSE)). artemis의 저작권 표시는 그대로 두었습니다.
