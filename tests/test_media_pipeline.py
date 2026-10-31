"""Behavior checks; external ASR is stubbed and never counted as live inference."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.services import media_api, media_core, media_jobs
from app.services.media_core import MediaConfig, MediaError, process_media
from app.services.media_jobs import JobError, MediaJobs


@pytest.fixture
def owner_client(tmp_path, monkeypatch):
    monkeypatch.setenv("EDURECOVER_OWNER_USER", "test-owner")
    monkeypatch.setenv("EDURECOVER_OWNER_PASSWORD", "test-password")
    monkeypatch.setenv("MEDIA_MAX_UPLOAD_MB", "1")
    for name in ("MEDIA_ASR_ENDPOINT", "MEDIA_ASR_MODEL", "MEDIA_ASR_API_KEY", "MEDIA_ASR_DATA_POLICY_ACK"):
        monkeypatch.delenv(name, raising=False)
    jobs = MediaJobs(tmp_path / "jobs")
    monkeypatch.setattr(media_api, "store", lambda: jobs)
    app = FastAPI()
    app.include_router(media_api.router)
    basic = base64.b64encode(b"test-owner:test-password").decode()
    with TestClient(app, headers={"Authorization": "Basic " + basic}) as client:
        yield client, jobs


def submit(client, data=b"recording", **params):
    options = {"filename": "lecture.wav", "purpose": "lecture", "explicit_authorization": "true", **params}
    return client.post("/owner/media/jobs", params=options, content=data,
                       headers={"Content-Type": "application/octet-stream", "X-Media-Upload": "1"})


def test_owner_auth_required_for_every_surface(owner_client):
    client, _ = owner_client
    response = client.get("/owner/media/status", headers={"Authorization": "Basic d3Jvbmc6d3Jvbmc="}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/owner/login"
    response = client.post("/owner/media/jobs?filename=x.wav", headers={"Authorization": "Basic d3Jvbmc6d3Jvbmc="}, content=b"x")
    assert response.status_code == 401


def test_readiness_does_not_claim_actual_asr(owner_client):
    client, _ = owner_client
    response = client.get("/owner/media/status")
    assert response.status_code == 200
    assert response.json()["max_upload_mb"] == 1
    assert response.json()["asr_configured"] is False
    assert response.json()["actual_asr_verified"] is False
    assert response.headers["cache-control"] == "no-store"


def test_requires_authorization_and_raw_body_type(owner_client):
    client, jobs = owner_client
    assert submit(client, explicit_authorization="false").status_code == 400
    assert client.post("/owner/media/jobs?filename=x.wav&explicit_authorization=true", content=b"x",
                       headers={"Content-Type": "application/octet-stream"}).status_code == 400
    assert client.post("/owner/media/jobs?filename=x.wav&explicit_authorization=true", content=b"x",
                       headers={"Content-Type": "text/plain", "X-Media-Upload": "1"}).status_code == 415
    assert not jobs.list()


@pytest.mark.parametrize("filename", ["../secret.wav", "folder\\x.wav", "playlist.m3u8", "report.pdf"])
def test_rejects_paths_playlists_and_nonmedia(owner_client, filename):
    client, jobs = owner_client
    assert submit(client, filename=filename).status_code == 415
    assert not jobs.list()


def test_upload_limits_with_declared_and_streamed_content(owner_client):
    client, jobs = owner_client
    assert submit(client, b"x" * (1024 * 1024 + 1)).status_code == 413
    response = submit(client, iter([b"x" * (700 * 1024), b"x" * (700 * 1024)]))
    assert response.status_code == 413
    assert response.json()["error_code"] == "UPLOAD_TOO_LARGE"
    assert submit(client, b"").status_code == 400
    assert not jobs.list()
    assert not list(jobs.root.glob("*/source.*"))


def test_queue_bound_and_restart_preserve_queued_work(owner_client):
    client, jobs = owner_client
    first = submit(client).json()
    second = submit(client, filename="news.mp4", purpose="news", language="auto").json()
    assert first["status"] == second["status"] == "queued"
    assert submit(client).status_code == 429
    restarted = MediaJobs(jobs.root)
    assert restarted.get(first["id"])["status"] == "queued"
    assert restarted.get(second["id"])["language"] == "auto"
    assert client.delete("/owner/media/jobs/" + first["id"]).status_code == 409


def test_worker_failure_is_visible_and_removes_source(owner_client):
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe not installed")
    client, jobs = owner_client
    job = submit(client, b"invalid media").json()
    jobs.execute(job)
    response = client.get("/owner/media/jobs/" + job["id"])
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "MEDIA_DECODE_FAILED"
    assert not list(jobs.path(job["id"]).glob("source.*"))
    assert client.get(f"/owner/media/jobs/{job['id']}/files/transcript.txt").status_code == 404
    assert client.delete("/owner/media/jobs/" + job["id"]).json() == {"deleted": True}


def test_completed_artifacts_are_allowlisted_and_deleted(owner_client, monkeypatch):
    client, jobs = owner_client
    job = submit(client).json()

    def fake_process(path, output_dir, **kwargs):
        kwargs["progress"]("asr", 1, 1)
        result = {"status": "complete", "segments": [{"start": 0, "end": 1, "text": "mock only"}]}
        (output_dir / "transcript.json").write_text(json.dumps(result))
        (output_dir / "transcript.txt").write_text("mock only")
        (output_dir / "secret.key").write_text("not downloadable")
        return result

    monkeypatch.setattr(media_jobs, "process_media", fake_process)
    jobs.execute(job)
    details = client.get("/owner/media/jobs/" + job["id"]).json()
    assert details["status"] == "completed"
    assert details["result"]["segments"][0]["text"] == "mock only"
    assert client.get(f"/owner/media/jobs/{job['id']}/files/transcript.txt").text == "mock only"
    assert client.get(f"/owner/media/jobs/{job['id']}/files/secret.key").status_code == 404
    assert not list(jobs.path(job["id"]).glob("source.*"))
    assert client.delete("/owner/media/jobs/" + job["id"]).status_code == 200
    assert client.get("/owner/media/jobs/" + job["id"]).status_code == 404


def test_finished_results_expire_after_24_hours(tmp_path):
    jobs = MediaJobs(tmp_path / "jobs")
    job = jobs.create("news.wav", "news", "ko", False)
    jobs.update(job["id"], "failed", "test failure")
    with jobs.connect() as con:
        con.execute("UPDATE jobs SET updated_at=? WHERE id=?", (time.time() - 86401, job["id"]))
    jobs.cleanup()
    assert not jobs.path(job["id"]).exists()
    with pytest.raises(JobError):
        jobs.get(job["id"])


def test_stalled_upload_cleanup_removes_source_and_frees_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_MAX_UPLOAD_MB", "1")
    jobs = MediaJobs(tmp_path / "jobs")
    stalled = jobs.create("stalled.wav", "lecture", "ko", False)
    jobs.create("active.wav", "lecture", "ko", False)
    source = jobs.path(stalled["id"]) / "source.wav"
    source.write_bytes(b"abandoned private upload")
    with jobs.connect() as con:
        con.execute("UPDATE jobs SET updated_at=? WHERE id=?", (time.time() - 601, stalled["id"]))
    jobs.cleanup()
    recovered = jobs.get(stalled["id"])
    assert recovered["status"] == "failed"
    assert recovered["error_code"] == "INTERRUPTED"
    assert not source.exists()
    replacement = jobs.create("replacement.wav", "lecture", "ko", False)
    assert replacement["status"] == "uploading"


def test_concurrent_empty_uploads_reserve_their_maximum_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_MAX_UPLOAD_MB", "256")
    jobs = MediaJobs(tmp_path / "jobs")
    barrier = threading.Barrier(2)

    def reserve(index):
        barrier.wait(timeout=3)
        try:
            return jobs.create(f"upload-{index}.wav", "lecture", "ko", False)
        except JobError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    accepted = [item for item in results if isinstance(item, dict)]
    rejected = [item for item in results if isinstance(item, JobError)]
    assert len(accepted) == len(rejected) == 1
    assert accepted[0]["status"] == "uploading"
    assert rejected[0].code == "STORAGE_FULL"
    assert not list(jobs.path(accepted[0]["id"]).glob("source.*"))


def test_worker_restart_marks_interrupted_and_removes_extracted_audio(tmp_path):
    jobs = MediaJobs(tmp_path / "jobs")
    job = jobs.create("lecture.wav", "lecture", "ko", False)
    jobs.update(job["id"], "running", "asr")
    (jobs.path(job["id"]) / "source.wav").write_bytes(b"private upload")
    abandoned = jobs.path(job["id"]) / "media-work-abandoned"
    abandoned.mkdir()
    (abandoned / "chunk.wav").write_bytes(b"private extracted audio")
    jobs.start()
    try:
        deadline = time.monotonic() + 3
        while jobs.get(job["id"])["status"] != "failed" and time.monotonic() < deadline:
            time.sleep(0.02)
        state = jobs.get(job["id"])
        assert state["status"] == "failed"
        assert state["error_code"] == "INTERRUPTED"
        # Status and file cleanup happen consecutively; wait for the worker's
        # recovery block to finish before checking its privacy guarantee.
        while abandoned.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not abandoned.exists()
        assert not (jobs.path(job["id"]) / "source.wav").exists()
    finally:
        jobs.stop()
        jobs.thread.join(timeout=2)


@pytest.fixture
def audio_file(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "speech-fixture.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=21", "-y", str(path)], check=True)
    return path


def test_real_audio_decode_chunking_with_stub_asr(audio_file, tmp_path, monkeypatch):
    calls = []

    def speech_stub(chunk, language, config, **kwargs):
        with wave.open(str(chunk)) as audio:
            assert audio.getframerate() == 16000
            assert audio.getnchannels() == 1
            length = audio.getnframes() / audio.getframerate()
        calls.append((language, length))
        return {"segments": [{"start": 0, "end": min(1, length), "text": f"stub phrase {len(calls)}"}]}

    monkeypatch.setattr(media_core, "_request_asr", speech_stub)
    config = MediaConfig(asr_endpoint="https://example.invalid/audio/transcriptions", asr_model="unit-test",
                         asr_api_key="unit-test-only", data_policy_ack=True, chunk_seconds=10)
    result = process_media(audio_file, tmp_path / "out", language="auto", include_ocr=False, config=config)
    assert len(calls) == 3
    assert [s["start"] for s in result["segments"]] == [0, 10, 20]
    assert result["asr_status"] == "complete"
    assert result["analysis"]["method"] == "extractive_frequency"
    assert "stub phrase 1" in (tmp_path / "out" / "transcript.srt").read_text()
    assert not list((tmp_path / "out").glob("media-work-*"))


def test_unconfigured_asr_never_produces_fake_transcript(audio_file, tmp_path):
    with pytest.raises(MediaError, match="음성인식") as exc:
        process_media(audio_file, tmp_path / "out", include_ocr=False, config=MediaConfig())
    assert exc.value.code == "ASR_NOT_CONFIGURED"
    assert not (tmp_path / "out" / "transcript.json").exists()


def test_asr_transport_omits_auto_language_and_protects_secret(tmp_path, monkeypatch):
    observed = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, maximum):
            return b'{"text":"stub transport response"}'

    class Opener:
        def open(self, request, timeout):
            observed.append(request)
            return Response()

    monkeypatch.setattr(media_core, "build_opener", lambda *args: Opener())
    chunk = tmp_path / "chunk.wav"
    chunk.write_bytes(b"bounded-test-audio")
    config = MediaConfig(asr_endpoint="https://example.invalid/audio/transcriptions", asr_model="unit-test",
                         asr_api_key="unit-test-only", data_policy_ack=True)
    assert media_core._request_asr(chunk, "auto", config)["text"] == "stub transport response"
    assert b'name="language"' not in observed[0].data
    assert b'name="file"' in observed[0].data
    assert b"unit-test-only" not in observed[0].data
    assert "unit-test-only" not in repr(config)


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -1, 999])
def test_bad_provider_timestamps_are_rejected(number):
    with pytest.raises(MediaError):
        media_core._normalize_segments({"segments": [{"start": number, "end": 1, "text": "bad"}]}, 0, 10)


def test_subtitle_source_text_cannot_inject_cues_or_markup():
    text = media_core.render_subtitles([{"start": 0, "end": 1, "text": "<script>x</script>\n\n00:00 --> 99:99"}])
    assert "<script>" not in text
    assert text.count("\n\n") <= 2
    assert "&lt;script&gt;" in text


def test_real_video_ocr_preserves_timestamped_evidence(tmp_path):
    if not all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe", "tesseract")):
        pytest.skip("local media tools not installed")
    from PIL import Image, ImageDraw, ImageFont
    image_path = tmp_path / "slide.png"
    image = Image.new("RGB", (1280, 720), "white")
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 70)
    ImageDraw.Draw(image).text((80, 280), "NEWS 2026 TEST", font=font, fill="black")
    image.save(image_path)
    path = tmp_path / "lecture.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-loop", "1", "-i", str(image_path), "-t", "2", "-pix_fmt", "yuv420p", "-y", str(path)], check=True)
    result = process_media(path, tmp_path / "out", config=MediaConfig(ocr_languages="eng", max_ocr_frames=1))
    assert result["asr_status"] == "not_applicable"
    assert result["ocr_status"] == "complete"
    assert "NEWS 2026 TEST" in result["visual_text"][0]["text"]
    assert result["visual_text"][0]["start"] == 1
    assert result["visual_text"][0]["timing"] == "sampled_instant"
    assert result["provenance"]["external_audio_transfer"] is False
    assert not result["segments"]
    assert result["analysis"]["number_mentions"]


def test_decoder_rejects_url_playlist_without_fetching(tmp_path):
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe not installed")
    source = tmp_path / "source.mp4"
    source.write_text("#EXTM3U\n#EXTINF:10,\nhttp://127.0.0.1/private\n")
    with pytest.raises(MediaError) as exc:
        media_core.probe_media(source, MediaConfig())
    assert exc.value.code == "MEDIA_DECODE_FAILED"
