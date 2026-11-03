// Sansa DJ · Minimal Online DJ & Lounge MVP
// Web Audio API Synthesizer & Client State

class SoundEngine {
  constructor() {
    this.ctx = null;
    this.isPlaying = false;
    this.filterNode = null;
    this.gainNode = null;
    this.timerId = null;
    this.step = 0;
    this.bpm = 84;
  }

  init() {
    if (this.ctx) return;
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    this.ctx = new AudioContext();

    this.filterNode = this.ctx.createBiquadFilter();
    this.filterNode.type = 'lowpass';
    this.filterNode.frequency.value = 4000;

    this.gainNode = this.ctx.createGain();
    this.gainNode.gain.value = 0.4;

    this.filterNode.connect(this.gainNode);
    this.gainNode.connect(this.ctx.destination);
  }

  setFilter(freq) {
    if (this.filterNode) {
      this.filterNode.frequency.setTargetAtTime(freq, this.ctx.currentTime, 0.05);
    }
  }

  // Synthesized Lofi Beat Generator
  playKick(time) {
    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    osc.frequency.setValueAtTime(140, time);
    osc.frequency.exponentialRampToValueAtTime(38, time + 0.12);
    gain.gain.setValueAtTime(1, time);
    gain.gain.exponentialRampToValueAtTime(0.001, time + 0.25);
    osc.connect(gain);
    gain.connect(this.filterNode);
    osc.start(time);
    osc.stop(time + 0.25);
  }

  playSnare(time) {
    // Noise buffer
    const bufferSize = this.ctx.sampleRate * 0.15;
    const buffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < bufferSize; i++) {
      data[i] = Math.random() * 2 - 1;
    }
    const noise = this.ctx.createBufferSource();
    noise.buffer = buffer;
    const filter = this.ctx.createBiquadFilter();
    filter.type = 'highpass';
    filter.frequency.value = 800;
    const gain = this.ctx.createGain();
    gain.gain.setValueAtTime(0.35, time);
    gain.gain.exponentialRampToValueAtTime(0.001, time + 0.15);
    noise.connect(filter);
    filter.connect(gain);
    gain.connect(this.filterNode);
    noise.start(time);
  }

  playHihat(time) {
    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    osc.type = 'highpass';
    osc.frequency.setValueAtTime(6000, time);
    gain.gain.setValueAtTime(0.12, time);
    gain.gain.exponentialRampToValueAtTime(0.001, time + 0.04);
    osc.connect(gain);
    gain.connect(this.filterNode);
    osc.start(time);
    osc.stop(time + 0.05);
  }

  playChord(time, freqs) {
    freqs.forEach(f => {
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.type = 'triangle';
      osc.frequency.setValueAtTime(f, time);
      gain.gain.setValueAtTime(0.08, time);
      gain.gain.exponentialRampToValueAtTime(0.001, time + 0.8);
      osc.connect(gain);
      gain.connect(this.filterNode);
      osc.start(time);
      osc.stop(time + 0.85);
    });
  }

  // FX: Vinyl Scratch
  playScratch() {
    this.init();
    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    const now = this.ctx.currentTime;
    osc.type = 'sawtooth';
    osc.frequency.setValueAtTime(300, now);
    osc.frequency.linearRampToValueAtTime(1400, now + 0.08);
    osc.frequency.linearRampToValueAtTime(120, now + 0.18);
    gain.gain.setValueAtTime(0.25, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.2);
    osc.connect(gain);
    gain.connect(this.ctx.destination);
    osc.start(now);
    osc.stop(now + 0.22);
  }

  // FX: Airhorn
  playHorn() {
    this.init();
    const freqs = [466.16, 466.16, 466.16];
    const now = this.ctx.currentTime;
    for (let burst = 0; burst < 3; burst++) {
      const burstTime = now + burst * 0.12;
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(466.16, burstTime);
      gain.gain.setValueAtTime(0.2, burstTime);
      gain.gain.exponentialRampToValueAtTime(0.001, burstTime + 0.1);
      osc.connect(gain);
      gain.connect(this.ctx.destination);
      osc.start(burstTime);
      osc.stop(burstTime + 0.11);
    }
  }

  start() {
    this.init();
    if (this.ctx.state === 'suspended') {
      this.ctx.resume();
    }
    this.isPlaying = true;
    const stepTime = (60 / this.bpm) / 4; // 16th notes
    const chords = [
      [261.63, 329.63, 392.00, 493.88], // Cmaj7
      [220.00, 261.63, 329.63, 392.00], // Am7
      [174.61, 220.00, 261.63, 329.63], // Fmaj7
      [196.00, 246.94, 293.66, 349.23]  // G7
    ];

    let chordIdx = 0;
    this.timerId = setInterval(() => {
      const now = this.ctx.currentTime;
      // Kick on 0, 8
      if (this.step % 8 === 0) this.playKick(now);
      // Snare on 4, 12
      if (this.step % 8 === 4) this.playSnare(now);
      // Hihat on every even 16th
      if (this.step % 2 === 0) this.playHihat(now);
      // Chords every 16 steps
      if (this.step % 16 === 0) {
        this.playChord(now, chords[chordIdx % chords.length]);
        chordIdx++;
      }

      this.step = (this.step + 1) % 64;
    }, stepTime * 1000);
  }

  stop() {
    this.isPlaying = false;
    clearInterval(this.timerId);
  }
}

