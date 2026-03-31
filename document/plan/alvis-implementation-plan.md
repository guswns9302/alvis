# Alvis 구현 및 안정화 계획

## 1. 현재 상태 요약

이 문서는 `document/alvis-langgraph-tmux-orchestrator-design.md`를 기준으로, 현재 Alvis의 실제 구현 상태와 다음 안정화 우선순위를 정리한 운영 문서다.

현재 기준 Alvis는 다음 구조로 동작한다.

- 단일 머신
- 단일 저장소
- `~/.alvis` 전역 설치 홈
- `launchd` 기반 background daemon
- `LangGraph` 중심 오케스트레이션
- `tmux` 기반 `leader 1 + worker 2`
- `Codex CLI` 기반 worker 실행
- `SQLite` 기반 상태/이벤트 저장
- shared-root 기반 역할 팀 운영
- leader console + worker monitor UI
- 자동 handoff + 자동 redo 중심 협업 흐름

현재 수준은 “자동 handoff가 연결된 프로토타입”이다. 팀 생성, leader 입력, worker 실행, reviewer handoff, leader 출력 후보 집계까지는 동작하지만, 가장 큰 병목은 첫 worker 결과의 신뢰도와 결과 수집 안정성이다. 앞으로의 우선순위는 기능 확장보다 결과 품질과 런타임 안정화에 둔다.

## 2. 현재 운영 모델

### 기본 사용자 흐름

- `alvis team create`
- tmux 세션 준비 및 attach
- leader pane에서 요청 입력
- worker pane에서 작업 진행 상태 확인
- 필요 시 `alvis status`, `alvis logs`, `alvis recover` 사용

기본 입력 경로는 leader console이다. `alvis run`은 보조 경로와 자동화 경로로 유지한다.

### 현재 아키텍처 결정

- `tmux` pane은 source of truth가 아니다.
- 기본 사용자 CLI는 daemon client로 동작한다.
- 상태 변화의 source of truth는 DB 이벤트와 runtime state 파일이다.
- leader는 승인자가 아니라 입력/최종 출력 창이다.
- worker는 raw Codex UI를 직접 노출하지 않고 monitor + background runtime 구조로 동작한다.
- worker는 shared-root를 사용하되 `owned_paths`와 scope conflict 규칙으로 쓰기 범위를 제한한다.
- review 승인 단계는 기본 흐름에서 제거했고, `LangGraph`가 다음 목적지를 자동 결정한다.
- migration은 정식 revision 체계 대신 `백업 + reset` 정책으로 운영한다.

## 3. 현재 구현 현황

### Phase 1. 프로젝트 부트스트랩

- [x] Python 프로젝트 초기화
- [x] `pyproject.toml` 작성
- [x] 기본 패키지 구조 생성
- [x] `README.md` 작성
- [x] 설정 로더 작성
- [x] 로깅 초기화 작성
- [x] 환경 자동 설치 스크립트 작성
- [x] 전역 설치용 `install.sh` 작성
- [x] 로컬 `.venv` 기반 bootstrap 경로 정리
- [x] `tmux` 설치 및 로컬 실행 환경 검증
- [x] Python 의존성 설치 및 import 검증

### Phase 2. 저장소와 도메인 모델

- [x] enum 정의
- [x] `analyst` 역할 enum 추가
- [x] SQLite 엔진 및 세션 팩토리 작성
- [x] SQLAlchemy 모델 정의
- [x] interaction 저장 모델 추가
- [x] DB 초기화 루틴 작성
- [x] repository 계층 작성
- [x] migration 전략 구체화
- [x] LangGraph checkpointer 영속 저장 연결
- [x] DB init 오류 메시지 개선

### Phase 3. 세션 및 실행 계층

