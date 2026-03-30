# Alvis 구현 완료 계획

## 1. 목표

이 문서는 `document/alvis-langgraph-tmux-orchestrator-design.md`를 구현 가능한 작업 단위로 쪼갠 실행 기준 문서다. 이후 구현 작업은 이 문서를 기준으로 진행하고, 완료된 항목은 지속적으로 갱신한다.

초기 완료 기준은 다음과 같다.

- 단일 머신
- 단일 저장소
- `LangGraph` 중심 오케스트레이터
- `tmux` 기반 leader 1명 + worker 2명
- `Codex CLI` 세션 기반 에이전트 실행
- `SQLite` 이벤트 저장소
- `git worktree` 격리
- review gate

## 2. 단계별 실행 계획

### Phase 1. 프로젝트 부트스트랩

- [x] Python 프로젝트 초기화
- [x] `pyproject.toml` 작성
- [x] 기본 패키지 구조 생성
- [x] `README.md` 작성
- [x] 설정 로더 작성
- [x] 로깅 초기화 작성
- [x] 환경 자동 설치 스크립트 작성
- [x] 로컬 `.venv` 기반 bootstrap 경로 정리
- [x] `tmux` 설치 및 로컬 실행 환경 검증
- [x] Python 의존성 설치 및 import 검증

### Phase 2. 저장소와 도메인 모델

- [x] enum 정의
- [x] SQLite 엔진 및 세션 팩토리 작성
- [x] SQLAlchemy 모델 정의
- [x] DB 초기화 루틴 작성
- [x] repository 계층 작성
- [ ] migration 전략 구체화
- [ ] LangGraph checkpointer 영속 저장 연결

### Phase 3. 세션 및 실행 계층

- [x] tmux manager 작성
- [x] team layout 생성 기능 작성
- [x] pane 입력 주입 기능 작성
- [x] pane snapshot 기능 작성
- [x] Codex adapter 작성
- [x] task prompt contract 구현
- [x] Codex stdout/stderr 구조화 수집기 초안 구현
- [x] heartbeat 파일 기반 모니터 초안 연결

### Phase 4. 작업공간 격리

- [x] worktree manager 작성
- [x] branch naming 규칙 구현
- [x] worktree 생성 로직 구현
- [ ] worktree 정리 로직 구현
- [ ] 충돌 감지 정책 구현

### Phase 5. 오케스트레이션 계층

- [x] run state 정의
- [x] supervisor 초안 작성
- [x] task planning 초안 작성
- [x] agent selection 초안 작성
- [x] dispatch 초안 작성
- [x] review 생성 초안 작성
- [x] wait/update 루프 초안 구현
- [x] 실제 Codex 출력 기반 상태 전이 초안 구현
- [ ] interrupt/resume 정교화

### Phase 6. Review Gate

- [x] review gate 규칙 초안 구현
- [x] review 저장 모델 구현
- [x] approve/reject CLI 구현
- [x] review reject 후 재계획 연결
- [x] review approve 후 run resume 초안 연결

### Phase 7. CLI/API

- [x] `alvis team create`
- [x] `alvis team start`
- [x] `alvis run`
- [x] `alvis status`
- [x] `alvis review list`
- [x] `alvis review approve`
- [x] `alvis review reject`
- [x] `alvis logs`
- [x] `alvis recover`
- [x] `alvis tmux-attach`
- [x] `alvis bootstrap`
- [x] `alvis collect-outputs`
- [x] 최소 FastAPI surface 작성
- [ ] CLI 출력 포맷 개선

### Phase 8. 복구와 운영 안정성

- [x] stalled agent 탐지 초안
- [x] recover 명령 초안
- [x] tmux pane과 실제 프로세스 상태 reconciliation 초안
- [x] orphaned task 자동 정정 초안
- [ ] retry 정책 구현

### Phase 9. 테스트와 검증

- [x] review gate 단위 테스트
- [x] task prompt 단위 테스트
- [x] supervisor 기본 테스트
- [x] output collector 단위 테스트
- [x] CLI smoke test
- [x] review approve 후 run 완료 smoke test
- [x] recovery reconciliation 테스트
- [ ] repository 테스트 확장
- [ ] tmux manager 테스트 또는 mock 기반 테스트
- [ ] e2e 시나리오 테스트

## 3. 남은 핵심 구현 항목

다음 항목은 실제 운영 가능한 수준으로 가기 위해 우선적으로 마무리해야 한다.

1. LangGraph interrupt/resume 정교화
2. output collector 품질 고도화
3. 테스트 확장
4. CLI 출력 개선
5. migration/checkpointer 정리

## 4. 운영 기준

- `tmux`는 source of truth가 아니다.
- 상태 변화는 모두 DB 이벤트를 남긴다.
- destructive action은 review 전 자동 수행하지 않는다.
- worker는 각자 독립 worktree에서만 작업한다.
- leader는 worker 산출물을 통합하는 중심 역할을 가진다.

## 5. 다음 작업 우선순위

가장 먼저 이어서 구현할 항목은 다음 순서로 진행한다.

1. repository/tmux 통합 테스트 추가
2. output collector 품질 고도화
3. CLI 출력 개선
4. LangGraph interrupt/resume 정교화
5. migration/checkpointer 정리
