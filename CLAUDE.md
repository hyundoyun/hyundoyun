# 내 전용 영상 편집기

사용자가 “구글 드라이브에 영상 올렸어, 편집해줘”, “영상 편집해줘”처럼 말하면 **video-edit 스킬**
(`.claude/skills/video-edit/SKILL.md`)을 따라 묻지 말고 끝까지 편집한다.
편집 결과에 대한 아쉬운 점은 같은 스킬 7단계대로 규칙집(`config/rules.json` + `docs/편집규칙집.md`)에 추가한다.

- 사용자는 편집을 모른다: 전문 용어 없이 쉬운 말로 보고한다.
- 말 내용·말투는 절대 바꾸지 않는다(빼기와 인식 오류 표기 수정만).
- 자료화면의 기사 제목·출처는 실제로 확인한 것만 쓴다.
- 코드: `editor/` (파이썬 + ffmpeg). 설치: `./setup.sh`. 점검: `python3 -m editor check`.
- 시험: `python3 tests/make_sample.py tests/sample.mp4 && python3 -m editor edit tests/sample.mp4`.
