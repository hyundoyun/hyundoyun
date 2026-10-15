"""설치·연결 상태 점검."""
from __future__ import annotations

import importlib
import os
import shutil
import urllib.request

from .common import ROOT
from .transcribe import SHERPA_DIR, VAD_PATH

HOSTS = {
    "www.youtube.com": "레퍼런스 유튜브 내려받기",
    "huggingface.co": "고정확도 받아쓰기(whisper) 모델",
    "drive.google.com": "구글 드라이브 영상 받기",
    "www.googleapis.com": "구글 드라이브 API",
    "www.google.com": "자료화면용 사이트 캡처(일반 웹)",
}


def reach(host: str) -> bool:
    try:
        urllib.request.urlopen(f"https://{host}", timeout=6)
        return True
    except Exception as e:
        return getattr(e, "code", None) is not None


def youtube_status() -> str:
    """로그인 쿠키가 있는지, 실제로 영상 정보를 받아올 수 있는지(로봇 확인 통과) 시험한다."""
    from .analyze import YT_OPTS
    from .common import run

    opts = YT_OPTS()
    r = run(["yt-dlp", *opts, "--simulate", "-f", "b", "--no-playlist", "--print", "title",
             "https://www.youtube.com/watch?v=jNQXAC9IVRw"], check=False)
    if r.returncode == 0:
        return "✅ 영상 받기 가능" + (" (로그인 쿠키 사용)" if "--cookies" in opts else "")
    if "--cookies" in opts:
        return "❌ 쿠키가 있지만 막힘(쿠키가 만료됐을 수 있음) — docs/유튜브_연결.md 로 다시 내보내기"
    return "⚪ 로봇 확인으로 막힘 → 미리보기 그림만 가능. 연결: docs/유튜브_연결.md"


def doctor() -> int:
    bad = 0
    print("== 프로그램 ==")
    for t in ("ffmpeg", "ffprobe", "tesseract"):
        ok = shutil.which(t) is not None
        bad += not ok
        print(f"  {'✅' if ok else '❌'} {t}")
    for m in ("numpy", "soundfile", "PIL", "cv2", "pytesseract", "sherpa_onnx", "faster_whisper", "gdown", "playwright", "yt_dlp"):
        try:
            importlib.import_module(m)
            print(f"  ✅ {m}")
        except Exception:
            bad += 1
            print(f"  ❌ {m}")
    ok = (SHERPA_DIR / "tokens.txt").exists() and VAD_PATH.exists()
    bad += not ok
    print(f"  {'✅' if ok else '❌'} 오프라인 한국어 받아쓰기 모델")
    ok = (ROOT / "assets/fonts/Pretendard-ExtraBold.otf").exists()
    print(f"  {'✅' if ok else '❌'} 자막 글꼴")
    print("== 인터넷 연결 ==")
    for h, why in HOSTS.items():
        print(f"  {'✅' if reach(h) else '🚫'} {h} — {why}")
    print("== 유튜브 레퍼런스 받기 ==")
    print(f"  {youtube_status()}")
    print("== 구글 드라이브 ==")
    has = bool(os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")) or any(k.startswith("RCLONE_CONFIG_GDRIVE") for k in os.environ)
    print(f"  {'✅ 자동 받기 설정됨' if has else '⚪ 자동 받기 미설정 (공유 링크로는 가능) — docs/구글드라이브_연결.md'}")
    return 1 if bad else 0
