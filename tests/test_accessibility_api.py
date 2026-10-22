"""Owner accessibility API behavior, using generated local fixtures only."""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest

from app.services import accessibility_api
from app.services.accessibility_store import AccessibilityStore


@pytest.fixture
def accessibility_client(tmp_path, monkeypatch):
    monkeypatch.setenv("EDURECOVER_OWNER_USER", "test-owner")
    monkeypatch.setenv("EDURECOVER_OWNER_PASSWORD", "test-password")
    for name in ("SIGN_RECOGNITION_ENDPOINT", "SIGN_RECOGNITION_MODEL", "SIGN_RECOGNITION_API_KEY", "SIGN_DATA_POLICY_ACK", "MEDIA_SIGN_LOCAL_MODEL_PATH"):
        monkeypatch.delenv(name, raising=False)
    archive = AccessibilityStore(tmp_path / "accessibility")
    monkeypatch.setattr(accessibility_api, "store", lambda: archive)
    app = FastAPI()
    app.include_router(accessibility_api.router)
    basic = base64.b64encode(b"test-owner:test-password").decode()
    with TestClient(app, headers={"Authorization": "Basic " + basic}) as client:
        yield client, archive


def raw_post(client, route, data, **params):
    return client.post("/owner/accessibility/" + route, params={"explicit_authorization": "true", **params}, content=data,
                       headers={"Content-Type": "application/octet-stream", "X-Accessibility-Upload": "1"})


@pytest.fixture
def braille_image():
    image = Image.new("RGB", (130, 95), "white")
    draw = ImageDraw.Draw(image)
    # Unicode cells U+2801 (dot1) and U+2803 (dot1+dot2).
    for x, y in ((20, 20), (80, 20), (80, 40)):
        draw.ellipse((x-4, y-4, x+4, y+4), fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    grid = {"origin_x": 20, "origin_y": 20, "dot_dx": 20, "dot_dy": 20,
            "cell_dx": 60, "cell_dy": 80, "columns": 2, "rows": 1, "sample_radius": 3}
    return output.getvalue(), json.dumps(grid)


@pytest.fixture
def sign_video(tmp_path):
    if not all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe")):
        pytest.skip("local media tools unavailable")
    path = tmp_path / "sign.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1", "-an", "-pix_fmt", "yuv420p", "-y", str(path)], check=True)
    return path


def landmarks():
    return [[value for point in range(21) for value in (point / 20, point / 40 + frame / 100, 0.0)] for frame in range(3)]


def upload_gallery(client, path, **params):
    return raw_post(client, "sign/videos", path.read_bytes(), filename="sign.mp4", phrase="감사합니다", title="사용자 등록 수어", **params)


