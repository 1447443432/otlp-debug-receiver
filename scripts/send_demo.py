#!/usr/bin/env python3
"""发送 2 条测试 Span + 1 条 OTLP 日志，无需 grpcurl，依赖项目 requirements。"""
import argparse
import time
import grpc
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc


def main():
    p = argparse.ArgumentParser(description='OTLP/gRPC demo sender')
    p.add_argument('--endpoint', default='127.0.0.1:4317', help='host:port')
    args = p.parse_args()
    with grpc.insecure_channel(args.endpoint) as channel:
        t = trace_service_pb2_grpc.TraceServiceStub(channel)
        l = logs_service_pb2_grpc.LogsServiceStub(channel)
        now = time.time_ns()
        trace_id = bytes.fromhex('0123456789abcdef0123456789abcdef')
        req = trace_service_pb2.ExportTraceServiceRequest()
        rs = req.resource_spans.add()
        a = rs.resource.attributes.add()
        a.key = 'service.name'
        a.value.string_value = 'demo-istio-service'
        scope = rs.scope_spans.add()
        for i, (name, duration) in enumerate([('GET /api/demo', 75), ('outbound HTTP /backend', 25)], 1):
            span = scope.spans.add()
            span.name = name
            span.trace_id = trace_id
            span.span_id = i.to_bytes(8, 'big')
            if i == 2:
                span.parent_span_id = (1).to_bytes(8, 'big')
            span.start_time_unix_nano = now + (i - 1) * 20_000_000
            span.end_time_unix_nano = span.start_time_unix_nano + duration * 1_000_000
            a = span.attributes.add()
            a.key = 'http.response.status_code'
            a.value.int_value = 200
        t.Export(req, timeout=5)
        lr = logs_service_pb2.ExportLogsServiceRequest()
        resource = lr.resource_logs.add()
        a = resource.resource.attributes.add()
        a.key = 'service.name'
        a.value.string_value = 'demo-istio-service'
        record = resource.scope_logs.add().log_records.add()
        record.time_unix_nano = now
        record.trace_id = trace_id
        record.span_id = (1).to_bytes(8, 'big')
        record.severity_text = 'INFO'
        record.body.string_value = '测试日志: OTLP/gRPC 已成功接收'
        l.Export(lr, timeout=5)
        print('OK: sent 2 spans + 1 log to', args.endpoint)
        print('trace_id:', trace_id.hex())

if __name__ == '__main__':
    main()
