# Account database recovery

The runtime backup inventory includes `liferecover/accounts.sqlite3`. Account
identifiers, usernames, password verifiers, salts and creation times are needed
to restore login and the ownership links in the Life/Medical/Legal stores.

Only this canonical account DB is allowed. Other account DB paths, raw SQLite
sidecars, deployment keys, OAuth tokens and credential files remain excluded.
SQLite's online backup captures committed WAL pages in a private snapshot.
The snapshot follows the existing AES-256-GCM encrypted server and Drive path,
including ciphertext readback and decrypt/hash verification. Even manually
registered account entries are restricted personal data, never public/general
content or training records. A metadata-only account entry fails explicitly
with `ACCOUNT_DATABASE_CONTENT_BACKUP_REQUIRED`; it cannot report recovery
success. Correct that manifest flag only after confirming it points to the
canonical application DB.

The change does not enable backups, configure Drive credentials, rewrite owner
manifests or change deployment secrets. The worker still requires its existing
enablement, encryption and destination checks to pass.

For recovery, preserve the backup decryption credential and `LIFERECOVER_DATA_KEY`
through a separate protected process. Restore the account DB and associated
domain DBs into an isolated environment, provision a session-signing secret,
and require a fresh login. Verify that the original account ID owns the restored
resources and that other accounts cannot read them. Creating replacement
accounts with new IDs is not a substitute. Keep legacy decryption credentials
until their backups have been migrated and verified.

Automated tests use synthetic accounts, real encryption/decryption and mocked
Drive transport. They verify login, unchanged account IDs, resource access and
cross-account denial for both supported encryption formats. They do not prove
a production backup ran or establish a whole-service atomic snapshot; runtime
coverage continues to report `full_service_restore_verified: false`.
