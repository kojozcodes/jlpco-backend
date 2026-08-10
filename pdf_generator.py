"""
PDF Generator - Mobile Version

Fills the JL PCO hire agreement by drawing an overlay onto the template PDF.

Positions are read from the template at run time rather than hard-coded: every
value is placed against the label the template already prints beside it, the
damage notes against the ruled lines, and the damage markers against the vehicle
diagram's own image box. Adding, moving or reordering a row in the template -
or the reflow that follows - therefore needs no change in this file.

Add a new field by giving it an entry in TABLE_FIELDS with the label the
template prints. Until that row exists in the template the value is skipped and
a note is logged; nothing else is affected.
"""

import os
import re
import tempfile
from functools import lru_cache
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.colors import black

# Page dimensions
PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89

BODY_FONT = "Helvetica"
BODY_FONT_SIZE = 10

# Labels sit in the left-hand column; anything further right is body copy.
LABEL_COLUMN_MAX_X = 130
# Longer than this and it is a sentence, not a table label.
LABEL_MAX_CHARS = 45

# Used when a page has no table to measure (matches the historic layout).
VALUE_COLUMN_X = 237
VALUE_CELL_PADDING = 7

# Damage notes sit this far above their printed rule, indented this far into it.
NOTE_RULE_GAP = 3.25
NOTE_INDENT = 3.75

# Equipment values sit just above their label's baseline.
EQUIPMENT_BASELINE_LIFT = 0.89

# Signatures are drawn this far above the footer caption they belong to.
SIGNATURE_CAPTION = 'hirer signature'
SIGNATURE_BASELINE_LIFT = 19.09
SIGNATURE_WIDTH = 140
SIGNATURE_HEIGHT = 45
HIRER_SIGNATURE_X = 30
HIRER_DATE_X = 180
LESSOR_SIGNATURE_X = 290
LESSOR_DATE_X = 475

# A tapped point on the frontend's car_diagram.png mapped onto the diagram
# embedded in the template. Vertical framing of the two images is identical, so
# ny maps straight across. Horizontally they differ: the frontend PNG carries
# about 2% more white margin on its right edge. Scale and offset are the fit
# measured from the ink bounding boxes of both images.
MARKER_X_SCALE = 1.02122
MARKER_X_OFFSET = 0.000407
MARKER_RADIUS = 10

# Every value that goes in a table, keyed by the label the template prints.
# Each field is drawn on whichever page carries its label.
TABLE_FIELDS = (
    # Hirer
    ('full_name',            ('full name',)),
    ('dob',                  ('date of birth',)),
    ('address',              ('address',)),
    ('email',                ('email',)),
    ('phone_number',         ('phone number',)),
    ('pco_badge_number',     ('pco badge number',)),
    ('licence_number',       ('driving licence number',)),
    ('licence_expiry',       ('licence expiry date',)),
    ('ni_number',            ('national insurance number',)),
    # Vehicle
    ('vehicle_reg',          ('vehicle registration',)),
    ('make_model',           ('make / model', 'make/model')),
    ('vin_number',           ('vehicle vin number',)),
    ('hire_start',           ('hire start date',)),
    # Insurance
    ('insurance_provider',   ('insurance provider',)),
    ('policy_start',         ('policy valid from',)),
    ('policy_expiry',        ('policy expiry date',)),
    ('cover_level',          ('level of cover',)),
    # Deposit
    ('deposit_amount',       ('deposit amount (£)', 'deposit amount')),
    ('deposit_date',         ('deposit paid date',)),
    ('deposit_payment_type', ('deposit payment type',)),
    # Rent
    ('weekly_rent_amount',   ('weekly rent amount (£)', 'weekly rent amount',
                              'weekly rent')),
    ('payment_start_date',   ('payment start date', 'date payment starts from',
                              'date payment start from', 'payment starts from',
                              'payment start')),
)

# Values written beside a label rather than in a table cell, with the x each
# one needs to clear its label's printed underscores.
EQUIPMENT_FIELDS = (
    ('wheel_locking_nut',     ('wheel locking nut',),     170),
    ('immobiliser_installed', ('immobiliser installed',), 190),
    ('dashcam_installed',     ('dashcam installed',),     185),
    ('dashcam_serial',        ('dashcam serial number',), 210),
    ('puncture_repair_kit',   ('puncture repair kit',),   173),
)


