# Multidomain Storage and API Contract

Status: design branch only. No migration has been executed.

## Database rule

The SQL file under db/multidomain is an inert design artifact. It is not imported by app/main.py and is not part of any Railway pre-deploy command.

Schemas:
- core
- life
- medical
- legal

Existing EduRecover education storage is intentionally not touched or remapped.

## Cross-domain rule

There are no automatic cross-domain database views.
Cross-domain access requires:
1. explicit consent,
2. explicit resource-scoped sharing grant,
3. audit event,
4. matching subject identity.

## API contracts

Pydantic request models reject unknown fields.
Medical patient-specific treatment purpose must not be inferred solely from official drug indications.
Legal deadlines start unverified and require source-backed verification.
Life activities may reference life/medical/legal only; this new layer does not claim ownership of education routing.

## Production gate

Do not execute the SQL or import these API models into production routers until:
- EduRecover problem-solving work is stable,
- latest main is incorporated,
- full existing regression tests pass,
- new multidomain tests pass,
- explicit owner approval is given.
