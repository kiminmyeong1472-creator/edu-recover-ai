"""Read an ingredient-label photo with the Tesseract OCR program.

OCR only turns the picture into characters. It never decides whether an
allergen is present: that stays with the rule comparison in app.rug_formula.
The recognised text is kept beside the stored photo so a wrong reading can be
traced back to the original image.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_IMAGE_BYTES = 8 * 1024 * 1024
CONTENT_TYPES = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}
MAX_INGREDIENTS = 400
PREFERRED_LANGS = ('kor+eng', 'kor', 'eng')
TIMEOUT_SECONDS = 60
SEPARATORS = re.compile(r'[,;/·•∙•\n\r\t]|\s{3,}')
LABEL_PREFIX = re.compile(r'^\s*(전성분|성분|ingredients?)\s*[:：]?\s*', re.I)


class OcrNotInstalled(Exception):
    """The OCR program or its language data is missing on this server."""


class PhotoUnreadable(Exception):
    """The photo produced no usable ingredient text."""


def available_languages() -> list[str]:
    if not shutil.which('tesseract'):
        return []
    try:
        done = subprocess.run(['tesseract', '--list-langs'], capture_output=True,
                              text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in done.stdout.splitlines()[1:] if line.strip()]


def choose_language(langs: list[str]) -> str:
    for candidate in PREFERRED_LANGS:
        if all(part in langs for part in candidate.split('+')):
            return candidate
    raise OcrNotInstalled('ocr_language_data_missing')


def _split(text: str) -> list[str]:
    out, seen = [], set()
    # Numeric locants inside one name, such as 1,2-Hexanediol, are not separators.
    guarded = re.sub(r'(?<=\d),(?=\d)', '', text)
    for piece in SEPARATORS.split(guarded):
        name = LABEL_PREFIX.sub('', piece.replace('', ',')).strip(' .()[]{}<>-—·')
        name = re.sub(r'\s+', ' ', name)
        if len(name) < 2 or len(name) > 200 or not re.search(r'[가-힣A-Za-z]', name):
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= MAX_INGREDIENTS:
            break
    return out


def read_label(image: bytes, content_type: str) -> dict:
    """Return {'text', 'ingredients', 'language', 'korean_available'}."""
    suffix = CONTENT_TYPES.get(content_type)
    if suffix is None:
        raise PhotoUnreadable('unsupported_image_type')
    if not image or len(image) > MAX_IMAGE_BYTES:
        raise PhotoUnreadable('image_size_invalid')

    langs = available_languages()
    if not langs:
        raise OcrNotInstalled('ocr_program_missing')
    language = choose_language(langs)

    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / ('label' + suffix)
        source.write_bytes(image)
        try:
            done = subprocess.run(
                ['tesseract', str(source), 'stdout', '-l', language, '--psm', '6'],
                capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            raise PhotoUnreadable('ocr_timeout') from None
        except OSError:
            raise OcrNotInstalled('ocr_program_missing') from None

    if done.returncode != 0:
        raise PhotoUnreadable('ocr_failed')
    text = done.stdout.strip()
    ingredients = _split(text)
    if not ingredients:
        raise PhotoUnreadable('photo_no_ingredient_found')
    return {'text': text, 'ingredients': ingredients, 'language': language,
            'korean_available': 'kor' in langs}
