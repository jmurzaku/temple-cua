"""Exact text extraction for TempleOS's native, unscaled 8x8 stock font.

The bitmap values below are the printable ASCII subset of the public-domain
TempleOS Kernel/FontStd.HC (sys_font_std). Each little-endian byte is one row;
the least-significant bit is the leftmost pixel. This is a deterministic font
reader, not a fuzzy OCR model. Unknown or modified cells remain U+FFFD so they
cannot silently become expected benchmark characters.

Upstream font source (immutable revision):
https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Kernel/FontStd.HC
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from PIL import Image

GLYPH_SIZE = 8
UNKNOWN = "\ufffd"
FONT_ASCII: tuple[int, ...] = (
    0x0000000000000000,  # 32: ' '
    0x00180018183C3C18,  # 33: '!'
    0x0000000000363636,  # 34: '"'
    0x006C6CFE6CFE6C6C,  # 35: '#'
    0x00187ED07C16FC30,  # 36: '$'
    0x0060660C18306606,  # 37: '%'
    0x00DC66B61C36361C,  # 38: '&'
    0x0000000000181818,  # 39: "'"
    0x0030180C0C0C1830,  # 40: '('
    0x000C18303030180C,  # 41: ')'
    0x0000187E3C7E1800,  # 42: '*'
    0x000018187E181800,  # 43: '+'
    0x0C18180000000000,  # 44: ','
    0x000000007E000000,  # 45: '-'
    0x0018180000000000,  # 46: '.'
    0x0000060C18306000,  # 47: '/'
    0x003C666E7E76663C,  # 48: '0'
    0x007E181818181C18,  # 49: '1'
    0x007E0C183060663C,  # 50: '2'
    0x003C66603860663C,  # 51: '3'
    0x0030307E363C3830,  # 52: '4'
    0x003C6660603E067E,  # 53: '5'
    0x003C66663E060C38,  # 54: '6'
    0x000C0C0C1830607E,  # 55: '7'
    0x003C66663C66663C,  # 56: '8'
    0x001C30607C66663C,  # 57: '9'
    0x0018180018180000,  # 58: ':'
    0x0C18180018180000,  # 59: ';'
    0x0030180C060C1830,  # 60: '<'
    0x0000007E007E0000,  # 61: '='
    0x000C18306030180C,  # 62: '>'
    0x001800181830663C,  # 63: '?'
    0x003C06765676663C,  # 64: '@'
    0x006666667E66663C,  # 65: 'A'
    0x003E66663E66663E,  # 66: 'B'
    0x003C66060606663C,  # 67: 'C'
    0x001E36666666361E,  # 68: 'D'
    0x007E06063E06067E,  # 69: 'E'
    0x000606063E06067E,  # 70: 'F'
    0x003C66667606663C,  # 71: 'G'
    0x006666667E666666,  # 72: 'H'
    0x007E18181818187E,  # 73: 'I'
    0x001C36303030307C,  # 74: 'J'
    0x0066361E0E1E3666,  # 75: 'K'
    0x007E060606060606,  # 76: 'L'
    0x00C6C6D6D6FEEEC6,  # 77: 'M'
    0x006666767E6E6666,  # 78: 'N'
    0x003C66666666663C,  # 79: 'O'
    0x000606063E66663E,  # 80: 'P'
    0x006C36566666663C,  # 81: 'Q'
    0x006666363E66663E,  # 82: 'R'
    0x003C66603C06663C,  # 83: 'S'
    0x001818181818187E,  # 84: 'T'
    0x003C666666666666,  # 85: 'U'
    0x00183C6666666666,  # 86: 'V'
    0x00C6EEFED6D6C6C6,  # 87: 'W'
    0x0066663C183C6666,  # 88: 'X'
    0x001818183C666666,  # 89: 'Y'
    0x007E060C1830607E,  # 90: 'Z'
    0x003E06060606063E,  # 91: '['
    0x00006030180C0600,  # 92: '\\'
    0x007C60606060607C,  # 93: ']'
    0x000000000000663C,  # 94: '^'
    0xFFFF000000000000,  # 95: '_'
    0x000000000030180C,  # 96: '`'
    0x007C667C603C0000,  # 97: 'a'
    0x003E6666663E0606,  # 98: 'b'
    0x003C6606663C0000,  # 99: 'c'
    0x007C6666667C6060,  # 100: 'd'
    0x003C067E663C0000,  # 101: 'e'
    0x000C0C0C3E0C0C38,  # 102: 'f'
    0x3C607C66667C0000,  # 103: 'g'
    0x00666666663E0606,  # 104: 'h'
    0x003C1818181C0018,  # 105: 'i'
    0x0E181818181C0018,  # 106: 'j'
    0x0066361E36660606,  # 107: 'k'
    0x003C18181818181C,  # 108: 'l'
    0x00C6D6D6FE6C0000,  # 109: 'm'
    0x00666666663E0000,  # 110: 'n'
    0x003C6666663C0000,  # 111: 'o'
    0x06063E66663E0000,  # 112: 'p'
    0xE0607C66667C0000,  # 113: 'q'
    0x000606066E360000,  # 114: 'r'
    0x003E603C067C0000,  # 115: 's'
    0x00380C0C0C3E0C0C,  # 116: 't'
    0x007C666666660000,  # 117: 'u'
    0x00183C6666660000,  # 118: 'v'
    0x006CFED6D6C60000,  # 119: 'w'
    0x00663C183C660000,  # 120: 'x'
    0x3C607C6666660000,  # 121: 'y'
    0x007E0C18307E0000,  # 122: 'z'
    0x003018180E181830,  # 123: '{'
    0x0018181818181818,  # 124: '|'
    0x000C18187018180C,  # 125: '}'
    0x000000000062D68C,  # 126: '~'
)
_UNDERLINE = 0xFF00000000000000
# The stock renderer ORs this final-row mask in _GR_ROP_EQU_U8_NO_CLIPPING:
# https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Adam/Gr/GrAsm.HC#L274-L285
_GLYPHS: dict[int, set[str]] = defaultdict(set)
for _code, _bits in enumerate(FONT_ASCII, start=32):
    _GLYPHS[_bits].add(chr(_code))
    _GLYPHS[_bits | _UNDERLINE].add(chr(_code))


def read_bitmap_text(
    source: Image.Image | str | Path, *, origin: tuple[int, int] = (0, 0)
) -> str:
    """Read exact printable ASCII cells from a native TempleOS screenshot.

    ``origin`` is the first complete 8x8 cell in the supplied image. For a crop
    starting at (x, y) in the native screenshot, use ((-x) % 8, (-y) % 8).
    Every cell must contain at most two flat RGB colors, and a foreground
    mask must match a stock glyph or its standard underlined form exactly.
    This supports colored and inverted text without guessing a background
    color. An underline overwrites the final bitmap row, so some characters
    become indistinguishable (for example, '.' and ','); these remain U+FFFD.
    Antialiased, resized, occluded, and other unknown cells also become U+FFFD.
    Rows without any recognized nonspace glyph are omitted (including borders
    and blank desktop regions).
    """
    ox, oy = origin
    if not (isinstance(ox, int) and isinstance(oy, int) and 0 <= ox < 8 and 0 <= oy < 8):
        raise ValueError("origin coordinates must be integers from 0 to 7")
    if isinstance(source, Image.Image):
        frame = source.convert("RGB")
    else:
        with Image.open(source) as opened:
            frame = opened.convert("RGB")
    pixels = frame.load()
    lines: list[str] = []
    for y in range(oy, frame.height - 7, 8):
        row: list[str] = []
        for x in range(ox, frame.width - 7, 8):
            masks: dict[tuple[int, int, int], int] = {}
            for dy in range(8):
                for dx in range(8):
                    color = pixels[x + dx, y + dy]
                    masks[color] = masks.get(color, 0) | (1 << (dy * 8 + dx))
            if len(masks) == 1:
                row.append(" ")
            elif len(masks) == 2:
                candidates = set().union(*(_GLYPHS.get(mask, set()) for mask in masks.values()))
                row.append(candidates.pop() if len(candidates) == 1 else UNKNOWN)
            else:
                row.append(UNKNOWN)
        if any(char != " " and char != UNKNOWN for char in row):
            lines.append("".join(row).rstrip())
    return "\n".join(lines)
