from enum import StrEnum


class SchemaName(StrEnum):
    CORE = "core"
    LIFE = "life"
    MEDICAL = "medical"
    LEGAL = "legal"


def schema_for_domain(domain: str) -> SchemaName:
    mapping = {
        "life": SchemaName.LIFE,
        "medical": SchemaName.MEDICAL,
        "legal": SchemaName.LEGAL,
    }
    try:
        return mapping[domain]
    except KeyError as exc:
        raise ValueError(f"unsupported_non_education_domain:{domain}") from exc
