"""④⑤⑥ 자막·강조자막·효과음·배경음·자료화면·인트로/아웃트로를 합쳐 mp4 로 완성."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import broll as broll_mod
from .common import ROOT, asset, ffmpeg, log, read_json, write_json
from .graphics import inset_mask, outro_card, title_card
from .sounds import SR, mix
from .subtitles import build_ass, make_lines


def pick_hook(script: dict, timeline: dict, rules: dict) -> tuple[float, float] | None:
    c = rules["intro"]
    if not c.get("enabled", True) or not c.get("hook_from_script", True):
        return None
    words = timeline["words"]
    by_id = {w["id"]: w for w in words}
    h = script.get("hook")
    if h and h.get("from_word") in by_id and h.get("to_word") in by_id:
        return by_id[h["from_word"]]["out_start"] - 0.05, by_id[h["to_word"]]["out_end"] + 0.2
    if h == "off":
        return None
    # 자동: 처음 나오는 강조 문장(인상적인 한 마디)을 영상 맨 앞에 미리 보여준다
    lines = make_lines(words, rules)
    for i, ln in enumerate(lines):
        if any(w.get("emph") for w in ln["words"]):
            # 강조가 든 문장을 문장 끝까지(최대 hook_max_sec) — 문장 중간에서 끊기지 않게
            a, b = ln["start"], ln["end"]
            j = i
            while not lines[j]["words"][-1]["text"].endswith((".", "?", "!")) and j + 1 < len(lines) \
                    and lines[j + 1]["end"] - a <= c["hook_max_sec"]:
                j += 1
                b = lines[j]["end"]
            if a < 3.0:  # 이미 맨 앞이면 굳이 반복하지 않는다
                return None
            return max(0.0, a - 0.05), min(b + 0.1, a + c["hook_max_sec"])
    return None


def render(work: Path, rules: dict, out_path: Path) -> Path:
    script = read_json(work / "script.json")
    tl = read_json(work / "timeline.json")
    o = rules["output"]
    W, H, fps = o["width"], o["height"], o["fps"]
    D = tl["duration"]
    cut_mp4 = work / "cut.mp4"

    hook = pick_hook(script, tl, rules)
    T = rules["intro"]["title_card_sec"] if rules["intro"].get("enabled", True) else 0.0
    oc = rules["outro"]
    # 아웃트로: card = 구독 카드를 붙임, fade = 카드 없이 마지막 장면이 어두워지며 끝남(레퍼런스 방식)
    fade_out = oc.get("enabled", True) and oc.get("style", "card") == "fade"
    O = oc["card_sec"] if oc.get("enabled", True) and not fade_out else 0.0
    hook_len = (hook[1] - hook[0]) if hook else 0.0
    main_off = hook_len + T
    total = main_off + D + O

    mapping = []
    if hook:
        mapping.append((hook[0], hook[1], 0.0))
    mapping.append((0.0, D, main_off))

    # 자막
    ass = work / "final.ass"
    emph = build_ass(tl["words"], mapping, rules, ass)

    # 자료화면 (본편 위치로 옮김)
    brolls = broll_mod.prepare(script, tl, work, rules)
    for b in brolls:
        b["final_start"] = main_off + b["start"]

    # 소리
    import soundfile as sf

    voice, _ = sf.read(str(work / "cut_voice.wav"), dtype="float32", always_2d=True)
    parts = []
    if hook:
        hv = voice[int(hook[0] * SR):int(hook[1] * SR)].copy()
        f = int(0.03 * SR)
        hv[:f] *= np.linspace(0, 1, f)[:, None]
        hv[-f:] *= np.linspace(1, 0, f)[:, None]
        parts.append(hv)
        # 훅 길이를 영상 프레임 단위와 정확히 맞춘다
        need = int(round(hook_len * SR))
        parts[-1] = np.vstack([hv, np.zeros((max(0, need - len(hv)), 2), np.float32)])[:need]
    parts.append(np.zeros((int(round(T * SR)), 2), np.float32))
    parts.append(voice[: int(round(D * SR))])
    parts.append(np.zeros((int(round(O * SR)), 2), np.float32))
    vfinal = np.vstack(parts)
    s = rules["sfx"]
    events = [(e["t"], s["on_emphasis"]) for e in emph if s.get("on_emphasis")]
    if T and s.get("on_intro_title"):
        events.append((hook_len, s["on_intro_title"]))
    events += [(b["final_start"], s["on_broll_in"]) for b in brolls if s.get("on_broll_in")]
    if O and s.get("on_outro"):
        events.append((main_off + D, s["on_outro"]))
    mixed = mix(vfinal, events, rules, work)
    final_wav = work / "final_mix.wav"
    sf.write(str(final_wav), mixed, SR, subtype="PCM_16")

    # 영상 합치기
    title = script.get("title") or out_path.stem.replace("_편집본", "")
    tpng = title_card(title, rules, work / "title.png")
    opng = outro_card(rules, work / "outro.png")
    inputs = ["-i", cut_mp4,
              "-loop", "1", "-framerate", str(fps), "-t", f"{max(T, 0.1):.3f}", "-i", tpng,
              "-loop", "1", "-framerate", str(fps), "-t", f"{max(O, 0.1):.3f}", "-i", opng]
    for b in brolls:
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{b['dur']:.3f}", "-i", b["png"]]
    inputs += ["-i", final_wav]
    a_idx = 3 + len(brolls)
    n_in = a_idx + 1
    b_cfg = rules["broll"]
    # 전체 화면 자료화면 위에 말하는 사람을 작은 창으로 계속 보여준다
    inset = b_cfg["mode"] == "full" and b_cfg.get("face_inset", False) and brolls
    if inset:
        iw = int(W * b_cfg.get("face_inset_width_ratio", 0.22)) // 2 * 2
        ih = int(iw * H / W) // 2 * 2
        mask = inset_mask(iw, ih, b_cfg.get("face_inset_radius_px", 28), work / "inset_mask.png")
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{total:.3f}", "-i", mask]
        mask_idx, n_in = n_in, n_in + 1
    wm = rules.get("watermark", {})
    wm_file = asset(wm["file"]) if wm.get("enabled") and wm.get("file") else None
    if wm_file and wm_file.exists():
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{total:.3f}", "-i", wm_file]
        wm_idx, n_in = n_in, n_in + 1
    else:
        wm_file = None

    fg = []
    v = "[0:v]"
    segs = []
    if hook:
        fg.append(f"{v}split[mainsrc][hooksrc];[hooksrc]trim={hook[0]:.4f}:{hook[0] + hook_len:.4f},setpts=PTS-STARTPTS[hk]")
        v = "[mainsrc]"
        segs.append("[hk]")
    if T:
        fg.append(f"[1:v]scale={W}:{H},fps={fps},format=yuv420p,setsar=1,"
                  f"zoompan=z='1+0.0008*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps}[tc]")
        segs.append("[tc]")
    fg.append(f"{v}trim=0:{D:.4f},setpts=PTS-STARTPTS[mn]")
    segs.append("[mn]")
    if O:
        fg.append(f"[2:v]scale={W}:{H},fps={fps},format=yuv420p,setsar=1[oc]")
        segs.append("[oc]")
    fg.append(f"{''.join(segs)}concat=n={len(segs)}:v=1:a=0,format=yuv420p[base]")
    v = "[base]"
    if inset:
        fg.append(f"[base]split[base0][facesrc];[facesrc]scale={iw}:{ih},format=rgba[fs];"
                  f"[{mask_idx}:v]scale={iw}:{ih},format=gray[fm];[fs][fm]alphamerge[face]")
        v = "[base0]"
    for k, b in enumerate(brolls):
        st, du = b["final_start"], b["dur"]
        if b_cfg["mode"] == "full":
            pos = "0:0"
        else:
            pos = f"W-w-{b_cfg['pip_margin_px'] - 24}:{b_cfg['pip_margin_px'] - 24}"
        fg.append(f"[{3 + k}:v]format=rgba,fade=in:st=0:d=0.2:alpha=1,fade=out:st={max(0, du - 0.25):.3f}:d=0.25:alpha=1,"
                  f"setpts=PTS-STARTPTS+{st:.3f}/TB[b{k}];{v}[b{k}]overlay={pos}:eof_action=pass:"
                  f"enable='between(t,{st:.3f},{st + du:.3f})'[ov{k}]")
        v = f"[ov{k}]"
    if inset:
        m = b_cfg.get("face_inset_margin_px", 32)
        x = f"{m}" if b_cfg.get("face_inset_corner", "bottom-right") == "bottom-left" else f"W-w-{m}"
        on = "+".join(f"between(t,{b['final_start'] + 0.15:.3f},{b['final_start'] + b['dur'] - 0.15:.3f})" for b in brolls)
        fg.append(f"{v}[face]overlay={x}:H-h-{m}:enable='{on}'[vin]")
        v = "[vin]"
    if wm_file:
        m = wm.get("margin_px", 32)
        fg.append(f"[{wm_idx}:v]scale={wm.get('width_px', 220)}:-2,format=rgba,"
                  f"colorchannelmixer=aa={wm.get('opacity', 0.9)}[wm];{v}[wm]overlay={m}:{m}:shortest=1[vwm]")
        v = "[vwm]"
    if fade_out:
        fd = min(oc.get("fade_sec", 1.5), D)
        fg.append(f"{v}fade=t=out:st={main_off + D - fd:.3f}:d={fd:.3f}[vfd]")
        v = "[vfd]"
    fontsdir = str(ROOT / "assets" / "fonts")
    fg.append(f"{v}subtitles='{ass}':fontsdir='{fontsdir}'[vout]")
    lufs = o["loudness_lufs"]
    fg.append(f"[{a_idx}:a]loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample={SR}[aout]")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"최종 렌더링 중… (길이 {total:.1f}초)")
    ffmpeg(*inputs, "-filter_complex", ";".join(fg), "-map", "[vout]", "-map", "[aout]",
           "-c:v", "libx264", "-preset", o["video_preset"], "-crf", str(o["video_crf"]), "-r", str(fps),
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", o["audio_bitrate"], "-movflags", "+faststart",
           "-t", f"{total:.3f}", out_path)

    static = []
    if T:
        static.append([hook_len, hook_len + T])
    if O:
        static.append([main_off + D, total])
    static += [[b["final_start"], b["final_start"] + b["dur"]] for b in brolls if b_cfg["mode"] == "full"]
    write_json(work / "render_plan.json", {
        "output": str(out_path), "total": total, "hook": hook, "hook_len": hook_len, "title_sec": T,
        "main_offset": main_off, "main_duration": D, "outro_sec": O, "mapping": mapping,
        "static_ranges": static, "face_inset": bool(inset), "outro_fade": fade_out, "brolls": brolls, "emphasis": emph, "blur_regions": script.get("blur", []),
    })
    log(f"완성: {out_path}")
    return out_path
