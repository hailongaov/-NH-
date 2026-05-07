import os, re, zipfile, plistlib, tempfile, shutil, base64, json, uuid as _uuid, secrets
import hmac as _hmac, hashlib as _hashlib, time as _time
from datetime import datetime, timezone, timedelta
from functools import wraps

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass

from flask import Flask, request, jsonify, send_from_directory, send_file, Response, redirect, url_for, session
from flask_login import LoginManager, login_user, logout_user, current_user
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from authlib.integrations.flask_client import OAuth
from models import db, User, Scan, Setting, Payment

app = Flask(__name__, static_folder='static')
CORS(app, supports_credentials=True)

# ─── Rate Limiter (chống DDoS / brute-force) ──────────────────────────────────
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=['200 per minute', '1000 per hour'],
    storage_uri='memory://',
    on_breach=lambda limit: (jsonify({'error': 'Quá nhiều yêu cầu. Vui lòng thử lại sau.', 'retry_after': int(limit.reset_at.timestamp()) if limit.reset_at else 60}), 429),
)

# ─── Config ───────────────────────────────────────────────────────────────────
app.config['SECRET_KEY']                  = os.environ.get('SECRET_KEY', 'CHANGE-THIS-SECRET-IN-PRODUCTION')
_base_dir = os.path.dirname(os.path.abspath(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    f"sqlite:///{os.path.join(_base_dir, 'data', 'ipa_scanner.db')}"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH']          = int(os.environ.get('MAX_UPLOAD_MB', 500)) * 1024 * 1024
app.config['SESSION_COOKIE_SAMESITE']     = 'Lax'
app.config['SESSION_COOKIE_SECURE']       = os.environ.get('HTTPS', 'false') == 'true'

UPLOADS_DIR = os.path.join(_base_dir, 'data', 'uploads')

# ─── Cloudflare R2 ────────────────────────────────────────────────────────────
R2_ACCOUNT_ID = os.environ.get('R2_ACCOUNT_ID', '')
R2_ACCESS_KEY = os.environ.get('R2_ACCESS_KEY', '')
R2_SECRET_KEY = os.environ.get('R2_SECRET_KEY', '')
R2_BUCKET     = os.environ.get('R2_BUCKET', 'longlq')
R2_PUBLIC_URL = os.environ.get('R2_PUBLIC_URL', '').rstrip('/')

def _r2_client():
    if not R2_ACCOUNT_ID:
        return None
    try:
        import boto3
        from botocore.client import Config
        return boto3.client(
            's3',
            endpoint_url=f'https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com',
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
            config=Config(signature_version='s3v4'),
            region_name='auto',
        )
    except Exception as e:
        print(f'[WARN] R2 client error: {e}')
        return None

def _r2_upload(src_path, key):
    client = _r2_client()
    if not client:
        return False
    try:
        client.upload_file(src_path, R2_BUCKET, key,
                           ExtraArgs={'ContentType': 'application/octet-stream'})
        return True
    except Exception as e:
        print(f'[WARN] R2 upload {key}: {e}')
        return False

def _r2_delete(key):
    client = _r2_client()
    if not client:
        return
    try:
        client.delete_object(Bucket=R2_BUCKET, Key=key)
    except Exception as e:
        print(f'[WARN] R2 delete {key}: {e}')

def _r2_url(key):
    if R2_PUBLIC_URL:
        return f'{R2_PUBLIC_URL}/{key}'
    return None

# ─── Plan limits ──────────────────────────────────────────────────────────────
PLAN_LIMITS = {
    'free': {
        'daily_uploads':  3,
        'storage_bytes':  500 * 1024 * 1024,    # 500 MB
        'storage_label':  '500 MB',
        'link_days':      7,
        'max_downloads':  100,
    },
    'premium': {
        'daily_uploads':  None,
        'storage_bytes':  10 * 1024 * 1024 * 1024,  # 10 GB
        'storage_label':  '10 GB',
        'link_days':      None,
        'max_downloads':  None,
    },
}

# ─── Pricing plans ────────────────────────────────────────────────────────────
PRICING_PLANS = [
    {'months': 1,  'price': 99_000,  'label': '1 tháng',  'badge': '',              'popular': False},
    {'months': 3,  'price': 249_000, 'label': '3 tháng',  'badge': 'Phổ biến ⭐',  'popular': True},
    {'months': 12, 'price': 799_000, 'label': '12 tháng', 'badge': 'Tiết kiệm 33%', 'popular': False},
]

def _bank_configured():
    return bool(Setting.query.filter_by(key='bank_account').first() and
                Setting.get('bank_account', '').strip())

def _bank_qr_url(amount, content):
    from urllib.parse import quote
    bank    = Setting.get('bank_name',    'MB')
    account = Setting.get('bank_account', '')
    owner   = Setting.get('bank_owner',   '')
    if not account:
        return ''
    return (f'https://img.vietqr.io/image/{quote(bank)}-{quote(account)}-qr_only.png'
            f'?amount={amount}&addInfo={quote(content)}&accountName={quote(owner)}')

# ─── Google OAuth ─────────────────────────────────────────────────────────────
oauth = OAuth(app)
_gcid = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
_gcs  = os.environ.get('GOOGLE_CLIENT_SECRET', '').strip()
google_oauth = None
if _gcid and _gcs:
    google_oauth = oauth.register(
        name='google',
        client_id=_gcid,
        client_secret=_gcs,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = None

@login_manager.user_loader
def load_user(uid):
    return User.query.get(int(uid))

@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({'error': 'Chưa đăng nhập', 'redirect': '/login'}), 401


# ─── Decorators ───────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({'error': 'Chưa đăng nhập'}), 401
        if not current_user.is_admin:
            return jsonify({'error': 'Không có quyền admin'}), 403
        return f(*args, **kwargs)
    return decorated


# ─── DB Init ──────────────────────────────────────────────────────────────────

def init_db():
    os.makedirs(os.path.join(_base_dir, 'data'), exist_ok=True)
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    db.create_all()
    # Schema migration: add columns if they don't exist
    from sqlalchemy import text
    with db.engine.connect() as conn:
        for col_def in [
            "ALTER TABLE users ADD COLUMN google_id VARCHAR(100)",
            "ALTER TABLE users ADD COLUMN plan VARCHAR(20) DEFAULT 'free'",
            "ALTER TABLE users ADD COLUMN plan_expires_at DATETIME",
            "ALTER TABLE users ADD COLUMN storage_used INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN daily_uploads_count INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN daily_uploads_date DATE",
            "ALTER TABLE scans ADD COLUMN file_uuid VARCHAR(36)",
            "ALTER TABLE scans ADD COLUMN short_code VARCHAR(12)",
            "ALTER TABLE scans ADD COLUMN download_count INTEGER DEFAULT 0",
            "ALTER TABLE scans ADD COLUMN file_size_bytes INTEGER",
            "ALTER TABLE scans ADD COLUMN ipa_stored BOOLEAN DEFAULT 0",
        ]:
            try:
                conn.execute(text(col_def))
                conn.commit()
            except Exception:
                pass

    from sqlalchemy.exc import IntegrityError as _IntegrityError

    _default_settings = {
        'site_name':        'IPA Scanner',
        'require_login':    'false',
        'allow_register':   'true',
        'max_upload_mb':    '500',
        'footer_text':      '© 2024 IPA Scanner',
        'bank_name':        '',
        'bank_account':     '',
        'bank_owner':       '',
        'bank_stc_secret':  '',
        'contact_zalo':     '',
        'contact_telegram': '',
        'contact_email':    '',
        'contact_website':  '',
    }

    try:
        with db.session.no_autoflush:
            if not User.query.filter_by(username='admin').first():
                admin_pw = os.environ.get('ADMIN_PASSWORD', 'admin@123')
                admin = User(username='admin', email='admin@localhost', role='admin')
                admin.set_password(admin_pw)
                db.session.add(admin)
                print(f'✅  Admin tạo thành công → username: admin  password: {admin_pw}')
            for k, v in _default_settings.items():
                if not Setting.query.filter_by(key=k).first():
                    db.session.add(Setting(key=k, value=v))
        db.session.commit()
    except _IntegrityError:
        db.session.rollback()
        # Another worker already created admin — just ensure settings exist
        with db.session.no_autoflush:
            for k, v in _default_settings.items():
                if not Setting.query.filter_by(key=k).first():
                    db.session.add(Setting(key=k, value=v))
        db.session.commit()


# ─── Pages ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'landing.html')

@app.route('/scan')
def scan_page():
    return send_from_directory('static', 'scan.html')

@app.route('/login')
def login_page():
    return send_from_directory('static', 'login.html')

@app.route('/register')
def register_page():
    return send_from_directory('static', 'register.html')

@app.route('/pricing')
def pricing_page():
    return send_from_directory('static', 'pricing.html')

@app.route('/payment/success')
def payment_success():
    return send_from_directory('static', 'payment-result.html')

@app.route('/payment/cancel')
def payment_cancel():
    return send_from_directory('static', 'payment-result.html')

@app.route('/admin')
def admin_page():
    return send_from_directory('static', 'admin.html')

@app.route('/payment/history')
def payment_history_page():
    return send_from_directory('static', 'payment-history.html')

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

_SHORT_CODE_RE = re.compile(r'^[a-z0-9]{4,10}$')

@app.route('/<path:filename>')
def static_files(filename):
    if '/' not in filename and _SHORT_CODE_RE.match(filename):
        scan = Scan.query.filter_by(short_code=filename, ipa_stored=True).first()
        if scan:
            # Check link expiry (free plan: 7 days)
            if scan.user_id:
                owner    = User.query.get(scan.user_id)
                eff      = owner.effective_plan() if owner else 'free'
                link_days = PLAN_LIMITS[eff]['link_days']
                if link_days:
                    expiry = scan.created_at + timedelta(days=link_days)
                    if datetime.utcnow() > expiry:
                        return _render_limit_page(
                            'Link đã hết hạn',
                            f'Link tải chỉ tồn tại {link_days} ngày với gói Free. Chủ file cần nâng cấp Premium để có link vĩnh viễn.'
                        )
            return _render_install_html(scan)
    return send_from_directory('static', filename)


def _render_limit_page(title, desc):
    return f'''<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:-apple-system,sans-serif;background:#0f0f1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}}
.card{{background:#1a1a2e;border:1px solid #2d2d4e;border-radius:20px;padding:40px 32px;max-width:400px;width:100%;text-align:center}}
.icon{{font-size:52px;margin-bottom:20px}}.title{{font-size:20px;font-weight:700;margin-bottom:10px;color:#f87171}}
.desc{{font-size:14px;color:#94a3b8;line-height:1.6;margin-bottom:28px}}
.btn{{display:inline-block;padding:12px 28px;border-radius:10px;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;font-size:14px;font-weight:600;text-decoration:none}}</style>
</head><body><div class="card">
<div class="icon">⏰</div>
<div class="title">{title}</div>
<div class="desc">{desc}</div>
<a href="/pricing" class="btn">⭐ Nâng cấp Premium</a>
</div></body></html>'''


# ─── Auth API ─────────────────────────────────────────────────────────────────

@app.route('/api/auth/login', methods=['POST'])
@limiter.limit('10 per minute; 30 per hour')
def api_login():
    data = request.get_json() or {}
    uname = (data.get('username') or '').strip()
    pw    = data.get('password') or ''
    if not uname or not pw:
        return jsonify({'error': 'Vui lòng nhập đầy đủ thông tin'}), 400
    user = User.query.filter(
        (User.username == uname) | (User.email == uname)
    ).first()
    if not user or not user.check_password(pw):
        return jsonify({'error': 'Tên đăng nhập hoặc mật khẩu không đúng'}), 401
    if not user.is_active_user:
        return jsonify({'error': 'Tài khoản đã bị vô hiệu hóa'}), 403
    login_user(user, remember=bool(data.get('remember')))
    user.last_login = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'user': {
        'id': user.id, 'username': user.username,
        'role': user.role, 'is_admin': user.is_admin,
    }})


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    logout_user()
    return jsonify({'success': True})


@app.route('/api/auth/google_enabled')
def api_google_enabled():
    return jsonify({'enabled': google_oauth is not None})


@app.route('/api/auth/register', methods=['POST'])
@limiter.limit('5 per minute; 20 per hour')
def api_register():
    if Setting.get('allow_register', 'true') != 'true':
        return jsonify({'error': 'Đăng ký tài khoản mới đang bị tắt'}), 403
    d = request.get_json() or {}
    username = (d.get('username') or '').strip()
    email    = (d.get('email')    or '').strip()
    password = d.get('password')  or ''
    if not username or not password:
        return jsonify({'error': 'Vui lòng nhập đầy đủ thông tin'}), 400
    if len(username) < 3:
        return jsonify({'error': 'Username phải có ít nhất 3 ký tự'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Mật khẩu phải có ít nhất 6 ký tự'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username đã tồn tại'}), 409
    if email and User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email đã được sử dụng'}), 409
    u = User(username=username, email=email or None, role='user')
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    login_user(u, remember=True)
    u.last_login = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'user': {
        'id': u.id, 'username': u.username,
        'role': u.role, 'is_admin': u.is_admin,
    }}), 201


