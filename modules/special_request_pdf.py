"""The special-request PDF (spec 2038, ``contracts/special-request-pdf.md``).

One file for the operations team, in three parts: the request with both
approvals, the complete report, and every attachment (photos as images, attached
PDFs appended page for page). Arabic is shaped with HarfBuzz and laid out
right-to-left in Cairo, the font the field app's own PDFs use.

The module is pure except for :func:`fetch_attachments`, which is the only thing
that touches the network. It never imports ``special_requests`` (that module
imports this one).
"""
import io
import os
import time
import urllib.request
from collections import namedtuple
from typing import Optional
from urllib.parse import urlparse

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter

from modules import config
from modules.dates import as_iso

# (name, type, bytes | None): a None payload is an attachment that could not be
# fetched, which the PDF shows as a placeholder page instead of failing.
FetchedAttachment = namedtuple("FetchedAttachment", ["name", "type", "data"])

# Firebase Storage download links only. The attachment list is written by the
# reps' own apps, so a link is data, not trust: anything else (an internal
# address, the metadata server) is refused instead of fetched.
_ALLOWED_ATTACHMENT_HOSTS = ("firebasestorage.googleapis.com", "storage.googleapis.com")
MAX_ATTACHMENT_BYTES = 30 * 1024 * 1024

_FOOTER = "طلب خاص من العميل"
_UNREADABLE = "تعذّر تضمين المرفق"

_TYPE_LABELS = {
    ("report", "sales"): "تقرير مبيعات",
    ("report", "technical"): "تقرير دعم فني",
    ("visit", "technical"): "زيارة دعم فني",
}

# The stored `type` of a report is the enum's wire value; the dashboard also
# accepts the Arabic name itself, so both resolve.
_ACTIVITY_LABELS = {
    "product_presentation": "عرض المنتج للأطباء المؤثرين",
    "support_within_operating_room": "الدعم داخل صالة العمليات",
    "follow_and_gather_info": "متابعة وجمع معلومات",
    "technical_support": "دعم فني",
    "call": "مكالمة هاتفية",
    "email": "إرسال بريد إلكتروني",
    "meeting": "اجتماع",
    "payment_follow_up": "متابعة تحصيل / مالي",
    "internal_task": "نشاط داخلي / إداري",
    "warehouse_activity": "نشاط مخزني",
    "public_relations": "علاقات عامة",
    "other": "أخرى",
}

_VISIT_TYPE_LABELS = {
    "maintenance": "صيانة",
    "periodicVisit": "زيارة دورية",
    "toolsDelivery": "تسليم أدوات",
}

_SLOT_LABELS = (("salesManagerDecision", "مدير المبيعات"), ("adminDecision", "مسؤول النظام"))
_DECISION_LABELS = {"approved": "موافقة", "rejected": "رفض"}

_MARGIN = 15
_PAGE_W = 210
_CONTENT_W = _PAGE_W - 2 * _MARGIN


class PdfTooLarge(Exception):
    """The PDF is over the size limit even after the smallest image pass."""


def type_label_for(source_type: str, family: str) -> str:
    """The Arabic report type shown in notifications and the PDF (research R12)."""
    return _TYPE_LABELS.get((source_type, family)) or _TYPE_LABELS[("report", "sales")]


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

def attachment_entries(raw: dict) -> list:
    """The attachment records of a report or visit, in stored order.

    A report keeps them at its top level; a visit keeps them on its completion
    report.
    """
    entries = raw.get("attachments")
    if not entries and isinstance(raw.get("completionReport"), dict):
        entries = raw["completionReport"].get("attachments")
    return [entry for entry in (entries or []) if isinstance(entry, dict)]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # never follow to another host
        return None


def _download(url: str) -> Optional[bytes]:
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_ATTACHMENT_HOSTS:
        return None
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": "special-request-pdf"})
    with opener.open(request, timeout=config.ATTACHMENT_FETCH_TIMEOUT_S) as response:
        data = response.read(MAX_ATTACHMENT_BYTES + 1)
    return None if len(data) > MAX_ATTACHMENT_BYTES else data


def fetch_attachments(raw: dict, download=_download, clock=time.monotonic) -> list:
    """Every attachment of ``raw`` as a :class:`FetchedAttachment`.

    One that cannot be fetched keeps its place with ``data=None``; it never
    stops the others or the PDF. Once the downloads have used
    ``ATTACHMENT_FETCH_BUDGET_S`` in total, the rest are not fetched (they get
    their placeholder), so a slow link cannot run the send out of time.
    """
    fetched = []
    started = clock()
    for position, entry in enumerate(attachment_entries(raw), start=1):
        name = (entry.get("name") or "").strip() or f"مرفق {position}"
        kind = "pdf" if entry.get("type") == "pdf" else "image"
        if clock() - started >= config.ATTACHMENT_FETCH_BUDGET_S:
            print(f"Attachment {name!r} skipped: download budget used up")
            fetched.append(FetchedAttachment(name, kind, None))
            continue
        try:
            data = download(entry.get("url") or "")
        except Exception as error:
            print(f"Could not fetch attachment {name!r}: {error}")
            data = None
        fetched.append(FetchedAttachment(name, kind, data))
    return fetched


