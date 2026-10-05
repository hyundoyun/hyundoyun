"""③ 컷 편집: 교정 대본에 남은 말만 이어 붙인다.

- 영상은 구간마다 정확한 프레임 수로 잘라 붙이고, 소리는 같은 길이만큼 샘플 단위로 잘라 붙여
  소리와 입모양이 절대 어긋나지 않게 한다.
- 이음새마다 아주 짧은 페이드(12ms)를 넣어 '틱' 소리가 나지 않게 한다.
- 점프컷 줌: 구간이 바뀔 때마다 화면 크기를 1.0 ↔ 1.12 로 번갈아 바꿔 튀는 느낌을 없앤다.
- 강조 순간에는 화면을 확 당긴다(펀치 줌).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .common import ffmpeg, log, read_json, video_info, write_json

AUDIO_SR = 48000


def plan_segments(words: list[dict], rules: dict, total: float) -> list[dict]:
    c, sc = rules["cut"], rules["script_cleanup"]
    segs: list[dict] = []
    cur = None
    prev = None
    for w in words:
        if not w["keep"]:
            if cur:
                segs.append(cur)
                cur = None
            prev = w
            continue
        if cur and prev is not None and prev["keep"] and w["start"] - prev["end"] <= sc["long_pause_sec"]:
            cur["end_word"] = w
            cur["ids"].append(w["id"])
        else:
            if cur:
                segs.append(cur)
            cur = {"start_word": w, "end_word": w, "ids": [w["id"]], "prev_drop_end": prev["end"] if prev is not None and not prev["keep"] else None}
        prev = w
    if cur:
        segs.append(cur)

    # 다음 구간의 시작 전에 끝나는 '버린 단어'를 피해서 앞뒤 여유를 준다
    out = []
    by_id = {w["id"]: w for w in words}
    for s in segs:
        a = s["start_word"]["start"] - c["pad_before_sec"]
        b = s["end_word"]["end"] + c["pad_after_sec"]
        prev_w = by_id.get(s["start_word"]["id"] - 1)
        next_w = by_id.get(s["end_word"]["id"] + 1)
        if prev_w is not None and not prev_w["keep"]:
            a = max(a, prev_w["end"] + 0.01)
        if next_w is not None and not next_w["keep"]:
            b = min(b, next_w["start"] - 0.01)
        a, b = max(0.0, a), min(total, b)
        out.append({"src_start": a, "src_end": b, "ids": s["ids"]})

    # 겹치거나 아주 가까운 구간 합치기
    merged: list[dict] = []
    for s in out:
        if merged and s["src_start"] - merged[-1]["src_end"] <= c["merge_gap_sec"] and \
                all(by_id[i]["keep"] for i in range(merged[-1]["ids"][-1], s["ids"][0] + 1)):
            merged[-1]["src_end"] = max(merged[-1]["src_end"], s["src_end"])
            merged[-1]["ids"] += s["ids"]
        else:
            if merged and s["src_start"] < merged[-1]["src_end"]:
                s["src_start"] = merged[-1]["src_end"]
            merged.append(s)
    return [s for s in merged if s["src_end"] - s["src_start"] >= c["min_segment_sec"]]


def split_for_zoom(segs: list[dict], words_by_id: dict, rules: dict) -> list[dict]:
    """점프컷 줌 배율을 정하고, 강조 단어가 있으면 그 순간부터 펀치 줌 조각으로 나눈다."""
    z = rules["zoom"]
    scales = z["jumpcut_scales"] if z.get("jumpcut_alternate", True) else [1.0]
    # 한 화면이 너무 오래 이어지면(레퍼런스의 컷 간격 기준) 단어 사이에서 줌을 바꿔 리듬을 준다
    segs = [{**s, "aseg": k} for k, s in enumerate(segs)]  # aseg: 소리가 끊김 없이 이어지는 묶음 번호
    max_static = z.get("max_static_sec")
    if max_static and len(scales) > 1:
        split = []
        for s in segs:
            parts, cur_start, cur_ids = [], s["src_start"], []
            for wid in s["ids"]:
                w = words_by_id[wid]
                if cur_ids and w["start"] - cur_start >= max_static and s["src_end"] - w["start"] > 1.0:
                    cut_t = (words_by_id[cur_ids[-1]]["end"] + w["start"]) / 2
                    parts.append({**s, "src_start": cur_start, "src_end": cut_t, "ids": cur_ids, "rhythm": True})
                    cur_start, cur_ids = cut_t, []
                cur_ids.append(wid)
            parts.append({**s, "src_start": cur_start, "ids": cur_ids})
            split += parts
        segs = split
    pieces = []
    for i, s in enumerate(segs):
        base = scales[i % len(scales)]
        cuts = []
        if z.get("punch_in_on_emphasis", True):
            for wid in s["ids"]:
                w = words_by_id[wid]
                if w.get("emph") and s["src_start"] < w["start"] < s["src_end"] - 0.2:
                    cuts.append((w["start"], min(w["start"] + z["punch_hold_sec"], s["src_end"])))
        t = s["src_start"]
        for a, b in cuts:
            if a - t < 0.25:  # 앞에 남는 조각이 너무 짧으면 줌을 조금 일찍 시작
                a = t
            if s["src_end"] - b < 0.3:  # 뒤에 남는 조각이 너무 짧으면 줌을 끝까지 유지
                b = s["src_end"]
            if a - t > 0.05:
                pieces.append({**s, "src_start": t, "src_end": a, "scale": base, "seg": s["aseg"]})
            pieces.append({**s, "src_start": a, "src_end": b, "scale": max(z["punch_scale"], base + 0.08), "seg": s["aseg"], "punch": True})
            t = b
        if s["src_end"] - t > 0.05:
            pieces.append({**s, "src_start": t, "src_end": s["src_end"], "scale": base, "seg": s["aseg"]})
    return pieces


def _render_piece(src: Path, p: dict, out: Path, rules: dict, fps: float, blur: list[dict] | None = None) -> None:
    o = rules["output"]
    W, H = o["width"], o["height"]
    z = p["scale"]
    fy = rules["zoom"].get("focus_y", 0.42)
    if p.get("pan"):  # 화면이 멈춘 구간: 살짝 당긴 채 천천히 옆으로 움직여 멈춘 느낌을 없앤다
        z = max(z, 1.08)
    cw, ch = int(W / z) // 2 * 2, int(H / z) // 2 * 2
    x_expr = f"(iw-{cw})*t/{max(p['dur'], 0.1):.3f}" if p.get("pan") else f"(iw-{cw})/2"
    fg = [f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[n0]"]
    v = "[n0]"
    # 개인정보 모자이크: 줌을 하기 전(원본 좌표)에 덮어서, 줌이 바뀌어도 정확히 따라가게 한다
    blk = max(4, int(rules["qc"].get("blur_strength", 24)))
    for k, r in enumerate(blur or []):
        a, b = r["t0"] - p["src_start"], r["t1"] - p["src_start"]
        if b <= 0 or a >= p["dur"]:
            continue
        w, h = max(2, int(r["w"]) // 2 * 2), max(2, int(r["h"]) // 2 * 2)
        x, y = max(0, min(int(r["x"]), W - w)), max(0, min(int(r["y"]), H - h))
        fg.append(f"{v}split[a{k}][b{k}];[b{k}]crop={w}:{h}:{x}:{y},scale={max(1, w // blk)}:{max(1, h // blk)}:flags=area,"
                  f"scale={w}:{h}:flags=neighbor[m{k}];[a{k}][m{k}]overlay={x}:{y}:enable='between(t,{a:.3f},{b:.3f})'[n{k + 1}]")
        v = f"[n{k + 1}]"
    fg.append(f"{v}crop={cw}:{ch}:{x_expr}:(ih-{ch})*{fy},scale={W}:{H}:flags=lanczos,fps={fps},format=yuv420p[out]")
    ffmpeg("-ss", f"{p['src_start']:.4f}", "-i", src, "-frames:v", str(p["frames"]), "-an",
           "-filter_complex", ";".join(fg), "-map", "[out]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-g", str(int(fps * 2)), out)


def cut(video: Path, script_path: Path, work: Path, rules: dict) -> tuple[Path, Path]:
    script = read_json(script_path)
    words = script["words"]
    by_id = {w["id"]: w for w in words}
    info = video_info(video)
    fps = rules["output"]["fps"]
    segs = plan_segments(words, rules, info["duration"])
    pieces = split_for_zoom(segs, by_id, rules)

    for p in pieces:
        p["pan"] = any(a < p["src_end"] and b > p["src_start"] for a, b in script.get("fix_freeze", []))

    # 프레임 단위로 길이를 확정 → 소리도 똑같은 길이로 자른다
    t_out = 0.0
    for i, p in enumerate(pieces):
        if i > 0 and pieces[i - 1]["seg"] == p["seg"]:  # 이어지는 조각은 프레임 반올림 오차 없이 딱 붙인다
            p["src_start"] = pieces[i - 1]["src_start"] + pieces[i - 1]["dur"]
        p["frames"] = max(1, round((p["src_end"] - p["src_start"]) * fps))
        p["dur"] = p["frames"] / fps
        p["out_start"] = t_out
        t_out += p["dur"]

    # 소리: 원본 48kHz 스테레오에서 샘플 단위로 잘라 이음새에 짧은 페이드
    full_wav = work / "audio48k.wav"
    ffmpeg("-i", video, "-vn", "-ac", "2", "-ar", str(AUDIO_SR), "-c:a", "pcm_s16le", full_wav)
    import soundfile as sf

    audio, _ = sf.read(str(full_wav), dtype="float32", always_2d=True)
    fade = int(AUDIO_SR * rules["cut"]["audio_fade_ms"] / 1000)
    chunks = []
    for i, p in enumerate(pieces):
        n = round(p["dur"] * AUDIO_SR)
        a0 = int(round(p["src_start"] * AUDIO_SR))
        ch = audio[a0:a0 + n]
        if len(ch) < n:
            ch = np.vstack([ch, np.zeros((n - len(ch), audio.shape[1]), dtype=np.float32)])
        ch = ch.copy()
        # 같은 구간 안의 줌 조각끼리는 소리가 이어지므로 페이드하지 않는다
        joined_prev = i > 0 and pieces[i - 1]["seg"] == p["seg"]
        joined_next = i + 1 < len(pieces) and pieces[i + 1]["seg"] == p["seg"]
        if not joined_prev and fade:
            ch[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)[:, None]
        if not joined_next and fade:
            ch[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
        chunks.append(ch)
    voice = np.vstack(chunks) if chunks else np.zeros((1, 2), dtype=np.float32)
    voice_wav = work / "cut_voice.wav"
    sf.write(str(voice_wav), voice, AUDIO_SR, subtype="PCM_16")

    # 영상: 조각별로 병렬 렌더 → 이어 붙이기
    pdir = work / "pieces"
    pdir.mkdir(exist_ok=True)
    for f in pdir.glob("*.mp4"):
        f.unlink()
    log(f"컷 편집: {len(segs)}개 구간 / {len(pieces)}개 조각 (원본 {info['duration']:.1f}초 → {t_out:.1f}초)")
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(_render_piece, video, p, pdir / f"{i:05d}.mp4", rules, fps, script.get("blur")) for i, p in enumerate(pieces)]
        for f in futs:
            f.result()
    lst = pdir / "list.txt"
    lst.write_text("".join(f"file '{pdir / f'{i:05d}.mp4'}'\n" for i in range(len(pieces))), encoding="utf-8")
    cut_mp4 = work / "cut.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", lst, "-i", voice_wav, "-map", "0:v", "-map", "1:a",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", cut_mp4)

    # 단어별 새 시간(컷 편집 후 타임라인): 단어 시작이 들어 있는 조각(없으면 가장 가까운 조각)에 배정
    tl_words = []
    owner: dict[int, dict] = {}
    for p in pieces:
        for wid in p["ids"]:
            w = by_id[wid]
            a, b = p["src_start"], p["src_start"] + p["dur"]
            dist = 0.0 if a - 0.001 <= w["start"] < b else min(abs(w["start"] - a), abs(w["start"] - b))
            if wid not in owner or dist < owner[wid][0]:
                owner[wid] = (dist, p)
    for wid, (_, p) in owner.items():
        w = by_id[wid]
        off = p["out_start"] - p["src_start"]
        st = min(max(w["start"], p["src_start"]), p["src_start"] + p["dur"] - 0.01)
        tl_words.append({**w, "out_start": round(st + off, 3),
                         "out_end": round(max(st + 0.05, min(w["end"], p["src_start"] + p["dur"])) + off, 3),
                         "piece_seg": p["seg"]})
    tl_words.sort(key=lambda w: w["out_start"])
    timeline = work / "timeline.json"
    write_json(timeline, {"duration": round(t_out, 3), "fps": fps,
                          "pieces": [{k: p[k] for k in ("src_start", "src_end", "out_start", "dur", "scale", "seg")} for p in pieces],
                          "words": tl_words})
    return cut_mp4, timeline
