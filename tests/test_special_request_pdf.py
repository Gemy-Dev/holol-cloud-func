"""The special-request PDF (spec 2038, contracts/special-request-pdf.md)."""
import io
import os

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from modules import config
from modules import special_request_pdf as pdf

FetchedAttachment = pdf.FetchedAttachment

REQUEST = {
    "sourceType": "report", "family": "sales", "clientName": "مستشفى الأمل",
    "representativeName": "أحمد", "reportDate": "2026-10-01T09:00:00.000",
    "requestText": "عشر علب قفازات للمختبر",
    "salesManagerDecision": {"decision": "approved", "deciderName": "هالة",
                             "decidedAt": "2026-10-01T10:05:00.000", "note": "موافق"},
    "adminDecision": {"decision": "approved", "deciderName": "سامي",
                      "decidedAt": "2026-10-01T10:20:00.000", "note": None},
}

REPORT = {
    "type": "product_presentation", "details": "عرض المنتج على الفريق الطبي",
    "problems": "تأخر التسليم", "clientOrders": "نص قديم لم يعد معتمدا",
    "theNextProcedure": "زيارة متابعة",
}

VISIT = {
    "id": "v1", "visitType": "maintenance", "visitDate": "2026-10-01T09:00:00.000",
    "technicianName": "فني الصيانة",
    "completionReport": {"visitDetails": "فحص الجهاز", "problemDescription": "عطل في المضخة",
                         "suggestedSolution": "استبدال المضخة", "nextProcedures": "إعادة الفحص"},
    "reviews": {"salesManager": {"reviewerName": "هالة", "reviewedAt": "2026-10-02T09:00:00.000",
                                 "note": "جيد"}},
    "resolution": {"solutionDetails": "تم الاستبدال", "resolvedByName": "سمير",
                   "resolvedAt": "2026-10-03T09:00:00.000"},
}


def _png(color=(200, 60, 60), size=(800, 500)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def _noise_jpeg(side=2000) -> bytes:
    out = io.BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(out, "JPEG", quality=95)
    return out.getvalue()


def _pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=300)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _build(request=REQUEST, source=REPORT, attachments=(), max_px=1600, quality=80) -> bytes:
    return pdf.build_special_request_pdf(
        request, source, list(attachments), image_max_px=max_px, jpeg_quality=quality)


def _texts(data: bytes) -> list:
    # pypdf's extraction is lossy for shaped Arabic: it can drop a trailing
    # glyph, and lines that mix digits or Latin letters lose pieces. The pages
    # render correctly (checked by eye); assertions use whole Arabic words that
    # extract intact.
    return [page.extract_text() for page in PdfReader(io.BytesIO(data)).pages]


class TestShaping:
    def test_arabic_is_shaped_and_in_logical_order(self):
        # Unshaped Arabic extracts reversed ("ليمعلا نم صاخ بلط"); shaped text
        # extracts as written, which only holds with the shaping engine on.
        first = _texts(_build())[0]
        assert "طلب خاص من العميل" in first
        assert "مستشفى الأمل" in first


class TestParts:
    def test_all_parts_and_the_decided_text(self):
        pages = _texts(_build())
        assert len(pages) == 2
        assert "الطلبات الخاصة" in pages[0] and "الموافقات" in pages[0]
        assert "عشر علب قفازات للمختبر" in pages[0]
        assert "التقرير الكامل" in pages[1]
        assert "تفاصيل المهمة" in pages[1]

    def test_both_approvals_with_deciders_and_note(self):
        first = _texts(_build())[0]
        for expected in ("مدير المبيعات", "مسؤول النظام", "هالة", "سامي", "موافق"):
            assert expected in first

    def test_the_report_prints_the_decided_text_not_the_current_one(self):
        report_page = _texts(_build())[1]
        assert "عشر علب قفازات للمختبر" in report_page
        assert "نص قديم لم يعد معتمدا" not in report_page

    def test_empty_fields_are_omitted(self):
        report_page = _texts(_build())[1]
        assert "الإجراء المتخذ" not in report_page  # actionTaken was empty
        assert "تفاصيل الحل" not in report_page  # not resolved

    def test_a_visit_renders_the_visit_labels(self):
        request = {**REQUEST, "sourceType": "visit", "family": "technical"}
        pages = _texts(_build(request, VISIT))
        assert "زيارة دعم فني" in pages[0]
        report_page = pages[1]
        for label in ("نوع الزيارة", "تاريخ الزيارة", "تفاصيل الزيارة", "المشكلة",
                      "الحل المقترح", "الإجراءات القادمة", "تفاصيل الحل", "مراجعة مدير المبيعات"):
            assert label in report_page, label
        assert "صيانة" in report_page and "فني الصيانة" in report_page

    def test_the_three_type_labels(self):
        assert pdf.type_label_for("report", "sales") == "تقرير مبيعات"
        assert pdf.type_label_for("report", "technical") == "تقرير دعم فني"
        assert pdf.type_label_for("visit", "technical") == "زيارة دعم فني"

    def test_the_activity_type_is_shown_for_a_report_only(self):
        assert "عرض المنتج للأطباء المؤثرين" in _texts(_build())[0]
        request = {**REQUEST, "sourceType": "visit", "family": "technical"}
        assert "نوع النشاط" not in _texts(_build(request, VISIT))[0]


