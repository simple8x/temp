"""Builds small PDFs with hand-written content streams for tests.

The expected geometry in tests is computed by hand from these content streams, never from
the code under test.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pikepdf
from pikepdf import Array, Dictionary, Name


@dataclass
class Form:
    content: bytes
    matrix: tuple | None = None
    bbox: tuple = (0, 0, 1000, 1000)
    xobjects: dict[str, "Form"] = field(default_factory=dict)


@dataclass
class PageSpec:
    content: bytes
    size: tuple = (200, 100)
    mediabox: tuple | None = None
    cropbox: tuple | None = None
    rotate: int | None = None
    userunit: float | None = None
    forms: dict[str, Form] = field(default_factory=dict)
    extgstate: dict[str, dict] = field(default_factory=dict)
    image_names: tuple[str, ...] = ()


def _make_form(pdf: pikepdf.Pdf, f: Form):
    s = pdf.make_stream(f.content)
    s.Type = Name.XObject
    s.Subtype = Name.Form
    s.BBox = Array(list(f.bbox))
    if f.matrix is not None:
        s.Matrix = Array(list(f.matrix))
    if f.xobjects:
        s.Resources = Dictionary(XObject=Dictionary(
            {("/" + k): _make_form(pdf, v) for k, v in f.xobjects.items()}))
    return s


def build_pdf(pages: list[PageSpec], *, encrypt: tuple[str, str] | None = None) -> bytes:
    pdf = pikepdf.new()
    for spec in pages:
        pdf.add_blank_page(page_size=spec.size)
        page = pdf.pages[-1]
        if spec.mediabox is not None:
            page.obj.MediaBox = Array(list(spec.mediabox))
        if spec.cropbox is not None:
            page.obj.CropBox = Array(list(spec.cropbox))
        if spec.rotate is not None:
            page.obj.Rotate = spec.rotate
        if spec.userunit is not None:
            page.obj.UserUnit = spec.userunit
        res = Dictionary()
        if spec.forms or spec.image_names:
            xo = Dictionary()
            for k, v in spec.forms.items():
                xo["/" + k] = _make_form(pdf, v)
            for k in spec.image_names:
                img = pdf.make_stream(b"\x00")
                img.Type = Name.XObject
                img.Subtype = Name.Image
                img.Width = 1
                img.Height = 1
                img.ColorSpace = Name.DeviceGray
                img.BitsPerComponent = 8
                xo["/" + k] = img
            res["/XObject"] = xo
        if spec.extgstate:
            res["/ExtGState"] = Dictionary({("/" + k): Dictionary(v) for k, v in spec.extgstate.items()})
        if len(res.keys()):
            page.obj.Resources = res
        page.obj.Contents = pdf.make_stream(spec.content)
    buf = io.BytesIO()
    if encrypt:
        pdf.save(buf, encryption=pikepdf.Encryption(user=encrypt[0], owner=encrypt[1]))
    else:
        pdf.save(buf)
    return buf.getvalue()


def one_page(content: bytes, **kw) -> bytes:
    return build_pdf([PageSpec(content=content, **kw)])