@app.route('/auth/google')
@limiter.limit('20 per minute')
def auth_google():
    if not google_oauth:
        return redirect('/login?error=Google+OAuth+chưa+được+cấu+hình')
    # Dùng GOOGLE_REDIRECT_URI từ .env nếu có, fallback url_for
    redirect_uri = os.environ.get('GOOGLE_REDIRECT_URI') or url_for('auth_google_callback', _external=True)
    return google_oauth.authorize_redirect(redirect_uri)


@app.route('/auth/google/callback')
def auth_google_callback():
    if not google_oauth:
        return redirect('/login?error=oauth_disabled')
    try:
        token     = google_oauth.authorize_access_token()
        user_info = token.get('userinfo') or {}
        google_id = user_info.get('sub', '')
        email     = user_info.get('email', '').lower()
        name      = (user_info.get('name') or user_info.get('given_name') or '').strip()
        if not google_id:
            return redirect('/login?error=Không+lấy+được+thông+tin+Google')

        user = User.query.filter_by(google_id=google_id).first()
        if not user and email:
            user = User.query.filter_by(email=email).first()
            if user:
                user.google_id = google_id
                db.session.commit()
        if not user:
            base = re.sub(r'[^a-z0-9_]', '', (name or email.split('@')[0]).lower()) or 'user'
            uname, i = base[:20], 1
            while User.query.filter_by(username=uname).first():
                uname = f'{base[:18]}{i}'; i += 1
            user = User(username=uname, email=email or None, google_id=google_id, role='user')
            user.set_password(secrets.token_hex(32))
            db.session.add(user)
            db.session.commit()

        if not user.is_active_user:
            return redirect('/login?error=Tài+khoản+đã+bị+vô+hiệu+hóa')
        login_user(user, remember=True)
        user.last_login = datetime.utcnow()
        db.session.commit()
        return redirect('/admin' if user.is_admin else '/scan')
    except Exception as e:
        print(f'[OAuth Error] {e}')
        return redirect('/login?error=Đăng+nhập+Google+thất+bại')


