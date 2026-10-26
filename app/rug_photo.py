"""Per-account encrypted storage for ingredient-label photos.

A photo is kept so that a wrong reading can be traced back to the picture it
came from. It is encrypted under the existing Life Recover key, stored under a
folder named after a hash of the account, and never placed in a public path.
One account can never read another account's photo.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException

from app.platform_core.persistent_domain_store import _key
from app.services.secret_vault import state_root

MAX_PHOTO_BYTES = 8 * 1024 * 1024
EXTENSIONS = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp'}
NONCE = 12


def _folder(subject: str) -> Path:
    safe = hashlib.sha256(subject.encode('utf-8')).hexdigest()[:32]
    root = state_root() / 'liferecover' / 'rug_photos' / safe
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    return root


def _aad(subject: str, photo_id: str) -> bytes:
    return ('rug_photo_v1|' + hashlib.sha256(subject.encode()).hexdigest() + '|' + photo_id).encode()


def _path(subject: str, photo_id: str) -> Path:
    if not photo_id.startswith('rug_photo_') or not photo_id[10:].isalnum() or len(photo_id) > 64:
        raise HTTPException(404, 'photo_not_found')
    return _folder(subject) / (photo_id + '.enc')


def store_photo(subject: str, image: bytes, content_type: str) -> dict:
    """Encrypt and keep one photo. Returns the record to bind to the analysis."""
    if content_type not in EXTENSIONS:
        raise HTTPException(415, 'unsupported_image_type')
    if not image or len(image) > MAX_PHOTO_BYTES:
        raise HTTPException(413, 'image_size_invalid')
    photo_id = 'rug_photo_' + uuid.uuid4().hex
    nonce = os.urandom(NONCE)
    sealed = AESGCM(_key()).encrypt(nonce, image, _aad(subject, photo_id))
    target = _path(subject, photo_id)
    target.write_bytes(nonce + sealed)
    target.chmod(0o600)
    return {'photo_id': photo_id, 'content_type': content_type, 'bytes': len(image),
            'sha256': hashlib.sha256(image).hexdigest()}


def read_photo(subject: str, photo_id: str) -> tuple[bytes, str]:
    target = _path(subject, photo_id)
    if not target.is_file():
        raise HTTPException(404, 'photo_not_found')
    blob = target.read_bytes()
    if len(blob) <= NONCE:
        raise HTTPException(409, 'photo_unreadable')
    try:
        image = AESGCM(_key()).decrypt(blob[:NONCE], blob[NONCE:], _aad(subject, photo_id))
    except InvalidTag:
        # Wrong account, wrong key, or a changed file. Never serve it.
        raise HTTPException(409, 'photo_unreadable') from None
    if image[:3] == b'\xff\xd8\xff':
        kind = 'image/jpeg'
    elif image[:8] == b'\x89PNG\r\n\x1a\n':
        kind = 'image/png'
    elif image[:4] == b'RIFF' and image[8:12] == b'WEBP':
        kind = 'image/webp'
    else:
        kind = 'application/octet-stream'
    return image, kind