def convert_y(top_from_top):
    """Convert y coordinate from 'top from top of page' to reportlab's bottom-left origin"""
    return PAGE_HEIGHT - top_from_top


def _normalise(text):
    return re.sub(r'\s+', ' ', text).strip().lower()


@lru_cache(maxsize=16)
def page_lines(template_path, page_num):
    """
    Every line of text on a template page as (start_x, baseline_y, text).

    A page's text is emitted in fragments whose text matrix is relative to the
    current transform, so the two are composed to get the position on the page
    and fragments sharing a baseline are joined back into a line.
    """
    try:
        page = PdfReader(template_path).pages[page_num - 1]
    except Exception as e:
        print(f"Could not read text from template page {page_num}: {e}")
        return ()

    fragments = []

    def visitor(text, cm, tm, font_dict, font_size):
        stripped = text.strip()
        if not stripped:
            return
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        fragments.append((x, y, stripped))

    page.extract_text(visitor_text=visitor)

    rows = {}
    for x, y, text in fragments:
        rows.setdefault(round(y), []).append((x, y, text))

    lines = []
    for parts in rows.values():
        parts.sort()
        lines.append((parts[0][0], parts[0][1],
                      _normalise(' '.join(p[2] for p in parts))))
    return tuple(sorted(lines, key=lambda line: -line[1]))


@lru_cache(maxsize=16)
def label_anchors(template_path, page_num):
    """Map each left-column label printed on a page to its text baseline."""
    anchors = {}
    for start_x, baseline, text in page_lines(template_path, page_num):
        if start_x > LABEL_COLUMN_MAX_X or len(text) > LABEL_MAX_CHARS:
            continue
        anchors.setdefault(text, baseline)
    return anchors


def find_label(template_path, page_num, candidates):
    """Baseline of the first candidate label printed on the page, else None."""
    anchors = label_anchors(template_path, page_num)
    for candidate in candidates:
        if candidate in anchors:
            return anchors[candidate]
        match = next((y for label, y in anchors.items()
                      if label.startswith(candidate)), None)
        if match is not None:
            return match
    return None


@lru_cache(maxsize=16)
def value_column(template_path, page_num):
    """
    (x, available_width) of the value column, from the template's own cells.

    Falls back to the historic constant on pages that have no table.
    """
    try:
        page = PdfReader(template_path).pages[page_num - 1]
    except Exception:
        return VALUE_COLUMN_X, PAGE_WIDTH - VALUE_COLUMN_X

    cells = []

    def before(op, args, cm, tm):
        if op != b're':
            return
        try:
            x, y, w, h = (float(a) for a in args)
        except (TypeError, ValueError):
            return
        x = x * cm[0] + cm[4]
        w = w * cm[0]
        if w > 250 and x > LABEL_COLUMN_MAX_X:
            cells.append((x, w))

    page.extract_text(visitor_operand_before=before)
    if not cells:
        return VALUE_COLUMN_X, PAGE_WIDTH - VALUE_COLUMN_X

    left, width = min(cells)
    value_x = left + VALUE_CELL_PADDING
    return value_x, (left + width) - value_x - VALUE_CELL_PADDING


@lru_cache(maxsize=16)
def placed_images(template_path, page_num):
    """Placed image boxes on a page as (x, y, width, height)."""
    try:
        page = PdfReader(template_path).pages[page_num - 1]
    except Exception:
        return ()

    boxes = []

    def before(op, args, cm, tm):
        if op == b'Do':
            boxes.append((cm[4], cm[5], cm[0], cm[3]))

    page.extract_text(visitor_operand_before=before)
    return tuple(boxes)


def car_diagram_box(template_path, page_num):
    """The vehicle diagram - the largest image placed on the page."""
    boxes = [b for b in placed_images(template_path, page_num)
             if b[2] > 100 and b[3] > 100]
    return max(boxes, key=lambda b: b[2] * b[3]) if boxes else None


def note_rules(template_path, page_num):
    """The printed ruled lines for damage notes, top-most first, as (x, y, w)."""
    rules = {(b[0], b[1], b[2]) for b in placed_images(template_path, page_num)
             if b[2] > 200 and 0 < b[3] <= 3}
    return sorted(rules, key=lambda rule: -rule[1])


