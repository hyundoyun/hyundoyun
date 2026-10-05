"""1. 레퍼런스 영상 분석 → 편집 규칙집 갱신.

유튜브 링크들을 받아 장면 단위로 분해한다:
컷 간격, 자막 크기·색·위치, 강조 자막 빈도, 효과음·배경음 타이밍, 자료화면, 화면 확대, 인트로·아웃트로.
결과: refs/<영상ID>/analysis.json, refs/<영상ID>/contact_*.jpg(장면 썸네일), refs/summary.json
그리고 config/rules.json 의 해당 값과 docs/편집규칙집.md 의 '레퍼런스 측정값' 칸을 자동으로 고친다.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .common import ROOT, ffmpeg, load_rules, log, run, save_rules, write_json

REFS = ROOT / "refs"
SR = 16000


# ---------- 내려받기 ----------
def download(url: str) -> Path:
    REFS.mkdir(exist_ok=True)
    r = run(["yt-dlp", "--print", "id", "--skip-download", url])
    vid = r.stdout.strip().splitlines()[-1]
    d = REFS / vid
    d.mkdir(exist_ok=True)
    mp4 = d / "video.mp4"
    if not mp4.exists():
        log(f"레퍼런스 내려받는 중: {url}")
        run(["yt-dlp", "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]", "--merge-output-format", "mp4",
             "-o", str(mp4), "--write-info-json", "--no-playlist", url])
        info = next(d.glob("*.info.json"), None)
        if info:
            info.rename(d / "info.json")
    return mp4


# ---------- 장면(컷) ----------
def read_frames(path: Path, fps: float = 10.0, width: int = 320):
    import cv2

    cap = cv2.VideoCapture(str(path))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, round(src_fps / fps))
    i = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, fr = cap.retrieve()
            if not ok:
                break
            h = int(fr.shape[0] * width / fr.shape[1])
            yield i / src_fps, cv2.resize(fr, (width, h))
        i += 1
    cap.release()


def detect_cuts(path: Path) -> tuple[list[float], list[tuple[float, np.ndarray]], float]:
    import cv2

    cuts, thumbs = [0.0], []
    prev_h = None
    t_last = 0.0
    diffs = []
    frames = []
    for t, fr in read_frames(path):
        hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([hsv], [0, 1, 2], None, [16, 8, 8], [0, 180, 0, 256, 0, 256])
        cv2.normalize(h, h)
        if prev_h is not None:
            diffs.append((t, 1 - cv2.compareHist(prev_h, h, cv2.HISTCMP_CORREL)))
        prev_h = h
        frames.append((t, fr))
        t_last = t
    if not diffs:
        return cuts, thumbs, t_last
    vals = np.array([d for _, d in diffs])
    thr = max(0.35, np.median(vals) + 6 * (np.percentile(vals, 75) - np.median(vals) + 1e-3))
    for t, d in diffs:
        if d > thr and t - cuts[-1] > 0.25:
            cuts.append(t)
    return cuts, frames, t_last


def zoom_between(a: np.ndarray, b: np.ndarray) -> float | None:
    """두 화면이 같은 장면이면 확대 배율을 돌려준다 (아니면 None)."""
    import cv2

    orb = cv2.ORB_create(600)
    ka, da = orb.detectAndCompute(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), None)
    kb, db_ = orb.detectAndCompute(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), None)
    if da is None or db_ is None or len(ka) < 20 or len(kb) < 20:
        return None
    m = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db_)
    if len(m) < 25:
        return None
    pa = np.float32([ka[x.queryIdx].pt for x in m])
    pb = np.float32([kb[x.trainIdx].pt for x in m])
    M, inl = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=3)
    if M is None or inl is None or inl.sum() < 20:
        return None
    return float(np.sqrt(M[0, 0] ** 2 + M[0, 1] ** 2))


def has_face(img: np.ndarray) -> bool:
    import cv2

    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (g.shape[1] * 2, g.shape[0] * 2))
    return len(casc.detectMultiScale(g, 1.1, 5, minSize=(40, 40))) > 0


# ---------- 자막 ----------
def text_boxes(png: Path):
    import pytesseract
    from PIL import Image

    img = Image.open(png).convert("RGB")
    W, H = img.size
    try:
        d = pytesseract.image_to_data(img, lang="kor+eng", output_type=pytesseract.Output.DICT, config="--psm 11", timeout=90)
    except RuntimeError:  # 너무 오래 걸리는 화면은 건너뜀
        return []
    out = []
    arr = np.asarray(img)
    for i, txt in enumerate(d["text"]):
        txt = txt.strip()
        if len(txt) < 2 or float(d["conf"][i]) < 55 or not re.search(r"[가-힣]", txt):
            continue
        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
        crop = arr[y:y + h, x:x + w].reshape(-1, 3).astype(float)
        if len(crop) < 20:
            continue
        lum = crop.mean(axis=1)
        bright = crop[lum >= np.percentile(lum, 80)].mean(axis=0)
        dark = crop[lum <= np.percentile(lum, 15)].mean(axis=0)
        sat = crop.max(axis=1) - crop.min(axis=1)
        vivid = crop[sat >= np.percentile(sat, 85)].mean(axis=0)
        out.append({"text": txt, "y": (y + h / 2) / H, "x": (x + w / 2) / W, "h_px1080": h * 1080 / H,
                    "bright": bright.round().tolist(), "dark": dark.round().tolist(), "vivid": vivid.round().tolist()})
    return out


def hexc(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*[int(max(0, min(255, c))) for c in rgb])


# ---------- 소리 ----------
def audio_features(path: Path, d: Path, cuts: list[float]) -> dict:
    import soundfile as sf

    wav = d / "a16k.wav"
    ffmpeg("-i", path, "-vn", "-ac", "1", "-ar", str(SR), wav)
    a, _ = sf.read(str(wav), dtype="float32")
    from .transcribe import VAD_PATH
    import sherpa_onnx

    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(VAD_PATH)
    cfg.silero_vad.min_silence_duration = 0.2
    cfg.sample_rate = SR
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)
    speech = []
    win = cfg.silero_vad.window_size
    for i in range(0, len(a), win):
        vad.accept_waveform(a[i:i + win])
        while not vad.empty():
            speech.append((vad.front.start / SR, vad.front.start / SR + len(vad.front.samples) / SR))
            vad.pop()
    vad.flush()
    while not vad.empty():
        speech.append((vad.front.start / SR, vad.front.start / SR + len(vad.front.samples) / SR))
        vad.pop()
    hop = 0.02
    n = int(SR * hop)
    k = len(a) // n
    fr = a[: k * n].reshape(k, n)
    rms = 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-9)
    is_sp = np.zeros(k, bool)
    for s, e in speech:
        is_sp[int(s / hop):int(e / hop)] = True
    gaps = rms[~is_sp]
    sp_level = float(np.median(rms[is_sp])) if is_sp.any() else -20
    gap_level = float(np.median(gaps)) if len(gaps) else -90
    # 효과음: 짧고 강한 소리 튐(스펙트럼 변화량 급증)
    spec = np.abs(np.fft.rfft(fr * np.hanning(n), axis=1))
    flux = np.maximum(0, np.diff(np.log1p(spec), axis=0)).sum(axis=1)
    z = (flux - np.median(flux)) / (np.percentile(flux, 90) - np.median(flux) + 1e-6)
    onsets = []
    for i in np.where(z > 4)[0]:
        t = (i + 1) * hop
        if not onsets or t - onsets[-1] > 0.3:
            onsets.append(round(t, 2))
    sfx_on_cut = sum(1 for c in cuts[1:] if any(abs(o - c) < 0.15 for o in onsets))
    return {
        "speech_ratio": round(float(is_sp.mean()), 3),
        "speech_level_db": round(sp_level, 1),
        "gap_level_db": round(gap_level, 1),
        "bgm_present": bool(gap_level > -52 and gaps.std() < 8 if len(gaps) else False),
        "bgm_relative_db": round(gap_level - sp_level, 1),
        "sfx_onsets": onsets,
        "sfx_per_min": round(len(onsets) / (len(a) / SR / 60), 2),
        "sfx_on_cut_ratio": round(sfx_on_cut / max(1, len(cuts) - 1), 2),
        "first_speech_sec": round(speech[0][0], 2) if speech else None,
        "last_speech_sec": round(speech[-1][1], 2) if speech else None,
        "pauses_median": round(float(np.median([b[0] - a_[1] for a_, b in zip(speech, speech[1:])])), 2) if len(speech) > 1 else None,
    }


def contact_sheet(frames, cuts, d: Path) -> list[str]:
    from PIL import Image, ImageDraw

    times = [(c + (cuts[i + 1] if i + 1 < len(cuts) else c + 1)) / 2 for i, c in enumerate(cuts)]
    thumbs = []
    ft = [f for f in frames]
    for t in times:
        fr = min(ft, key=lambda x: abs(x[0] - t))[1]
        thumbs.append((t, Image.fromarray(fr[:, :, ::-1])))
    sheets = []
    per = 30
    for s in range(0, len(thumbs), per):
        chunk = thumbs[s:s + per]
        w, h = chunk[0][1].size
        cols = 6
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * w, rows * (h + 20)), (20, 20, 20))
        dr = ImageDraw.Draw(sheet)
        for i, (t, im) in enumerate(chunk):
            x, y = (i % cols) * w, (i // cols) * (h + 20)
            sheet.paste(im, (x, y + 20))
            dr.text((x + 4, y + 3), f"#{s + i + 1} {t:.1f}s", fill=(255, 220, 0))
        p = d / f"contact_{s // per + 1}.jpg"
        sheet.save(p, quality=80)
        sheets.append(str(p))
    return sheets


def analyze_video(mp4: Path) -> dict:
    d = mp4.parent
    log(f"분석 중: {d.name}")
    cuts, frames, dur = detect_cuts(mp4)
    shots = np.diff(cuts + [dur])
    by_t = {round(t, 1): f for t, f in frames}

    def frame_at(t):
        k = min(by_t, key=lambda x: abs(x - t))
        return by_t[k]

    # 컷 경계에서 확대(점프컷 줌) / 장면 안에서 서서히 확대
    jump_zooms, slow_zooms, broll = [], [], []
    for i, c in enumerate(cuts[1:], 1):
        z = zoom_between(frame_at(c - 0.1), frame_at(c + 0.05))
        if z and abs(z - 1) > 0.04:
            jump_zooms.append({"t": round(c, 2), "scale": round(z, 3)})
    for i, c in enumerate(cuts):
        e = cuts[i + 1] if i + 1 < len(cuts) else dur
        mid = frame_at((c + e) / 2)
        if e - c > 1.5:
            z = zoom_between(frame_at(c + 0.1), frame_at(e - 0.15))
            if z and abs(z - 1) > 0.03:
                slow_zooms.append({"t": round(c, 2), "len": round(e - c, 2), "scale": round(z, 3)})
        if not has_face(mid):
            broll.append({"t": round(c, 2), "len": round(e - c, 2)})

    # 자막: 1초마다 글자 인식
    fdir = d / "frames"
    fdir.mkdir(exist_ok=True)
    ffmpeg("-i", mp4, "-vf", "fps=1,scale=1280:-2", fdir / "f_%05d.png")
    boxes = []
    pngs = sorted(fdir.glob("f_*.png"))
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(4) as ex:
        for i, bs in enumerate(ex.map(text_boxes, pngs)):
            for b in bs:
                b["t"] = i
                boxes.append(b)
    bottom = [b for b in boxes if b["y"] > 0.68]
    other = [b for b in boxes if b["y"] <= 0.68]
    subs = {}
    if bottom:
        subs = {
            "size_px_1080": round(float(np.median([b["h_px1080"] for b in bottom])) * 1.3),
            "center_y": round(float(np.median([b["y"] for b in bottom])), 3),
            "fill": hexc(np.median([b["bright"] for b in bottom], axis=0)),
            "outline_or_box": hexc(np.median([b["dark"] for b in bottom], axis=0)),
            "accent": hexc(np.median([b["vivid"] for b in bottom], axis=0)),
            "seconds_with_subtitle_ratio": round(len({b["t"] for b in bottom}) / max(1, len(pngs)), 2),
        }
    emph_events = sorted({b["t"] for b in other})
    emph_starts = [t for i, t in enumerate(emph_events) if i == 0 or t - emph_events[i - 1] > 1]
    emph = {}
    if other:
        emph = {
            "size_px_1080": round(float(np.median([b["h_px1080"] for b in other])) * 1.3),
            "center_y": round(float(np.median([b["y"] for b in other])), 3),
            "colors": [hexc(np.median([b["vivid"] for b in other], axis=0)), hexc(np.median([b["bright"] for b in other], axis=0))],
            "per_min": round(len(emph_starts) / (dur / 60), 2),
            "times": emph_starts[:80],
        }
    aud = audio_features(mp4, d, cuts)
    emph_with_sfx = sum(1 for t in emph_starts if any(abs(o - t) < 0.6 for o in aud["sfx_onsets"]))
    first15 = [c for c in cuts if c < 15]
    res = {
        "id": d.name,
        "duration": round(dur, 1),
        "cuts": {"count": len(cuts), "per_min": round(len(cuts) / (dur / 60), 1),
                 "shot_median": round(float(np.median(shots)), 2), "shot_mean": round(float(np.mean(shots)), 2),
                 "shot_p10": round(float(np.percentile(shots, 10)), 2), "shot_p90": round(float(np.percentile(shots, 90)), 2),
                 "first_60s_per_min": len([c for c in cuts if c < 60]), "times": [round(c, 2) for c in cuts]},
        "zoom": {"jumpcut_zoom_ratio": round(len(jump_zooms) / max(1, len(cuts) - 1), 2),
                 "jump_scales": [z["scale"] for z in jump_zooms],
                 "jump_scales_median": round(float(np.median([max(z["scale"], 1 / z["scale"]) for z in jump_zooms])), 3) if jump_zooms else None,
                 "slow_zoom_shots": len(slow_zooms),
                 "detail": jump_zooms[:50]},
        "subtitle": subs,
        "emphasis": {**emph, "with_sfx_ratio": round(emph_with_sfx / max(1, len(emph_starts)), 2)},
        "broll": {"shots": len(broll), "share": round(sum(b["len"] for b in broll) / dur, 3),
                  "avg_len": round(float(np.mean([b["len"] for b in broll])), 2) if broll else None, "detail": broll[:60]},
        "audio": {k: v for k, v in aud.items() if k != "sfx_onsets"} | {"sfx_onsets": aud["sfx_onsets"][:200]},
        "intro": {"cuts_in_first_15s": len(first15), "first_speech_sec": aud["first_speech_sec"],
                  "text_in_first_3s": any(b["t"] < 3 for b in boxes)},
        "outro": {"tail_after_last_speech_sec": round(dur - (aud["last_speech_sec"] or dur), 1)},
    }
    res["contact_sheets"] = contact_sheet(frames, cuts, d)
    write_json(d / "analysis.json", res)
    for p in pngs:
        p.unlink()
    return res


def summarize(results: list[dict]) -> dict:
    def med(path, default=None):
        vals = []
        for r in results:
            v = r
            for k in path.split("."):
                v = v.get(k) if isinstance(v, dict) else None
            if isinstance(v, (int, float)) and v is not None:
                vals.append(v)
        return round(float(np.median(vals)), 3) if vals else default

    def mode_color(path):
        vals = []
        for r in results:
            v = r
            for k in path.split("."):
                v = v.get(k) if isinstance(v, dict) else None
            if v:
                vals.append(v)
        return vals[0] if vals else None

    return {
        "videos": [r["id"] for r in results],
        "shot_median": med("cuts.shot_median"), "shot_p90": med("cuts.shot_p90"), "cuts_per_min": med("cuts.per_min"),
        "jumpcut_zoom_ratio": med("zoom.jumpcut_zoom_ratio"),
        "jump_scale": med("zoom.jump_scales_median"),
        "sub_size": med("subtitle.size_px_1080"), "sub_y": med("subtitle.center_y"),
        "sub_fill": mode_color("subtitle.fill"), "sub_outline": mode_color("subtitle.outline_or_box"),
        "sub_accent": mode_color("subtitle.accent"),
        "emph_size": med("emphasis.size_px_1080"), "emph_y": med("emphasis.center_y"), "emph_per_min": med("emphasis.per_min"),
        "emph_with_sfx": med("emphasis.with_sfx_ratio"), "emph_color": mode_color("emphasis.colors"),
        "bgm_present_ratio": round(float(np.mean([r["audio"]["bgm_present"] for r in results])), 2) if results else None,
        "bgm_relative_db": med("audio.bgm_relative_db"), "sfx_per_min": med("audio.sfx_per_min"),
        "sfx_on_cut_ratio": med("audio.sfx_on_cut_ratio"),
        "broll_share": med("broll.share"), "broll_len": med("broll.avg_len"),
        "first_speech": med("intro.first_speech_sec"), "outro_tail": med("outro.tail_after_last_speech_sec"),
        "pause_median": med("audio.pauses_median"),
    }


def apply_to_rules(s: dict) -> list[str]:
    """측정값을 규칙집(rules.json)에 반영. 바뀐 항목 목록을 돌려준다."""
    r = load_rules()
    ch = []

    def setv(sec, key, val, note):
        if val is None:
            return
        if r[sec].get(key) != val:
            r[sec][key] = val
            ch.append(f"{sec}.{key} = {val} ({note})")

    if s["shot_p90"]:
        setv("zoom", "max_static_sec", round(max(2.0, min(12.0, s["shot_p90"])), 1), "한 화면이 이보다 길면 줌 변화로 리듬 주기")
    if s["jumpcut_zoom_ratio"] is not None:
        setv("zoom", "jumpcut_alternate", s["jumpcut_zoom_ratio"] >= 0.15, f"레퍼런스 컷 중 {int(s['jumpcut_zoom_ratio'] * 100)}%가 확대 변화")
    if s["jump_scale"]:
        setv("zoom", "jumpcut_scales", [1.0, round(min(1.4, s["jump_scale"]), 2)], "점프컷 확대 배율")
    if s["sub_size"]:
        setv("subtitle", "size_px", int(max(40, min(110, s["sub_size"]))), "자막 글자 높이 측정")
    if s["sub_y"]:
        setv("subtitle", "margin_bottom_px", int(max(30, (1 - s["sub_y"]) * 1080 - (s["sub_size"] or 68) / 2)), "자막 세로 위치")
    if s["sub_fill"]:
        setv("subtitle", "color", s["sub_fill"], "자막 글자색")
    if s["sub_outline"]:
        setv("subtitle", "outline_color", s["sub_outline"], "자막 테두리/배경색")
    if s["sub_accent"] and s["sub_accent"] != s["sub_fill"]:
        setv("subtitle", "highlight_color", s["sub_accent"], "자막 강조 단어색")
    if s["emph_size"]:
        setv("emphasis", "size_px", int(max(70, min(180, s["emph_size"]))), "강조 자막 크기")
    if s["emph_y"]:
        setv("emphasis", "position_y", round(max(0.08, min(0.6, s["emph_y"] - 0.05)), 2), "강조 자막 위치")
    if s["emph_per_min"]:
        setv("emphasis", "max_per_minute", int(max(1, round(s["emph_per_min"]))), "분당 강조 자막 수")
        setv("emphasis", "min_gap_sec", round(max(1.5, 60 / max(1, s["emph_per_min"]) * 0.5), 1), "강조 자막 최소 간격")
    if s["emph_color"]:
        r["emphasis"]["colors"] = [s["emph_color"][0]] + [c for c in r["emphasis"]["colors"] if c != s["emph_color"][0]][:2]
        ch.append(f"emphasis.colors 첫 색 = {s['emph_color'][0]}")
    if s["emph_with_sfx"] is not None:
        setv("sfx", "on_emphasis", "pop" if s["emph_with_sfx"] >= 0.3 else None, f"강조 자막의 {int(s['emph_with_sfx'] * 100)}%에 효과음")
    if s["sfx_on_cut_ratio"] is not None:
        setv("sfx", "on_jumpcut", "click" if s["sfx_on_cut_ratio"] >= 0.35 else None, f"컷의 {int(s['sfx_on_cut_ratio'] * 100)}%에 효과음")
    if s["bgm_present_ratio"] is not None:
        setv("bgm", "enabled", s["bgm_present_ratio"] >= 0.5, "배경음 사용 여부")
        if s["bgm_relative_db"] is not None:
            setv("bgm", "volume_db", round(max(-34, min(-14, s["bgm_relative_db"] - 18)), 1), "말소리 대비 배경음 크기")
    if s["broll_len"]:
        setv("broll", "duration_sec", round(max(1.5, min(8, s["broll_len"])), 1), "자료화면 평균 길이")
    if s["first_speech"] is not None:
        setv("intro", "hook_from_script", s["first_speech"] < 1.5, "첫 말이 바로 시작하면 훅 사용")
    if s["outro_tail"] is not None:
        setv("outro", "card_sec", round(max(0.0, min(20.0, s["outro_tail"])), 1), "마지막 말 이후 길이")
        setv("outro", "enabled", s["outro_tail"] >= 1.5, "엔딩 화면 사용")
    if s["pause_median"] is not None:
        setv("script_cleanup", "keep_pause_sec", round(max(0.05, min(0.4, s["pause_median"] * 0.5)), 2), "말 사이 간격")
    r["source"] = f"레퍼런스 {len(s['videos'])}개 분석: {', '.join(s['videos'])}"
    save_rules(r)
    return ch


def update_rulebook_md(s: dict, results: list[dict], changes: list[str]) -> None:
    md = ROOT / "docs" / "편집규칙집.md"
    text = md.read_text(encoding="utf-8")
    L = ["<!-- MEASURED:START -->", "", f"분석한 영상: {', '.join(s['videos'])}", "",
         "| 항목 | 측정값 |", "|---|---|"]
    rows = [
        ("컷 간격(중앙값 / 90%)", f"{s['shot_median']}초 / {s['shot_p90']}초"), ("분당 컷 수", s["cuts_per_min"]),
        ("점프컷 확대 비율", s["jumpcut_zoom_ratio"]), ("자막 크기(1080p)", s["sub_size"]), ("자막 세로 위치(0=위,1=아래)", s["sub_y"]),
        ("자막 글자색 / 테두리색 / 강조색", f"{s['sub_fill']} / {s['sub_outline']} / {s['sub_accent']}"),
        ("강조 자막 크기 / 위치 / 분당 횟수", f"{s['emph_size']} / {s['emph_y']} / {s['emph_per_min']}"),
        ("강조 자막에 효과음 비율", s["emph_with_sfx"]), ("분당 효과음", s["sfx_per_min"]), ("컷에 효과음 비율", s["sfx_on_cut_ratio"]),
        ("배경음 사용 비율 / 말소리 대비 크기", f"{s['bgm_present_ratio']} / {s['bgm_relative_db']}dB"),
        ("자료화면 비중 / 평균 길이", f"{s['broll_share']} / {s['broll_len']}초"),
        ("첫 말 시작", f"{s['first_speech']}초"), ("마지막 말 이후(아웃트로)", f"{s['outro_tail']}초"),
    ]
    L += [f"| {a} | {b} |" for a, b in rows]
    L += ["", "규칙에 반영된 변경:"] + [f"- {c}" for c in changes] + ["", "장면 썸네일: " +
          ", ".join(f"`{p}`" for r in results for p in r.get("contact_sheets", [])[:2]), "", "<!-- MEASURED:END -->"]
    text = re.sub(r"<!-- MEASURED:START -->.*<!-- MEASURED:END -->", "\n".join(L), text, flags=re.S)
    md.write_text(text, encoding="utf-8")


def analyze(urls: list[str]) -> dict:
    results = []
    for u in urls:
        mp4 = Path(u) if Path(u).exists() else download(u)
        results.append(analyze_video(mp4))
    s = summarize(results)
    write_json(REFS / "summary.json", {"summary": s, "videos": results})
    changes = apply_to_rules(s)
    update_rulebook_md(s, results, changes)
    log("규칙집 갱신 완료:\n  " + "\n  ".join(changes or ["(변경 없음)"]))
    return s

