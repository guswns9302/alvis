# Alvis 설계 문서

## 1. 문서 목적

이 문서는 `alvis`의 초기 아키텍처를 정의한다. `alvis`는 여러 개의 AI 에이전트를 하나의 팀으로 다루는 오케스트레이터이며, 각 에이전트는 독립된 `Codex CLI` 세션으로 실행된다.

핵심 방향은 다음과 같다.

- 제어 평면은 `LangGraph`가 담당한다.
- 실행 및 관찰 UI는 `tmux`가 담당한다.
- 실제 상태, 메시지, 이력의 진실 원본은 별도 저장소가 가진다.
- 각 Codex 세션은 하나의 독립 actor로 취급한다.

이 문서는 `cokacdir`와 `Ensemble` 문서에서 확인한 패턴을 참고하되, `alvis`에서는 이를 더 명시적으로 분리된 아키텍처로 정리한다.

---

## 2. 핵심 설계 원칙

### 2.1 tmux는 UI이자 세션 제어 계층이다

`tmux`는 다음 역할만 가진다.

- 에이전트별 터미널 세션 실행
- 리더와 워커 화면 분할
- 특정 에이전트 세션에 텍스트 입력 주입
- 운영자가 현재 상태를 시각적으로 관찰

`tmux`가 담당하면 안 되는 역할은 다음과 같다.

- 대화 이력 저장소
- 작업 상태의 진실 원본
- 메시지 라우팅 버스
- 워크플로 의사결정 엔진

즉, `tmux`는 execution UI이며 transport bus가 아니다.

### 2.2 LangGraph는 control plane이다

`LangGraph`는 다음을 담당한다.

- 사용자 요청 수신
- 작업 분해
- 에이전트 선택 및 할당
- 재시도, 타임아웃, 실패 전파
- 사람 승인 지점에서 interrupt
- 워커 결과 종합
- 팀 전체 종료 조건 판단

중요한 점은, LangGraph가 개별 코딩 작업을 직접 수행하는 것이 아니라 외부 actor인 Codex 세션들을 조정하는 역할을 맡는다는 것이다.

### 2.3 상태 저장소가 source of truth다

실행 중인 팀의 상태는 터미널 화면이 아니라 데이터 저장소에서 복원 가능해야 한다.

따라서 다음 정보는 모두 영속화한다.

- 팀 정보
- 에이전트 정보
- 작업 정보
- 작업 할당 정보
- 이벤트 로그
- 리뷰 요청 및 승인 상태
- 세션 메타데이터
- LangGraph 체크포인트 메타데이터

### 2.4 에이전트는 장기 실행 actor다

각 `Codex CLI` 세션은 독립된 actor로 동작한다.

- 자체 작업 디렉터리와 컨텍스트를 가진다.
- 특정 task contract를 받아 실행한다.
- 결과를 이벤트로 보고한다.
- 재시작 가능해야 한다.

### 2.5 사람 승인 흐름은 1급 기능이다

아래 작업은 기본적으로 사람 승인 흐름을 거치도록 설계한다.

- `git commit`
- `git push`
- 대량 삭제 또는 파일 이동
- 브랜치 병합
- 장시간 재시도
- 계획 변경이 큰 경우

이는 `LangGraph interrupt`와 resume 메커니즘을 활용해 구현한다.

---

## 3. 목표 시스템 개요

`alvis`는 단일 머신에서 여러 `Codex CLI` 세션을 `tmux` pane으로 실행하고, `LangGraph` 기반 리더 오케스트레이터가 작업을 분배하는 구조를 가진다.

상위 수준 데이터 흐름은 다음과 같다.

```text
사용자 요청
  ↓
Alvis Server
  ↓
LangGraph Supervisor
  ↓
Task 생성 / Agent 선택 / Dispatch
  ↓
Codex Adapter → tmux pane 입력 주입
  ↓
Codex CLI 세션 실행
  ↓
출력 수집 / 상태 갱신 / 이벤트 기록
  ↓
Supervisor가 다음 단계 결정
  ↓
필요 시 Review Interrupt
  ↓
최종 결과 정리
```

---

## 4. 주요 구성 요소

## 4.1 Alvis Server

