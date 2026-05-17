# plan.md 갱신 로그

`docs/plan.md` 의 변경 이력. plan.md 가 컨텍스트로 자주 로드되므로 로그는 분리.
새 엔트리는 최신이 아래로 가도록 시간순 append.

| 날짜 | 내용 |
|---|---|
| 2026-05-13 | 초기 작성. Phase 0~5 정의, 사용자 시나리오 + gap analysis 포함 |
| 2026-05-13 | 최종 검토 반영: Phase 1.5(서명검증), 2.6/2.7(에러알림·truncate), 4.0(mock→real), 4.6(redaction), 5.5~5.7(정리) 추가. Agent 활용 지점 명시 |
| 2026-05-13 | Phase 1 완료 (5 commits, 104 tests). Phase 1.6 추가 — DB 레이어 SQLAlchemy/Alembic, MySQL dev/prod + in-memory SQLite test, 테스트 패키지별 격리 |
| 2026-05-13 | Phase 1.6 완료 (90 tests). 테스트 패키지별 이동, SQLAlchemy 2.0 + Alembic, async store, docker-compose MySQL 8.0 |
| 2026-05-13 | Phase 1.7 정식화 + 완료 (90 tests / 0 warnings). init_schema SQLite-only, .claude/rules 5개 분리, common.clock + ruff DTZ + StrEnum, ruff lint+format 전면, pre-commit. 5.6 (datetime cleanup) 은 1.7.3 에 흡수돼 제거. 5.7 GitHub Actions CI 신규 |
| 2026-05-13 | Phase 0 (0.1 Slack / 0.2 Sentry / 0.2b ngrok / 0.3 GitHub App) 완료. Sentry 실 webhook E2E 검증 중 `Sentry-Hook-Signature` 헤더 버그 발견·수정 (`ecb1182`) |
| 2026-05-14 | Phase 2 (Slack 가시성) 완료 — 6 commits (`c2feda2`~`0b6fb39`) + fix (`4000cfa`). 실 LLM + mock 양쪽 E2E 캡처 확보. 발견 잔무: Slack section truncation (2.8, patch=3044 / RCA=4146 실측), 실 LLM agent 단위 thread emit 누락 (2.9). 우선순위 재조정: 다음 주 Phase 4 (실 코드 변경 PR — unified diff prompt + git apply) 가 Phase 3 보다 1순위 |
| 2026-05-14 | 갱신 로그를 plan.md → plan-log.md 로 분리 (plan.md 컨텍스트 비용 절감) |
| 2026-05-14 | gateway 패키지 layered 정리 — api/services/infrastructure 3-layer. `IncidentStore`→`IncidentRepository`. `parsers`→`monitors`. `.claude/rules/architecture.md` 신설 (import 방향 + naming 룰). Phase 6 신설 (logger / orchestrator 실 API tool) |
| 2026-05-18 | Phase 4 (실 코드 변경 PR) 완료 → PR #1 rebase merge (9 commits). `common/diff.py` (extract/changed_paths/is_new_file/verify_apply/apply_diff) + `common/redact.py` (9 secret patterns) + `github.app:_apply_diff_pr` (Git Data API 10-call) + `github.app:close_pr` + `gateway.services.decisions:_close_pr_if_exists`. `incidents` 테이블에 `pr_number/pr_branch` 컬럼 (Alembic `f858338542cd`). 144 tests, e2e 실 PR 검증 (`dlwodnjs724/warroom-demo`) |
| 2026-05-18 | Phase 4 머지 직전 cold-context sub-agent 2-round 리뷰 — 1차 코드 (5 findings, HIGH 3건 fix: redaction-vs-diff 충돌·sk- 패턴 너무 광범·PR persist 실패 시 orphan), 2차 architecture (4 findings, HIGH 1건 `GitHubAppClient` 책임 비대화는 follow-up 으로 분리). 검증된 패턴 → Phase 3 머지 전에도 동일 적용 권장 |
| 2026-05-18 | Architecture follow-up 이슈 4건 신설: [#2](https://github.com/dlwodnjs724/warroom/issues/2) `GitHubAppClient` 분해, [#3](https://github.com/dlwodnjs724/warroom/issues/3) gateway GitHub client DI, [#4](https://github.com/dlwodnjs724/warroom/issues/4) `common/diff.py` 분리, [#5](https://github.com/dlwodnjs724/warroom/issues/5) async to_thread + close_pr observability. Phase 3 / 6.2 진입 전 #2~#4 정리 권장 (새 transport 추가 시점 임박) |
| 2026-05-18 | plan.md / plan-log.md / architecture.md / decisions.md stale 갱신 — Phase 4 완료 반영 (현재 가능 매트릭스, 의존 그래프, 추천 순서, agent 활용 패턴) |
