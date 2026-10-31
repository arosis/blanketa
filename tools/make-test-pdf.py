"""Builds samples-pdf/zadost.pdf: a small Czech AcroForm (text, checkbox, radio, choice, signature field)."""
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "samples-pdf/zadost.pdf")
pdfmetrics.registerFont(TTFont("Arial", "/System/Library/Fonts/Supplemental/Arial.ttf"))
c = canvas.Canvas(str(out), pagesize=A4)
c.setTitle("Žádost o vydání potvrzení (test)")
c.setFont("Arial", 16)
c.drawString(60, 790, "Žádost o vydání potvrzení – testovací formulář")
c.setFont("Arial", 10)
form = c.acroForm
y = 740
for label, name in [("Jméno a příjmení", "jmeno"), ("Datum narození", "datum_narozeni"), ("Adresa", "adresa"), ("E-mail", "email")]:
    c.drawString(60, y + 6, label)
    form.textfield(name=name, x=200, y=y, width=320, height=20, borderColor=None, fillColor=None, fontName="Helvetica", fontSize=10)
    y -= 34
c.drawString(60, y + 6, "Způsob doručení")
for i, (value, label) in enumerate([("posta", "poštou"), ("datovka", "datovou schránkou"), ("osobne", "osobně")]):
    form.radio(name="doruceni", value=value, selected=i == 1, x=200 + i * 110, y=y, size=14, buttonStyle="circle")
    c.drawString(218 + i * 110, y + 3, label)
y -= 34
c.drawString(60, y + 6, "Účel")
form.choice(name="ucel", options=["Pro banku", "Pro soud", "Ostatní"], value="Pro banku", x=200, y=y, width=200, height=20,
            fontName="Helvetica")
y -= 34
form.checkbox(name="souhlas", x=60, y=y, size=14, checked=False, buttonStyle="check")
c.drawString(82, y + 3, "Souhlasím se zpracováním osobních údajů")
y -= 60
c.drawString(60, y + 50, "Podpis žadatele")
c.rect(200, y, 220, 60)
c.showPage()
c.save()

from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields

with open(out, "rb+") as f:
    w = IncrementalPdfFileWriter(f)
    fields.append_signature_field(w, fields.SigFieldSpec("podpis_zadatele", on_page=0, box=(200, y, 420, y + 60)))
    w.write_in_place()
print(out)
