"""5. 아쉬운 점 → 규칙집에 추가 → 다음 편집부터 자동 반영.

자주 나오는 말은 여기서 바로 숫자로 바꿔 반영하고, 나머지는 Claude 가 규칙(rules.json)으로 옮긴다.
모든 피드백은 rules.json 의 learned_from_feedback 와 docs/편집규칙집.md '피드백 반영 기록'에 남는다.
"""
from __future__ import annotations

import datetime as dt
import re

from .common import ROOT, load_rules, log, save_rules

# '너무 커요'(=줄여라)와 '작게'(=줄여라), '작아요'(=키워라)를 구분한다
LESS = r"(너무커|너무크|커요|커서|크다|컸|줄여|작게|작았으면|시끄|거슬|과해|과하|부담|어지러|빼|없애|낮춰|세요$|너무세)"
MORE = r"(작아|작다|작네|작은것같|안보|안들|키워|크게|더크|늘려|부족|심심|높여|올려줘|약해)"


def _scale(r, sec, key, f, lo, hi, nd=1):
    old = r[sec][key]
    new = round(max(lo, min(hi, old * f)), nd) if isinstance(old, float) else int(max(lo, min(hi, round(old * f))))
    r[sec][key] = new
    return f"{sec}.{key}: {old} → {new}"


def _add_db(r, sec, key, d):
    old = r[sec][key]
    r[sec][key] = round(old + d, 1)
    return f"{sec}.{key}: {old} → {r[sec][key]}"


