"""
PDF Generator - Mobile Version
UPDATED: Fixed positioning based on actual template
Support for 3 new fields (Email, Phone Number, PCO Badge Number)
"""

import os
import sys
import tempfile
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.colors import black, red
from io import BytesIO

# Page dimensions
PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89

# Value column position
VALUE_COLUMN_X = 460  # Adjusted based on screenshots

def convert_y(top_from_top):
    """Convert y coordinate from 'top from top of page' to reportlab's bottom-left origin"""
    return PAGE_HEIGHT - top_from_top

def draw_cell_text(c, text, cell_top, cell_bottom):
    """Draw text aligned with the label text in the cell"""
    if not text:
        return
    y = convert_y(cell_top + 18)
    c.drawString(VALUE_COLUMN_X, y, str(text))

def save_pil_image_to_temp(pil_image):
    """Save PIL Image to temporary PNG file and return path"""
    if pil_image is None:
        return None
    
    temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    pil_image.save(temp_file.name, 'PNG')
    return temp_file.name

def create_overlay_pdf(data, output_path, page_num=1):
    """Create a PDF overlay for the template"""
    c = canvas.Canvas(output_path, pagesize=A4)
    c.setFont("Helvetica", 10)
    c.setFillColor(black)
    
    if page_num == 1:
        # ===== PAGE 1: Insurance & Deposit Details =====
        
        # Insurance Table (rows are ~55pt apart, starting from ~217)
        draw_cell_text(c, data.get('insurance_provider', ''), 217, 245)
        draw_cell_text(c, data.get('policy_start', ''), 272, 300)
        draw_cell_text(c, data.get('policy_expiry', ''), 327, 355)
        draw_cell_text(c, data.get('cover_level', ''), 382, 410)
        
        # Deposit Table (starts around ~497)
        draw_cell_text(c, data.get('deposit_amount', ''), 497, 525)
        draw_cell_text(c, data.get('deposit_date', ''), 552, 580)
        draw_cell_text(c, data.get('deposit_payment_type', ''), 607, 635)
        
        # Signatures at bottom
        draw_signatures(c, data)
        
    elif page_num == 2:
        # ===== PAGE 2: Hirer & Vehicle Details (WITH 3 NEW FIELDS) =====
        
        # Hirer Table - Based on screenshot analysis
        # Starting position appears to be around ~164
        draw_cell_text(c, data.get('full_name', ''), 164, 192)
        draw_cell_text(c, data.get('dob', ''), 210, 238)
        
        # Address (multi-line handling)
        address = data.get('address', '')
        if address:
            lines = address.replace('\n', ', ').split(', ')
            address_text = ', '.join([l.strip() for l in lines if l.strip()])
            if len(address_text) > 60:
                address_text = address_text[:57] + '...'
            draw_cell_text(c, address_text, 256, 302)
        
        # ✅ NEW FIELDS (Email, Phone, PCO Badge)
        draw_cell_text(c, data.get('email', ''), 330, 358)
        draw_cell_text(c, data.get('phone_number', ''), 376, 404)
        draw_cell_text(c, data.get('pco_badge_number', ''), 422, 450)
        
        # Existing fields (Licence Number, Expiry, NI)
        draw_cell_text(c, data.get('licence_number', ''), 468, 496)
        draw_cell_text(c, data.get('licence_expiry', ''), 514, 542)
        draw_cell_text(c, data.get('ni_number', ''), 560, 588)
        
        # Vehicle Table (starts around ~663)
        draw_cell_text(c, data.get('vehicle_reg', ''), 663, 691)
        draw_cell_text(c, data.get('make_model', ''), 709, 737)
        draw_cell_text(c, data.get('vin_number', ''), 755, 783)
        draw_cell_text(c, data.get('hire_start', ''), 801, 829)
        
        # Signatures at bottom
        draw_signatures(c, data)
        
    elif page_num == 3:
        # ===== PAGE 3: Vehicle Condition & Damage Record =====
        
        # Draw damage markers on the car diagram
        markers = data.get('damage_markers', [])
        if markers:
            # EXACT car diagram bounding box from PDF template
            car_x_start = 69.26
            car_y_start = 127.00
            car_width = 420.00
            car_height = 250.00
            
            for i, marker in enumerate(markers):
                # Handle both list and dict formats
                if isinstance(marker, (list, tuple)):
                    nx, ny = marker
                else:
                    nx = marker.get('x', 0)
                    ny = marker.get('y', 0)
                
                # Convert normalized coords (0-1) to PDF coords
                pdf_x = car_x_start + (nx * car_width)
                pdf_y = convert_y(car_y_start + (ny * car_height))
                
                # Draw red circle marker with white number
                c.setFillColorRGB(1, 0, 0)  # Red fill
                c.setStrokeColorRGB(0.6, 0, 0)  # Dark red stroke
                c.setLineWidth(1.5)
                c.circle(pdf_x, pdf_y, 10, fill=1, stroke=1)
                
                # Draw marker number in white
                c.setFillColorRGB(1, 1, 1)  # White text
                c.setFont("Helvetica-Bold", 9)
                c.drawCentredString(pdf_x, pdf_y - 3, str(i + 1))
            
            # Reset colors and font
            c.setFillColor(black)
            c.setFont("Helvetica", 10)
            c.setLineWidth(1)
        
        # Damage Notes (3 lines starting around y=54)
        damage_notes = data.get('damage_notes', '')
        if damage_notes:
            lines = damage_notes.split('\n')
            y_positions = [convert_y(54), convert_y(78), convert_y(102)]
            for i, line in enumerate(lines[:3]):
                if line.strip():
                    c.drawString(92, y_positions[i], line.strip()[:85])
        
        # Equipment fields (based on screenshot, x positions vary)
        c.drawString(255, convert_y(207), data.get('wheel_locking_nut', ''))
        c.drawString(285, convert_y(243), data.get('immobiliser_installed', ''))
        c.drawString(270, convert_y(279), data.get('dashcam_installed', ''))
        c.drawString(310, convert_y(315), data.get('dashcam_serial', ''))
        c.drawString(258, convert_y(351), data.get('puncture_repair_kit', ''))
        
        # Signatures at bottom
        draw_signatures(c, data)
    
    c.save()
    return output_path

