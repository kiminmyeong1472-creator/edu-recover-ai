from datetime import datetime, timedelta, timezone

import app.liferevolve_burst as burst
from tests.test_liferevolve_screen import HEADERS, client_for, image_bytes, payload


def test_gesture_rules():
    a = {"t_ms": 0, "x": 100, "y": 100}
    assert burst.classify_tap(None, a) == "single"
    assert burst.classify_tap(a, {"t_ms": 300, "x": 120, "y": 110}) == "double"
    assert burst.classify_tap(a, {"t_ms": 80, "x": 100, "y": 100}) == "duplicate"
    assert burst.classify_tap(a, {"t_ms": 300, "x": 200, "y": 100}) == "single"
    assert burst.classify_tap(a, {"t_ms": 500, "x": 100, "y": 100}) == "single"
    assert burst.classify_tap(a, {"t_ms": 900, "x": 0, "y": 0, "long": True}) == "close"
    assert burst.missing_sequences([1, 2, 5]) == [3, 4]


def test_recognition_status():
    now = datetime.now(timezone.utc)
    f = {"tapped_at": now.isoformat(), "sha256": "abc"}
    assert burst.recognition_status(None, False, "abc", now) == "NO_FRAME"
    assert burst.recognition_status(f, False, "abc", now) == "NOT_SENT_TO_AI"
    assert burst.recognition_status(f, True, "zzz", now) == "PAST_SCREEN"
    assert burst.recognition_status(f, True, "abc", now + timedelta(seconds=4)) == "PAST_SCREEN"
    assert burst.recognition_status(f, True, "abc", now + timedelta(seconds=1)) == "CURRENT"


def frame(seq, tap_ms):
    return {**payload(image_bytes()), "seq": seq, "tap_ms": tap_ms}


def test_burst_flow_dedupe_missing_grants_and_check(monkeypatch, tmp_path):
    c = client_for(monkeypatch, tmp_path)
    r = c.post("/life/api/burst", json={"tap_ms": 1000}, headers=HEADERS)
    assert r.status_code == 201, r.text
    bid = r.json()["burst"]["burst_id"]
    assert c.post(f"/life/api/burst/{bid}/frames", json=frame(1, 1000), headers=HEADERS).status_code == 201
    dup = c.post(f"/life/api/burst/{bid}/frames", json=frame(2, 1050), headers=HEADERS)
    assert dup.json() == {"accepted": False, "reason": "DUPLICATE_TAP"}
    r3 = c.post(f"/life/api/burst/{bid}/frames", json=frame(3, 1600), headers=HEADERS)
    assert r3.status_code == 201
    assert r3.json()["burst"]["missing_sequences"] == [2]
    sha = r3.json()["sha256"]
    # No AI approval → nothing released, claim reported as not sent.
    assert c.post(f"/life/api/burst/{bid}/deliver", json={"recipient": "ai", "seqs": [3]}, headers=HEADERS).status_code == 403
    assert c.post(f"/life/api/burst/{bid}/recognition-check", json={"sha256": sha}, headers=HEADERS).json()["status"] == "NOT_SENT_TO_AI"
    g = c.post(f"/life/api/burst/{bid}/grants", json={"recipient": "ai", "purpose": "문제 풀이 도움", "minutes": 5}, headers=HEADERS)
    assert g.status_code == 201
    d = c.post(f"/life/api/burst/{bid}/deliver", json={"recipient": "ai", "seqs": [2, 3]}, headers=HEADERS).json()
    assert d["delivered"] == [3] and d["missing"] == [2]
    assert c.post(f"/life/api/burst/{bid}/recognition-check", json={"sha256": sha}, headers=HEADERS).json()["status"] == "CURRENT"
    assert c.post(f"/life/api/burst/{bid}/recognition-check", json={"sha256": "0" * 64}, headers=HEADERS).json()["status"] == "PAST_SCREEN"
    # Approval for AI does not cover the chat; revoked approval stops release.
    assert c.post(f"/life/api/burst/{bid}/deliver", json={"recipient": "chat", "seqs": [3]}, headers=HEADERS).status_code == 403
    assert c.delete(f"/life/api/burst/{bid}/grants/{g.json()['grant_id']}", headers=HEADERS).status_code == 204
    assert c.post(f"/life/api/burst/{bid}/deliver", json={"recipient": "ai", "seqs": [3]}, headers=HEADERS).status_code == 403
    closed = c.post(f"/life/api/burst/{bid}/close", headers=HEADERS).json()["burst"]
    assert closed["status"] == "CLOSED" and closed["close_reason"] == "LONG_PRESS"
    assert c.post(f"/life/api/burst/{bid}/frames", json=frame(4, 9000), headers=HEADERS).status_code == 409