class TestAttachments:
    def test_no_attachments_means_no_attachment_pages(self):
        assert len(_texts(_build())) == 2

    def test_page_count_photos_and_pdfs(self):
        # parts 1-2 + divider + one page per photo + (divider + its pages) per PDF
        attachments = [
            FetchedAttachment("صورة.png", "image", _png()),
            FetchedAttachment("صورة ثانية.png", "image", _png((20, 90, 200))),
            FetchedAttachment("عقد.pdf", "pdf", _pdf(3)),
        ]
        assert len(_texts(_build(attachments=attachments))) == 2 + 1 + 2 + (1 + 3)

    def test_the_divider_page_comes_after_the_report(self):
        attachments = [FetchedAttachment("صورة الموقع.png", "image", _png()),
                       FetchedAttachment("عقد.pdf", "pdf", _pdf(1))]
        divider = _texts(_build(attachments=attachments))[2]
        assert "المرفقات" in divider

    def test_attached_pdf_pages_follow_their_divider_unmodified(self):
        attachments = [FetchedAttachment("عقد.pdf", "pdf", _pdf(2)),
                       FetchedAttachment("صورة.png", "image", _png())]
        reader = PdfReader(io.BytesIO(_build(attachments=attachments)))
        sizes = [(round(float(p.mediabox.width)), round(float(p.mediabox.height)))
                 for p in reader.pages]
        # parts 1-2, divider, PDF divider, 2 appended 300x300 pages, photo page.
        assert sizes[4:6] == [(300, 300), (300, 300)]
        assert sizes[3] != (300, 300) and sizes[6] != (300, 300)
        assert reader.pages[2].extract_text().startswith("المرفقات")

    def test_unreadable_attachments_get_a_placeholder_and_never_raise(self):
        attachments = [FetchedAttachment("تالف.png", "image", b"not an image"),
                       FetchedAttachment("مفقود.pdf", "pdf", None),
                       FetchedAttachment("مكسور.pdf", "pdf", b"%PDF-broken")]
        pages = _texts(_build(attachments=attachments))
        assert len(pages) == 2 + 1 + 3
        assert all("تعذّر تضمين المرفق" in page for page in pages[3:])

    def test_a_photo_with_transparency_is_embedded(self):
        out = io.BytesIO()
        Image.new("RGBA", (300, 300), (255, 0, 0, 80)).save(out, "PNG")
        attachments = [FetchedAttachment("شفاف.png", "image", out.getvalue())]
        assert len(_texts(_build(attachments=attachments))) == 4


class TestFooter:
    @pytest.fixture
    def footers(self, monkeypatch):
        seen = []

        def record(self):
            seen.append((self.page_no() + self.offset, self.total_pages))

        monkeypatch.setattr(pdf._Document, "footer", record)
        return seen

    def test_numbers_run_through_appended_pages_and_the_total_counts_them(self, footers):
        attachments = [FetchedAttachment("عقد.pdf", "pdf", _pdf(2)),
                       FetchedAttachment("صورة.png", "image", _png())]
        data = _build(attachments=attachments)
        total = len(PdfReader(io.BytesIO(data)).pages)
        assert total == 2 + 1 + 3 + 1
        # The final render is the second one; the dry run recorded total 0.
        final = [entry for entry in footers if entry[1] == total]
        assert [number for number, _ in final] == [1, 2, 3, 4, 7]
        assert {t for _, t in final} == {total}

    def test_appended_pages_carry_no_footer_or_text(self):
        attachments = [FetchedAttachment("عقد.pdf", "pdf", _pdf(2))]
        pages = _texts(_build(attachments=attachments))
        assert "طلب خاص من العميل" in pages[0]
        assert pages[4] == "" and pages[5] == ""


