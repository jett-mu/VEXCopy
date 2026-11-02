"""Minimal PDF writer that mirrors the structure of the reference document.

Handles what general PDF libraries don't do cleanly here: per-glyph positioning
with the font's own kerning, ligatures tagged with /ActualText, and subset
TrueType fonts with MacRoman encoding.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import zlib
from dataclasses import dataclass

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

from . import VexCopyError

# fontTools logs harmless notices about Apple's legacy font tables.
logging.getLogger("fontTools").setLevel(logging.ERROR)

SYSTEM_HELVETICA = "/System/Library/Fonts/Helvetica.ttc"

# The one kerning value that differs from the font's tables. The reference has
# T+o at -132.5/1000 in the Oblique subtitle, but both the kern table and
# CoreText give -110.8/1000. Pinned to the reference so the output matches.
KERN_OVERRIDES = {("Helvetica-Oblique", "T", "o"): -271}

# Font descriptor values as written in the reference, for its three faces.
REFERENCE_DESCRIPTORS = {
    "Helvetica": dict(Flags=32, FontBBox=(-951, -481, 1445, 1122), ItalicAngle=0,
                      CapHeight=717, XHeight=523, StemH=85, AvgWidth=441),
    "Helvetica-Bold": dict(Flags=32, FontBBox=(-1018, -481, 1372, 962), ItalicAngle=0,
                           CapHeight=720, XHeight=532, StemH=127, AvgWidth=622),
    "Helvetica-Oblique": dict(Flags=96, FontBBox=(-933, -481, 1571, 1138), ItalicAngle=-6,
                              CapHeight=717, XHeight=523, StemH=85, AvgWidth=-441),
}

LIGATURES = {"fi": "\ufb01", "fl": "\ufb02"}

# Longest run encoded with a single Tc value (measured from the reference).
MAX_TC_RUN = 8


def num(value: float) -> str:
    """Format a number the way Quartz does: at most 7 significant digits."""
    text = f"{value:.7g}"
    if "e" in text:
        text = f"{value:.7f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


@dataclass
class Glyph:
    code: int        # MacRoman byte
    advance: float   # true advance including kerning, in 1/1000 em
    source: str      # the original text this glyph stands for


class Face:
    """One font face: measuring, shaping and subset embedding."""

    def __init__(self, path: str, index: int, resource: str, prefix: str):
        try:
            self.font = TTFont(path, fontNumber=index, recalcTimestamp=False, lazy=False)
        except FileNotFoundError:
            raise VexCopyError(f"Font file not found: {path}") from None
        except Exception as exc:
            raise VexCopyError(f"Could not read font {path} (face {index}): {exc}") from None
        if "glyf" not in self.font:
            raise VexCopyError(f"Font {path} has no TrueType outlines; use a .ttf or .ttc font.")
        self.path, self.index = path, index
        self.resource, self.prefix = resource, prefix
        self.psname = self.font["name"].getDebugName(6) or f"Font{index}"
        self.upem = self.font["head"].unitsPerEm
        self.ascent = self.font["hhea"].ascent / self.upem
        self.cmap = self.font.getBestCmap()
        self.hmtx = self.font["hmtx"]
        self.kern = {}
        if "kern" in self.font:
            for table in self.font["kern"].kernTables:
                self.kern.update(getattr(table, "kernTable", {}) or {})
        for (name, left, right), value in KERN_OVERRIDES.items():
            if name == self.psname:
                self.kern[(left, right)] = value
        self.used: set[int] = set()

    def _glyph_name(self, char: str) -> str:
        name = self.cmap.get(ord(char))
        if name is None:
            raise VexCopyError(f"Character {char!r} is not available in font {self.psname}.")
        return name

    def shape(self, text: str) -> list[Glyph]:
        """Turn text into positioned glyphs and register them for embedding."""
        chars: list[tuple[str, str]] = []
        i = 0
        while i < len(text):
            pair = text[i:i + 2]
            if pair in LIGATURES and ord(LIGATURES[pair]) in self.cmap:
                chars.append((LIGATURES[pair], pair))
                i += 2
            else:
                chars.append((text[i], text[i]))
                i += 1

        glyphs = []
        names = []
        for char, source in chars:
            try:
                code = char.encode("mac_roman")[0]
            except UnicodeEncodeError:
                raise VexCopyError(
                    f"Character {char!r} in {text!r} can't be printed: this report's fonts "
                    "only support the Mac Roman character set."
                ) from None
            name = self._glyph_name(char)
            names.append(name)
            glyphs.append(Glyph(code, self.hmtx[name][0] * 1000 / self.upem, source))
            self.used.add(code)
        for j in range(len(glyphs) - 1):
            glyphs[j].advance += self.kern.get((names[j], names[j + 1]), 0) * 1000 / self.upem
        return glyphs

    def width(self, text: str, size: float) -> float:
        return sum(g.advance for g in self.shape(text)) * size / 1000

    def pdf_width(self, code: int) -> int:
        char = bytes([code]).decode("mac_roman")
        return round(self.hmtx[self._glyph_name(char)][0] * 1000 / self.upem)

    def subset_bytes(self) -> bytes:
        """Build the embedded subset, with a Mac Roman cmap like the reference."""
        # Keep the full font's bounding box, as the reference does. Some renderers,
        # poppler included, size glyphs from it, so a tighter box shifts the text.
        font = TTFont(self.path, fontNumber=self.index, recalcTimestamp=False,
                      recalcBBoxes=False, lazy=False)
        original_flags = font["head"].flags
        mapping = {}
        for code in sorted(self.used):
            mapping[code] = self._glyph_name(bytes([code]).decode("mac_roman"))

        options = subset.Options()
        options.hinting = True
        options.notdef_outline = True
        options.glyph_names = False
        options.name_IDs = [1, 2, 4, 6]
        options.drop_tables += ["OS/2", "morx", "feat", "prop", "meta", "kern", "hdmx", "Zapf",
                                "GDEF", "GPOS", "GSUB", "DSIG", "kerx", "gasp", "VDMX", "LTSH"]
        subsetter = subset.Subsetter(options)
        subsetter.populate(glyphs=sorted(set(mapping.values())))
        subsetter.subset(font)

        table = CmapSubtable.newSubtable(6)
        table.platformID, table.platEncID, table.language = 1, 0, 0
        table.cmap = mapping
        font["cmap"].tables = [table]
        font["head"].flags = original_flags

        out = io.BytesIO()
        font.save(out, reorderTables=True)
        return out.getvalue()

    def descriptor(self) -> dict:
        if self.psname in REFERENCE_DESCRIPTORS:
            return dict(REFERENCE_DESCRIPTORS[self.psname])
        head = self.font["head"]
        scale = 1000 / self.upem
        os2 = self.font["OS/2"] if "OS/2" in self.font else None
        return dict(
            Flags=32,
            FontBBox=tuple(round(v * scale) for v in (head.xMin, head.yMin, head.xMax, head.yMax)),
            ItalicAngle=round(self.font["post"].italicAngle),
            CapHeight=round(getattr(os2, "sCapHeight", 717 / scale) * scale),
            XHeight=round(getattr(os2, "sxHeight", 523 / scale) * scale),
            StemH=85,
            AvgWidth=round(getattr(os2, "xAvgCharWidth", 441 / scale) * scale),
        )


def escape(data: bytes) -> bytes:
    """Escape bytes for a PDF literal string."""
    out = bytearray()
    for byte in data:
        if byte in b"()\\":
            out += b"\\" + bytes([byte])
        elif byte < 32:
            out += b"\\%03o" % byte
        else:
            out.append(byte)
    return bytes(out)


def text_operator(glyphs: list[Glyph], face: Face) -> tuple[str, bytes]:
    """Build the character-spacing prefix and Tj/TJ operator for a run of glyphs.

    PDF viewers advance by the rounded /Widths values, so adjustments carry the
    difference from the true advance, including kerning. This follows how the
    reference (written by Quartz) encodes them: each glyph is corrected on its
    own rather than accumulating error. A short run whose first glyph needs a
    correction uses that value as Tc, and only whole-unit remainders are kept.
    """
    residuals = [g.advance - face.pdf_width(g.code) for g in glyphs[:-1]]
    spacing = 0.0
    if residuals and len(glyphs) <= MAX_TC_RUN and round(residuals[0], 1) != 0:
        spacing = round(residuals[0] / 1000, 4)

    parts: list[bytes] = []
    current = bytearray()
    for index, glyph in enumerate(glyphs):
        current.append(glyph.code)
        if index == len(glyphs) - 1:
            break
        if spacing:
            adjust = float(round(residuals[index] - spacing * 1000))
        else:
            adjust = round(residuals[index], 1)
        if adjust:
            parts.append(b"(" + escape(bytes(current)) + b")")
            parts.append(num(-adjust).encode())
            current = bytearray()
    if current:
        parts.append(b"(" + escape(bytes(current)) + b")")
    prefix = f"{num(spacing)} Tc " if spacing else ""
    if len(parts) == 1:
        return prefix, parts[0] + b" Tj"
    return prefix, b"[ " + b" ".join(parts) + b" ] TJ"


def text_string(value: str) -> bytes:
    """Encode an Info dictionary string."""
    try:
        return b"(" + escape(value.encode("ascii")) + b")"
    except UnicodeEncodeError:
        return b"<" + (b"\xfe\xff" + value.encode("utf-16-be")).hex().encode() + b">"


class Document:
    """Collects numbered objects and serializes them with a cross-reference table."""

    def __init__(self):
        self.objects: dict[int, bytes] = {}
        self.next_id = 1

    def reserve(self) -> int:
        obj_id = self.next_id
        self.next_id += 1
        return obj_id

    def add(self, body: bytes, obj_id: int | None = None) -> int:
        if obj_id is None:
            obj_id = self.reserve()
        self.objects[obj_id] = body
        return obj_id

    def add_stream(self, entries: bytes, data: bytes, obj_id: int | None = None) -> int:
        packed = zlib.compress(data, 9)
        head = b"<< " + entries + b" /Length %d /Filter /FlateDecode >>" % len(packed)
        return self.add(head + b"\nstream\n" + packed + b"\nendstream", obj_id)

    def add_font(self, face: Face) -> int:
        file_data = face.subset_bytes()
        file_id = self.add_stream(b"/Length1 %d" % len(file_data), file_data)
        info = face.descriptor()
        name = f"{face.prefix}+{face.psname}".encode()
        bbox = " ".join(str(v) for v in info["FontBBox"]).encode()
        descriptor = self.add(
            b"<< /Type /FontDescriptor /FontName /" + name
            + b" /Flags %d /FontBBox [%s] /ItalicAngle %d /Ascent 770 /Descent -230"
              % (info["Flags"], bbox, info["ItalicAngle"])
            + b" /CapHeight %d /StemV 0 /XHeight %d /StemH %d /AvgWidth %d /MaxWidth 1500"
              % (info["CapHeight"], info["XHeight"], info["StemH"], info["AvgWidth"])
            + b" /FontFile2 %d 0 R >>" % file_id
        )
        first, last = min(face.used), max(face.used)
        widths = " ".join(
            str(face.pdf_width(c)) if c in face.used else "0" for c in range(first, last + 1)
        ).encode()
        return self.add(
            b"<< /Type /Font /Subtype /TrueType /BaseFont /" + name
            + b" /FontDescriptor %d 0 R /Encoding /MacRomanEncoding" % descriptor
            + b" /FirstChar %d /LastChar %d /Widths [ %s ] >>" % (first, last, widths)
        )

    def serialize(self, root: int, info: int) -> bytes:
        out = io.BytesIO()
        out.write(b"%PDF-1.3\n%\xc4\xe5\xf2\xe5\xeb\xa7\xf3\xa0\xd0\xc4\xc6\n")
        offsets = {}
        for obj_id in sorted(self.objects):
            offsets[obj_id] = out.tell()
            out.write(b"%d 0 obj\n" % obj_id + self.objects[obj_id] + b"\nendobj\n")
        digest = hashlib.md5(b"".join(self.objects[i] for i in sorted(self.objects))).hexdigest()
        start = out.tell()
        size = max(self.objects) + 1
        out.write(b"xref\n0 %d\n0000000000 65535 f \n" % size)
        for obj_id in range(1, size):
            out.write(b"%010d 00000 n \n" % offsets[obj_id])
        out.write(
            b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R /ID [ <%s>\n<%s> ] >>\n"
            % (size, root, info, digest.encode(), digest.encode())
        )
        out.write(b"startxref\n%d\n%%%%EOF\n" % start)
        return out.getvalue()


def pdf_date(moment) -> bytes:
    """Format a timezone-aware datetime as a PDF date in UTC."""
    return re.sub(rb"\s", b"", moment.strftime("D:%Y%m%d%H%M%SZ00'00'").encode())
