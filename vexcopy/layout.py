"""Lays out the report and builds the PDF.

All positions use the reference's logical page of 2550x3300 units, mapped into
the printable area with slightly different x and y scales. The drawing order
follows the reference's content stream.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil, floor

from PIL import Image

from . import VexCopyError
from .config import ASSETS as ASSETS_DIR, Config
from .pdfwriter import Document, Face, num, pdf_date, text_operator, text_string
from .schedule import (TEAM_KEYS, fill_rows, format_timestamp, generate_pairings, load_csv,
                       pairing_rows, parse_datetime, team_list)

# Page mapping: x_pt = LEFT + x * SCALE_X, y_pt = TOP - y * SCALE_Y.
PAGE_WIDTH, PAGE_HEIGHT = 612, 792
LOGICAL_WIDTH, LOGICAL_HEIGHT = 2550, 3300
LEFT, TOP = 18.0, 774.0
PRINT_WIDTH, PRINT_HEIGHT = 576.0, 733.92
SCALE_X = PRINT_WIDTH / LOGICAL_WIDTH
SCALE_Y = PRINT_HEIGHT / LOGICAL_HEIGHT

# Reference's text transform and page clip, as written in its content stream.
TEXT_CM = f"{num(SCALE_X)} 0 0 {num(SCALE_Y)}"
LINE_CM = f"{num(SCALE_X)} 0 0 -{num(SCALE_Y)} 18 774 cm"
PAGE_CLIP = f"18 {num(TOP - PRINT_HEIGHT)} 576 {num(PRINT_HEIGHT)} re W n"

MARGIN_LEFT, MARGIN_RIGHT = 105, 2445
TITLE_TOP, TITLE_SIZE = 105, 80
EVENT_TOP, EVENT_SIZE = 200, 55
RULE_1_Y, RULE_2_Y = 270, 300
HEADER_TOP = 301
ROW_HEIGHT = 65
CELL_PADDING = 8
CELL_SIZE = 48
HEADER_RULE_WIDTH = 5
FOOTER_RULE_Y = 3154
FOOTER_TOP, FOOTER_SIZE = 3159, 36
OBLIQUE_SKEW = math.tan(math.radians(11))
LOGO_BOX_WIDTH, LOGO_BOX_HEIGHT = 383, 150
LOGO_TOP = 105
MAX_ROWS_PER_PAGE = (FOOTER_RULE_Y - (HEADER_TOP + ROW_HEIGHT)) // ROW_HEIGHT


def px(x: float) -> float:
    return LEFT + x * SCALE_X


def py(y: float) -> float:
    return TOP - y * SCALE_Y


def split_font(spec: str) -> tuple[str, int]:
    path, _, index = spec.partition("#")
    try:
        return path, int(index or 0)
    except ValueError:
        raise VexCopyError(f"Bad font setting {spec!r}. Use path or path#faceIndex.") from None


def column_edges(columns) -> list[int]:
    if not columns:
        raise VexCopyError("The table needs at least one column.")
    if any(c.weight <= 0 for c in columns):
        raise VexCopyError("Column weights must be greater than 0.")
    total = sum(c.weight for c in columns)
    width = MARGIN_RIGHT - MARGIN_LEFT
    edges, running = [MARGIN_LEFT], 0.0
    for column in columns:
        running += column.weight
        edges.append(MARGIN_LEFT + round(running * width / total))
    return edges


def resolve_timestamp(value) -> datetime:
    if isinstance(value, str) and value.strip().lower() == "now":
        return datetime.now().replace(second=0, microsecond=0)
    return parse_datetime(value)


def build_rows(config: Config) -> list[dict]:
    """Produce every table row from the CSV or the generator."""
    start = parse_datetime(config.start)
    if config.csv:
        rows = load_csv(config.csv, config.columns)
    else:
        unknown = [c.label for c in config.columns
                   if c.key not in TEAM_KEYS and c.key not in ("match", "field", "time")]
        if unknown:
            raise VexCopyError(
                f"Column(s) {', '.join(unknown)} can only be filled from a CSV file."
            )
        teams = team_list(config.teams)
        if config.matches is not None:
            matches = int(config.matches)
        elif config.rounds is not None:
            matches = int(config.rounds) * (len(teams) // 4)
        else:
            matches = 10 * (len(teams) // 4)
        rows = pairing_rows(generate_pairings(teams, matches, config.seed, config.min_gap))
    return fill_rows(rows, start, config.cycle_minutes, config.breaks,
                     list(config.fields), config.match_prefix)


@dataclass
class LogoImage:
    pixels: bytes
    width: int
    height: int
    draw_width: float
    draw_height: float


def load_logo(path: str) -> LogoImage | None:
    if not path:
        return None
    try:
        image = Image.open(path)
        image.load()
    except FileNotFoundError:
        raise VexCopyError(f"Logo file not found: {path}") from None
    except OSError as exc:
        raise VexCopyError(f"Could not read logo {path}: {exc}") from None
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        flat = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        flat.alpha_composite(rgba)
        image = flat
    image = image.convert("RGB")
    fit = min(LOGO_BOX_WIDTH / image.width, LOGO_BOX_HEIGHT / image.height)
    return LogoImage(image.tobytes(), image.width, image.height,
                     image.width * fit, image.height * fit)


class Report:
    def __init__(self, config: Config, rows: list[dict]):
        self.config = config
        self.rows = rows
        self.warnings: list[str] = []
        self.bold = Face(*split_font(config.font_bold), "TT1", "AAAAAB")
        self.oblique = Face(*split_font(config.font_oblique), "TT2", "AAAAAC")
        self.regular = Face(*split_font(config.font_regular), "TT3", "AAAAAD")
        self.edges = column_edges(config.columns)
        self.logo = load_logo(config.logo)
        self.moment = resolve_timestamp(config.timestamp)
        self._overflowed: set[str] = set()

        rpp = config.rows_per_page
        if not isinstance(rpp, int) or not 1 <= rpp <= MAX_ROWS_PER_PAGE:
            raise VexCopyError(f"rows_per_page must be between 1 and {MAX_ROWS_PER_PAGE}.")
        if not rows:
            raise VexCopyError("There are no matches to print.")
        self.page_rows = [rows[i:i + rpp] for i in range(0, len(rows), rpp)]

    # ---- text ------------------------------------------------------------

    def text(self, face: Face, size: int, x: float, top: float, value: str,
             skew: float = 0.0) -> bytes:
        """Draw one line of text. Ligatures become /ActualText spans."""
        if not value:
            return b""
        baseline = top + face.ascent * size
        glyphs = face.shape(value)
        cm = f"q {TEXT_CM} {num(px(x))} {num(py(baseline))} cm BT ".encode()
        shear = num(size * skew) if skew else "0"

        def run(chunk, offset):
            spacing, operator = text_operator(chunk, face)
            head = (f"{spacing}{size} 0 {shear} {size} {num(offset) if offset else 0} 0 Tm "
                    f"/{face.resource} 1 Tf ")
            return cm + head.encode() + operator + b" ET Q"

        parts, chunk, chunk_start, pen = [], [], 0.0, 0.0
        for glyph in glyphs:
            if len(glyph.source) > 1:
                if chunk:
                    parts.append(run(chunk, chunk_start))
                    chunk = []
                actual = glyph.source.encode("ascii")
                parts.append(b"/Span << /ActualText (" + actual + b") >> BDC "
                             + run([glyph], pen) + b" EMC")
                chunk_start = pen + glyph.advance * size / 1000
            else:
                if not chunk:
                    chunk_start = pen
                chunk.append(glyph)
            pen += glyph.advance * size / 1000
        if chunk:
            parts.append(run(chunk, chunk_start))
        return b" ".join(parts)

    def cell_text(self, face: Face, top: int, column: int, value: str) -> bytes:
        """Draw centred, clipped cell text using the reference's centering rule."""
        left = self.edges[column] + CELL_PADDING
        width = self.edges[column + 1] - self.edges[column] - 2 * CELL_PADDING
        height = ROW_HEIGHT - 2 * CELL_PADDING
        text_top = top + CELL_PADDING
        measured = round(face.width(value, CELL_SIZE)) if value else 0
        if measured > width and value not in self._overflowed:
            self._overflowed.add(value)
            self.warnings.append(f"{value!r} is too wide for its column and will be cut off.")
        x = left + (width - measured) // 2
        clip = (f"q {num(px(left))} {num(py(text_top + height))} "
                f"{num(width * SCALE_X)} {num(height * SCALE_Y)} re W n /Cs1 cs 0 0 0 sc ")
        return clip.encode() + self.text(face, CELL_SIZE, x, text_top, value) + b" Q"

    # ---- lines -----------------------------------------------------------

    @staticmethod
    def line(x1: int, y1: int, x2: int, y2: int) -> str:
        return f"q {LINE_CM} {x1} {y1} m {x2} {y2} l S Q"

    def cell_borders(self, top: int, column: int) -> list[str]:
        bottom = top + ROW_HEIGHT
        left, right = self.edges[column], self.edges[column + 1]
        lines = []
        if column == 0:
            lines.append(self.line(left, top, left, bottom))
        lines.append(self.line(right, top, right, bottom))
        lines.append(self.line(left, bottom, right + 1, bottom))
        return lines

    def table_row(self, top: int, values: list[str], face: Face, opening: str) -> list[bytes]:
        """Draw a row, alternating border and text groups like the reference."""
        ops: list[bytes] = []
        for column, value in enumerate(values):
            borders = " ".join(self.cell_borders(top, column))
            if column == 0:
                ops.append(f"{opening}{borders} Q".encode())
            else:
                ops.append(f"q {PAGE_CLIP} 1 J 1 j /Cs1 CS 0 0 0 SC {borders} Q".encode())
            ops.append(self.cell_text(face, top, column, value))
        return ops

    # ---- page ------------------------------------------------------------

    def page(self, number: int, rows: list[dict], form_name: bytes | None) -> bytes:
        ops: list[bytes] = [b"q Q q /Cs1 cs 0 0 0 sc"]
        ops.append(self.text(self.bold, TITLE_SIZE, MARGIN_LEFT, TITLE_TOP, self.config.title))
        ops.append(self.text(self.oblique, EVENT_SIZE, MARGIN_LEFT, EVENT_TOP,
                             self.config.event_name, OBLIQUE_SKEW))
        ops.append(b"Q")

        rules = f"{self.line(MARGIN_LEFT, RULE_1_Y, MARGIN_RIGHT, RULE_1_Y)} " \
                f"{self.line(MARGIN_LEFT, RULE_2_Y, MARGIN_RIGHT, RULE_2_Y)} "
        form = f"/{form_name.decode()} Do " if form_name else ""
        opening = f"q {form}1 J 1 j /Cs1 CS 0 0 0 SC {rules}"
        labels = [c.label for c in self.config.columns]
        ops += self.table_row(HEADER_TOP, labels, self.bold, opening)

        first_top = HEADER_TOP + ROW_HEIGHT
        thick = self.line(MARGIN_LEFT, first_top, MARGIN_RIGHT, first_top)
        for index, row in enumerate(rows):
            top = first_top + index * ROW_HEIGHT
            if index == 0:
                opening = (f"q {PAGE_CLIP} {HEADER_RULE_WIDTH} w 1 J 1 j /Cs1 CS 0 0 0 SC "
                           f"{thick} 1 w ")
            else:
                opening = f"q {PAGE_CLIP} 1 J 1 j /Cs1 CS 0 0 0 SC "
            values = [row.get(c.key, "") for c in self.config.columns]
            ops += self.table_row(top, values, self.regular, opening)

        total = len(self.page_rows)
        stamp = format_timestamp(self.moment)
        stamp_x = MARGIN_RIGHT - round(self.regular.width(stamp, FOOTER_SIZE))
        ops.append(f"q {PAGE_CLIP} /Cs1 cs 0 0 0 sc".encode())
        ops.append(self.text(self.regular, FOOTER_SIZE, MARGIN_LEFT, FOOTER_TOP,
                             f"Page {number} of {total}"))
        ops.append(self.text(self.regular, FOOTER_SIZE, stamp_x, FOOTER_TOP, stamp))
        ops.append(f"1 J 1 j /Cs1 CS 0 0 0 SC "
                   f"{self.line(MARGIN_LEFT, FOOTER_RULE_Y, MARGIN_RIGHT, FOOTER_RULE_Y)} Q".encode())
        return b" ".join(op for op in ops if op)

    def _check_header_fit(self) -> None:
        limit = MARGIN_RIGHT
        if self.logo:
            limit -= round(self.logo.draw_width)
        for label, face, size, value in (("Title", self.bold, TITLE_SIZE, self.config.title),
                                         ("Event name", self.oblique, EVENT_SIZE,
                                          self.config.event_name)):
            if MARGIN_LEFT + face.width(value, size) > limit:
                where = "under the logo" if self.logo else "past the right margin"
                self.warnings.append(f"{label} {value!r} is long enough to run {where}.")

    # ---- document --------------------------------------------------------

    def build(self) -> bytes:
        self._check_header_fit()
        doc = Document()
        pages_id = doc.reserve()

        icc_output = (ASSETS_DIR / "output_rgb.icc").read_bytes()
        icc_group = (ASSETS_DIR / "group_rgb.icc").read_bytes()
        output_cs = doc.add(b"[ /ICCBased %d 0 R ]" % doc.add_stream(
            b"/N 3 /Alternate /DeviceRGB", icc_output))
        group_cs = doc.add(b"[ /ICCBased %d 0 R ]" % doc.add_stream(
            b"/N 3 /Alternate /DeviceRGB", icc_group))

        image_id = None
        if self.logo:
            image_id = doc.add_stream(
                b"/Type /XObject /Subtype /Image /Width %d /Height %d /Interpolate true"
                b" /ColorSpace %d 0 R /BitsPerComponent 8"
                % (self.logo.width, self.logo.height, output_cs),
                self.logo.pixels,
            )

        page_streams = []
        for number, rows in enumerate(self.page_rows, start=1):
            form_name = b"Fm%d" % number if self.logo else None
            page_streams.append((number, form_name, self.page(number, rows, form_name)))

        # Fonts are built last, once every character on every page is known.
        fonts = (b"<< /TT1 %d 0 R /TT2 %d 0 R /TT3 %d 0 R >>"
                 % (doc.add_font(self.bold), doc.add_font(self.oblique), doc.add_font(self.regular)))

        page_ids = []
        for number, form_name, stream in page_streams:
            xobjects = b""
            if self.logo:
                form_id = self._logo_form(doc, image_id, group_cs)
                xobjects = b" /XObject << /" + form_name + b" %d 0 R >>" % form_id
            resources = doc.add(b"<< /ProcSet [ /PDF /Text ] /ColorSpace << /Cs1 %d 0 R >> /Font "
                                % output_cs + fonts + xobjects + b" >>")
            content = doc.add_stream(b"", stream)
            page_ids.append(doc.add(
                b"<< /Type /Page /Parent %d 0 R /Resources %d 0 R /Contents %d 0 R"
                b" /MediaBox [0 0 %d %d] >>"
                % (pages_id, resources, content, PAGE_WIDTH, PAGE_HEIGHT)))

        kids = b" ".join(b"%d 0 R" % i for i in page_ids)
        doc.add(b"<< /Type /Pages /MediaBox [0 0 %d %d] /Count %d /Kids [ %s ] >>"
                % (PAGE_WIDTH, PAGE_HEIGHT, len(page_ids), kids), pages_id)
        root = doc.add(b"<< /Type /Catalog /Pages %d 0 R /Version /1.4 >>" % pages_id)

        date = pdf_date(self.moment.astimezone().astimezone(timezone.utc))
        pdf_title = self.config.pdf_title if self.config.pdf_title is not None else self.config.title
        entries = [b"/Title " + text_string(pdf_title)]
        entries.append(b"/Producer " + text_string("vexcopy"))
        if self.config.pdf_author:
            entries.append(b"/Author " + text_string(self.config.pdf_author))
        if self.config.pdf_creator:
            entries.append(b"/Creator " + text_string(self.config.pdf_creator))
        entries.append(b"/CreationDate (" + date + b") /ModDate (" + date + b")")
        info = doc.add(b"<< " + b" ".join(entries) + b" >>")
        return doc.serialize(root, info)

    def _logo_form(self, doc: Document, image_id: int, group_cs: int) -> int:
        """Build the logo form XObject, top aligned and flush with the right margin."""
        width_units, height_units = self.logo.draw_width, self.logo.draw_height
        x1, x2 = px(MARGIN_RIGHT - width_units), px(MARGIN_RIGHT)
        y2, y1 = py(LOGO_TOP), py(LOGO_TOP + height_units)
        clip_x, clip_y = floor(x1), floor(y1)
        clip_w, clip_h = ceil(x2) - clip_x, ceil(y2) - clip_y
        content = (f"q Q q {clip_x} {clip_y} {clip_w} {clip_h} re W n /Perceptual ri "
                   f"q {num(x2 - x1)} 0 0 {num(y2 - y1)} {num(x1)} {num(y1)} cm /Im1 Do Q Q").encode()
        resources = doc.add(b"<< /ProcSet [ /PDF /ImageB /ImageC /ImageI ] /XObject << /Im1 %d 0 R >> >>"
                            % image_id)
        bbox = f"{num(x1)} {num(y1)} {num(x2)} {num(y2)}".encode()
        return doc.add_stream(
            b"/Type /XObject /Subtype /Form /FormType 1 /BBox [" + bbox + b"] /Resources %d 0 R"
            b" /Group << /S /Transparency /CS %d 0 R /I true /K false >>" % (resources, group_cs),
            content,
        )


def render(config: Config) -> tuple[bytes, list[str], list[dict]]:
    """Build the PDF. Returns its bytes, any warnings, and the printed rows."""
    rows = build_rows(config)
    report = Report(config, rows)
    return report.build(), report.warnings, rows
