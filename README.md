# EduRecover AI v3.24.9

EduRecover AI is a verification-first educational AI system designed to generate, validate, recover, and safely release learning content under strict integrity controls.

The system is organized around three core architectures.

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

## 3. Socratic Trap

Socratic Trap is the learning-recovery architecture for diagnosing and correcting misconceptions.

The system converts a wrong answer into a structured error operator and generates a controlled sequence of questions that exposes contradiction, counterexample, or missing inference without simply revealing the answer.

Core ideas:

- Wrong answer -> error operator
- Error operator -> contradiction / counterexample prompt
- Safety and scope gate before presentation
- Guided reasoning through required semantic nodes
- Exit only after the target concept is reconstructed
- Isomorphic follow-up item to verify actual recovery

The mechanism is intended to distinguish accidental guessing from genuine conceptual repair.

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
