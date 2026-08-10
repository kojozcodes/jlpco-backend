"""
Template preview / check.

Run this after changing the template PDF. It reports which fields the template
can actually print and writes a sample agreement so you can eyeball the layout
before deploying.

    cd ~/projects/jlpco-mobile-app/jlpco-backend
    .venv/bin/python preview_template.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw
from pypdf import PdfReader

import pdf_generator as g

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'preview.pdf')

SAMPLE = {
    'full_name': 'JOHN A SAMPLE',
    'dob': '12/04/1985',
    'address': '12 Example Road, Watford, Hertfordshire, WD24 7UB',
    'email': 'john.sample@example.com',
    'phone_number': '07700 900123',
    'pco_badge_number': 'PCO-123456',
    'licence_number': 'SAMPL854123JA9XY',
    'licence_expiry': '11/04/2030',
    'ni_number': 'QQ123456C',
    'vehicle_reg': 'AB12 CDE',
    'make_model': 'Toyota Prius 1.8 Hybrid',
    'vin_number': 'JTDKB20U893456789',
    'hire_start': '10/08/2026',
    'insurance_provider': 'Acme Insurance Ltd',
    'policy_start': '01/08/2026',
    'policy_expiry': '31/07/2027',
    'cover_level': 'Comprehensive',
    'deposit_amount': '500.00',
    'deposit_date': '09/08/2026',
    'deposit_payment_type': 'Bank Transfer',
    'weekly_rent_amount': '275.00',
    'payment_start_date': '17/08/2026',
    'damage_notes': ('Deep scratch along the nearside from the front wing to the '
                     'rear door, dent below the offside mirror, and kerbing to '
                     'both nearside alloy wheels.'),
    # deliberately at the four corners + centre, so you can see the markers
    # line up with the diagram
    'damage_markers': [{'x': 0.0, 'y': 0.0}, {'x': 1.0, 'y': 0.0},
                       {'x': 0.0, 'y': 1.0}, {'x': 1.0, 'y': 1.0},
                       {'x': 0.5, 'y': 0.5}],
    'wheel_locking_nut': 'Yes',
    'immobiliser_installed': 'Yes',
    'dashcam_installed': 'Yes',
    'dashcam_serial': 'DC-99887766',
    'puncture_repair_kit': 'Yes',
    'hirer_sig_date': '10/08/2026',
    'lessor_sig_date': '10/08/2026',
}


def sample_signature():
    image = Image.new('RGB', (400, 120), 'white')
    ImageDraw.Draw(image).line(
        [(20, 90), (80, 30), (140, 95), (200, 35), (260, 90), (340, 50)],
        fill='blue', width=6)
    return image


def main():
    template = None
    for candidate in ('template_updated.pdf', 'template.pdf'):
        if os.path.exists(candidate):
            template = candidate
            break
    if template is None:
        print("No template found. Run this from the jlpco-backend folder.")
        return 1

    pages = len(PdfReader(template).pages)
    print(f"Template: {template}  ({pages} pages)\n")

    missing = []
    for field, candidates in g.TABLE_FIELDS:
        found = None
        for page_num in range(1, pages + 1):
            if g.find_label(template, page_num, candidates) is not None:
                found = page_num
                break
        if found:
            print(f"  ok      {field:22} page {found}")
        else:
            missing.append((field, candidates[0]))
            print(f"  MISSING {field:22} needs a row labelled '{candidates[0]}'")

    diagram = any(g.car_diagram_box(template, p) for p in range(1, pages + 1))
    rules = max((len(g.note_rules(template, p)) for p in range(1, pages + 1)), default=0)
    print(f"\n  vehicle diagram: {'found' if diagram else 'NOT FOUND'}")
    print(f"  damage note lines: {rules}")

    data = dict(SAMPLE)
    data['hirer_signature'] = sample_signature()
    data['lessor_signature'] = sample_signature()
    g.generate_hire_agreement_pdf_mobile(data, OUT, template_path=template)

    print(f"\nWrote {OUT} - open it and check the layout.")
    if missing:
        print("\nStill to add to the template:")
        for field, label in missing:
            print(f"  - a row labelled '{label}'")
        return 1
    print("\nEvery field has a home in this template.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
