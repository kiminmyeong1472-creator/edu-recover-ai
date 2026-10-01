from app.domains.medical.hospital_os import (
    ClinicalAction,
    make_actual_use_evidence,
    make_source_revision,
    selective_revalidation,
    utcnow,
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