def test_idle_close_and_header_and_owner(monkeypatch, tmp_path):
    c = client_for(monkeypatch, tmp_path)
    assert c.post("/life/api/burst", json={"tap_ms": 1}).status_code == 403
    bid = c.post("/life/api/burst", json={"tap_ms": 1}, headers=HEADERS).json()["burst"]["burst_id"]
    later = datetime.now(timezone.utc) + timedelta(seconds=6)
    monkeypatch.setattr(burst, "_now", lambda: later)
    got = c.get(f"/life/api/burst/{bid}").json()["burst"]
    assert got["status"] == "CLOSED" and got["close_reason"] == "IDLE"
    other = client_for(monkeypatch, tmp_path, username="other-user")
    assert other.get(f"/life/api/burst/{bid}").status_code == 404


def black_png():
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (60, 120), "#000000").save(out, format="PNG")
    return out.getvalue()


def test_protected_screen_never_stored_and_chain_detects_tampering(monkeypatch, tmp_path):
    import sqlite3
    c = client_for(monkeypatch, tmp_path)
    bid = c.post("/life/api/burst", json={"tap_ms": 1000}, headers=HEADERS).json()["burst"]["burst_id"]
    ok = c.post(f"/life/api/burst/{bid}/frames", json=frame(1, 1000), headers=HEADERS).json()
    sha = ok["sha256"]
    blocked = c.post(f"/life/api/burst/{bid}/frames", json={**payload(black_png()), "seq": 2, "tap_ms": 1600}, headers=HEADERS).json()
    assert blocked["accepted"] is False and blocked["reason"] == "PROTECTED_SCREEN"
    dev = c.post(f"/life/api/burst/{bid}/frames", json={"seq": 3, "tap_ms": 2200, "protected": True}, headers=HEADERS).json()
    assert dev["reason"] == "PROTECTED_SCREEN"
    b = dev["burst"]
    assert b["protected_sequences"] == [2, 3] and b["missing_sequences"] == [] and len(b["frames"]) == 1
    assert len(c.get("/life/api/screens").json()["screens"]) == 1  # black frame never stored
    c.post(f"/life/api/burst/{bid}/grants", json={"recipient": "ai", "purpose": "도움", "minutes": 5}, headers=HEADERS)
    c.post(f"/life/api/burst/{bid}/deliver", json={"recipient": "ai", "seqs": [1]}, headers=HEADERS)
    st = c.post(f"/life/api/burst/{bid}/recognition-check", json={"sha256": sha}, headers=HEADERS).json()["status"]
    assert st == "PROTECTED_SCREEN"
    led = c.get(f"/life/api/burst/{bid}/ledger").json()
    events = [r["event"] for r in led["records"]]
    assert events[:4] == ["OPENED", "FRAME", "PROTECTED_BLOCKED", "PROTECTED_BLOCKED"]
    assert "GRANTED" in events and "DELIVERED" in events and events[-1] == "RECOGNITION_CHECK"
    assert led["verification"]["intact"] is True
    # Someone edits a stored record afterwards → the chain shows exactly where.
    db = sqlite3.connect(tmp_path / "liferecover" / "life.sqlite3")
    db.execute("UPDATE liferevolve_burst_ledger SET body=? WHERE burst_id=? AND idx=2", ('{"by":"server","seq":2,"tap_ms":9}', bid))
    db.commit(); db.close()
    v = c.get(f"/life/api/burst/{bid}/ledger").json()["verification"]
    assert v == {"intact": False, "broken_at": 2, "reason": "HASH_MISMATCH"}
    db = sqlite3.connect(tmp_path / "liferecover" / "life.sqlite3")
    db.execute("DELETE FROM liferevolve_burst_ledger WHERE burst_id=? AND idx=1", (bid,))
    db.commit(); db.close()
    assert c.get(f"/life/api/burst/{bid}/ledger").json()["verification"]["reason"] == "INDEX_GAP"