def truncate_to_width(text, max_width):
    """Trim text to a pixel width, marking it if anything was dropped."""
    if stringWidth(text, BODY_FONT, BODY_FONT_SIZE) <= max_width:
        return text
    while text and stringWidth(text + '...', BODY_FONT, BODY_FONT_SIZE) > max_width:
        text = text[:-1]
    return text.rstrip() + '...'


def wrap_to_width(text, max_width, max_lines):
    """
    Wrap text to a pixel width, keeping the author's own line breaks where they
    fit. Anything past max_lines is marked with an ellipsis rather than dropped
    silently.
    """
    lines = []
    for paragraph in text.split('\n'):
        words = paragraph.split()
        if not words:
            continue
        current = ''
        for word in words:
            candidate = f'{current} {word}'.strip()
            if not current or stringWidth(candidate, BODY_FONT, BODY_FONT_SIZE) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = truncate_to_width(lines[-1] + ' ...', max_width)
    return lines


def format_address(address, max_width):
    """Flatten a multi-line address onto the single row the template gives it."""
    parts = [part.strip() for part in address.replace('\n', ', ').split(', ')]
    return truncate_to_width(', '.join(p for p in parts if p), max_width)


def save_pil_image_to_temp(pil_image):
    """Save PIL Image to temporary PNG file and return path"""
    if pil_image is None:
        return None

    temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    pil_image.save(temp_file.name, 'PNG')
    return temp_file.name


def draw_table_fields(c, data, template_path, page_num):
    """Draw every table value whose label is printed on this page."""
    value_x, value_width = value_column(template_path, page_num)

    for field, candidates in TABLE_FIELDS:
        value = data.get(field, '')
        if not value:
            continue

        baseline = find_label(template_path, page_num, candidates)
        if baseline is None:
            continue

        text = (format_address(value, value_width) if field == 'address'
                else truncate_to_width(str(value), value_width))
        c.drawString(value_x, baseline, text)


def draw_damage_markers(c, markers, car_box):
    """Number each tapped point onto the vehicle diagram."""
    car_x, car_y, car_w, car_h = car_box

    for i, marker in enumerate(markers):
        if isinstance(marker, (list, tuple)):
            nx, ny = marker
        else:
            nx = marker.get('x', 0)
            ny = marker.get('y', 0)

        pdf_x = car_x + ((nx * MARKER_X_SCALE) + MARKER_X_OFFSET) * car_w
        pdf_y = (car_y + car_h) - (ny * car_h)

        c.setFillColorRGB(1, 0, 0)
        c.setStrokeColorRGB(0.6, 0, 0)
        c.setLineWidth(1.5)
        c.circle(pdf_x, pdf_y, MARKER_RADIUS, fill=1, stroke=1)

        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(pdf_x, pdf_y - 3, str(i + 1))

    c.setFillColor(black)
    c.setFont(BODY_FONT, BODY_FONT_SIZE)
    c.setLineWidth(1)


def draw_damage_notes(c, damage_notes, rules):
    """Write the notes onto the printed ruled lines, one line per rule."""
    rule_x, _, rule_width = rules[0]
    note_x = rule_x + NOTE_INDENT
    max_width = (rule_x + rule_width) - note_x

    for (_, rule_y, _), line in zip(rules, wrap_to_width(damage_notes, max_width, len(rules))):
        c.drawString(note_x, rule_y + NOTE_RULE_GAP, line)


def draw_equipment_fields(c, data, template_path, page_num):
    """Write each equipment answer beside its label."""
    for field, candidates, x in EQUIPMENT_FIELDS:
        value = data.get(field, '')
        if not value:
            continue
        baseline = find_label(template_path, page_num, candidates)
        if baseline is not None:
            c.drawString(x, baseline + EQUIPMENT_BASELINE_LIFT, str(value))


