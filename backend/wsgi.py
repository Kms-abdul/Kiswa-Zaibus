"""Entry point for Gunicorn (deploy/oci/kz-api.service) and `flask --app wsgi ...`."""
from kz import create_app

app = create_app()