@app.route('/api/auth/me')
def api_me():
    if current_user.is_authenticated:
        u = current_user
        eff = u.effective_plan()
        lim = PLAN_LIMITS[eff]
        return jsonify({'logged_in': True, 'user': {
            'id': u.id, 'username': u.username,
            'role': u.role, 'is_admin': u.is_admin,
            'plan': eff,
            'plan_expires_at': u.plan_expires_at.strftime('%d/%m/%Y') if u.plan_expires_at else None,
            'storage_used': u.storage_used or 0,
            'storage_limit': lim['storage_bytes'],
            'storage_label': lim['storage_label'],
            'daily_uploads_count': u.daily_uploads_count or 0,
            'daily_uploads_limit': lim['daily_uploads'],
        }})
    return jsonify({'logged_in': False})


# ─── User plan API ────────────────────────────────────────────────────────────

@app.route('/api/user/plan')
def api_user_plan():
    if not current_user.is_authenticated:
        return jsonify({'plan': 'guest', 'limits': PLAN_LIMITS['free']})
    u   = current_user
    eff = u.effective_plan()
    lim = PLAN_LIMITS[eff]
    today = datetime.utcnow().date()
    daily = u.daily_uploads_count if u.daily_uploads_date == today else 0
    return jsonify({
        'plan':               eff,
        'plan_expires_at':    u.plan_expires_at.strftime('%d/%m/%Y') if u.plan_expires_at else None,
        'storage_used':       u.storage_used or 0,
        'storage_limit':      lim['storage_bytes'],
        'storage_label':      lim['storage_label'],
        'daily_uploads':      daily,
        'daily_limit':        lim['daily_uploads'],
        'link_days':          lim['link_days'],
        'max_downloads':      lim['max_downloads'],
    })


# ─── Payment API ──────────────────────────────────────────────────────────────

@app.route('/api/payment/plans')
def api_payment_plans():
    return jsonify({'plans': PRICING_PLANS, 'bank_configured': _bank_configured()})


@app.route('/api/payment/bank-info')
def api_bank_info():
    return jsonify({
        'configured':    _bank_configured(),
        'bank_name':     Setting.get('bank_name',    ''),
        'bank_account':  Setting.get('bank_account', ''),
        'bank_owner':    Setting.get('bank_owner',   ''),
    })


@app.route('/api/payment/create', methods=['POST'])
def api_payment_create():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Vui lòng đăng nhập'}), 401
    if not _bank_configured():
        return jsonify({'error': 'Thanh toán chưa được cấu hình. Vui lòng liên hệ admin.'}), 503

    d      = request.get_json() or {}
    months = int(d.get('months', 1))
    plan_map = {p['months']: p['price'] for p in PRICING_PLANS}
    if months not in plan_map:
        return jsonify({'error': 'Gói không hợp lệ'}), 400

    amount = plan_map[months]

    # Generate unique random order code (8 alphanumeric chars, case-insensitive)
    import random as _rnd, string as _str
    _chars = _str.ascii_uppercase + _str.digits
    for _ in range(30):
        code = ''.join(_rnd.choices(_chars, k=8))
        if not Payment.query.filter_by(order_code=code).first():
            break
    transfer_content = f'IPA{code}'

    pmt = Payment(user_id=current_user.id, order_code=code,
                  amount=amount, plan_months=months, status='pending')
    db.session.add(pmt)
    db.session.commit()

    return jsonify({
        'success':          True,
        'order_code':       code,
        'amount':           amount,
        'amount_fmt':       f'{amount:,}đ'.replace(',', '.'),
        'transfer_content': transfer_content,
        'bank_name':        Setting.get('bank_name',    ''),
        'bank_account':     Setting.get('bank_account', ''),
        'bank_owner':       Setting.get('bank_owner',   ''),
        'qr_url':           _bank_qr_url(amount, transfer_content),
        'plan_months':      months,
    })


@app.route('/api/payment/check')
def api_payment_check():
    order_code = request.args.get('order_code', '').strip()
    pmt = Payment.query.filter_by(order_code=order_code).first()
    if not pmt:
        return jsonify({'status': 'not_found'}), 404
    return jsonify({'status': pmt.status, 'amount': pmt.amount,
                    'plan_months': pmt.plan_months,
                    'paid_at': pmt.paid_at.isoformat() if pmt.paid_at else None})


@app.route('/api/payment/history')
def api_payment_history():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Chưa đăng nhập'}), 401
    pmts = Payment.query.filter_by(user_id=current_user.id).order_by(Payment.created_at.desc()).limit(20).all()
    return jsonify({'payments': [p.to_dict() for p in pmts]})


# ─── SieuThiCode bank webhook ─────────────────────────────────────────────────

@app.route('/webhook/bank', methods=['GET', 'POST'])
def bank_webhook():
    if request.method == 'GET':
        return jsonify({'status': True, 'msg': 'OK'})

    # Validate signature header (SieuThiCode sends lowercase 'signature')
    recv_sig = (request.headers.get('signature') or
                request.headers.get('Signature') or '')
    expected = Setting.get('bank_stc_secret', '').strip()
    if not expected or recv_sig != expected:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(silent=True) or {}
    for tx in data.get('transactions', []):
        if tx.get('type') != 'IN':
            continue
        amount = int(tx.get('amount', 0))
        if amount < 10_000:
            continue
        tid  = str(tx.get('transactionID', '')).strip()
        desc = str(tx.get('description',  '')).strip()

        # Deduplicate by transactionID
        if tid and Payment.query.filter_by(order_code=tid).first():
            continue

        # Match pattern IPA{8-char random code} in description
        m = re.search(r'IPA([A-Z0-9]{6,12})', desc.upper())
        if not m:
            continue
        order_code = m.group(1)

        pmt = Payment.query.filter_by(order_code=order_code, status='pending').first()
        if not pmt:
            continue

        # Verify amount >= expected plan price (allow slight under-payment due to bank fees)
        expected_amount = {p['months']: p['price'] for p in PRICING_PLANS}.get(pmt.plan_months, 0)
        if amount < expected_amount - 2000:
            print(f'[BANK] Thiếu tiền: nhận {amount}đ, cần {expected_amount}đ — order {order_code}')
            continue

        # Mark payment paid
        pmt.status  = 'paid'
        pmt.paid_at = datetime.utcnow()

        # Auto-upgrade user to premium
        user = User.query.get(pmt.user_id)
        if user:
            user.plan = 'premium'
            now  = datetime.utcnow()
            base = user.plan_expires_at if (user.plan_expires_at and user.plan_expires_at > now) else now
            user.plan_expires_at = base + timedelta(days=30 * pmt.plan_months)
            print(f'[BANK] +{amount}đ → user #{pmt.user_id} ({user.username}) · Premium {pmt.plan_months}th · hết hạn {user.plan_expires_at.date()} · order {order_code}')
        db.session.commit()

    return jsonify({'status': True, 'msg': 'OK'})


# ─── My Apps API ──────────────────────────────────────────────────────────────

