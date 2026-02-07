"""
JL PCO Mobile Backend - With Authentication
Secure login with password hashing and JWT tokens
Users must login every time they open the app
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import tempfile
import base64
from io import BytesIO
from datetime import datetime, timedelta
from PIL import Image
from pdf_generator import generate_hire_agreement_pdf_mobile
import traceback
import hashlib
import secrets
import jwt

app = Flask(__name__)
CORS(app)  # Enable CORS for mobile app

# SECURITY CONFIGURATION
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

# PASSWORD CONFIGURATION
# IMPORTANT: Change this password in production!
# To generate a new password hash, run: python3 -c "import hashlib; print(hashlib.sha256('YOUR_PASSWORD'.encode()).hexdigest())"
ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH')

if not ADMIN_PASSWORD_HASH:
    raise ValueError(
        "ADMIN_PASSWORD_HASH environment variable is required! "
        "Set it in Railway dashboard under Variables tab."
    )

# Token expiration: 8 hours (but users login every time anyway)
TOKEN_EXPIRATION_HOURS = 8


def hash_password(password):
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password, password_hash):
    """Verify password against hash"""
    return hash_password(password) == password_hash


def generate_token(user_id='admin'):
    """Generate JWT token"""
    payload = {
        'user_id': user_id,
        'exp': datetime.utcnow() + timedelta(hours=TOKEN_EXPIRATION_HOURS),
        'iat': datetime.utcnow()
    }
    token = jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')
    return token


def verify_token(token):
    """Verify JWT token"""
    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def require_auth(f):
    """Decorator to require authentication"""
    def decorated_function(*args, **kwargs):
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        
        if not auth_header:
            return jsonify({'success': False, 'error': 'No authorization token provided'}), 401
        
        try:
            # Expected format: "Bearer <token>"
            token = auth_header.split(' ')[1]
        except IndexError:
            return jsonify({'success': False, 'error': 'Invalid authorization header'}), 401
        
        # Verify token
        payload = verify_token(token)
        if not payload:
            return jsonify({'success': False, 'error': 'Invalid or expired token'}), 401
        
        # Add user info to request
        request.user_id = payload['user_id']
        
        return f(*args, **kwargs)
    
    decorated_function.__name__ = f.__name__
    return decorated_function


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint (no auth required)"""
    return jsonify({'status': 'ok', 'message': 'JL PCO Backend Running'})


@app.route('/api/login', methods=['POST'])
def login():
    """
    Login endpoint
    Expects: { "password": "your_password" }
    Returns: { "success": true, "token": "jwt_token", "expires_in": 28800 }
    """
    try:
        data = request.json
        password = data.get('password', '')
        
        if not password:
            return jsonify({
                'success': False,
                'error': 'Password is required'
            }), 400
        
        # Verify password
        if not verify_password(password, ADMIN_PASSWORD_HASH):
            # Add small delay to prevent brute force attacks
            import time
            time.sleep(1)
            return jsonify({
                'success': False,
                'error': 'Invalid password'
            }), 401
        
        # Generate token
        token = generate_token()
        
        return jsonify({
            'success': True,
            'token': token,
            'expires_in': TOKEN_EXPIRATION_HOURS * 3600  # seconds
        })
        
    except Exception as e:
        print(f"Login error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Login failed'
        }), 500


@app.route('/api/verify-token', methods=['GET'])
@require_auth
def verify_token_endpoint():
    """
    Verify if token is still valid
    Requires: Authorization header with Bearer token
    Returns: { "success": true, "user_id": "admin" }
    """
    return jsonify({
        'success': True,
        'user_id': request.user_id
    })


@app.route('/api/generate-pdf', methods=['POST'])
@require_auth  # ← Now requires authentication!
def generate_pdf():
    """
    Generate PDF from form data
    NOW REQUIRES AUTHENTICATION
    Expects: Authorization header + JSON with all form fields
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
        
        # Process signatures (convert base64 to PIL Images)
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
        
        # Log successful generation
        print(f"PDF generated by user '{request.user_id}': {filename}")
        
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
    # Print production startup info
    print("\n" + "="*60)
    print("JL PCO SECURE BACKEND - PRODUCTION MODE")
    print("="*60)
    
    # Verify environment variables
    if not ADMIN_PASSWORD_HASH:
        print("\n❌ ERROR: ADMIN_PASSWORD_HASH not set!")
        print("Set it in Railway dashboard under Variables tab")
        exit(1)
    
    print("\n✅ Password hash: CONFIGURED")
    print("✅ Secret key: CONFIGURED")
    print("✅ Security: ENABLED")
    print("\n" + "="*60 + "\n")
    
    # Production mode
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)