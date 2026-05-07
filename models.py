from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import json

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id                  = db.Column(db.Integer,  primary_key=True)
    username            = db.Column(db.String(80),  unique=True, nullable=False)
    email               = db.Column(db.String(120), unique=True, nullable=True)
    password_hash       = db.Column(db.String(256), nullable=False)
    role                = db.Column(db.String(20),  default='user')
    is_active_user      = db.Column(db.Boolean,     default=True)
    google_id           = db.Column(db.String(100), unique=True, nullable=True)
    # ── Plan / quota ──────────────────────────────────────────────────────────
    plan                = db.Column(db.String(20),  default='free')
    plan_expires_at     = db.Column(db.DateTime,    nullable=True)
    storage_used        = db.Column(db.Integer,     default=0)        # bytes
    daily_uploads_count = db.Column(db.Integer,     default=0)
    daily_uploads_date  = db.Column(db.Date,        nullable=True)
    # ─────────────────────────────────────────────────────────────────────────
    created_at          = db.Column(db.DateTime,    default=datetime.utcnow)
    last_login          = db.Column(db.DateTime,    nullable=True)
    scans               = db.relationship('Scan', backref='user', lazy='dynamic',
                                          foreign_keys='Scan.user_id')

    @property
    def is_admin(self):
        return self.role == 'admin'

    def plan_is_active(self):
        if self.plan != 'premium':
            return False
        return (self.plan_expires_at is None) or (datetime.utcnow() < self.plan_expires_at)

    def effective_plan(self):
        return 'premium' if self.plan_is_active() else 'free'

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    def to_dict(self):
        eff = self.effective_plan()
        return {
            'id':                   self.id,
            'username':             self.username,
            'email':                self.email or '',
            'role':                 self.role,
            'is_active':            self.is_active_user,
            'plan':                 eff,
            'plan_expires_at':      self.plan_expires_at.strftime('%d/%m/%Y') if self.plan_expires_at else None,
            'storage_used':         self.storage_used or 0,
            'daily_uploads_count':  self.daily_uploads_count or 0,
            'created_at':           self.created_at.strftime('%d/%m/%Y %H:%M'),
            'last_login':           self.last_login.strftime('%d/%m/%Y %H:%M') if self.last_login else '—',
            'scan_count':           self.scans.count(),
        }


class Scan(db.Model):
    __tablename__ = 'scans'

    id             = db.Column(db.Integer,  primary_key=True)
    file_uuid      = db.Column(db.String(36),  unique=True, nullable=True)
    short_code     = db.Column(db.String(12),  unique=True, nullable=True)
    ipa_stored     = db.Column(db.Boolean,  default=False)
    download_count = db.Column(db.Integer,  default=0)
    user_id        = db.Column(db.Integer,  db.ForeignKey('users.id'), nullable=True)
    filename       = db.Column(db.String(255), nullable=False)
    file_size      = db.Column(db.String(20))
    file_size_bytes= db.Column(db.Integer,  nullable=True)
    app_name       = db.Column(db.String(255))
    bundle_id      = db.Column(db.String(255))
    version        = db.Column(db.String(50))
    build          = db.Column(db.String(50))
    min_os         = db.Column(db.String(20))
    profile_type   = db.Column(db.String(50))
    expiry_date    = db.Column(db.String(50))
    days_left      = db.Column(db.Integer,  nullable=True)
    team_name      = db.Column(db.String(255))
    cert_count     = db.Column(db.Integer,  default=0)
    icon_base64    = db.Column(db.Text,     nullable=True)
    result_json    = db.Column(db.Text)
    ip_address     = db.Column(db.String(50))
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self, include_result=False):
        d = {
            'id':           self.id,
            'filename':     self.filename,
            'file_size':    self.file_size or '—',
            'app_name':     self.app_name  or '—',
            'bundle_id':    self.bundle_id or '—',
            'version':      self.version   or '—',
            'build':        self.build     or '—',
            'min_os':       self.min_os    or '—',
            'profile_type': self.profile_type or '—',
            'expiry_date':  self.expiry_date  or '—',
            'days_left':    self.days_left,
            'team_name':    self.team_name or '—',
            'cert_count':   self.cert_count,
            'icon_base64':  self.icon_base64,
            'ip_address':   self.ip_address or '—',
            'download_count': self.download_count or 0,
            'created_at':   self.created_at.strftime('%d/%m/%Y %H:%M'),
            'user':         self.user.username if self.user else 'Ẩn danh',
        }
        d['file_uuid']  = self.file_uuid
        d['short_code'] = self.short_code
        d['ipa_stored'] = self.ipa_stored
        if include_result:
            try:
                d['result'] = json.loads(self.result_json) if self.result_json else {}
            except Exception:
                d['result'] = {}
        return d


class Setting(db.Model):
    __tablename__ = 'settings'

    id    = db.Column(db.Integer, primary_key=True)
    key   = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, default='')

    @staticmethod
    def get(key, default=''):
        s = Setting.query.filter_by(key=key).first()
        return s.value if s else default

    @staticmethod
    def set(key, value):
        s = Setting.query.filter_by(key=key).first()
        if s:
            s.value = str(value)
        else:
            db.session.add(Setting(key=key, value=str(value)))
        db.session.commit()


class Payment(db.Model):
    __tablename__ = 'payments'

    id          = db.Column(db.Integer,  primary_key=True)
    user_id     = db.Column(db.Integer,  db.ForeignKey('users.id'), nullable=False)
    order_code  = db.Column(db.String(50), unique=True, nullable=False)
    amount      = db.Column(db.Integer,  nullable=False)
    status      = db.Column(db.String(20), default='pending')   # pending / paid / cancelled
    plan_months = db.Column(db.Integer,  default=1)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    paid_at     = db.Column(db.DateTime, nullable=True)
    user        = db.relationship('User', backref=db.backref('payments', lazy='dynamic'))

    def to_dict(self):
        return {
            'id':          self.id,
            'order_code':  self.order_code,
            'amount':      self.amount,
            'amount_fmt':  f'{self.amount:,}đ'.replace(',', '.'),
            'status':      self.status,
            'plan_months': self.plan_months,
            'created_at':  self.created_at.strftime('%d/%m/%Y %H:%M'),
            'paid_at':     self.paid_at.strftime('%d/%m/%Y %H:%M') if self.paid_at else None,
        }
