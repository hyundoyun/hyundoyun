"""④⑤⑥ 자막·강조자막·효과음·배경음·자료화면·인트로/아웃트로를 합쳐 mp4 로 완성."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import broll as broll_mod
from .common import ROOT, ffmpeg, log, read_json, write_json
from .graphics import outro_card, title_card
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
    O = rules["outro"]["card_sec"] if rules["outro"].get("enabled", True) else 0.0
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
    b_cfg = rules["broll"]
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
        "static_ranges": static, "brolls": brolls, "emphasis": emph, "blur_regions": script.get("blur", []),
    })
    log(f"완성: {out_path}")
    return out_path
