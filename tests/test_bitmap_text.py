"""Verify exact stock-font reading and conservative treatment of unknown pixels."""

from pathlib import Path

import pytest
from PIL import Image

from temple_cua.bitmap_text import FONT_ASCII, UNKNOWN, read_bitmap_text


def render(
    text: str, foreground=(0, 0, 255), background=(255, 255, 255), *, underlined=False
) -> Image.Image:
    image = Image.new("RGB", (max(1, len(text)) * 8, 8), background)
    pixels = image.load()
    for index, character in enumerate(text):
        bits = FONT_ASCII[ord(character) - 32]
        if underlined:
            bits |= 0xFF00000000000000
        for bit in range(64):
            if bits & (1 << bit):
                pixels[index * 8 + bit % 8, bit // 8] = foreground
    return image


def test_stock_font_reads_arithmetic_and_distinguishes_n_from_h():
    # TempleOS N has diagonal pixels (0x6e, 0x7e, 0x76); H has one crossbar.
    assert FONT_ASCII[ord("N") - 32] == 0x006666767E6E6666
    assert FONT_ASCII[ord("H") - 32] == 0x006666667E666666
    assert read_bitmap_text(render("BENCH_ARITH=391")) == "BENCH_ARITH=391"
    assert read_bitmap_text(render("BEHCH_ARITH=391")) == "BEHCH_ARITH=391"


def test_real_templeos_arithmetic_frame():
    screenshot = Path(__file__).parent / "fixtures" / "temple-arithmetic.png"
    with Image.open(screenshot) as image:
        assert image.size == (640, 480)
    text = read_bitmap_text(screenshot)
    assert 'T:/Home>"BENCH_ARITH=%d\\n",37*19-24*13;' in text
    assert "BENCH_ARITH=391" in text
    assert "BEHCH_ARITH=391" not in text


def test_underlined_stock_font_reads_only_unambiguous_characters():
    assert read_bitmap_text(render("BlotHCZ", underlined=True)) == "BlotHCZ"
    assert read_bitmap_text(render("Blot.HC.Z", underlined=True)) == f"Blot{UNKNOWN}HC{UNKNOWN}Z"


@pytest.mark.parametrize(("first", "second"), [(".", ","), (":", ";"), ("g", "q")])
def test_underlining_does_not_guess_colliding_characters(first, second):
    first_image = render(f"A{first}B", underlined=True)
    second_image = render(f"A{second}B", underlined=True)
    assert first_image.tobytes() == second_image.tobytes()
    assert read_bitmap_text(first_image) == f"A{UNKNOWN}B"
    assert read_bitmap_text(second_image) == f"A{UNKNOWN}B"


def test_real_directory_redlinks_preserve_ambiguous_punctuation():
    screenshot = Path(__file__).parent / "fixtures" / "temple-directory.png"
    text = read_bitmap_text(screenshot)
    assert "/Demo/Graphics" in text
    assert f"Blot{UNKNOWN}HC{UNKNOWN}Z" in text
    assert "Blot.HC" not in text


@pytest.mark.parametrize(
    ("foreground", "background"),
    [((0, 0, 170), (255, 255, 255)), ((0, 170, 0), (255, 255, 255)), ((255, 255, 255), (0, 0, 170))],
)
def test_colored_and_inverted_text(foreground, background):
    assert read_bitmap_text(render("Blot.HC", foreground, background)) == "Blot.HC"


def test_damage_cannot_manufacture_an_expected_literal():
    image = render("BENCH_ARITH=391")
    image.putpixel((2 * 8, 0), (255, 0, 0))  # Third color corrupts the N cell.
    assert read_bitmap_text(image) == f"BE{UNKNOWN}CH_ARITH=391"


def test_blank_and_nontext_rows_are_omitted():
    image = Image.new("RGB", (32, 32), "white")
    image.paste(render("AB"), (0, 8))
    image.putpixel((0, 24), (255, 0, 0))
    assert read_bitmap_text(image) == "AB"


def test_crop_origin_and_path_input(tmp_path: Path):
    image = Image.new("RGB", (24, 16), "white")
    image.paste(render("AB"), (8, 8))
    cropped = image.crop((3, 5, 24, 16))
    path = tmp_path / "crop.png"
    cropped.save(path)
    assert read_bitmap_text(path, origin=(5, 3)) == "AB"


def test_invalid_origin():
    with pytest.raises(ValueError, match="origin"):
        read_bitmap_text(render("A"), origin=(8, 0))
