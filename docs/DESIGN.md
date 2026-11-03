# 设计方案 / 维护手册

## 一、目标与非目标

**目标**：证明 Istio/Envoy 已将 OTLP/gRPC 数据真正发出并被成功解析；通过 UI 查看请求追踪、日志、程序运行情况；所有文件在一个可被 VSCode 打开的仓库中；Docker/K8s 可重复部署，默认不碰生产 Service。

**非目标**：替代完整 Alloy/OTel Collector 做生产高吞吐接入、指标存储、协议代理、消息缓冲、转发、TLS/mTLS、HA、告警或权限系统。

## 二、服务设计

- `app.main` 启动 gRPC 监听线程池 + Uvicorn Web 服务，生命周期由主进程管理。
- `TraceService.Export` 将 OTLP `resource_spans → scope_spans → spans` 展平，每个 Span 记录一行；`LogService.Export` 将 `resource_logs → scope_logs → log_records` 展平。
- 元数据同时保留：外部常用字段（Trace ID、服务、耗时、状态）、protobuf 原始结构 JSON（`attrs`）、Resource JSON（`resource_attrs`）。protobuf `AnyValue` 经官方 `MessageToDict` 转换，兼容对象/数组/bytes 等结构。
- SQLite 开启 WAL，线程锁序列化写入，按接收时间保留；写失败返回 `UNAVAILABLE`，避免虚假成功。
- REST API 只读；搜索是参数化 LIKE。普通日志可含敏感信息，默认不暴露公网。
- Web 使用原生 HTML/CSS/JS，无 npm/build；所有业务文本通过 `textContent` 显示，不直接拼 HTML，降低 XSS 风险。

## 三、Signal 映射

| Signal | gRPC Service | 保存 | UI |
|---|---|---|---|
| Traces | `opentelemetry.proto.collector.trace.v1.TraceService/Export` | spans | 列表、瀑布图、原始 JSON |
| Logs | `opentelemetry.proto.collector.logs.v1.LogsService/Export` | logs | 日志列表、Trace 关联 |
| Metrics | 未实现 | 不保存 | 不展示 |
| HTTP OTLP | 未实现 | 不保存 | 不展示 |

**准确性注意**：
- `status` 来自 OpenTelemetry SpanStatus，不等于 HTTP 状态码；HTTP 状态码可能在 span.attributes 的 `http.response.status_code` 或 `http.status_code`。
- `trace_id` 跨服务聚合需要客户端正确传播上下文；Envoy Span 可能只有部分链路。
- 资源 service.name 若不存在，显示 `unknown`。
- `received_ns` 是 Collector 收到数据的时间，与 Span `start_ns/end_ns` 不同。

## 四、关键数据表和索引

- `spans(id, received_ns, trace_id, span_id, parent_span_id, service, name, start_ns, end_ns, duration_ms, status, attrs, resource_attrs)`；索引 `trace_id`, `received_ns`, `service`。
- `logs(id, received_ns, event_ns, service, severity, body, trace_id, span_id, attrs, resource_attrs)`；相同维度索引。
- SQLite 文件放在 `/data/otlp.sqlite3`；WAL/SHM 与数据库同目录。**不支持** SQLite 多写副本或多节点共享 NFS。
- 定时 DELETE 不自动收缩数据库文件；维护窗口可在停机备份后 VACUUM，避免在线高负载执行。

## 五、Istio 和目标 Collector 的关系

只添加 `extensionProviders.opentelemetry` 不会自动启用 tracing；需 `defaultProviders.tracing` 或 `Telemetry.tracing.providers` 引用。Envoy 中看到 `OpenTelemetryConfig` 的 `grpcService.envoyGrpc.clusterName` 是必要但非充分条件。真正生效以 Receiver Export 收到记录、UI/DB 可见为准。

如需从同一 Envoy 收集 Access Logs：

```yaml
# 追加到 MeshConfig 顶层的 extensionProviders 数组（保留现有配置）：
- name: mdis-otel-debug-logs
  envoyOtelAls:
    service: otlp-debug-receiver.otlp-debug.svc.cluster.local
    port: 4317
```

```yaml
# 测试工作负载 Telemetry 示例（与 tracing 可写进同一 Telemetry）：
apiVersion: telemetry.istio.io/v1
kind: Telemetry
metadata:
  name: otlp-debug-logs
  namespace: default
spec:
  selector:
    matchLabels:
      otlp-debug-tracing: "true"
  accessLogging:
    - providers:
        - name: mdis-otel-debug-logs
```

**警告**：此片段不能与已有同级工作负载 Telemetry 随便叠加；先检查 `kubectl get telemetry -A -o yaml`。不同 Istio 版本行为需按版本验证。发送端不一定提供 HTTP 请求体 / 响应体，避免将此项目误解为抓包工具。

## 六、安全边界

- 4317 明文 gRPC，没有客户端身份认证；仅接入可信内网。必要时用 Kubernetes NetworkPolicy 限定仅测试命名空间到 4317 的流量；当前包不提供通用 NetworkPolicy，因为不同 CNI 和业务 namespace 规则差异大。
- 8080 若开启 `UI_TOKEN`，只对 `/api/*` 保护（首页静态页面与 `/healthz` 无鉴权）。令牌通过浏览器 `sessionStorage` 保存，非单点登录；**使用本地 kubectl port-forward，不要公开 Ingress**。
- Logging Span 属性可能包含路径/标识/密钥，严禁无审查导出到公网或共享。
- 建议测试环境 100% Trace 采样，生产环境控制测试范围与采样率。
- 进程以普通用户 UID 10001 启动，非 root、只读根文件系统，依赖 `/data`、`/tmp` 可写卷。

## 七、运维与扩展路线

1. **阶段一（当前）**：Trace、Log、SQLite、趋势图、瀑布图、程序日志；HTTP healthz；镜像 + K8s 单副本。
2. **阶段二**：增加 OTLP MetricsService 与指标样本查询；增加 HTTP/4318；可选 gRPC Health Checking；更多字段级搜索与分页。
3. **阶段三**：可选反向转发至原 Collector（tee/relay）、TLS/mTLS、RBAC/Auth、Prometheus 自监控、限速、容量配额、可配置字段脱敏。
4. **阶段四（需要生产化时）**：改用正统 OTel Collector 接入层 + 专门可观测存储后端，放弃 SQLite 单机设计。

### 简单性能预算

默认 1 CPU、512Mi 内存、单 SQLite、8MiB 请求体；适合调试和低吞吐的临时数据验证。未经过压力测试，**不声明可靠 TPS/吞吐数字**。若服务满载，请降低测试采样率、清理/缩短保留时间、停用大批量发送。
