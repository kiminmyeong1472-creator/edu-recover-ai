# EduRecover Multidomain Platform Foundation

Status: branch-only / not production-wired.

Priority: EduRecover production stability always wins.

## Current boundary
- No modification to app/main.py.
- No modification to existing education routes or services.
- No Railway production deployment from this branch.
- New code only under app/platform_core and app/domains.

## Domains
- education: existing EduRecover production code
- life: Life Recover
- medical: medication/treatment-purpose/health integrations
- legal: case/evidence/deadline workflows

## Shared platform primitives
- domain scope
- consent receipt
- audit event

## Data separation target
- core.*
- education.*
- life.*
- medical.*
- legal.*

Medical and legal information must never flow automatically into education-generation context.

## Merge gate
Merge only after latest main is incorporated, all existing EduRecover regression tests pass, new isolation tests pass, and the owner explicitly approves production deployment.
