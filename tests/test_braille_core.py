"""Generated raster checks; no claim of accuracy on real embossed braille."""
from dataclasses import replace
from pathlib import Path
import random
import subprocess

from PIL import Image, ImageDraw
import pytest

from app.services.braille_core import (BrailleError, BrailleGrid, decode_braille,
                                       translate_braille_unicode)

DOTS = ((1, 4), (2, 5), (3, 6), (7, 8))


def board(tmp_path, masks, *, dot_rows=3, polarity="dark", columns=8):
    rows = (len(masks) + columns - 1) // columns
    grid = BrailleGrid(origin_x=14, origin_y=14, dot_dx=14, dot_dy=14,
                       cell_dx=40, cell_dy=70, columns=columns, rows=rows,
                       dot_rows=dot_rows, polarity=polarity, sample_radius=3)
    background, dot = (255, 0) if polarity == "dark" else (0, 255)
    image = Image.new("L", (int(grid.origin_x + columns * grid.cell_dx + 20),
                            int(grid.origin_y + rows * grid.cell_dy + 20)), background)
    draw = ImageDraw.Draw(image)
    for index, mask in enumerate(masks):
        row, col = divmod(index, columns)
        for y in range(dot_rows):
            for x in range(2):
                if mask & (1 << (DOTS[y][x] - 1)):
                    cx, cy = grid.origin_x + col * grid.cell_dx + x * grid.dot_dx, grid.origin_y + row * grid.cell_dy + y * grid.dot_dy
                    draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=dot)
    path = tmp_path / "board.png"
    image.save(path)
    return path, image, grid


@pytest.mark.parametrize("polarity", ["dark", "light"])
def test_all_64_six_dot_raster_patterns_preserve_bits(tmp_path, polarity):
    path, _, grid = board(tmp_path, list(range(64)), polarity=polarity)
    result = decode_braille(path, grid)
    assert [cell["dot_mask"] for cell in result["cells"]] == list(range(64))
    assert result["unicode_braille"].replace("\n", "") == "".join(chr(0x2800 + mask) for mask in range(64))
    assert result["observation_state"] == "UNCONFIRMED"
    assert result["reading_state"] == "READABLE"
    assert result["translation"]["state"] == "NOT_REQUESTED"
    assert all(cell["observation_state"] == "UNCONFIRMED" for cell in result["cells"])


def test_all_256_eight_dot_patterns_follow_unicode_dot_numbering(tmp_path):
    path, _, grid = board(tmp_path, list(range(256)), dot_rows=4, columns=16)
    result = decode_braille(path, grid)
    assert [cell["dot_mask"] for cell in result["cells"]] == list(range(256))
    assert result["cells"][64]["unicode"] == "\u2840"  # dot 7, fourth row left
    assert result["cells"][128]["unicode"] == "\u2880"  # dot 8, fourth row right


def test_speckle_noise_does_not_turn_blank_cells_into_dots(tmp_path):
    path, image, grid = board(tmp_path, [0, 63], columns=2)
    draw = ImageDraw.Draw(image)
    # One black pixel in each otherwise empty sampling disk is below the absent
    # coverage threshold; a matching white defect in filled dots is tolerated.
    for row in range(3):
        for column in range(2):
            draw.point((grid.origin_x + column * grid.dot_dx, grid.origin_y + row * grid.dot_dy), fill=0)
            draw.point((grid.origin_x + grid.cell_dx + column * grid.dot_dx, grid.origin_y + row * grid.dot_dy), fill=255)
    image.save(path)
    result = decode_braille(path, grid)
    assert [cell["dot_mask"] for cell in result["cells"]] == [0, 63]


