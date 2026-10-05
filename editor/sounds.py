"""효과음·배경음.

assets/sfx/<이름>.wav, assets/bgm/*.(mp3|wav|m4a) 에 내 파일을 넣으면 그걸 쓴다.
없으면 저작권 걱정 없는 소리를 직접 합성해서 만든다.
"""
from __future__ import annotations

import random
from pathlib import Path

import numpy as np

from .common import ASSETS, ffmpeg, log

SR = 48000


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.minimum(1, t / max(attack, 1e-4))
    return a * np.exp(-t / decay)


def _pop() -> np.ndarray:
    n = int(SR * 0.16)
    t = np.arange(n) / SR
    f = 900 * np.exp(-t * 18) + 280
    return 0.9 * np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.002, 0.045)


def _whoosh() -> np.ndarray:
    n = int(SR * 0.55)
    rng = np.random.default_rng(1)
    noise = rng.standard_normal(n)
    t = np.linspace(0, 1, n)
    out = np.zeros(n)
    # 움직이는 대역통과: 저역→고역 스윕
    win = 2048
    hop = 512
    for s in range(0, n - win, hop):
        seg = noise[s:s + win] * np.hanning(win)
        sp = np.fft.rfft(seg)
        fc = 300 + 4000 * (s / n) ** 1.5
        fr = np.fft.rfftfreq(win, 1 / SR)
        sp *= np.exp(-((fr - fc) / (fc * 0.6)) ** 2)
        out[s:s + win] += np.fft.irfft(sp, win)
    env = np.sin(np.pi * t) ** 1.5
    out = out * env
    return 0.9 * out / (np.abs(out).max() + 1e-9)


def _ding() -> np.ndarray:
    n = int(SR * 1.2)
    t = np.arange(n) / SR
    s = sum(a * np.sin(2 * np.pi * 1318.5 * m * t) * np.exp(-t * d)
            for a, m, d in [(1, 1, 3), (0.5, 2.0, 5), (0.25, 3.01, 8), (0.15, 4.2, 12)])
    return 0.6 * s * _env(n, 0.002, 10)


def _click() -> np.ndarray:
    n = int(SR * 0.05)
    t = np.arange(n) / SR
    return 0.7 * np.sin(2 * np.pi * 2200 * t) * _env(n, 0.0005, 0.008)


SYNTH = {"pop": _pop, "whoosh": _whoosh, "ding": _ding, "click": _click}


def sfx_path(name: str) -> Path | None:
    if not name:
        return None
    d = ASSETS / "sfx"
    d.mkdir(parents=True, exist_ok=True)
    for ext in ("wav", "mp3", "m4a", "ogg"):
        p = d / f"{name}.{ext}"
        if p.exists():
            return p
    if name in SYNTH:
        import soundfile as sf

        p = d / f"{name}.wav"
        sf.write(str(p), SYNTH[name]().astype(np.float32), SR)
        return p
    return None


def load_audio(path: Path, work: Path) -> np.ndarray:
    import soundfile as sf

    tmp = work / f"_ld_{path.stem}.wav"
    ffmpeg("-i", path, "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", tmp)
    a, _ = sf.read(str(tmp), dtype="float32", always_2d=True)
    tmp.unlink(missing_ok=True)
    return a


