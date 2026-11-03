# OTLP Debug Receiver（Istio / Envoy 遥测排障工具）

> v0.1.0 · 单副本调试用途 · Python 3.12 · OTLP/gRPC `4317` + Web UI `8080` · 中文维护文档

## 1. 项目定位

这是一个**能直接部署到 Kubernetes 的轻量级 OTLP 接收与查看项目**。用于验证 Istio Envoy → OTLP Collector 的链路、查看 Envoy 实际发送的 Span，以及接收 OTLP Log 数据；不要求安装 Alloy、DataKit、Tempo、Jaeger 或 Grafana。

**重要边界**：
- `4317`：支持标准 **OTLP/gRPC Traces 和 Logs**（`TraceService/Export`、`LogsService/Export`）。
- `8080`：内置中文 Web 调试 UI 和只读 REST API。
- **不支持** OTLP Metrics、OTLP/HTTP (`4318`)、TLS/mTLS、采样/聚合、队列重试、转发、跨 Pod 多副本或生产级高可用。
- 只能收到实际发送过来的数据。**配置了 Istio `opentelemetry` Provider 不代表自动启用 Logs**；Istio Access Logs 若要使用 OTLP，需要单独的 `envoyOtelAls` Provider + `Telemetry.accessLogging`。
- 这个项目的“日志”包含：**OTLP LogRecords** 和 **本程序自身 stdout/容器运行日志**，两类分开展示。

## 2. 目录结构

```text
otlp-debug-receiver/
├── app/
│   ├── config.py               # 环境变量与默认值
│   ├── main.py                 # 启动 gRPC + Web，定期清理历史数据
│   ├── receiver.py             # OTLP Trace / Log gRPC Service
│   ├── storage.py              # SQLite 持久化、索引、保留期
│   ├── web.py                  # FastAPI /healthz /api/* 查询
│   └── static/index.html       # 零构建依赖的单页中文 UI（趋势、列表、瀑布图）
├── k8s/
│   ├── namespace.yaml          # 独立 namespace
│   ├── deployment.yaml         # 单副本 Pod、ClusterIP Service、探针、安全约束
│   ├── pvc-optional.yaml       # 可选 PVC（默认 emptyDir）
│   └── istio-telemetry-example.yaml # 仅测试工作负载的 Telemetry 示例
├── scripts/send_demo.py        # 生成 2 条 Span + 1 条 OTLP 日志用于验收
├── tests/test_receiver.py      # 标准 protobuf 解析与 SQLite 写入单测
├── docs/DESIGN.md              # 架构、数据流、扩展点、风险
├── .env.example                # 配置模板
├── Dockerfile                  # 非 root 容器镜像
├── .dockerignore
├── requirements.txt
└── README.md
```

## 3. 架构和端口

```text
   业务 HTTP 请求
         │
    Istio Envoy Sidecar
         │  OTLP/gRPC (HTTP/2, Protobuf)
         ▼
  otlp-debug-receiver.otlp-debug.svc.cluster.local:4317
         │
  gRPC Receiver (TraceService + LogsService)
         │  解析 Resource / Scope / Span / LogRecord
         ▼
     SQLite (WAL, /data/otlp.sqlite3)
         │
         ▼
  FastAPI :8080 ── 浏览器 Web UI (port-forward)
         │
    /api/stats /api/spans /api/traces/{id}
    /api/logs /api/runtime-logs /healthz

  运行日志同时输出到容器 stdout → kubectl logs
```

**注意**：Envoy OTLP Tracing Provider 可以直接发 Trace；接收 Log 还需要另一个具备 OTLP Access Logging 的发送端；单纯拥有 Trace 不会自动产生 LogRecord。

## 4. 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `GRPC_HOST` | `0.0.0.0` | gRPC 绑定地址 |
| `GRPC_PORT` | `4317` | OTLP/gRPC 监听端口 |
| `WEB_HOST` | `0.0.0.0` | Web 服务绑定地址 |
| `WEB_PORT` | `8080` | Web UI/API 端口 |
| `DB_PATH` | `/data/otlp.sqlite3` | SQLite 数据库文件路径 |
| `RETENTION_HOURS` | `24` | 根据**接收时间**自动清理历史记录，启动时也会执行 |
| `CLEANUP_SECONDS` | `300` | 清理任务间隔，最少 60 秒 |
| `MAX_MESSAGE_MB` | `8` | gRPC 单消息大小上限（MiB） |
| `LOG_LEVEL` | `INFO` | `DEBUG/INFO/WARNING/ERROR` |
| `UI_TOKEN` | 空 | 可选 Web API Bearer Token；默认无鉴权，**强烈建议设置**；不保护 4317 |

**Docker 镜像体积与运行依赖**：基于 `python:3.12-slim`，在构建镜像时通过 pip 安装 requirements。离线构建须预先准备基础镜像及 pip 依赖缓存/内网源。

## 5. 本地运行（先验证代码）

```bash
cd otlp-debug-receiver
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DB_PATH=/tmp/otlp-debug.sqlite3
export UI_TOKEN='change-me-long-random-token' # 可选，建议开启
python3 -m app.main
```