def test_partial_dot_is_unconfirmed_and_never_silently_rounded(tmp_path):
    path, image, grid = board(tmp_path, [0], columns=1)
    draw = ImageDraw.Draw(image)
    x, y = grid.origin_x, grid.origin_y
    draw.rectangle((x - 3, y - 3, x, y + 3), fill=0)
    image.save(path)
    result = decode_braille(path, grid)
    assert result["unconfirmed_cells"] == 1
    assert result["unicode_braille"] == "\ufffd"
    assert result["cells"][0]["dot_mask"] is None
    assert result["cells"][0]["unicode"] is None
    assert result["reading_state"] == "UNCONFIRMED"
    with pytest.raises(BrailleError) as error:
        translate_braille_unicode(result["unicode_braille"])
    assert error.value.code == "UNCONFIRMED_BRAILLE"


def test_grid_cannot_sample_outside_photo(tmp_path):
    path, _, grid = board(tmp_path, [1], columns=1)
    with pytest.raises(BrailleError) as error:
        decode_braille(path, replace(grid, origin_x=1))
    assert error.value.code == "GRID_OUTSIDE_IMAGE"


@pytest.mark.parametrize("change", [{"dot_dx": float("nan")}, {"columns": True},
                                    {"sample_radius": 100}, {"columns": 256, "rows": 256},
                                    {"dot_rows": 5}, {"polarity": "auto"}, {"dot_dx": 1}])
def test_invalid_or_excessive_grid_is_rejected(change):
    values = dict(origin_x=14, origin_y=14, dot_dx=14, dot_dy=14,
                  cell_dx=40, cell_dy=70, columns=1, rows=1)
    values.update(change)
    with pytest.raises(BrailleError):
        BrailleGrid(**values)


def test_image_size_limit_checked_before_decoding(tmp_path):
    path, _, grid = board(tmp_path, [1], columns=1)
    path.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    with pytest.raises(BrailleError) as error:
        decode_braille(path, grid)
    assert error.value.code == "IMAGE_TOO_LARGE"


def test_image_pixel_limit_checked_before_loading(tmp_path):
    path = tmp_path / "large.png"
    Image.new("1", (5000, 4001)).save(path)
    grid = BrailleGrid(14, 14, 14, 14, 40, 70, 1, 1)
    with pytest.raises(BrailleError) as error:
        decode_braille(path, grid)
    assert error.value.code == "IMAGE_TOO_LARGE"


def test_photo_exif_rotation_applied_before_grid_coordinates(tmp_path):
    path = tmp_path / "rotated.jpg"
    image = Image.new("L", (120, 80), 255)
    exif = Image.Exif(); exif[274] = 6
    image.save(path, exif=exif)
    result = decode_braille(path, BrailleGrid(14, 14, 14, 14, 40, 70, 1, 1))
    assert (result["width"], result["height"]) == (80, 120)
    assert result["cells"][0]["dot_mask"] == 0


def test_symlink_and_url_inputs_rejected(tmp_path):
    path, _, grid = board(tmp_path, [1], columns=1)
    linked = tmp_path / "linked.png"; linked.symlink_to(path)
    with pytest.raises(BrailleError):
        decode_braille(linked, grid)
    with pytest.raises(BrailleError):
        decode_braille(Path("https://example.invalid/board.png"), grid)


def test_no_unconfigured_translation_is_claimed(monkeypatch):
    monkeypatch.delenv("BRAILLE_LIBLOUIS_TABLE", raising=False)
    result = translate_braille_unicode("\u2801")
    assert result["state"] == "NOT_CONFIGURED"
    assert result["text"] is None


def test_liblouis_translation_uses_selected_table_and_unicode_display(tmp_path, monkeypatch):
    table = tmp_path / "operator-verified.utb"; table.write_text("# test table only")
    observed = []
    monkeypatch.setattr("app.services.braille_core.shutil.which", lambda name: "/usr/bin/lou_translate")

    def run(args, **kwargs):
        observed.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="test-only\n".encode())

    monkeypatch.setattr("app.services.braille_core.subprocess.run", run)
    result = translate_braille_unicode("\u2801", table_path=table)
    assert observed[0][0] == ["/usr/bin/lou_translate", "--backward", "--display-table", "unicode.dis", str(table.resolve())]
    assert observed[0][1]["input"] == "\u2801".encode()
    assert result["state"] == "UNCONFIRMED"
    assert result["text"] == "test-only"
    assert result["table"] == table.name
