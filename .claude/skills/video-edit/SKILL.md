---
name: video-edit
description: 내 전용 유튜브 영상 편집기. "구글 드라이브에 영상 올렸어, 편집해줘", "영상 편집해줘", "이 영상 편집", "편집 규칙 바꿔줘", "자막이 너무 커/배경음이 시끄러워" 같은 편집 결과 피드백, "레퍼런스 분석해줘 + 유튜브 링크"에 사용. 받아쓰기→교정 대본→컷→자막·강조·효과음·배경음→자료화면→mp4→검수까지 묻지 않고 끝까지 한다.
---

# 영상 편집 스킬

사용자는 편집을 전혀 모른다고 가정한다. **묻지 말고 끝까지** 하고, 끝나면 쉬운 말로 결과만 보고한다.
규칙은 `config/rules.json`(프로그램용)과 `docs/편집규칙집.md`(사람용). 저장소 루트에서 실행한다.

## 0. 준비 (매번)
```bash
./setup.sh            # 이미 설치돼 있으면 몇 초 안에 끝남
python3 -m editor check
```

## 1. 영상 받기
- 사용자가 링크를 줬으면: `python3 -m editor drive "<링크>" --download-only`
- “드라이브에 올렸어”만 말했으면: `python3 -m editor drive --download-only` (‘편집할영상’ 폴더의 최신 영상)
- `NO_DRIVE_ACCESS` 가 나오면: 편집을 멈추지 말고 `docs/구글드라이브_연결.md` 의 **방법 A**를 쉬운 말로 단계별 안내하고,
  당장은 공유 링크(방법 B)를 달라고 한다. 키를 채팅에 붙여넣으라고 하지 않는다(환경 변수 `GDRIVE_SERVICE_ACCOUNT_JSON`).
- 링크 다운로드가 403/연결 실패면: 환경 Network access 에서 drive.google.com, drive.usercontent.google.com 허용 안내.
- 받은 파일 경로를 VIDEO 라 하자.

## 2. 받아쓰기 + 교정 대본 (자동)
```bash
python3 -m editor edit VIDEO --stop script
python3 -m editor script VIDEO --show        # 단어 번호와 함께 보기
```
`work/<영상이름>/교정대본.md` 를 읽고 **Claude 가 검토**한다. 원칙: **말 내용·말투는 절대 바꾸지 않는다.**
- 받아쓰기 인식 오류만 고친다(문맥상 분명히 그렇게 말한 것): `--set 15=편집`
  (사투리·구어체·반말·말버릇은 그대로 둔다. 맞춤법 교정 수준을 넘는 문장 다듬기 금지)
- 알고리즘이 놓친 더듬음·같은 말 반복·다시 말하기를 뺀다: `--drop 9-11`. 잘못 지운 건 `--keep 3`.
- 강조 자막: 숫자·핵심 주장·결론 위주로 분당 최대 규칙 수 이내. `--emph 17=3배`, 과한 것은 `--unemph 23`.
- 제목: 내용을 대표하는 짧은 제목 `--title "..."` (사용자가 준 제목이 있으면 그것).
- 훅: 자동(첫 강조 문장). 더 좋은 한 문장이 있으면 `--hook 34-41`.

## 3. 자료화면 찾기 (Claude 가 직접)
교정 대본에서 회사·서비스·제품·기관·뉴스·통계·사건이 언급된 곳마다(최소 6초 간격, 1분에 2~3개 이내):
1. WebSearch 로 **공식 사이트**(제품/서비스) 또는 **신뢰할 만한 언론 기사**(사건·통계)를 찾는다.
2. 기사면 WebFetch 로 **실제 제목을 원문 그대로** 확인한다. 지어내지 않는다. 못 찾으면 넣지 않는다.
3. 추가:
```bash
python3 -m editor script VIDEO --broll '{"at":"그 말을 하는 대본 속 문구","url":"https://...","source":"사이트·언론사 이름","kind":"site 또는 article","title":"페이지/기사 제목 원문","sub":"부제나 한 줄 설명(선택)"}'
```
`at` 은 교정 대본에 실제로 있는 연속된 말이어야 한다. 화면 캡처가 막혀 있으면 프로그램이 제목 카드로 대신 만든다.

