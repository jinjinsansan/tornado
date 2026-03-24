"""Gunicorn configuration for TornadoAI."""

import os

bind = "0.0.0.0:5001"
workers = int(os.getenv("GUNICORN_WORKERS", "1"))
worker_class = "gevent"
worker_connections = int(os.getenv("GUNICORN_WORKER_CONNECTIONS", "1000"))
timeout = 120
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = "info"
proc_name = "tornado-ai"
preload_app = False
