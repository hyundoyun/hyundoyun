"""시험용 영상 만들기: 더듬기·반복·긴 멈춤·개인정보 노출·화면 멈춤이 일부러 들어간 영상."""
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/sample.mp4")
TMP = OUT.parent / "_sample_tmp"
TMP.mkdir(parents=True, exist_ok=True)
SR = 48000

# (말, 뒤에 쉬는 시간)
LINES = [
    ("안녕하세요 여러분", 0.4),
    ("오늘은 오늘은 유튜브 편집을 쉽게 하는 방법을 알려드릴게요", 1.8),   # 반복 + 긴 멈춤
    ("음", 0.6),                                                          # 군말
    ("이 방법을 쓰면", 0.9),                                               # 다시 말하기(미완성)
    ("이 방법을 쓰면 편집 시간이 3배 빨라집니다!", 0.5),
    ("첫 번째로 컷 편집이 진짜 중요합니다", 2.2),
    ("어", 0.5),
    ("두 번째는 자막인데요 자막은 크게 넣는 게 좋습니다", 0.6),
    ("마지막으로 애플 공식 홈페이지에서도 이런 기능을 소개하고 있어요", 0.6),
    ("구독과 좋아요 부탁드립니다", 1.0),
]


TTS_DIR = Path(__file__).resolve().parent.parent / "models" / "vits-mimic3-ko_KO-kss_low"
if not TTS_DIR.exists():
    url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-mimic3-ko_KO-kss_low.tar.bz2"
    subprocess.run(f"curl -sSLf {url} | tar xj -C {TTS_DIR.parent}", shell=True, check=True)
import sherpa_onnx  # noqa: E402

_tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
    vits=sherpa_onnx.OfflineTtsVitsModelConfig(model=str(TTS_DIR / "ko_KO-kss_low.onnx"), lexicon="",
                                               tokens=str(TTS_DIR / "tokens.txt"), data_dir=str(TTS_DIR / "espeak-ng-data")),
    num_threads=4)))


def tts(text, path):
    g = _tts.generate(text, sid=0, speed=1.0)
    sf.write(str(path), np.array(g.samples, dtype=np.float32), g.sample_rate)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-ar", str(SR), "-ac", "1", str(path) + ".r.wav"], check=True)
    a, _ = sf.read(str(path) + ".r.wav", dtype="float32")
    return a


parts = [np.zeros(int(0.5 * SR), np.float32)]
starts = []
for i, (t, pause) in enumerate(LINES):
    starts.append(sum(len(x) for x in parts) / SR)
    parts.append(tts(t, TMP / f"l{i}.wav") * 0.8)
    parts.append(np.zeros(int(pause * SR), np.float32))
audio = np.concatenate(parts)
dur = len(audio) / SR
sf.write(str(TMP / "voice.wav"), audio, SR)

W, H = 1920, 1080
f = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 46)
pii = Image.new("RGBA", (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(pii)
d.rectangle([80, 760, 1100, 960], fill=(255, 255, 255, 255))
d.text((110, 780), "이메일: hong.gildong@gmail.com", font=f, fill=(0, 0, 0))
d.text((110, 870), "연락처 010-1234-5678", font=f, fill=(0, 0, 0))
pii.save(TMP / "pii.png")

freeze_a = starts[7] + 0.2  # '두 번째는 자막인데요…' 말하는 동안 화면이 멈춤
freeze_b = freeze_a + 3.0
pii_a, pii_b = 6.0, 12.0
vf = (f"[0:v]drawtext=fontfile=/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf:text='내 화면 녹화':x=60:y=60:fontsize=50:fontcolor=white[bg];"
      f"[bg][1:v]overlay=0:0:enable='between(t,{pii_a},{pii_b})'[v1];"
      # 화면 멈춤: freeze_a 의 장면을 3초간 붙잡는다
      f"[v1]split[x][y];[x]trim=0:{freeze_a},setpts=PTS-STARTPTS[p1];"
      f"[y]split[y1][y2];[y1]trim={freeze_a}:{freeze_a + 1/30},setpts=PTS-STARTPTS,loop=90:1:0,setpts=N/30/TB[fz];"
      f"[y2]trim={freeze_b}:{dur},setpts=PTS-STARTPTS[p3];[p1][fz][p3]concat=n=3:v=1:a=0,format=yuv420p[v]")
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=30:d={dur}",
                "-loop", "1", "-i", str(TMP / "pii.png"), "-i", str(TMP / "voice.wav"),
                "-filter_complex", vf, "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "23", "-c:a", "aac", "-shortest", str(OUT)], check=True)
print(f"{OUT} ({dur:.1f}초) / 개인정보 {pii_a}~{pii_b}초 / 화면 멈춤 {freeze_a:.1f}~{freeze_b:.1f}초")
