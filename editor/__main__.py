"""사용법

  python -m editor edit 영상.mp4 [--title "영상 제목"]          처음부터 끝까지 자동 편집
  python -m editor edit 영상.mp4 --stop script                  교정 대본까지만 (검토용)
  python -m editor edit 영상.mp4 --start cut                    script.json 고친 뒤 이어서
  python -m editor script 영상.mp4 --set 15=편집 --drop 9-11 --show   교정 대본 손보기
  python -m editor drive [공유링크]                               구글 드라이브에서 받아서 편집
  python -m editor analyze 유튜브링크1 링크2 ...                  레퍼런스 분석 → 규칙집 갱신
  python -m editor feedback "자막이 너무 커요"                    아쉬운 점 → 규칙집에 반영
  python -m editor check                                         설치 상태·인터넷 연결 점검
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="editor", description="내 전용 영상 편집기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("edit", help="영상 자동 편집")
    e.add_argument("video")
    e.add_argument("--title")
    e.add_argument("--out", type=Path)
    e.add_argument("--start", default="transcribe", choices=["transcribe", "script", "cut", "render", "qc"])
    e.add_argument("--stop", default="qc", choices=["transcribe", "script", "cut", "render", "qc"])
    d = sub.add_parser("drive", help="구글 드라이브에서 받아 편집")
    d.add_argument("link", nargs="?")
    d.add_argument("--folder", default="편집할영상")
    d.add_argument("--download-only", action="store_true")
    a = sub.add_parser("analyze", help="레퍼런스 유튜브 분석")
    a.add_argument("urls", nargs="*", help="비우면 config/references.txt 목록을 분석")
    f = sub.add_parser("feedback", help="아쉬운 점을 규칙집에 추가")
    f.add_argument("text")
    f.add_argument("--applied", action="append", default=[], help="Claude가 직접 바꾼 규칙 설명")
    sc = sub.add_parser("script", help="교정 대본 손보기 (python -m editor.script_edit 설명 참고)")
    sc.add_argument("video")
    for flag in ("--set", "--drop", "--keep", "--emph", "--unemph", "--broll"):
        sc.add_argument(flag, action="append")
    sc.add_argument("--hook")
    sc.add_argument("--title")
    sc.add_argument("--clear-broll", action="store_true")
    sc.add_argument("--show", action="store_true")
    sub.add_parser("check", help="환경 점검")
    args = ap.parse_args(argv)

    if args.cmd == "edit":
        from .pipeline import edit

        out = edit(Path(args.video), args.title, args.out, args.start, args.stop)
        print(f"\n결과: {out}")
    elif args.cmd == "drive":
        from . import gdrive
        from .pipeline import edit

        v = gdrive.download_link(args.link) if args.link else gdrive.latest(args.folder)
        print(f"받은 영상: {v}")
        if not args.download_only:
            out = edit(v)
            link = gdrive.upload(out)
            print(f"\n결과: {out}" + (f"\n드라이브 업로드: {link}" if link else ""))
    elif args.cmd == "analyze":
        from .analyze import analyze

        urls = args.urls
        if not urls:
            from .common import ROOT

            ref = ROOT / "config" / "references.txt"
            urls = [ln.strip() for ln in ref.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.startswith("#")]
        analyze(urls)
    elif args.cmd == "feedback":
        from .feedback import add

        add(args.text, args.applied)
    elif args.cmd == "script":
        from .script_edit import edit_script

        edit_script(Path(args.video), args)
    elif args.cmd == "check":
        from .doctor import doctor

        sys.exit(doctor())


if __name__ == "__main__":
    main()