서버 프로세스는 시스템 전체의 중심 제어점이다.

주요 책임:

- LangGraph graph 실행
- 세션 관리자 호출
- Codex adapter와의 통신
- 이벤트 저장
- review gate 처리
- CLI 또는 API 제공
- 재시작 시 상태 복원

권장 구현:

- `Python 3.12`
- `FastAPI`
- `asyncio`
- `Typer`

## 4.2 LangGraph Supervisor

Supervisor graph는 팀 단위 작업의 오케스트레이션을 담당한다.

주요 책임:

- 사용자 목표를 task 단위로 분해
- 적절한 에이전트를 선택
- task assignment 생성
- 워커의 진행 상태 관찰
- 실패, 지연, block 상태 처리
- 결과 통합
- 사람 승인 지점에서 interrupt 발생

초기에는 supervisor 패턴 하나로 충분하며, 이후 다음과 같이 분화할 수 있다.

- planner subgraph
- coding subgraph
- review subgraph
- synthesis subgraph

## 4.3 Session Manager

`tmux` 세션 및 pane의 생명주기를 관리한다.

주요 책임:

- 팀 생성 시 tmux 세션 생성
- 리더/워커 pane 분할
- pane별 Codex CLI 프로세스 실행
- pane 재연결
- pane 종료 및 정리
- pane 메타데이터 저장

Session Manager는 `tmux` 명령을 직접 다루는 낮은 수준 래퍼 계층을 가진다.

예상 기능:

- `create_team_layout(team_id, agents)`
- `spawn_agent_pane(agent_id, worktree_path)`
- `send_input(agent_id, text)`
- `focus_agent(agent_id)`
- `capture_debug_snapshot(agent_id)`

## 4.4 Codex Adapter

Codex Adapter는 `alvis`와 실제 Codex CLI 세션 사이의 표준화 계층이다.

주요 책임:

- task prompt를 표준 포맷으로 구성
- pane에 안전하게 입력 주입
- 출력 수집
- heartbeat 기록
- 작업 시작/종료 marker 관리
- 에이전트 상태 이벤트 발행

중요한 점:

- 입력은 `send-keys`보다 파일 기반 paste 방식을 우선 고려한다.
- 출력 수집의 진실 원본은 래퍼가 별도 로그/이벤트 저장소에 남긴 결과여야 한다.
- `capture-pane`는 디버깅 보조 수단으로만 사용한다.

## 4.5 Workspace Manager

에이전트별 작업공간 격리를 담당한다.

권장 방식:

- `git worktree` 사용
- agent별 worktree 경로 분리
- agent별 branch 분리
- 충돌 가능성 최소화

예시:

- leader: 메인 작업 디렉터리
- worker-1: `.worktrees/team-a-worker-1`
- worker-2: `.worktrees/team-a-worker-2`

주요 책임:

- worktree 생성 및 삭제
- branch naming 정책 관리
- 에이전트별 cwd 설정
- 충돌 감지

## 4.6 Review Gate

사람 승인 흐름을 담당한다.

주요 책임:

- 승인 대상 작업 감지
- review request 생성
- 사용자 승인/수정/거절 수신
- LangGraph resume
- 승인 결과 이벤트 기록

초기에는 CLI 기반 승인 흐름으로 충분하다.

## 4.7 Event Store

시스템 전체의 상태 추적과 복구를 위한 중심 저장소다.

초기 권장 구현:

- `SQLite`

확장 시 권장 구현:

- `Postgres`

이벤트는 append-only 방식으로 저장한다.

---

## 5. 데이터 흐름

## 5.1 사용자 요청 처리 흐름

```text
사용자
  ↓
alvis CLI / API
  ↓
Alvis Server
  ↓
LangGraph Supervisor
  ↓
Task 생성
  ↓
Agent 선택
  ↓
Codex Adapter가 해당 tmux pane에 task 주입
  ↓
Codex 세션 실행
  ↓
출력 수집 및 이벤트 저장
  ↓
Supervisor가 상태를 보고 다음 단계 결정
  ↓
필요 시 추가 작업 할당 또는 리뷰 요청
  ↓
최종 응답 생성
```

## 5.2 에이전트 메시지 처리 흐름

