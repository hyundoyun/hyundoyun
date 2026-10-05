#!/usr/bin/env bash
# 영상 편집기에 필요한 도구를 한 번에 설치한다. 여러 번 실행해도 안전(이미 있으면 건너뜀).
set -uo pipefail
cd "$(dirname "$0")"
log() { echo "[setup] $*"; }

need_apt=()
command -v ffmpeg >/dev/null || need_apt+=(ffmpeg)
command -v tesseract >/dev/null || need_apt+=(tesseract-ocr)
dpkg -s tesseract-ocr-kor >/dev/null 2>&1 || need_apt+=(tesseract-ocr-kor)
fc-list :lang=ko | grep -qi nanum || need_apt+=(fonts-nanum)
if [ ${#need_apt[@]} -gt 0 ]; then
  log "apt 설치: ${need_apt[*]}"
  SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  $SUDO apt-get update -qq >/dev/null 2>&1
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq "${need_apt[@]}" >/dev/null 2>&1 || log "apt 설치 일부 실패"
fi

python3 -c "import numpy, soundfile, PIL, cv2, pytesseract, sherpa_onnx, faster_whisper, gdown, yt_dlp, playwright, googleapiclient" 2>/dev/null || {
  log "파이썬 패키지 설치"
  pip install -q -r requirements.txt 2>&1 | grep -v -i warning | tail -3
}

mkdir -p models
if [ ! -f models/sherpa-onnx-zipformer-korean-2024-06-24/tokens.txt ]; then
  log "오프라인 한국어 받아쓰기 모델 받는 중(약 80MB)"
  curl -sSLf https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-zipformer-korean-2024-06-24.tar.bz2 \
    | tar xj -C models && rm -f models/sherpa-onnx-zipformer-korean-2024-06-24/{encoder,decoder,joiner}-epoch-99-avg-1.onnx
fi
[ -f models/silero_vad.onnx ] || curl -sSLf -o models/silero_vad.onnx https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx

mkdir -p assets/fonts
[ -f assets/fonts/BlackHanSans-Regular.ttf ] || curl -sSLf -o assets/fonts/BlackHanSans-Regular.ttf \
  https://raw.githubusercontent.com/google/fonts/main/ofl/blackhansans/BlackHanSans-Regular.ttf
if [ ! -f assets/fonts/Pretendard-ExtraBold.otf ]; then
  tmp=$(mktemp -d)
  curl -sSLf -o "$tmp/p.zip" https://github.com/orioncactus/pretendard/releases/download/v1.3.9/Pretendard-1.3.9.zip && \
    unzip -q -o "$tmp/p.zip" 'public/static/Pretendard-Bold.otf' 'public/static/Pretendard-ExtraBold.otf' -d "$tmp" && \
    cp "$tmp"/public/static/*.otf assets/fonts/
  rm -rf "$tmp"
fi
log "완료. 점검: python3 -m editor check"