打开 `http://127.0.0.1:8080`，如果设置了 UI_TOKEN，在右上角输入令牌并点击“应用”。

另开终端发测试数据（**不用 grpcurl**）：

```bash
python3 scripts/send_demo.py --endpoint 127.0.0.1:4317
curl -s http://127.0.0.1:8080/healthz
# token 已启用时
curl -s -H 'Authorization: Bearer change-me-long-random-token' http://127.0.0.1:8080/api/stats
```

验收：UI 显示 2 条 Span、1 条 Log；点击 Span 行查看 Trace 瀑布图，显示两个 Span 及对应关联日志；运行日志页面有两条 Export 记录。

## 6. 构建、推送 Docker 镜像

```bash
# 示例镜像地址：替换成实际私有镜像仓库
docker build -t registry.example.com/ops/otlp-debug-receiver:0.1.0 .
docker push registry.example.com/ops/otlp-debug-receiver:0.1.0
```

本地 Docker 运行：

```bash
docker run --rm --name otlp-debug -p 127.0.0.1:4317:4317 -p 127.0.0.1:8080:8080 \
  -e RETENTION_HOURS=24 \
  -e UI_TOKEN=change-me-long-random-token \
  -v otlp-debug-data:/data \
  registry.example.com/ops/otlp-debug-receiver:0.1.0
```

使用 registry 凭据/镜像拉取 Secret 时，按现有集群管理规范配置 `imagePullSecrets`。

## 7. Kubernetes 部署（推荐流程）

以下默认 namespace 为 `otlp-debug`。清单**不会修改已有** `datakit-service` / `ops-alloy` Service。

```bash
# 1. 创建命名空间
kubectl apply -f k8s/namespace.yaml

# 2. 修改 k8s/deployment.yaml 中 image 字段为可拉取的镜像地址
# 可选：启用 UI Token Secret（先创建 Secret，再取消清单里的 env 注释）
kubectl -n otlp-debug create secret generic otlp-debug-ui \
  --from-literal=token='replace-this-with-a-long-random-token'

# 3. 检查 YAML，再手动部署
kubectl apply --dry-run=client -f k8s/deployment.yaml
kubectl apply -f k8s/deployment.yaml

# 4. 查看运行状态
kubectl -n otlp-debug get pods,svc -o wide
kubectl -n otlp-debug rollout status deploy/otlp-debug-receiver
kubectl -n otlp-debug logs deploy/otlp-debug-receiver -f --tail=100

# 5. 仅本机访问 UI，避免直接暴露公网
kubectl -n otlp-debug port-forward svc/otlp-debug-receiver 8080:8080 4317:4317
```

本地浏览器打开 `http://127.0.0.1:8080`；本地运行 `python3 scripts/send_demo.py --endpoint 127.0.0.1:4317` 自测接收链路。

**持久化选项**：默认使用 emptyDir，Pod 重建后数据消失。需要保留历史数据时，`kubectl apply -f k8s/pvc-optional.yaml`，再将 Deployment 的 data 卷从 `emptyDir: {}` 改为 `persistentVolumeClaim: {claimName: otlp-debug-data}`。若 storageclass 不自动配置权限，需按实际存储后端处理 UID 10001 写权限。

## 8. Istio 对接：先验证、后切换

当前环境已确认 Envoy Tracing Cluster 可能为：

```text
outbound|4317||datakit-service.datakit.svc.cluster.local
```

**不要直接把生产的 DataKit Service 替换为调试程序！** 推荐新建测试 Provider，指向：

```yaml
# 在 istio ConfigMap 的 data.mesh 字符串内部，extensionProviders 顶层追加：
extensionProviders:
  # 保留原有的其他 Provider，不要覆盖
  - name: mdis-otel-debug
    opentelemetry:
      service: otlp-debug-receiver.otlp-debug.svc.cluster.local
      port: 4317
```

`extensionProviders` 是 `mesh` 的**顶层字段**，不要写在 `defaultConfig` 下面。保留已有 Provider，避免改掉已有观测链路。对已有 ConfigMap 编辑前执行 `kubectl -n istio-system get cm istio -o yaml > istio-before.yaml` 做备份。

然后仅对测试工作负载打标签 `otlp-debug-tracing=true`，在其命名空间按 `k8s/istio-telemetry-example.yaml` 创建 Telemetry。**先读已有 Telemetry 配置，确认是否会覆盖或与已有工作负载规则冲突**，只对测试 Pod 采样 100%。

观察：

```bash
# 根据实际的业务 Pod 修改
NS=default
POD=your-test-pod
istioctl proxy-config listeners "$POD" -n "$NS" -o json | jq -r '
  .. | objects
  | select(.["@type"]? == "type.googleapis.com/envoy.config.trace.v3.OpenTelemetryConfig")
  | .grpcService.envoyGrpc.clusterName // empty
' | sort -u
# 预期包含 outbound|4317||otlp-debug-receiver.otlp-debug.svc.cluster.local

kubectl -n otlp-debug logs deploy/otlp-debug-receiver --tail=100
```

