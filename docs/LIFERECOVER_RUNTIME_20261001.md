# Life Recover Runtime

Status: integration branch only until review.

## Backend sharing
Life Recover uses the existing EduRecover FastAPI deployment and persistent Railway volume. It does not require a second application server for the first production phase.

## Authentication
- invite-gated registration
- scrypt password hashing with random per-user salt
- secure HttpOnly SameSite=Strict session cookie
- server-side account existence check
- configurable session secret required

## Persistent storage
Four separate SQLite databases beneath the existing persistent state root:
- accounts.sqlite3
- life.sqlite3
- medical.sqlite3
- legal.sqlite3

Life/medical/legal payloads are encrypted before SQLite storage using AES-256-GCM and a deployment secret LIFERECOVER_DATA_KEY.

## Isolation
A logged-in account can only read resources whose subject_id equals its account_id. Medical and legal records live in different physical SQLite files.

## First UI
/life is the Life Recover mobile-first entry page. It exposes Life, Medical, Legal and privacy areas while using the shared backend.

## Production requirements
Before deployment set:
- LIFERECOVER_SESSION_SECRET
- LIFERECOVER_REGISTRATION_INVITE
- LIFERECOVER_DATA_KEY

Do not place secret values in Git, source documents, prompts or status responses.
