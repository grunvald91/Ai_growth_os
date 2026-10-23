"""Прототип: рекламный ролик из кода + озвучка, синхронная по словам.
Запуск: python build.py [script.json] [out_dir]
"""
import json, math, re, subprocess, sys, wave
from pathlib import Path
import numpy as np

import os
HERE = Path(__file__).parent
SCRIPT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "script.example.json"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
VOICE = Path(os.environ.get("AD_VOICE_DIR", Path.home() / ".cache/ad_video/voices")) / "ru_RU-irina-medium.onnx"
SR, FPS, LEAD, TAIL, MIN_BEAT = 44100, 30, 0.35, 0.55, 2.6

data = json.loads(SCRIPT.read_text())
rng = np.random.default_rng(7)


def norm(w):
    return re.sub(r"[^\wё]", "", w.lower())


def tts(text, path):
    subprocess.run([sys.executable, "-m", "piper", "-m", str(VOICE), "-f", str(path)],
                   input=text.encode(), check=True, capture_output=True)


def read_wav(path):
    with wave.open(str(path)) as w:
        sr, n = w.getframerate(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
    return np.interp(np.arange(0, len(x) * SR / sr) * sr / SR, np.arange(len(x)), x).astype(np.float32)


_whisper = None
def word_times(path, words, dur):
    """Времена слов внутри реплики: faster-whisper, а если не вышло, пропорционально длине слов."""
    global _whisper
    try:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel("small", device="cpu", compute_type="int8")
        segs, _ = _whisper.transcribe(str(path), language="ru", word_timestamps=True)
        got = [w for s in segs for w in s.words]
        if len(got) == len(words):
            return [(w.start, w.end) for w in got], "whisper"
    except Exception as e:
        print("whisper недоступен:", type(e).__name__, str(e)[:80])
    lens = np.array([max(2, len(norm(w))) for w in words], float)
    edges = np.concatenate([[0], np.cumsum(lens)]) / lens.sum() * dur
    return [(edges[i], edges[i + 1]) for i in range(len(words))], "proportional"


# ---------- 1. озвучка и тайминг ----------
timeline, voices, S, method = [], [], 0.0, set()
for i, b in enumerate(data["beats"]):
    wav = OUT / f"beat{i}.wav"
    tts(b["voice"], wav)
    audio = read_wav(wav)
    dur = len(audio) / SR
    words = b["voice"].replace("—", " ").split()
    wt, how = word_times(wav, words, dur); method.add(how)
    v0 = S + LEAD
    end = max(v0 + dur + TAIL, S + MIN_BEAT)
    ws = [{"text": w.strip(".,!?"), "t0": v0 + a, "t1": v0 + z} for w, (a, z) in zip(words, wt)]
    timeline.append({"s": S, "v0": v0, "end": end, "words": ws, "lines": b.get("lines")})
    voices.append((v0, audio))
    # реплики-«сигналы»: слово из текста -> время, когда оно произносится
    cues = {}
    for name, key in (b.get("cues") or {}).items():
        hit = next((w for w in ws if norm(w["text"]) == norm(key)), None)
        cues[name] = hit["t0"] if hit else v0
    b["cues"] = cues
    S = end
total = S + 0.5
print(f"тайминг: {how}, итого {total:.2f} c")
(OUT / "timeline.json").write_text(json.dumps({"beats": timeline, "total": total}, ensure_ascii=False, indent=1))

# ---------- 2. звук: голос + музыка с ducking + эффекты ----------
N = int(total * SR)
t = np.arange(N) / SR
voice = np.zeros(N, np.float32)
for v0, a in voices:
    k = int(v0 * SR); voice[k:k + len(a)] += a[: N - k]
voice_env = np.zeros(N, np.float32)
for v0, a in voices:
    k = int(v0 * SR); voice_env[k:k + len(a)] = 1
sm = int(0.18 * SR)
voice_env = np.convolve(voice_env, np.ones(sm) / sm, mode="same")
duck = 1 - 0.62 * np.clip(voice_env * 1.6, 0, 1)

chords = [(220.0, 261.63, 329.63), (174.61, 220.0, 261.63), (196.0, 246.94, 293.66), (164.81, 196.0, 246.94)]
music = np.zeros(N, np.float32)
bar = 2.4
for k, tt in enumerate(np.arange(0, total + bar, bar)):
    ch = chords[k % 4]
    a, z = int(tt * SR), min(N, int((tt + bar + 0.3) * SR))
    if a >= N: break
    tx = t[a:z] - tt
    env = np.minimum(1, tx / 0.4) * np.exp(-np.maximum(0, tx - bar) * 6)
    for f in ch:
        music[a:z] += (np.sin(2 * np.pi * f * tx) + .3 * np.sin(4 * np.pi * f * tx)) * env * 0.05
    # мягкий пульс
    for q in range(4):
        p = int((tt + q * bar / 4) * SR)
        if p + 4000 < N:
            tp = np.arange(4000) / SR
            music[p:p + 4000] += np.sin(2 * np.pi * 110 * tp) * np.exp(-tp * 28) * 0.10
music *= duck


def sfx_at(buf, when, kind):
    k = int(when * SR)
    if kind == "whoosh":
        n = int(0.55 * SR); tx = np.arange(n) / SR
        noise = rng.standard_normal(n).astype(np.float32)
        lp = np.zeros(n, np.float32); acc = 0.0
        for j in range(n):                       # однополюсный фильтр, частота растёт со временем
            c = 0.02 + 0.5 * (j / n) ** 2; acc += c * (noise[j] - acc); lp[j] = acc
        e = np.sin(np.pi * np.clip(tx / 0.55, 0, 1)) ** 2
        x = lp * e * 1.6
    elif kind == "pop":
        n = int(0.18 * SR); tx = np.arange(n) / SR
        x = np.sin(2 * np.pi * (500 + 900 * np.exp(-tx * 40)) * tx) * np.exp(-tx * 24) * 0.55
    elif kind == "click":
        n = int(0.25 * SR); tx = np.arange(n) / SR
        x = (rng.standard_normal(n) * np.exp(-tx * 90) * .35 + np.sin(2 * np.pi * 1200 * tx) * np.exp(-tx * 30) * .45).astype(np.float32)
    else:  # ding
        n = int(1.3 * SR); tx = np.arange(n) / SR
        x = (np.sin(2 * np.pi * 1318.5 * tx) + .5 * np.sin(2 * np.pi * 1975 * tx)) * np.exp(-tx * 3.2) * 0.32
    m = min(len(x), N - k)
    if m > 0: buf[k:k + m] += x[:m]


fx = np.zeros(N, np.float32)
for i, b in enumerate(data["beats"]):
    if i: sfx_at(fx, timeline[i]["s"] + 0.05, "whoosh")
    for name, ct in b["cues"].items():
        if name == "click": sfx_at(fx, ct, "click")
        elif name == "done": pass
        else: sfx_at(fx, ct, "pop")
    if b["type"] == "cta": sfx_at(fx, b["cues"]["click"] + 0.12, "ding")
mix = voice * 1.0 + music + fx * 0.8
mix = np.tanh(mix * 1.1) / 1.1
pcm = (np.clip(mix, -1, 1) * 32767).astype(np.int16)
with wave.open(str(OUT / "mix.wav"), "w") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())

