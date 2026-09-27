# Industrial Gas Supply Continuity Decision Support PoC — Cloud Ready

산업가스 비정상 운전에서 **LLM 기반 운전제약 의미추출 + Purpose-Specific State Contract + Deterministic Hard Rules**를 결합한 연구용 PoC입니다.

## Streamlit Community Cloud
Entry point: `app.py`

API Key 없이도 C-01 / C-02 / C-03 Offline Demo가 작동합니다.

Live LLM은 공개 링크에서 비용 오남용을 막기 위해 서버측 Secret의 `DEMO_ACCESS_CODE`를 입력한 발표자만 사용할 수 있습니다. 브라우저 세션당 Live 호출도 6회로 제한했습니다.

### Cloud Secrets 예시
```toml
OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
DEMO_ACCESS_CODE = "YOUR_PRIVATE_DEMO_CODE"
OPENAI_MODEL = "gpt-5.6-luna"
```

**실제 Secret은 GitHub에 커밋하지 마세요.**

## 연구상 주의
- 모든 기본 데이터는 합성데이터입니다.
- 실제 AIRFIRST 또는 특정 사업장의 내부 데이터를 사용하지 않습니다.
- 자동 운전승인/설비조작 시스템이 아닙니다.
- Purpose-Specific State Contract는 본 연구의 연구용 중간상태 모델입니다.
