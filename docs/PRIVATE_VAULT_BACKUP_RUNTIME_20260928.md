# Private teaching vault, backup and learning-evidence runtime

This change connects owner editing to the actual authoring payload. It does not start GPU training or use student evidence to train an assessment-item generator.

## Runtime data

- Existing classroom volume: `/app/.edurecover_classroom_state`.
- `secret_vault.sqlite3`: immutable asset revisions, approved sections, purpose/provider scopes and disclosure receipts.
- `classroom_tutor.sqlite3`: original classroom responses and exposed material snapshots.
- `ai_tutor_training.sqlite3`: pseudonymous evidence, rewards, eligibility revisions and use receipts.
- Import only the enumerated legacy authoring/private assets. Preserve original files; never rescan chat archives or credential containers. Legacy `excerpt_only` permissions migrate once, while `never_send` stays blocked. Subsequent deployments never overwrite owner edits or restore revoked approval.
- New or edited sections require owner approval. The mobile console clears approval when text or scope changes. The server validates the submitted scope; optimistic revision checks prevent stale overwrites.

## External AI disclosure

Owner screen: `/owner/secrets`; existing owner login and same-origin protections apply.
Only matching approved sections are selected from the latest DB revision. Scope checks cover provider, purpose, subject, optional grade and optional question type. No-match does not fall back to document prefixes and does not launch generation/verification. Each section is at most 1,800 characters, at most two sections per asset and 12,000 characters per request. These are operational disclosure caps, not teaching reward weights.

Keyword matching is a bounded selector, not proof of semantic necessity. The owner can preview the selected material and narrow section criteria. Preview does not call an AI. Receipt states distinguish PREPARED, NO_MATCH, COMPLETED and FAILED_OR_UNCERTAIN. A failed response is not proof the provider received nothing. Receipt metadata stores hashes and revision references, not student text.

The tutor purpose can be reviewed in the selector but has no new external tutor dispatch endpoint in this change. Production generation and independent verification use the DB selector. No provider settings, access policy or retention attestations are changed by the vault.

## Encrypted backups

On startup register the three SQLite databases for online SQLite backup snapshots, not raw copies of an active WAL database. Each database snapshot is integrity checked. Snapshots are individually consistent; they are not a distributed transaction across both classroom and ledger databases.

Encrypt with the existing owner backup credential; never replace it, send it to the browser or substitute an AI key. The existing authenticated encryption format is reused. Read back server and Drive ciphertext and verify hashes; decrypt the same authenticated ciphertext locally and compare original snapshot hashes before reporting success.

The scheduler runs every 30 minutes only after configured consent. Retain three verified local copies per asset; only app-generated ciphertext within the backup root can be rotated after destination verification. The local encrypted backup budget is 100 MiB; snapshots additionally check free space. Capacity and transport failures report failure, never false success. Drive backups are not automatically deleted.

This is an allowlisted database backup, not a backup of the entire Google Drive or every Railway file. Credentials, `.env`, OAuth secrets and files outside the explicit manifest are excluded. Restore checks do not overwrite the live databases.

## Personal Google Drive setup remaining with the owner

The connected ChatGPT Drive app is not a server OAuth credential. The existing personal 5 TB Drive uses owner OAuth. The new flow requests only `https://www.googleapis.com/auth/drive.file`, creates its own private backup folders and never changes sharing permissions.

1. In the owner's Google Cloud project enable Drive API: https://console.cloud.google.com/apis/library/drive.googleapis.com
2. Create a **Web application** OAuth client: https://console.cloud.google.com/auth/clients
3. Register exactly `https://edurecover-prod-production.up.railway.app/owner/drive/callback` as the authorized redirect URI and configure the consent audience/test users as appropriate.
4. Put the generated client ID and client secret in Railway service secrets as `EDURECOVER_DRIVE_CLIENT_ID` and `EDURECOVER_DRIVE_CLIENT_SECRET`. Do not paste them into chat or commit them.
5. Open `/owner/secrets`, choose Google Drive connection, and personally approve Google's consent screen. The app then enables its backup worker. Inspect a verified receipt rather than assuming consent means backup succeeded.

Consent uses a single-use, ten-minute state bound to a secure browser cookie and PKCE. Refresh tokens are encrypted in `oauth_private/drive_oauth.edurbk`, outside the content backup manifest. Existing encryption credentials must remain available for decryption; changing a key/password does not migrate old backups. Reconnection with another account is rejected if it cannot own the existing folders.

Primary references checked 2026-09-28:
- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/workspace/drive/api/guides/api-specific-auth

## Learning and training

Existing teacher validation, independent follow-up, defective-content exclusion, evidence revisions and purpose eligibility remain in place. Accumulating evidence metadata does not constitute SFT or RL training. No learner evidence enters the secret-vault migration.

The existing GPU script is SFT/LoRA, not an RL worker. It now requires explicit approved source classes, rights/consent review, TUTOR_POLICY scope, the existing first-party tutoring review gate, a matching file hash/count and real assistant-target conversations. Metadata-only snapshots cannot be passed off as a training corpus. `--check-only` checks data and recipe without importing GPU libraries or starting a job.

A reviewed materialized training corpus, GPU environment and actual training/evaluation run remain required. No paid GPU resource is provisioned here. Product implementation details stay in the private repository and owner data store; patent documents are not altered.
