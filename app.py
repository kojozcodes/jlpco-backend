"""
JL PCO Mobile Backend - With Authentication
Updated to support 3 NEW FIELDS: email, phone_number, pco_badge_number
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import tempfile
import base64
from io import BytesIO
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from PIL import Image
from pdf_generator import (generate_hire_agreement_pdf_mobile,
                           resolve_template_path,
                           save_pil_image_to_temp as save_signature_to_temp)
import db
import end_document
import traceback
import bcrypt
import secrets
import jwt
from functools import wraps

app = Flask(__name__)
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '').split(',')
CORS(app, origins=ALLOWED_ORIGINS)

# SECURITY CONFIGURATION
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

# PASSWORD CONFIGURATION
ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH')

if not ADMIN_PASSWORD_HASH:
    raise ValueError(
        "ADMIN_PASSWORD_HASH environment variable is required! "
        "Set it in Railway dashboard under Variables tab."
    )

# Token expiration: 8 hours
TOKEN_EXPIRATION_HOURS = 8

# Prepare the archive. Idempotent, so it is safe for each gunicorn worker to
# run it, and a failure here leaves agreement generation working.
db.init_schema()


def verify_password(password, stored_hash):
    """Verify password against bcrypt hash"""
    return bcrypt.checkpw(password.encode(), stored_hash.encode())


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
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        
        if not auth_header:
            return jsonify({'success': False, 'error': 'No authorization token provided'}), 401
        
        try:
            token = auth_header.split(' ')[1]
        except IndexError:
            return jsonify({'success': False, 'error': 'Invalid authorization header'}), 401
        
        payload = verify_token(token)
        if not payload:
            return jsonify({'success': False, 'error': 'Invalid or expired token'}), 401
        
        request.user_id = payload['user_id']
        
        return f(*args, **kwargs)
    
    return decorated_function


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    # 'history' says whether saved agreements are available, so a deployment
    # can be checked without signing in. Deliberately just a state name: this
    # endpoint is unauthenticated.
    return jsonify({
        'status': 'ok',
        'message': 'JL PCO Backend Running - WITH NEW FIELDS',
        'history': db.status(),
    })


@app.route('/api/login', methods=['POST'])
def login():
    """Login endpoint"""
    try:
        data = request.json
        password = data.get('password', '')
        
        if not password:
            return jsonify({
                'success': False,
                'error': 'Password is required'
            }), 400
        
        if not verify_password(password, ADMIN_PASSWORD_HASH):
            import time
            time.sleep(1)
            return jsonify({
                'success': False,
                'error': 'Invalid password'
            }), 401
        
        token = generate_token()
        
        return jsonify({
            'success': True,
            'token': token,
            'expires_in': TOKEN_EXPIRATION_HOURS * 3600
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
    """Verify token endpoint"""
    return jsonify({
        'success': True,
        'user_id': request.user_id
    })


@app.route('/api/generate-pdf', methods=['POST'])
@require_auth
def generate_pdf():
    """
    Generate PDF with support for 3 NEW FIELDS
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
        
        # Process signatures
        hirer_sig = process_signature(data.get('hirer_signature'))
        lessor_sig = process_signature(data.get('lessor_signature'))
        
        # Prepare data dict WITH 3 NEW FIELDS
        pdf_data = {
            # Hirer details
            'full_name': data.get('full_name', ''),
            'dob': format_date_uk(data.get('dob', '')),
            'address': data.get('address', ''),
            'email': data.get('email', ''),                      # ✅ NEW FIELD
            'phone_number': data.get('phone_number', ''),        # ✅ NEW FIELD
            'pco_badge_number': data.get('pco_badge_number', ''),# ✅ NEW FIELD
            'licence_number': data.get('licence_number', ''),
            'licence_expiry': format_date_uk(data.get('licence_expiry', '')),
            'ni_number': data.get('ni_number', ''),

            # Vehicle details
            'vehicle_reg': data.get('vehicle_reg', ''),
            'make_model': data.get('make_model', ''),
            'vin_number': data.get('vin_number', ''),
            'hire_start': format_date_uk(data.get('hire_start', '')),

            # Insurance details
            'insurance_provider': data.get('insurance_provider', ''),
            'policy_start': format_date_uk(data.get('policy_start', '')),
            'policy_expiry': format_date_uk(data.get('policy_expiry', '')),
            'cover_level': data.get('cover_level', ''),

            # Deposit details
            'deposit_amount': data.get('deposit_amount', ''),
            'deposit_date': format_date_uk(data.get('deposit_date', '')),
            'deposit_payment_type': data.get('deposit_payment_type', ''),

            # Rent details
            'weekly_rent_amount': data.get('weekly_rent_amount', ''),
            'payment_start_date': format_date_uk(data.get('payment_start_date', '')),

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
            'hirer_sig_date': format_date_uk(data.get('hirer_sig_date', '')),
            'lessor_signature': lessor_sig,
            'lessor_sig_date': format_date_uk(data.get('lessor_sig_date', '')),
        }
        
        # Generate PDF
        vehicle_reg = data.get('vehicle_reg', 'UNKNOWN').strip().upper().replace(" ", "")
        hirer_name = data.get('full_name', 'UNKNOWN').strip().replace(" ", "_")
        date_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        safe_reg = ''.join(c for c in vehicle_reg if c.isalnum())
        safe_name = ''.join(c for c in hirer_name if c.isalnum() or c == '_')
        
        filename = f"JL_PCO_Hire_{safe_reg}_{safe_name}_{date_str}.pdf"
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Generate PDF
        generate_hire_agreement_pdf_mobile(pdf_data, output_path)

        print(f"PDF generated by '{request.user_id}': {filename}")
        print(f"  ✅ Email: {data.get('email', 'N/A')}")
        print(f"  ✅ Phone: {data.get('phone_number', 'N/A')}")
        print(f"  ✅ PCO Badge: {data.get('pco_badge_number', 'N/A')}")

        # Serve from memory and drop the file straight away - agreements contain
        # personal data and the container's temp dir was never being cleared.
        try:
            with open(output_path, 'rb') as generated:
                raw_pdf = generated.read()
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

        # Archive a copy. Deliberately after the PDF exists and deliberately
        # unable to raise: if the archive is down the operator still gets the
        # agreement they are standing at a handover waiting for.
        # The submitted values, not pdf_data: by that point the dates have been
        # reformatted for printing, and DD/MM/YYYY is silently rejected by the
        # date inputs when an agreement is reused as the basis for a new one.
        db.save_agreement(data, raw_pdf, filename, created_by=request.user_id)

        return send_file(
            BytesIO(raw_pdf),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        print(f"Error generating PDF: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': 'Failed to generate PDF. Please try again.'
        }), 500


