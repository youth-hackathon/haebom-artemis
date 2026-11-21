# artemis 서버 팀 프롬프트: 내 컴퓨터에서 띄우고 재기

이 문서를 그대로 코딩 에이전트에게 줍니다. artemis 서버를 맡은 사람이 자기 컴퓨터에서 한 번 실행합니다. Docker가 없으면 `docs/docker.md`부터 합니다.

---

## 할 일

이 컴퓨터에서 artemis 서버를 띄우고, 테스트 픽스처로 벤치마크를 돌릴 수 있게 합니다. 폰도 에뮬레이터도 필요 없고, 서버를 어디에 올릴 필요도 없습니다.

끝나면 이 셋이 됩니다.

1. `http://127.0.0.1:8765/health`가 답한다.
2. 워크로드 하나를 벤치마크로 돌려 정답 수와 지연시간이 나온다.
3. 코드를 고치고 다시 재서 앞의 결과와 나란히 볼 수 있다.

## 지킬 것

- 모델 키는 `relay/.env`에만 둡니다. 화면에 출력하지 않고, 커밋하지 않고, 로그나 다른 파일에 옮겨 적지 않습니다. 키가 들어 있는지는 이름만으로 확인합니다.
- `bench/workloads/`의 파일은 고치지 않습니다. 모두가 같은 것으로 재야 숫자를 비교할 수 있습니다.
- `docs/interface.md`에 적힌 요청과 응답의 형식은 바꾸지 않습니다. 앱과 합의한 내용입니다.
- 모르는 것은 추측해서 진행하지 말고 사람에게 묻습니다(키).

## 1. 준비물 확인

- `docker version`, `docker compose version`: 안 되면 `docs/docker.md`를 먼저 합니다.
- `git --version`
- `python --version`: 3.10 이상. 벤치마크는 파이썬 표준 라이브러리만 씁니다. 따로 설치할 것이 없습니다.

## 2. 저장소

- `https://github.com/youth-hackathon/haebom-artemis`를 받습니다. 이미 있으면 최신으로 맞춥니다.
- 아래가 있는지 봅니다. 없는 것이 있으면 무엇이 없는지 알리고 멈춥니다(서버가 아직 없으면 `docs/prompt.md`가 먼저입니다).

| 있어야 하는 것 | 무엇 |
|---|---|
| `relay/` | artemis 서버 |
| `relay/Dockerfile`, `docker-compose.yml` | 서버를 띄우는 방법. 서비스 이름은 `artemis`, 포트는 8765 |
| `bench/run.py` | 벤치마크 |
| `bench/workloads/*.jsonl` | 테스트 픽스처 |
| `docs/interface.md` | 서버가 받는 것과 돌려주는 것 |

## 3. 모델 키

서버는 모델을 Letsur로 부릅니다. 키는 `relay/.env`에서 읽습니다.

1. `git check-ignore relay/.env`로 이 파일이 git에 들어가지 않는지 확인합니다. 아무것도 출력되지 않으면 `.gitignore`에 `relay/.env`를 더합니다.
2. `relay/.env`가 없으면 `relay/.env.example`을 복사해 만듭니다.
3. 사람에게 키를 받아 아래 둘 중 하나를 채웁니다. 어느 것을 쓰는지는 팀장이 알려 줍니다.
   - `LETSUR_API_KEY`: Letsur를 직접 부를 때
   - `AI_GATEWAY_TOKEN`: 팀의 Cloudflare AI Gateway를 거쳐 부를 때. 경로 주소가 기본값과 다르면 `LETSUR_GATEWAY_URL`도 넣습니다
4. 사람이 파일을 직접 열어 넣는 것이 가장 안전합니다. 에이전트가 대신 쓸 때는 받은 값을 파일에만 쓰고 다시 출력하지 않습니다.

## 4. 서버 띄우기

```bash
docker compose up -d --build artemis
```

- 처음에는 몇 분 걸립니다. artemis와 그 의존성(OpenCV 등)을 받아 설치합니다. 이미지는 2GB를 조금 넘습니다. 두 번째부터는 코드만 다시 넣으므로 몇 초입니다.
- 떴는지는 `http://127.0.0.1:8765/health`로 봅니다. `profile`이 `artemis`이고 `planner`가 `letsur:gemini-3.8-flash`면 베이스라인 구성입니다. `modes`에 서버가 가진 방식이, `default_mode`에 기본 방식이 나옵니다.
- 로그는 `docker compose logs -f artemis`로 봅니다.

## 5. 워크로드 하나로 확인

```bash
python bench/run.py bench/workloads/brightness.jsonl --label smoke
```

4단계짜리이고 30초쯤 걸립니다. 비용은 0.02 unit쯤입니다(단위는 Letsur의 것).

정답 수, 단계 지연시간, 모델 호출 수, 토큰, 비용이 나오면 된 것입니다.

"서버가 답하지 못한 단계"가 나오면 키나 통신 문제입니다. `docker compose logs artemis --tail 20`에서 이유를 봅니다. `letsur 401`이나 `403`이면 키가 틀렸거나 비어 있는 것이고, 키를 고친 뒤에는 `docker compose up -d artemis`로 서버를 다시 띄워야 읽힙니다.

## 6. 전체를 재서 기준 만들기

```bash
python bench/run.py --mode baseline --label baseline
```

워크로드 8개, 55단계입니다. 단계 하나에 7~10초가 걸리므로 세션을 4개씩 동시에 돌려 5분 안팎이고, 비용은 요약까지 합쳐 0.5 unit 안팎입니다. 사람이 원하지 않으면 건너뛰고 그렇게 했다고 알립니다.

