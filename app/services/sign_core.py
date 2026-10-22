"""Sign-video adapters and limited-vocabulary hand-motion template matching.

Automatic results are unconfirmed. Hand landmarks omit face/body/linguistic
context and are never advertised as complete Korean Sign Language translation.
No model, provider, credential or downloadable asset is chosen automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid


class SignError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class SignConfig:
    endpoint: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)
    data_policy_ack: bool = False
    language: str = "ksl"
    max_upload_bytes: int = 32 * 1024 * 1024
    max_duration_seconds: int = 60
    timeout_seconds: int = 120

    def __post_init__(self):
        if self.endpoint:
            parsed = urlsplit(self.endpoint)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment):
                raise SignError("SIGN_CONFIG_INVALID", "수어 인식 주소에는 인증정보·질의문이 없는 HTTPS 주소를 지정하십시오.")
        if self.language != "ksl":
            raise SignError("SIGN_CONFIG_INVALID", "현재 수어 요청 언어는 한국수어(ksl)로 지정해야 합니다.")
        if not isinstance(self.api_key, str) or any(c in self.api_key for c in "\r\n") or any(c in self.model for c in "\r\n"):
            raise SignError("SIGN_CONFIG_INVALID", "수어 인증키·모델 이름에 줄바꿈을 넣을 수 없습니다.")
        for name, low, high in (("max_upload_bytes", 1, 32 * 1024 * 1024), ("max_duration_seconds", 1, 60), ("timeout_seconds", 1, 180)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise SignError("SIGN_CONFIG_INVALID", "수어 처리 한도 설정이 올바르지 않습니다.")

    @classmethod
    def from_env(cls):
        # Credentials remain as issued; no hex conversion or fixed key length.
        return cls(endpoint=os.getenv("SIGN_RECOGNITION_ENDPOINT", "").strip(),
                   model=os.getenv("SIGN_RECOGNITION_MODEL", "").strip(),
                   api_key=os.getenv("SIGN_RECOGNITION_API_KEY", ""),
                   data_policy_ack=os.getenv("SIGN_DATA_POLICY_ACK", "").lower() == "true")

    @property
    def ready(self):
        return bool(self.endpoint and self.model and self.api_key and self.data_policy_ack)


def _run(args, timeout):
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except FileNotFoundError:
        raise SignError("SIGN_TOOL_MISSING", "서버에 FFmpeg·FFprobe가 없습니다. 미디어 설치 설정을 적용하십시오.") from None
    except subprocess.TimeoutExpired:
        raise SignError("SIGN_PROCESS_TIMEOUT", "수어 영상 처리 제한시간을 초과했습니다.") from None
    if result.returncode:
        raise SignError("SIGN_VIDEO_INVALID", "수어 영상을 읽지 못했습니다. 파일 형식과 손상 여부를 확인하십시오.")
    return result.stdout


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe_sign_video(path: Path, config: SignConfig | None = None):
    config = config or SignConfig()
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= config.max_upload_bytes:
        raise SignError("SIGN_VIDEO_INVALID", "수어 영상은 32MiB 이하의 일반 파일이어야 합니다.")
    raw = _run(["ffprobe", "-v", "error", "-max_alloc", "67108864",
                "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,avi",
                "-select_streams", "v:0", "-show_entries", "format=duration:stream=width,height,duration",
                "-of", "json", str(path.resolve())], 20)
    try:
        data = json.loads(raw)
        stream = data["streams"][0]
        duration = float(data.get("format", {}).get("duration") or stream["duration"])
        width, height = int(stream["width"]), int(stream["height"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
    except (ValueError, TypeError, KeyError, IndexError):
        raise SignError("SIGN_VIDEO_INVALID", "수어 영상의 재생시간·화면 크기를 확인하지 못했습니다.") from None
    if duration > config.max_duration_seconds or not 0 < width * height <= 1920 * 1080:
        raise SignError("SIGN_VIDEO_LIMIT", "수어 영상은 60초·1920×1080 이하이어야 합니다.")
    return {"duration_seconds": duration, "width": width, "height": height,
            "source_video_sha256": _sha256(path)}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _provider_request(path, config, digest):
    boundary = "edurecover-sign-" + uuid.uuid4().hex
    parts = []
    for name, value in {"model": config.model, "language": config.language,
                        "source_video_sha256": digest, "response_format": "json"}.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    suffix = path.suffix.lower()
    mime = {".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo"}.get(suffix, "video/mp4")
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sign{suffix}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
    parts.extend([path.read_bytes(), f"\r\n--{boundary}--\r\n".encode()])
    request = Request(config.endpoint, data=b"".join(parts), method="POST", headers={
        "Authorization": "Bearer " + config.api_key,
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "Accept": "application/json"})
    try:
        # No automatic retry: a timeout does not prove a paid request was unused.
        with build_opener(_NoRedirect()).open(request, timeout=config.timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            payload = response.read(1024 * 1024 + 1)
        if content_type != "application/json" or len(payload) > 1024 * 1024:
            raise ValueError
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError
        return data
    except HTTPError as exc:
        code = exc.code
        exc.close()
        reason = "SIGN_AUTH_FAILED" if code in {401, 403} else "SIGN_RATE_LIMIT" if code == 429 else "SIGN_PROVIDER_HTTP_FAILED"
        raise SignError(reason, f"수어 인식 제공자가 요청을 처리하지 못했습니다(응답번호 {code}).") from None
    except (TimeoutError, URLError, OSError):
        raise SignError("SIGN_PROVIDER_UNREACHABLE", "수어 인식 제공자에 연결하지 못했습니다. 요청을 자동 재실행하지 않았습니다.") from None
    except (ValueError, UnicodeError):
        raise SignError("SIGN_RESPONSE_INVALID", "수어 인식 응답의 자료 형식이 올바르지 않습니다.") from None


def recognize_sign_video(path: Path, config: SignConfig | None = None):
    """Call an explicitly configured sign-specific provider using the stated contract.

    The provider must echo language=ksl and source_video_sha256, and return
    segments[{start,end,text,confidence}]. An ordinary text/ASR endpoint does not
    meet this contract. The caller obtains per-file disclosure consent.
    """
    config = config or SignConfig.from_env()
    if not config.ready:
        raise SignError("SIGN_NOT_CONFIGURED", "수어 전용 제공자의 주소·모델·인증키와 데이터 처리정책 확인 설정이 필요합니다.")
    path = Path(path)
    info = probe_sign_video(path, config)
    data = _provider_request(path, config, info["source_video_sha256"])
    if data.get("language") != config.language or data.get("source_video_sha256") != info["source_video_sha256"]:
        raise SignError("SIGN_RESPONSE_MISMATCH", "수어 인식 응답의 언어 또는 원본 식별값이 요청과 다릅니다.")
    if data.get("model", config.model) != config.model:
        raise SignError("SIGN_RESPONSE_MISMATCH", "수어 인식 응답의 모델 식별자가 요청과 다릅니다.")
    raw = data.get("segments")
    if not isinstance(raw, list) or len(raw) > 1000:
        raise SignError("SIGN_RESPONSE_INVALID", "수어 인식 응답의 구간 목록이 올바르지 않습니다.")
    segments, total, previous = [], 0, -1.0
    for item in raw:
        try:
            if any(isinstance(item[key], bool) for key in ("start", "end", "confidence")):
                raise ValueError
            start, end = float(item["start"]), float(item["end"])
            confidence, text = float(item["confidence"]), item["text"]
            if (not all(math.isfinite(x) for x in (start, end, confidence))
                    or not 0 <= start < end <= info["duration_seconds"] + 0.05
                    or start < previous or not 0 <= confidence <= 1
                    or not isinstance(text, str) or not text.strip() or len(text) > 5000):
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise SignError("SIGN_RESPONSE_INVALID", "수어 인식 구간의 시간·본문·확신도 값이 올바르지 않습니다.") from None
        total += len(text)
        if total > 100000:
            raise SignError("SIGN_RESPONSE_INVALID", "수어 인식 본문이 허용 분량을 넘었습니다.")
        previous = start
        segments.append({"start": start, "end": min(end, info["duration_seconds"]), "text": text,
                         "confidence": confidence, "source": "sign_provider",
                         "observation_state": "UNCONFIRMED", "expert_confirmed": False})
    return {"status": "UNCONFIRMED" if segments else "UNKNOWN", "method": "configured_sign_provider",
            "language": config.language, "segments": segments, **info,
            "observation_state": "UNCONFIRMED", "expert_confirmed": False,
            "provenance": {"model": config.model, "provider_host": urlsplit(config.endpoint).hostname,
                           "external_video_transfer": True},
            "warnings": ["자동 수어 인식 결과입니다. 한국수어 전문가가 확인하기 전에는 확정 번역으로 사용하지 마십시오."]}


def _normalize(sequence):
    if not isinstance(sequence, list) or not 3 <= len(sequence) <= 120:
        raise SignError("SIGN_LANDMARKS_INVALID", "손 좌표는 3~120개 화면의 목록이어야 합니다.")
    dim = len(sequence[0]) if isinstance(sequence[0], list) else 0
    if dim not in {63, 126}:
        raise SignError("SIGN_LANDMARKS_INVALID", "화면마다 한 손 63개 또는 좌우 두 손 126개 좌표값이 필요합니다.")
    frames, scales, origin = [], [], None
    for frame in sequence:
        if not isinstance(frame, list) or len(frame) != dim:
            raise SignError("SIGN_LANDMARKS_INVALID", "손 좌표의 화면별 길이가 다릅니다.")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 10000 for v in frame):
            raise SignError("SIGN_LANDMARKS_INVALID", "손 좌표에는 유한한 숫자만 사용할 수 있습니다.")
        hands = []
        for start in range(0, dim, 63):
            hand = [tuple(float(v) for v in frame[i:i+3]) for i in range(start, start+63, 3)]
            scale = math.dist(hand[0], hand[9])
            present = scale > 1e-8
            hands.append((hand, present))
            if present:
                scales.append(scale)
                if origin is None:
                    origin = hand[0]
        frames.append(hands)
    if origin is None:
        raise SignError("NO_HAND_LANDMARKS", "사용 가능한 손 좌표를 찾지 못했습니다.")
    scale = statistics.median(scales)
    vectors = []
    for hands in frames:
        vector = []
        for hand, present in hands:
            vector.extend((value-origin[axis])/scale if present else 0.0 for point in hand for axis, value in enumerate(point))
            vector.append(1.0 if present else 0.0)
        vectors.append(vector)
    # Bound matching cost without assuming frame rate or clip duration.
    count = min(60, len(vectors))
    output = []
    for index in range(count):
        position = index * (len(vectors)-1) / (count-1)
        lo, hi = math.floor(position), math.ceil(position)
        alpha = position-lo
        output.append([a+(b-a)*alpha for a, b in zip(vectors[lo], vectors[hi])])
    return output


def _dtw(left, right):
    if len(left[0]) != len(right[0]):
        return math.inf
    n, m = len(left), len(right)
    radius = max(abs(n-m), math.ceil(max(n, m)*0.3))
    previous = [(math.inf, 0)] * (m+1)
    previous[0] = (0.0, 0)
    for i in range(1, n+1):
        current = [(math.inf, 0)] * (m+1)
        for j in range(max(1, i-radius), min(m, i+radius)+1):
            distance = math.sqrt(sum((a-b)**2 for a, b in zip(left[i-1], right[j-1])) / len(left[i-1]))
            cost, steps = min(previous[j], current[j-1], previous[j-1], key=lambda pair: pair[0])
            current[j] = (cost+distance, steps+1)
        previous = current
    cost, steps = previous[m]
    return cost / steps if steps else math.inf


def classify_landmarks(sequence, templates, threshold=0.25, ambiguity_margin=0.04):
    """Match a previously registered hand-motion template, never open-ended KSL."""
    if (isinstance(threshold, bool) or not isinstance(threshold, (float, int)) or not math.isfinite(threshold)
            or not 0 < threshold <= 5 or not isinstance(ambiguity_margin, (float, int))
            or not math.isfinite(ambiguity_margin) or not 0 <= ambiguity_margin <= 1):
        raise SignError("SIGN_MATCH_SETTINGS_INVALID", "손동작 비교 기준값이 올바르지 않습니다.")
    observed = _normalize(sequence)
    if not isinstance(templates, list) or len(templates) > 20:
        raise SignError("SIGN_TEMPLATES_INVALID", "등록 손동작은 최대 20개입니다.")
    candidates, seen = [], set()
    for item in templates:
        if not isinstance(item, dict):
            raise SignError("SIGN_TEMPLATES_INVALID", "등록 손동작 자료 형식이 올바르지 않습니다.")
        identity, label = item.get("id"), item.get("label")
        if (not isinstance(identity, str) or not 0 < len(identity) <= 100 or identity in seen
                or not isinstance(label, str) or not label.strip() or len(label) > 200):
            raise SignError("SIGN_TEMPLATES_INVALID", "등록 손동작 식별자·표시 문구가 올바르지 않습니다.")
        seen.add(identity)
        sample = _normalize(item.get("sequence"))
        distance = _dtw(observed, sample)
        if math.isfinite(distance):
            candidates.append({"id": identity, "label": label, "distance": round(distance, 6)})
    candidates.sort(key=lambda item: item["distance"])
    best = candidates[0] if candidates else None
    status = "UNKNOWN"
    if best is not None and best["distance"] <= threshold:
        status = "AMBIGUOUS" if (len(candidates) > 1 and candidates[1]["label"] != best["label"]
                                 and candidates[1]["distance"]-best["distance"] < ambiguity_margin) else "MATCH_CANDIDATE"
    matched = best if status == "MATCH_CANDIDATE" else None
    return {"status": status, "label": matched["label"] if matched else None,
            "template_id": matched["id"] if matched else None, "candidates": candidates[:3],
            "distance": best["distance"] if best else None, "confidence": None,
            "observation_state": "UNCONFIRMED", "is_ksl_translation": False,
            "method": "gesture_template_matching",
            "scope": "사용자가 등록한 손동작 후보의 유사도를 비교했습니다. 한국수어 문장의 자동 번역이 아닙니다.",
            "warnings": ["표정·몸 자세·문법을 판독하지 않습니다. 등록 문구의 의미와 손동작 일치 여부는 사람이 확인해야 합니다."]}


def extract_hand_landmarks(video_path: Path, task_model_path: Path, *, sample_fps=8, max_frames=120):
    """Extract fixed left/right hand coordinates with an operator-supplied task asset.

    Optional dependencies: mediapipe, its supported runtime, and FFmpeg. Sampling
    times are approximate. The caller retains the video for expert inspection.
    """
    info = probe_sign_video(Path(video_path))
    model = Path(task_model_path)
    if model.is_symlink() or not model.is_file() or not 0 < model.stat().st_size <= 64*1024*1024:
        raise SignError("SIGN_LOCAL_MODEL_MISSING", "운영자가 확보한 HandLandmarker .task 파일의 경로를 지정하십시오. 모델을 자동 다운로드하지 않았습니다.")
    if (isinstance(sample_fps, bool) or not isinstance(sample_fps, (int, float)) or not math.isfinite(sample_fps)
            or not 0 < sample_fps <= 15 or isinstance(max_frames, bool) or not isinstance(max_frames, int) or not 3 <= max_frames <= 120):
        raise SignError("SIGN_MATCH_SETTINGS_INVALID", "수어 화면 추출 기준값이 올바르지 않습니다.")
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
    except (ImportError, OSError):
        raise SignError("SIGN_LOCAL_DEPENDENCY_MISSING", "로컬 손 좌표 인식에 필요한 mediapipe가 없습니다. 지원되는 mediapipe 실행환경과 .task 모델을 설치하십시오.") from None
    rate = min(float(sample_fps), max_frames/info["duration_seconds"])
    sequence, frames = [], []
    try:
        options = vision.HandLandmarkerOptions(base_options=mp_python.BaseOptions(model_asset_path=str(model.resolve())),
            running_mode=vision.RunningMode.VIDEO, num_hands=2, min_hand_detection_confidence=0.6,
            min_tracking_confidence=0.6)
        with tempfile.TemporaryDirectory(prefix="sign-frames-", dir=Path(video_path).parent) as work:
            _run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-max_alloc", "67108864",
                  "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,avi",
                  "-i", str(Path(video_path).resolve()), "-map", "0:v:0", "-an",
                  "-vf", f"fps={rate:.6f},scale=640:640:force_original_aspect_ratio=decrease",
                  "-frames:v", str(max_frames), "-threads", "1", str(Path(work)/"frame-%04d.png")], 90)
            with vision.HandLandmarker.create_from_options(options) as detector:
                for index, path in enumerate(sorted(Path(work).glob("frame-*.png"))):
                    timestamp = round(index*1000/rate)
                    result = detector.detect_for_video(mp.Image.create_from_file(str(path)), timestamp)
                    vector, presence = [0.0]*126, [False, False]
                    for points, handed in zip(result.hand_landmarks, result.handedness):
                        if len(points) != 21 or not handed:
                            continue
                        label = handed[0].category_name
                        if label not in {"Left", "Right"}:
                            continue
                        slot = 0 if label == "Left" else 1
                        if presence[slot]:
                            continue
                        vector[slot*63:(slot+1)*63] = [float(value) for point in points for value in (point.x, point.y, point.z)]
                        presence[slot] = True
                    sequence.append(vector)
                    frames.append({"timestamp_ms": timestamp, "hand_presence": presence})
    except SignError:
        raise
    except Exception:
        raise SignError("SIGN_LOCAL_EXTRACTION_FAILED", "손 좌표 인식을 실행하지 못했습니다. 모델 파일과 mediapipe 실행환경을 확인하십시오.") from None
    _normalize(sequence)  # Includes finite-value, number-of-frames and no-hand checks.
    return {"sequence": sequence, "frames": frames, **info, "method": "mediapipe_hand_landmarks",
            "observation_state": "UNCONFIRMED", "model_sha256": _sha256(model),
            "warnings": ["시각은 표본 추출 간격에 따른 근사값입니다.",
                         "좌우 손 좌표만 읽습니다. 표정·몸 자세·수어 문법은 포함하지 않습니다. 거울 촬영 여부와 좌우 손 슬롯을 확인하십시오."]}