@app.route('/api/agreements', methods=['GET'])
@require_auth
def list_agreements():
    """Past agreements, most recent first, filtered by name or registration."""
    if not db.is_enabled():
        return jsonify({
            'success': False,
            'error': 'History is not set up yet.',
            'agreements': []
        }), 503

    try:
        return jsonify({
            'success': True,
            'agreements': db.list_agreements(
                search=request.args.get('search', ''),
                limit=request.args.get('limit', 50),
                offset=request.args.get('offset', 0),
            )
        })
    except Exception as e:
        print(f"Error listing agreements: {e}")
        return jsonify({'success': False, 'error': 'Could not load history.'}), 500


@app.route('/api/agreements/<agreement_id>/pdf', methods=['GET'])
@require_auth
def download_agreement(agreement_id):
    """Re-download the exact PDF that was produced at the time."""
    try:
        filename, pdf_bytes, end_pdf = db.get_pdf(agreement_id)
    except Exception as e:
        print(f"Error fetching agreement {agreement_id}: {e}")
        return jsonify({'success': False, 'error': 'Could not load agreement.'}), 500

    if not pdf_bytes:
        return jsonify({'success': False, 'error': 'Agreement not found.'}), 404

    # One file, assembled on the way out. An ended hire reads as the agreement
    # followed by its end page, while each half stays stored exactly as it was
    # produced.
    if end_pdf:
        try:
            pdf_bytes = end_document.merge(pdf_bytes, end_pdf)
        except Exception as e:
            print(f"Could not merge the end page for {agreement_id}: {e}")

    return send_file(
        BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )


@app.route('/api/agreements/<agreement_id>/form', methods=['GET'])
@require_auth
def agreement_form_data(agreement_id):
    """The submitted values, for starting a repeat hire. Signatures excluded."""
    try:
        form_data = db.get_form_data(agreement_id)
    except Exception as e:
        print(f"Error fetching form data {agreement_id}: {e}")
        return jsonify({'success': False, 'error': 'Could not load agreement.'}), 500

    if form_data is None:
        return jsonify({'success': False, 'error': 'Agreement not found.'}), 404

    return jsonify({'success': True, 'form_data': form_data})