`alvis`에서 에이전트 간 협업은 tmux pane 사이 직접 대화가 아니라 오케스트레이터를 통한 조정으로 본다.

기본 흐름:

```text
Worker A 결과 보고
  ↓
Event Store에 기록
  ↓
Supervisor가 결과 해석
  ↓
필요 시 Worker B에 새로운 task 할당
  ↓
Worker B는 직접 A와 대화하지 않고 Supervisor가 정리한 컨텍스트를 받음
```

즉, 기본 모델은 supervisor-mediated communication이다.

필요 시 향후 직접 agent-to-agent task handoff도 추가할 수 있으나, 초기에는 복잡도를 줄이기 위해 Supervisor 중심 라우팅으로 제한한다.

## 5.3 리뷰 흐름

```text
에이전트가 commit 필요 상태 도달
  ↓
Review Gate 이벤트 생성
  ↓
LangGraph interrupt
  ↓
사용자 승인 / 수정 / 거절
  ↓
graph resume
  ↓
다음 작업 진행
```

---

## 6. LangGraph 그래프 설계

## 6.1 초기 그래프 노드 제안

초기 MVP 그래프는 아래 노드들로 충분하다.

1. `ingest_request`
2. `plan_tasks`
3. `select_agents`
4. `dispatch_tasks`
5. `wait_for_updates`
6. `evaluate_progress`
7. `request_review`
8. `synthesize_result`
9. `finish`

## 6.2 노드 역할

### `ingest_request`

- 사용자 입력 수신
- 팀 컨텍스트 로드
- 새 run 생성

### `plan_tasks`

- 요청을 task 단위로 분해
- 병렬화 가능한 작업 식별
- 각 task의 expected output 정의

### `select_agents`

- 역할, 현재 부하, 작업공간 상태를 기준으로 agent 배치

### `dispatch_tasks`

- assignment 생성
- Codex Adapter를 통해 작업 전달

### `wait_for_updates`

- 이벤트 스트림을 관찰
- 완료, 실패, 대기, 리뷰 요청 상태를 감지

### `evaluate_progress`

- 다음 행동 결정
- 재할당, 추가 task 생성, 종료 여부 판단

### `request_review`

- interrupt 기반 사람 승인 요청

### `synthesize_result`

- 워커 산출물 병합
- 사용자에게 보여줄 최종 결과 정리

### `finish`

- run 종료
- 최종 상태 저장

## 6.3 상태 객체 제안

LangGraph state는 최소 아래 정보를 포함한다.

```python
class AlvisRunState(TypedDict):
    run_id: str
    team_id: str
    user_request: str
    tasks: list[dict]
    assignments: list[dict]
    completed_tasks: list[dict]
    blocked_tasks: list[dict]
    review_requests: list[dict]
    final_response: str | None
    status: str
```

실제 구현에서는 더 세분화된 schema를 둘 수 있으나, 초기에는 단순한 구조가 좋다.

---

## 7. 에이전트 모델

각 Codex 세션은 아래 메타데이터를 가진다.

| 필드 | 설명 |
|------|------|
| `agent_id` | 에이전트 고유 식별자 |
| `team_id` | 소속 팀 |
| `role` | leader, implementer, reviewer 등 |
| `provider` | codex |
| `model` | 세션의 모델 정보 |
| `tmux_session` | tmux 세션명 |
| `tmux_pane` | tmux pane id |
| `cwd` | 현재 작업 디렉터리 |
| `git_branch` | 현재 브랜치 |
| `git_worktree_path` | worktree 경로 |
| `status` | 에이전트 상태 |
| `current_task_id` | 현재 작업 |
| `last_heartbeat_at` | 마지막 heartbeat |

권장 상태 값:

- `idle`
- `assigned`
- `running`
- `waiting_input`
- `waiting_review`
- `blocked`
- `done`
- `failed`

---

## 8. 작업 모델

## 8.1 Task Contract

에이전트에 전달되는 작업은 자유 텍스트가 아니라 표준 계약 형태를 가져야 한다.

예시:

```text
[ALVIS TASK]
task_id: task_123
role: implementer
cwd: /path/to/worktree
goal: Fix flaky test in billing module
constraints:
- Do not push
- Ask for review before commit
expected_output:
- Summary
- Changed files
- Test results
```