@app.route('/api/my/apps')
def api_my_apps():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Chưa đăng nhập'}), 401
    scans = (Scan.query
             .filter_by(user_id=current_user.id, ipa_stored=True)
             .order_by(Scan.created_at.desc())
             .all())
    base_url = request.host_url.rstrip('/')
    items = []
    for s in scans:
        short = f'{base_url}/{s.short_code}' if s.short_code else f'{base_url}/install/{s.file_uuid}'
        items.append({
            'id':           s.id,
            'app_name':     s.app_name or s.filename,
            'bundle_id':    s.bundle_id or '',
            'version':      s.version or '',
            'file_size':    s.file_size or '',
            'profile_type': s.profile_type or '',
            'expiry_date':  s.expiry_date or '',
            'short_url':    short,
            'icon_base64':  s.icon_base64 or '',
            'created_at':   s.created_at.strftime('%d/%m/%Y') if s.created_at else '',
            'download_count': s.download_count or 0,
            'custom_name':  s.app_name or '',
        })
    return jsonify({'apps': items})


@app.route('/api/my/apps/<int:scan_id>', methods=['PATCH'])
def api_my_app_update(scan_id):
    if not current_user.is_authenticated:
        return jsonify({'error': 'Chưa đăng nhập'}), 401
    s = Scan.query.filter_by(id=scan_id, user_id=current_user.id).first_or_404()
    d = request.get_json() or {}
    if 'app_name' in d:
        s.app_name = d['app_name'][:100].strip()
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/my/apps/<int:scan_id>', methods=['DELETE'])
def api_my_app_delete(scan_id):
    if not current_user.is_authenticated:
        return jsonify({'error': 'Chưa đăng nhập'}), 401
    s = Scan.query.filter_by(id=scan_id, user_id=current_user.id).first_or_404()
    _delete_ipa_file(s.file_uuid)
    _deduct_storage(s)
    db.session.delete(s)
    db.session.commit()
    return jsonify({'success': True})


# ─── Scan API ─────────────────────────────────────────────────────────────────

@app.route('/api/scan', methods=['POST'])
@limiter.limit('10 per minute; 50 per hour')
def scan_ipa():
    is_guest = not current_user.is_authenticated
    if is_guest and Setting.get('require_login', 'false') == 'true':
        return jsonify({'error': 'Vui lòng đăng nhập để upload IPA', 'redirect': '/login'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'Chưa chọn file IPA'}), 400
    f = request.files['file']
    if not f.filename.lower().endswith('.ipa'):
        return jsonify({'error': 'File phải có định dạng .ipa'}), 400

    # ── Enforce plan limits (chỉ áp dụng với user đã đăng nhập) ─────────────
    if not is_guest:
        today = datetime.utcnow().date()
        u   = current_user._get_current_object()
        eff = u.effective_plan()
        lim = PLAN_LIMITS[eff]
        if u.daily_uploads_date != today:
            u.daily_uploads_count = 0
            u.daily_uploads_date  = today
            db.session.commit()
        if lim['daily_uploads'] and (u.daily_uploads_count or 0) >= lim['daily_uploads']:
            return jsonify({
                'error': f'Bạn đã đạt giới hạn {lim["daily_uploads"]} lần upload/ngày (gói Free). Nâng cấp Premium để upload không giới hạn.',
                'upgrade_required': True, 'upgrade_url': '/pricing',
            }), 429
        if lim['storage_bytes'] and (u.storage_used or 0) >= lim['storage_bytes']:
            return jsonify({
                'error': f'Đã hết dung lượng lưu trữ ({lim["storage_label"]}). Nâng cấp Premium để có 10 GB.',
                'upgrade_required': True, 'upgrade_url': '/pricing',
            }), 429
    # ────────────────────────────────────────────────────────────────────────

    tmpdir = tempfile.mkdtemp()
    try:
        ipa_path = os.path.join(tmpdir, 'upload.ipa')
        f.save(ipa_path)
        size_bytes = os.path.getsize(ipa_path)
        result = parse_ipa(ipa_path)
        result['file_name'] = f.filename
        result['file_size'] = format_size(size_bytes)

        file_uuid  = str(_uuid.uuid4())
        short_code = None
        ipa_stored = False
        # Guest chỉ scan metadata, không lưu file
        if not is_guest:
            try:
                r2_key = f'{file_uuid}.ipa'
                if R2_ACCOUNT_ID and _r2_upload(ipa_path, r2_key):
                    ipa_stored = True
                    short_code = _gen_short_code()
                else:
                    dest = os.path.join(UPLOADS_DIR, r2_key)
                    shutil.copy2(ipa_path, dest)
                    ipa_stored = True
                    short_code = _gen_short_code()
            except Exception as e:
                print(f'[WARN] could not save IPA: {e}')

        _save_scan(result, f.filename, format_size(size_bytes), size_bytes, file_uuid, ipa_stored, short_code)

        # Update user quota
        if not is_guest:
            u = current_user._get_current_object()
            u.daily_uploads_count = (u.daily_uploads_count or 0) + 1
            u.daily_uploads_date  = datetime.utcnow().date()
            if ipa_stored:
                u.storage_used = (u.storage_used or 0) + size_bytes
            db.session.commit()

        base_url = request.host_url.rstrip('/')
        result['file_uuid']    = file_uuid
        result['short_code']   = short_code
        result['ipa_stored']   = ipa_stored
        result['download_url'] = f'{base_url}/files/{file_uuid}.ipa' if ipa_stored else None
        result['short_url']    = f'{base_url}/{short_code}' if short_code else None
        result['install_url']  = (
            f'itms-services://?action=download-manifest&url={base_url}/manifests/{file_uuid}.plist'
            if ipa_stored else None
        )
        result['install_page'] = f'{base_url}/{short_code}' if short_code else None
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _save_scan(result, filename, file_size, file_size_bytes=None, file_uuid=None, ipa_stored=False, short_code=None):
    try:
        ai   = result.get('app_info', {})
        prov = result.get('provision') or {}
        certs = result.get('certificates', [])
        slim = {k: v for k, v in result.items() if k not in ('icon_base64', 'download_url', 'install_url', 'install_page')}
        slim['icon_base64'] = '[omitted]'
        scan = Scan(
            file_uuid       = file_uuid,
            short_code      = short_code,
            ipa_stored      = ipa_stored,
            file_size_bytes = file_size_bytes,
            user_id      = current_user.id if current_user.is_authenticated else None,
            filename     = filename,
            file_size    = file_size,
            app_name     = ai.get('display_name', ''),
            bundle_id    = ai.get('bundle_id', ''),
            version      = ai.get('version', ''),
            build        = ai.get('build', ''),
            min_os       = ai.get('min_os', ''),
            profile_type = prov.get('profile_type', ''),
            expiry_date  = prov.get('expiry', ''),
            days_left    = prov.get('days_left'),
            team_name    = prov.get('team_name', ''),
            cert_count   = len(certs),
            icon_base64  = result.get('icon_base64') or None,
            result_json  = json.dumps(slim, ensure_ascii=False),
            ip_address   = request.remote_addr,
        )
        db.session.add(scan)
        db.session.commit()
    except Exception as e:
        print(f'[WARN] save_scan: {e}')


# ─── Admin API ────────────────────────────────────────────────────────────────

