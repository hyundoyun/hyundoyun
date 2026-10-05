"""② 교정 대본: 더듬은 곳·같은 말을 다시 한 곳·길게 멈춘 곳을 '빼기만' 한다.

원칙: 단어를 고치거나 바꾸지 않는다. 말한 내용과 말투는 그대로.
결과
- script.json   : 기계용(단어마다 keep/이유/강조 표시). 이 파일만 고치면 다시 렌더링 가능.
- 교정대본.md    : 사람용. 뺀 부분은 ~~취소선~~ 과 이유가 적혀 있다.
"""
from __future__ import annotations

import re
from pathlib import Path

from .common import log, read_json, write_json

PUNCT = re.compile(r"[.,?!…~\"'“”‘’]")
INTENTIONAL_REPEATS = {"정말", "진짜", "너무", "많이", "네", "예", "아주", "하나", "점점", "계속",
                       "천천히", "빨리", "다시", "자", "좀", "더", "또", "잘", "꼭"}
JOSA = ("에서는", "으로는", "에서", "으로", "이라고", "라고", "까지", "부터", "에게", "한테", "처럼",
        "보다", "이랑", "랑", "하고", "은", "는", "이", "가", "을", "를", "로", "에", "도", "만", "의", "요")
NUM_RE = re.compile(r"\d|[일이삼사오육칠팔구십백천만억조]+(배|퍼센트|%|원|명|개|위|등|년|시간|분|초)")


def norm(t: str) -> str:
    return PUNCT.sub("", t).strip()


def strip_josa(t: str) -> str:
    t = norm(t)
    for j in JOSA:
        if t.endswith(j) and len(t) - len(j) >= 1:
            return t[: -len(j)]
    return t


def _drop(w: dict, reason: str) -> None:
    if w["keep"]:
        w["keep"] = False
        w["reason"] = reason


def clean(words: list[dict], rules: dict) -> list[dict]:
    cfg = rules["script_cleanup"]
    fillers = set(cfg["filler_words"])
    ws = [dict(w, id=i, keep=True, reason=None, emph=False) for i, w in enumerate(words)]

    # 1) 군말·더듬음: '어', '음' 같은 소리만 있는 단어
    for w in ws:
        if norm(w["text"]) in fillers or not norm(w["text"]):
            _drop(w, "더듬음(군말)")

    def kept():
        return [w for w in ws if w["keep"]]

    # 2) 더듬음: 뒤 단어의 앞부분만 먼저 말한 경우 ("괜 괜찮은", "그 그러니까")
    if cfg.get("remove_stutters", True):
        k = kept()
        for a, b in zip(k, k[1:]):
            ta, tb = norm(a["text"]), norm(b["text"])
            if ta and len(ta) <= 2 and len(ta) < len(tb) and tb.startswith(ta) and b["start"] - a["end"] < 1.0:
                _drop(a, "더듬음")

    # 3) 같은 말 반복: 바로 이어서 같은 말을 다시 함 → 앞의 것을 뺀다 (뒤의 말이 보통 더 또렷함)
    if cfg.get("remove_repeats", True):
        changed = True
        while changed:
            changed = False
            k = kept()
            toks = [norm(w["text"]) for w in k]
            for n in range(cfg.get("max_repeat_ngram", 6), 0, -1):
                i = 0
                while i + 2 * n <= len(k):
                    if toks[i:i + n] == toks[i + n:i + 2 * n]:
                        if n == 1 and toks[i] in INTENTIONAL_REPEATS:
                            i += 1
                            continue
                        for w in k[i:i + n]:
                            _drop(w, "같은 말 반복")
                        changed = True
                        break
                    i += 1
                if changed:
                    break

    # 4) 다시 말하기: 문장을 말하다 멈추고 같은 첫마디로 다시 시작 → 앞의 미완성 부분을 뺀다
    if cfg.get("remove_restarts", True):
        k = kept()
        toks = [norm(w["text"]) for w in k]
        i = 0
        while i < len(k):
            done = False
            for j in range(i + 2, min(len(k) - 1, i + 14)):
                head_match = toks[i] == toks[j] and toks[i + 1] == toks[j + 1]
                long_word = toks[i] == toks[j] and len(toks[i]) >= 3
                if not (head_match or long_word):
                    continue
                frag = k[i:j]
                ended = any(re.search(r"[.?!]$", w["text"]) for w in frag[:-1])
                pause = k[j]["start"] - k[j - 1]["end"]
                span = k[j]["start"] - k[i]["start"]
                if not ended and span <= cfg.get("restart_max_gap", 2.5) * 2.5 and pause >= (0.25 if head_match else 0.5):
                    for w in frag:
                        _drop(w, "다시 말함(앞부분 삭제)")
                    i = j
                    done = True
                    break
            if not done:
                i += 1
    return ws


