"""Kiswa-Zaibus Operations API (Flask 3, SQLAlchemy 2, MySQL 8).

create_app() wires configuration, the database session, every blueprint in kz/api, error handling and the
`flask kz ...` maintenance commands (kz/cli). Run locally with:  flask --app wsgi run --debug
"""
import logging
from decimal import InvalidOperation
from logging.handlers import RotatingFileHandler

from flask import Flask, g, jsonify, request
from sqlalchemy.exc import DataError as SADataError
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db
from .config import Config
from .errors import ApiError

__version__ = '2.8.0'
MULTIPART_PATHS = ('/api/evidence', '/api/erp/import')       # file uploads; everything else must be JSON
MULTIPART_SUFFIXES = ('/import',)                           # e.g. /api/stock-audits/<id>/import (count sheets)


def create_app(config=Config):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = config.MAX_UPLOAD_MB * 1024 * 1024
    app.config['JSON_SORT_KEYS'] = False
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)   # behind Nginx / OCI Load Balancer
    db.init_engine(config.db_url(), config.DB_POOL_SIZE, config.connect_args())
    _logging(app, config)

    from .api import ALL_BLUEPRINTS
    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp)
    from .cli import register as register_cli
    register_cli(app)
    _sync_permission_catalog(app)

    @app.before_request
    def json_only():
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and request.content_length and not request.is_json:
            if not ((request.path.startswith(MULTIPART_PATHS) or request.path.endswith(MULTIPART_SUFFIXES)) and request.mimetype == 'multipart/form-data') and not request.path.startswith('/webhooks/'):
                raise ApiError(415, 'json_required', 'Send JSON (Content-Type: application/json).')

    @app.after_request
    def headers(resp):
        if request.path.startswith('/api/'):
            resp.headers['Cache-Control'] = 'no-store'
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        g.response_ok = resp.status_code < 400
        return resp

    @app.teardown_request
    def end_tx(exc):
        db.finish(exc is None and g.get('response_ok', True))

    @app.errorhandler(ApiError)
    def api_error(e):
        g.response_ok = False
        return jsonify(e.body()), e.status

    @app.errorhandler(HTTPException)
    def http_error(e):
        g.response_ok = False
        return jsonify({'error': e.name.lower().replace(' ', '_'), 'message': e.description}), e.code

    @app.errorhandler(SADataError)
    def data_error(e):
        # a value the database cannot hold (an amount of 10^30, text longer than its column): the user's input, not a crash
        g.response_ok = False
        app.logger.warning('Value refused by the database on %s %s: %s', request.method, request.path, str(e.orig)[:200])
        return jsonify({'error': 'value_out_of_range', 'message': 'A value is too large or too long. Check amounts, quantities and text lengths.'}), 400

    @app.errorhandler(Exception)
    def crash(e):
        if isinstance(e, (TypeError, AttributeError, KeyError, ValueError, InvalidOperation, OverflowError)) and _input_error(e):
            # a field of the wrong type or a missing field reached code that expected it: answer 400, keep the trace in the log
            g.response_ok = False
            app.logger.warning('Malformed input on %s %s', request.method, request.path, exc_info=e)
            return jsonify({'error': 'invalid_input', 'message': 'Some fields are missing or have the wrong type. Check the form and try again.'}), 400
        g.response_ok = False
        app.logger.exception('Unhandled error on %s %s', request.method, request.path)
        return jsonify({'error': 'server_error', 'message': 'Something went wrong. The error has been logged (logs/api.log).'}), 500

    return app


def _input_error(e):
    """True when the failing frame is in the request-handling layers (api / services / core) and the request carried data:
    a type or missing-field error there comes from the input. Errors in the database layer or elsewhere stay 500."""
    import traceback
    if not (request.get_data(cache=True) or request.args or request.view_args):
        return False
    tb = traceback.extract_tb(e.__traceback__)
    ours = [f for f in tb if '/kz/' in f.filename.replace('\\', '/')]
    return bool(ours) and any(x in ours[-1].filename.replace('\\', '/') for x in ('/kz/api/', '/kz/services/', '/kz/core/'))


def _sync_permission_catalog(app):
    """Bring perm_modules / perm_screens in line with kz/core/permission_catalog.py, so a screen added in code shows up on the
    Role Permissions screen after a restart. Never stops the app: before migration 0007, or with the database down, it only logs."""
    from .core.permission_catalog import sync_catalog
    try:
        with db.engine().begin() as conn:
            sync_catalog(conn)
    except Exception as e:  # noqa: BLE001
        app.logger.warning('Permission catalogue not synced at start-up (%s). Run migrations, then restart.', type(e).__name__)


def _logging(app, config):
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(config.LOG_DIR / 'api.log', maxBytes=5_000_000, backupCount=10, encoding='utf-8')
        h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        h.setLevel(logging.INFO)
        app.logger.addHandler(h)
        app.logger.setLevel(logging.INFO)
    except OSError:
        pass