- [x] tmux manager 작성
- [x] fixed 3-pane team layout 구현
- [x] `leader left / worker-1 right-top / worker-2 right-bottom` 레이아웃 고정
- [x] pane title 설정
- [x] pane 입력 주입 기능 작성
- [x] pane snapshot 기능 작성
- [x] Codex adapter 작성
- [x] task prompt contract 구현
- [x] PTY 기반 Codex runtime bootstrap 적용
- [x] background runner 기반 worker 실행 경로 적용
- [x] Codex stdout/stderr 구조화 수집기 구현
- [x] heartbeat 파일 기반 모니터 연결
- [x] stale runtime file reset 처리

### Phase 4. 공유 작업공간과 역할 팀 모델

- [x] shared-root workspace manager 작성
- [x] 고정 3인 팀 모델 구현
- [x] worker role alias 저장
- [x] `owned_paths` 기반 task scope 계약 구현
- [x] scope conflict 감지 정책 구현
- [x] `team remove` 구현

### Phase 5. 오케스트레이션 계층

- [x] run state 정의
- [x] supervisor 초안 작성
- [x] task planning 초안 작성
- [x] agent selection 초안 작성
- [x] dispatch 초안 작성
- [x] wait/update 루프 초안 구현
- [x] 실제 Codex 출력 기반 상태 전이 초안 구현
- [x] interrupt/resume 정교화
- [x] interaction 생성/라우팅/해결 초안 구현
- [x] leader queue / pending interactions 상태 노출
- [x] leader follow-up task 생성 초안 구현
- [x] 자동 handoff 규칙 구현
- [x] reviewer 자동 검증 worker 경로 구현
- [x] reviewer 결과 기반 `final_output_candidate` 집계 구현
- [x] invalid output 감지 시 자동 redo task 생성 구현
- [x] `final_output_ready` 상태 분리

### Phase 6. CLI / UI

- [x] `alvis team create`
- [x] `alvis team create` interactive wizard 구현
- [x] `alvis team start`
- [x] `alvis run`
- [x] `alvis resume`
- [x] `alvis status`
- [x] `alvis logs`
- [x] `alvis recover`
- [x] `alvis cleanup`
- [x] `alvis tmux-attach`
- [x] `alvis bootstrap`
- [x] `alvis collect-outputs`
- [x] `alvis version`
- [x] `alvis doctor`
- [x] `alvis upgrade`
- [x] `alvis daemon status|start|stop|restart`
- [x] 최소 FastAPI surface 작성
- [x] background daemon API 경로 추가
- [x] CLI 출력 포맷 개선
- [x] leader pane을 `Alvis Leader Console`로 전환
- [x] worker pane을 `worker monitor`로 전환
- [x] leader pane에 handoff / final candidate / redo 상태 표시
- [x] worker pane에 structured output 표시

### Phase 7. 복구와 운영 안정성

- [x] stalled agent 탐지 초안
- [x] recover 명령 초안
- [x] tmux pane과 실제 프로세스 상태 reconciliation 초안
- [x] orphaned task 자동 정정 초안
- [x] retry 정책 구현
- [x] session warning / runtime error 요약 노출
- [x] `stdin is not a terminal` 대응을 위한 PTY 실행 경로 적용
- [x] `tmux.command` 내부 로그를 pane UI에서 차단
- [x] stale DB / stale runtime 파일 대응 메시지 개선

### Phase 8. 출력 수집과 테스트

- [x] task prompt 단위 테스트
- [x] supervisor 기본 테스트
- [x] output collector 단위 테스트
- [x] CLI smoke test
- [x] recovery reconciliation 테스트
- [x] repository 테스트 확장
- [x] tmux manager 실제 통합 테스트
- [x] e2e 시나리오 테스트
- [x] shared-root cleanup / conflict / retry 테스트
- [x] interaction routing 테스트
- [x] fake Codex session fixture 기반 회귀 테스트 추가
- [x] invalid structured output / redo 흐름 테스트 추가

## 4. 최근 안정화 작업

