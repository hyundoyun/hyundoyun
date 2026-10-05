# 내 전용 영상 편집기

말하는 영상을 넣으면 **편집 규칙집**대로 알아서 편집해 mp4로 만들어 주는 프로그램입니다(파이썬 + ffmpeg).

## 이렇게 부르면 됩니다 (Claude Code 에서)
- “구글 드라이브에 영상 올렸어, 편집해줘”
- “이 영상 편집해줘: <드라이브 공유 링크>”
- “자막이 너무 커요” / “배경음이 시끄러워요” → 규칙집에 추가되고 다음 편집부터 반영
- “이 영상들처럼 편집해줘: <유튜브 링크 3~4개>” → 레퍼런스 분석 후 규칙집 갱신

구글 드라이브 연결은 [docs/구글드라이브_연결.md](docs/구글드라이브_연결.md), 편집 규칙은 [docs/편집규칙집.md](docs/편집규칙집.md).

## 편집 순서
① 받아쓰기 → ② 교정 대본(더듬음·반복·긴 멈춤만 빼기, 말 내용·말투 그대로) → ③ 컷 편집
→ ④ 자막·강조 자막·효과음·배경음 → ⑤ 자료화면(공식 사이트·기사 제목 + 작은 출처) → ⑥ mp4 저장
→ 검수(자막 오타·소리 끊김·화면 멈춤·개인정보) 후 고쳐서 다시 저장 → 보고서

## 직접 실행
```bash
./setup.sh                                   # 필요한 도구 설치(한 번)
python3 -m editor check                      # 설치·연결 점검
python3 -m editor edit 영상.mp4 --title "제목"  # 끝까지 자동
python3 -m editor edit 영상.mp4 --stop script   # 교정 대본까지만 → work/<영상>/교정대본.md 확인
python3 -m editor script 영상.mp4 --show        # 대본 손보기(--set/--drop/--emph/--broll …)
python3 -m editor edit 영상.mp4 --start cut     # 손본 대본으로 이어서
python3 -m editor feedback "자막이 너무 커요"
python3 -m editor analyze <유튜브 링크들>
```
결과: `output/<영상>_편집본.mp4`, `output/<영상>_편집본_검수보고서.md`

## 폴더
| 경로 | 내용 |
|---|---|
| `editor/` | 프로그램 (transcribe, clean_script, cut, subtitles, sounds, broll, render, qc, analyze, feedback, gdrive) |
| `config/rules.json` | 규칙집(프로그램이 읽는 값) |
| `docs/편집규칙집.md` | 규칙집(사람이 읽는 설명 + 피드백 기록) |
| `assets/fonts` | 자막 글꼴(Pretendard, Black Han Sans — 둘 다 OFL 무료 글꼴) |
| `assets/bgm`, `assets/sfx` | 내 배경음·효과음을 넣는 곳(없으면 자동 합성) |
| `.claude/skills/video-edit` | 한 문장으로 부르는 스킬 |
| `tests/make_sample.py` | 더듬기·개인정보·화면 멈춤이 들어간 시험 영상 생성 |
