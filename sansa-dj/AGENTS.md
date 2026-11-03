# AGENTS.md - Codex & Autonomous Agent Execution Guide

This document defines execution rules, interface contracts, and task specifications for OpenAI Codex and other autonomous coding agents working on the **Sansa DJ** repository.

---

## 🎯 Agent Mission

Transform the standalone frontend MVP in `public/` into a production-ready, synchronized online DJ lounge with real-time audio playback, synchronized room presence, and reactive visuals.

---

## 📦 Directory Layout

```text
sansa-dj/
├── CLAUDE.md          # Guide for Claude Code
├── AGENTS.md          # Guide for OpenAI Codex & automated agents (this file)
├── README.md          # Project overview & architecture
├── docs/
│   └── SPEC.md       # Technical and functional specifications
└── public/
    ├── index.html     # Main application markup
    ├── style.css      # Neon temple theme & responsive styles
    └── app.js         # Web Audio synthesizer & state controller
```

---

## 🔌 Event Protocol & Schemas (Target Sync Contract)

When implementing the real-time sync layer, adhere to the following JSON message schema:

### 1. `DJ_STATE_BROADCAST` (Server/Host -> All Listeners)
```json
{
  "type": "DJ_STATE_BROADCAST",
  "djId": "user_2997",
  "djName": "작은 솔잎",
  "track": {
    "source": "youtube",
    "trackId": "jfKfPfyJRdk",
    "title": "lofi hip hop radio - beats to relax/study to",
    "duration": 0
  },
  "playback": {
    "isPlaying": true,
    "currentPositionSec": 142.5,
    "serverTimestamp": 1793618400000
  }
}
```

### 2. `USER_REACTION` (Listener -> All in Room)
```json
{
  "type": "USER_REACTION",
  "userId": "anon_9182",
  "reaction": "glowstick", // glowstick | headbang | fire | namaste
  "xPercent": 45.2,
  "yPercent": 60.1
}
```

### 3. `DJ_QUEUE_REQUEST` (Listener -> Server)
```json
{
  "type": "DJ_QUEUE_REQUEST",
  "userId": "anon_9182",
  "requestedTrackUrl": "https://www.youtube.com/watch?v=..."
}
```

---

## 🔨 Step-by-Step Task Breakdown

Execute tasks in strict sequential order. Verify each task before moving to the next.

### Task 1: YouTube Iframe Audio Player Integration
- [ ] Replace or wrap the synthetic `SoundEngine` with a dual-mode player (Synthesizer fallback + YouTube Iframe API).
- [ ] Implement hidden or embedded YouTube player inside the DJ booth.
- [ ] Maintain the spinning vinyl animation synchronized with actual playback status.
- [ ] *Verification*: Running the page allows entering a YouTube URL or playing default lofi streams.

### Task 2: Real-time Presence & Room Sync (PartyKit / Supabase / Cloudflare)
- [ ] Set up lightweight WebSocket room server (PartyKit or WebSocket server in `server/`).
- [ ] Emit heartbeat every 5s from connected clients to maintain active listener count.
- [ ] Broadcast floating reactions to all connected clients in the room.
- [ ] *Verification*: Open two browser tabs; trigger a reaction in Tab 1, observe floating reaction appear in Tab 2 within 150ms.

### Task 3: Audio Drift Correction (Sync Algorithm)
- [ ] Compute client-server clock offset via simple ping-pong message.
- [ ] If local audio position differs from host position by > 0.5s, seek smoothly to host position.
- [ ] Mute audio automatically if browser autoplay policy blocks unmuted audio on entrance, and show "터치하여 소리 켜기" overlay.

---

## ⚠️ Constraints & Guidelines

1. **No Mandatory User Authentication**: Access must remain completely open and anonymous (cookie/localStorage session only).
2. **Audio Autoplay Compliance**: Modern browsers block audio without prior user interaction. Always provide an explicit initial tap gesture.
3. **Bandwidth Efficiency**: Do not stream raw audio through the server. The server only passes metadata, track IDs, and timecodes. Audio is fetched directly from YouTube/SoundCloud CDN on each client.
