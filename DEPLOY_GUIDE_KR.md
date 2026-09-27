# Python 설치 없이 웹으로 실행하기

## 1) GitHub 저장소 만들기
추천 이름: `industrial-gas-llm-state-contract`

이 폴더의 파일을 그대로 GitHub에 올립니다. `.streamlit/secrets.toml`은 절대 올리지 않습니다.

구조:
```text
repo/
├─ app.py
├─ scenario_presets.json
├─ requirements.txt
├─ README.md
├─ .gitignore
└─ .streamlit/
   ├─ config.toml
   └─ secrets.toml.example
```

## 2) Streamlit Community Cloud
`share.streamlit.io`에서 GitHub로 로그인 → **Create app** → 방금 만든 repository 선택 → branch `main` → entrypoint `app.py`.

## 3) Advanced settings
Python은 기본 3.12를 사용해도 됩니다.

Secrets에는 다음 형식으로 입력:
```toml
OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
DEMO_ACCESS_CODE = "본인만_아는_발표용_코드"
OPENAI_MODEL = "gpt-5.6-luna"
```

Live LLM이 필요 없으면 Secrets 없이 배포해도 Offline Demo는 작동합니다.

## 4) Deploy
Deploy를 누르면 `*.streamlit.app` 링크가 생성됩니다. 이후 PC에 Python을 설치하지 않아도 브라우저 링크로 실행합니다.

## 발표 추천 순서
1. C-01 Offline으로 구조 설명
2. 발표자 코드 입력
3. Live LLM 실행
4. MOC의 `65%`를 `55%`로 수정
5. 다시 Live LLM 실행
6. 공급능력 변화 확인
7. C-02에서 `비상보충 허용 ≠ 지속백업 허용` 설명
8. C-03에서 `수량 충족 ≠ 품질판단 확정` 설명

## 보안
- OpenAI API Key를 코드/GitHub에 적지 마세요.
- 실제 회사 비공개 DCS/MOC/CMMS 데이터를 공개 데모에 입력하지 마세요.
- 공개 링크에서는 발표자 코드 없이 Live LLM을 사용할 수 없게 두는 것을 권장합니다.
