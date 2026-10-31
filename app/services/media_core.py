"""Local media -> timestamped speech/visible text, with evidence-preserving exports.

No URLs are downloaded. External speech recognition is disabled until its endpoint,
model, credential and data-policy acknowledgement are configured by the operator.
Video support here means sampled-frame OCR, not object/action understanding.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class MediaError(RuntimeError):
    """A stable code plus a safe message; never include provider bodies or secrets."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise MediaError("INVALID_CONFIG", f"{name} 설정에는 정수를 입력해야 합니다.") from exc
    if not low <= value <= high:
        raise MediaError("INVALID_CONFIG", f"{name} 설정값이 허용 범위를 벗어났습니다.")
    return value


@dataclass(frozen=True)
class MediaConfig:
    asr_endpoint: str = ""
    asr_model: str = ""
    asr_api_key: str = field(default="", repr=False)
    data_policy_ack: bool = False
    response_format: str = "verbose_json"
    max_upload_bytes: int = 128 * 1024 * 1024
    max_duration_seconds: int = 7200
    chunk_seconds: int = 120
    max_ocr_frames: int = 24
    ocr_languages: str = "kor+eng"
    process_timeout_seconds: int = 1800

    def __post_init__(self) -> None:
        bounds = {
            "max_upload_bytes": (1, 512 * 1024 * 1024),
            "max_duration_seconds": (1, 14400),
            "chunk_seconds": (10, 600),
            "max_ocr_frames": (1, 120),
            "process_timeout_seconds": (30, 7200),
        }
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise MediaError("INVALID_CONFIG", f"{name} 설정값이 허용 범위를 벗어났습니다.")
        if self.response_format not in {"json", "verbose_json"}:
            raise MediaError("INVALID_CONFIG", "음성인식 응답 형식은 json 또는 verbose_json이어야 합니다.")
        if not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", self.ocr_languages):
            raise MediaError("INVALID_CONFIG", "화면 글자 인식 언어 설정이 올바르지 않습니다.")
        if self.asr_endpoint:
            parsed = urlsplit(self.asr_endpoint)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
                raise MediaError("INVALID_CONFIG", "음성인식 주소에는 인증정보·질의문이 없는 HTTPS 주소를 지정해야 합니다.")
        if any(c in self.asr_api_key for c in "\r\n"):
            raise MediaError("INVALID_CONFIG", "음성인식 인증키에 줄바꿈이 포함돼 있습니다.")
        if any(c in self.asr_model for c in "\r\n"):
            raise MediaError("INVALID_CONFIG", "음성인식 모델 이름에 줄바꿈이 포함돼 있습니다.")

    @classmethod
    def from_env(cls) -> "MediaConfig":
        return cls(
            asr_endpoint=os.environ.get("MEDIA_ASR_ENDPOINT", "").strip(),
            asr_model=os.environ.get("MEDIA_ASR_MODEL", "").strip(),
            asr_api_key=os.environ.get("MEDIA_ASR_API_KEY", ""),
            data_policy_ack=os.environ.get("MEDIA_ASR_DATA_POLICY_ACK", "").lower() == "true",
            response_format=os.environ.get("MEDIA_ASR_RESPONSE_FORMAT", "verbose_json"),
            max_upload_bytes=_env_int("MEDIA_MAX_UPLOAD_MB", 128, 1, 512) * 1024 * 1024,
            max_duration_seconds=_env_int("MEDIA_MAX_DURATION_SECONDS", 7200, 1, 14400),
            chunk_seconds=_env_int("MEDIA_CHUNK_SECONDS", 120, 10, 600),
            max_ocr_frames=_env_int("MEDIA_MAX_OCR_FRAMES", 24, 1, 120),
            ocr_languages=os.environ.get("MEDIA_OCR_LANGUAGES", "kor+eng"),
            process_timeout_seconds=_env_int("MEDIA_PROCESS_TIMEOUT_SECONDS", 1800, 30, 7200),
        )

    @property
    def asr_ready(self) -> bool:
        return bool(self.asr_endpoint and self.asr_model and self.asr_api_key and self.data_policy_ack)


_FORMATS = "mov,matroska,mp3,wav,flac,ogg,aac"