@app.route('/api/admin/stats')
@admin_required
def admin_stats():
    from sqlalchemy import func
    from datetime import timedelta
    now   = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week  = today - timedelta(days=7)

    profile_rows = db.session.query(Scan.profile_type, func.count(Scan.id))\
        .group_by(Scan.profile_type).all()

    recent = Scan.query.order_by(Scan.created_at.desc()).limit(8).all()

    return jsonify({
        'total_scans':   Scan.query.count(),
        'today_scans':   Scan.query.filter(Scan.created_at >= today).count(),
        'week_scans':    Scan.query.filter(Scan.created_at >= week).count(),
        'total_users':   User.query.count(),
        'expiring_soon': Scan.query.filter(Scan.days_left != None, Scan.days_left >= 0, Scan.days_left <= 30).count(),
        'expired':       Scan.query.filter(Scan.days_left != None, Scan.days_left < 0).count(),
        'profile_breakdown': {pt or '—': cnt for pt, cnt in profile_rows},
        'recent_scans':  [s.to_dict() for s in recent],
    })


@app.route('/api/admin/scans')
@admin_required
def admin_scans():
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    search   = request.args.get('q', '').strip()
    ptype    = request.args.get('profile_type', '')

    q = Scan.query
    if search:
        q = q.filter(
            Scan.app_name.ilike(f'%{search}%') |
            Scan.bundle_id.ilike(f'%{search}%') |
            Scan.filename.ilike(f'%{search}%') |
            Scan.team_name.ilike(f'%{search}%')
        )
    if ptype:
        q = q.filter(Scan.profile_type == ptype)

    pg = q.order_by(Scan.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'scans': [s.to_dict() for s in pg.items],
        'total': pg.total, 'pages': pg.pages, 'page': page,
    })


@app.route('/api/admin/scans/<int:sid>')
@admin_required
def admin_scan_detail(sid):
    return jsonify(Scan.query.get_or_404(sid).to_dict(include_result=True))


@app.route('/api/admin/scans/<int:sid>', methods=['DELETE'])
@admin_required
def admin_delete_scan(sid):
    s = Scan.query.get_or_404(sid)
    _delete_ipa_file(s.file_uuid)
    _deduct_storage(s)
    db.session.delete(s)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/admin/scans/bulk-delete', methods=['POST'])
