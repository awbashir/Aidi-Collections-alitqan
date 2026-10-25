"""Message-campaign audio (customer request 2026-10-25, migration 0088).

An upload (WAV, or MP3 / M4A / OGG when `ffmpeg` is installed on the server) becomes the one format
Asterisk 11 plays without transcoding surprises: RIFF WAVE, PCM 16-bit, mono, 8000 Hz. WAV is read
with the standard library (`wave` + `audioop`: to 16-bit, to mono, resampled); other formats go
through ffmpeg (no shell, bounded time). At most MAX_INPUT_BYTES in, MAX_SECONDS long.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import warnings
import wave

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    import audioop  # noqa: E402 - Python 3.11 standard library (deprecated, still shipped)

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_SECONDS = 120
RATE = 8000
FFMPEG_TIMEOUT = 60


class AudioError(ValueError):
    """`code` is a fixed reason for the API (invalid_audio, too_long, too_large, unsupported_format)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _from_wav(data: bytes) -> tuple[bytes, float]:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if frames <= 0 or rate <= 0 or width not in (1, 2, 3, 4) or channels not in (1, 2):
                raise AudioError("invalid_audio", "the WAV file has no usable PCM audio")
            if frames / rate > MAX_SECONDS:
                raise AudioError("too_long", f"the message must be at most {MAX_SECONDS} seconds")
            pcm = w.readframes(frames)
    except (wave.Error, EOFError):
        raise AudioError("invalid_audio", "not a PCM WAV file") from None
    if width == 1:                                   # 8-bit WAV is unsigned
        pcm = audioop.bias(pcm, 1, -128)
    if width != 2:
        pcm = audioop.lin2lin(pcm, width, 2)
    if channels == 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    if rate != RATE:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, RATE, None)
    seconds = len(pcm) / 2 / RATE
    if seconds <= 0.2:
        raise AudioError("invalid_audio", "the message is empty")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return out.getvalue(), round(seconds, 1)


def _with_ffmpeg(data: bytes) -> bytes:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise AudioError("unsupported_format", "upload a WAV file (MP3 needs ffmpeg on the server)")
    with tempfile.TemporaryDirectory(prefix="cawp-audio-") as d:
        src, dst = f"{d}/in", f"{d}/out.wav"
        with open(src, "wb") as f:
            f.write(data)
        try:
            subprocess.run([exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-t",
                            str(MAX_SECONDS + 1), "-ac", "1", "-ar", str(RATE), "-acodec", "pcm_s16le", dst],
                           check=True, timeout=FFMPEG_TIMEOUT, capture_output=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise AudioError("invalid_audio", "the audio file could not be read") from None
        with open(dst, "rb") as f:
            return f.read()


def convert(data: bytes, filename: str | None = None) -> tuple[bytes, float]:
    """-> (8 kHz mono 16-bit WAV bytes, duration seconds). Raises AudioError."""
    if not data:
        raise AudioError("invalid_audio", "the file is empty")
    if len(data) > MAX_INPUT_BYTES:
        raise AudioError("too_large", f"the file must be at most {MAX_INPUT_BYTES // (1024 * 1024)} MB")
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        try:
            return _from_wav(data)
        except AudioError as e:
            if e.code != "invalid_audio":
                raise
            # e.g. a compressed (non-PCM) WAV: let ffmpeg try when it is there
            if shutil.which("ffmpeg") is None:
                raise
    return _from_wav(_with_ffmpeg(data))
