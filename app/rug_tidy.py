"""Tidy recognised label text into an ingredient list, with a verifiable guard.

Character recognition returns text broken by line wraps and stray marks. A
language model is good at putting that back into a list. It is also capable of
producing a plausible ingredient name that was never on the label, which in this
service would be an invented ingredient.

So the model's output is not trusted on its own: every name it returns must be
traceable to characters present in the recognised text. A name that is not is
discarded and reported. The model never decides whether an allergen is present;
that stays with the rule comparison in app.rug_formula.
"""
from __future__ import annotations

import json
import re
import unicodedata

from app.core.config import settings
from app.services.privacy_guard import assert_provider_zdr

MAX_INGREDIENTS = 400
MAX_TEXT = 20000
MIN_RUN = 2

SYSTEM = (
    'You repair an ingredient list that optical character recognition produced '
    'from a photograph of a cosmetic label. Join names broken across lines, drop '
    'stray marks and page furniture, and keep the printed order. '
    'Use only characters present in the supplied text. Never translate, never '
    'correct spelling to a name you expect, never add an ingredient that is not '
    'in the text, and never remove one that is. If a fragment is too damaged to '
    'read, keep it as it stands rather than guessing what it was. '
    'A numeric locant inside one name, such as 1,2-Hexanediol, stays one name. '
    'Reply with JSON only: {"ingredients": ["...", "..."]}'
)


def _norm(value: str) -> str:
    return re.sub(r'[\s·.;:/|()\[\]{}<>_-]+', '', unicodedata.normalize('NFKC', value)).casefold()


def available() -> bool:
    return settings.anthropic_api_key is not None


def verify_against_text(candidates, text: str) -> tuple[list[str], list[str]]:
    """Keep only names whose characters appear in the recognised text.

    This is the guard that makes the tidy step safe to use: a name the model
    produced but recognition never saw is an invented ingredient, so it is
    dropped and reported rather than compared against the person's allergens.
    """
    source = _norm(text)
    kept, dropped, seen = [], [], set()
    for value in candidates if isinstance(candidates, list) else []:
        if not isinstance(value, str):
            continue
        name = re.sub(r'\s+', ' ', value).strip(' .;·/|')
        if not name or len(name) > 200 or any(ord(c) < 32 for c in name):
            continue
        key = _norm(name)
        if len(key) < MIN_RUN or key not in source:
            dropped.append(name)
            continue
        if key in seen:
            continue
        seen.add(key)
        kept.append(name)
        if len(kept) >= MAX_INGREDIENTS:
            break
    return kept, dropped


def tidy(text: str, fallback: list[str]) -> dict:
    """Return {'ingredients', 'applied', 'dropped'}.

    On any failure the recognised split in `fallback` is used unchanged, so the
    tidy step can never be the reason an analysis does not run.
    """
    plain = (text or '')[:MAX_TEXT]
    if not plain.strip() or not available():
        return {'ingredients': fallback, 'applied': False, 'dropped': []}

    try:
        assert_provider_zdr('claude')
        from anthropic import Anthropic
        client = Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
        try:
            message = client.messages.create(
                model=settings.anthropic_model, max_tokens=4000, system=SYSTEM,
                messages=[{'role': 'user', 'content': plain}])
            raw = ''.join(b.text for b in message.content if getattr(b, 'type', None) == 'text')
        finally:
            client.close()
        match = re.search(r'\{.*\}', raw, re.S)
        parsed = json.loads(match.group(0)) if match else {}
        candidates = parsed.get('ingredients')
        if not isinstance(candidates, list):
            raise ValueError('ingredients_not_a_list')
    except Exception:
        # A tidy failure must not block the analysis.
        return {'ingredients': fallback, 'applied': False, 'dropped': []}

    kept, dropped = verify_against_text(candidates, plain)
    if not kept:
        return {'ingredients': fallback, 'applied': False, 'dropped': dropped}
    return {'ingredients': kept, 'applied': True, 'dropped': dropped}