Task Contract의 목적:

- 작업 경계 명확화
- 에이전트 간 책임 분리
- 결과 포맷 일관성 확보
- supervisor의 후처리 단순화

## 8.2 Task 상태

권장 task 상태:

- `created`
- `assigned`
- `running`
- `waiting_review`
- `blocked`
- `done`
- `failed`
- `cancelled`

---

## 9. 데이터 모델

초기 저장소는 `SQLite`로 시작한다.

권장 테이블:

## 9.1 `teams`

- 팀 정보
- 기본 layout
- 생성 시각

## 9.2 `agents`

- 에이전트 메타데이터
- 세션 연결 상태

## 9.3 `tasks`

- task 정의
- 우선순위
- 상태

## 9.4 `task_assignments`

- 어떤 task가 어떤 agent에 할당되었는지
- 할당 시각
- 완료 시각

## 9.5 `events`

- append-only 이벤트 로그

권장 이벤트 타입:

- `run.created`
- `task.created`
- `task.assigned`
- `agent.prompt.sent`
- `agent.output.delta`
- `agent.output.final`
- `agent.status.changed`
- `review.requested`
- `review.approved`
- `review.rejected`
- `session.started`
- `session.exited`
- `error.raised`

## 9.6 `reviews`

- 승인 요청 내용
- 대상 action
- 승인 상태
- 코멘트

## 9.7 `sessions`

- tmux 세션 정보
- pane 정보
- 시작/종료 시각

---

## 10. tmux 설계

## 10.1 기본 방향

`tmux`는 실행 환경을 시각적으로 배치하는 UI로 본다.

초기에는 두 가지 layout만 제공한다.

- `leader-focus`
- `grid`

## 10.2 권장 레이아웃

### `leader-focus`

- leader pane 1개를 크게 배치
- worker pane N개를 작은 영역에 배치
- 선택적으로 이벤트 로그 pane 1개 추가

### `grid`

- 전체 worker를 균등 분할
- 디버깅이나 병렬 작업 관찰에 적합

## 10.3 tmux 사용 규칙

- 입력 주입은 가능하면 파일 기반 paste 사용
- `capture-pane`는 디버깅용
- pane 내용은 source of truth가 아님
- pane id와 agent id를 항상 매핑 저장

---

## 11. 작업공간 격리 전략

여러 에이전트가 같은 저장소를 동시에 수정하므로 작업공간 격리는 필수다.

권장 정책:

- agent별 `git worktree`
- branch naming 규칙 표준화
- 리더는 기본 worktree 또는 별도 supervisor worktree 사용 가능

예시 branch 규칙:

- `alvis/<team_id>/<agent_id>`

예시 디렉터리:

```text
.worktrees/
  team-a-leader/
  team-a-worker-1/
  team-a-worker-2/
```

이 전략의 장점:

- 파일 충돌 감소
- 독립적인 테스트 가능
- 실패한 작업의 폐기 쉬움
- 리뷰와 병합 흐름 단순화

---

## 12. 승인 및 안전장치

초기 안전장치는 반드시 아래를 포함해야 한다.

### 12.1 승인 필요 작업

- `git commit`
- `git push`
- 파일 삭제
- 대규모 리팩터링
- branch merge

### 12.2 운영 안전장치

- heartbeat
- task timeout
- retry limit
- agent status reconciliation
- 리뷰 대기 중 추가 destructive action 차단

### 12.3 상태 불일치 대응

다음과 같은 불일치가 발생할 수 있다.

- tmux pane은 살아 있지만 Codex 프로세스가 종료됨
- task는 running인데 실제로 응답이 없음
- agent가 작업 범위를 벗어나 수정 시도

대응 방안:

- heartbeat 기반 비정상 세션 탐지
- timeout 후 supervisor 재할당
- task contract 재강조
- 작업공간 diff 기반 범위 점검

---

## 13. 관측성과 디버깅

초기에도 최소 관측성은 확보해야 한다.

권장 항목:

- structured logging
- run 단위 correlation id
- agent별 heartbeat
- task lifecycle 로그
- review lifecycle 로그

권장 도구:

