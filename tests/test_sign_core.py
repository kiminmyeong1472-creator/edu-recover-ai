"""Local synthetic hand-motion tests and stub sign-provider contract checks.

These checks do not measure accuracy on human Korean Sign Language recordings.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import types
from urllib.error import HTTPError, URLError

import pytest

from app.services import sign_core as core
from app.services.sign_core import SignConfig, SignError, classify_landmarks


def motion(finger_shift=0.0, shift=0.0, scale=1.0):
    sequence = []
    for frame in range(5):
        coordinates = []
        for index in range(21):
            coordinates.extend([scale*(index*0.02+frame*0.1)+shift,
                                scale*((index % 4)*0.03+(finger_shift if index > 12 else 0)), 0.0])
        sequence.append(coordinates)
    return sequence


def template(label="등록 손동작", identity="sample", sequence=None):
    return {"id": identity, "label": label, "sequence": motion() if sequence is None else sequence}


def test_local_motion_match_is_only_unconfirmed_template_candidate():
    result = classify_landmarks(motion(), [template()])
    assert result["status"] == "MATCH_CANDIDATE"
    assert result["template_id"] == "sample"
    assert result["distance"] == 0
    assert result["observation_state"] == "UNCONFIRMED"
    assert result["confidence"] is None
    assert result["is_ksl_translation"] is False


def test_motion_match_preserves_translation_and_scale_invariance():
    result = classify_landmarks(motion(shift=3, scale=2), [template()])
    assert result["status"] == "MATCH_CANDIDATE"
    assert result["distance"] < 1e-5


def test_different_unregistered_motion_is_not_forced_into_a_known_label():
    result = classify_landmarks(motion(finger_shift=3), [template()], threshold=0.1)
    assert result["status"] == "UNKNOWN"
    assert result["label"] is None


def test_same_motion_with_conflicting_labels_remains_ambiguous():
    result = classify_landmarks(motion(), [template("첫 뜻", "a"), template("다른 뜻", "b")])
    assert result["status"] == "AMBIGUOUS"
    assert result["label"] is None


@pytest.mark.parametrize("bad", [[], [[0]*63]*2, [[0]*62]*3, [[float('nan')]*63]*3,
                                  [[True]*63]*3, [[0]*63]*121])
def test_malformed_or_unbounded_landmarks_fail_closed(bad):
    with pytest.raises(SignError):
        classify_landmarks(bad, [])


def test_no_hand_data_and_duplicate_template_ids_are_rejected():
    with pytest.raises(SignError) as exc:
        classify_landmarks([[0]*126]*3, [])
    assert exc.value.code == "NO_HAND_LANDMARKS"
    with pytest.raises(SignError):
        classify_landmarks(motion(), [template(), template()])


def test_different_hand_slot_counts_do_not_match():
    double = [frame + [0.0]*63 for frame in motion()]
    result = classify_landmarks(double, [template()])
    assert result["status"] == "UNKNOWN"
    assert result["distance"] is None


@pytest.fixture
def video(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("media tools not installed")
    path = tmp_path / "sign.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=320x240:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path)], check=True)
    return path


def configured():
    return SignConfig(endpoint="https://example.invalid/sign", model="test-sign-only",
                      api_key="short-key-not-hex", data_policy_ack=True)


def response_for(path, **changes):
    value = {"language": "ksl", "source_video_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "segments": [{"start": 0.1, "end": 0.8, "text": "시험용 응답", "confidence": 0.8}]}
    value.update(changes)
    return value


def test_real_video_probe_and_stub_provider_remain_unconfirmed(video, monkeypatch):
    monkeypatch.setattr(core, "_provider_request", lambda *args: response_for(video))
    result = core.recognize_sign_video(video, configured())
    assert result["segments"][0]["text"] == "시험용 응답"
    assert result["segments"][0]["observation_state"] == "UNCONFIRMED"
    assert result["expert_confirmed"] is False
    assert result["width"] == 320


def test_missing_settings_never_calls_any_provider(video, monkeypatch):
    monkeypatch.setattr(core, "_provider_request", lambda *args: pytest.fail("provider must not be contacted"))
    with pytest.raises(SignError) as exc:
        core.recognize_sign_video(video, SignConfig())
    assert exc.value.code == "SIGN_NOT_CONFIGURED"
    with pytest.raises(SignError):
        core.recognize_sign_video(video, SignConfig(endpoint="https://example.invalid/sign", model="x", api_key="x"))


@pytest.mark.parametrize("change", [{"language": "asl"}, {"source_video_sha256": "wrong"}, {"model": "another"}])
def test_wrong_video_language_or_model_cannot_be_accepted(video, monkeypatch, change):
    monkeypatch.setattr(core, "_provider_request", lambda *args: response_for(video, **change))
    with pytest.raises(SignError) as exc:
        core.recognize_sign_video(video, configured())
    assert exc.value.code == "SIGN_RESPONSE_MISMATCH"


@pytest.mark.parametrize("segment", [
    {"start": -1, "end": 0.8, "text": "x", "confidence": 0.8},
    {"start": 0.1, "end": 10, "text": "x", "confidence": 0.8},
    {"start": 0.1, "end": 0.8, "text": "x", "confidence": float("nan")},
    {"start": 0.1, "end": 0.8, "text": "x", "confidence": 2},
    {"start": True, "end": 0.8, "text": "x", "confidence": 0.8},
    {"start": 0.1, "end": 0.8, "text": "", "confidence": 0.8},
])
def test_provider_output_times_and_confidence_are_validated(video, monkeypatch, segment):
    monkeypatch.setattr(core, "_provider_request", lambda *args: response_for(video, segments=[segment]))
    with pytest.raises(SignError) as exc:
        core.recognize_sign_video(video, configured())
    assert exc.value.code == "SIGN_RESPONSE_INVALID"


def test_provider_transport_contains_exact_source_binding_and_no_fallback(video, monkeypatch):
    captured = []
    class Response:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return json.dumps(response_for(video)).encode()
    class Opener:
        def open(self, request, timeout):
            captured.append(request)
            assert timeout == 120
            return Response()
    monkeypatch.setattr(core, "build_opener", lambda *args: Opener())
    result = core.recognize_sign_video(video, configured())
    assert result["status"] == "UNCONFIRMED"
    assert len(captured) == 1
    body = captured[0].data
    assert b"source_video_sha256" in body
    assert b"test-sign-only" in body
    assert video.read_bytes() in body


@pytest.mark.parametrize("failure", [HTTPError("https://example.invalid", 302, "redirect", {}, None),
                                    HTTPError("https://example.invalid", 401, "short-key-not-hex", {}, None),
                                    URLError("short-key-not-hex"), TimeoutError("short-key-not-hex")])
def test_transport_errors_do_not_reveal_secret_or_retry(video, monkeypatch, failure):
    calls = []
    class Opener:
        def open(self, *args, **kwargs):
            calls.append(True)
            raise failure
    monkeypatch.setattr(core, "build_opener", lambda *args: Opener())
    with pytest.raises(SignError) as exc:
        core.recognize_sign_video(video, configured())
    assert len(calls) == 1
    assert "short-key-not-hex" not in str(exc.value)
    assert core._NoRedirect().redirect_request(None, None, 302, None, None, "https://other.invalid") is None


def test_configuration_does_not_impose_hex_or_key_length(monkeypatch):
    monkeypatch.setenv("SIGN_RECOGNITION_ENDPOINT", "https://example.invalid/sign")
    monkeypatch.setenv("SIGN_RECOGNITION_MODEL", "test-model")
    monkeypatch.setenv("SIGN_RECOGNITION_API_KEY", "x!")
    monkeypatch.setenv("SIGN_DATA_POLICY_ACK", "true")
    assert SignConfig.from_env().ready
    assert SignConfig.from_env().api_key == "x!"
    assert "x!" not in repr(SignConfig.from_env())
    with pytest.raises(SignError):
        SignConfig(endpoint="http://example.invalid/sign")


def test_local_extractor_requires_operator_asset(video, tmp_path):
    with pytest.raises(SignError) as exc:
        core.extract_hand_landmarks(video, tmp_path / "missing.task")
    assert exc.value.code == "SIGN_LOCAL_MODEL_MISSING"


def test_local_video_extractor_with_stub_mediapipe_has_fixed_slots(video, tmp_path, monkeypatch):
    model = tmp_path / "test.task"
    model.write_bytes(b"test asset only")
    points = [types.SimpleNamespace(x=i*0.02, y=(i % 4)*0.03, z=0) for i in range(21)]
    detection = types.SimpleNamespace(hand_landmarks=[points], handedness=[[types.SimpleNamespace(category_name="Left")]])
    calls = []
    class Detector:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def detect_for_video(self, image, timestamp):
            calls.append(timestamp)
            return detection
    vision = types.ModuleType("mediapipe.tasks.python.vision")
    vision.HandLandmarkerOptions = lambda **kwargs: kwargs
    vision.RunningMode = types.SimpleNamespace(VIDEO="VIDEO")
    vision.HandLandmarker = types.SimpleNamespace(create_from_options=lambda options: Detector())
    mp_python = types.ModuleType("mediapipe.tasks.python")
    mp_python.BaseOptions = lambda **kwargs: kwargs
    mp_python.vision = vision
    tasks = types.ModuleType("mediapipe.tasks")
    tasks.python = mp_python
    mp = types.ModuleType("mediapipe")
    mp.tasks = tasks
    mp.Image = types.SimpleNamespace(create_from_file=lambda name: name)
    for name, value in [("mediapipe", mp), ("mediapipe.tasks", tasks), ("mediapipe.tasks.python", mp_python),
                        ("mediapipe.tasks.python.vision", vision)]:
        monkeypatch.setitem(sys.modules, name, value)
    result = core.extract_hand_landmarks(video, model, sample_fps=4)
    assert 3 <= len(result["sequence"]) <= 120
    assert len(result["sequence"][0]) == 126
    assert result["sequence"][0][63:] == [0]*63
    assert result["frames"][0]["hand_presence"] == [True, False]
    assert calls == sorted(set(calls))
    assert result["observation_state"] == "UNCONFIRMED"
    assert not list(tmp_path.glob("sign-frames-*"))


def test_frame_cap_samples_whole_clip_instead_of_only_its_beginning(tmp_path, monkeypatch):
    video = tmp_path / "bounded.mp4"
    video.write_bytes(b"sampling fixture; probe is stubbed")
    model = tmp_path / "test.task"
    model.write_bytes(b"stub model")
    monkeypatch.setattr(core, "probe_sign_video", lambda path: {"duration_seconds": 60.0, "source_video_sha256": "fixture"})
    commands, timestamps = [], []
    def ffmpeg_stub(args, timeout):
        commands.append(args)
        output = Path(args[-1])
        for index in range(120):
            (output.parent / f"frame-{index:04d}.png").write_bytes(b"stub frame")
        return b""
    monkeypatch.setattr(core, "_run", ffmpeg_stub)
    points = [types.SimpleNamespace(x=i*0.02, y=(i % 4)*0.03, z=0) for i in range(21)]
    class Detector:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def detect_for_video(self, image, timestamp):
            timestamps.append(timestamp)
            return types.SimpleNamespace(hand_landmarks=[points], handedness=[[types.SimpleNamespace(category_name="Left")]])
    vision = types.ModuleType("mediapipe.tasks.python.vision")
    vision.HandLandmarkerOptions = lambda **kwargs: kwargs
    vision.RunningMode = types.SimpleNamespace(VIDEO="VIDEO")
    vision.HandLandmarker = types.SimpleNamespace(create_from_options=lambda options: Detector())
    mp_python = types.ModuleType("mediapipe.tasks.python")
    mp_python.BaseOptions = lambda **kwargs: kwargs
    mp_python.vision = vision
    tasks = types.ModuleType("mediapipe.tasks")
    tasks.python = mp_python
    mp = types.ModuleType("mediapipe")
    mp.tasks = tasks
    mp.Image = types.SimpleNamespace(create_from_file=lambda name: name)
    for name, value in [("mediapipe", mp), ("mediapipe.tasks", tasks), ("mediapipe.tasks.python", mp_python),
                        ("mediapipe.tasks.python.vision", vision)]:
        monkeypatch.setitem(sys.modules, name, value)
    result = core.extract_hand_landmarks(video, model)
    filter_text = commands[0][commands[0].index("-vf")+1]
    assert "fps=2.000000" in filter_text
    assert len(result["sequence"]) == 120
    assert timestamps[-1] == 59500


def test_local_extractor_reports_missing_optional_dependency(video, tmp_path, monkeypatch):
    model = tmp_path / "test.task"
    model.write_bytes(b"stub task")
    monkeypatch.setitem(sys.modules, "mediapipe", None)
    with pytest.raises(SignError) as exc:
        core.extract_hand_landmarks(video, model)
    assert exc.value.code == "SIGN_LOCAL_DEPENDENCY_MISSING"
