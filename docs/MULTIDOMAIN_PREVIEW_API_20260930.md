# Multidomain Preview API

This preview is intentionally not imported by EduRecover production app/main.py.

Purpose:
- exercise Life Recover, Medical and Legal request/response contracts,
- test domain and subject isolation,
- validate patient-specific medication-purpose behavior,
- validate source-backed legal deadline behavior.

Endpoints live under /preview/* and use in-memory stores only.

Required headers:
- X-Subject-Id
- X-Domain-Scope

The preview application is app/multidomain_preview.py and is not referenced by Railway production configuration.

Do not deploy this preview as the EduRecover production service.
