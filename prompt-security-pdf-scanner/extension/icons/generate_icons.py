"""Generate simple PNG icons (no external deps) so the extension has an icon.

Draws an indigo rounded-ish square with a lighter shield block and a small
"gap" evoking a document/scan. Run: python generate_icons.py
"""
import struct
import zlib
import pathlib

OUT = pathlib.Path(__file__).parent

BG = (67, 56, 202)      # indigo
FG = (224, 231, 255)    # light lavender


def _png(path, size):
    w = h = size
    m = max(1, size // 8)  # margin
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter type 0 per scanline
        for x in range(w):
            inner = (m <= x < w - m) and (m <= y < h - m)
            # a horizontal "slot" through the middle to suggest a document line
            slot = inner and (h // 2 - max(1, size // 16) <= y <= h // 2 + max(1, size // 16))
            r, g, b = FG if (inner and not slot) else BG
            raw += bytes((r, g, b))

    def chunk(typ, data):
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    idat = zlib.compress(bytes(raw), 9)
    path.write_bytes(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def main():
    for size in (16, 48, 128):
        _png(OUT / f"icon{size}.png", size)
    print("wrote icon16/48/128.png to", OUT)


if __name__ == "__main__":
    main()
