# EduRecover Multidomain Platform Foundation

Status: branch-only / not production-wired.

Priority rule: EduRecover production stability always wins over Life Recover, Medical, or Legal integration.

## Safety boundary

- Do not modify existing education routes while the EduRecover problem-solving work is active.
- Do not modify `app/main.py` on this branch until explicit merge review.
- Do not deploy this branch to Railway production.
- New work is additive under:
  - `app/platform_core/`
  - `app/domains/life/`
  - `app/domains/medical/`
  - `app/domains/legal/`

## Target architecture

One shared backend can serve multiple app surfaces while preserving domain isolation.

- Core: identity, auth, consent, audit, notifications, file references, AI gateway.
- Education: existing EduRecover production paths.
- Life: Life Recover general-life features.
- Medical: medication, treatment-purpose, health-record and public-care integrations.
- Legal: case timeline, evidence, deadline and expert-review workflows.

## Data isolation

Recommended initial PostgreSQL schemas:

- `core.*`
- `education.*`
- `life.*`
- `medical.*`
- `legal.*`

Medical and legal information must not enter education-generation context automatically.
All cross-domain movement must be explicit, consented, auditable, and fail closed by default.

## Merge gate

This branch may be merged only after:

1. the latest `main` is incorporated,
2. existing EduRecover regression tests pass unchanged,
3. new domain-isolation tests pass,
4. no existing education route behavior changes,
5. production deployment receives explicit owner approval.