def mark_emphasis(ws: list[dict], rules: dict) -> None:
    cfg = rules["emphasis"]
    keys = cfg.get("keywords", [])
    last = -1e9
    k = [w for w in ws if w["keep"]]
    per_min: dict[int, int] = {}
    for idx, w in enumerate(k):
        t = norm(w["text"])
        hit = False
        if cfg.get("auto_numbers", True) and NUM_RE.search(t):
            hit = True
        if any(key in t for key in keys):
            hit = True
        if cfg.get("auto_exclaim", True) and w["text"].endswith("!"):
            hit = True
        minute = int(w["start"] // 60)
        if hit and w["start"] - last >= cfg["min_gap_sec"] and per_min.get(minute, 0) < cfg["max_per_minute"]:
            w["emph"] = True
            # 숫자 + 단위가 다음 단어로 떨어져 있으면 붙여서 보여준다 ("3" "배" → "3배")
            label = strip_josa(w["text"])
            if idx + 1 < len(k) and re.fullmatch(r"[\d.,]+", label) and len(norm(k[idx + 1]["text"])) <= 3:
                label += strip_josa(k[idx + 1]["text"])
            w["emph_text"] = label
            last = w["start"]
            per_min[minute] = per_min.get(minute, 0) + 1


def write_readable(ws: list[dict], rules: dict, path: Path) -> None:
    long_pause = rules["script_cleanup"]["long_pause_sec"]
    lines = ["# 교정 대본", "",
             "> 뺀 부분은 ~~취소선~~, 강조 자막은 **굵게**, 길게 멈춘 곳은 ⏸ 로 표시했습니다. 말 내용·말투는 바꾸지 않았습니다.", ""]
    para: list[str] = []
    prev_end = None
    removed = {"더듬음": 0, "반복": 0, "다시": 0, "멈춤": 0}
    for w in ws:
        if prev_end is not None and w["start"] - prev_end >= long_pause:
            gap = w["start"] - prev_end
            para.append(f"⏸{gap:.1f}초")
            removed["멈춤"] += 1
            if gap >= rules["script_cleanup"]["sentence_break_pause_sec"] * 2:
                lines.append(" ".join(para))
                lines.append("")
                para = []
        if w["keep"]:
            para.append(f"**{w['text']}**" if w.get("emph") else w["text"])
            prev_end = w["end"]
        else:
            para.append(f"~~{w['text']}~~")
            r = w["reason"] or ""
            removed["더듬음" if "더듬" in r else "반복" if "반복" in r else "다시"] += 1
    if para:
        lines.append(" ".join(para))
    lines += ["", "---",
              f"삭제: 더듬음 {removed['더듬음']}곳 · 반복 {removed['반복']}곳 · 다시 말함 {removed['다시']}단어 · 긴 멈춤 {removed['멈춤']}곳"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_script(transcript_path: Path, work: Path, rules: dict) -> Path:
    tr = read_json(transcript_path)
    ws = clean(tr["words"], rules)
    mark_emphasis(ws, rules)
    out = work / "script.json"
    old = read_json(out) if out.exists() else {}
    script = {
        "_help": "keep=false 면 컷, text 는 자막 표기(인식 오타만 고칠 것), emph=true 면 강조 자막",
        "engine": tr.get("engine"),
        "title": old.get("title", ""),
        "hook": old.get("hook"),
        "broll": old.get("broll", []),
        "words": ws,
    }
    write_json(out, script)
    write_readable(ws, rules, work / "교정대본.md")
    (work / "교정대본.txt").write_text(" ".join(w["text"] for w in ws if w["keep"]) + "\n", encoding="utf-8")
    n_drop = sum(1 for w in ws if not w["keep"])
    log(f"교정 대본 완료: 전체 {len(ws)}단어 중 {n_drop}개 삭제 → {work / '교정대본.md'}")
    return out
