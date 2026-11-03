import os
from pathlib import Path

GRPC_HOST = os.getenv('GRPC_HOST', '0.0.0.0')
GRPC_PORT = int(os.getenv('GRPC_PORT', '4317'))
WEB_HOST = os.getenv('WEB_HOST', '0.0.0.0')
WEB_PORT = int(os.getenv('WEB_PORT', '8080'))
DB_PATH = Path(os.getenv('DB_PATH', '/data/otlp.sqlite3'))
RETENTION_HOURS = max(1, int(os.getenv('RETENTION_HOURS', '24')))
CLEANUP_SECONDS = max(60, int(os.getenv('CLEANUP_SECONDS', '300')))
UI_TOKEN = os.getenv('UI_TOKEN', '')
MAX_MESSAGE_MB = max(1, int(os.getenv('MAX_MESSAGE_MB', '8')))
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
