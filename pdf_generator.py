"""
PDF Generator - Mobile Version
UPDATED: Support for 3 new fields (Email, Phone Number, PCO Badge Number)
Page 2 field positions adjusted accordingly
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
VALUE_COLUMN_X = 238

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
        
        # Insurance Table
        draw_cell_text(c, data.get('insurance_provider', ''), 482, 510)
        draw_cell_text(c, data.get('policy_start', ''), 510, 538)
        draw_cell_text(c, data.get('policy_expiry', ''), 538, 566)
        draw_cell_text(c, data.get('cover_level', ''), 566, 594)
        
        # Deposit Table
        draw_cell_text(c, data.get('deposit_amount', ''), 618, 646)
        draw_cell_text(c, data.get('deposit_date', ''), 646, 674)
        draw_cell_text(c, data.get('deposit_payment_type', ''), 674, 702)
        
        # Signatures at bottom
        draw_signatures(c, data)
        
    elif page_num == 2:
        # ===== PAGE 2: Hirer & Vehicle Details (WITH 3 NEW FIELDS) =====
        
        # Hirer Table - UPDATED positions for new template
        draw_cell_text(c, data.get('full_name', ''), 312, 340)
        draw_cell_text(c, data.get('dob', ''), 339, 367)
        
        # Address (same as before)
        address = data.get('address', '')
        if address:
            lines = address.replace('\n', ', ').split(', ')
            address_text = ', '.join([l.strip() for l in lines if l.strip()])
            if len(address_text) > 60:
                address_text = address_text[:57] + '...'
            draw_cell_text(c, address_text, 367, 395)
        
        # ✅ NEW FIELDS (after Address)
        draw_cell_text(c, data.get('email', ''), 405, 430)              # Email
        draw_cell_text(c, data.get('phone_number', ''), 432, 462)       # Phone Number  
        draw_cell_text(c, data.get('pco_badge_number', ''), 458, 484)   # PCO Badge Number
        
        # Existing fields (shifted down by 3 rows = 84 points)
        draw_cell_text(c, data.get('licence_number', ''), 483, 511)
        draw_cell_text(c, data.get('licence_expiry', ''), 506, 534)
        draw_cell_text(c, data.get('ni_number', ''), 531, 559)
        
        # Vehicle Table (shifted down by 3 rows = 84 points)
        draw_cell_text(c, data.get('vehicle_reg', ''), 587, 615)
        draw_cell_text(c, data.get('make_model', ''), 614, 640)
        draw_cell_text(c, data.get('vin_number', ''), 642, 670)
        draw_cell_text(c, data.get('hire_start', ''), 669, 697)
        
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
        
        # Damage Notes
        damage_notes = data.get('damage_notes', '')
        if damage_notes:
            lines = damage_notes.split('\n')
            y_positions = [convert_y(410), convert_y(434), convert_y(458)]
            for i, line in enumerate(lines[:3]):
                if line.strip():
                    c.drawString(50, y_positions[i], line.strip()[:65])
        
        # Equipment fields
        c.drawString(170, convert_y(486), data.get('wheel_locking_nut', ''))
        c.drawString(190, convert_y(492), data.get('immobiliser_installed', ''))
        c.drawString(185, convert_y(510), data.get('dashcam_installed', ''))
        c.drawString(210, convert_y(534), data.get('dashcam_serial', ''))
        c.drawString(173, convert_y(550), data.get('puncture_repair_kit', ''))
        
        # Signatures at bottom
        draw_signatures(c, data)
    
    c.save()
    return output_path

def draw_signatures(c, data):
    """Draw signatures and dates at the bottom of the page"""
    
    sig_line_y = convert_y(795)
    sig_width = 140
    sig_height = 45
    
    # Hirer signature (left side)
    hirer_sig = data.get('hirer_signature')
    if hirer_sig:
        temp_path = save_pil_image_to_temp(hirer_sig)
        if temp_path:
            try:
                c.drawImage(temp_path, 40, sig_line_y, width=sig_width, height=sig_height, 
                           preserveAspectRatio=True, mask='auto')
            except:
                pass
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
    
    # Hirer date
    hirer_date = data.get('hirer_sig_date', '')
    if hirer_date:
        c.drawString(210, sig_line_y, hirer_date)
    
    # Lessor signature (right side)
    lessor_sig = data.get('lessor_signature')
    if lessor_sig:
        temp_path = save_pil_image_to_temp(lessor_sig)
        if temp_path:
            try:
                c.drawImage(temp_path, 320, sig_line_y, width=sig_width, height=sig_height,
                           preserveAspectRatio=True, mask='auto')
            except:
                pass
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
    
    # Lessor date
    lessor_date = data.get('lessor_sig_date', '')
    if lessor_date:
        c.drawString(490, sig_line_y, lessor_date)

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
