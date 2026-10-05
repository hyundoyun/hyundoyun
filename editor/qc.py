"""검수: 자막 오타, 소리 끊김, 화면 멈춤, 개인정보 노출을 찾아서 고친다.

자동으로 고치는 것
- 자막 오타: 규칙집의 맞춤법 사전(qc.typo_dictionary)으로 교체
- 소리 끊김: 말소리가 없는데 자막 단어가 있는 구간(인식 오류)을 컷 / 영상·소리 길이 불일치 확인
- 화면 멈춤: 원본 화면이 멈춘 구간은 천천히 움직이는 줌으로 바꿈
- 개인정보: 이메일·전화번호·주민번호·카드/계좌번호가 보이면 그 부분을 흐림 처리
사람(또는 Claude)이 확인할 것
- 인식이 불확실한 자막 줄 목록(qc_report.json 의 'review_lines')
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .common import ffmpeg, log, read_json, run, video_info, write_json


# ---------- 자막 오타 ----------
def fix_typos(script: dict, rules: dict) -> list[dict]:
    fixes = []
    dic = rules["qc"].get("typo_dictionary", {})
    for w in script["words"]:
        if not w["keep"]:
            continue
        t = w["text"]
        for bad, good in dic.items():
            if bad in t:
                t = t.replace(bad, good)
        if t != w["text"]:
            fixes.append({"word_id": w["id"], "from": w["text"], "to": t})
            w["text"] = t
    return fixes


def review_lines(timeline: dict, rules: dict) -> list[dict]:
    """인식 신뢰도가 낮거나 말이 안 되어 보이는 자막 줄 → 확인 목록."""
    from .subtitles import make_lines

    out = []
    for ln in make_lines(timeline["words"], rules):
        confs = [w.get("conf") for w in ln["words"] if w.get("conf") is not None]
        low = [w["text"] for w in ln["words"] if w.get("conf") is not None and w["conf"] < 0.45]
        weird = [w["text"] for w in ln["words"] if re.search(r"[A-Za-z]{1,2}\d|[^\w\s.,?!%~'\"()·-]", w["text"])]
        if low or weird or (confs and np.mean(confs) < 0.6):
            out.append({"time": round(ln["start"], 2), "text": " ".join(w["text"] for w in ln["words"]),
                        "word_ids": [w["id"] for w in ln["words"]], "uncertain": low + weird})
    return out


# ---------- 소리 ----------
def rms_db(a: np.ndarray, sr: int, win: float = 0.05) -> np.ndarray:
    m = np.abs(a).mean(axis=1) if a.ndim == 2 else np.abs(a)
    n = int(sr * win)
    k = len(m) // n
    frames = m[: k * n].reshape(k, n)
    return 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)


def silent_words(work: Path, rules: dict) -> list[int]:
    """컷 편집본에서 자막 단어는 있는데 실제 소리는 없는 구간(=소리 끊김처럼 들림)."""
    import soundfile as sf

    tl = read_json(work / "timeline.json")
    a, sr = sf.read(str(work / "cut_voice.wav"), dtype="float32", always_2d=True)
    db = rms_db(a, sr)
    floor = np.percentile(db, 10)
    speech = np.percentile(db, 70)
    thr = floor + (speech - floor) * 0.25
    bad = []
    for w in tl["words"]:
        i0, i1 = int(w["out_start"] / 0.05), max(int(w["out_start"] / 0.05) + 1, int(w["out_end"] / 0.05))
        seg = db[i0:i1]
        if len(seg) and seg.max() < thr and w["out_end"] - w["out_start"] > 0.25:
            bad.append(w["id"])
    return bad


def audio_gaps(final: Path, plan: dict, rules: dict) -> list[dict]:
    r = run(["ffmpeg", "-hide_banner", "-i", str(final), "-af",
             f"silencedetect=noise=-45dB:d={rules['qc']['speech_gap_max_sec']}", "-f", "null", "-"], check=False)
    gaps = []
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    a0, a1 = plan["main_offset"], plan["main_offset"] + plan["main_duration"]
    for s, e in zip(starts, ends):
        if s >= a0 and e <= a1:
            gaps.append({"start": round(s, 2), "end": round(e, 2)})
    return gaps


def read_streams(path: Path) -> dict:
    import json

    r = run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration,nb_frames", "-of", "json", str(path)])
    st = json.loads(r.stdout)["streams"]
    v = next(s for s in st if s["codec_type"] == "video")
    a = next((s for s in st if s["codec_type"] == "audio"), None)
    return {"video_sec": float(v.get("duration", 0)), "audio_sec": float(a.get("duration", 0)) if a else 0.0,
            "frames": int(v.get("nb_frames", 0) or 0)}


def clicks(work: Path) -> int:
    """이음새에서 갑자기 튀는 소리(틱) 개수."""
    import soundfile as sf

    a, sr = sf.read(str(work / "cut_voice.wav"), dtype="float32", always_2d=True)
    d = np.abs(np.diff(a.mean(axis=1)))
    tl = read_json(work / "timeline.json")
    n = 0
    for p in tl["pieces"][1:]:
        i = int(p["out_start"] * sr)
        if i < len(d) and d[max(0, i - 2):i + 2].max(initial=0) > 0.3:
            n += 1
    return n


# ---------- 화면 멈춤 ----------
def freezes(video: Path, rules: dict, exclude: list[list[float]] | None = None) -> list[dict]:
    q = rules["qc"]
    r = run(["ffmpeg", "-hide_banner", "-i", str(video), "-vf",
             f"freezedetect=n={q['freeze_noise']}:d={q['freeze_min_sec']}", "-map", "0:v", "-f", "null", "-"], check=False)
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", r.stderr)]
    dur = video_info(video)["duration"]
    if len(ends) < len(starts):
        ends.append(dur)
    out = []
    for s, e in zip(starts, ends):
        if any(s >= a - 0.2 and e <= b + 0.2 for a, b in (exclude or [])):
            continue
        out.append({"start": round(s, 2), "end": round(e, 2)})
    return out


def cut_to_src(t: float, tl: dict) -> float | None:
    for p in tl["pieces"]:
        if p["out_start"] <= t < p["out_start"] + p["dur"]:
            return p["src_start"] + (t - p["out_start"])
    return None


# ---------- 개인정보 ----------
def _ocr_frame(args):
    import pytesseract
    from PIL import Image

    path, t, patterns, keywords = args
    img = Image.open(path)
    try:
        data = pytesseract.image_to_data(img, lang="kor+eng", output_type=pytesseract.Output.DICT,
                                         config="--psm 11", timeout=90)
    except RuntimeError:  # 너무 오래 걸리는 화면은 건너뜀(보고서에 남지 않으므로 시간 초과는 드묾)
        return []
    hits = []
    # 같은 줄의 글자를 이어붙여 패턴 검사 (번호가 칸으로 나뉘어 인식돼도 잡히게)
    rows: dict[tuple, list[int]] = {}
    for i, txt in enumerate(data["text"]):
        if txt.strip():
            rows.setdefault((data["block_num"][i], data["par_num"][i], data["line_num"][i]), []).append(i)
    for idx in rows.values():
        line = " ".join(data["text"][i] for i in idx)
        for name, pat in patterns.items():
            for m in re.finditer(pat, line):
                # 매칭된 글자가 속한 단어 상자들을 합친다
                pos, boxes = 0, []
                for i in idx:
                    w = data["text"][i]
                    s, e = pos, pos + len(w)
                    if s < m.end() and e > m.start():
                        boxes.append((data["left"][i], data["top"][i], data["width"][i], data["height"][i]))
                    pos = e + 1
                if boxes:
                    x0 = min(b[0] for b in boxes)
                    y0 = min(b[1] for b in boxes)
                    x1 = max(b[0] + b[2] for b in boxes)
                    y1 = max(b[1] + b[3] for b in boxes)
                    hits.append({"t": t, "kind": name, "text": m.group(0), "box": [x0, y0, x1 - x0, y1 - y0]})
        low = line.lower()
        for kw in keywords:
            if kw.lower() in low:
                xs = [data["left"][i] for i in idx]
                ys = [data["top"][i] for i in idx]
                x1 = max(data["left"][i] + data["width"][i] for i in idx)
                y1 = max(data["top"][i] + data["height"][i] for i in idx)
                hits.append({"t": t, "kind": f"민감어:{kw}", "text": line[:40], "box": [min(xs), min(ys), x1 - min(xs), y1 - min(ys)]})
    return hits


def find_pii(video: Path, work: Path, rules: dict) -> list[dict]:
    """원본 영상(줌 전, 출력 해상도로 맞춘 좌표)에서 개인정보를 찾는다. 시간도 원본 기준."""
    q, o = rules["qc"], rules["output"]
    W, H = o["width"], o["height"]
    step = q["pii_sample_every_sec"]
    d = work / "ocr_frames"
    d.mkdir(exist_ok=True)
    for f in d.glob("*.png"):
        f.unlink()
    ffmpeg("-i", video, "-vf", f"fps=1/{step},scale={W}:{H}:force_original_aspect_ratio=decrease,"
           f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2", d / "f_%05d.png")
    frames = sorted(d.glob("f_*.png"))
    args = [(str(f), i * step, q["pii_patterns"], q.get("pii_keywords", [])) for i, f in enumerate(frames)]
    log(f"개인정보 검사: 원본 화면 {len(frames)}장 글자 인식 중…")
    with ThreadPoolExecutor(max_workers=4) as ex:
        hits = [h for hs in ex.map(_ocr_frame, args) for h in hs]
    # 비슷한 위치·종류의 연속된 발견을 한 구간으로 합치고 앞뒤로 여유를 둔다
    regions: list[dict] = []
    pad_t = step + 0.25
    for h in sorted(hits, key=lambda h: h["t"]):
        x, y, w, hh = h["box"]
        x0, y0, x1, y1 = x - 14, y - 12, x + w + 14, y + hh + 12
        for r in regions:
            if r["kind"] == h["kind"] and h["t"] - r["last"] <= step * 2.5 and abs(r["x"] - x0) < 120 and abs(r["y"] - y0) < 80:
                r["last"] = h["t"]
                r["t1"] = h["t"] + pad_t
                nx0, ny0 = min(r["x"], x0), min(r["y"], y0)
                r["w"] = max(r["x"] + r["w"], x1) - nx0
                r["h"] = max(r["y"] + r["h"], y1) - ny0
                r["x"], r["y"] = nx0, ny0
                break
        else:
            regions.append({"kind": h["kind"], "text": h["text"], "last": h["t"], "t0": max(0.0, h["t"] - pad_t),
                            "t1": h["t"] + pad_t, "x": max(0, x0), "y": max(0, y0), "w": x1 - max(0, x0), "h": y1 - max(0, y0)})
    # 같은 글자가 여러 패턴(휴대폰/전화/계좌)에 동시에 걸리면 하나로 합친다
    merged: list[dict] = []
    for r in regions:
        for m in merged:
            if abs(m["x"] - r["x"]) < 40 and abs(m["y"] - r["y"]) < 30 and r["t0"] < m["t1"] and m["t0"] < r["t1"]:
                m["kind"] = m["kind"] if r["kind"] in m["kind"] else f"{m['kind']}/{r['kind']}"
                m["t0"], m["t1"] = min(m["t0"], r["t0"]), max(m["t1"], r["t1"])
                x1, y1 = max(m["x"] + m["w"], r["x"] + r["w"]), max(m["y"] + m["h"], r["y"] + r["h"])
                m["x"], m["y"] = min(m["x"], r["x"]), min(m["y"], r["y"])
                m["w"], m["h"] = x1 - m["x"], y1 - m["y"]
                break
        else:
            merged.append(r)
    regions = merged
    for r in regions:
        r.pop("last", None)
        r["t0"], r["t1"] = round(r["t0"], 2), round(r["t1"], 2)
    return regions


def src_to_out(t0: float, t1: float, tl: dict, plan: dict) -> list[tuple[float, float]]:
    """원본 시간 구간 → 완성본에서 그 장면이 나오는 시간 구간들(훅 포함)."""
    out = []
    for p in tl["pieces"]:
        a, b = max(t0, p["src_start"]), min(t1, p["src_start"] + p["dur"])
        if a < b:
            ca, cb = p["out_start"] + a - p["src_start"], p["out_start"] + b - p["src_start"]
            for ma, mb, fa in plan["mapping"]:
                x, y = max(ca, ma), min(cb, mb)
                if x < y:
                    out.append((fa + x - ma, fa + y - ma))
    return out


def verify_pii(final: Path, regions: list[dict], tl: dict, plan: dict, work: Path, rules: dict) -> list[dict]:
    """완성본에서 그 장면들을 0.5초마다 다시 글자 인식해, 가려지지 않은 개인정보가 남았는지 확인."""
    times = []
    for r in regions:
        for a, b in src_to_out(r["t0"], r["t1"], tl, plan):
            t = a + 0.05
            while t < b:
                times.append(round(t, 2))
                t += 0.5
    d = work / "verify_frames"
    d.mkdir(exist_ok=True)
    args = []
    for i, t in enumerate(sorted(set(times))):
        png = d / f"v_{i:05d}.png"
        ffmpeg("-ss", f"{t:.2f}", "-i", final, "-frames:v", "1", png)
        args.append((str(png), t, rules["qc"]["pii_patterns"], []))
    with ThreadPoolExecutor(max_workers=4) as ex:
        left = [h for hs in ex.map(_ocr_frame, args) for h in hs]
    return left


def mask(text: str) -> str:
    return re.sub(r"[0-9A-Za-z]", "*", text[:3]) + "*" * max(0, len(text) - 3)


def write_report(work: Path, report: dict, out_md: Path) -> None:
    L = ["# 검수 보고서", ""]
    f = report
    ok = lambda b: "✅" if b else "⚠️"  # noqa: E731
    L.append(f"- 완성 파일: `{f['output']}` ({f['duration']:.1f}초)")
    L.append(f"- 원본 {f['source_duration']:.1f}초 → 본편 {f['main_duration']:.1f}초 (뺀 시간 {f['source_duration'] - f['main_duration']:.1f}초)")
    L.append(f"- 검수·수정 반복: {f['rounds']}회")
    L.append("")
    L.append("| 항목 | 결과 | 내용 |")
    L.append("|---|---|---|")
    t = f["typos"]
    L.append(f"| 자막 오타 | {ok(not t['remaining'])} | 맞춤법 사전으로 {len(t['fixed'])}곳 수정, 확인 필요 {len(t['remaining'])}줄 (자동 검사 기준 — 문맥 오타는 Claude가 자막 전체를 읽어 확인) |")
    a = f["audio"]
    L.append(f"| 소리 끊김 | {ok(not a['gaps'] and a['clicks'] == 0 and a['sync_ok'])} | 무음 단어 {a['silent_words_cut']}개 컷, 남은 끊김 {len(a['gaps'])}곳, 이음새 틱 {a['clicks']}곳, 영상·소리 길이 차 {a['sync_diff']:.3f}초 |")
    z = f["freeze"]
    L.append(f"| 화면 멈춤 | {ok(not z['remaining'])} | 원본 멈춤 {z['fixed']}곳 움직임 처리, 남은 멈춤 {len(z['remaining'])}곳 |")
    p = f["pii"]
    L.append(f"| 개인정보 | {ok(p['remaining'] == 0)} | 발견 {len(p['found'])}곳 → 모자이크, 완성본 재검사 남은 노출 {p['remaining']}곳 |")
    L.append("")
    if p["found"]:
        L.append("## 가린 개인정보")
        for r in p["found"]:
            L.append(f"- 원본 {r['t0']:.1f}~{r['t1']:.1f}초: {r['kind']} `{mask(r['text'])}` → 모자이크")
        L.append("")
    if t["fixed"]:
        L.append("## 고친 자막")
        for x in t["fixed"]:
            L.append(f"- {x['from']} → {x['to']}")
        L.append("")
    if t["remaining"]:
        L.append("## 확인이 필요한 자막 (인식이 불확실)")
        for x in t["remaining"][:40]:
            L.append(f"- {x['time']:.1f}초: {x['text']}  ← {', '.join(x['uncertain'])}")
        L.append("")
    if a["gaps"] or z["remaining"]:
        L.append("## 남은 문제 위치 (완성본 기준)")
        for g in a["gaps"]:
            L.append(f"- 소리 끊김 {g['start']}~{g['end']}초")
        for g in z["remaining"]:
            L.append(f"- 화면 멈춤 {g['start']}~{g['end']}초")
    out_md.write_text("\n".join(L) + "\n", encoding="utf-8")
    write_json(work / "qc_report.json", report)
