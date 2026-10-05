"""⑤ 자료화면: 말한 내용에 맞는 공식 사이트 화면·기사 제목을 넣고 출처를 작게 적는다.

script.json 의 "broll" 목록을 읽는다. 항목 예시:
  {"at": "애플 공식 홈페이지", "url": "https://www.apple.com/kr/", "source": "Apple",
   "kind": "site", "title": "Apple (대한민국)"}
  {"word_id": 120, "url": "https://news.example.com/123", "source": "OO일보", "kind": "article",
   "title": "기사 제목 그대로", "sub": "부제(선택)"}
- at      : 이 말을 하는 순간에 자료화면이 뜬다(교정 대본 속 문구)
- image   : 직접 준비한 그림 파일 경로(있으면 캡처하지 않음)
인터넷 화면을 캡처할 수 있으면 실제 화면을, 막혀 있으면 기사/사이트 카드를 만들어 넣는다.
"""
from __future__ import annotations

import re
from pathlib import Path

from .clean_script import norm
from .common import asset, log
from .graphics import article_card, finish_broll


def screenshot(url: str, out: Path) -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            br = p.chromium.launch()
            pg = br.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1, locale="ko-KR")
            pg.goto(url, wait_until="domcontentloaded", timeout=20000)
            pg.wait_for_timeout(1800)
            # 쿠키 동의 팝업 등 흔한 오버레이 닫기 시도
            for sel in ["button:has-text('동의')", "button:has-text('Accept')", "button:has-text('확인')"]:
                try:
                    pg.locator(sel).first.click(timeout=600)
                except Exception:
                    pass
            pg.screenshot(path=str(out))
            br.close()
        return out.exists() and out.stat().st_size > 10000
    except Exception as e:
        log(f"화면 캡처 실패({url}): {type(e).__name__} → 카드로 대체")
        return False


def find_time(item: dict, words: list[dict]) -> float | None:
    if "word_id" in item:
        for w in words:
            if w["id"] == item["word_id"]:
                return w["out_start"]
    at = norm(item.get("at", "")).replace(" ", "")
    if not at:
        return None
    toks = [norm(w["text"]) for w in words]
    for i in range(len(words)):
        acc = ""
        for j in range(i, min(len(words), i + 12)):
            acc += toks[j]
            if acc.startswith(at):
                return words[i]["out_start"]
            if not at.startswith(acc):
                break
    return None


def prepare(script: dict, timeline: dict, work: Path, rules: dict) -> list[dict]:
    b = rules["broll"]
    if not b.get("enabled", True):
        return []
    d = work / "broll"
    d.mkdir(exist_ok=True)
    out = []
    last = -1e9
    for i, item in enumerate(script.get("broll", [])):
        t = find_time(item, timeline["words"])
        if t is None:
            log(f"자료화면 {i}: '{item.get('at') or item.get('word_id')}' 위치를 대본에서 못 찾음 → 건너뜀")
            continue
        if t - last < b["min_gap_sec"]:
            continue
        raw = d / f"raw_{i}.png"
        if item.get("image") and asset(item["image"]).exists():
            raw = asset(item["image"])
        elif not (item.get("url") and screenshot(item["url"], raw)):
            article_card(item.get("title") or item.get("at", ""), item.get("source", ""), item.get("url", ""),
                         item.get("kind", "site"), rules, raw, item.get("sub", ""))
        png = finish_broll(raw, item.get("source") or re.sub(r"^https?://(www\.)?([^/]+).*", r"\2", item.get("url", "")),
                           rules, d / f"broll_{i}.png")
        dur = float(item.get("duration", b["duration_sec"]))
        out.append({"start": round(t, 3), "dur": min(dur, timeline["duration"] - t), "png": str(png)})
        last = t
    log(f"자료화면 {len(out)}개 준비")
    return out