@app.route('/api/agreements/<agreement_id>/end', methods=['POST', 'DELETE'])
@require_auth
def end_agreement(agreement_id):
    """
    Finish a hire, or put it back to running.

    Ending produces an End of Hire page which is kept alongside the agreement
    and merged onto the back of it when the file is opened. The agreement's own
    pages are never rewritten: it is what the hirer signed.
    """
    if request.method == 'DELETE':
        try:
            reopened = db.clear_end(agreement_id)
        except Exception as e:
            print(f"Error reopening agreement {agreement_id}: {e}")
            return jsonify({'success': False, 'error': 'Could not reopen agreement.'}), 500
        if not reopened:
            return jsonify({'success': False, 'error': 'Agreement not found.'}), 404
        print(f"Agreement {agreement_id} reopened by '{request.user_id}'")
        return jsonify({'success': True, 'ended_at': None})

    data = request.json or {}

    try:
        agreement = db.get_agreement(agreement_id)
    except Exception as e:
        print(f"Error loading agreement {agreement_id}: {e}")
        return jsonify({'success': False, 'error': 'Could not load agreement.'}), 500

    if not agreement:
        return jsonify({'success': False, 'error': 'Agreement not found.'}), 404

    ended_at = datetime.now(timezone.utc)
    ended_local = ended_at.astimezone(UK_TIMEZONE)
    form_data = agreement.get('form_data') or {}

    end_data = {
        'hirer_name': agreement['hirer_name'],
        'vehicle_reg': agreement['vehicle_reg'],
        'make_model': agreement['make_model'],
        'hire_start': format_date_uk(form_data.get('hire_start', '')),
        'ended_at_display': ended_local.strftime('%d/%m/%Y %H:%M'),
        'ended_date': ended_local.strftime('%d/%m/%Y'),
        'return_notes': data.get('return_notes', ''),
        'deposit_amount': format_money(data.get('deposit_amount',
                                                form_data.get('deposit_amount', ''))),
        'deposit_withheld': format_money(data.get('deposit_withheld', '')),
        'deposit_returned': format_money(data.get('deposit_returned', '')),
        'deduction_reason': data.get('deduction_reason', ''),
    }

    try:
        page = end_document.build_end_page(
            dict(end_data,
                 hirer_signature=process_signature(data.get('hirer_signature')),
                 lessor_signature=process_signature(data.get('lessor_signature'))),
            resolve_template_path(),
            save_signature_to_temp,
        )
        stored = db.save_end(agreement_id, ended_at, end_data, page)
    except Exception as e:
        print(f"Error ending agreement {agreement_id}: {e}")
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': 'Could not end agreement.'}), 500

    if stored is None:
        return jsonify({'success': False, 'error': 'Agreement not found.'}), 404

    print(f"Agreement {agreement_id} ended by '{request.user_id}'")
    return jsonify({'success': True, 'ended_at': stored})


@app.route('/api/agreements/<agreement_id>', methods=['DELETE'])
@require_auth
def delete_agreement(agreement_id):
    """Erase an agreement, for a data protection request."""
    try:
        deleted = db.delete_agreement(agreement_id)
    except Exception as e:
        print(f"Error deleting agreement {agreement_id}: {e}")
        return jsonify({'success': False, 'error': 'Could not delete agreement.'}), 500

    if not deleted:
        return jsonify({'success': False, 'error': 'Agreement not found.'}), 404

    print(f"Agreement {agreement_id} deleted by '{request.user_id}'")
    return jsonify({'success': True})


# The hire business runs on UK time. Timestamps are stored in UTC, which is the
# same as UK time in winter and an hour behind it all summer, so anything shown
# to a person is converted rather than printed raw. Otherwise a vehicle returned
# at 3pm in July is recorded as having come back at 14:00.
UK_TIMEZONE = ZoneInfo('Europe/London')


def format_money(value):
    """Money to two decimal places. Anything unparseable is left alone."""
    if value in (None, ''):
        return ''
    try:
        return f"{float(str(value).replace(',', '').strip()):.2f}"
    except (TypeError, ValueError):
        return str(value)


def format_date_uk(value):
    """
    Render an <input type="date"> value (YYYY-MM-DD) as DD/MM/YYYY.

    The agreement is a UK document, so ISO dates read as a bug to the hirer
    signing it. Anything that is not an ISO date is passed through untouched.
    """
    if not value:
        return ''
    try:
        return datetime.strptime(value.strip(), '%Y-%m-%d').strftime('%d/%m/%Y')
    except (ValueError, AttributeError):
        return value


def process_signature(sig_base64):
    """Convert base64 signature to PIL Image"""
    if not sig_base64:
        return None
    
    try:
        if ',' in sig_base64:
            sig_base64 = sig_base64.split(',')[1]
        
        img_data = base64.b64decode(sig_base64)
        img = Image.open(BytesIO(img_data))
        
        return img
        
    except Exception as e:
        print(f"Error processing signature: {e}")
        return None


if __name__ == '__main__':
    print("\n" + "="*60)
    print("JL PCO SECURE BACKEND - PRODUCTION MODE")
    print("="*60)
    
    if not ADMIN_PASSWORD_HASH:
        print("\n❌ ERROR: ADMIN_PASSWORD_HASH not set!")
        print("Set it in Railway dashboard under Variables tab")
        exit(1)
    
    print("\n✅ Password hash: CONFIGURED")
    print("✅ Secret key: CONFIGURED")
    print("✅ Security: ENABLED")
    print("✅ NEW FIELDS ADDED:")
    print("   - Email")
    print("   - Phone Number")
    print("   - PCO Badge Number")
    print("\n" + "="*60 + "\n")
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)