def auto_apply(text: str, r: dict) -> list[str]:
    t = text.replace(" ", "")
    ch: list[str] = []
    more = bool(re.search(MORE, t))
    less = bool(re.search(LESS, t)) and not more
    if "강조" in t:
        if re.search(r"(많|자주|과해|정신없)", t):
            ch.append(_scale(r, "emphasis", "max_per_minute", 0.6, 1, 20))
            ch.append(_scale(r, "emphasis", "min_gap_sec", 1.6, 1.0, 30.0))
        elif re.search(r"(적|더넣|부족|심심)", t):
            ch.append(_scale(r, "emphasis", "max_per_minute", 1.5, 1, 20))
            ch.append(_scale(r, "emphasis", "min_gap_sec", 0.65, 1.0, 30.0))
        elif less:
            ch.append(_scale(r, "emphasis", "size_px", 0.85, 60, 220))
        elif more:
            ch.append(_scale(r, "emphasis", "size_px", 1.15, 60, 220))
    elif "자막" in t:
        if re.search(r"(위로|올려)", t):
            ch.append(_scale(r, "subtitle", "margin_bottom_px", 1.4, 20, 500))
        elif re.search(r"(아래로|내려)", t):
            ch.append(_scale(r, "subtitle", "margin_bottom_px", 0.7, 20, 500))
        elif re.search(r"(빨리지나|빨리넘어|짧게떠)", t):
            ch.append(_scale(r, "subtitle", "min_duration_sec", 1.4, 0.3, 3.0))
        elif re.search(r"(길어|한줄에너무|글자가너무많)", t):
            ch.append(_scale(r, "subtitle", "max_chars_per_line", 0.8, 8, 40))
        elif less:
            ch.append(_scale(r, "subtitle", "size_px", 0.88, 36, 140))
        elif more:
            ch.append(_scale(r, "subtitle", "size_px", 1.12, 36, 140))
    if re.search(r"(배경음|브금|BGM|음악)", t, re.I):
        if less:
            ch.append(_add_db(r, "bgm", "volume_db", -4))
        elif more:
            ch.append(_add_db(r, "bgm", "volume_db", +3))
        if re.search(r"(빼|없애|넣지마)", t):
            r["bgm"]["enabled"] = False
            ch.append("bgm.enabled → false")
    if "효과음" in t:
        if re.search(r"(빼|없애|넣지마)", t):
            r["sfx"]["enabled"] = False
            ch.append("sfx.enabled → false")
        elif less:
            ch.append(_add_db(r, "sfx", "volume_db", -4))
        elif more:
            ch.append(_add_db(r, "sfx", "volume_db", +3))
    if re.search(r"(줌|확대|화면이움직|어지러)", t):
        if less or "어지러" in t:
            r["zoom"]["jumpcut_scales"] = [1.0, round(1 + (r["zoom"]["jumpcut_scales"][-1] - 1) * 0.6, 2)]
            ch.append(f"zoom.jumpcut_scales → {r['zoom']['jumpcut_scales']}")
            ch.append(_scale(r, "zoom", "punch_scale", 0.94, 1.0, 1.6, 2))
        elif more:
            r["zoom"]["jumpcut_scales"] = [1.0, round(1 + (r["zoom"]["jumpcut_scales"][-1] - 1) * 1.4, 2)]
            ch.append(f"zoom.jumpcut_scales → {r['zoom']['jumpcut_scales']}")
    if re.search(r"(호흡|템포|컷)", t) and re.search(r"(빠르|빨라|급하|숨막|정신없)", t):
        ch.append(_scale(r, "cut", "pad_after_sec", 1.6, 0.03, 0.5, 2))
        ch.append(_scale(r, "script_cleanup", "long_pause_sec", 1.3, 0.2, 2.0, 2))
    elif re.search(r"(호흡|템포|컷|늘어|지루)", t) and re.search(r"(느리|늘어|지루|길)", t):
        ch.append(_scale(r, "cut", "pad_after_sec", 0.7, 0.03, 0.5, 2))
        ch.append(_scale(r, "script_cleanup", "long_pause_sec", 0.8, 0.2, 2.0, 2))
    if "자료화면" in t:
        if re.search(r"(짧|빨리사라)", t):
            ch.append(_scale(r, "broll", "duration_sec", 1.3, 1.0, 10.0))
        elif re.search(r"(길|오래)", t):
            ch.append(_scale(r, "broll", "duration_sec", 0.75, 1.0, 10.0))
        if re.search(r"(전체|크게|꽉)", t):
            r["broll"]["mode"] = "full"
            ch.append("broll.mode → full")
    if "인트로" in t and re.search(r"(빼|없애|길)", t):
        ch.append(_scale(r, "intro", "title_card_sec", 0.6, 0.0, 5.0))
    if "아웃트로" in t and re.search(r"(빼|없애)", t):
        r["outro"]["enabled"] = False
        ch.append("outro.enabled → false")
    m = re.findall(r"[\"'“‘](.+?)[\"'”’]\s*(?:를|을|은|는)?\s*[\"'“‘](.+?)[\"'”’]\s*(?:로|으로)", text)
    for bad, good in m:  # "OO"를 "XX"로 (자막 표기 교정)
        r["qc"]["typo_dictionary"][bad] = good
        ch.append(f"qc.typo_dictionary: '{bad}' → '{good}'")
    return ch


def add(text: str, manual_changes: list[str] | None = None) -> list[str]:
    r = load_rules()
    ch = auto_apply(text, r) + (manual_changes or [])
    entry = {"date": dt.date.today().isoformat(), "feedback": text, "applied": ch or ["(Claude가 규칙으로 옮겨야 함)"]}
    r.setdefault("learned_from_feedback", []).append(entry)
    save_rules(r)
    md = ROOT / "docs" / "편집규칙집.md"
    s = md.read_text(encoding="utf-8")
    line = f"| {entry['date']} | {text} | {'; '.join(entry['applied'])} |"
    s = s.replace("<!-- FEEDBACK:END -->", line + "\n<!-- FEEDBACK:END -->")
    md.write_text(s, encoding="utf-8")
    log("피드백 저장: " + ("; ".join(ch) if ch else "자동 해석 못함 → 규칙을 직접 수정해 주세요"))
    return ch