def test_owner_only_accessibility_surfaces(accessibility_client):
    client, _ = accessibility_client
    for route in ("status", "sign/videos", "sign/templates", "sign/exposure?session_id=testsession"):
        response = client.get("/owner/accessibility/" + route, headers={"Authorization": "Basic d3Jvbmc6d3Jvbmc="}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/owner/login"
    response = client.post("/owner/accessibility/braille?grid={}", content=b"x", headers={"Authorization": "Basic d3Jvbmc6d3Jvbmc="})
    assert response.status_code == 401


def test_generated_braille_upload_reads_unicode_without_claiming_translation(accessibility_client, braille_image):
    client, archive = accessibility_client
    image, grid = braille_image
    response = raw_post(client, "braille", image, grid=grid)
    assert response.status_code == 200
    result = response.json()
    assert result["unicode_braille"] == "\u2801\u2803"
    assert result["source_sha256"] == hashlib.sha256(image).hexdigest()
    assert result["observation_state"] == "UNCONFIRMED"
    assert result["translation"] == {"state": "NOT_REQUESTED", "text": None}
    assert response.headers["cache-control"] == "no-store"
    assert not list(archive.root.glob("braille-*"))


def test_braille_requires_consent_and_checks_both_upload_lengths(accessibility_client, braille_image):
    client, archive = accessibility_client
    image, grid = braille_image
    assert raw_post(client, "braille", image, grid=grid, explicit_authorization="false").status_code == 400
    assert raw_post(client, "braille", b"x" * (10*1024*1024+1), grid=grid).status_code == 413
    oversized = iter([b"x" * (6*1024*1024), b"x" * (6*1024*1024)])
    assert raw_post(client, "braille", oversized, grid=grid).status_code == 413
    assert raw_post(client, "braille", b"", grid=grid).status_code == 400
    assert not list(archive.root.glob("braille-*"))


def test_bad_braille_grid_or_image_is_explained(accessibility_client, braille_image):
    client, _ = accessibility_client
    image, grid = braille_image
    assert raw_post(client, "braille", image, grid='{"unexpected":1}').status_code == 400
    assert raw_post(client, "braille", b"not an image", grid=grid).json()["error_code"] == "INVALID_IMAGE"


@pytest.mark.parametrize("filename", ["../movie.mp4", "folder\\movie.mp4", "movie.m3u8", "movie.exe"])
def test_gallery_rejects_paths_and_nonmedia(accessibility_client, filename):
    client, archive = accessibility_client
    response = raw_post(client, "sign/videos", b"x", filename=filename, phrase="안녕")
    assert response.status_code in (400, 415)
    assert not archive.list_videos()


def test_video_gallery_range_playback_exposure_and_delete(accessibility_client, sign_video):
    client, archive = accessibility_client
    response = upload_gallery(client, sign_video)
    assert response.status_code == 201
    item = response.json()
    assert item["expert_verified"] is False
    assert item["meaning_state"] == "OWNER_LABEL_UNCONFIRMED"
    video_id = item["id"]
    content_url = f"/owner/accessibility/sign/videos/{video_id}/content"
    content = client.get(content_url)
    assert content.status_code == 200
    assert content.headers["content-type"] == "video/mp4"
    assert content.content == sign_video.read_bytes()
    partial = client.get(content_url, headers={"Range": "bytes=0-15"})
    assert partial.status_code == 206
    assert partial.headers["content-range"] == f"bytes 0-15/{sign_video.stat().st_size}"
    assert partial.content == sign_video.read_bytes()[:16]
    event = {"video_id": video_id, "session_id": "session_test", "event": "play", "current_time": 0, "visible": True}
    recorded = client.post("/owner/accessibility/sign/exposure", json=event)
    assert recorded.status_code == 200
    assert recorded.json()["state"] == "PLAYBACK_EVENT_RECORDED"
    assert recorded.json()["understanding_verified"] is False
    assert recorded.json()["source_sha256"] == item["sha256"]
    observed = client.get("/owner/accessibility/sign/exposure", params={"session_id": "session_test"}).json()
    assert len(observed["events"]) == 1
    assert observed["understanding_verified"] is False
    assert client.post("/owner/accessibility/sign/exposure", json={**event, "current_time": 10}).status_code == 400
    assert client.post("/owner/accessibility/sign/exposure", json={**event, "event": "understood"}).status_code == 422
    assert client.post("/owner/accessibility/sign/exposure", json={**event, "expert_verified": True}).status_code == 422
    assert client.delete(f"/owner/accessibility/sign/videos/{video_id}").status_code == 200
    assert client.get(content_url).status_code == 404
    assert not list(archive.videos.iterdir())


def test_gallery_rejects_blank_label_and_false_container(accessibility_client, sign_video, tmp_path):
    client, archive = accessibility_client
    assert raw_post(client, "sign/videos", sign_video.read_bytes(), filename="sign.mp4", phrase="   ").status_code == 400
    renamed = tmp_path / "actual.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(sign_video), "-c", "copy", "-y", str(renamed)], check=True)
    response = raw_post(client, "sign/videos", renamed.read_bytes(), filename="pretend.mp4", phrase="안녕")
    assert response.status_code == 415
    assert response.json()["error_code"] == "PLAYER_CONTAINER_MISMATCH"
    assert not archive.list_videos()


def test_sign_upload_declared_limit_is_checked_before_decoding(accessibility_client):
    client, archive = accessibility_client
    response = client.post("/owner/accessibility/sign/videos", params={"filename": "x.mp4", "phrase": "시험", "explicit_authorization": "true"},
                           content=b"x", headers={"Content-Type": "application/octet-stream", "X-Accessibility-Upload": "1", "Content-Length": str(32*1024*1024+1)})
    assert response.status_code == 413
    assert response.json()["error_code"] == "UPLOAD_TOO_LARGE"
    assert not archive.list_videos()
    assert not list(archive.root.glob("gallery-*"))


def test_unconfigured_sign_provider_never_returns_mock_translation(accessibility_client, sign_video, monkeypatch):
    client, archive = accessibility_client
    from app.services import sign_core
    called = []
    monkeypatch.setattr(sign_core, "_provider_request", lambda *args: called.append(True))
    response = raw_post(client, "sign/recognize", sign_video.read_bytes(), filename="sign.mp4")
    assert response.status_code == 400
    assert response.json()["error_code"] == "SIGN_NOT_CONFIGURED"
    assert not called
    assert not list(archive.root.glob("sign-*"))


def test_registered_landmarks_are_limited_unconfirmed_candidates(accessibility_client):
    client, _ = accessibility_client
    payload = {"label": "시험 동작", "sequence": landmarks()}
    created = client.post("/owner/accessibility/sign/templates", json=payload)
    assert created.status_code == 200
    assert created.json()["observation_state"] == "UNCONFIRMED"
    assert created.json()["is_ksl_translation"] is False
    public = client.get("/owner/accessibility/sign/templates").json()["templates"][0]
    assert "sequence" not in public
    assert public["observation_state"] == "UNCONFIRMED"
    assert public["is_ksl_translation"] is False
    matched = client.post("/owner/accessibility/sign/classify", json={"sequence": landmarks()})
    assert matched.status_code == 200
    assert matched.json()["status"] == "MATCH_CANDIDATE"
    assert matched.json()["is_ksl_translation"] is False
    assert matched.json()["observation_state"] == "UNCONFIRMED"
    assert matched.json()["confidence"] is None
    assert client.delete("/owner/accessibility/sign/templates/" + created.json()["id"]).status_code == 200


def test_landmark_payload_bound_and_label_validation(accessibility_client):
    client, _ = accessibility_client
    route = "/owner/accessibility/sign/templates"
    response = client.post(route, content=iter([b"x"*1100000, b"x"*1100000]), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["error_code"] == "PAYLOAD_TOO_LARGE"
    assert client.post(route, json={"label": "   ", "sequence": landmarks()}).status_code == 400
    assert client.post(route, json={"label": "test", "sequence": [[0]*63]*121}).status_code == 400
    assert client.post(route, json={"label": "test", "sequence": [[0]*64]*3}).status_code == 400
    assert client.post(route, json={"label": "test", "sequence": landmarks(), "expert_verified": True}).status_code == 400
    for value in (True, "0.5"):
        sequence = landmarks()
        sequence[0][0] = value
        assert client.post(route, json={"label": "test", "sequence": sequence}).status_code == 400


def test_template_count_quota_is_enforced(accessibility_client):
    client, archive = accessibility_client
    route = "/owner/accessibility/sign/templates"
    for index in range(20):
        assert client.post(route, json={"label": f"동작{index}", "sequence": landmarks()}).status_code == 200
    response = client.post(route, json={"label": "한도초과", "sequence": landmarks()})
    assert response.status_code == 409
    assert response.json()["error_code"] == "TEMPLATE_LIMIT"
    assert len(archive.list_templates()) == 20


def test_shared_quota_applies_to_new_templates(accessibility_client):
    client, archive = accessibility_client
    with (archive.root / "quota-fixture").open("wb") as full:
        full.truncate(96 * 1024 * 1024)
    response = client.post("/owner/accessibility/sign/templates", json={"label": "시험", "sequence": landmarks()})
    assert response.status_code == 507
    assert response.json()["error_code"] == "ACCESSIBILITY_STORAGE_FULL"
    assert not archive.list_templates()


def test_killed_upload_is_reaped_without_deleting_current_upload_or_video(tmp_path):
    import os
    import time
    archive = AccessibilityStore(tmp_path / "accessibility")
    old = archive.root / "sign-killed"
    recent = archive.root / "braille-current"
    for path in (old, recent):
        path.mkdir()
        (path / "source").write_bytes(b"private temporary input")
    old_time = time.time()-601
    os.utime(old, (old_time, old_time))
    (archive.videos / "keep.mp4").write_bytes(b"registered video")
    archive.cleanup_temporary()
    assert not old.exists()
    assert (recent / "source").exists()
    assert (archive.videos / "keep.mp4").read_bytes() == b"registered video"
