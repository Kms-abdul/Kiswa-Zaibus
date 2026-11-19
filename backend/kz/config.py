"""Configuration from backend/.env (see .env.example). Every setting has a safe default for development."""
import os
import secrets
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / '.env')


def _bool(name, default=False):
    v = os.getenv(name)
    return default if v is None or v == '' else v.strip().lower() in ('1', 'true', 'yes', 'on')


class Config:
    ENV = os.getenv('KZ_ENV', 'production')                     # production | development | test

    DB_HOST = os.getenv('DB_HOST', '127.0.0.1')
    DB_PORT = int(os.getenv('DB_PORT', '3306'))
    DB_NAME = os.getenv('DB_NAME', 'kiswa_zaibus')
    DB_USER = os.getenv('DB_USER', 'kz_app')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '')
    DB_SSL_CA = os.getenv('DB_SSL_CA', '')                      # OCI MySQL HeatWave: path to the CA bundle when TLS is required
    DB_POOL_SIZE = int(os.getenv('DB_POOL_SIZE', '10'))

    SECRET_KEY = os.getenv('SECRET_KEY') or secrets.token_hex(32)
    COOKIE_NAME = 'kz_session'
    PARENT_COOKIE_NAME = 'kz_parent'
    COOKIE_SECURE = _bool('COOKIE_SECURE', True)                # false only for plain-http development
    OTP_DEMO = _bool('OTP_DEMO', False)                         # show one-time codes in API responses (test servers only)
    PUBLIC_URL = os.getenv('PUBLIC_URL', 'http://localhost:5173')

    DATA_DIR = Path(os.getenv('DATA_DIR', str(BACKEND_DIR.parent / 'data')))
    EVIDENCE_DIR = Path(os.getenv('EVIDENCE_DIR', str(DATA_DIR / 'evidence')))
    LOG_DIR = Path(os.getenv('LOG_DIR', str(BACKEND_DIR.parent / 'logs')))
    MAX_UPLOAD_MB = int(os.getenv('MAX_UPLOAD_MB', '10'))

    # Razorpay: per-company keys. With no key the store runs in "simulated" payment mode.
    RAZORPAY = {co: {'key_id': os.getenv(f'RAZORPAY_KEY_ID_{co}', ''), 'key_secret': os.getenv(f'RAZORPAY_KEY_SECRET_{co}', ''),
                     'webhook_secret': os.getenv(f'RAZORPAY_WEBHOOK_SECRET_{co}', '')} for co in ('KG', 'ZE')}

    # Node.js document renderer (kz-docs, release 2.3): Excel / PDF exports of finance reports. Empty URL = render in Python (openpyxl / reportlab).
    DOCS_URL = os.getenv('DOCS_URL', 'http://127.0.0.1:8095')
    DOCS_SECRET = os.getenv('DOCS_SECRET', '')
    DOCS_TIMEOUT = float(os.getenv('DOCS_TIMEOUT', '20'))

    # Outgoing messages
    SMTP_HOST = os.getenv('SMTP_HOST', '')
    SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
    SMTP_USER = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    SMTP_FROM = os.getenv('SMTP_FROM', 'Kiswa-Zaibus <no-reply@kiswazaibus.in>')
    SMTP_TLS = _bool('SMTP_TLS', True)
    SMS_API_URL = os.getenv('SMS_API_URL', '')
    SMS_API_KEY = os.getenv('SMS_API_KEY', '')
    SMS_SENDER_ID = os.getenv('SMS_SENDER_ID', 'KZOPS')
    WHATSAPP_API_URL = os.getenv('WHATSAPP_API_URL', '')
    WHATSAPP_API_TOKEN = os.getenv('WHATSAPP_API_TOKEN', '')

    # MSEA ERP connector (students). Empty URL = file import only.
    ERP_API_URL = os.getenv('ERP_API_URL', '')
    ERP_API_TOKEN = os.getenv('ERP_API_TOKEN', '')
    # MCB Read-Only API (views): the key travels in this header; set only here, never on a screen
    ERP_API_KEY = os.getenv('ERP_API_KEY', '')
    ERP_API_KEY_HEADER = os.getenv('ERP_API_KEY_HEADER', 'x-api-key')

    @classmethod
    def db_url(cls, name=None):
        return (f"mysql+pymysql://{quote_plus(cls.DB_USER)}:{quote_plus(cls.DB_PASSWORD)}@{cls.DB_HOST}:{cls.DB_PORT}/"
                f"{name or cls.DB_NAME}?charset=utf8mb4")

    @classmethod
    def connect_args(cls):
        return {'ssl': {'ca': cls.DB_SSL_CA}} if cls.DB_SSL_CA else {}
