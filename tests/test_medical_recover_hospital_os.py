from app.domains.medical.hospital_os import (
    ClinicalAction,
    apply_action_completion,
    evaluate_prescription_change,
    make_actual_use_evidence,
    make_medication_execution_object,
    make_source_revision,
    selective_revalidation,
    utcnow,
    verify_patient_identity,
)


def _action(action_id: str, state: str, risk: str = "MEDIUM") -> ClinicalAction:
    return ClinicalAction(
        clinical_action_id=action_id,
        patient_id="patient-1",
        action_type="medication-review",
        responsible_role="NURSE",
        action_state=state,
        created_at=utcnow(),
        risk_tier=risk,
    )


def test_selective_revalidation_only_changes_actual_live_users():
    before = make_source_revision(
        source_id="rx-1",
        payload={"dose": "old"},
        verification_state="VERIFIED",
    )
    after = make_source_revision(
        source_id="rx-1",
        payload={"dose": "new"},
        predecessor_revision_id=before.source_revision_id,
        source_state="LATEST_UNCONFIRMED",
        verification_state="UNVERIFIED",
        changed_fields=["dose"],
    )
    affected = _action("act-affected", "INCOMPLETE", "HIGH")
    unrelated = _action("act-unrelated", "INCOMPLETE", "HIGH")
    historical = _action("act-historical", "CLOSED_HISTORICAL", "HIGH")
    evidence = make_actual_use_evidence(
        clinical_action_id=affected.clinical_action_id,
        source_revision_id=before.source_revision_id,
        manifest_type="execution_manifest",
        actor_or_system="nurse-ui",
        input_set={"revision": before.source_revision_id},
        context={"patient": "patient-1"},
        verification_state="VERIFIED",
    )
    historical_evidence = make_actual_use_evidence(
        clinical_action_id=historical.clinical_action_id,
        source_revision_id=before.source_revision_id,
        manifest_type="review_receipt",
        actor_or_system="archive",
        input_set={},
        context={},
        verification_state="VERIFIED",
    )

    result = selective_revalidation(
        source_before=before,
        source_after=after,
        actions=[affected, unrelated, historical],
        actual_use_evidence=[evidence, historical_evidence],
        dependency_state="UNRESOLVED",
    )
    states = {item.clinical_action_id: item.action_state for item in result.actions}

    assert states["act-affected"] == "HOLD"
    assert states["act-unrelated"] == "INCOMPLETE"
    assert states["act-historical"] == "CLOSED_HISTORICAL"
    assert result.receipt.affected_action_ids == ("act-affected",)
    assert set(result.receipt.unaffected_action_ids) == {"act-unrelated", "act-historical"}


def test_uncertain_source_cannot_be_marked_verified():
    try:
        make_source_revision(
            source_id="external-1",
            payload={"value": "unknown"},
            source_state="LATEST_UNCONFIRMED",
            verification_state="VERIFIED",
        )
    except ValueError as exc:
        assert str(exc) == "uncertain_source_cannot_be_auto_verified"
    else:
        raise AssertionError("uncertain source was incorrectly verified")



def test_patient_identity_blocks_completion_on_mismatch_or_missing_data():
    mismatch = verify_patient_identity(
        patient_id="patient-1",
        expected={"name": "Kim", "second_identifier": "1234"},
        observed={"name": "Lee", "second_identifier": "1234"},
    )
    assert mismatch.status == "MISMATCH"
    assert mismatch.completion_allowed is False

    unresolved = verify_patient_identity(
        patient_id="patient-1",
        expected={"name": "Kim", "second_identifier": "1234"},
        observed={"name": "Kim", "second_identifier": ""},
    )
    assert unresolved.status == "UNRESOLVED"
    assert unresolved.completion_allowed is False

    verified = verify_patient_identity(
        patient_id="patient-1",
        expected={"name": "Kim", "second_identifier": "1234"},
        observed={"name": "Kim", "second_identifier": "1234"},
    )
    assert verified.status == "VERIFIED"
    assert verified.completion_allowed is True


def test_prescription_change_holds_only_matching_unadministered_medication():
    medication = make_medication_execution_object(
        patient_id="patient-1",
        clinical_action_id="act-1",
        prescription_revision_id="rx-old",
        custody_stage="AT_BEDSIDE",
        prepared_fields={"drug": "A", "dose": "10mg"},
    )
    decision = evaluate_prescription_change(
        medication=medication,
        changed_from_revision_id="rx-old",
        changed_fields=["dose"],
        identity_status="VERIFIED",
    )
    assert decision.affected is True
    assert decision.next_state == "HOLD"
    assert decision.required_action == "BLOCK_ADMINISTRATION_AND_RECHECK"

    unrelated = evaluate_prescription_change(
        medication=medication,
        changed_from_revision_id="different-rx",
        changed_fields=["dose"],
        identity_status="VERIFIED",
    )
    assert unrelated.affected is False
    assert unrelated.next_state == "READY"

    administered = make_medication_execution_object(
        patient_id="patient-1",
        clinical_action_id="act-2",
        prescription_revision_id="rx-old",
        custody_stage="ADMINISTERED",
        prepared_fields={"drug": "A", "dose": "10mg"},
    )
    closed = evaluate_prescription_change(
        medication=administered,
        changed_from_revision_id="rx-old",
        changed_fields=["dose"],
        identity_status="VERIFIED",
    )
    assert closed.affected is False
    assert closed.next_state == "CLOSED"



def test_completion_gate_fails_closed_and_allows_verified_context():
    action = _action("act-complete", "INCOMPLETE", "HIGH")
    source = make_source_revision(
        source_id="rx-current",
        payload={"dose": "10mg"},
        verification_state="VERIFIED",
    )

    blocked_action, blocked = apply_action_completion(
        action=action,
        identity_status="UNRESOLVED",
        source_revisions=[source],
        actual_use_count=1,
        dependency_state="RESOLVED",
    )
    assert blocked.allowed is False
    assert "PATIENT_IDENTITY_NOT_VERIFIED" in blocked.blocked_reasons
    assert blocked_action.action_state == "INCOMPLETE"

    uncertain_source = make_source_revision(
        source_id="rx-uncertain",
        payload={"dose": "10mg"},
        source_state="LATEST_UNCONFIRMED",
        verification_state="UNVERIFIED",
    )
    _, uncertain = apply_action_completion(
        action=action,
        identity_status="VERIFIED",
        source_revisions=[uncertain_source],
        actual_use_count=1,
        dependency_state="RESOLVED",
    )
    assert uncertain.allowed is False
    assert "USED_SOURCE_NOT_VERIFIED" in uncertain.blocked_reasons
    assert "USED_SOURCE_NOT_CURRENTLY_USABLE" in uncertain.blocked_reasons

    completed_action, allowed = apply_action_completion(
        action=action,
        identity_status="VERIFIED",
        source_revisions=[source],
        actual_use_count=1,
        dependency_state="RESOLVED",
    )
    assert allowed.allowed is True
    assert completed_action.action_state == "CLOSED_HISTORICAL"
    assert completed_action.completed_at is not None
