"""① 받아쓰기: 원본 영상의 음성을 단어 단위 시간과 함께 전부 받아쓴다.

엔진
- faster-whisper : 정확도 최고. 처음 실행 때 인터넷(huggingface.co)에서 모델을 받는다.
- sherpa-onnx    : 오프라인 한국어 모델(models/ 폴더). 인터넷이 막혀 있어도 동작.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .common import MODELS, ffmpeg, log, write_json

SHERPA_DIR = MODELS / "sherpa-onnx-zipformer-korean-2024-06-24"
VAD_PATH = MODELS / "silero_vad.onnx"
SR = 16000

# whisper 는 '어', '음' 같은 군말을 지워버리는 습관이 있어서, 그대로 적으라고 예시를 준다.
VERBATIM_PROMPT = "음, 어, 그러니까 그 그게, 어 이거는 음 말을 더듬은 것도 그대로 적습니다."


def extract_audio(video: Path, wav: Path) -> None:
    ffmpeg("-i", video, "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", wav)


def load_wav(wav: Path) -> np.ndarray:
    import soundfile as sf

    a, sr = sf.read(str(wav), dtype="float32")
    assert sr == SR
    return a


def _reachable(host: str) -> bool:
    import urllib.request

    try:
        urllib.request.urlopen(f"https://{host}", timeout=6)
        return True
    except Exception as e:
        return getattr(e, "code", None) is not None and e.code < 500  # HTTP 오류여도 연결은 된 것


def _whisper(wav: Path, rules: dict) -> list[dict]:
    from faster_whisper import WhisperModel

    cfg = rules["transcribe"]
    name = cfg.get("whisper_model", "large-v3")
    try:  # 이미 받아둔 모델이 있으면 인터넷 없이 사용
        model = WhisperModel(name, device="auto", compute_type="auto", local_files_only=True)
    except Exception:
        if not _reachable("huggingface.co"):
            raise RuntimeError("huggingface.co 에 연결할 수 없어 whisper 모델을 받을 수 없음")
        model = WhisperModel(name, device="auto", compute_type="auto")
    segments, _ = model.transcribe(
        str(wav),
        language=cfg.get("language", "ko"),
        word_timestamps=True,
        vad_filter=False,  # 멈춘 구간도 시간 그대로 보존해야 컷 편집이 정확하다
        condition_on_previous_text=False,
        initial_prompt=VERBATIM_PROMPT,
    )
    words = []
    for si, seg in enumerate(segments):
        for w in seg.words or []:
            t = w.word.strip()
            if t:
                words.append({"text": t, "start": round(w.start, 3), "end": round(w.end, 3), "seg": si,
                              "conf": round(float(w.probability), 3)})
    return words


def _sherpa(wav: Path) -> list[dict]:
    import sherpa_onnx

    audio = load_wav(wav)
    rec = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(SHERPA_DIR / "encoder-epoch-99-avg-1.int8.onnx"),
        decoder=str(SHERPA_DIR / "decoder-epoch-99-avg-1.int8.onnx"),
        joiner=str(SHERPA_DIR / "joiner-epoch-99-avg-1.int8.onnx"),
        tokens=str(SHERPA_DIR / "tokens.txt"),
        num_threads=4,
    )
    # 말소리 구간(VAD)으로 나눠서 인식 → 멈춘 구간은 자연스럽게 빈 시간으로 남는다
    vcfg = sherpa_onnx.VadModelConfig()
    vcfg.silero_vad.model = str(VAD_PATH)
    vcfg.silero_vad.min_silence_duration = 0.25
    vcfg.silero_vad.min_speech_duration = 0.15
    vcfg.silero_vad.max_speech_duration = 20
    vcfg.sample_rate = SR
    vad = sherpa_onnx.VoiceActivityDetector(vcfg, buffer_size_in_seconds=120)
    win = vcfg.silero_vad.window_size
    chunks = []
    for i in range(0, len(audio), win):
        vad.accept_waveform(audio[i:i + win])
        while not vad.empty():
            chunks.append((vad.front.start, np.array(vad.front.samples, dtype=np.float32)))
            vad.pop()
    vad.flush()
    while not vad.empty():
        chunks.append((vad.front.start, np.array(vad.front.samples, dtype=np.float32)))
        vad.pop()

    words: list[dict] = []
    for si, (start_sample, samples) in enumerate(chunks):
        off = start_sample / SR
        chunk_end = off + len(samples) / SR
        s = rec.create_stream()
        s.accept_waveform(SR, samples)
        rec.decode_stream(s)
        r = s.result
        cur = None
        seg_words = []
        for tok, ts in zip(r.tokens, r.timestamps):
            t = off + float(ts)
            if tok.strip() in {".", ",", "?", "!"} and cur:
                cur["text"] += tok.strip()
                cur["last"] = t
                continue
            if tok.startswith(" ") or cur is None:
                if cur:
                    seg_words.append(cur)
                cur = {"text": tok.strip(), "start": t, "last": t}
            else:
                cur["text"] += tok
                cur["last"] = t
        if cur:
            seg_words.append(cur)
        for i, w in enumerate(seg_words):
            nxt = seg_words[i + 1]["start"] if i + 1 < len(seg_words) else chunk_end
            end = min(nxt, w["last"] + 0.32)
            words.append({"text": w["text"], "start": round(max(off, w["start"] - 0.04), 3),
                          "end": round(max(end, w["start"] + 0.08), 3), "seg": si, "conf": None})
    return words


def transcribe(video: Path, work: Path, rules: dict) -> Path:
    wav = work / "audio16k.wav"
    extract_audio(video, wav)
    engine = rules["transcribe"].get("engine", "auto")
    words = None
    used = None
    if engine in ("auto", "whisper"):
        try:
            log("받아쓰기: faster-whisper 사용 시도")
            words = _whisper(wav, rules)
            used = "faster-whisper"
        except Exception as e:  # 모델 다운로드 불가 등
            if engine == "whisper":
                raise
            log(f"faster-whisper 사용 불가({type(e).__name__}) → 오프라인 한국어 모델로 전환")
    if words is None:
        words = _sherpa(wav)
        used = "sherpa-onnx-korean"
    out = work / "transcript.json"
    write_json(out, {"engine": used, "words": words})
    log(f"받아쓰기 완료: 단어 {len(words)}개 ({used}) → {out}")
    return out