class TestFetch:
    def test_entries_from_a_report_and_from_a_visit(self):
        report = {"attachments": [{"url": "u1", "name": "a", "type": "image"}]}
        visit = {"completionReport": {"attachments": [{"url": "u2", "name": "b", "type": "pdf"}]}}
        assert [e["url"] for e in pdf.attachment_entries(report)] == ["u1"]
        assert [e["url"] for e in pdf.attachment_entries(visit)] == ["u2"]

    def test_a_failed_download_keeps_its_place(self):
        raw = {"attachments": [{"url": "https://x", "name": "a.png", "type": "image"},
                               {"url": "https://y", "name": "", "type": "pdf"}]}

        def download(url):
            if url == "https://x":
                raise OSError("boom")
            return b"data"

        fetched = pdf.fetch_attachments(raw, download=download)
        assert fetched == [FetchedAttachment("a.png", "image", None),
                           FetchedAttachment("مرفق 2", "pdf", b"data")]

    @pytest.mark.parametrize("url", [
        "http://firebasestorage.googleapis.com/v0/b/x/o/y",
        "https://169.254.169.254/computeMetadata/v1/",
        "https://evil.example.com/file.png",
        "file:///etc/passwd",
        "",
    ])
    def test_only_https_firebase_storage_links_are_fetched(self, url):
        assert pdf._download(url) is None


class TestSizeLimit:
    def test_the_second_pass_is_used_when_the_first_is_too_big(self, monkeypatch):
        photos = [FetchedAttachment(f"{i}.jpg", "image", _noise_jpeg()) for i in range(3)]
        first = len(_build(attachments=photos, max_px=1600, quality=80))
        second = len(_build(attachments=photos, max_px=1024, quality=60))
        assert second < first
        monkeypatch.setattr(config, "SPECIAL_REQUEST_MAX_PDF_BYTES", (first + second) // 2)
        data = pdf.build_within_limit(REQUEST, REPORT, photos)
        assert len(data) <= config.SPECIAL_REQUEST_MAX_PDF_BYTES

    def test_an_oversize_result_raises_too_large(self, monkeypatch):
        photos = [FetchedAttachment("0.jpg", "image", _noise_jpeg())]
        monkeypatch.setattr(config, "SPECIAL_REQUEST_MAX_PDF_BYTES", 50_000)
        with pytest.raises(pdf.PdfTooLarge):
            pdf.build_within_limit(REQUEST, REPORT, photos)

    def test_a_small_pdf_uses_the_first_pass(self):
        data = pdf.build_within_limit(REQUEST, REPORT, [])
        assert len(PdfReader(io.BytesIO(data)).pages) == 2


class TestRobustness:
    def test_a_pdf_damaged_past_its_first_page_gets_a_placeholder(self, monkeypatch):
        data = _pdf(3)  # built before add_page is patched
        real_add_page = PdfWriter.add_page
        calls = {'n': 0}

        def flaky_add_page(self, page, *args, **kwargs):
            calls['n'] += 1
            # The attachment's second page breaks only once copied.
            if calls['n'] == 2:
                raise ValueError('broken page')
            return real_add_page(self, page, *args, **kwargs)

        monkeypatch.setattr(PdfWriter, 'add_page', flaky_add_page)
        assert pdf._pdf_reader(data) is None

    def test_downloads_past_the_budget_get_placeholders(self, monkeypatch):
        monkeypatch.setattr(config, 'ATTACHMENT_FETCH_BUDGET_S', 10)
        ticks = iter([0, 0, 5, 12, 12])
        raw = {'attachments': [{'url': f'u{i}', 'name': f'{i}.png', 'type': 'image'} for i in range(3)]}
        fetched = pdf.fetch_attachments(raw, download=lambda url: b'data',
                                        clock=lambda: next(ticks))
        assert [f.data for f in fetched] == [b'data', b'data', None]
