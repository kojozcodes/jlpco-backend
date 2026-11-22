"""
End of Hire and Vehicle Return document.

Produced when a hire is ended and merged onto the back of the agreement it
belongs to, so the operator gets one file. The agreement's own pages are never
touched: this is an extra page, not an edit.

Geometry is taken from the hire agreement template so the two read as one set
of paperwork, and the logo is lifted from the template at runtime rather than
kept as a second copy that could drift from it.
"""

import io
import os
import tempfile
from functools import lru_cache

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor, black
from reportlab.pdfgen import canvas

# Matches the hire agreement: body margins, label cell, value cell, row pitch.
LEFT, RIGHT = 46.25, 551.0
LABEL_X, LABEL_W = 59.75, 169.5
VALUE_X, VALUE_W = 230.0, 309.0
ROW_H = 27.75
CELL_PAD = 7.5

BODY_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"

NOTE_LINES = 5
NOTE_LINE_GAP = 22

# Lifted from the agreement template, so the merged file has its signature
# block in exactly the same place on every page. The block there is a two row
# table: the rule a signature sits on is the line between the rows, and the
# caption is centred in the row beneath it.
SIGNATURE_LINE_Y = 53.15
CAPTION_BASELINE_Y = 37.80
CAPTION_FONT_SIZE = 10
# Sized to fill the narrowest slot, so both signatures render at the same size
# and sit as prominently as the ones on the agreement's own pages.
SIGNATURE_WIDTH = 108
SIGNATURE_HEIGHT = 36

SIGNATURE_SLOTS = (
    (42.50, 152.75, "Hirer Signature", 'hirer_signature'),
    (164.75, 245.00, "Date", None),
    (262.25, 443.75, "Lessor Signature (JL PCO Limited)", 'lessor_signature'),
    (455.75, 551.00, "Date", None),
)


@lru_cache(maxsize=4)
def logo_from_template(template_path):
    """The JL PCO logo, read out of the agreement template. None if absent."""
    try:
        for image in PdfReader(template_path).pages[0].images:
            path = tempfile.NamedTemporaryFile(suffix='.png', delete=False).name
            with open(path, 'wb') as out:
                out.write(image.data)
            return path
    except Exception as e:
        print(f"Could not read the logo from the template: {e}")
    return None


@lru_cache(maxsize=4)
def template_page_size(template_path):
    """Match the template's page size so merged pages do not change dimensions."""
    try:
        box = PdfReader(template_path).pages[0].mediabox
        return float(box.width), float(box.height)
    except Exception:
        return 595.28, 841.89


def _draw_rows(c, page_height, y_top, pairs):
    """A label and value table in the agreement's style. Returns the new y."""
    c.setLineWidth(0.75)
    c.setStrokeColor(black)
    y = y_top
    for label, value in pairs:
        bottom = page_height - (y + ROW_H)
        c.rect(LABEL_X, bottom, LABEL_W, ROW_H, fill=0, stroke=1)
        c.rect(VALUE_X, bottom, VALUE_W, ROW_H, fill=0, stroke=1)
        c.setFont(BODY_FONT, 9)
        c.setFillColor(black)
        c.drawString(LABEL_X + CELL_PAD, bottom + 9.5, label)
        if value:
            c.drawString(VALUE_X + CELL_PAD, bottom + 9.5, str(value))
        y += ROW_H
    return y


def _wrap(text, font, size, max_width, max_lines):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    lines = []
    for paragraph in str(text).split('\n'):
        current = ''
        for word in paragraph.split():
            candidate = f'{current} {word}'.strip()
            if not current or stringWidth(candidate, font, size) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines[:max_lines]


def _draw_notes(c, page_height, y_top, notes):
    """
    The condition box. Typed notes print onto the lines; with nothing typed the
    lines are simply left empty to be written on by hand.
    """
    height = NOTE_LINES * NOTE_LINE_GAP + 10
    c.setStrokeColor(black)
    c.setLineWidth(0.75)
    c.rect(LABEL_X, page_height - (y_top + height), RIGHT - LABEL_X, height,
           fill=0, stroke=1)

    written = _wrap(notes or '', BODY_FONT, 9,
                    (RIGHT - CELL_PAD) - (LABEL_X + CELL_PAD), NOTE_LINES)

    for i in range(1, NOTE_LINES + 1):
        line_y = page_height - (y_top + i * NOTE_LINE_GAP)
        c.setStrokeColor(HexColor("#BBBBBB"))
        c.setLineWidth(0.5)
        c.line(LABEL_X + CELL_PAD, line_y, RIGHT - CELL_PAD, line_y)
        if i <= len(written):
            c.setFillColor(black)
            c.setFont(BODY_FONT, 9)
            c.drawString(LABEL_X + CELL_PAD, line_y + 4, written[i - 1])

    c.setStrokeColor(black)
    c.setLineWidth(0.75)
    return y_top + height