def draw_signatures(c, data, template_path=None, page_num=None):
    """Draw signatures and dates above the footer caption on the page"""

    sig_line_y = None
    if template_path and page_num:
        caption = next((y for _, y, text in page_lines(template_path, page_num)
                        if SIGNATURE_CAPTION in text), None)
        if caption is not None:
            sig_line_y = caption + SIGNATURE_BASELINE_LIFT
    if sig_line_y is None:
        sig_line_y = convert_y(785)

    for signature, sig_x, date, date_x in (
        (data.get('hirer_signature'), HIRER_SIGNATURE_X,
         data.get('hirer_sig_date', ''), HIRER_DATE_X),
        (data.get('lessor_signature'), LESSOR_SIGNATURE_X,
         data.get('lessor_sig_date', ''), LESSOR_DATE_X),
    ):
        if signature:
            temp_path = save_pil_image_to_temp(signature)
            if temp_path:
                try:
                    c.drawImage(temp_path, sig_x, sig_line_y,
                                width=SIGNATURE_WIDTH, height=SIGNATURE_HEIGHT,
                                preserveAspectRatio=True, mask='auto')
                except Exception:
                    pass
                finally:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)

        if date:
            c.drawString(date_x, sig_line_y, date)


def create_overlay_pdf(data, output_path, page_num=1, template_path=None):
    """Create a PDF overlay for the given template page"""
    c = canvas.Canvas(output_path, pagesize=A4)
    c.setFont(BODY_FONT, BODY_FONT_SIZE)
    c.setFillColor(black)

    if template_path:
        draw_table_fields(c, data, template_path, page_num)

        # The vehicle condition page is the one carrying the diagram, wherever
        # in the document that ends up.
        car_box = car_diagram_box(template_path, page_num)
        markers = data.get('damage_markers', [])
        if car_box and markers:
            draw_damage_markers(c, markers, car_box)

        rules = note_rules(template_path, page_num)
        damage_notes = data.get('damage_notes', '')
        if rules and damage_notes:
            draw_damage_notes(c, damage_notes, rules)

        draw_equipment_fields(c, data, template_path, page_num)

    draw_signatures(c, data, template_path, page_num)

    c.save()
    return output_path


def report_missing_fields(data, template_path, page_count):
    """Warn about values that have nowhere to go in the current template."""
    pages = range(1, page_count + 1)

    printable = {field for page_num in pages
                 for field, candidates in TABLE_FIELDS
                 if find_label(template_path, page_num, candidates) is not None}

    for field, candidates in TABLE_FIELDS:
        if data.get(field) and field not in printable:
            print(f"No '{candidates[0]}' row in the template - {field} was not "
                  f"printed. Add the row to the template and re-export.")

    if data.get('damage_notes') and not any(note_rules(template_path, p) for p in pages):
        print("No ruled lines found in the template - damage notes were not "
              "printed. The notes are written onto the template's printed rules.")

    if data.get('damage_markers') and not any(car_diagram_box(template_path, p) for p in pages):
        print("No vehicle diagram found in the template - damage markers were "
              "not printed.")


def generate_hire_agreement_pdf_mobile(data, output_path, template_path=None):
    """Generate the PCO Hire Agreement PDF - Mobile Version"""

    # Find template PDF (prioritize template_updated.pdf)
    if template_path is None:
        possible_paths = [
            'template_updated.pdf',  # ✅ NEW template first
            'template.pdf',          # Fallback to old
            '/app/template_updated.pdf',
            '/app/template.pdf',
            os.path.join(os.path.dirname(__file__), 'template_updated.pdf'),
            os.path.join(os.path.dirname(__file__), 'template.pdf'),
        ]

        for path in possible_paths:
            if os.path.exists(path):
                template_path = path
                print(f"✅ Using template: {path}")
                break

        if template_path is None:
            raise FileNotFoundError("Template PDF not found. Please upload template_updated.pdf to backend folder.")

    # Read the template PDF
    template_reader = PdfReader(template_path)
    writer = PdfWriter()

    report_missing_fields(data, template_path, len(template_reader.pages))

    # Process each page
    for page_num, template_page in enumerate(template_reader.pages, start=1):
        # Create overlay for this page
        overlay_path = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False).name
        create_overlay_pdf(data, overlay_path, page_num, template_path)

        # Merge overlay onto template
        overlay_reader = PdfReader(overlay_path)
        if len(overlay_reader.pages) > 0:
            template_page.merge_page(overlay_reader.pages[0])

        writer.add_page(template_page)
        os.unlink(overlay_path)

    # Write the final PDF
    with open(output_path, 'wb') as output_file:
        writer.write(output_file)

    return output_path
