"""Health and version (used by Nginx / OCI load-balancer health checks and the PWA)."""
import datetime as dt

from flask import Blueprint, jsonify

from .. import __version__, db

bp = Blueprint('system', __name__)


@bp.get('/api/health')
def health():
    try:
        rev = db.val('SELECT version_num FROM alembic_version')
        return jsonify({'status': 'ok', 'database': 'ok', 'migration': rev, 'version': __version__,
                        'time': dt.datetime.now().isoformat(timespec='seconds')})
    except Exception as e:  # noqa: BLE001 - health must always answer
        return jsonify({'status': 'degraded', 'database': 'unreachable', 'error': type(e).__name__, 'version': __version__}), 503