- 세션을 4개씩 동시에 돌립니다(`--parallel`). 세션 안의 단계는 차례로 갑니다. 오류가 나면 `--parallel 2`로 줄입니다.
- 결과는 `runs/bench-baseline/`에 남습니다. `result.json`이 전체이고 `turns.csv`가 단계별 표입니다.
- 도중에 끊기거나 오류가 나면 `python bench/run.py --label baseline --resume`으로 이어서 잽니다. 오류 없이 끝난 세션은 다시 재지 않습니다.
- "오류"나 "규약 위반"이 0이 아니면 그 측정은 기준으로 쓰지 않습니다.
- 지연시간을 정확히 보려면 `--parallel 1`로 한 번 더 잽니다. 동시에 돌리면 모델 쪽이 밀려 조금 늘 수 있습니다. 비교는 같은 `--parallel`로 잰 것끼리 합니다.

## 7. 고치고 다시 재기

이것이 하루 동안 되풀이할 일입니다.

```bash
# 1. relay/ 아래의 코드를 고친다
docker compose up -d --build artemis                                              # 2. 다시 띄운다 (몇 초)
python bench/run.py --mode cache --label cache --against runs/bench-baseline      # 3. 새 방식을 재고, 기준과 나란히 본다
```

서버는 베이스라인과 새 방식을 둘 다 가지고 있고 요청이 고릅니다(`docs/interface.md`의 "베이스라인과 새 방식"). `--mode`에는 `/health`의 `modes`에 나온 이름을 줍니다. 새 방식을 더한 뒤에도 `--mode baseline`은 처음과 같은 답을 내야 합니다. 가끔 다시 재서 확인합니다.

- 빨리 보고 싶으면 워크로드를 골라서 잽니다: `python bench/run.py bench/workloads/bluetooth.jsonl bench/workloads/brightness.jsonl --label try`
- 캐시는 같은 서버에 여러 번 넣어 봅니다: `python bench/run.py --mode cache --repeat 3 --label cache-try`. 1회차는 처음 보는 작업이고 2회차부터는 본 적 있는 작업입니다. 처음부터 다시 재려면 서버를 다시 띄웁니다(`docker compose restart artemis`).
- 같은 작업을 따로 기록한 것끼리는 순서대로 넣습니다: `python bench/run.py bench/workloads/bluetooth.jsonl bench/workloads/bluetooth-again.jsonl --parallel 1 --label transfer`
- 틀린 단계를 하나씩 보려면 `--detail`을 붙입니다.
- 이력을 쌓는 방법을 고쳤으면 `--check-context`로 모델에 들어가는 글이 기록과 어디서 달라졌는지 봅니다. 한 단계의 기록된 프롬프트는 `--show-prompt bluetooth:3`으로 봅니다.
- 정답률이 기준보다 떨어지면 그 변경은 넣지 않습니다.

## 8. Docker 없이 바로 돌리기 (선택)

고칠 때마다 이미지를 다시 만들지 않고 싶으면 서버를 직접 띄웁니다. `uv`가 필요합니다.

```bash
uv sync                                               # 처음 한 번
GUIDE_PROFILE=artemis uv run python relay/server.py   # 127.0.0.1:8765
```

Docker로 띄운 서버와 같은 포트를 쓰므로 둘 중 하나만 켭니다(`docker compose stop artemis`).

## 9. 새 워크로드 기록하기 (폰이나 에뮬레이터가 있을 때만)

```bash
GUIDE_RECORD_DIR=/workloads docker compose up -d artemis
```

이렇게 띄운 뒤 앱에서 한 번 따라 하면 `bench/workloads/<세션 id>.jsonl`이 생깁니다. 끝까지 간 세션만 남기고, 파일 이름을 작업을 알 수 있는 이름으로 바꿉니다. 화면에 보인 것이 그대로 들어가므로 남의 폰에서 기록한 것은 내용을 확인한 뒤에 올립니다. 기록을 끝내면 `GUIDE_RECORD_DIR` 없이 다시 띄웁니다.

## 안 될 때

| 증상 | 볼 것 |
|---|---|
| 8765 포트를 이미 쓰고 있다고 나온다 | 직접 띄운 서버나 다른 컨테이너가 떠 있는지. 하나를 끕니다 |
| `relay/.env`가 없다고 나온다 | 3번 |
| 이미지를 만들다가 실패한다 | 인터넷. 다시 실행하면 이어서 받습니다 |
| 벤치마크의 모든 단계가 오류다 | 키, 또는 컨테이너의 통신. `docker compose restart artemis` 뒤에 `--resume`으로 다시 |
| 오류가 가끔 하나씩 난다 | 모델 쪽 연결이 끊긴 것입니다. `--resume`으로 그 세션만 다시 잽니다 |
| 서버 로그에 `Name or service not known`이 나온다 | 컨테이너의 DNS입니다. `docker-compose.yml`의 `artemis`에 `dns`(`8.8.8.8`, `1.1.1.1`)가 있는지 보고, 그래도 안 되면 8번처럼 서버를 직접 띄웁니다 |
| 한글이 깨진다 | `python -X utf8 bench/run.py ...` |
| Git Bash에서 `docker exec`의 경로가 이상하다 | 명령 앞에 `MSYS_NO_PATHCONV=1` |

## 끝나면 알려 줄 것

- `/health`의 답(모델 이름)
- 5번의 결과: 정답 수, 걸린 시간, 모델 호출 수
- 6번을 했으면 전체의 정답률, 단계 지연시간 중앙값, 비용, 오류와 규약 위반의 수. 안 했으면 안 했다고
- 사람이 직접 한 일과 남은 일
