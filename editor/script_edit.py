"""교정 대본(script.json) 손보기 — Claude 가 검토할 때 쓰는 도구.

  --set 15=편집          단어 표기 고치기(받아쓰기 인식 오류만! 말 내용·말투는 그대로)
  --drop 9-11            단어 빼기(놓친 더듬음·다시 말하기)
  --keep 3               뺀 단어 되살리기
  --emph 17[=3배]        강조 자막 지정(= 뒤는 화면에 띄울 글자)
  --unemph 23            강조 해제
  --hook 34-41 / off     맨 앞에 미리 보여줄 구간
  --title "제목"
  --broll '{"at":"애플 공식 홈페이지","url":"https://www.apple.com/kr/","source":"Apple","title":"Apple (대한민국)"}'
  --show                 단어 번호와 함께 보기
"""
from __future__ import annotations

import json
from pathlib import Path

from .clean_script import strip_josa, write_readable
from .common import load_rules, read_json, workdir_for, write_json


def _ids(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def edit_script(video: Path, args) -> None:
    work = workdir_for(video.resolve())
    sp = work / "script.json"
    s = read_json(sp)
    by_id = {w["id"]: w for w in s["words"]}
    for item in args.set or []:
        i, t = item.split("=", 1)
        by_id[int(i)].setdefault("heard", by_id[int(i)]["text"])
        by_id[int(i)]["text"] = t
    for spec in args.drop or []:
        for i in _ids(spec):
            by_id[i]["keep"], by_id[i]["reason"] = False, "검토 중 삭제(더듬음/다시 말함)"
    for spec in args.keep or []:
        for i in _ids(spec):
            by_id[i]["keep"], by_id[i]["reason"] = True, None
    for item in args.emph or []:
        i, _, label = item.partition("=")
        w = by_id[int(i)]
        w["emph"], w["emph_text"] = True, label or strip_josa(w["text"])
    for spec in args.unemph or []:
        for i in _ids(spec):
            by_id[i]["emph"] = False
    if args.hook:
        if args.hook == "off":
            s["hook"] = "off"
        else:
            a, b = args.hook.split("-")
            s["hook"] = {"from_word": int(a), "to_word": int(b)}
    if args.title:
        s["title"] = args.title
    if args.clear_broll:
        s["broll"] = []
    for b in args.broll or []:
        item = json.loads(b)
        if item not in s.setdefault("broll", []):
            s["broll"].append(item)
    write_json(sp, s)
    write_readable(s["words"], load_rules(), work / "교정대본.md")
    if args.show:
        line = []
        for w in s["words"]:
            mark = "" if w["keep"] else "✗"
            mark += "★" if w.get("emph") else ""
            line.append(f"{w['id']}:{mark}{w['text']}")
        print(" ".join(line))
        print("자료화면:", json.dumps(s.get("broll", []), ensure_ascii=False))
        print("훅:", s.get("hook"), "/ 제목:", s.get("title"))
