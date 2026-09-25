"""
Generates a sample multi-page PDF that mimics a real business document
(e.g. an invoice bundle) so the pipeline can be demoed without needing
a real scanned file. Run once: python make_sample_pdf.py
"""
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import os

OUT_DIR = os.path.dirname(__file__)


def make_pdf(path, pages_text, title):
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter
    for i, text_lines in enumerate(pages_text):
        c.setFont("Helvetica-Bold", 16)
        c.drawString(72, height - 72, f"{title} - Page {i + 1}")
        c.setFont("Helvetica", 12)
        y = height - 110
        for line in text_lines:
            c.drawString(72, y, line)
            y -= 20
        c.showPage()
    c.save()


if __name__ == "__main__":
    invoice_pages = [
        [
            "Invoice #: INV-2026-0091",
            "Vendor: Bharat Business Solutions Pvt Ltd",
            "Date: 12-Sep-2026",
            "Description: Annual document archiving contract",
            "Amount: INR 84,500.00",
        ],
        [
            "Terms and Conditions",
            "Payment due within 30 days of invoice date.",
            "Late payments accrue 1.5% monthly interest.",
            "This page will be REMOVED in the demo pipeline.",
        ],
        [
            "Bank Details",
            "Account Name: Bharat Business Solutions Pvt Ltd",
            "Account No: XXXXXXXX2231",
            "IFSC: HDFC0001234",
        ],
    ]
    make_pdf(os.path.join(OUT_DIR, "sample_invoice.pdf"), invoice_pages, "Invoice")

    extra_pages = [
        [
            "Delivery Acknowledgement",
            "Received the above services in good order.",
            "Signed: Operations Manager",
            "This page will be APPENDED in the demo pipeline.",
        ]
    ]
    make_pdf(os.path.join(OUT_DIR, "extra_page.pdf"), extra_pages, "Acknowledgement")

    print("Created sample_invoice.pdf (3 pages) and extra_page.pdf (1 page)")
