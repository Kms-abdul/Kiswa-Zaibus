# Gunicorn settings for OCI (systemd unit deploy/oci/kz-api.service runs: gunicorn -c gunicorn.conf.py wsgi:app)
import multiprocessing
import os

bind = os.getenv('GUNICORN_BIND', '127.0.0.1:5000')
workers = int(os.getenv('GUNICORN_WORKERS', min(4, multiprocessing.cpu_count() * 2 + 1)))
threads = int(os.getenv('GUNICORN_THREADS', '4'))
worker_class = 'gthread'
timeout = 60
graceful_timeout = 30
max_requests = 2000
max_requests_jitter = 200
accesslog = os.getenv('GUNICORN_ACCESS_LOG', '-')
errorlog = os.getenv('GUNICORN_ERROR_LOG', '-')
forwarded_allow_ips = '127.0.0.1'