// App Controller & UI
document.addEventListener('DOMContentLoaded', () => {
  const engine = new SoundEngine();

  // Elements
  const vinyl = document.getElementById('vinyl');
  const tonearm = document.getElementById('tonearm');
  const btnPlayToggle = document.getElementById('btn-play-toggle');
  const filterSlider = document.getElementById('filter-slider');
  const visualizer = document.getElementById('visualizer');
  const audienceGrid = document.getElementById('audience-grid');
  const reactionLayer = document.getElementById('reaction-layer');
  const tabAudience = document.getElementById('tab-audience');
  const tabDj = document.getElementById('tab-dj');
  const audienceControls = document.getElementById('audience-controls');
  const djControls = document.getElementById('dj-controls');
  const btnQueue = document.getElementById('btn-queue');

  // Generate 24 visualizer bars
  for (let i = 0; i < 24; i++) {
    const bar = document.createElement('div');
    bar.className = 'v-bar';
    visualizer.appendChild(bar);
  }
  const bars = document.querySelectorAll('.v-bar');

  // Generate Mock Audience Avatars (산사 스타일: 나: 작은 솔잎, 고요한 밤, 찻잔, 달빛 등)
  const avatarPool = [
    { name: '나 (작은솔잎)', icon: '🌱', me: true },
    { name: '새벽안개', icon: '🌫️' },
    { name: '달빛스님', icon: '🌙' },
    { name: '목탁비트', icon: '🪵' },
    { name: '연꽃', icon: '🪷' },
    { name: '바람소리', icon: '🍃' },
    { name: '솔바람', icon: '🌲' },
    { name: '찻잔', icon: '🍵' },
    { name: '고요한밤', icon: '🌌' },
    { name: '단청', icon: '🎨' }
  ];

  avatarPool.forEach(item => {
    const card = document.createElement('div');
    card.className = `avatar-card bobbing ${item.me ? 'me' : ''}`;
    card.innerHTML = `
      <div class="avatar-body">${item.icon}</div>
      <div class="avatar-name">${item.name}</div>
    `;
    audienceGrid.appendChild(card);
  });

  // Role switching
  tabAudience.addEventListener('click', () => {
    tabAudience.classList.add('active');
    tabDj.classList.remove('active');
    audienceControls.classList.remove('hidden');
    djControls.classList.add('hidden');
  });

  tabDj.addEventListener('click', () => {
    tabDj.classList.add('active');
    tabAudience.classList.remove('active');
    djControls.classList.remove('hidden');
    audienceControls.classList.add('hidden');
  });

  // Play / Pause Toggle
  btnPlayToggle.addEventListener('click', () => {
    if (!engine.isPlaying) {
      engine.start();
      btnPlayToggle.textContent = '⏸ 일시 정지';
      btnPlayToggle.style.background = '#ff5400';
      vinyl.classList.add('spinning');
      tonearm.style.transform = 'rotate(36deg)';
      startVisualizer();
    } else {
      engine.stop();
      btnPlayToggle.textContent = '▶ 음악 재생';
      btnPlayToggle.style.background = 'var(--neon-cyan)';
      vinyl.classList.remove('spinning');
      tonearm.style.transform = 'rotate(20deg)';
      stopVisualizer();
    }
  });

  // Filter Slider
  filterSlider.addEventListener('input', (e) => {
    engine.setFilter(parseFloat(e.target.value));
  });

  // FX Buttons
  document.querySelectorAll('.btn-fx').forEach(btn => {
    btn.addEventListener('click', () => {
      const fx = btn.dataset.fx;
      if (fx === 'scratch') engine.playScratch();
      if (fx === 'horn') engine.playHorn();
      spawnFloatingReaction('⚡');
    });
  });

  // Reactions
  const rxMap = {
    glowstick: '🪄',
    headbang: '🕺',
    fire: '🔥',
    namaste: '🙏'
  };

  document.querySelectorAll('.rx-btn[data-reaction]').forEach(btn => {
    btn.addEventListener('click', () => {
      const rx = rxMap[btn.dataset.reaction] || '✨';
      spawnFloatingReaction(rx);
    });
  });

  btnQueue.addEventListener('click', () => {
    btnQueue.classList.toggle('active');
    const isQueued = btnQueue.classList.contains('active');
    btnQueue.querySelector('.rx-text').textContent = isQueued ? '대기 중 (2번)' : 'DJ 줄서기';
    spawnFloatingReaction('🎧');
  });

  function spawnFloatingReaction(icon) {
    const rx = document.createElement('div');
    rx.className = 'floating-rx';
    rx.textContent = icon;
    rx.style.left = `${30 + Math.random() * 40}%`;
    rx.style.bottom = `${20 + Math.random() * 30}%`;
    reactionLayer.appendChild(rx);
    setTimeout(() => rx.remove(), 1800);
  }

  // Periodic random reactions from others (산사 느낌의 소셜 공존감)
  setInterval(() => {
    if (Math.random() > 0.4) {
      const randomIcons = ['🙏', '🌱', '🔥', '🪄', '✨'];
      const icon = randomIcons[Math.floor(Math.random() * randomIcons.length)];
      spawnFloatingReaction(icon);
    }
  }, 2400);

  // Visualizer Animation Loop
  let visInterval = null;
  function startVisualizer() {
    visInterval = setInterval(() => {
      bars.forEach((b, i) => {
        const height = 4 + Math.sin(Date.now() * 0.008 + i) * 12 + Math.random() * 16;
        b.style.height = `${Math.max(4, height)}px`;
      });
    }, 80);
  }

  function stopVisualizer() {
    clearInterval(visInterval);
    bars.forEach(b => b.style.height = '6px');
  }
});
