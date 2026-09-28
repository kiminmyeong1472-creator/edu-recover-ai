"""Only the versioned DB selector can supply private provider context."""
from app.services import authoring_assets as base
from app.services.secret_vault import SecretVault


def build_authoring_context(*, subject, grade, source_text, question_types, provider=""):
    selection = SecretVault().select(provider=provider, purpose="generate", subject=subject,
                                    grade=grade, source_text=source_text, question_types=question_types)
    return {"delivery_scope": "single_request_only", "purpose": "generate",
            "privacy_boundary": "Use only the authorized excerpts supplied for this task.",
            "selection": selection}


def build_verification_context(problems, provider=""):
    source = "\n".join(str(p.get("passage") or p.get("evidence") or "") for p in problems)
    qtypes = [str(p.get("question_type") or "") for p in problems]
    selection = SecretVault().select(provider=provider, purpose="verify", subject="영어",
                                    grade="", source_text=source, question_types=qtypes)
    return {"delivery_scope": "single_request_only", "purpose": "verify", "selection": selection}


base.build_authoring_context = build_authoring_context
base.build_verification_context = build_verification_context