def test_training_provenance_and_answer_binding(monkeypatch, tmp_path):
    import sqlite3
    c = client_for(monkeypatch, tmp_path)
    bid = c.post("/life/api/burst", json={"tap_ms": 1000}, headers=HEADERS).json()["burst"]["burst_id"]
    s1 = c.post(f"/life/api/burst/{bid}/frames", json=frame(1, 1000), headers=HEADERS).json()["sha256"]
    s2 = c.post(f"/life/api/burst/{bid}/frames", json={**payload(image_bytes(size=(61, 120))), "seq": 2, "tap_ms": 1600}, headers=HEADERS).json()["sha256"]
    assert s1 != s2
    c.post(f"/life/api/burst/{bid}/frames", json={"seq": 3, "tap_ms": 2200, "protected": True}, headers=HEADERS)
    use = {"seqs": [1, 3], "model_id": "튜닝모델-1", "dataset_id": "자료-10월"}
    # Looking (ai) is not learning (train).
    c.post(f"/life/api/burst/{bid}/grants", json={"recipient": "ai", "purpose": "풀이", "minutes": 5}, headers=HEADERS)
    assert c.post(f"/life/api/burst/{bid}/training-use", json=use, headers=HEADERS).status_code == 403
    tg = c.post(f"/life/api/burst/{bid}/grants", json={"recipient": "train", "purpose": "개인 맞춤 학습", "minutes": 5}, headers=HEADERS).json()
    u = c.post(f"/life/api/burst/{bid}/training-use", json=use, headers=HEADERS).json()
    assert [f["seq"] for f in u["used"]] == [1] and u["skipped"] == [3]  # protected frame never learned
    p1 = c.get(f"/life/api/burst/{bid}/training-proof", params={"sha256": s1}).json()
    assert p1["verdict"] == "USED_FOR_TRAINING" and p1["uses"][0]["model_id"] == "튜닝모델-1"
    assert c.get(f"/life/api/burst/{bid}/training-proof", params={"sha256": s2}).json()["verdict"] == "NOT_USED_FOR_TRAINING"
    assert c.get(f"/life/api/burst/{bid}/training-proof", params={"sha256": "f" * 64}).json()["verdict"] == "UNKNOWN_FRAME"
    # Answer binding: only frames really delivered to the AI count as evidence.
    c.post(f"/life/api/burst/{bid}/deliver", json={"recipient": "ai", "seqs": [1]}, headers=HEADERS)
    a = c.post(f"/life/api/burst/{bid}/answers", json={"answer": "정답은 3번입니다.", "cited_sha256": [s1]}, headers=HEADERS).json()
    assert a["overall"] == "GROUNDED"
    b = c.post(f"/life/api/burst/{bid}/answers", json={"answer": "화면에 4번이 보입니다.", "cited_sha256": [s1, s2, "0" * 64]}, headers=HEADERS).json()
    assert [x["status"] for x in b["citations"]] == ["GROUNDED", "NOT_DELIVERED_TO_AI", "UNKNOWN_FRAME"] and b["overall"] == "UNGROUNDED"
    assert c.post(f"/life/api/burst/{bid}/answers", json={"answer": "그냥 압니다.", "cited_sha256": []}, headers=HEADERS).json()["overall"] == "NO_EVIDENCE"
    # Revoking learning approval: further learning refused, deletion required then confirmed.
    gid = tg["grant_id"]
    assert c.post(f"/life/api/burst/{bid}/grants/{gid}/deletion-confirmed", headers=HEADERS).status_code == 409
    c.delete(f"/life/api/burst/{bid}/grants/{gid}", headers=HEADERS)
    assert c.post(f"/life/api/burst/{bid}/training-use", json=use, headers=HEADERS).status_code == 403
    assert c.post(f"/life/api/burst/{bid}/grants/{gid}/deletion-confirmed", headers=HEADERS).status_code == 200
    led = c.get(f"/life/api/burst/{bid}/ledger").json()
    ev = [r["event"] for r in led["records"]]
    for e in ["TRAINING_REFUSED", "TRAINING_USE", "ANSWER_BOUND", "REVOKED", "DELETION_CONFIRMED"]:
        assert e in ev
    assert led["verification"]["intact"] and "정답은" not in str(led["records"])  # answer text not kept, only hash
    # Erasing the training record afterwards is detected; no proof is given from a broken chain.
    idx = ev.index("TRAINING_USE")
    db = sqlite3.connect(tmp_path / "liferecover" / "life.sqlite3")
    db.execute("UPDATE liferevolve_burst_ledger SET event='TRAINING_REFUSED' WHERE burst_id=? AND idx=?", (bid, idx))
    db.commit(); db.close()
    assert c.get(f"/life/api/burst/{bid}/training-proof", params={"sha256": s1}).json()["verdict"] == "CHAIN_BROKEN"