def draw_signatures(c, data):
    """Draw signatures and dates at the bottom of the page"""
    
    sig_line_y = convert_y(800)
    sig_width = 140
    sig_height = 45
    
    # Hirer signature (left side)
    hirer_sig = data.get('hirer_signature')
    if hirer_sig:
        temp_path = save_pil_image_to_temp(hirer_sig)
        if temp_path:
            try:
                c.drawImage(temp_path, 100, sig_line_y, width=sig_width, height=sig_height, 
                           preserveAspectRatio=True, mask='auto')
            except:
                pass
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
    
    # Hirer date
    hirer_date = data.get('hirer_sig_date', '')
    if hirer_date:
        c.drawString(360, sig_line_y + 35, hirer_date)
    
    # Lessor signature (right side)
    lessor_sig = data.get('lessor_signature')
    if lessor_sig:
        temp_path = save_pil_image_to_temp(lessor_sig)
        if temp_path:
            try:
                c.drawImage(temp_path, 600, sig_line_y, width=sig_width, height=sig_height,
                           preserveAspectRatio=True, mask='auto')
            except:
                pass
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
    
    # Lessor date
    lessor_date = data.get('lessor_sig_date', '')
    if lessor_date:
        c.drawString(920, sig_line_y + 35, lessor_date)

def generate_hire_agreement_pdf_mobile(data, output_path, template_path=None):
    """Generate the PCO Hire Agreement PDF - Mobile Version"""
    
    # Find template PDF (prioritize template_updated.pdf)
    if template_path is None:
        possible_paths = [
            'template_updated.pdf',
            'template.pdf',
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
    
    # Process each page
    for page_num, template_page in enumerate(template_reader.pages, start=1):
        # Create overlay for this page
        overlay_path = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False).name
        create_overlay_pdf(data, overlay_path, page_num)
        
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