def _draw_signatures(c, data, save_image):
    """The agreement's own signature block, in the agreement's own position."""
    date = data.get('ended_date', '')

    for x0, x1, caption, signature_key in SIGNATURE_SLOTS:
        c.setStrokeColor(black)
        c.setLineWidth(0.75)
        c.line(x0, SIGNATURE_LINE_Y, x1, SIGNATURE_LINE_Y)

        c.setFillColor(black)
        c.setFont(BODY_FONT, CAPTION_FONT_SIZE)
        c.drawCentredString((x0 + x1) / 2, CAPTION_BASELINE_Y, caption)

        if signature_key is None:
            if date:
                c.setFont(BODY_FONT, 9)
                c.drawCentredString((x0 + x1) / 2, SIGNATURE_LINE_Y + 6, date)
            continue

        signature = data.get(signature_key)
        if signature is None:
            continue

        path = save_image(signature)
        if not path:
            continue
        # Centred over its own line, and the same size in every slot. The
        # slots are different widths, so a signature drawn to fill each one
        # would come out bigger for the lessor than the hirer.
        box_x = x0 + ((x1 - x0) - SIGNATURE_WIDTH) / 2
        try:
            c.drawImage(path, box_x, SIGNATURE_LINE_Y + 2,
                        width=SIGNATURE_WIDTH, height=SIGNATURE_HEIGHT,
                        preserveAspectRatio=True, anchor='s', mask='auto')
        except Exception:
            pass
        finally:
            if os.path.exists(path):
                os.unlink(path)


def build_end_page(data, template_path, save_image):
    """The End of Hire page on its own, as PDF bytes."""
    width, height = template_page_size(template_path)
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))

    logo = logo_from_template(template_path)
    if logo:
        try:
            c.drawImage(logo, 269, height - 123.89, width=70.5, height=76.5,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

    c.setFillColor(black)
    c.setFont(BOLD_FONT, 9)
    c.drawCentredString(width / 2, height - 142.9, "JL PCO LIMITED")
    c.setFont(BODY_FONT, 9)
    for i, line in enumerate((
        "Unit 1F Sandown Road Industrial Estate, Watford, WD24 7UB",
        "Tel: 01923 372516     Email: info@jlpco.co.uk",
        "Company Registration No: 16975750",
    )):
        c.drawCentredString(width / 2, height - (154.9 + i * 12), line)

    c.setFont(BOLD_FONT, 15)
    c.drawCentredString(width / 2, height - 210.9, "END OF HIRE & VEHICLE RETURN")

    c.setFont(BODY_FONT, 8.5)
    c.drawString(LEFT, height - 232,
                 "This record confirms the return of the vehicle and the end of the "
                 "hire agreement detailed below. It is")
    c.drawString(LEFT, height - 243,
                 "supplementary to that agreement and does not vary its terms.")

    c.setFont(BOLD_FONT, 9)
    c.drawString(LEFT, height - 268, "AGREEMENT")
    y = _draw_rows(c, height, 278, [
        ("Hirer Name", data.get('hirer_name', '')),
        ("Vehicle Registration", data.get('vehicle_reg', '')),
        ("Make / Model", data.get('make_model', '')),
        ("Hire Start Date", data.get('hire_start', '')),
        ("Agreement End Date & Time", data.get('ended_at_display', '')),
    ])

    c.setFont(BOLD_FONT, 9)
    c.drawString(LEFT, height - (y + 18), "VEHICLE CONDITION ON RETURN")
    y = _draw_notes(c, height, y + 28, data.get('return_notes', ''))

    c.setFont(BOLD_FONT, 9)
    c.drawString(LEFT, height - (y + 18), "DEPOSIT")
    y = _draw_rows(c, height, y + 28, [
        ("Deposit Held (£)", data.get('deposit_amount', '')),
        ("Amount Withheld (£)", data.get('deposit_withheld', '')),
        ("Deposit Returned (£)", data.get('deposit_returned', '')),
        ("Reason for Any Deduction", data.get('deduction_reason', '')),
    ])

    c.setFont(BODY_FONT, 7.5)
    c.drawString(LEFT, height - (y + 16),
                 "Both parties confirm the vehicle condition and deposit figures "
                 "recorded above.")

    _draw_signatures(c, data, save_image)

    c.save()
    return buffer.getvalue()


def merge(agreement_pdf, end_page_pdf):
    """The agreement with the end page on the back, as one file."""
    writer = PdfWriter()
    for source in (agreement_pdf, end_page_pdf):
        for page in PdfReader(io.BytesIO(source)).pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
