# cottontrip 인수인계 · 2026-09-20

## 먼저 읽기
- 여행 짐 준비 웹사이트의 실제 소스는 `cottontrip/`에 있다. 루트의 index.html은 기존 정적 사이트이며 별개다.
- 상세 기획·현재 구현·실행 방법은 `docs/COTTONTRIP_HANDOFF.md`를 읽는다.
- 이 변경은 코드 인수인계다. 기존 사이트 교체, 운영 배포, 유료 AI 연결은 실행하지 않았다.
- 기존 main push는 Cloudflare Pages 프로젝트 ppppdddd로 루트의 HTML/CSS/JS만 배포한다. cottontrip을 배포하는 설정이 아니다.

## 목표와 현재 범위
- 한국 이용자가 여행 경로와 짐을 입력하고 기내/위탁 준비 및 주의사항을 이해하는 서비스.
- 현재 시제품은 인천↔나리타 직항 왕복. 제주항공·파라타항공 제한은 폐기됐다.
- 항공편 API 서버 코드는 있지만 검증된 수하물 규정 판정은 미연결. 물품 무게와 안내는 시험용이다.
- 회원가입 없음. React 메모리에 입력 유지, 새로고침 시 초기화.
- 여행 설정, 짐 담기, 가방 무게, 결과의 네 페이지. 둥근 터치 영역과 분홍·라벤더·하늘색 그라데이션.
- Figma: https://www.figma.com/design/3NnRk67sEVCoE4v6iJAW8V?node-id=14-17

## 구현·운영
- TypeScript/React, App Router 형태, 실제 도구는 vinext/Vite Sites starter. Node 22.13 이상.
- 인증키는 AIRPORT_API_KEY 환경변수로 별도 제공한다. 이 저장소에는 실제 인증키를 포함하지 않는다.
- 공식 문서 등록·AI 검증·주간 갱신·상품 페이지 해석·ATPCO 연결은 미구현이다.
- 다음 AI는 먼저 코드를 실행하고 사용자의 다음 작업 지시를 확인한다. 시험 안내를 검증된 결과로 바꾸지 않는다.
