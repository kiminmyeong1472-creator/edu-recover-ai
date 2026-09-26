# EduRecover Drive preservation status — 2026-09-26

## Verified inventory
- Source root: `교육플랫폼_비공개_마스터DB_영업비밀`
- Source root Drive ID: `1NYmOs9DRluSSbHeM568FDDEuA94MntuT`
- Recursively indexed: 17 unique folders / 177 unique files.
- Manifest: `app/private_assets/drive_archive_manifest.json`.

## Content actually installed in the private build source
- Existing curated private assets: 15 Drive-backed files.
- Additional immutable archive copies: 67 files under `app/private_assets/drive_archive/`.
- Total files with content installed: 82.
- Root direct files: 76 total; 72 content-installed, 4 held for secret scan.

## Not claimed as copied
- 4 root files are `PENDING_SECRET_SCAN` because they are chat/source/archive material that may contain secrets.
- Large encrypted or split Library backups are `REFERENCE_ONLY_BULK_BACKUP`; their Drive IDs, names and sizes are indexed, but their bytes are not copied into the application Git repository.
- Historical/superseded items not selected for runtime preservation are `REFERENCE_ONLY_DRIVE`.
- This is not a claim of a byte-for-byte copy of all Google Drive data, and it is not a Railway persistent-volume copy.

## Provider disclosure boundary
- Every `drive_archive` file is `never_send`.
- Only the previously curated assets explicitly marked `excerpt_only` may contribute task-scoped excerpts.
- Provider context remains single-request only, does not require persistent provider memory, and does not authorize providers to store the internal corpus.
- Chat history, secret values, unrelated customer data, archive files and full internal corpora are excluded from provider payloads.

## Integrity evidence
- The manifest records source Drive IDs, source sizes, storage state and Git blob IDs for archived copies.
- At runtime, the owner-only integrity endpoint computes SHA-256 for installed private/archive files.
- Detailed inventory is owner-only through `/owner/authoring-assets`; the teacher-safe endpoint does not enumerate internal files.

## Known blockers
- Railway currently has no persistent volume mount for this service; preserved files are carried through the private Git build source into the deployment image.
- The 4 `PENDING_SECRET_SCAN` files are not marked installed until a secret scan is completed.
- Provider-side ZDR confirmation and owner credential environment variables remain separate deployment-configuration checks.
