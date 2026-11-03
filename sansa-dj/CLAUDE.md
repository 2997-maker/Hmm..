# CLAUDE.md - Sansa DJ (사이버 산사 온라인 라운지)

> **프로젝트 개요**: 최근 100만 명이 찾은 온라인 사찰 '산사(sansatemple.com)'의 무언·공존 힐링 감성을 온라인 DJ 및 리스닝 라운지로 재해석한 프로젝트입니다. 한 명의 DJ가 음악을 믹싱/선곡하고, 다수의 관객이 로그인 없이 입장하여 말없이 음악을 함께 듣고 가벼운 리액션(야광봉, 비트타기, 합장)을 나눕니다.

---

## 🚀 빠른 시작 (Local Dev)

이 프로젝트는 현재 제로 디펜던시 순수 웹(HTML5 / CSS3 / Vanilla JS + Web Audio API)으로 구성되어 있습니다.

```bash
# 로컬 개발 서버 실행 (선택 중 하나)
python3 -m http.server 8000 --directory public
# 또는
npx serve public
```

브라우저에서 `http://localhost:8000` 접속 시 즉시 동작합니다.

---

## 🏗️ 시스템 아키텍처

```text
[DJ Client] ──(Web Audio / YouTube)──> [Audio Stream & Sync Engine]
                                               │
                                       (WebSocket / WebRTC)
                                               ▼
[Audience 1] ─── [Audience 2] ─── [Audience N] (Synced Playback & Float Reactions)
```

1. **DJ 부스 (DJ Console)**:
   - 음원 소스: 현재 Web Audio 내장 신디사이저 탑재 -> 향후 YouTube Iframe / SoundCloud API / MP3 스트림 연동 예정.
   - 실시간 컨트롤: 필터 스위프(BiquadFilter), 볼륨, 스크래치, 에어혼 FX.
   - 타임코드 및 트랙 상태 브로드캐스트.
2. **리스닝 플로어 (Audience Floor)**:
   - 산사식 가벼운 입장 (로그인 없음, 랜덤 닉네임: '나: 작은 솔잎', '달빛스님' 등).
   - 비트에 맞춰 헤드뱅잉하는 2D/3D 미니멀 아바타들.
   - 플로팅 리액션: 야광봉(🪄), 둠칫(🕺), 불꽃(🔥), 합장(🙏).
3. **실시간 동기화 레이어 (Sync Layer - 개발 예정)**:
   - Supabase Realtime 또는 Cloudflare Workers (Durable Objects) / Node WebSocket 활용.

---

## 📋 클로드코드(Claude Code) 구현 로드맵

클로드코드가 이 저장소를 이어받아 개발할 핵심 단계입니다:

### Phase 1: 실제 음원 재생 및 동기화 (Audio Sync)
- [ ] YouTube Iframe Player API 또는 Soundcloud Widget API 연동
- [ ] 호스트(DJ)의 `play`, `pause`, `seek`, `track_change` 이벤트 실시간 동기화
- [ ] 리스너 지연 시간(Latency) 보정: NTP 스타일 타임스탬프 계산으로 ±0.2초 오차 내 재생 싱크 유지

### Phase 2: 실시간 웹소켓/서버리스 브로드캐스트
- [ ] PartyKit, Cloudflare Durable Objects, 또는 Supabase Broadcast 채널을 통한 경량 P2P/웹소켓 연결
- [ ] 관객 접속자 수 실시간 집계 및 아바타 배치 알고리즘
- [ ] 사용자 리액션(야광봉, 합장 등) 실시간 브로드캐스트 및 렌더링

### Phase 3: DJ 큐(대기열) 시스템
- [ ] 관객이 "DJ 줄서기"를 누르면 대기열(Queue)에 등록
- [ ] 현재 곡 종료 시 다음 대기자에게 DJ 권한 자동 인수인계
- [ ] 관객들의 비토/스킵 투표(산사식 방생 투표) 기능

---

## 💡 코드 스타일 및 원칙

1. **Zero-Friction**: 로그인이나 복잡한 설정 없이 링크 클릭 1초 만에 음악이 나오는 UX 유지.
2. **Aesthetic & Vibe**: 어둑한 네온과 사찰 목탁/연등 감성이 섞인 사이버 템플 비주얼 유지.
3. **가벼운 번들**: 모바일 브라우저에서도 부드럽게 돌아갈 수 있도록 렌더링 부하 최소화 (CSS 하드웨어 가속, Canvas 최적화).
