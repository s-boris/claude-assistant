#!/usr/bin/env python3
"""Generate test bills for ~/bin/bills extract.  Run: ~/opt/bills-venv/bin/python -I make_samples.py OUTDIR

Fictional data, SIX example account numbers/references (valid checksums):
  qrbill-swisscom.pdf     A4 German invoice + QR payment part, QRR ref, CHF 59.90, "Zahlbar bis 31.10.2026", Swico S1 info
  qrbill-scor-eur.pdf     2 pages, English, QR on page 2, SCOR ref, EUR 1'234.50, "Payable by 15 November 2026", umlauts
  qrbill-open-amount.pdf  QR-bill without amount and without due date (donation slip)
  qrbill-mahnung.pdf      reminder letter with two different dates near due-date words (must stay empty)
  qrbill-swico-only.pdf   no due date in the text, only Swico /11/ + /40/0:30 in the QR bill information
  invoice-text.pdf        plain invoice without QR: IBAN, RF ref, total, "Zahlbar innert 30 Tagen" + Rechnungsdatum
  qrbill-scan.pdf         image-only PDF (no text layer) of qrbill-swisscom.pdf
  qrbill-photo.jpg        phone-photo-like JPEG of the swisscom bill (rotated, scaled, blurred, recompressed)
  no-qr-photo.jpg         image without any QR code
"""
import io, os, subprocess, sys, tempfile
from qrbill import QRBill
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPDF
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from PIL import Image, ImageDraw, ImageFilter
from reportlab import rl_config

rl_config.invariant = 1   # fixed PDF dates and document id: the same generator makes the same bytes

OUT = sys.argv[1] if len(sys.argv) > 1 else "samples"
os.makedirs(OUT, exist_ok=True)
DEBTOR = {"name": "Max Muster", "street": "Musterweg", "house_num": "1", "pcode": "1234",
          "city": "Exampletown", "country": "CH"}


def payment_part(bill):
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        path = f.name
    bill.as_svg(path)
    d = svg2rlg(path)
    os.unlink(path)
    return d


def letter(c, lines, top=270):
    y = top * mm
    for ln in lines:
        if isinstance(ln, tuple):
            font, size, txt = ln
        else:
            font, size, txt = "Helvetica", 10, ln
        c.setFont(font, size)
        c.drawString(20 * mm, y, txt)
        y -= size * 1.5


def invoice(path, pages, bill=None):
    c = canvas.Canvas(path, pagesize=A4)
    for i, lines in enumerate(pages):
        letter(c, lines)
        if bill is not None and i == len(pages) - 1:
            renderPDF.draw(payment_part(bill), c, 0, 0)
        c.showPage()
    c.save()


# 1. Swisscom, German, QRR
swisscom = QRBill(
    account="CH44 3199 9123 0008 8901 2", language="de", amount="59.90",
    creditor={"name": "Swisscom (Schweiz) AG", "street": "Alte Tiefenaustrasse", "house_num": "6",
              "pcode": "3048", "city": "Worblaufen", "country": "CH"},
    debtor=DEBTOR, reference_number="210000000003139471430009017",
    additional_information="Rechnung Oktober 2026",
    billing_information="//S1/10/10201409/11/261001/20/1400.000-53/40/0:30")
invoice(f"{OUT}/qrbill-swisscom.pdf", [[
    ("Helvetica-Bold", 14, "Swisscom (Schweiz) AG"), "Alte Tiefenaustrasse 6, 3048 Worblaufen", "",
    "Max Muster", "Musterweg 1", "1234 Exampletown", "",
    ("Helvetica-Bold", 12, "Rechnung Oktober 2026"),
    "Kundennummer: 1400.000-53            Rechnungsnummer: 10201409",
    "Rechnungsdatum: 01.10.2026", "",
    "inOne mobile go                                        CHF 55.40",
    "MWST 8.1%                                               CHF  4.50",
    ("Helvetica-Bold", 10, "Total zu bezahlen                                      CHF 59.90"), "",
    "Zahlbar bis 31.10.2026", "Vielen Dank, dass Sie Swisscom gewählt haben."]], swisscom)

# 2. SCOR, EUR, English, two pages, QR on page 2
scor = QRBill(
    account="CH58 0079 1123 0008 8901 2", language="en", amount="1234.50", currency="EUR",
    creditor={"name": "Zahnarztpraxis Müller & Söhne GmbH", "street": "Bahnhofstrasse", "house_num": "12",
              "pcode": "8200", "city": "Examplecity", "country": "CH"},
    debtor=DEBTOR, reference_number="RF18539007547034", additional_information="Invoice 2026-0815")
invoice(f"{OUT}/qrbill-scor-eur.pdf", [
    [("Helvetica-Bold", 14, "Zahnarztpraxis Müller & Söhne GmbH"), "Bahnhofstrasse 12, 8200 Examplecity", "",
     ("Helvetica-Bold", 12, "Invoice 2026-0815"), "Invoice date: 25 September 2026", "",
     "Treatment 22.09.2026                                    EUR 1'100.00",
     "Materials                                               EUR   134.50"],
    [("Helvetica-Bold", 10, "Total amount due                                        EUR 1'234.50"), "",
     "Payable by 15 November 2026. Thank you."]], scor)