def _run(args: list[str], timeout: float) -> bytes:
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=max(0.1, timeout), check=False)
    except FileNotFoundError as exc:
        raise MediaError("TOOL_MISSING", "음성·영상 처리 도구가 서버에 설치돼 있지 않습니다.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("PROCESS_TIMEOUT", "음성·영상 처리 제한시간을 초과했습니다.") from exc
    if result.returncode:
        raise MediaError("MEDIA_DECODE_FAILED", "파일을 읽거나 변환하지 못했습니다. 지원 형식과 파일 손상 여부를 확인해야 합니다.")
    return result.stdout


def probe_media(path: Path, config: MediaConfig) -> dict:
    """Inspect a regular local file, allowing only bounded conventional containers."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise MediaError("INVALID_FILE", "일반 음성·동영상 파일을 지정해야 합니다.")
    if not 0 < path.stat().st_size <= config.max_upload_bytes:
        raise MediaError("UPLOAD_TOO_LARGE", "파일이 비어 있거나 허용 크기를 초과했습니다.")
    payload = _run([
        "ffprobe", "-v", "error", "-max_alloc", "67108864",
        "-protocol_whitelist", "file,pipe", "-format_whitelist", _FORMATS,
        "-show_entries", "format=duration,format_name:stream=codec_type,width,height,duration",
        "-of", "json", str(path.resolve()),
    ], 20)
    try:
        data = json.loads(payload)
        streams = data.get("streams", [])
        candidates = [data.get("format", {}).get("duration")] + [s.get("duration") for s in streams]
        durations = [float(x) for x in candidates if x not in (None, "N/A", "")]
        duration = max(durations)
    except (ValueError, TypeError, AttributeError) as exc:
        raise MediaError("INVALID_DURATION", "파일 재생시간을 확인하지 못했습니다.") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise MediaError("INVALID_DURATION", "파일 재생시간이 올바르지 않습니다.")
    if duration > config.max_duration_seconds:
        raise MediaError("DURATION_TOO_LONG", "파일 재생시간이 허용 길이를 초과했습니다.")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    has_video = any(s.get("codec_type") == "video" for s in streams)
    if not has_audio and not has_video:
        raise MediaError("NO_MEDIA_STREAM", "파일에서 음성 또는 동영상 내용을 찾지 못했습니다.")
    for stream in streams:
        if stream.get("codec_type") == "video":
            width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
            if width <= 0 or height <= 0 or width * height > 3840 * 2160:
                raise MediaError("VIDEO_RESOLUTION_TOO_LARGE", "동영상 해상도는 3840×2160 이하이어야 합니다.")
    return {"duration_seconds": duration, "has_audio": has_audio, "has_video": has_video,
            "format": data.get("format", {}).get("format_name", "unknown")}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_asr(wav_path: Path, language: str, config: MediaConfig, *, timeout_seconds: float = 300) -> dict:
    """Send one bounded PCM chunk; redirects and non-transient retries are disabled."""
    if not config.asr_ready:
        raise MediaError("ASR_NOT_CONFIGURED", "음성인식 주소·모델·인증키와 데이터 처리정책 확인 설정이 필요합니다.")
    boundary = "edurecover-" + uuid.uuid4().hex
    parts: list[bytes] = []
    fields = {"model": config.asr_model, "response_format": config.response_format}
    if language != "auto":
        fields["language"] = language
    for name, value in fields.items():
        parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode())
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\nContent-Type: audio/wav\r\n\r\n").encode())
    parts.extend([wav_path.read_bytes(), f"\r\n--{boundary}--\r\n".encode()])
    request = Request(config.asr_endpoint, data=b"".join(parts), method="POST", headers={
        "Authorization": "Bearer " + config.asr_api_key,
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "Accept": "application/json",
    })
    opener = build_opener(_NoRedirect())
    deadline = time.monotonic() + timeout_seconds
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MediaError("PROCESS_TIMEOUT", "음성인식 처리 제한시간을 초과했습니다.")
        try:
            with opener.open(request, timeout=min(90, remaining)) as response:
                payload = response.read(2 * 1024 * 1024 + 1)
            if len(payload) > 2 * 1024 * 1024:
                raise MediaError("ASR_RESPONSE_TOO_LARGE", "음성인식 응답이 허용 크기를 초과했습니다.")
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return data
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if status in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(min(2 ** attempt, max(0, deadline - time.monotonic())))
                continue
            code = "ASR_AUTH_FAILED" if status in {401, 403} else "ASR_RATE_LIMIT" if status == 429 else "ASR_HTTP_FAILED"
            raise MediaError(code, f"음성인식 제공자가 요청을 처리하지 못했습니다(응답번호 {status}).") from None
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 2:
                time.sleep(min(2 ** attempt, max(0, deadline - time.monotonic())))
                continue
            raise MediaError("ASR_NETWORK_FAILED", "음성인식 제공자에 연결하지 못했습니다.") from None
        except (ValueError, UnicodeDecodeError) as exc:
            raise MediaError("ASR_INVALID_RESPONSE", "음성인식 응답을 읽지 못했습니다.") from None
    raise MediaError("ASR_NETWORK_FAILED", "음성인식 제공자에 연결하지 못했습니다.")


def _normalize_segments(data: dict, offset: float, length: float) -> list[dict]:
    segments: list[dict] = []
    raw_segments = data.get("segments")
    if isinstance(raw_segments, list) and raw_segments:
        for item in raw_segments:
            try:
                text = item["text"]
                start, end = float(item["start"]), float(item["end"])
                if not isinstance(text, str) or not math.isfinite(start) or not math.isfinite(end):
                    raise ValueError("invalid segment")
                if start < 0 or end < start or start > length or end > length + 2:
                    raise ValueError("invalid timestamp")
            except (KeyError, TypeError, ValueError):
                raise MediaError("ASR_INVALID_RESPONSE", "음성인식 응답의 문장 또는 시간 정보가 올바르지 않습니다.") from None
            if text.strip() and end > start:
                segments.append({"start": round(offset + start, 3), "end": round(offset + min(end, length), 3),
                                 "text": text.strip(), "source": "asr", "timing": "provider"})
    else:
        text = data.get("text", "")
        if not isinstance(text, str):
            raise MediaError("ASR_INVALID_RESPONSE", "음성인식 응답의 본문이 올바르지 않습니다.")
        if text.strip():
            segments.append({"start": round(offset, 3), "end": round(offset + length, 3),
                             "text": text.strip(), "source": "asr", "timing": "chunk"})
    return sorted(segments, key=lambda item: (item["start"], item["end"]))


def _ocr_frames(path: Path, work: Path, duration: float, config: MediaConfig,
                remaining: Callable[[], float], progress: Callable[[str, int, int], None]) -> tuple[list[dict], list[str]]:
    if not shutil.which("tesseract"):
        return [], ["화면 글자 인식 도구가 없어 영상 화면을 읽지 못했습니다."]
    available = _run(["tesseract", "--list-langs"], min(10, remaining())).decode("utf-8", "replace").splitlines()
    languages = config.ocr_languages.split("+")
    missing = [lang for lang in languages if lang not in available]
    if missing:
        return [], ["화면 글자 인식에 필요한 언어 자료가 없습니다: " + ", ".join(missing)]
    count = min(config.max_ocr_frames, max(1, math.ceil(duration / 30)))
    frames: list[dict] = []
    for index in range(count):
        # Sample each equal-length interval at its midpoint. OCR time denotes the
        # sampled instant only, not proof that text persisted across the interval.
        timestamp = duration * (index + 0.5) / count
        frame = work / "frame.png"
        _run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-max_alloc", "67108864",
              "-protocol_whitelist", "file,pipe", "-format_whitelist", _FORMATS,
              "-ss", f"{timestamp:.3f}", "-i", str(path), "-map", "0:v:0",
              "-frames:v", "1", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease",
              "-threads", "1", str(frame)], min(30, remaining()))
        text = _run(["tesseract", str(frame), "stdout", "-l", config.ocr_languages,
                     "--psm", "11"], min(30, remaining())).decode("utf-8", "replace").strip()
        if sum(len(item["text"]) for item in frames) + len(text) > 2_000_000:
            return frames, ["화면 글자 분량이 한 작업의 처리 한도를 넘어 이후 화면 인식을 중단했습니다."]
        if text:
            frames.append({"start": round(timestamp, 3), "end": round(timestamp, 3),
                           "text": text, "source": "ocr", "timing": "sampled_instant"})
        frame.unlink(missing_ok=True)
        progress("ocr", index + 1, count)
    return frames, []


def analyze_evidence(segments: list[dict], visual_text: list[dict], purpose: str) -> dict:
    """Extract source quotes; do not invent summaries, factual verification or lessons."""
    entries = segments + visual_text
    words = Counter(word.lower() for entry in entries for word in re.findall(r"[가-힣A-Za-z]{2,}", entry["text"]))
    stop = {"그리고", "하지만", "그래서", "이것은", "있습니다", "합니다", "대한", "the", "and", "this", "that", "with", "from"}
    keywords = [{"word": word, "count": count} for word, count in words.most_common() if word not in stop][:15]
    vocabulary = {word["word"] for word in keywords}
    ranked = sorted(enumerate(entries), key=lambda pair: (-sum(w.lower() in vocabulary for w in re.findall(r"[가-힣A-Za-z]{2,}", pair[1]["text"])), pair[0]))
    quotes = [{"start": entry["start"], "end": entry["end"], "quote": entry["text"][:1000],
               "source": entry["source"]} for _, entry in ranked[:8]]
    quotes.sort(key=lambda item: item["start"])
    numbers = []
    for entry in entries:
        if re.search(r"\d", entry["text"]):
            numbers.append({"start": entry["start"], "quote": entry["text"][:1000], "source": entry["source"]})
    return {"method": "extractive_frequency", "purpose": purpose, "keywords": keywords,
            "evidence_quotes": quotes, "number_mentions": numbers[:20],
            "review_note": ("뉴스의 주장을 원문에서 뽑았습니다. 사실 여부와 다른 출처의 확인은 별도로 해야 합니다."
                            if purpose == "news" else "강의에서 반복된 단어와 원문 구절을 뽑았습니다. 개념의 정확성은 별도로 확인해야 합니다."),
            "visual_scope": "일정 간격으로 뽑은 화면의 글자만 읽었습니다. 사람·물체·행동이나 모든 화면 변화를 인식한 결과가 아닙니다."}


def _subtitle_time(seconds: float, srt: bool = False) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hour, rest = divmod(milliseconds, 3600000)
    minute, rest = divmod(rest, 60000)
    second, ms = divmod(rest, 1000)
    return f"{hour:02}:{minute:02}:{second:02}{',' if srt else '.'}{ms:03}"


def render_subtitles(segments: list[dict], *, srt: bool = False) -> str:
    parts = [] if srt else ["WEBVTT\n"]
    for index, entry in enumerate(segments, 1):
        # Escape markup and strip structural separators so source text cannot
        # add active WebVTT tags, fake cues or cue settings.
        text = re.sub(r"\s+", " ", entry["text"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parts.append(f"{index}\n{_subtitle_time(entry['start'], srt)} --> {_subtitle_time(entry['end'], srt)}\n{text}\n")
    return "\n".join(parts) + "\n"


def process_media(path: Path, output_dir: Path, *, purpose: str = "lecture", language: str = "ko",
                  include_ocr: bool = True, progress: Callable[[str, int, int], None] | None = None,
                  config: MediaConfig | None = None) -> dict:
    """Process one uploaded file synchronously inside an isolated job directory.

    Output status is partial when speech or requested OCR could not be completed.
    Caller owns authentication, job scheduling, source deletion and export retention.
    """
    config = config or MediaConfig.from_env()
    if purpose not in {"news", "lecture"} or (language != "auto" and not re.fullmatch(r"[a-z]{2,3}(?:-[A-Z]{2})?", language)):
        raise MediaError("INVALID_OPTIONS", "분석 목적 또는 음성 언어 설정이 올바르지 않습니다.")
    path, output_dir = Path(path), Path(output_dir)
    info = probe_media(path, config)
    path = path.resolve()
    duration = info["duration_seconds"]
    deadline = time.monotonic() + config.process_timeout_seconds
    callback = progress or (lambda stage, current, total: None)

    def remaining() -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise MediaError("PROCESS_TIMEOUT", "전체 처리 제한시간을 초과했습니다.")
        return value

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    output_dir.mkdir(parents=True, exist_ok=True)
    segments: list[dict] = []
    visual_text: list[dict] = []
    warnings: list[str] = []
    asr_error: MediaError | None = None
    asr_status = "not_applicable" if not info["has_audio"] else "not_configured"
    ocr_status = "not_requested" if not include_ocr else "not_applicable" if not info["has_video"] else "pending"
    with tempfile.TemporaryDirectory(prefix="media-work-", dir=output_dir) as work_name:
        work = Path(work_name)
        if info["has_audio"]:
            if not config.asr_ready:
                warnings.append("음성인식 주소·모델·인증키와 데이터 처리정책 확인 설정이 없어 음성을 전사하지 못했습니다.")
            else:
                count = math.ceil(duration / config.chunk_seconds)
                asr_status = "complete"
                for index in range(count):
                    offset = index * config.chunk_seconds
                    length = min(config.chunk_seconds, duration - offset)
                    chunk = work / "chunk.wav"
                    _run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-max_alloc", "67108864",
                          "-protocol_whitelist", "file,pipe", "-format_whitelist", _FORMATS,
                          "-ss", f"{offset:.3f}", "-i", str(path), "-t", f"{length:.3f}",
                          "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                          "-c:a", "pcm_s16le", "-threads", "1", str(chunk)], min(180, remaining()))
                    remaining()
                    try:
                        response = _request_asr(chunk, language, config, timeout_seconds=remaining())
                        remaining()
                        new_segments = _normalize_segments(response, offset, length)
                        if len(segments) + len(new_segments) > 20_000 or sum(len(item["text"]) for item in segments + new_segments) > 2_000_000:
                            raise MediaError("TRANSCRIPT_TOO_LARGE", "전사문 분량이 한 작업의 처리 한도를 초과했습니다.")
                        segments.extend(new_segments)
                    except MediaError as exc:
                        asr_error = exc
                        asr_status = "partial" if segments else "failed"
                        warnings.append(f"음성 전사가 {offset:.0f}초 지점에서 중단됐습니다: {exc.message}")
                        break
                    finally:
                        chunk.unlink(missing_ok=True)
                    callback("asr", index + 1, count)
        if include_ocr and info["has_video"]:
            try:
                visual_text, ocr_warnings = _ocr_frames(path, work, duration, config, remaining, callback)
                warnings.extend(ocr_warnings)
                ocr_status = "unavailable" if ocr_warnings else "complete"
            except MediaError as exc:
                ocr_status = "failed"
                warnings.append("영상 화면 글자를 읽지 못했습니다: " + exc.message)
    if not segments and not visual_text:
        if asr_error is not None:
            raise asr_error
        if asr_status == "not_configured":
            raise MediaError("ASR_NOT_CONFIGURED", warnings[0])
        raise MediaError("NO_RECOGNIZED_TEXT", "인식한 음성·화면 글자가 없습니다. 무음·빈 화면 또는 인식 설정을 확인해야 합니다.")
    if any(segment["timing"] == "chunk" for segment in segments):
        warnings.append("문장별 시간정보가 없어 해당 음성 조각의 시작·끝 시간을 표시했습니다. 자막 시간이 정밀하지 않습니다.")
    analysis = analyze_evidence(segments, visual_text, purpose)
    result = {"schema_version": 1, "status": "partial" if warnings else "complete", **info,
              "source_sha256": digest.hexdigest(), "language": language,
              "asr_status": asr_status, "ocr_status": ocr_status,
              "segments": segments, "visual_text": visual_text,
              "analysis": analysis, "warnings": warnings,
              "provenance": {"asr_model": config.asr_model if segments else None,
                             "asr_host": urlsplit(config.asr_endpoint).hostname if segments else None,
                             "ocr_engine": "tesseract" if visual_text else None,
                             "ocr_languages": config.ocr_languages if visual_text else None,
                             "external_audio_transfer": bool(segments) or asr_status in {"complete", "partial", "failed"}},
              "exports": ["transcript.json", "transcript.txt", "transcript.srt", "transcript.vtt", "analysis.json"]}
    (output_dir / "transcript.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    lines = [f"[{_subtitle_time(e['start'])}] {e['text']}" for e in segments]
    lines.extend(f"[화면 {_subtitle_time(e['start'])}] {e['text']}" for e in visual_text)
    (output_dir / "transcript.txt").write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    (output_dir / "transcript.srt").write_text(render_subtitles(segments, srt=True), encoding="utf-8")
    (output_dir / "transcript.vtt").write_text(render_subtitles(segments), encoding="utf-8")
    (output_dir / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    callback("complete", 1, 1)
    return result
