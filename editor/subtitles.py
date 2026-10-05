"""④ 자막·강조 자막 (ASS 형식으로 만들어 영상에 입힌다)."""
from __future__ import annotations

import re
from pathlib import Path

from .common import hex_to_ass

BREAK_AFTER = ("요", "다", "죠", "고", "서", "는데", "니까", "지만", "면", "며", "게", "까")


def _t(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def make_lines(words: list[dict], rules: dict) -> list[dict]:
    """자막 한 줄 단위로 묶는다: 글자 수 제한, 문장 끝, 숨 쉬는 곳에서 끊는다."""
    sub = rules["subtitle"]
    maxc = sub["max_chars_per_line"] * sub.get("max_lines", 1)
    brk = rules["script_cleanup"]["sentence_break_pause_sec"]
    lines: list[dict] = []
    cur: list[dict] = []

    def flush():
        if cur:
            lines.append({"words": list(cur), "start": cur[0]["out_start"], "end": cur[-1]["out_end"]})
            cur.clear()

    for i, w in enumerate(words):
        if cur:
            text_len = len(" ".join(x["text"] for x in cur + [w]))
            orig_gap = w["start"] - cur[-1]["end"]
            prev = cur[-1]["text"]
            if (text_len > maxc or re.search(r"[.?!]$", prev) or orig_gap >= brk
                    or (len(" ".join(x["text"] for x in cur)) >= maxc * 0.55 and prev.rstrip(",").endswith(BREAK_AFTER))):
                flush()
        cur.append(w)
    flush()
    # 시간 다듬기: 다음 줄까지 이어서 보여주되(깜빡임 방지) 긴 공백이면 끊는다
    for i, ln in enumerate(lines):
        nxt = lines[i + 1]["start"] if i + 1 < len(lines) else None
        end = ln["end"] + 0.15
        if nxt is not None and nxt - ln["end"] < 0.6:
            end = nxt
        end = max(end, ln["start"] + sub["min_duration_sec"])
        if nxt is not None:
            end = min(end, nxt)
        ln["end"] = end
    return lines


def _clean(t: str) -> str:
    # 자막에는 마침표·쉼표를 찍지 않는다(물음표·느낌표는 유지)
    return re.sub(r"[.,]+$", "", t).replace("{", "(").replace("}", ")")


def build_ass(words: list[dict], mapping: list[tuple[float, float, float]], rules: dict, out: Path,
              extra_events: list[str] | None = None) -> list[dict]:
    """mapping: (컷영상 시작, 컷영상 끝, 최종영상에서의 시작) — 훅/본편처럼 같은 장면이 두 번 나와도 처리."""
    o, s, e = rules["output"], rules["subtitle"], rules["emphasis"]
    W, H = o["width"], o["height"]
    styles = [
        ("Main", s["font"], s["size_px"], s["color"], s["outline_color"], s["outline_px"], s["shadow_px"], 2,
         s["margin_bottom_px"], 3 if s.get("box") else 1, s.get("box_color", "#000000B0")),
        ("Emph", e["font"], e["size_px"], e["colors"][0], e["outline_color"], e["outline_px"], 3, 8, 0, 1, "#00000000"),
    ]
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
    ]
    for name, fn, size, col, ocol, ol, sh, align, mv, bs, back in styles:
        outline = hex_to_ass(back) if bs == 3 else hex_to_ass(ocol)
        head.append(f"Style: {name},{fn},{size},{hex_to_ass(col)},{hex_to_ass(col)},{outline},"
                    f"{hex_to_ass('#00000080')},0,0,0,0,100,100,0,0,{bs},{ol},{sh},{align},60,60,{mv},1")
    head += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    def place(a: float, b: float):
        """컷 영상 시간 → 최종 영상 시간. 구간(훅/본편) 끝을 넘어가지 않게 자른다."""
        for ca, cb, fa in mapping:
            if ca - 1e-3 <= a < cb:
                yield fa + (a - ca), fa + (min(b, cb) - ca)

    events = []
    lines = make_lines(words, rules)
    hl = hex_to_ass(s["highlight_color"])
    for ln in lines:
        parts = []
        for w in ln["words"]:
            t = _clean(w["text"])
            if s.get("highlight_words") and w.get("emph"):
                t = f"{{\\c{hl}}}{t}{{\\c{hex_to_ass(s['color'])}}}"
            parts.append(t)
        text = " ".join(p for p in parts if p)
        for a, b in place(ln["start"], ln["end"]):
            if b - a < 0.3:  # 훅 끝에 걸쳐 한순간 스치는 자막은 넣지 않는다
                continue
            events.append(f"Dialogue: 1,{_t(a)},{_t(b)},Main,,0,0,0,,{text}")

    # 강조 자막: 위쪽에 크게 '팝' (커졌다가 제자리)
    y = int(H * e["position_y"])
    pop = e["pop_ms"]
    emph_times = []
    k = 0
    for w in words:
        if not w.get("emph"):
            continue
        col = hex_to_ass(e["colors"][k % len(e["colors"])])
        k += 1
        label = _clean(w.get("emph_text") or w["text"])
        anim = (f"{{\\an8\\pos({W // 2},{y})\\c{col}\\fscx40\\fscy40\\t(0,{pop},\\fscx115\\fscy115)"
                f"\\t({pop},{pop + 90},\\fscx100\\fscy100)\\fad(0,180)}}")
        for a, b in place(w["out_start"], w["out_start"] + e["duration_sec"]):
            if b - a < 0.4:  # 구간 끝에 걸려 너무 짧게 스치는 강조는 넣지 않는다
                continue
            events.append(f"Dialogue: 2,{_t(a)},{_t(b)},Emph,,0,0,0,,{anim}{label}")
            emph_times.append({"t": a, "text": label})
    out.write_text("\n".join(head + events + (extra_events or [])) + "\n", encoding="utf-8-sig")
    return emph_times
