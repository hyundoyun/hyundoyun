"""공용 도구: 경로, 규칙 로드, ffmpeg 실행, 로그."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 글자 인식(tesseract)을 여러 개 동시에 돌릴 때 서로 CPU를 뺏으며 수십 배 느려지는 것을 막는다
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "config" / "rules.json"
MODELS = ROOT / "models"
ASSETS = ROOT / "assets"


def log(msg: str) -> None:
    print(f"[편집기] {msg}", flush=True)


def load_rules(path: Path | None = None) -> dict:
    with open(path or RULES_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_rules(rules: dict, path: Path | None = None) -> None:
    with open(path or RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def asset(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def run(cmd: list[str], quiet: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    if not quiet:
        log(" ".join(map(str, cmd)))
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.stderr.write(r.stderr[-4000:])
        raise RuntimeError(f"명령 실패: {cmd[0]} (코드 {r.returncode})")
    return r


def ffmpeg(*args, check: bool = True) -> subprocess.CompletedProcess:
    return run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=check)


def probe(path: Path) -> dict:
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)])
    return json.loads(r.stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def video_info(path: Path) -> dict:
    info = probe(path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    fps = 30.0
    if v and v.get("avg_frame_rate", "0/0") != "0/0":
        n, d = v["avg_frame_rate"].split("/")
        fps = float(n) / float(d) if float(d) else 30.0
    return {
        "width": int(v["width"]) if v else 0,
        "height": int(v["height"]) if v else 0,
        "fps": fps,
        "has_audio": a is not None,
        "duration": float(info["format"]["duration"]),
    }


def hex_to_ass(color: str) -> str:
    """#RRGGBB(AA) → ASS &HAABBGGRR (ASS의 알파는 00=불투명)."""
    c = color.lstrip("#")
    r, g, b = c[0:2], c[2:4], c[4:6]
    a = 255 - int(c[6:8], 16) if len(c) == 8 else 0
    return f"&H{a:02X}{b}{g}{r}".upper()


def hex_to_rgba(color: str) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    a = int(c[6:8], 16) if len(c) == 8 else 255
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), a


def need(tool: str) -> None:
    if not shutil.which(tool):
        raise SystemExit(f"{tool} 가 없습니다. ./setup.sh 를 먼저 실행하세요.")


def workdir_for(video: Path, base: Path | None = None) -> Path:
    base = base or ROOT / "work"
    d = base / video.stem
    d.mkdir(parents=True, exist_ok=True)
    return d


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")
