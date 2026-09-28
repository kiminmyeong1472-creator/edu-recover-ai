# EduRecover AI v3.24.9

EduRecover AI is a verification-first educational AI system designed to generate, validate, recover, and safely release learning content under strict integrity controls.

The teaching goal is structured mastery: show the overall learning map and the
conditional reasoning links from basic concepts to advanced applications.
AI teaching materials implement this goal through explanations, worked examples,
diagrams, audio, condition comparisons and separate independent assessments.
The following integrity architectures support that goal.

## 1. Trust Chain

Trust Chain is the release-control architecture for educational outputs.

Key principles:

- Blind solving before answer disclosure
- Independent validation rather than majority voting
- Exact binding between source item, answer, evidence, validator result, and release artifact
- Fail-closed behavior when required evidence or validation is missing
- Immutable evidence receipts and hash-linked execution history
- Explicit HOLD / RELEASE states
- Human approval only after machine validation gates are satisfied
- No release merely because multiple models agree

A typical validation path is:

```text
SOURCE_LOCKED
  -> BLIND_SOLVING
  -> GOLD_REVEALED
  -> ANSWER_GATE_PASSED
  -> SCHEMA_RECEIPT
  -> SEMANTIC_RECEIPT
  -> PACKAGE_MACHINE_PASS
  -> HUMAN_APPROVAL_PENDING
  -> RELEASE_READY
```

Any missing or conflicting requirement moves the item to `HOLD`.

## 2. Vaccine Graph

Vaccine Graph is the contamination-tracing and recovery architecture.

Instead of deleting suspicious history, the system preserves the original event chain and appends quarantine, invalidation, correction, and recovery events.

Core ideas:

- Event-sourced history
- Provenance and dependency graph
- Actual-use / actual-contact receipts
- Quarantine without evidence destruction
- Descendant contamination tracing
- Counterfactual recomputation after removing invalid ancestors
- Clean-room recovery verification
- Recovery clearance receipts
- Reinfection signatures for future prevention

A recovered artifact is not considered safe merely because it was regenerated. It must prove that no active path from a quarantined or invalid source reaches the final active output.

## 3. Optional Socratic diagnosis

Socratic questioning is one selectable aid for diagnosing misconceptions. It is
not required in every lesson, and explanations and worked examples may be
provided when a learner needs basic knowledge.

The system converts a wrong answer into a structured error operator and generates a controlled sequence of questions that exposes contradiction, counterexample, or missing inference without simply revealing the answer.

Core ideas:

- Wrong answer -> error operator
- Error operator -> contradiction / counterexample prompt
- Safety and scope gate before presentation
- Guided reasoning through required semantic nodes
- Exit only after the target concept is reconstructed
- Isomorphic follow-up item to verify actual recovery

These observations are separated from independent transfer evidence. Repeating
an explained item, watching a video or agreeing with an explanation does not
establish mastery. The optional legacy withholding policy applies only when
explicitly selected; it does not govern all AI teaching materials.

## 4. Structured AI teaching materials

Teacher materials bind premises, applicability conditions, inference rules,
conclusions and evidence to per-scenario revisions. A reviewed bundle binds its
scene, audio script, questions and answers. Editing conditions withdraws the old
publication; unreviewed or mismatched bundles cannot reach the student or board
display. Structural consistency checks and a teacher's content review are
distinct, and neither is a claim of automated mathematical proof or measured
learning efficacy. See `app/services/structured_materials.py`.

Execution requires owner authentication and an explicitly selected AI provider.
Independent verification uses one different provider for the whole authored
batch; failures do not silently switch providers. Exported DOCX files are marked
teacher-review drafts. API configuration readiness is not proof of a successful
live provider call.

The training plane records evidence and eligibility separately from actual
weight updates. Backup adapters and the opt-in 30-minute worker require verified
persistent storage and explicit Drive configuration; code existence alone is
not a successful production backup. See `docs/BACKUP_RUNTIME_20260928.md`.

## System Philosophy

EduRecover AI treats generation and validation as separate responsibilities.

External AI providers may generate candidate content, but no provider output becomes trusted merely because it was produced by a capable model. Every candidate must pass the same internal validation, provenance, integrity, and release gates.

The project follows these default rules:

- Trust is earned by evidence, not model identity.
- A negative finding is sticky until explicitly resolved.
- Missing provenance is a blocking defect.
- Majority agreement cannot override failed evidence.
- Recovery must be graph-verifiable.
- Student-facing output must be generated only from an active, validated revision.

## Version

Current foundation version: **v3.24.9**

This repository is private and contains the evolving implementation and architecture documentation for EduRecover AI.
