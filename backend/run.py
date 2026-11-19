"""Production entry point: python run.py  (the KZ-API Windows service runs exactly this)."""
from waitress import serve
from kz import create_app
from kz.config import Config

app = create_app()

if __name__ == '__main__':
    print(f'Kiswa-Zaibus API listening on http://{Config.API_HOST}:{Config.API_PORT}')
    serve(app, host=Config.API_HOST, port=Config.API_PORT, threads=Config.API_THREADS, ident='kz-api')
