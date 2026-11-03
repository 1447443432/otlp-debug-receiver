"""同一进程启动 OTLP/gRPC 与 HTTP UI。"""
import logging
import signal
import threading
import time
import uvicorn
from app import config, web
from app.storage import Store
from app.receiver import Counters, start_grpc


def cleanup_loop(store, stop):
    while not stop.wait(config.CLEANUP_SECONDS):
        try:
            store.cleanup(config.RETENTION_HOURS)
        except Exception:
            logging.exception('数据保留清理失败')


def main():
    logging.basicConfig(level=getattr(logging, config.LOG_LEVEL, logging.INFO), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    handler = web.MemoryLogHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    logging.getLogger().addHandler(handler)
    store = Store()
    counters = Counters()
    web.STORE = store
    web.COUNTERS = counters
    store.cleanup(config.RETENTION_HOURS)
    stop = threading.Event()
    worker = threading.Thread(target=cleanup_loop, args=(store, stop), daemon=True)
    worker.start()
    grpc_server = start_grpc(store, counters)
    logging.info('Web UI 监听 %s:%s，保留 %s 小时，数据库 %s', config.WEB_HOST, config.WEB_PORT, config.RETENTION_HOURS, config.DB_PATH)
    try:
        uvicorn.run(web.app, host=config.WEB_HOST, port=config.WEB_PORT, log_level=config.LOG_LEVEL.lower())
    finally:
        stop.set()
        grpc_server.stop(grace=5).wait(timeout=6)
        store.close()

if __name__ == '__main__':
    main()
