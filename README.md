# 한미 헤드라인

한국·미국 주요 뉴스 30개를 한국어로 제공하는 정적 웹사이트입니다.

## 자체 분석 콘텐츠
- 30개 기사를 종합한 오늘의 핵심 흐름 3개
- 정치, 경제, 기술, 문화, 스포츠, 과학, IT, 주식, 사회, AI 분야별 동향
- 기사 수, 국가 수, 분야 수, 언론사 수 통계
- AI 분석임을 표시하고 원문 확인을 안내하는 편집 기준

## 뉴스 자동 업데이트
GitHub Actions가 매시간 17분에 Google News RSS에서 정치, 경제, 기술, 문화, 스포츠, 과학, IT, 주식, 사회, AI 분야의 기사 30개를 수집합니다. 한국과 미국 기사를 각각 15개씩 배분하며, `GEMINI_API_KEY`가 설정되어 있으면 미국 기사를 한국어로 번역하고 모든 기사에 짧은 한국어 요약을 생성합니다.

1. Google AI Studio에서 Gemini API 키를 발급합니다.
2. GitHub 저장소의 `Settings → Secrets and variables → Actions`로 이동합니다.
3. `New repository secret`을 눌러 이름을 `GEMINI_API_KEY`로 지정하고 키를 등록합니다.
4. `Actions → 최신 뉴스 업데이트 → Run workflow`를 한 번 실행합니다.

API 키가 없더라도 뉴스는 수집되지만 미국 기사 제목은 영어로 표시되고 요약은 생성되지 않습니다. API 키는 코드나 `headlines.json`에 저장되지 않습니다.

## GitHub Pages 배포
1. GitHub에서 Public repository를 만듭니다.
2. `index.html`을 repository 최상위에 업로드합니다.
3. Settings → Pages → Deploy from a branch → `main` / `/ (root)` → Save
4. 생성된 Pages 주소로 접속합니다.

기사 제목과 요약의 저작권은 각 언론사에 있으며, 사이트에서는 원문 링크를 제공합니다.
