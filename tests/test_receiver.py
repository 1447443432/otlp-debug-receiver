import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from app.storage import Store
from app.receiver import TraceService, LogService, Counters
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2


class TestReceiver(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')
        self.counters = Counters()
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_trace(self):
        req = trace_service_pb2.ExportTraceServiceRequest()
        rs = req.resource_spans.add()
        a = rs.resource.attributes.add()
        a.key = 'service.name'
        a.value.string_value = 'service-a'
        span = rs.scope_spans.add().spans.add()
        span.trace_id = bytes.fromhex('12' * 16)
        span.span_id = bytes.fromhex('34' * 8)
        span.name = 'GET /test'
        span.start_time_unix_nano = 100
        span.end_time_unix_nano = 2_000_100
        TraceService(self.store, self.counters).Export(req, Mock(peer=lambda: 'unit-test'))
        row = self.store.query('SELECT * FROM spans')[0]
        self.assertEqual(row['service'], 'service-a')
        self.assertEqual(row['duration_ms'], 2)
        self.assertEqual(self.counters.snapshot()['spans'], 1)

    def test_log(self):
        req = logs_service_pb2.ExportLogsServiceRequest()
        log = req.resource_logs.add().scope_logs.add().log_records.add()
        log.body.string_value = 'hello'
        LogService(self.store, self.counters).Export(req, Mock(peer=lambda: 'unit-test'))
        self.assertEqual(self.store.query('SELECT count(*) AS n FROM logs')[0]['n'], 1)

if __name__ == '__main__':
    unittest.main()