## 4. 컷 → 자막·소리 → 자료화면 → mp4 → 검수 (자동)
```bash
python3 -m editor edit VIDEO --start cut
```
검수(자막 오타 사전, 소리 끊김, 화면 멈춤, 개인정보 모자이크)를 스스로 돌리고 고친 뒤 다시 저장한다.

## 5. Claude 의 최종 검수 (반드시)
- `output/<이름>_편집본_검수보고서.md` 를 읽는다. ⚠️ 항목이 있으면 원인을 보고 고친 뒤 4단계를 다시 돌린다.
- 자막 오타: `work/<이름>/final.ass` 의 Dialogue 줄을 전부 읽고 오타·인식 오류가 있으면 `--set` 으로 고치고 `--start cut` 재실행.
- 화면 확인: `ffmpeg -i OUT -vf "fps=1/3,scale=480:-2,tile=4x6" -frames:v 1 work/check.png` 로 훑어보고
  (Read 로 이미지 확인) 자막 겹침·잘림, 모자이크 누락, 자료화면 위치를 본다.
- 드라이브가 연결돼 있으면 `python3 -c "from editor.gdrive import upload; print(upload(__import__('pathlib').Path('OUT')))"`.
- 완성 파일을 SendUserFile 로 보낸다(크면 경로 안내).

## 6. 보고 (쉬운 말, 짧게)
원본 길이 → 완성 길이, 뺀 것(더듬음 n곳, 반복 n곳, 긴 멈춤 n곳), 넣은 것(자막, 강조 n개, 자료화면 n개 + 출처),
검수 결과(오타 수정 n곳, 소리 끊김, 화면 멈춤, 개인정보 모자이크 n곳), 파일 위치. 마지막에 “아쉬운 점을 말해 주시면 규칙에 넣어 다음부터 반영합니다.”

## 7. 피드백 → 규칙집 (사용자가 아쉬운 점을 말하면)
1. `python3 -m editor feedback "사용자 말 그대로"` — 흔한 요청(자막 크기/위치, 배경음·효과음 크기, 강조 빈도, 줌, 템포, 자료화면 길이, "A를 B로" 표기)은 자동 반영된다.
2. 자동 반영이 없거나 부족하면 Claude 가 `config/rules.json` 의 알맞은 값을 직접 바꾸고, `docs/편집규칙집.md` 의 해당 설명도 같이 고친다.
   그리고 `python3 -m editor feedback "사용자 말" --applied "바꾼 내용"` 으로 기록을 남긴다(자동 반영과 중복되지 않게 한 번만).
3. 지금 영상에도 적용: `python3 -m editor edit VIDEO --start cut` (받아쓰기·교정 대본은 재사용).
4. 규칙 변경은 커밋·푸시해서 다음 세션에도 남긴다.

## 8. 레퍼런스 분석 ("이 영상들처럼 편집해줘" + 유튜브 링크)
```bash
python3 -m editor analyze "<링크1>" "<링크2>" "<링크3>"   # 링크를 새로 받았을 때(config/references.txt 에도 추가)
python3 -m editor analyze                                 # 저장된 레퍼런스 목록(config/references.txt) 분석
```
- 유튜브가 막혀 있으면 환경 Network access 에서 www.youtube.com, *.googlevideo.com, i.ytimg.com 허용(또는 더 넓은 단계) 안내.
- 끝나면 `refs/<ID>/contact_*.jpg` 장면 썸네일을 Read 로 직접 보고, 숫자로 안 잡히는 것(글꼴 느낌, 자막 상자 유무,
  인트로·아웃트로 구성, 자료화면 배치)을 `docs/편집규칙집.md` 2장과 `rules.json` 에 반영한 뒤 커밋·푸시.