# ---------- 3. кадры: браузер рисует каждый кадр строго по времени ----------
from playwright.sync_api import sync_playwright
frames = int(total * FPS)
ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-i", "-",
                       "-i", str(OUT / "mix.wav"), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                       "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(OUT / "ad.mp4")],
                      stdin=subprocess.PIPE)
issues = []
with sync_playwright() as p:
    br = p.chromium.launch()
    page = br.new_page(viewport={"width": 1080, "height": 1920})
    page.goto((HERE / "template.html").as_uri())
    page.evaluate("([d,t]) => window.init(d,t)", [data, {"beats": timeline}])
    page.evaluate("document.fonts.ready")
    sample = {int(x * FPS): i for i, b in enumerate(timeline) for x in (b["s"] + 1.2, b["end"] - 0.2)}
    shots = []
    for f in range(frames):
        tm = f / FPS
        page.evaluate("t => window.seek(t)", tm)
        if f in sample:
            bad = page.evaluate("t => window.audit(t)", tm)
            if bad: issues.append((round(tm, 2), bad))
            page.screenshot(path=str(OUT / f"f{f:04d}.png"))
            shots.append({"beat": sample[f], "t": round(tm, 2), "file": f"f{f:04d}.png"})
        ff.stdin.write(page.screenshot(type="png"))
    br.close()
ff.stdin.close(); ff.wait()
report = {
    "total_seconds": round(total, 2), "timing_method": how,
    "beats": [{"i": i, "type": b["type"], "seconds": round(t["end"] - t["s"], 2), "voice": b["voice"],
               "words_per_second": round(len(t["words"]) / max(0.1, t["end"] - t["v0"]), 2)}
              for i, (b, t) in enumerate(zip(data["beats"], timeline))],
    "safe_zone_issues": issues, "frames": shots,
}
(OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
print("кадров:", frames, "| замечания по безопасной зоне:", issues or "нет")
print("готово:", OUT / "ad.mp4")
