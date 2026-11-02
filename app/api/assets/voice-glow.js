/* VoiceGlow — faithful 44-bar visual from the supplied demo.
   In Growth Studio start(stream) MUST reuse MediaRecorder's MediaStream:
   this module never requests or owns microphone permission/tracks. */
(function () {
  'use strict';
  var root = document.getElementById('dp-voice-overlay');
  if (!root || !root.classList.contains('vg')) return;
  var label = document.getElementById('dp-voice-overlay-text');
  var cv = document.getElementById('vgBars');
  var knob = document.getElementById('vgKnob');
  if (!cv || !label || !knob) return;
  var ctx = cv.getContext('2d');
  if (!ctx) return;

  var N = 44, PITCH = 5.21, BAR_W = 1.5, H_MIN = 5.5, H_MAX = 32;
  var ATTACK = 0.5, RELEASE = 0.2, REROLL_MS = 80;
  var W = 240, H = 80, dpr = Math.min(window.devicePixelRatio || 1, 3);
  cv.width = Math.round(W * dpr);
  cv.height = Math.round(H * dpr);
  ctx.scale(dpr, dpr);

  var vals = new Float32Array(N).fill(H_MIN);
  var wts = new Float32Array(N).fill(0);
  var mode = 'idle', raf = 0, lastRoll = 0, level = 0, t0 = 0;
  var analyser = null, samples = null, source = null, audioContext = null;
  var simulated = false, endTimer = null, generation = 0;

  function edge(i) {
    var u = Math.min(i, N - 1 - i) / 9, t = Math.min(1, u);
    return .15 + .85 * t * t * (3 - 2 * t);
  }
  function envelope(i) {
    return .55 + .45 * Math.cos((i - (N - 1) / 2) / ((N - 1) / 2) * Math.PI / 2 * 1.1);
  }
  function reroll() {
    for (var i = 0; i < N; i++) {
      var r = Math.pow(Math.random(), 1.7);
      var near = (wts[i - 1] || 0) * .25 + (wts[i + 1] || 0) * .25;
      wts[i] = Math.min(1, (r + near * .5) * envelope(i) * 1.5);
    }
  }
  function readLevel(now) {
    if (analyser && samples) {
      analyser.getByteTimeDomainData(samples);
      var sum = 0;
      for (var i = 0; i < samples.length; i++) {
        var v = (samples[i] - 128) / 128;
        sum += v * v;
      }
      // RMS amplitude, with a small noise floor. This measures loudness, NOT pitch.
      return Math.min(1, Math.max(0, (Math.sqrt(sum / samples.length) - .012) * 8));
    }
    if (simulated) {
      var t = (now - t0) / 1000;
      var gate = Math.sin(t * .9) > -.25 ? 1 : 0;
      return gate * Math.max(0, Math.sin(t * 7.1) * Math.sin(t * 2.3 + 1)) * .95;
    }
    return 0; // No analyser: do not claim to hear the speaker.
  }
  function frame(now) {
    raf = requestAnimationFrame(frame);
    var target = mode === 'listen' ? readLevel(now) : 0;
    level += (target - level) * (target > level ? ATTACK : RELEASE);
    if (mode === 'listen' && level > .08 && now - lastRoll > REROLL_MS) {
      reroll(); lastRoll = now;
    }
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#fff'; ctx.lineCap = 'round';
    var x0 = (W - (N - 1) * PITCH) / 2;
    for (var i = 0; i < N; i++) {
      var goal = H_MIN + (H_MAX - H_MIN) * level * wts[i];
      vals[i] += (goal - vals[i]) * (goal > vals[i] ? ATTACK : RELEASE);
      var h = Math.max(H_MIN, vals[i]), x = x0 + i * PITCH;
      ctx.globalAlpha = (mode === 'rec' ? .55 : 1) * edge(i);
      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(x - BAR_W / 2, H / 2 - h / 2, BAR_W, h, BAR_W / 2);
        ctx.fill();
      } else {
        ctx.fillRect(x - BAR_W / 2, H / 2 - h / 2, BAR_W, h);
      }
    }
    ctx.globalAlpha = 1;
  }
  function animate() {
    if (!raf) raf = requestAnimationFrame(frame);
  }
  function releaseAudio() {
    analyser = null; samples = null;
    if (source) {
      try { source.disconnect(); } catch (ignored) {}
      source = null;
    }
    if (audioContext) {
      var previous = audioContext;
      audioContext = null;
      try {
        var closed = previous.close();
        if (closed && closed.catch) closed.catch(function () {});
      } catch (ignored) {}
    }
    // IMPORTANT: recordStream is owned and stopped by dispatcher.js, not here.
  }
  function reveal(next) {
    generation += 1;
    if (endTimer) { clearTimeout(endTimer); endTimer = null; }
    mode = next;
    root.classList.remove('is-exiting', 'off', 'rec');
    root.classList.add('is-active');
    root.setAttribute('aria-hidden', 'false');
    knob.style.opacity = '1';
    animate();
  }
  function connecting() {
    releaseAudio();
    simulated = false;
    reveal('connecting');
    label.textContent = 'Подключаю микрофон…';
  }
  function start(stream) {
    releaseAudio();
    simulated = false;
    level = 0;
    vals.fill(H_MIN);
    wts.fill(0);
    t0 = performance.now(); lastRoll = 0;
    reveal('listen');
    label.textContent = 'Отпустите, чтобы отправить';
    if (!stream) {
      // Standalone demo fallback only. Production always passes its recorder stream.
      simulated = true;
      label.textContent = 'Имитация · микрофон не подключён';
      return;
    }
    try {
      var Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) throw new Error('AudioContext unavailable');
      audioContext = new Audio();
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 512;
      samples = new Uint8Array(analyser.fftSize);
      source = audioContext.createMediaStreamSource(stream);
      source.connect(analyser);
      if (audioContext.state === 'suspended') {
        var resumed = audioContext.resume();
        if (resumed && resumed.catch) resumed.catch(function () {
          label.textContent = 'Запись идёт · индикатор звука недоступен';
          releaseAudio();
        });
      }
    } catch (error) {
      releaseAudio();
      label.textContent = 'Запись идёт · индикатор звука недоступен';
    }
  }
  function recognize() {
    if (mode !== 'listen' && mode !== 'connecting') return;
    releaseAudio();
    simulated = false;
    mode = 'rec';
    label.textContent = 'Распознавание…';
    knob.style.opacity = '0';
    root.classList.add('rec');
  }
  function hide() {
    if (mode === 'idle' && !root.classList.contains('is-active')) return;
    releaseAudio();
    simulated = false;
    mode = 'idle';
    generation += 1;
    var ticket = generation;
    root.classList.remove('is-active', 'rec');
    root.classList.add('is-exiting');
    root.setAttribute('aria-hidden', 'true');
    if (endTimer) clearTimeout(endTimer);
    endTimer = setTimeout(function () {
      if (ticket !== generation) return;
      root.classList.remove('is-exiting');
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      endTimer = null;
    }, 240);
  }
  function setKnobX(px) {
    if (Number.isFinite(px)) knob.style.left = Math.max(9, Math.min(window.innerWidth - 9, px)) + 'px';
  }
  window.VoiceGlow = {
    start: start, recognize: recognize, hide: hide,
    connecting: connecting, setKnobX: setKnobX
  };
})();
