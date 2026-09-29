# Multidomain Functional Slice

Branch-only. Not production-wired.

## Life activity ledger
Activities are append-only. State changes create successor events instead of mutating history.

## Medical
The medication passport keeps:
- medicine/product and ingredient,
- prescribing organization and department,
- prescription date,
- patient-specific treatment purpose when actually supported,
- verification state,
- current/stopped/completed/unknown status,
- safety notes as notes, not an overprescribing diagnosis.

Official label indications are kept separate from the reason a particular patient actually received a medicine. The application must not infer patient-specific purpose solely from a drug label.

## Legal
Case timeline events preserve:
- case and subject scope,
- procedural stage,
- event type,
- source reference,
- successor revision link.

Mixed case or subject scopes fail closed.

## Production gate
None of these modules are imported by app/main.py. They remain inert until explicit merge review after EduRecover regression testing.
