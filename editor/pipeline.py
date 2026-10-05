"""정해진 편집 순서대로 처음부터 끝까지.

① 받아쓰기 → ② 교정 대본 → ③ 컷 편집 → ④ 자막·강조자막·효과음·배경음
→ ⑤ 자료화면 → ⑥ mp4 저장 → 검수(오타·소리 끊김·화면 멈춤·개인정보) 후 고쳐서 다시 저장 → 보고서
"""
from __future__ import annotations

from pathlib import Path

from . import qc
from .clean_script import build_script, write_readable
from .common import ROOT, load_rules, log, read_json, video_info, workdir_for, write_json
from .cut import cut
from .render import render
from .transcribe import transcribe

STAGES = ["transcribe", "script", "cut", "render", "qc"]


def output_path(video: Path, out_dir: Path | None) -> Path:
    return (out_dir or ROOT / "output") / f"{video.stem}_편집본.mp4"


def run_qc(video: Path, work: Path, rules: dict, out: Path) -> dict:
    sp = work / "script.json"
    all_typos, silent_cut, freeze_fixed = [], 0, 0
    pii_regions: list[dict] = []
    rounds = 0
    report: dict = {}
    need_render = True
    for rnd in range(1, rules["qc"].get("max_fix_rounds", 2) + 2):
        rounds = rnd
        log(f"검수 {rnd}회차")
        script = read_json(sp)
        changed = False
        # 자막 오타 (맞춤법 사전)
        t = qc.fix_typos(script, rules)
        all_typos += t
        changed |= bool(t)
        # 소리 없는 '유령 단어' → 컷 (소리가 끊겨 들리는 원인)
        bad = qc.silent_words(work, rules)
        for w in script["words"]:
            if w["id"] in bad and w["keep"]:
                w["keep"], w["reason"] = False, "소리 없음(인식 오류)"
        silent_cut += len(bad)
        changed |= bool(bad)
        # 원본 화면이 멈춘 구간 → 움직임 처리
        tl = read_json(work / "timeline.json")
        fr = qc.freezes(work / "cut.mp4", rules)
        new_fix = []
        for f in fr:
            a, b = qc.cut_to_src(f["start"], tl), qc.cut_to_src(max(f["start"], f["end"] - 0.05), tl)
            if a is not None and b is not None:
                rng = [round(a, 3), round(b + 0.05, 3)]
                if rng not in script.get("fix_freeze", []):
                    new_fix.append(rng)
        if new_fix:
            script.setdefault("fix_freeze", []).extend(new_fix)
            freeze_fixed += len(new_fix)
            changed = True
        # 개인정보: 원본에서 한 번 찾아 두면 컷이 바뀌어도 그대로 따라간다
        if rnd == 1:
            pii_regions = qc.find_pii(video, work, rules)
            if pii_regions != script.get("blur", []):
                script["blur"] = pii_regions
                changed = True
        if changed:
            write_json(sp, script)
            write_readable(script["words"], rules, work / "교정대본.md")
            log("검수에서 고칠 곳 발견 → 컷 편집부터 다시")
            cut(video, sp, work, rules)
            need_render = True
        if need_render:
            render(work, rules, out)
            need_render = False
        plan = read_json(work / "render_plan.json")
        tl = read_json(work / "timeline.json")
        # 완성본 다시 확인
        st = qc.read_streams(out)
        sync_diff = abs(st["video_sec"] - st["audio_sec"])
        gaps = qc.audio_gaps(out, plan, rules)
        rem_freeze = qc.freezes(out, rules, plan["static_ranges"])
        left = qc.verify_pii(out, pii_regions, tl, plan, work, rules) if pii_regions else []
        if left and rnd <= rules["qc"].get("max_fix_rounds", 2):
            # 아직 보이면 가리는 영역을 넓히고 다시
            script = read_json(sp)
            for r in script.get("blur", []):
                r["x"], r["y"] = max(0, r["x"] - 30), max(0, r["y"] - 20)
                r["w"], r["h"] = r["w"] + 60, r["h"] + 40
                r["t0"], r["t1"] = max(0, r["t0"] - 0.5), r["t1"] + 0.5
            write_json(sp, script)
        remaining_pii = len(left)
        review = qc.review_lines(read_json(work / "timeline.json"), rules)
        report = {
            "output": str(out), "duration": plan["total"], "source_duration": video_info(video)["duration"],
            "main_duration": plan["main_duration"], "rounds": rounds,
            "typos": {"fixed": all_typos, "remaining": review},
            "audio": {"silent_words_cut": silent_cut, "gaps": gaps, "clicks": qc.clicks(work),
                      "sync_ok": sync_diff < 0.1, "sync_diff": sync_diff},
            "freeze": {"fixed": freeze_fixed, "remaining": rem_freeze},
            "pii": {"found": pii_regions, "remaining": remaining_pii},
        }
        # 최종본에서 새로 발견된 멈춤·남은 개인정보는 다음 회차에서 고친다
        if left and rnd <= rules["qc"].get("max_fix_rounds", 2):
            cut(video, sp, work, rules)
            need_render = True
            continue
        if rem_freeze and rnd <= rules["qc"].get("max_fix_rounds", 2):
            script = read_json(sp)
            for f in rem_freeze:
                c0 = f["start"] - plan["main_offset"]
                a = qc.cut_to_src(max(0, c0), tl)
                b = qc.cut_to_src(max(0, f["end"] - plan["main_offset"] - 0.05), tl)
                if a is not None and b is not None:
                    script.setdefault("fix_freeze", []).append([round(a, 3), round(b + 0.05, 3)])
            write_json(sp, script)
            cut(video, sp, work, rules)
            need_render = True
            continue
        if not changed or rnd > rules["qc"].get("max_fix_rounds", 2):
            break
    qc.write_report(work, report, work / "검수보고서.md")
    md = (work / "검수보고서.md").read_text(encoding="utf-8")
    out.with_name(out.stem + "_검수보고서.md").write_text(md, encoding="utf-8")
    return report


def edit(video: Path, title: str | None = None, out_dir: Path | None = None,
         start: str = "transcribe", stop: str = "qc", work_base: Path | None = None) -> Path:
    rules = load_rules()
    video = video.resolve()
    work = workdir_for(video, work_base)
    out = output_path(video, out_dir)
    log(f"작업 폴더: {work}")
    i0, i1 = STAGES.index(start), STAGES.index(stop)
    sp = work / "script.json"
    for st in STAGES[i0:i1 + 1]:
        if st == "transcribe":
            transcribe(video, work, rules)
        elif st == "script":
            build_script(work / "transcript.json", work, rules)
            if title:
                s = read_json(sp)
                s["title"] = title
                write_json(sp, s)
        elif st == "cut":
            if title:
                s = read_json(sp)
                s["title"] = title
                write_json(sp, s)
            cut(video, sp, work, rules)
        elif st == "render":
            if stop != "qc":  # 검수 단계가 이어지면 거기서 개인정보 흐림까지 넣어 한 번에 렌더링
                render(work, rules, out)
        elif st == "qc":
            run_qc(video, work, rules, out)  # 검수가 렌더링까지 함께 한다
    return out
