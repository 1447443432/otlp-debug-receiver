"""只读 Web API；UI_TOKEN 可选但强烈建议生产/共享环境启用。"""
import logging
import time
from collections import deque
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from app import config
from app.storage import parse_json_fields

app = FastAPI(title='OTLP Debug Receiver', docs_url=None, redoc_url=None)
STORE = None
COUNTERS = None
EVENTS = deque(maxlen=500)

class MemoryLogHandler(logging.Handler):
    def emit(self, record):
        EVENTS.append({'time': record.created, 'level': record.levelname, 'source': record.name, 'message': self.format(record)})

@app.get('/healthz')
def healthz():
    if STORE is None:
        raise HTTPException(503, 'initializing')
    try:
        STORE.query('SELECT 1 AS ok')
    except Exception:
        raise HTTPException(503, 'database unavailable')
    return {'ok': True}

def require_auth(authorization: str | None = Header(default=None)):
    if config.UI_TOKEN:
        import hmac
        expected = 'Bearer ' + config.UI_TOKEN
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, 'UI token required', headers={'WWW-Authenticate': 'Bearer'})

from fastapi import Depends

def with_filter(table, service, search):
    where, values = [], []
    if service:
        where.append('service = ?')
        values.append(service)
    if search:
        # 文本模糊匹配仅用于调试，小数据规模；不向 SQL 拼入用户输入。
        if table == 'spans':
            where.append('(trace_id LIKE ? OR name LIKE ? OR attrs LIKE ?)')
        else:
            where.append('(trace_id LIKE ? OR body LIKE ? OR attrs LIKE ?)')
        values.extend([f'%{search}%'] * 3)
    return (' WHERE ' + ' AND '.join(where)) if where else '', values

@app.get('/api/stats', dependencies=[Depends(require_auth)])
def stats():
    return {
        'counters': COUNTERS.snapshot(),
        'stored': {t: STORE.query(f'SELECT count(*) AS n FROM {t}')[0]['n'] for t in ('spans', 'logs')},
        'services': STORE.query('SELECT service, count(*) AS n FROM spans GROUP BY service ORDER BY n DESC LIMIT 20'),
        'recent_minutes': STORE.query('SELECT (received_ns/60000000000)*60000000000 AS bucket, count(*) AS n FROM spans WHERE received_ns > ? GROUP BY bucket ORDER BY bucket', (time.time_ns() - 60*60*1_000_000_000,)),
    }

@app.get('/api/spans', dependencies=[Depends(require_auth)])
def spans(service: str = '', q: str = '', limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    where, params = with_filter('spans', service, q)
    return parse_json_fields(STORE.query('SELECT * FROM spans' + where + ' ORDER BY received_ns DESC,id DESC LIMIT ? OFFSET ?', (*params, limit, offset)))

@app.get('/api/traces/{trace_id}', dependencies=[Depends(require_auth)])
def trace(trace_id: str):
    if len(trace_id) != 32 or any(c not in '0123456789abcdefABCDEF' for c in trace_id):
        raise HTTPException(400, 'invalid trace_id')
    return {
        'spans': parse_json_fields(STORE.query('SELECT * FROM spans WHERE trace_id=? ORDER BY start_ns, id LIMIT 3000', (trace_id.lower(),))),
        'logs': parse_json_fields(STORE.query('SELECT * FROM logs WHERE trace_id=? ORDER BY event_ns, id LIMIT 500', (trace_id.lower(),))),
    }

@app.get('/api/logs', dependencies=[Depends(require_auth)])
def otlp_logs(service: str = '', q: str = '', limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    where, params = with_filter('logs', service, q)
    return parse_json_fields(STORE.query('SELECT * FROM logs' + where + ' ORDER BY received_ns DESC,id DESC LIMIT ? OFFSET ?', (*params, limit, offset)))

@app.get('/api/runtime-logs', dependencies=[Depends(require_auth)])
def runtime_logs(limit: int = Query(150, ge=1, le=500)):
    return list(EVENTS)[-limit:][::-1]

@app.get('/', include_in_schema=False)
def home():
    return FileResponse(Path(__file__).parent / 'static' / 'index.html', headers={'Cache-Control': 'no-store'})
