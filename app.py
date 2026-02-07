"""
JL PCO Mobile Backend - Flask API
Handles PDF generation using the existing Python logic
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import tempfile
import base64
from io import BytesIO
from datetime import datetime
from PIL import Image
from pdf_generator import generate_hire_agreement_pdf_mobile
import traceback

app = Flask(__name__)
CORS(app)  # Enable CORS for mobile app

# Configure upload folder
UPLOAD_FOLDER = tempfile.gettempdir()
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'message': 'JL PCO Backend Running'})

@app.route('/api/generate-pdf', methods=['POST'])
def generate_pdf():
    """
    Generate PDF from form data
    Expects JSON with all form fields + base64 encoded signatures
    """
    try:
        data = request.json
        
        # Validate required fields
        required = ['full_name', 'vehicle_reg', 'make_model']
        missing = [f for f in required if not data.get(f)]
        if missing:
            return jsonify({
                'success': False,
                'error': f'Missing required fields: {", ".join(missing)}'
            }), 400
        
        # Process signatures (convert base64 to QImage-like objects)
        hirer_sig = process_signature(data.get('hirer_signature'))
        lessor_sig = process_signature(data.get('lessor_signature'))
        
        # Prepare data dict
        pdf_data = {
            # Hirer details
            'full_name': data.get('full_name', ''),
            'dob': data.get('dob', ''),
            'address': data.get('address', ''),
            'licence_number': data.get('licence_number', ''),
            'licence_expiry': data.get('licence_expiry', ''),
            'ni_number': data.get('ni_number', ''),
            
            # Vehicle details
            'vehicle_reg': data.get('vehicle_reg', ''),
            'make_model': data.get('make_model', ''),
            'vin_number': data.get('vin_number', ''),
            'hire_start': data.get('hire_start', ''),
            
            # Insurance details
            'insurance_provider': data.get('insurance_provider', ''),
            'policy_start': data.get('policy_start', ''),
            'policy_expiry': data.get('policy_expiry', ''),
            'cover_level': data.get('cover_level', ''),
            
            # Deposit details
            'deposit_amount': data.get('deposit_amount', ''),
            'deposit_date': data.get('deposit_date', ''),
            'deposit_payment_type': data.get('deposit_payment_type', ''),
            
            # Vehicle condition
            'damage_notes': data.get('damage_notes', ''),
            'damage_markers': data.get('damage_markers', []),
            'wheel_locking_nut': data.get('wheel_locking_nut', ''),
            'immobiliser_installed': data.get('immobiliser_installed', ''),
            'dashcam_installed': data.get('dashcam_installed', ''),
            'dashcam_serial': data.get('dashcam_serial', ''),
            'puncture_repair_kit': data.get('puncture_repair_kit', ''),
            
            # Signatures
            'hirer_signature': hirer_sig,
            'hirer_sig_date': data.get('hirer_sig_date', ''),
            'lessor_signature': lessor_sig,
            'lessor_sig_date': data.get('lessor_sig_date', ''),
        }
        
        # Generate PDF
        vehicle_reg = data.get('vehicle_reg', 'UNKNOWN').strip().upper().replace(" ", "")
        hirer_name = data.get('full_name', 'UNKNOWN').strip().replace(" ", "_")
        date_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        safe_reg = ''.join(c for c in vehicle_reg if c.isalnum())
        safe_name = ''.join(c for c in hirer_name if c.isalnum() or c == '_')
        
        filename = f"JL_PCO_Hire_{safe_reg}_{safe_name}_{date_str}.pdf"
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Use existing PDF generator
        generate_hire_agreement_pdf_mobile(pdf_data, output_path)
        
        # Return PDF as download
        return send_file(
            output_path,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        print(f"Error generating PDF: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

def process_signature(sig_base64):
    """Convert base64 signature to PIL Image for PDF insertion"""
    if not sig_base64:
        return None
    
    try:
        # Remove data URL prefix if present
        if ',' in sig_base64:
            sig_base64 = sig_base64.split(',')[1]
        
        # Decode base64
        img_data = base64.b64decode(sig_base64)
        
        # Convert to PIL Image
        img = Image.open(BytesIO(img_data))
        
        return img
        
    except Exception as e:
        print(f"Error processing signature: {e}")
        return None

if __name__ == '__main__':
    # For development
    app.run(host='0.0.0.0', port=5000, debug=True)
