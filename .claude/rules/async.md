# Async 컨벤션

FastAPI + CrewAI 통합에서 자주 빠지는 함정.

## 1. CrewAI 는 sync — `asyncio.to_thread` 로 감싸기

`orchestrator.runner.run_pipeline` 은 sync 함수 (CrewAI 내부가 sync). async endpoint / lifespan / `demo.py` 어디서든 직접 `await` 불가하고, 그냥 호출하면 이벤트 루프를 LLM 호출 시간 동안 통째로 점유한다 → 다른 요청 처리 불가, 1초 룰 위반.

```python
# ✅ YES — 워커 스레드 위임 (이벤트 루프 비점유)
report = await asyncio.to_thread(run_pipeline, event, notifier)

# ❌ NO — 이벤트 루프 점유
report = run_pipeline(event, notifier)
```

## 2. Lifespan 에서 외부 IO 는 최소화

스키마 자동 셋업처럼 **빠르고 결정적인** 작업만. LLM 워밍업, 외부 API 헬스체크 같은 건:

- lifespan 에서 실패 → 서버 자체가 안 뜸
- 시작 시간 길어짐 → deploy timeout

→ 첫 요청 시점으로 미루는 것이 안전. lifespan 은 "DB/캐시 준비" 같은 internal 만.
