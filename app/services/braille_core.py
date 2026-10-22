"""Calibrated photographs of six/eight-dot braille cells -> Unicode patterns.

The caller supplies the first dot centre, spacing and board size. Empty cells
cannot be inferred safely from an uncalibrated photograph, so this module does
not guess a grid. Automatic readings remain UNCONFIRMED, including clear images.
Unicode braille is a dot pattern, not a Korean translation.

Bit order: Unicode Braille Patterns, https://www.unicode.org/charts/PDF/U2800.pdf
Back-translation: https://liblouis.io/documentation/liblouis/lou_005ftranslate-_0028program_0029.html
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
_DOT_NUMBERS = ((1, 4), (2, 5), (3, 6), (7, 8))
SOURCES = [
    "https://www.unicode.org/charts/PDF/U2800.pdf",
    "https://liblouis.io/documentation/liblouis/lou_005ftranslate-_0028program_0029.html",
]


class BrailleError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class BrailleGrid:
    """Pixel measurements after applying the photograph's EXIF orientation."""

    origin_x: float
    origin_y: float
    dot_dx: float
    dot_dy: float
    cell_dx: float
    cell_dy: float
    columns: int
    rows: int
    dot_rows: int = 3
    sample_radius: float = 3.0
    polarity: str = "dark"
    min_contrast: float = 24.0

    def __post_init__(self):
        for field in ("origin_x", "origin_y", "dot_dx", "dot_dy", "cell_dx", "cell_dy", "sample_radius", "min_contrast"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise BrailleError("INVALID_GRID", "점자 격자의 위치와 간격에는 유한한 숫자를 입력해야 합니다.")
        for field in ("columns", "rows", "dot_rows"):
            if isinstance(getattr(self, field), bool) or not isinstance(getattr(self, field), int):
                raise BrailleError("INVALID_GRID", "점자 칸 수와 점의 행 수에는 정수를 입력해야 합니다.")
        if not 1 <= self.columns <= 256 or not 1 <= self.rows <= 256 or self.columns * self.rows > 4096:
            raise BrailleError("INVALID_GRID", "점자 격자는 가로·세로 각각 256칸, 전체 4096칸 이하여야 합니다.")
        if self.dot_rows not in (3, 4) or self.polarity not in ("dark", "light"):
            raise BrailleError("INVALID_GRID", "점자는 6점 또는 8점이며 점의 색은 dark 또는 light여야 합니다.")
        if self.origin_x < 0 or self.origin_y < 0 or not 1 <= self.sample_radius <= 16:
            raise BrailleError("INVALID_GRID", "격자 원점은 음수가 아니어야 하며 점 검사 반경은 1~16픽셀이어야 합니다.")
        if self.dot_dx < 3 * self.sample_radius or self.dot_dy < 3 * self.sample_radius:
            raise BrailleError("INVALID_GRID", "점 사이 간격이 검사 반경에 비해 너무 작습니다.")
        if self.cell_dx < self.dot_dx + 3 * self.sample_radius or self.cell_dy < (self.dot_rows - 1) * self.dot_dy + 3 * self.sample_radius:
            raise BrailleError("INVALID_GRID", "점자 칸 사이 간격이 점의 배치보다 작습니다.")
        if not 8 <= self.min_contrast <= 200:
            raise BrailleError("INVALID_GRID", "점과 배경의 최소 밝기 차이는 8~200이어야 합니다.")
        sample_budget = self.columns * self.rows * self.dot_rows * 2 * (4.4 * self.sample_radius + 2) ** 2
        if sample_budget > 5_000_000:
            raise BrailleError("GRID_TOO_LARGE", "점 검사량이 한 작업의 한도를 초과했습니다. 사진을 나눠 처리해야 합니다.")


def _read_image(path: Path) -> tuple[Image.Image, str]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BrailleError("INVALID_IMAGE", "일반 점자 사진 파일을 지정해야 합니다.")
    if not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
        raise BrailleError("IMAGE_TOO_LARGE", "점자 사진은 비어 있지 않은 10MiB 이하 파일이어야 합니다.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                if source.format not in {"PNG", "JPEG", "WEBP", "BMP"}:
                    raise BrailleError("UNSUPPORTED_IMAGE", "점자 사진은 PNG·JPEG·WEBP·BMP 형식이어야 합니다.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise BrailleError("IMAGE_TOO_LARGE", "점자 사진은 2000만 화소 이하여야 합니다.")
                # The UI must show the same orientation before the user places a grid.
                image = ImageOps.exif_transpose(source).convert("L")
                image.load()
    except BrailleError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise BrailleError("INVALID_IMAGE", "점자 사진을 읽지 못했습니다.") from exc
    return image, hashlib.sha256(path.read_bytes()).hexdigest()


def _observe_dot(image: Image.Image, x: float, y: float, grid: BrailleGrid) -> dict:
    radius = grid.sample_radius
    outer, inner_ring = 2.1 * radius, 1.55 * radius
    if x - outer < 0 or y - outer < 0 or x + outer >= image.width or y + outer >= image.height:
        raise BrailleError("GRID_OUTSIDE_IMAGE", "점자 격자 또는 점 주변 검사 영역이 사진 밖으로 나갔습니다.")
    centre, ring = [], []
    for py in range(math.floor(y - outer), math.ceil(y + outer) + 1):
        for px in range(math.floor(x - outer), math.ceil(x + outer) + 1):
            distance = (px - x) ** 2 + (py - y) ** 2
            if distance <= radius ** 2:
                centre.append(image.getpixel((px, py)))
            elif inner_ring ** 2 <= distance <= outer ** 2:
                ring.append(image.getpixel((px, py)))
    background = float(statistics.median(ring))
    direction = -1 if grid.polarity == "dark" else 1
    contrasts = [direction * (pixel - background) for pixel in centre]
    fraction = sum(value >= grid.min_contrast for value in contrasts) / len(contrasts)
    # Intermediate coverage can be glare, a misaligned grid or a partial dot.
    # It is not rounded to a definite six/eight-dot cell.
    if fraction >= 0.80:
        state, present = "PRESENT", True
        separation = (fraction - 0.80) / 0.20
    elif fraction <= 0.10:
        state, present = "ABSENT", False
        separation = (0.10 - fraction) / 0.10
    else:
        state, present, separation = "UNCONFIRMED", None, 0.0
    return {"x": round(x, 3), "y": round(y, 3), "reading_state": state,
            "present": present, "signal_fraction": round(fraction, 4),
            "background_level": round(background, 2),
            "mean_contrast": round(statistics.mean(contrasts), 2),
            "separation_score": round(max(0.0, min(1.0, separation)), 4)}


def decode_braille(image_path: Path, grid: BrailleGrid) -> dict:
    """Read a user-calibrated board without silently translating its dot patterns."""
    if not isinstance(grid, BrailleGrid):
        raise BrailleError("INVALID_GRID", "검증된 점자 격자 설정이 필요합니다.")
    image, source_sha256 = _read_image(image_path)
    cells, lines, unresolved = [], [], 0
    for row in range(grid.rows):
        line = []
        for column in range(grid.columns):
            observations, candidate_mask, ambiguous = [], 0, False
            for dot_row in range(grid.dot_rows):
                for dot_column in range(2):
                    number = _DOT_NUMBERS[dot_row][dot_column]
                    point = _observe_dot(image,
                                         grid.origin_x + column * grid.cell_dx + dot_column * grid.dot_dx,
                                         grid.origin_y + row * grid.cell_dy + dot_row * grid.dot_dy, grid)
                    point["dot_number"] = number
                    observations.append(point)
                    if point["present"] is True:
                        candidate_mask |= 1 << (number - 1)
                    if point["present"] is None:
                        ambiguous = True
            if ambiguous:
                unresolved += 1
            character = None if ambiguous else chr(0x2800 + candidate_mask)
            line.append(character if character is not None else "\ufffd")
            cells.append({"row": row, "column": column,
                          "dot_mask": None if ambiguous else candidate_mask,
                          "candidate_dot_mask": candidate_mask,
                          "unicode": character,
                          "reading_state": "UNCONFIRMED" if ambiguous else "READABLE",
                          "observation_state": "UNCONFIRMED", "dots": observations,
                          "separation_score": min(point["separation_score"] for point in observations)})
        lines.append("".join(line))
    notes = ["자동 판독 결과는 사람이 확인하기 전까지 미확정입니다.",
             "이 결과는 점의 배열입니다. 한국어·영어 문장으로 번역한 결과가 아닙니다.",
             "앞면·좌우 방향과 사진의 기울기를 확인해야 합니다. 격자를 자동으로 추정하지 않았습니다.",
             "밝기 차이가 작은 양각 점자·빛 반사·흐린 사진은 읽지 못하거나 틀릴 수 있습니다."]
    if unresolved:
        notes.append(f"{unresolved}칸에 확정할 수 없는 점이 있어 해당 칸을 대체문자(�)로 표시했습니다.")
    return {"schema_version": 1, "source_sha256": source_sha256,
            "width": image.width, "height": image.height, "grid": asdict(grid),
            "unicode_braille": "\n".join(lines), "cells": cells,
            "reading_state": "UNCONFIRMED" if unresolved else "READABLE",
            "observation_state": "UNCONFIRMED", "unconfirmed_cells": unresolved,
            "confidence_kind": "contrast_separation_heuristic_not_probability",
            "warnings": notes, "sources": SOURCES,
            "translation": {"state": "NOT_REQUESTED", "text": None}}


def translate_braille_unicode(text: str, *, table_path: Path | None = None) -> dict:
    """Optionally back-translate with an operator-selected Liblouis table.

    This is separate from decoding: unreadable cells (replacement characters) are
    rejected rather than converted into plausible text. Table installation and
    suitability for the board's braille convention must be checked separately.
    """
    if not isinstance(text, str) or not text or len(text) > 100_000:
        raise BrailleError("INVALID_BRAILLE", "번역할 점자 배열이 비어 있거나 너무 깁니다.")
    if any(character not in "\n\r " and not 0x2800 <= ord(character) <= 0x28FF for character in text):
        raise BrailleError("UNCONFIRMED_BRAILLE", "미확정 문자나 점자가 아닌 문자가 있어 번역을 보류했습니다.")
    configured = table_path if table_path is not None else os.getenv("BRAILLE_LIBLOUIS_TABLE", "")
    if not configured:
        return {"state": "NOT_CONFIGURED", "text": None,
                "message": "운영자가 확인한 Liblouis 점자 규칙 파일을 지정해야 합니다."}
    table = Path(configured)
    if table.is_symlink() or not table.is_file() or table.suffix.lower() not in {".ctb", ".utb"} or "," in str(table):
        raise BrailleError("INVALID_TABLE", "설치된 점자 규칙 파일의 정확한 경로를 지정해야 합니다.")
    executable = shutil.which("lou_translate")
    if not executable:
        return {"state": "TOOL_MISSING", "text": None,
                "message": "점자 역번역 도구 Liblouis가 설치돼 있지 않습니다."}
    try:
        result = subprocess.run([executable, "--backward", "--display-table", "unicode.dis", str(table.resolve())],
                                input=text.encode("utf-8"), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BrailleError("TRANSLATION_FAILED", "점자 배열을 문장으로 바꾸지 못했습니다.") from exc
    if result.returncode or len(result.stdout) > 2_000_000:
        raise BrailleError("TRANSLATION_FAILED", "점자 배열을 문장으로 바꾸지 못했습니다.")
    try:
        translated = result.stdout.decode("utf-8").rstrip("\n")
    except UnicodeDecodeError as exc:
        raise BrailleError("TRANSLATION_FAILED", "점자 번역 결과를 읽지 못했습니다.") from exc
    return {"state": "UNCONFIRMED", "text": translated, "engine": "liblouis",
            "table": table.name, "message": "지정한 규칙 파일에 따른 자동 역번역입니다. 원자료와 대조해야 합니다."}
