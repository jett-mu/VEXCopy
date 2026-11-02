"""Compare a generated PDF against the reference: content streams, then pixels."""

import difflib
import re
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from PIL import Image, ImageChops


def page_streams(path):
    data = Path(path).read_bytes()
    objects = {int(m[1]): m[2] for m in re.finditer(rb"(\d+) 0 obj\s*(.*?)\s*endobj", data, re.S)}
    streams = []
    for body in (objects[k] for k in sorted(objects)):
        if b"/Type /Page " in body and b"/Contents" in body:
            content = objects[int(re.search(rb"/Contents (\d+)", body)[1])]
            raw = re.search(rb"stream\r?\n(.*?)\r?\nendstream", content, re.S)[1]
            streams.append(re.sub(rb"\s+", b" ", zlib.decompress(raw)))
    return streams


def tokens(stream):
    return re.findall(rb"\((?:\\.|[^\\)])*\)|[^\s()]+", stream)


def stream_report(reference, generated, show=12):
    """Return (identical pages, total token differences, examples)."""
    ref, gen = page_streams(reference), page_streams(generated)
    identical = changed = 0
    examples = []
    for number, (a, b) in enumerate(zip(ref, gen), start=1):
        if a == b:
            identical += 1
            continue
        ta, tb = tokens(a), tokens(b)
        matcher = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "equal":
                changed += 1
                if len(examples) < show:
                    context = b" ".join(ta[max(i1 - 4, 0):i1]).decode("latin1")
                    examples.append((number, context, b" ".join(ta[i1:i2]).decode("latin1"),
                                     b" ".join(tb[j1:j2]).decode("latin1")))
    return len(ref), len(gen), identical, changed, examples


def pixel_report(reference, generated, dpi=300):
    """Return (page, differing pixels, pixels off by 32+ levels, bbox) for each page."""
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, path in (("a", reference), ("b", generated)):
            subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(path), f"{tmp}/{name}"],
                           check=True)
        for a_path in sorted(Path(tmp).glob("a-*.png")):
            b_path = a_path.with_name("b" + a_path.name[1:])
            a, b = Image.open(a_path).convert("L"), Image.open(b_path).convert("L")
            diff = ImageChops.difference(a, b)
            histogram = diff.histogram()
            results.append((int(a_path.stem.split("-")[1]), sum(histogram[1:]),
                            sum(histogram[32:]), diff.getbbox()))
    return results


if __name__ == "__main__":
    reference, generated = sys.argv[1:3]
    pages_a, pages_b, same, changed, examples = stream_report(reference, generated)
    print(f"streams: {pages_a}/{pages_b} pages, {same} byte-identical, {changed} differing spans")
    for number, context, a, b in examples:
        print(f"  p{number} after [{context}]\n     ref:  {a}\n     mine: {b}")
    for page, count, big, bbox in pixel_report(reference, generated):
        print(f"  page {page}: {count} differing pixels, {big} by 32+ levels, bbox {bbox}")
