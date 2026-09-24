"""Phase 6 Q2: QC audio inspection, private storage and byte ranges (review/phase6-quality.md).

Inspection never decodes audio; it reads container and frame headers only:

* **WAV**: a complete RIFF/WAVE file (the RIFF size matches the file), PCM (format 1, or
  WAVE_FORMAT_EXTENSIBLE with the PCM sub-format), mono or stereo, 8/16/24/32 bits,
  8-96 kHz, a consistent block align and byte rate, whole sample frames. Duration =
  data bytes / byte rate.
* **MP3**: MPEG-1/2/2.5 Layer III. An optional ID3v2 tag at the start, then contiguous frames
  with one sample rate and one channel mode up to the end (an ID3v1 tag, or a truncated last
  frame, may follow). Duration = frames x samples per frame / sample rate.

Limits (the rebuild's): at most 24 MB and 30 minutes per file. Anything else is refused with a
stable code (`audio_too_large` 413, `unsupported_audio` 415, `invalid_audio` 422,
`audio_too_long` 422).

Storage: `QUALITY_STORAGE_DIR` (default `<UPLOAD_DIR>/private/quality`, read at call time),
directories 0700, files 0600:

    audio/<yyyy>/<mm>/<recording id>.<wav|mp3>
    tmp/                                      in-flight imports

Only relative paths are stored; `resolve()` refuses anything that escapes the root. Nothing is
served statically: playback goes through the API with byte ranges and `no-store`.
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import struct
import uuid

from core.api_response import ApiError

MAX_BYTES = 24_000_000
MAX_SECONDS = 1800
MIN_RATE, MAX_RATE = 8000, 96000
CONTENT_TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg"}


@dataclasses.dataclass(frozen=True)
class AudioInfo:
    format: str
    content_type: str
    duration_seconds: float
    channels: int | None
    sample_rate: int | None
    bits_per_sample: int | None
    bitrate_kbps: int | None
    size_bytes: int

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _invalid(reason: str, message: str = "a complete PCM WAV or MP3 recording is required"):
    return ApiError(422, "invalid_audio", message, {"reason": reason})


# ------------------------------------------------------------------------------ WAV
_PCM_GUID_TAIL = b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"


def inspect_wav(data: bytes) -> AudioInfo:
    n = len(data)
    if n < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise _invalid("not_riff_wave")
    if struct.unpack_from("<I", data, 4)[0] + 8 != n:
        raise _invalid("riff_size_mismatch", "the WAV file is incomplete or has trailing data")
    fmt = None
    data_size = None
    p = 12
    while p + 8 <= n:
        cid = data[p:p + 4]
        size = struct.unpack_from("<I", data, p + 4)[0]
        start = p + 8
        if start + size > n:
            raise _invalid("truncated_chunk", "the WAV file has a truncated chunk")
        if cid == b"fmt ":
            if size < 16:
                raise _invalid("invalid_format_chunk")
            tag, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", data, start)
            if tag == 0xFFFE:
                if size < 40 or data[start + 24:start + 26] != b"\x01\x00" or \
                        data[start + 26:start + 40] != _PCM_GUID_TAIL:
                    raise _invalid("not_pcm", "only PCM WAV is supported")
                tag = 1
            fmt = (tag, channels, rate, byte_rate, align, bits)
        elif cid == b"data":
            if data_size is not None:
                raise _invalid("several_data_chunks")
            data_size = size
        p = start + size + (size % 2)
    if fmt is None or data_size is None:
        raise _invalid("missing_chunk", "the WAV file has no format or data chunk")
    tag, channels, rate, byte_rate, align, bits = fmt
    if tag != 1:
        raise _invalid("not_pcm", "only PCM WAV is supported")
    if channels not in (1, 2) or bits not in (8, 16, 24, 32) or not (MIN_RATE <= rate <= MAX_RATE):
        raise _invalid("unsupported_parameters", "supported audio: mono/stereo PCM WAV, 8-96 kHz")
    if align != channels * bits // 8 or byte_rate != rate * align or data_size == 0 or data_size % align:
        raise _invalid("inconsistent_format", "the WAV format fields are inconsistent")
    duration = data_size / byte_rate
    if duration > MAX_SECONDS:
        raise ApiError(422, "audio_too_long", "recordings must be no longer than 30 minutes",
                       {"duration_seconds": round(duration, 3), "max_seconds": MAX_SECONDS})
    return AudioInfo("wav", CONTENT_TYPES["wav"], round(duration, 3), channels, rate, bits,
                     round(byte_rate * 8 / 1000), n)


# ------------------------------------------------------------------------------ MP3
_MP3_BITRATES = {1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),     # MPEG-1 L3
                 2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)}         # MPEG-2/2.5 L3
_MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _mp3_frame(data: bytes, p: int):
    """(frame length, sample rate, samples, channel mode) of the Layer III frame at p, or None."""
    if p + 4 > len(data):
        return None
    b1, b2, b3 = data[p + 1], data[p + 2], data[p + 3]
    if data[p] != 0xFF or (b1 & 0xE0) != 0xE0:
        return None
    version = (b1 >> 3) & 0x03             # 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5, 1 = reserved
    layer = (b1 >> 1) & 0x03               # 1 = Layer III
    if version == 1 or layer != 1:
        return None
    br_index, sr_index, padding = (b2 >> 4) & 0x0F, (b2 >> 2) & 0x03, (b2 >> 1) & 0x01
    if br_index in (0, 15) or sr_index == 3:
        return None
    mpeg1 = version == 3
    bitrate = _MP3_BITRATES[1 if mpeg1 else 2][br_index] * 1000
    rate = _MP3_RATES[version][sr_index]
    length = (144 if mpeg1 else 72) * bitrate // rate + padding
    mode = (b3 >> 6) & 0x03
    return length, rate, (1152 if mpeg1 else 576), mode


def inspect_mp3(data: bytes) -> AudioInfo:
    n = len(data)
    p = 0
    if data[:3] == b"ID3":
        if n < 10 or any(b & 0x80 for b in data[6:10]):
            raise _invalid("invalid_id3", "the MP3 tag header is invalid")
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        p = 10 + size + (10 if data[5] & 0x10 else 0)
    end = n - 128 if n >= 128 and data[n - 128:n - 125] == b"TAG" else n
    frames, samples, rate0, mode0, frame_bytes = 0, 0, None, None, 0
    while p < end:
        f = _mp3_frame(data, p)
        if f is None:
            raise _invalid("invalid_mp3_frame", "the MP3 stream is not a sequence of Layer III frames")
        length, rate, spf, mode = f
        if rate0 is None:
            rate0, mode0 = rate, mode
        elif rate != rate0 or mode != mode0:
            raise _invalid("inconsistent_mp3", "the MP3 frames change sample rate or channel mode")
        if p + length > end:
            if frames == 0:
                raise _invalid("truncated_mp3")
            break                          # a truncated last frame: ignored
        frames += 1
        samples += spf
        frame_bytes += length
        p += length
    if frames == 0 or rate0 is None:
        raise _invalid("no_mp3_frames", "the MP3 file has no audio frames")
    duration = samples / rate0
    if duration > MAX_SECONDS:
        raise ApiError(422, "audio_too_long", "recordings must be no longer than 30 minutes",
                       {"duration_seconds": round(duration, 3), "max_seconds": MAX_SECONDS})
    return AudioInfo("mp3", CONTENT_TYPES["mp3"], round(duration, 3), 1 if mode0 == 3 else 2, rate0, None,
                     round(frame_bytes * 8 / duration / 1000), n)


def inspect(data: bytes) -> AudioInfo:
    """Inspect the whole file (bytes). Raises ApiError with a stable code."""
    if not data:
        raise ApiError(422, "empty_file", "the file is empty")
    if len(data) > MAX_BYTES:
        raise ApiError(413, "audio_too_large", f"the recording is larger than {MAX_BYTES} bytes",
                       {"max_bytes": MAX_BYTES})
    if data[:4] == b"RIFF":
        return inspect_wav(data)
    if data[:3] == b"ID3" or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return inspect_mp3(data)
    raise ApiError(415, "unsupported_audio", "only PCM WAV and MP3 recordings are supported")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------------------ storage
def root() -> str:
    configured = (os.getenv("QUALITY_STORAGE_DIR") or "").strip()
    if configured:
        return os.path.abspath(configured)
    from core.config import Config
    return os.path.abspath(os.path.join(Config.UPLOAD_DIR, "private", "quality"))


def _ensure(path: str) -> str:
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def resolve(relative: str) -> str:
    if not relative or os.path.isabs(relative) or "\x00" in relative or ".." in relative.split(os.sep):
        raise ValueError("invalid stored path")
    base = os.path.realpath(_ensure(root()))
    path = os.path.realpath(os.path.join(base, relative))
    if not path.startswith(base + os.sep):
        raise ValueError("stored path escapes the quality storage root")
    return path


def audio_relative(recording_id: str, ext: str, when) -> str:
    return os.path.join("audio", f"{when:%Y}", f"{when:%m}", f"{recording_id}.{ext}")


def receive(stream, max_bytes: int = MAX_BYTES) -> bytes:
    """Read an upload stream fully, refusing more than max_bytes (never buffers past the limit)."""
    chunks, size = [], 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise ApiError(413, "audio_too_large", f"the recording is larger than {max_bytes} bytes",
                           {"max_bytes": max_bytes})
        chunks.append(chunk)
    return b"".join(chunks)


def write_private(relative: str, data: bytes) -> str:
    """Write through a temp file, then move into place with mode 0600."""
    dest = resolve(relative)
    _ensure(os.path.dirname(dest))
    tmp = os.path.join(_ensure(os.path.join(root(), "tmp")), f"{uuid.uuid4().hex}.part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dest)
        os.chmod(dest, 0o600)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return dest


def delete(relative: str | None) -> bool:
    """Remove a stored file; True when it is gone (already absent included)."""
    if not relative:
        return True
    try:
        os.remove(resolve(relative))
    except FileNotFoundError:
        return True
    return True


def exists(relative: str | None) -> bool:
    if not relative:
        return False
    try:
        return os.path.isfile(resolve(relative))
    except ValueError:
        return False


def read_all(relative: str) -> bytes:
    with open(resolve(relative), "rb") as fh:
        return fh.read()


# ------------------------------------------------------------------------------ byte ranges
_RANGE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$")


def parse_range(header: str | None, size: int):
    """None (whole file), (start, end) inclusive, or "unsatisfiable". One range only; a
    multi-range or malformed header is ignored (the whole file is served, RFC 9110 allows it)."""
    if not header:
        return None
    m = _RANGE.match(header)
    if not m or "," in header:
        return None
    first, last = m.group(1), m.group(2)
    if first == "" and last == "":
        return None
    if first == "":                       # suffix: the last N bytes
        n = int(last)
        if n == 0:
            return "unsatisfiable"
        return max(size - n, 0), size - 1
    start = int(first)
    if start >= size:
        return "unsatisfiable"
    end = size - 1 if last == "" else min(int(last), size - 1)
    if end < start:
        return None
    return start, end


def iter_file(relative: str, start: int, length: int, chunk: int = 64 * 1024):
    path = resolve(relative)

    def gen():
        with open(path, "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                buf = fh.read(min(chunk, left))
                if not buf:
                    break
                left -= len(buf)
                yield buf
    return gen()
