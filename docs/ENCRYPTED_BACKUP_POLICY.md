# EduRecover encrypted backup policy

## Purpose

EduRecover keeps backup encryption separate from external AI provider authentication.

- Provider API keys authenticate calls to OpenAI, Gemini, or Claude.
- `EDURECOVER_BACKUP_MASTER_KEY` is used only to protect EduRecover backup data.
- These values must never be reused for each other.

## Terms

- **Master encryption key**: the top-level 256-bit secret held by the deployment secret store.
- **DEK (Data Encryption Key)**: a fresh random 256-bit key generated for each encrypted file.
- **Envelope encryption**: the file is encrypted with its DEK, and the DEK is separately encrypted with the master key.
- **AES-256-GCM**: authenticated encryption that protects confidentiality and detects alteration.
- **Sealed environment variable**: a deployment variable whose value is hidden from ordinary UI/API reads after it is stored.

## Required deployment variables

```text
EDURECOVER_BACKUP_ENCRYPTION_REQUIRED=true
EDURECOVER_BACKUP_MASTER_KEY=<32-byte secret encoded as base64url, or hex:<64 hex chars>>
```

The master-key value must be entered by the human owner directly into the deployment secret store.
It must not be pasted into chat, committed to GitHub, stored in Google Drive beside the ciphertext,
returned from an API, written to logs, or forwarded to an external AI provider.

## File-encryption flow

1. Read the master key from the deployment secret environment.
2. Generate a new random DEK for the file.
3. Encrypt the file as a stream with AES-256-GCM.
4. Encrypt ("wrap") the DEK with the master key.
5. Store only the encrypted file plus its wrapped DEK and non-secret format metadata.
6. Record file identity, revision, hashes, disclosure policy, and backup state in the internal registry/DB.
7. Upload only the encrypted backup artifact to the approved backup storage.

The implementation is in `app/services/encrypted_backup.py`.

## Fail-closed behavior

If backup encryption is required and the master key is missing or malformed:

- encryption does not start;
- decryption does not start;
- the service reports only non-secret readiness information;
- no API/provider key is substituted as an encryption key.

Owner-only readiness is available at:

```text
/owner/backup-encryption-status
```

This endpoint must never return the master-key value.

## Backup/storage separation

Recommended architecture:

1. Google Drive: encrypted disaster-recovery backup.
2. EduRecover private storage: operational AI-development/source assets.
3. EduRecover private DB/registry: file IDs, versions, hashes, classification, and provider-delivery policy.
4. External AI providers: task-scoped excerpts only; no master key, no full backup corpus, no unrelated internal material.

## Key rotation

Do not delete an old master key until every wrapped DEK protected by that key has been rewrapped or
all backups using it have been re-encrypted and verified. Rotation status must be tracked explicitly.

## Recovery rule

A backup is not considered recoverable merely because an encrypted object exists. Recovery is verified only
after an authorized test successfully decrypts the object, validates its authentication tag, and matches the
expected plaintext hash.