class _Prepared:
    """One attachment, readied for a given image pass."""

    def __init__(self, index, name, kind, jpeg=None, size=None, reader=None):
        self.index = index
        self.name = name
        self.kind = kind  # 'photo' | 'pdf' | 'bad'
        self.jpeg = jpeg
        self.size = size
        self.reader = reader

    @property
    def appended_pages(self) -> int:
        return len(self.reader.pages) if self.kind == "pdf" else 0


def _photo(data: bytes, max_px: int, quality: int):
    """``(jpeg bytes, (w, h))`` for an image, downscaled, or None if unreadable."""
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
        image.load()
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[-1])
            image = background
        else:
            image = image.convert("RGB")
        image.thumbnail((max_px, max_px), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=quality, optimize=True)
        return out.getvalue(), image.size
    except Exception as error:
        print(f"Could not read an image attachment: {error}")
        return None


def _pdf_reader(data: bytes):
    """A reader for an attached PDF, or None when it cannot be appended.

    The whole file is copied once here: damage on a later page would otherwise
    surface only at assembly and fail the entire send, where the contract wants
    that one attachment's placeholder page instead.
    """
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or len(reader.pages) == 0:
            return None
        trial = PdfWriter()
        for page in reader.pages:
            trial.add_page(page)
        trial.write(io.BytesIO())
        return reader
    except Exception as error:
        print(f"Could not read a PDF attachment: {error}")
        return None


def _prepare(attachments, max_px: int, quality: int) -> list:
    prepared = []
    for index, attachment in enumerate(attachments, start=1):
        name, kind, data = attachment
        if data is None:
            prepared.append(_Prepared(index, name, "bad"))
        elif kind == "pdf":
            reader = _pdf_reader(data)
            prepared.append(_Prepared(index, name, "pdf", reader=reader) if reader
                            else _Prepared(index, name, "bad"))
        else:
            photo = _photo(data, max_px, quality)
            prepared.append(_Prepared(index, name, "photo", jpeg=photo[0], size=photo[1])
                            if photo else _Prepared(index, name, "bad"))
    return prepared


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _date(value) -> str:
    iso = as_iso(value)
    return iso[:10].replace("-", "/") if iso else ""


def _date_time(value) -> str:
    iso = as_iso(value)
    return f"{iso[:10].replace('-', '/')} {iso[11:16]}" if iso else ""


def _signoff_line(review) -> str:
    if not isinstance(review, dict):
        return ""
    parts = [_text(review.get("reviewerName")), _date_time(review.get("reviewedAt")),
             _text(review.get("note"))]
    return " — ".join(part for part in parts if part)


def filename_date(request: dict) -> str:
    """``yyyy-MM-dd`` of the report, for the attachment name."""
    return _date(request.get("reportDate")).replace("/", "-")


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

class _Document(FPDF):
    """A4, RTL, Cairo. The footer numbers generated pages among the final ones.

    ``total_pages`` counts every page of the finished file, appended attachment
    pages included, and ``pending_offset`` is how many of those sit before the
    next generated page; ``header`` applies it after the previous page's footer
    has been written.
    """

    def __init__(self, total_pages: int):
        super().__init__(format="A4")
        self.total_pages = total_pages
        self.offset = 0
        self.pending_offset = 0
        self.set_margins(_MARGIN, _MARGIN, _MARGIN)
        self.set_auto_page_break(True, margin=18)
        self.add_font("Cairo", "", os.path.join(config.FONTS_DIR, "Cairo-Regular.ttf"))
        self.add_font("Cairo", "B", os.path.join(config.FONTS_DIR, "Cairo-Bold.ttf"))
        self.set_text_shaping(use_shaping_engine=True, direction="rtl", script="arab",
                              language="ar")

    def header(self):
        self.offset = self.pending_offset

    def footer(self):
        self.set_y(-13)
        self.set_font("Cairo", "", 9)
        self.set_text_color(110, 110, 110)
        number = self.page_no() + self.offset
        self.cell(0, 8, f"صفحة {number} من {self.total_pages} — {_FOOTER}", align="C")
        self.set_text_color(0, 0, 0)


