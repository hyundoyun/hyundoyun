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
    print("== 구글 드라이브 ==")
    has = bool(os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")) or any(k.startswith("RCLONE_CONFIG_GDRIVE") for k in os.environ)
    print(f"  {'✅ 자동 받기 설정됨' if has else '⚪ 자동 받기 미설정 (공유 링크로는 가능) — docs/구글드라이브_연결.md'}")
    return 1 if bad else 0