- [x] worker pane에 보이던 bootstrap 명령 흔적 제거
- [x] pane title과 배치로 leader / worker 식별성 확보
- [x] runtime state / stderr 요약을 status에 반영
- [x] worker monitor에서 불필요한 heartbeat / status changed 이벤트 숨김
- [x] output collector에서 raw task prompt / Codex tip / `ALVIS_RESULT` 템플릿 노이즈 제거
- [x] leader / worker monitor가 내용 변경 시에만 재렌더하도록 조정
- [x] output collector가 incomplete 또는 placeholder block을 `final`로 승격하지 않도록 수정
- [x] worker가 structured result를 내지 못한 경우 자동 `blocked` 처리 및 redo 유도
- [x] reviewer rejection note가 그대로 최종 완료가 되지 않도록 `final_output_ready` 분리

## 5. 앞으로의 계획

### Phase A. 결과 신뢰도 강화

- [ ] `OutputCollector`의 heuristic fallback 의존도를 더 낮추기
- [ ] worker가 항상 정확한 structured block만 내도록 prompt contract 보강
- [ ] placeholder, welcome UI, progress footer, starter prompt를 더 강하게 차단
- [ ] reviewer가 workspace diff fallback보다 source task structured output을 우선 사용하도록 정교화

### Phase B. 자동 handoff / redo 품질 향상

- [ ] invalid output 감지 시 same-role redo와 cross-role handoff 규칙을 더 정교하게 분리
- [ ] reviewer가 `blocked` 또는 `needs_review`를 낸 경우 redo 생성 기준 개선
- [ ] 중복 handoff / 중복 redo 생성 방지
- [ ] `final_output_candidate`와 실제 user-facing final response 사이의 전이 규칙 강화

### Phase C. Leader / Worker 협업 UX 정교화

- [ ] leader console에서 run 상태와 “진행 상태 vs 최종 응답” 구분을 더 명확히 표시
- [ ] worker monitor에서 source task / reviewer verdict / redo recommendation 표시를 더 구체화
- [ ] pane title에 역할 alias와 현재 상태를 더 직접적으로 반영
- [ ] “협업이 실제로 보이는 화면”에 가깝게 message stream 표현 개선

### Phase D. 운영 기반 정리

- [ ] `README.md`와 실제 UX를 현재 구현과 계속 동기화
- [ ] 계획 문서와 사용자 가이드를 자동 handoff 기준으로 유지
- [ ] upgrade 시 schema drift 대응 정책 정교화
- [ ] 장기 과제로 migration 체계와 배포/업그레이드 정책 설계

## 6. 운영 기준

- destructive action은 자동 수행하지 않는다.
- worker는 같은 프로젝트 루트를 공유하되 task의 `owned_paths` 범위 안에서만 작업한다.
- leader는 승인자가 아니라 입력과 최종 출력의 관문이다.
- 기본 사용 경로는 `team create -> tmux attach -> leader console 입력`이다.
- 기본 설치 경로는 `curl | bash -> alvis daemon start -> alvis team create`다.
- 기본 협업 흐름은 `primary worker -> reviewer(optional) -> leader output`이며, invalid output이면 자동 redo가 우선된다.

## 7. 검증 기준

앞으로의 변경은 최소 다음 시나리오를 기준으로 검증한다.

- `team create` wizard로 팀 생성이 되는지
- 새 tmux 세션에서 pane 배치와 title이 기대대로 보이는지
- leader console 입력만으로 run이 시작되는지
- worker monitor에 내부 `tmux.command` 로그가 노출되지 않는지
- task prompt 원문이 worker summary에 노출되지 않는지
- valid worker output이면 reviewer handoff 또는 leader 출력으로 자연스럽게 이어지는지
- invalid worker output이면 reviewer가 아니라 redo task가 생성되는지
- `final_output_candidate`가 있어도 `final_output_ready=false`면 run이 종료되지 않는지
- stale DB / stale runtime 파일이 있을 때 사용자 메시지가 적절한지

## 8. 장기 후속 과제

- 정식 migration 체계 도입 (`alembic` 또는 동등한 revision 관리)
- DB 호환성 정책 수립
- 배포/업그레이드 시나리오 문서화