def _line(doc: _Document, text: str, *, bold=False, size=11, align="R", gap=1.5):
    doc.set_font("Cairo", "B" if bold else "", size)
    doc.multi_cell(0, 7, text, align=align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    doc.ln(gap)


def _title(doc: _Document, text: str):
    _line(doc, text, bold=True, size=18, gap=3)


def _field(doc: _Document, label: str, value: str):
    _line(doc, label, bold=True, size=11, gap=0.5)
    _line(doc, value, size=11, gap=3)


def _box(doc: _Document, heading: str, text: str):
    _line(doc, heading, bold=True, size=12, gap=1)
    doc.set_fill_color(247, 247, 247)
    doc.set_draw_color(200, 200, 200)
    doc.set_font("Cairo", "", 11)
    doc.multi_cell(0, 7, text, border=1, align="R", fill=True, padding=2,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    doc.ln(4)


def _table(doc: _Document, rows, widths):
    """Rows are given right-to-left; the table lays columns out left-to-right."""
    doc.set_font("Cairo", "", 11)
    with doc.table(col_widths=widths, text_align="RIGHT", first_row_as_headings=False,
                   line_height=7, borders_layout="HORIZONTAL_LINES") as table:
        for cells in rows:
            row = table.row()
            for cell in reversed(cells):
                row.cell(cell or "—")
    doc.ln(4)


# ---------------------------------------------------------------------------
# Parts
# ---------------------------------------------------------------------------

def _part_request(doc: _Document, request: dict, source: dict):
    doc.add_page()
    _title(doc, "طلب خاص من العميل")
    is_report = request.get("sourceType") == "report"
    rows = [
        ("العميل", _text(request.get("clientName"))),
        ("المندوب", _text(request.get("representativeName"))),
        ("تاريخ التقرير", _date(request.get("reportDate"))),
        ("نوع التقرير", type_label_for(request.get("sourceType"), request.get("family"))),
    ]
    if is_report:
        activity = _text(source.get("type"))
        rows.append(("نوع النشاط", _ACTIVITY_LABELS.get(activity, activity)))
    _table(doc, [[label, value] for label, value in rows if value or label != "نوع النشاط"],
           (125, 55))
    _box(doc, "الطلبات الخاصة", _text(request.get("requestText")))

    _line(doc, "الموافقات", bold=True, size=12, gap=1)
    approvals = []
    for field, label in _SLOT_LABELS:
        decision = request.get(field)
        if isinstance(decision, dict):
            approvals.append([
                label, _text(decision.get("deciderName")),
                _date_time(decision.get("decidedAt")), _text(decision.get("note")),
            ])
        else:
            approvals.append([label, "", "", ""])
    _table(doc, approvals, (60, 40, 45, 35))


def _report_fields(request: dict, source: dict) -> list:
    fields = [
        ("تفاصيل المهمة", _text(source.get("details"))),
        ("الإجراء المتخذ", _text(source.get("actionTaken"))),
        ("المشاكل ان وجدت", _text(source.get("problems"))),
        ("الحلول المقترحة", _text(source.get("suggestSolutions"))),
        # The decided text, never the source's current one: the PDF must not
        # carry two versions of the request.
        ("طلبات خاصة من العميل", _text(request.get("requestText"))),
        ("الإجراءات القادمة", _text(source.get("theNextProcedure"))),
    ]
    if source.get("solved") is True:
        solver = " — ".join(part for part in (
            _text(source.get("solvedByName")), _date_time(source.get("solvedAt"))) if part)
        fields += [
            ("حالة المشكلة", "تم الحل" + (f" — {solver}" if solver else "")),
            ("تفاصيل الحل", _text(source.get("solutionDetails"))),
        ]
    fields += [
        ("مراجعة مدير المبيعات", _signoff_line(source.get("salesManagerReview"))),
        ("مراجعة مسؤول النظام", _signoff_line(source.get("adminReview"))),
    ]
    return fields


def _visit_fields(request: dict, source: dict) -> list:
    report = source.get("completionReport") if isinstance(source.get("completionReport"), dict) else {}
    resolution = source.get("resolution") if isinstance(source.get("resolution"), dict) else {}
    reviews = source.get("reviews") if isinstance(source.get("reviews"), dict) else {}
    visit_type = _text(source.get("visitType"))
    fields = [
        ("نوع الزيارة", _VISIT_TYPE_LABELS.get(visit_type, visit_type)),
        ("تاريخ الزيارة", _date(source.get("visitDate"))),
        ("الفني", _text(source.get("technicianName"))),
        ("تفاصيل الزيارة", _text(report.get("visitDetails"))),
        ("المشكلة", _text(report.get("problemDescription"))),
        ("الحل المقترح", _text(report.get("suggestedSolution"))),
        ("طلبات خاصة من العميل", _text(request.get("requestText"))),
        ("الإجراءات القادمة", _text(report.get("nextProcedures"))),
    ]
    details = _text(resolution.get("solutionDetails")) or _text(report.get("solutionDetails"))
    if resolution or report.get("problemResolved") is True:
        solver = " — ".join(part for part in (
            _text(resolution.get("resolvedByName")) or _text(report.get("resolvedByName")),
            _date_time(resolution.get("resolvedAt") or report.get("resolvedAt"))) if part)
        fields += [
            ("حالة المشكلة", "تم الحل" + (f" — {solver}" if solver else "")),
            ("تفاصيل الحل", details),
        ]
    fields += [
        ("مراجعة مدير المبيعات", _signoff_line(reviews.get("salesManager"))),
        ("مراجعة مسؤول النظام", _signoff_line(reviews.get("admin"))),
    ]
    return fields


def _part_report(doc: _Document, request: dict, source: dict):
    doc.add_page()
    _title(doc, "التقرير الكامل")
    build = _report_fields if request.get("sourceType") == "report" else _visit_fields
    for label, value in build(request, source):
        if value:  # an empty field is left out, not printed blank
            _field(doc, label, value)


def _part_attachments(doc: _Document, prepared: list, inserts: dict, draw_images: bool):
    if not prepared:
        return
    doc.add_page()
    _title(doc, f"المرفقات ({len(prepared)})")
    for item in prepared:
        _line(doc, f"{item.index}. {item.name}")

    for item in prepared:
        doc.add_page()
        if item.kind == "photo":
            _photo_page(doc, item, draw_images)
        elif item.kind == "pdf":
            _title(doc, f"{item.index}. {item.name}")
            # Its own pages follow this divider in the finished file.
            inserts[doc.page_no() - 1] = item.reader
            doc.pending_offset += item.appended_pages
        else:
            _title(doc, f"{item.index}. {item.name}")
            _line(doc, _UNREADABLE, size=12)


def _photo_page(doc: _Document, item: _Prepared, draw_images: bool):
    caption_h = 14
    max_w = _CONTENT_W
    max_h = doc.h - 2 * _MARGIN - caption_h - 10
    width_px, height_px = item.size
    scale = min(max_w / width_px, max_h / height_px)
    width, height = width_px * scale, height_px * scale
    top = _MARGIN + 4
    if draw_images:
        doc.image(io.BytesIO(item.jpeg), x=_MARGIN + (max_w - width) / 2, y=top,
                  w=width, h=height)
    doc.set_y(top + height + 4)
    _line(doc, f"{item.index}. {item.name}", size=10)


def _render(request, source, prepared, total_pages, draw_images):
    doc = _Document(total_pages)
    inserts: dict = {}
    _part_request(doc, request, source)
    _part_report(doc, request, source)
    _part_attachments(doc, prepared, inserts, draw_images)
    generated = doc.page_no()
    return bytes(doc.output()), generated, inserts


def _assemble(generated: bytes, inserts: dict) -> bytes:
    if not inserts:
        return generated
    writer = PdfWriter()
    for index, page in enumerate(PdfReader(io.BytesIO(generated)).pages):
        writer.add_page(page)
        attached = inserts.get(index)
        if attached is not None:
            for extra in attached.pages:
                writer.add_page(extra)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def build_special_request_pdf(request: dict, source: dict, attachments: list, *,
                              image_max_px: int, jpeg_quality: int) -> bytes:
    """The finished PDF for ``request`` and its ``source`` report or visit.

    ``source`` is the report document, or for a visit its ``visitHistory`` entry
    with the parent record's ``reviews`` (``{salesManager, admin}``) and
    ``resolution`` added.

    Built twice: a first pass without photos counts the generated pages, so the
    footer's total can include the appended attachment pages.
    """
    prepared = _prepare(attachments, image_max_px, jpeg_quality)
    appended = sum(item.appended_pages for item in prepared)
    _, generated, _ = _render(request, source, prepared, 0, draw_images=False)
    data, _, inserts = _render(request, source, prepared, generated + appended,
                               draw_images=True)
    return _assemble(data, inserts)


def build_within_limit(request: dict, source: dict, attachments: list) -> bytes:
    """The PDF, at the first image pass that fits the size limit.

    Raises:
        PdfTooLarge: even the smallest pass is over the limit.
    """
    size = 0
    for max_px, quality in config.SPECIAL_REQUEST_IMAGE_PASSES:
        data = build_special_request_pdf(
            request, source, attachments, image_max_px=max_px, jpeg_quality=quality)
        size = len(data)
        if size <= config.SPECIAL_REQUEST_MAX_PDF_BYTES:
            return data
    raise PdfTooLarge(size)