- `structlog`
- `LangSmith` 또는 `OpenTelemetry`

운영자가 빠르게 봐야 할 정보:

- 현재 실행 중인 run
- 각 agent 상태
- 마지막 출력 시각
- 리뷰 대기 상태
- 실패한 task 목록

---

## 14. 초기 기술 스택

권장 기술 스택은 다음과 같다.

- 언어: `Python 3.12`
- 오케스트레이터: `LangGraph`
- API: `FastAPI`
- CLI: `Typer`
- 비동기 런타임: `asyncio`
- 데이터 저장: `SQLite` -> `Postgres`
- ORM: `SQLAlchemy` 또는 `SQLModel`
- 로깅: `structlog`
- 관측성: `LangSmith` 또는 `OpenTelemetry`
- 세션 실행: `tmux`

Python을 선택하는 이유:

- LangGraph와의 결합이 자연스럽다.
- 제어 평면 구현 속도가 빠르다.
- 비동기 orchestration 코드를 작성하기 쉽다.

---

## 15. 권장 디렉터리 구조

```text
alvis/
  app/
    api/
    core/
    graph/
    agents/
    sessions/
    reviews/
    workspace/
    db/
    schemas/
  scripts/
  tests/
  migrations/
  document/
```

핵심 파일 예시:

- `app/graph/supervisor.py`
- `app/graph/state.py`
- `app/agents/codex_adapter.py`
- `app/sessions/tmux_manager.py`
- `app/workspace/worktree_manager.py`
- `app/reviews/gate.py`
- `app/db/models.py`

---

## 16. MVP 범위

초기 MVP는 기능을 좁게 유지해야 한다.

포함 범위:

1. 단일 머신 실행
2. leader 1명 + worker 2명
3. tmux 세션/레이아웃 자동 생성
4. Codex CLI 세션 실행
5. SQLite 기반 이벤트 로그
6. LangGraph supervisor
7. review interrupt
8. agent별 worktree 격리

제외 범위:

- 웹 대시보드
- 원격 호스트 분산 실행
- 복잡한 agent-to-agent 직접 프로토콜
- 고급 자동 merge
- 다중 저장소 동시 오케스트레이션

---

## 17. 구현 순서 제안

구현은 아래 순서가 적절하다.

1. `tmux` 세션 생성기
2. `Codex Adapter`
3. `SQLite event store`
4. `LangGraph supervisor`
5. `Review Gate`
6. `Workspace Manager`
7. 최소 CLI/API
8. 재시작 복구 로직

이 순서를 따르는 이유는 실행 기반과 상태 저장이 먼저 안정화되어야 이후 오케스트레이션이 의미를 가지기 때문이다.

---

## 18. 주요 리스크

### 18.1 Codex CLI 출력 파싱 불안정성

출력 포맷이 완전히 구조화되어 있지 않을 수 있다.

대응:

- 래퍼 marker 사용
- task 종료 조건 명시
- partial/final 이벤트를 단순 규칙으로 구분

### 18.2 동시 수정 충돌

여러 에이전트가 같은 파일 영역을 수정할 수 있다.

대응:

- worktree 분리
- task scope 제한
- leader 중심 통합

### 18.3 세션 상태와 실제 상태 불일치

tmux pane이 살아 있어도 Codex가 멈췄을 수 있다.

대응:

- heartbeat
- 주기적 상태 reconciliation

### 18.4 과도한 자동화

자동 commit, push, merge까지 한 번에 열면 운영 위험이 커진다.

대응:

- review gate 기본 활성화
- destructive action 기본 차단

---

## 19. 결론

`alvis`는 단순한 멀티 tmux 실행기가 아니라, `LangGraph`를 control plane으로 사용하는 팀형 AI 오케스트레이터로 설계해야 한다.

가장 중요한 네 가지 원칙은 다음과 같다.

- `tmux`는 UI다.
- `Codex CLI` 세션은 actor다.
- `LangGraph`는 orchestration control plane이다.
- `DB/Event Store`가 source of truth다.

이 원칙을 지키면 `alvis`는 단일 머신 기반 MVP에서 시작해, 이후 더 정교한 멀티 에이전트 협업 환경으로 확장할 수 있다.