# 3. open amount, no due date
open_amt = QRBill(
    account="CH44 3199 9123 0008 8901 2", language="de",
    creditor={"name": "Verein Riverside Freunde", "street": "Seequai", "house_num": "1",
              "pcode": "1234", "city": "Exampletown", "country": "CH"},
    reference_number="210000000003139471430009017", additional_information="Spende")
invoice(f"{OUT}/qrbill-open-amount.pdf", [[("Helvetica-Bold", 14, "Verein Riverside Freunde"),
                                          "Herzlichen Dank für Ihre Spende."]], open_amt)

# 4. Mahnung with two candidate dates
mahn = QRBill(
    account="CH44 3199 9123 0008 8901 2", language="de", amount="85.00",
    creditor={"name": "Fitnesspark Examplecity AG", "street": "Industriestrasse", "house_num": "3",
              "pcode": "8200", "city": "Examplecity", "country": "CH"},
    debtor=DEBTOR, reference_number="210000000003139471430009017", additional_information="1. Mahnung")
invoice(f"{OUT}/qrbill-mahnung.pdf", [[("Helvetica-Bold", 14, "Fitnesspark Examplecity AG"), "",
    ("Helvetica-Bold", 12, "1. Zahlungserinnerung"),
    "Unsere Rechnung vom 01.09.2026 war fällig am 30.09.2026.",
    "Bitte bezahlen Sie den Betrag von CHF 85.00 bis 20.10.2026."]], mahn)

# 5. due date only in the Swico bill information
swico = QRBill(
    account="CH44 3199 9123 0008 8901 2", language="de", amount="120.00",
    creditor={"name": "Stadtwerke Beispiel AG", "street": "Werkstrasse", "house_num": "5",
              "pcode": "8200", "city": "Examplecity", "country": "CH"},
    debtor=DEBTOR, reference_number="210000000003139471430009017",
    billing_information="//S1/10/555/11/261005/40/2:10;0:30")
invoice(f"{OUT}/qrbill-swico-only.pdf", [[("Helvetica-Bold", 14, "Stadtwerke Beispiel AG"),
                                         "Stromrechnung Q3 2026"]], swico)

# 6. text-only invoice (no QR code)
invoice(f"{OUT}/invoice-text.pdf", [[
    ("Helvetica-Bold", 14, "Garage Rhyblick GmbH"), "Zentralstrasse 40, 1234 Exampletown", "",
    "Max Muster, Musterweg 1, 1234 Exampletown", "",
    ("Helvetica-Bold", 12, "Rechnung Nr. 4711"), "Rechnungsdatum: 02.10.2026", "",
    "Service VW Golf                                    CHF 1'142.00",
    "MWST 8.1% auf CHF 1'142.00                         CHF    92.50",
    ("Helvetica-Bold", 10, "Rechnungsbetrag                                    CHF 1'234.50"), "",
    "Zahlbar innert 30 Tagen netto.",
    "Bankverbindung: IBAN CH93 0076 2011 6238 5295 7, Referenz RF18 5390 0754 7034"]])

# 7. scan: image-only PDF; 8. phone photo; 9. image without QR
with tempfile.TemporaryDirectory() as t:
    subprocess.run(["pdftoppm", "-r", "150", "-png", f"{OUT}/qrbill-swisscom.pdf", f"{t}/pg"], check=True)
    png = sorted(os.listdir(t))[0]
    subprocess.run(["img2pdf", "--nodate", "--engine", "internal", f"{t}/{png}", "-o", f"{OUT}/qrbill-scan.pdf"], check=True)
    subprocess.run(["pdftoppm", "-r", "220", "-png", f"{OUT}/qrbill-swisscom.pdf", f"{t}/hi"], check=True)
    im = Image.open(f"{t}/" + sorted(f for f in os.listdir(t) if f.startswith("hi"))[0]).convert("RGB")
    im = im.crop((0, int(im.height * 0.55), im.width, im.height))          # the payment part
    bg = Image.new("RGB", (im.width + 200, im.height + 200), (120, 110, 100))  # a table under the paper
    bg.paste(im, (100, 100))
    im = bg.rotate(4, expand=True, fillcolor=(120, 110, 100), resample=Image.BICUBIC)
    im = im.resize((int(im.width * 0.75), int(im.height * 0.75)), Image.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(0.8))
    im.save(f"{OUT}/qrbill-photo.jpg", quality=72)
noqr = Image.new("RGB", (1200, 800), "white")
dr = ImageDraw.Draw(noqr)
dr.text((50, 50), "Rechnung - kein QR-Code - Betrag CHF 42.00 - zahlbar bis 30.10.2026", fill="black")
noqr.save(f"{OUT}/no-qr-photo.jpg", quality=85)
print("samples in", OUT, ":", ", ".join(sorted(os.listdir(OUT))))