验证时向**测试业务服务**发送实际 HTTP 请求，观察 `OTLP traces Export: spans=N`，UI Span 数递增，并核对 Trace ID / URL / Span 属性。只有“Envoy 配置中出现目标 Cluster”不能证明发送成功，必须以 Receiver Export 日志和数据入库为准。

### OTLP 日志如何启用？

Istio Trace 的 `opentelemetry` Provider **不会自动发送访问日志**。如果要收 Envoy Access Logs，还需在 `extensionProviders` 中额外添加：

```yaml
  - name: mdis-otel-debug-logs
    envoyOtelAls:
      service: otlp-debug-receiver.otlp-debug.svc.cluster.local
      port: 4317
```

并对**同一个测试命名空间/工作负载**配置 `Telemetry.accessLogging.providers: [{name: mdis-otel-debug-logs}]`。详见 `docs/DESIGN.md`。不同 Istio 版本的支持情况需以实际 CRD/控制面版本为准。

### 回滚

首先删除或还原测试 `Telemetry` 对 `mdis-otel-debug` 的引用，确认 Sidecar 已恢复原目标；再移除测试用 `extensionProviders`。不要直接删除整个 istio ConfigMap 或覆盖其中其他 Provider。最后删除调试 namespace（如果不再需要）：

```bash
kubectl -n otlp-debug delete -f k8s/deployment.yaml
# kubectl delete -f k8s/namespace.yaml # 会删除 namespace 中的所有资源，谨慎！
```

## 9. UI 功能

- **首页计数器**：启动后累计收到的 Span/Log 数、当前数据库保存数（清理后可能小于累计数）。
- **接收趋势**：最近 60 分钟每分钟收到多少 Span。
- **Span 列表**：服务、Span 名、耗时、Trace ID、接收时间，关键词查询。
- **调用链详情**：按开始时间展示简易瀑布图；展示原始 Span JSON 与相同 Trace ID 的 LogRecords。
- **OTLP 日志**：显示 LogRecord 级别、正文（原始 AnyValue JSON）、服务及 Trace ID。
- **运行日志**：最近 500 条（仅内存保存）程序日志记录；完整记录可 `kubectl logs`。
- **刷新**：默认每 5 秒请求一次；可关闭。每次表格最多显示 100 条，API 单次上限 500 条。

> UI 为简易只读调试界面，没有登录用户体系、实时 WebSocket、集中认证、服务依赖图或跨 Collector 数据聚合；Trace 瀑布图仅展示接收到的 Span（可能是不完整调用链）。

## 10. 常用排障命令

```bash
kubectl -n otlp-debug get pod -l app=otlp-debug-receiver -o wide
kubectl -n otlp-debug describe pod -l app=otlp-debug-receiver
kubectl -n otlp-debug logs deploy/otlp-debug-receiver --since=10m | grep -E 'Export|ERROR|WARNING'
kubectl -n otlp-debug get endpointslice -l kubernetes.io/service-name=otlp-debug-receiver
kubectl -n otlp-debug port-forward svc/otlp-debug-receiver 8080:8080
```

| 现象 | 优先定位方向 |
|---|---|
| UI 可以打开，但没有 Span | 是否生成实际请求、Telemetry Provider、采样率、Sidecar 是否注入、Envoy 目标 Cluster |
| 4317 curl GET 返回 PROTOCOL_ERROR | curl GET 不是合法 OTLP/gRPC 请求，改用 `scripts/send_demo.py` |
| Span 有，日志为零 | `envoyOtelAls` 未启用，或发送端没有 OTLP LogRecords |
| API 401 | UI_TOKEN 错误，UI 输入正确 token；健康检查 `/healthz` 不鉴权 |
| Pod Pending | PVC 未绑定、镜像拉取权限或资源不足 |
| SQLite database is locked | 检查是否误用多副本或共享 SQLite 文件 |
| Pod 重建数据消失 | 默认 emptyDir，改用 PVC |
| 容器无法写 /data | fsGroup / PVC 权限，镜像以 UID 10001 运行 |

## 11. 版本与维护建议

- 第三方 Python 包使用**大版本上限范围**，便于当前安装；正式进入生产长期维护前请生成锁定版本（`pip freeze` / 内部包仓库），通过 CI 构建固定镜像 digest。
- 单副本仅供调试，小规模使用。默认保留 24 小时，容量不做硬限额；高流量场景只选择少量测试服务、限制采样率并监控磁盘占用。
- 数据库和 UI 的查询搜索都是面向调试量级；未来大规模场景考虑 ClickHouse/VictoriaLogs/Tempo 等后端，而不是继续扩 SQLite。
- 更多架构决策、风险、后续扩展见 `docs/DESIGN.md`。

## 12. 参考标准

- OpenTelemetry OTLP 规范：https://opentelemetry.io/docs/specs/otlp/
- Istio OpenTelemetry 分布式追踪：https://istio.io/latest/docs/tasks/observability/distributed-tracing/opentelemetry/
- Istio OpenTelemetry Access Logging：https://istio.io/latest/docs/tasks/observability/logs/otel-provider/
