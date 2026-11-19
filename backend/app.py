"""Simple local start for the Kiswa-Zaibus API.

    python app.py                      -> http://0.0.0.0:5000  (this PC and the LAN)
    python app.py --host 127.0.0.1     -> this PC only
    python app.py --port 5001          -> another port

Runs with the project's .venv automatically, so plain `python` works without activating it.
Uses Flask's built-in server: fine for local and LAN testing. On the production server use Gunicorn (deploy/oci).
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PY = HERE / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

# Started with the system Python? Re-run this same command with the project's .venv Python.
if VENV_PY.exists() and Path(sys.prefix).resolve() != (HERE / '.venv').resolve():
    sys.exit(subprocess.call([str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=str(HERE)))

import argparse  # noqa: E402
import socket  # noqa: E402

os.chdir(HERE)
from wsgi import app  # noqa: E402


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))   # no traffic is sent; only picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return None


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Run the Kiswa-Zaibus API')
    p.add_argument('--host', default='0.0.0.0', help='0.0.0.0 = this PC and the LAN (default), 127.0.0.1 = this PC only')
    p.add_argument('--port', type=int, default=5000)
    p.add_argument('--debug', action='store_true', help='auto-reload when code changes (development only)')
    a = p.parse_args()
    print(f'Kiswa-Zaibus API  (Ctrl+C to stop)')
    print(f'  This PC : http://127.0.0.1:{a.port}')
    if a.host == '0.0.0.0' and lan_ip():
        print(f'  LAN     : http://{lan_ip()}:{a.port}')
    app.run(host=a.host, port=a.port, debug=a.debug, threaded=True)
