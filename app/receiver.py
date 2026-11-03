"""标准 OTLP/gRPC Traces + Logs 接收服务。"""
import json
import logging
import time
from concurrent import futures

import grpc
from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc
from app import config

log = logging.getLogger('receiver')

def as_dict(value):
    """使用 Protobuf 官方 JSON 映射保留数组、对象、bytes 等类型。"""
    return MessageToDict(value, preserving_proto_field_name=True)

def resource_name(resource):
    for attr in resource.attributes:
        if attr.key == 'service.name' and attr.value.WhichOneof('value') == 'string_value':
            return attr.value.string_value
    return 'unknown'

def bytes_hex(raw):
    return raw.hex() if raw else ''

class TraceService(trace_service_pb2_grpc.TraceServiceServicer):
    def __init__(self, store, counters):
        self.store, self.counters = store, counters

    def Export(self, request, context):
        received = time.time_ns()
        rows = []
        for rs in request.resource_spans:
            service = resource_name(rs.resource)
            resource_attrs = json.dumps(as_dict(rs.resource), ensure_ascii=False)
            for scope in rs.scope_spans:
                for span in scope.spans:
                    rows.append(dict(
                        received_ns=received, trace_id=bytes_hex(span.trace_id),
                        span_id=bytes_hex(span.span_id), parent_span_id=bytes_hex(span.parent_span_id),
                        service=service, name=span.name,
                        start_ns=span.start_time_unix_nano, end_ns=span.end_time_unix_nano,
                        duration_ms=round(max(0, span.end_time_unix_nano - span.start_time_unix_nano) / 1e6, 3),
                        status=span.status.code,
                        attrs=json.dumps(as_dict(span), ensure_ascii=False), resource_attrs=resource_attrs,
                    ))
        try:
            n = self.store.insert('spans', rows)
            self.counters.add('spans', n)
            log.info('OTLP traces Export: spans=%d peer=%s', n, context.peer())
            return trace_service_pb2.ExportTraceServiceResponse()
        except Exception:
            log.exception('保存 Trace 失败，返回 UNAVAILABLE 允许发送端重试')
            context.abort(grpc.StatusCode.UNAVAILABLE, 'storage unavailable')

class LogService(logs_service_pb2_grpc.LogsServiceServicer):
    def __init__(self, store, counters):
        self.store, self.counters = store, counters

    def Export(self, request, context):
        received = time.time_ns()
        rows = []
        for rl in request.resource_logs:
            service = resource_name(rl.resource)
            resource_attrs = json.dumps(as_dict(rl.resource), ensure_ascii=False)
            for scope in rl.scope_logs:
                for rec in scope.log_records:
                    body = as_dict(rec.body)
                    rows.append(dict(
                        received_ns=received,
                        event_ns=rec.time_unix_nano or rec.observed_time_unix_nano,
                        service=service,
                        severity=rec.severity_text or str(rec.severity_number),
                        body=json.dumps(body, ensure_ascii=False),
                        trace_id=bytes_hex(rec.trace_id), span_id=bytes_hex(rec.span_id),
                        attrs=json.dumps(as_dict(rec), ensure_ascii=False),
                        resource_attrs=resource_attrs,
                    ))
        try:
            n = self.store.insert('logs', rows)
            self.counters.add('logs', n)
            log.info('OTLP logs Export: records=%d peer=%s', n, context.peer())
            return logs_service_pb2.ExportLogsServiceResponse()
        except Exception:
            log.exception('保存 Log 失败，返回 UNAVAILABLE 允许发送端重试')
            context.abort(grpc.StatusCode.UNAVAILABLE, 'storage unavailable')

class Counters:
    def __init__(self):
        from threading import Lock
        self.lock = Lock()
        self.values = {'spans': 0, 'logs': 0}
        self.started = time.time()
    def add(self, name, amount):
        with self.lock:
            self.values[name] += amount
    def snapshot(self):
        with self.lock:
            return {**self.values, 'started_at': self.started}

def start_grpc(store, counters):
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=8),
        options=[('grpc.max_receive_message_length', config.MAX_MESSAGE_MB * 1024 * 1024)],
    )
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(TraceService(store, counters), server)
    logs_service_pb2_grpc.add_LogsServiceServicer_to_server(LogService(store, counters), server)
    endpoint = f'{config.GRPC_HOST}:{config.GRPC_PORT}'
    if not server.add_insecure_port(endpoint):
        raise RuntimeError(f'无法监听 gRPC {endpoint}')
    server.start()
    log.info('OTLP/gRPC 接收器启动，监听 %s（Traces + Logs）', endpoint)
    return server
