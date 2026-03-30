# Ensemble: 멀티 에이전트 대화 흐름과 tmux 역할

Ensemble 프로젝트에서 tmux를 쓰는 방식과, 에이전트 간 대화가 어떻게 전달되는지 정리한 문서이다. 분석 기준은 `ensemble` 저장소(예: `work/git/ensemble`)의 `services/ensemble-service.ts`, `lib/agent-spawner.ts`, `lib/agent-runtime.ts`, `lib/ensemble-registry.ts`, `scripts/team-say.sh`, `scripts/team-read.sh`, `scripts/ensemble-bridge.sh`, `docs/architecture.md` 등이다.

---

## 핵심 요약

- **에이전트 간 “대화 흐름”은 tmux로 직접 주고받지 않는다.**
- **tmux**는 (1) 각 AI CLI를 돌리는 **격리된 터미널 세션**이고, (2) 오케스트레이터가 **새 메시지를 해당 CLI 입력으로 넣을 때** 쓰는 **입력 주입 채널**이다.
- **기록·라우팅·조회**는 **JSONL 파일 + HTTP API**가 담당한다.

---

## tmux의 역할

### 세션 이름

- 팀 생성 시 에이전트마다 세션 이름은 **`${team.name}-${agent.name}`** 형태로 쓰인다.
- 로컬 스폰 시 `spawnLocalAgent`에 전달되는 `name`은 `ensemble-service`에서 `const agentName = \`${team.name}-${agentSpec.name}\``로 만들어진다.
- `lib/agent-spawner.ts`의 `computeSessionName`으로 tmux에 안전한 문자만 남긴다.

### 세션 안에서 하는 일

- 세션 생성 후 `sendKeys`로 에이전트 CLI를 실행한다.
- 초기 태스크 프롬프트는 에이전트 설정에 따라 `sendKeys`(문자 단위 입력) 또는 `pasteFromFile`(tmux `load-buffer` + `paste-buffer`)으로 넣는다.
- 이후 **팀 메시지 전달**도 주로 **`pasteFromFile`**을 사용해 특수문자 이스케이프 문제를 피한다.

---

## 보내는 쪽: 에이전트 → 팀

에이전트가 “말”을 올리는 경로는 **tmux가 아니라 파일 append**이다.

1. **`team-say.sh`**  
   - 인자: `team-id`, `from`, `to`, 메시지 본문.  
   - `/tmp/ensemble/<team-id>/` 아래 `messages.jsonl`에 **`flock`으로 배타 잠금**을 걸고 JSON 한 줄을 append한다.

2. **`ensemble-bridge.sh`** (백그라운드)  
   - 같은 JSONL 파일의 **새 줄**을 감지한다.  
   - 각 메시지를 **`POST /api/ensemble/teams/:team_id`**로 서버에 보낸다.  
   - 성공한 줄까지 오프셋을 기록해 중복 POST를 막는다.

3. **서버 `sendTeamMessage`**  
   - 메시지를 **레지스트리 쪽 `feed.jsonl`**에도 남기고, 수신자에게 tmux로 전달한다(아래 절).

### 저장소의 두 갈래

- **`team-say` 경로**: collab 런타임 디렉터리의 `messages.jsonl`.
- **API가 기록하는 경로**: `lib/ensemble-registry`의 `MESSAGES_DIR/<teamId>/feed.jsonl`.

**`getMessages`**는 위 **두 파일을 모두 읽고**, `id`(없으면 from/timestamp/content 일부)로 **중복 제거**한 뒤 타임스탬프로 정렬한다. 따라서 “대화 로그”의 단일 뷰는 이 병합 결과이다.

---

## 받는 쪽: 서버 → 다른 에이전트 tmux

`sendTeamMessage`가 수신자 집합을 정한 뒤, **세션이 아직 존재하면** 해당 tmux에 텍스트를 넣는다.

- **`to === 'team'`**: 발신자(`from`)를 제외한 **활성 에이전트 전원**.
- **특정 `to`**: 이름이 일치하는 에이전트만.

전달 본문 예시 형태:

- `[Team message from <sender>]: <content>`
- `→ Respond with team-say. Then run team-read to check for more messages.`

로컬은 **`collabDeliveryFile`**로 임시 파일에 쓴 뒤 **`runtime.pasteFromFile(sessionName, tmpFile)`**로 붙인다. 원격 호스트 에이전트는 **`postRemoteSessionCommand`**로 같은 텍스트를 원격 API에 보낸다.

**정리:** “세션 간 공유 메모리”가 아니라, **오케스트레이터가 각 에이전트의 tmux pane에 입력을 재현**하는 방식이다.

---

## 과거 대화·전체 맥락: tmux가 아닌 HTTP

에이전트가 **전체 히스토리**를 보는 표준 경로는 **tmux `capture-pane`가 아니라** HTTP이다.

- **`team-read.sh`**: `GET /api/ensemble/teams/<team-id>/feed`를 호출해 메시지 목록을 출력한다.
- tmux pane 스크래핑은 **토큰 사용량 추정** 등 부가 용도에 쓰이는 수준이며, 대화 라우팅의 본류가 아니다.

---

## 데이터 플로우 (요약 다이어그램)

```
에이전트가 발화
    ↓
team-say.sh → messages.jsonl (flock)
    ↓
ensemble-bridge.sh (폴링) → POST /api/ensemble/teams/:id
    ↓
ensemble-service: append + 수신자 결정
    ↓
대상 에이전트 tmux 세션에 pasteFromFile (또는 원격 command)
    ↓
에이전트는 team-read로 feed 조회 · team-say로 회신
```

---

## 표로 정리

| 구분 | 메커니즘 |
|------|----------|
| 에이전트 실행 | 팀당·에이전트당 **tmux 세션** (`team.name-agent.name`) |
| 송신 | **team-say → JSONL → (bridge) → HTTP POST** |
| 저장·병합 | **feed.jsonl + collab messages.jsonl**, 조회 시 병합·중복 제거 |
| 수신 에이전트 알림 | **pasteFromFile / 원격 세션 command**로 pane에 입력 |
| 전체 대화 열람 | **team-read → HTTP feed** |

---

## 참고

- Ensemble은 에이전트 간을 **공식 프로토콜 버스**로 묶기보다, **파일·HTTP·tmux 입력**의 조합으로 협업을 구현한다.
- 프로젝트 루트의 `docs/architecture.md`에 동일한 데이터 플로우가 다이어그램과 함께 요약되어 있다.