@admin_required
def admin_bulk_delete():
    ids = (request.get_json() or {}).get('ids', [])
    if ids:
        for s in Scan.query.filter(Scan.id.in_(ids)).all():
            _delete_ipa_file(s.file_uuid)
            _deduct_storage(s)
        Scan.query.filter(Scan.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
    return jsonify({'success': True, 'deleted': len(ids)})


@app.route('/api/admin/users')
@admin_required
def admin_users():
    return jsonify({'users': [u.to_dict() for u in User.query.order_by(User.created_at.desc()).all()]})


@app.route('/api/admin/users', methods=['POST'])
@admin_required
def admin_create_user():
    d = request.get_json() or {}
    uname = (d.get('username') or '').strip()
    pw    = d.get('password') or ''
    if not uname or not pw:
        return jsonify({'error': 'Thiếu username hoặc mật khẩu'}), 400
    if len(pw) < 6:
        return jsonify({'error': 'Mật khẩu phải có ít nhất 6 ký tự'}), 400
    if User.query.filter_by(username=uname).first():
        return jsonify({'error': 'Username đã tồn tại'}), 409
    u = User(username=uname, email=d.get('email') or None, role=d.get('role', 'user'))
    u.set_password(pw)
    db.session.add(u)
    db.session.commit()
    return jsonify({'success': True, 'user': u.to_dict()}), 201


@app.route('/api/admin/users/<int:uid>', methods=['PUT'])
@admin_required
def admin_update_user(uid):
    u = User.query.get_or_404(uid)
    d = request.get_json() or {}
    if 'email'     in d: u.email          = d['email'].strip() or None
    if 'role'      in d: u.role           = d['role']
    if 'is_active' in d: u.is_active_user = bool(d['is_active'])
    if d.get('password'):
        if len(d['password']) < 6:
            return jsonify({'error': 'Mật khẩu phải có ít nhất 6 ký tự'}), 400
        u.set_password(d['password'])
    db.session.commit()
    return jsonify({'success': True, 'user': u.to_dict()})


@app.route('/api/admin/users/<int:uid>', methods=['DELETE'])
@admin_required
def admin_delete_user(uid):
    if uid == current_user.id:
        return jsonify({'error': 'Không thể xóa tài khoản đang đăng nhập'}), 400
    u = User.query.get_or_404(uid)
    db.session.delete(u)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/admin/users/<int:uid>/upgrade', methods=['POST'])
@admin_required
def admin_upgrade_user(uid):
    u = User.query.get_or_404(uid)
    d = request.get_json() or {}
    months = int(d.get('months', 1))
    if months not in {1, 3, 12}:
        return jsonify({'error': 'Số tháng không hợp lệ (1/3/12)'}), 400
    now  = datetime.utcnow()
    base = u.plan_expires_at if (u.plan_expires_at and u.plan_expires_at > now) else now
    u.plan            = 'premium'
    u.plan_expires_at = base + timedelta(days=30 * months)
    db.session.commit()
    return jsonify({'success': True, 'user': u.to_dict()})


@app.route('/api/admin/users/<int:uid>/downgrade', methods=['POST'])
@admin_required
def admin_downgrade_user(uid):
    u = User.query.get_or_404(uid)
    u.plan            = 'free'
    u.plan_expires_at = None
    db.session.commit()
    return jsonify({'success': True, 'user': u.to_dict()})


@app.route('/api/admin/settings')
@admin_required
def admin_get_settings():
    return jsonify({s.key: s.value for s in Setting.query.all()})


@app.route('/api/admin/settings', methods=['POST'])
@admin_required
def admin_update_settings():
    for k, v in (request.get_json() or {}).items():
        Setting.set(k, v)
    return jsonify({'success': True})


# ─── File serving ─────────────────────────────────────────────────────────────

import random as _random, string as _strlib

def _gen_short_code():
    chars = _strlib.ascii_lowercase + _strlib.digits
    for _ in range(20):
        code = ''.join(_random.choices(chars, k=6))
        if not Scan.query.filter_by(short_code=code).first():
            return code
    return _uuid.uuid4().hex[:8]


def _deduct_storage(scan):
    if scan.user_id and scan.ipa_stored and scan.file_size_bytes:
        try:
            user = User.query.get(scan.user_id)
            if user:
                user.storage_used = max(0, (user.storage_used or 0) - scan.file_size_bytes)
                db.session.commit()
        except Exception as e:
            print(f'[WARN] _deduct_storage user #{scan.user_id}: {e}')


def _delete_ipa_file(file_uuid):
    if not file_uuid: return
    if R2_ACCOUNT_ID:
        _r2_delete(f'{file_uuid}.ipa')
    try:
        p = os.path.join(UPLOADS_DIR, f'{file_uuid}.ipa')
        if os.path.exists(p): os.remove(p)
    except Exception as e:
        print(f'[WARN] delete_ipa_file {file_uuid}: {e}')

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


@app.route('/files/<path:uuid_ipa>')
def serve_ipa(uuid_ipa):
    file_uuid = uuid_ipa.removesuffix('.ipa')
    if not _UUID_RE.match(file_uuid):
        return jsonify({'error': 'Invalid'}), 400
    scan = Scan.query.filter_by(file_uuid=file_uuid, ipa_stored=True).first_or_404()
    # Enforce download limit for owner's plan
    if scan.user_id:
        owner = User.query.get(scan.user_id)
        eff   = owner.effective_plan() if owner else 'free'
        max_dl = PLAN_LIMITS[eff]['max_downloads']
        if max_dl and (scan.download_count or 0) >= max_dl:
            return _render_limit_page(
                f'Link này đã đạt giới hạn {max_dl} lượt tải (gói Free).',
                'Để tải thêm, chủ file cần nâng cấp lên Premium.'
            ), 429

    scan.download_count = (scan.download_count or 0) + 1
    db.session.commit()
    r2 = _r2_url(f'{file_uuid}.ipa')
    if r2:
        return redirect(r2)
    path = os.path.join(UPLOADS_DIR, f'{file_uuid}.ipa')
    if not os.path.isfile(path):
        return jsonify({'error': 'File không còn trên máy chủ'}), 404
    return send_file(path, as_attachment=True, download_name=scan.filename,
                     mimetype='application/octet-stream')


@app.route('/manifests/<path:uuid_plist>')
def serve_manifest(uuid_plist):
    file_uuid = uuid_plist.removesuffix('.plist')
    if not _UUID_RE.match(file_uuid):
        return jsonify({'error': 'Invalid'}), 400
    scan = Scan.query.filter_by(file_uuid=file_uuid, ipa_stored=True).first_or_404()
    base_url = request.host_url.rstrip('/')
    ipa_url = _r2_url(f'{file_uuid}.ipa') or f'{base_url}/files/{file_uuid}.ipa'
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>items</key>
  <array>
    <dict>
      <key>assets</key>
      <array>
        <dict>
          <key>kind</key><string>software-package</string>
          <key>url</key><string>{ipa_url}</string>
        </dict>
      </array>
      <key>metadata</key>
      <dict>
        <key>bundle-identifier</key><string>{scan.bundle_id or 'com.unknown.app'}</string>
        <key>bundle-version</key><string>{scan.version or '1.0'}</string>
        <key>kind</key><string>software</string>
        <key>title</key><string>{scan.app_name or scan.filename}</string>
      </dict>
    </dict>
  </array>
</dict>
</plist>'''
    return Response(xml, content_type='application/xml; charset=utf-8')


@app.route('/install/<file_uuid>')
def install_page(file_uuid):
    if not _UUID_RE.match(file_uuid):
        return 'Invalid UUID', 400
    scan = Scan.query.filter_by(file_uuid=file_uuid, ipa_stored=True).first_or_404()
    return _render_install_html(scan)


def _render_install_html(scan):
    base_url     = request.host_url.rstrip('/')
    file_uuid    = scan.file_uuid
    manifest_url = f'{base_url}/manifests/{file_uuid}.plist'
    install_url  = f'itms-services://?action=download-manifest&url={manifest_url}'
    download_url = f'{base_url}/files/{file_uuid}.ipa'
    short_url    = f'{base_url}/{scan.short_code}' if scan.short_code else f'{base_url}/install/{file_uuid}'
    is_https     = base_url.startswith('https')
    site_name    = Setting.get('site_name', 'IPA Scanner')
    profile_type = (scan.profile_type or '').strip()

    icon_src = f'data:image/png;base64,{scan.icon_base64}' if scan.icon_base64 else ''

    # Estimated install time based on file size
    try:
        mb = float((scan.file_size or '0').replace(' MB','').replace(' GB','').replace(',','.'))
        if 'GB' in (scan.file_size or ''): mb *= 1024
        est = f'~{max(1, int(mb / 50))} phút'
    except Exception:
        est = '~2 phút'

    # HTTPS warning banner
    https_warn = ('' if is_https else
        '<div class="banner banner-warn">⚠️ Cài đặt OTA cần HTTPS — truy cập qua domain có SSL</div>')

    # Profile type badge
    pt_color = '#FF9500' if 'Enterprise' in profile_type else '#34C759' if 'Ad Hoc' in profile_type else '#007AFF'
    profile_badge = f'<span class="pt-badge" style="background:{pt_color}22;color:{pt_color};border:1px solid {pt_color}44">{profile_type or "Unknown"}</span>' if profile_type else ''

    # Contact buttons
    contacts = []
    zalo = Setting.get('contact_zalo', '')
    tele = Setting.get('contact_telegram', '')
    mail = Setting.get('contact_email', '')
    web  = Setting.get('contact_website', '')
    if zalo: contacts.append(f'<a href="https://zalo.me/{zalo}" target="_blank" class="c-btn" style="background:#0068ff15;color:#0068ff;border-color:#0068ff30">💬 Zalo</a>')
    if tele: contacts.append(f'<a href="https://t.me/{tele.lstrip("@")}" target="_blank" class="c-btn" style="background:#229ed915;color:#229ed9;border-color:#229ed930">✈️ Telegram</a>')
    if mail: contacts.append(f'<a href="mailto:{mail}" class="c-btn" style="background:#5856d615;color:#5856d6;border-color:#5856d630">📧 Email</a>')
    if web:  contacts.append(f'<a href="{web}" target="_blank" class="c-btn" style="background:#34c75915;color:#34c759;border-color:#34c75930">🌐 Website</a>')
    contact_html = (
        f'<div class="section"><div class="section-title">Liên hệ hỗ trợ</div>'
        f'<div class="contact-row">{"".join(contacts)}</div></div>'
        if contacts else ''
    )

    # Trust guide (for Enterprise certs)
    trust_guide = ''
    if 'Enterprise' in profile_type or not profile_type:
        trust_guide = '''<div class="section">
  <div class="section-title">Hướng dẫn tin cậy chứng chỉ</div>
  <div class="trust-steps">
    <div class="trust-step"><span class="step-num">1</span><span>Sau khi cài xong, vào <strong>Cài đặt</strong> (Settings)</span></div>
    <div class="trust-step"><span class="step-num">2</span><span>Chọn <strong>Cài đặt chung</strong> → <strong>VPN &amp; Quản lý thiết bị</strong></span></div>
    <div class="trust-step"><span class="step-num">3</span><span>Tìm tên nhà phát triển → nhấn <strong>Tin cậy</strong></span></div>
    <div class="trust-step"><span class="step-num">4</span><span>Xác nhận <strong>Tin cậy</strong> một lần nữa → Mở app</span></div>
  </div>
</div>'''

    # Pre-compute expressions containing backslashes (f-string restriction < Python 3.12)
    icon_tag = (
        '<img src="' + icon_src + '" class="app-icon" alt=""'
        ' onerror="this.style.display=\'none\';document.getElementById(\'iconf\').style.display=\'block\'">'
        if icon_src else ''
    )
    icon_fallback_attr = ' style="display:none"' if icon_src else ''

    return f'''<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{scan.app_name or scan.filename}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{--blue:#007AFF;--bg:#F2F2F7;--card:#fff;--text:#000;--sub:#6C6C70;--sep:#C6C6C8;--radius:14px}}
body{{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:0 0 40px}}
.top-bar{{background:rgba(242,242,247,.8);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid var(--sep);padding:12px 20px;text-align:center;font-size:13px;color:var(--sub);font-weight:500;position:sticky;top:0;z-index:10}}
.hero{{background:var(--card);padding:28px 20px 24px;text-align:center;border-bottom:1px solid var(--sep)}}
.app-icon{{width:110px;height:110px;border-radius:26px;margin:0 auto 16px;display:block;object-fit:cover;box-shadow:0 4px 20px rgba(0,0,0,.15)}}
.icon-fallback{{background:linear-gradient(135deg,#6366f1,#8b5cf6);font-size:56px;line-height:110px;border-radius:26px}}
.app-name{{font-size:24px;font-weight:700;letter-spacing:-.4px;margin-bottom:4px}}
.app-version{{font-size:15px;color:var(--sub);margin-bottom:12px}}
.pt-badge{{font-size:12px;font-weight:600;padding:3px 10px;border-radius:20px;display:inline-block;margin-bottom:16px}}
.install-btn{{display:block;margin:0 auto;width:calc(100% - 0px);max-width:340px;padding:16px;border-radius:14px;background:var(--blue);color:#fff;font-size:17px;font-weight:700;text-decoration:none;text-align:center;box-shadow:0 4px 16px rgba(0,122,255,.3);transition:.15s}}
.install-btn:active{{transform:scale(.97);opacity:.9}}
.est-time{{font-size:13px;color:var(--sub);margin-top:10px;display:flex;align-items:center;justify-content:center;gap:5px}}
.banner{{margin:12px 16px 0;padding:11px 14px;border-radius:10px;font-size:13px;line-height:1.5;font-weight:500}}
.banner-warn{{background:#FF950015;color:#FF9500;border:1px solid #FF950030}}
.content{{padding:0 16px}}
.section{{background:var(--card);border-radius:var(--radius);margin-top:16px;overflow:hidden}}
.section-title{{font-size:13px;font-weight:600;color:var(--sub);text-transform:uppercase;letter-spacing:.4px;padding:14px 16px 8px}}
.info-item{{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-top:1px solid var(--sep);font-size:15px}}
.info-item:first-of-type{{border-top:none}}
.info-label{{display:flex;align-items:center;gap:8px;color:var(--text)}}
.info-label .ico{{font-size:16px}}
.info-value{{color:var(--sub);font-weight:500;text-align:right;max-width:60%}}
.copy-row{{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-top:1px solid var(--sep);font-size:14px;cursor:pointer;gap:8px}}
.copy-link{{color:var(--blue);font-family:monospace;font-size:12px;word-break:break-all;flex:1}}
.copy-btn-sm{{flex-shrink:0;background:var(--bg);border:1px solid var(--sep);border-radius:8px;padding:5px 12px;font-size:12px;font-weight:600;color:var(--blue);cursor:pointer;white-space:nowrap}}
.trust-steps{{padding:0 16px 14px;display:flex;flex-direction:column;gap:12px}}
.trust-step{{display:flex;align-items:flex-start;gap:12px;font-size:14px;color:#333;line-height:1.5}}
.step-num{{width:26px;height:26px;border-radius:50%;background:var(--blue);color:#fff;font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}}
.contact-row{{display:flex;flex-wrap:wrap;gap:8px;padding:4px 16px 14px}}
.c-btn{{padding:9px 16px;border-radius:10px;font-size:13px;font-weight:600;text-decoration:none;border:1px solid transparent;transition:.15s}}
.download-link{{display:block;text-align:center;margin-top:14px;font-size:14px;color:var(--sub);text-decoration:none}}
.download-link:hover{{color:var(--blue)}}
.copied-toast{{position:fixed;bottom:30px;left:50%;transform:translateX(-50%) translateY(20px);background:#1c1c1e;color:#fff;padding:10px 22px;border-radius:20px;font-size:14px;font-weight:500;opacity:0;transition:.3s;pointer-events:none;z-index:99}}
.copied-toast.show{{opacity:1;transform:translateX(-50%) translateY(0)}}
</style>
</head>
<body>
<div class="top-bar">📦 {site_name}</div>

<div class="hero">
  {icon_tag}
  <div class="app-icon icon-fallback" id="iconf"{icon_fallback_attr}>📱</div>
  <div class="app-name">{scan.app_name or scan.filename}</div>
  <div class="app-version">{scan.version or '—'}</div>
  {profile_badge}
  {'<a class="install-btn" href="'+install_url+'">⬇ Download &amp; Install</a>' if is_https else '<div style="opacity:.4;pointer-events:none;background:#8E8E93;color:#fff;border-radius:14px;padding:16px;text-align:center;font-weight:700">⬇ Download &amp; Install</div>'}
  <div class="est-time">🕐 {est} · Cần Safari iOS</div>
  {https_warn}
</div>

<div class="content">
  <div class="section">
    <div class="section-title">Thông tin ứng dụng</div>
    <div class="info-item">
      <span class="info-label"><span class="ico">📦</span> Dung lượng</span>
      <span class="info-value">{scan.file_size or '—'}</span>
    </div>
    <div class="info-item">
      <span class="info-label"><span class="ico">🏷</span> Phiên bản</span>
      <span class="info-value">{scan.version or '—'} (Build {scan.build or '—'})</span>
    </div>
    <div class="copy-row" onclick="copyLink('{short_url}')">
      <span class="info-label"><span class="ico">🔗</span></span>
      <span class="copy-link">{short_url}</span>
      <button class="copy-btn-sm" onclick="event.stopPropagation();copyLink('{short_url}')">Sao chép</button>
    </div>
  </div>

  {trust_guide}
  {contact_html}

  <a href="{download_url}" class="download-link" download>⬇ Tải file IPA về máy tính</a>
</div>

<div class="copied-toast" id="toast">✓ Đã sao chép link</div>
<script>
function copyLink(url) {{
  navigator.clipboard.writeText(url).catch(()=>{{}});
  const t = document.getElementById('toast');
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 1800);
}}
</script>
</body>
</html>'''


@app.route('/api/admin/files')
@admin_required
def admin_files():
    scans = Scan.query.filter_by(ipa_stored=True).order_by(Scan.created_at.desc()).all()
    total_size = 0
    rows = []
    for s in scans:
        sz = s.file_size_bytes or 0
        total_size += sz
        rows.append({**s.to_dict(), 'disk_size': format_size(sz)})
    return jsonify({'files': rows, 'total_size': format_size(total_size), 'count': len(rows)})


@app.route('/api/admin/files/<file_uuid>', methods=['DELETE'])
@admin_required
def admin_delete_file(file_uuid):
    if not _UUID_RE.match(file_uuid):
        return jsonify({'error': 'Invalid'}), 400
    scan = Scan.query.filter_by(file_uuid=file_uuid).first_or_404()
    _delete_ipa_file(file_uuid)
    scan.ipa_stored = False
    db.session.commit()
    return jsonify({'success': True})


# ─── IPA Parser ───────────────────────────────────────────────────────────────

def parse_ipa(ipa_path):
    result = {'app_info': {}, 'provision': None, 'certificates': [], 'icon_base64': None}
    try:
        zf = zipfile.ZipFile(ipa_path, 'r')
    except zipfile.BadZipFile:
        raise ValueError('File IPA không hợp lệ (không phải ZIP)')
    with zf:
        names = zf.namelist()
        app_dir = None
        for n in names:
            m = re.match(r'(Payload/[^/]+\.app)/', n)
            if m:
                app_dir = m.group(1)
                break
        if not app_dir:
            raise ValueError('Không tìm thấy thư mục .app trong IPA')

        info_path = f'{app_dir}/Info.plist'
        if info_path in names:
            with zf.open(info_path) as fp:
                plist = plistlib.load(fp)
            df = plist.get('UIDeviceFamily', [1])
            plats = (['iPhone'] if 1 in df else []) + (['iPad'] if 2 in df else [])
            result['app_info'] = {
                'display_name': plist.get('CFBundleDisplayName') or plist.get('CFBundleName', 'Unknown'),
                'bundle_name':  plist.get('CFBundleName', ''),
                'bundle_id':    plist.get('CFBundleIdentifier', ''),
                'version':      plist.get('CFBundleShortVersionString', ''),
                'build':        plist.get('CFBundleVersion', ''),
                'min_os':       plist.get('MinimumOSVersion', ''),
                'platforms':    plats or ['iPhone'],
                'executable':   plist.get('CFBundleExecutable', ''),
            }
            icon = _extract_icon(zf, names, app_dir, plist)
            if icon:
                result['icon_base64'] = base64.b64encode(icon).decode()

        prov_path = f'{app_dir}/embedded.mobileprovision'
        if prov_path in names:
            with zf.open(prov_path) as fp:
                prov_bytes = fp.read()
            result['provision']    = _parse_provision(prov_bytes)
            result['certificates'] = _parse_certificates(prov_bytes)
    return result


def _extract_icon(zf, names, app_dir, plist):
    names_set = set(names)

    # 1) Collect candidate base names from plist
    candidates = []
    for key in ['CFBundleIcons', 'CFBundleIcons~ipad']:
        ic = plist.get(key, {})
        if isinstance(ic, dict):
            pi = ic.get('CFBundlePrimaryIcon', {})
            if isinstance(pi, dict):
                candidates.extend(pi.get('CFBundleIconFiles', []))
    candidates.extend(plist.get('CFBundleIconFiles', []))

    # Try plist-declared icons (largest first = @3x > @2x > bare)
    for base in reversed(candidates):
        for sfx in ['@3x', '@2x', '']:
            for ext in ['.png', '.PNG', '']:
                p = f'{app_dir}/{base}{sfx}{ext}'
                if p in names_set:
                    try:
                        data = zf.read(p)
                        if _is_valid_png(data): return data
                    except Exception: pass

    # 2) Broad search: any file with 'AppIcon' or 'Icon' in name under app_dir
    # Sort by size descending (larger files = higher resolution)
    icon_candidates = [
        n for n in names
        if n.startswith(app_dir + '/')
        and n.lower().endswith('.png')
        and any(k in n for k in ('AppIcon', 'appicon', 'Icon60', 'Icon76', 'Icon120', 'Icon152', 'Icon167', 'Icon180'))
    ]
    # Prefer higher resolution: sort by numeric value in filename desc
    def _icon_res(path):
        nums = re.findall(r'(\d+)', path.split('/')[-1])
        return max((int(x) for x in nums), default=0)
    for n in sorted(icon_candidates, key=_icon_res, reverse=True):
        try:
            data = zf.read(n)
            if _is_valid_png(data): return data
        except Exception: pass

    # 3) Any PNG directly inside .app folder (not in subdirs)
    for n in names:
        if n.startswith(app_dir + '/') and n.lower().endswith('.png'):
            parts = n[len(app_dir)+1:].split('/')
            if len(parts) == 1:
                try:
                    data = zf.read(n)
                    if _is_valid_png(data): return data
                except Exception: pass

    # 4) Extract largest PNG from Assets.car (scan for PNG magic bytes)
    car_path = f'{app_dir}/Assets.car'
    if car_path in names_set:
        try:
            car_data = zf.read(car_path)
            # Find all PNG signatures in the binary blob
            PNG_SIG = b'\x89PNG\r\n\x1a\n'
            IEND    = b'IEND\xaeB`\x82'
            best = b''
            pos = 0
            while True:
                start = car_data.find(PNG_SIG, pos)
                if start == -1: break
                end = car_data.find(IEND, start)
                if end == -1: break
                chunk = car_data[start:end + 8]
                if len(chunk) > len(best):
                    best = chunk
                pos = end + 8
            if best and _is_valid_png(best):
                return best
        except Exception: pass

    return None


def _is_valid_png(data):
    return len(data) > 8 and data[:8] == b'\x89PNG\r\n\x1a\n'


def _parse_provision(data):
    m = re.search(b'<plist[^>]*>.*?</plist>', data, re.DOTALL)
    if not m: return None
    try:
        pl = plistlib.loads(m.group())
    except Exception as e:
        return {'error': str(e)}
    expiry = pl.get('ExpirationDate')
    creation = pl.get('CreationDate')
    now = datetime.now(timezone.utc)
    days_left = is_expired = None
    if isinstance(expiry, datetime):
        if not expiry.tzinfo: expiry = expiry.replace(tzinfo=timezone.utc)
        d = expiry - now
        days_left  = d.days
        is_expired = d.total_seconds() < 0
    devices = pl.get('ProvisionedDevices', [])
    ent = pl.get('Entitlements', {})
    if pl.get('ProvisionsAllDevices'):       ptype = 'Enterprise (In-House)'
    elif devices:                             ptype = 'Ad Hoc'
    elif isinstance(ent, dict) and ent.get('get-task-allow'): ptype = 'Development'
    else:                                     ptype = 'App Store'
    tid = pl.get('TeamIdentifier', '')
    if isinstance(tid, list): tid = tid[0] if tid else ''
    return {
        'name':         pl.get('Name', ''),
        'uuid':         pl.get('UUID', ''),
        'team_name':    pl.get('TeamName', ''),
        'team_id':      tid,
        'app_id_name':  pl.get('AppIDName', ''),
        'expiry':       _fmt(expiry),
        'creation':     _fmt(creation),
        'days_left':    days_left,
        'is_expired':   is_expired,
        'profile_type': ptype,
        'device_count': len(devices),
        'devices':      devices[:20],
        'total_devices': len(devices),
        'entitlements': list(ent.keys()) if isinstance(ent, dict) else [],
        'platforms':    pl.get('Platform', []),
    }


def _parse_certificates(data):
    m = re.search(b'<plist[^>]*>.*?</plist>', data, re.DOTALL)
    if not m: return []
    try: pl = plistlib.loads(m.group())
    except Exception: return []
    out = []
    for cb in pl.get('DeveloperCertificates', []):
        try:
            from cryptography import x509
            from cryptography.hazmat.backends import default_backend
            if isinstance(cb, memoryview): cb = bytes(cb)
            cert = x509.load_der_x509_certificate(cb, default_backend())
            def ga(oid):
                try: return cert.subject.get_attributes_for_oid(oid)[0].value
                except: return ''
            N = x509.oid.NameOID
            def utc(dt):
                return dt.replace(tzinfo=timezone.utc) if not dt.tzinfo else dt
            try:
                na = utc(cert.not_valid_after_utc)
                nb = utc(cert.not_valid_before_utc)
            except AttributeError:
                na = utc(cert.not_valid_after)
                nb = utc(cert.not_valid_before)
            d = na - datetime.now(timezone.utc)
            out.append({
                'common_name': ga(N.COMMON_NAME),
                'org':         ga(N.ORGANIZATION_NAME),
                'country':     ga(N.COUNTRY_NAME),
                'serial':      format(cert.serial_number, 'x').upper(),
                'not_before':  nb.strftime('%d/%m/%Y'),
                'not_after':   na.strftime('%d/%m/%Y'),
                'days_left':   d.days,
                'is_expired':  d.total_seconds() < 0,
                'fingerprint': cert.fingerprint(
                    __import__('cryptography').hazmat.primitives.hashes.SHA256()
                ).hex().upper(),
            })
        except Exception as e:
            out.append({'error': str(e), 'common_name': 'Parse error'})
    return out


def _fmt(dt):
    if not dt: return ''
    if isinstance(dt, datetime):
        if not dt.tzinfo: dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime('%d/%m/%Y %H:%M UTC')
    return str(dt)


def format_size(b):
    for u in ['B','KB','MB','GB']:
        if b < 1024: return f'{b:.1f} {u}'
        b /= 1024
    return f'{b:.1f} GB'


# ─── Run ──────────────────────────────────────────────────────────────────────

# Initialize DB at startup — works for both `python app.py` and Gunicorn
with app.app_context():
    init_db()

if __name__ == '__main__':
    print('🚀  IPA Scanner → http://0.0.0.0:5000')
    app.run(debug=False, host='0.0.0.0', port=5000)
