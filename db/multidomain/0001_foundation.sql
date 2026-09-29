-- DESIGN-ONLY SQL. NOT WIRED TO RAILWAY PRODUCTION.
-- EduRecover education storage is intentionally untouched.

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS life;
CREATE SCHEMA IF NOT EXISTS medical;
CREATE SCHEMA IF NOT EXISTS legal;

CREATE TABLE IF NOT EXISTS core.subject (
    subject_id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.consent_receipt (
    consent_id UUID PRIMARY KEY,
    subject_id UUID NOT NULL REFERENCES core.subject(subject_id),
    domain TEXT NOT NULL CHECK (domain IN ('life','medical','legal')),
    purpose TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS core.audit_event (
    audit_id UUID PRIMARY KEY,
    subject_id UUID NOT NULL REFERENCES core.subject(subject_id),
    actor_id TEXT NOT NULL,
    domain TEXT NOT NULL CHECK (domain IN ('life','medical','legal')),
    action TEXT NOT NULL,
    resource_ref TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS core.sharing_grant (
    grant_id UUID PRIMARY KEY,
    subject_id UUID NOT NULL REFERENCES core.subject(subject_id),
    source_domain TEXT NOT NULL CHECK (source_domain IN ('life','medical','legal')),
    target_party TEXT NOT NULL,
    purpose TEXT NOT NULL,
    resource_refs JSONB NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS life.activity_event (
    activity_id UUID PRIMARY KEY,
    subject_id UUID NOT NULL REFERENCES core.subject(subject_id),
    domain TEXT NOT NULL CHECK (domain IN ('life','medical','legal')),
    action TEXT NOT NULL,
    resource_ref TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('OPEN','DONE','CANCELLED','HOLD')),
    source_ref TEXT,
    parent_activity_id UUID REFERENCES life.activity_event(activity_id),
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS life.public_support_program (
    program_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL CHECK (domain IN ('MEDICAL','WELFARE','LEGAL','LIFE')),
    title TEXT NOT NULL,
    provider TEXT NOT NULL,
    official_source_ref TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('OPEN','UPCOMING','CLOSED','UNKNOWN')),
    application_start TIMESTAMPTZ,
    application_end TIMESTAMPTZ,
    eligibility_text TEXT,
    location_text TEXT,
    reservation_required BOOLEAN
);

CREATE TABLE IF NOT EXISTS medical.medication_record (
    medication_id TEXT PRIMARY KEY,
    subject_id UUID NOT NULL REFERENCES core.subject(subject_id),
    product_name TEXT NOT NULL,
    ingredient_name TEXT,
    dose_text TEXT,
    prescribing_org TEXT,
    department TEXT,
    prescribed_at TIMESTAMPTZ,
    approved_indications JSONB NOT NULL DEFAULT '[]'::jsonb,
    actual_treatment_purpose TEXT,
    user_reported_purpose TEXT,
    verification_state TEXT NOT NULL CHECK (
        verification_state IN ('OFFICIAL','DOCUMENT','USER_CONFIRMED','UNCONFIRMED')
    ),
    source_ref TEXT,
    source_revision TEXT
);

CREATE TABLE IF NOT EXISTS medical.safety_signal (
    signal_id TEXT PRIMARY KEY,
    subject_id UUID NOT NULL REFERENCES core.subject(subject_id),
    medication_refs JSONB NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('INFO','REVIEW','URGENT_REVIEW')),
    source_ref TEXT NOT NULL,
    explanation TEXT NOT NULL,
    clinician_or_pharmacist_review_required BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS legal.case_event (
    event_id UUID PRIMARY KEY,
    case_id TEXT NOT NULL,
    subject_id UUID NOT NULL REFERENCES core.subject(subject_id),
    stage TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    source_ref TEXT,
    supersedes_event_id UUID REFERENCES legal.case_event(event_id),
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_legal_case_event_scope
    ON legal.case_event(subject_id, case_id, occurred_at);

CREATE TABLE IF NOT EXISTS legal.deadline (
    deadline_id UUID PRIMARY KEY,
    case_id TEXT NOT NULL,
    subject_id UUID NOT NULL REFERENCES core.subject(subject_id),
    title TEXT NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    source_ref TEXT NOT NULL,
    legal_basis_ref TEXT,
    state TEXT NOT NULL CHECK (state IN ('UNVERIFIED','VERIFIED','COMPLETED','MISSED','HOLD')),
    verification_note TEXT
);

-- Deliberately no automatic cross-domain views or joins.
-- Cross-domain sharing must be application-mediated through explicit consent
-- and sharing grants with audit events.
