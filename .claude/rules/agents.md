# Sub-agent / Cold review 워크플로

multi-agent 환경에서 작업 분배와 검증을 일관성 있게 하기 위한 운영 규칙. Phase 4 / PR #6 / PR #8 / PR #9 에서 4회 검증된 패턴.

## 1. Bundle vs 분리 — 충돌 매트릭스 먼저

여러 이슈를 병렬 worktree 로 띄울지 결정하기 전에 **파일 충돌 매트릭스** 를 그린다:

| 이슈 | 핫스팟 파일 |
|---|---|
| #A | `foo.py`, `bar.py` |
| #B | `foo.py`, `baz.py` |
| #C | `qux.py` |

`foo.py` 가 #A 와 #B 양쪽 핫스팟이면 **bundle** (단일 worktree agent). 분리하면 merge 충돌이 거의 확정 + 두 agent 가 같은 파일을 다른 방향으로 끌고 가 PR 단계에서 재작업.

**판정 기준**:
- 핫스팟 (🔴 rewrite) 가 2개 이상 이슈에서 겹침 → bundle
- 같은 파일이 2개 이슈에서 가벼운 import 추가 (🟡) 정도 → 분리 가능
- 완전 분리된 파일 → 병렬 가능

판정이 애매하면 `AskUserQuestion` 으로 사용자 결정 받음 — bundle / 분리 worktree / 직접 진행 3안.

## 2. Worktree 격리

bundle 결정 시 `Agent` 호출에 `isolation: "worktree"` 명시. agent 는:

- main 에서 분기한 임시 worktree 에서 작업
- 로컬 commit 만 — push 금지 (parent 가 검토 후 push)
- 작업 종료 시 branch 이름 + 변경 summary 반환

```python
Agent({
  description: "...",
  subagent_type: "general-purpose",
  isolation: "worktree",
  prompt: "... 자세한 작업 지시 + 코드 컨벤션 + 'Do NOT push' ..."
})
```

**Agent prompt 필수 포함 항목**:
- 작업 목표 + 이슈 번호
- 관련 rule 파일 경로 (`.claude/rules/...`)
- 최근 머지된 architecture 요약 (cold-context agent 는 직전 PR 모름)
- 권장 commit 순서 (atomic + 독립 빌드 가능)
- hard constraints (test pass count baseline, lint clean, "Do NOT push", "Do NOT touch X")
- 결과 보고 형식 (branch 명 + 200자 요약 + 결정사항)

## 3. Cold-context review — non-trivial PR 의 기본 단계

self-review 의 한계가 검증됨: PR #1 (Phase 4) / #6 / #8 에서 모두 cold-context sub-agent 가 self-review 못 잡은 finding 발견. 그중 PR #1 의 redaction-vs-diff 충돌, PR #6 의 dep 누락, PR #8 의 transport 분류 비대칭은 **production-grade bug pattern**.

**Trigger**:
- worktree agent 가 만든 PR → 항상 cold review
- 직접 작업 PR 도 architecture refactor / external 경계 변경 / Protocol 수정 시 cold review
- 단순 docs / 한 파일 fix / typo → 스킵 OK

**Cold review agent prompt 필수**:
- "You have NOT seen the design discussion" 명시 (cold context 의무화)
- 검토 각도 enumerate: correctness / contract drift / layering 위반 / 테스트 커버리지 / dead code / smells
- 구체적 file:line 참조 강제 ("Be specific with file paths and line numbers")
- severity 분류 (HIGH / MEDIUM / LOW) 요구
- `git diff` 사용, `git checkout` **금지** (이전 cold reviewer 가 working tree unstaged 변경 덮어쓴 사고 있음)
- 결과 길이 제한 (~700자) — 노이즈 차단

**Push back 처리**:
- HIGH 는 머지 전 반드시 반영
- MEDIUM 은 case-by-case — pre-existing 위험은 별도 follow-up 이슈로 분리
- LOW 는 가치 있으면 같이, 아니면 스킵 (skip 이유는 PR comment 에 명시)

## 4. Workflow 다이어그램

```
[issue 모음]
    ↓
[충돌 매트릭스 검사]
    ↓
  bundle?
  ├─ yes → Agent (worktree isolation)
  └─ no  → 병렬 Agent 또는 직접 진행
    ↓
[branch push + PR open]
    ↓
[Cold-context review sub-agent]    ← non-trivial PR 기본
    ↓
[finding 분류: HIGH 반영 / MEDIUM 분리 / LOW 판단]
    ↓
[push back commit + PR comment]
    ↓
[rebase merge → main]              ← FF 또는 squash 가 아닌 rebase
    ↓
[worktree 정리 + 로컬 main sync]
    ↓
[docs/plan-log append + sticky 갱신]
```

## 5. Rebase merge — linear history

squash merge 는 commit-by-commit 컨텍스트 손실. FF merge 는 가능하지만 PR title 이 사라짐. **rebase merge** 가 둘 다 보존:

```bash
gh pr merge <N> --rebase --delete-branch
```

bundle PR 의 3~5 atomic commits 가 main 에 그대로 linear 로 박힘. 추후 `git log --oneline` 으로 작업 흐름 follow 가능.

## 6. Worktree cleanup

agent 작업 종료 + PR 머지 후 worktree 가 잠금 상태로 남을 수 있음 (agent process 가 lock):

```bash
git worktree remove .claude/worktrees/agent-<id> -f -f   # 2x force
git branch -D <branch-name>                              # local branch 삭제
git pull --ff-only origin main                           # main sync
git fetch --prune                                        # stale remote ref 정리
```

## 7. Auto-close 주의 — `closes #N #M` GitHub 한계

PR body 에 `closes #2 #3 #4` 단일 줄로 적으면 GitHub 가 **첫 번째 (#2) 만 auto-close**. 나머지는 수동 close:

```bash
gh issue close 3 --comment "Resolved by PR #<N> (commit <sha> — <topic>)"
gh issue close 4 --comment "..."
```

또는 PR body 에 `Closes #2\nCloses #3\nCloses #4` (줄바꿈) 로 쓰면 모두 auto-close. 권장은 후자.