def synth_bgm(path: Path, seconds: float = 64.0, seed: int = 7) -> None:
    """가볍고 밝은 로파이 느낌 배경음 (반복 재생용)."""
    import soundfile as sf

    rng = random.Random(seed)
    bpm = 92
    beat = 60 / bpm
    n = int(SR * seconds)
    out = np.zeros(n)
    roots = [57, 53, 48, 55]  # A minor - F - C - G
    def midi(m):
        return 440 * 2 ** ((m - 69) / 12)
    bar = beat * 4
    for b in range(int(seconds / bar) + 1):
        r = roots[b % 4]
        chord = [r, r + (3 if b % 4 == 0 else 4), r + 7, r + 12]
        s0 = int(b * bar * SR)
        s1 = min(n, int((b + 1) * bar * SR))
        if s0 >= n:
            break
        t = np.arange(s1 - s0) / SR
        pad = sum(np.sin(2 * np.pi * midi(m) * t + 0.3 * np.sin(2 * np.pi * 0.5 * t)) for m in chord)
        env = np.minimum(1, t / 0.4) * np.minimum(1, (bar - t) / 0.4)
        out[s0:s1] += 0.06 * pad * env
        # 베이스
        bass = np.sin(2 * np.pi * midi(r - 12) * t) * np.exp(-((t % beat) / 0.5))
        out[s0:s1] += 0.12 * bass
        # 아르페지오
        for k in range(8):
            m = chord[rng.randrange(4)] + 12
            a = s0 + int(k * beat / 2 * SR)
            ln = int(0.35 * SR)
            if a + ln > n:
                break
            tt = np.arange(ln) / SR
            out[a:a + ln] += 0.05 * np.sin(2 * np.pi * midi(m) * tt) * np.exp(-tt / 0.12)
    # 킥/하이햇
    for k in range(int(seconds / beat)):
        a = int(k * beat * SR)
        ln = int(0.2 * SR)
        if a + ln > n:
            break
        tt = np.arange(ln) / SR
        if k % 2 == 0:
            out[a:a + ln] += 0.25 * np.sin(2 * np.pi * (50 + 80 * np.exp(-tt * 30)) * tt) * np.exp(-tt / 0.08)
        h = a + int(beat / 2 * SR)
        if h + 1200 < n:
            out[h:h + 1200] += 0.03 * np.random.default_rng(k).standard_normal(1200) * np.exp(-np.arange(1200) / 200)
    out = np.tanh(out * 1.5) / 1.5
    # 이음새 없이 반복되도록 끝을 처음과 크로스페이드
    xf = int(SR * 1.0)
    out[:xf] = out[:xf] * np.linspace(0, 1, xf) + out[-xf:] * np.linspace(1, 0, xf)
    out = out[:-xf]
    stereo = np.stack([out, np.roll(out, 240)], axis=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), (0.8 * stereo / np.abs(stereo).max()).astype(np.float32), SR)


def bgm_file(rules: dict) -> Path | None:
    from .common import asset

    d = asset(rules["bgm"]["folder"])
    d.mkdir(parents=True, exist_ok=True)
    files = sorted([p for p in d.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac")])
    if files:
        return files[0]
    p = d / "auto_lofi.wav"
    log("배경음 파일이 없어 직접 합성합니다 → assets/bgm/auto_lofi.wav (내 음악을 이 폴더에 넣으면 그걸 씁니다)")
    synth_bgm(p)
    return p


def db(x: float) -> float:
    return 10 ** (x / 20)


def mix(voice: np.ndarray, events: list[tuple[float, str]], rules: dict, work: Path,
        music_ranges: list[tuple[float, float]] | None = None) -> np.ndarray:
    """목소리 + 효과음(events: (시각, 이름)) + 배경음(목소리 나올 때 자동으로 작아짐)."""
    out = voice.copy()
    n = len(out)
    s = rules["sfx"]
    if s.get("enabled", True):
        cache: dict[str, np.ndarray] = {}
        for t, name in events:
            p = sfx_path(name)
            if p is None:
                continue
            if name not in cache:
                cache[name] = load_audio(p, work) * db(s["volume_db"])
            a = cache[name]
            i0 = max(0, int((t - s.get("lead_ms", 30) / 1000) * SR))
            i1 = min(n, i0 + len(a))
            out[i0:i1] += a[: i1 - i0]
    b = rules["bgm"]
    if b.get("enabled", True):
        p = bgm_file(rules)
        if p:
            m = load_audio(p, work)
            reps = int(np.ceil(n / len(m)))
            m = np.tile(m, (reps, 1))[:n] * db(b["volume_db"])
            if b.get("duck_under_voice", True):
                # 목소리 크기를 따라 배경음을 줄인다(사이드체인 덕킹)
                mono = np.abs(voice).mean(axis=1)
                win = int(SR * 0.05)
                envv = np.convolve(mono, np.ones(win) / win, mode="same")
                talking = (envv > 0.01).astype(np.float32)
                sm = int(SR * 0.25)
                talking = np.convolve(talking, np.ones(sm) / sm, mode="same")
                gain = 1 - (1 - db(b["duck_db"])) * np.clip(talking, 0, 1)
                m *= gain[:, None]
            env = np.ones(n, dtype=np.float32)
            if music_ranges is not None:
                env[:] = 0
                for a0, a1 in music_ranges:
                    env[int(a0 * SR):int(a1 * SR)] = 1
            fi, fo = int(b["fade_in_sec"] * SR), int(b["fade_out_sec"] * SR)
            env[:fi] *= np.linspace(0, 1, min(fi, n))
            env[-fo:] *= np.linspace(1, 0, min(fo, n))
            out += m * env[:, None]
    peak = np.abs(out).max()
    if peak > 0.99:
        out *= 0.99 / peak
    return out.astype(np.float32)
