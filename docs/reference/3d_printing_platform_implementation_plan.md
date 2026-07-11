# 3D 打印客户服务平台 — 基于现有 Flask 项目的实现方案

## Context

在 Bambuddy 开源 3D 打印管理系统之上，需要构建一套独立的客户服务平台（下单、额度、排队、进度查询、后台管理）。已有基础设施是一个教育平台的 Flask 后端（BME_platform_flask），具备用户认证、权限、审计、文件上传、Redis 缓存等能力，但没有订单/支付/后台 Worker 系统。本方案评估如何最大化复用现有项目来落地架构方案。

---

## 核心决策：扩展现有 Flask 项目

**推荐方案：在现有 BME Flask 项目中新增 blueprint 和 model，不建独立服务。**

### 可直接复用的现有能力

| 现有能力 | 实现位置 | 复用方式 |
|---|---|---|
| 用户认证 (JWT) | `UserModel` + `Flask-JWT-Extended` | 扩展角色字段，复用登录/注册流程 |
| 权限 ACL | `PermissionModel` + `@check_permission` 装饰器 | 新增 order_management/credit_management/printer_management 权限 |
| 审计日志 | `AuditLog` 模型 + `@audit_log` 装饰器 | 直接复用于所有新写入接口 |
| 数据库框架 | `Flask-SQLAlchemy` + `Flask-Migrate` + MySQL 8.0 | 同库新增表，通过 migration 管理 |
| Redis 缓存 | `FlaskRedis` | 用于 Celery broker、限流、缓存 |
| 文件上传模式 | `request.files` + UUID 文件名 + 附件模型 | 按 TaskSubmissionAttachment 模式实现 OrderFile |
| 邮件服务 | `Flask-Mail` | 复用于订单通知 |
| API 文档 | Flasgger/Swagger | 新接口同步加 YAML |
| 限流 | `Flask-Limiter` + Redis | 用于客户接口 |
| 分页模式 | `query.paginate()` | 所有列表接口统一 |

### 必须全新构建的模块

| 新模块 | 原因 |
|---|---|
| Celery 后台 Worker（Poller、Scheduler、Slicer） | 项目无任何后台任务框架 |
| 额度/流水系统（冻结/实扣/释放/退款） | 无任何支付或账务模型 |
| Bambuddy API Adapter | 全新集成层 |
| 订单状态机 | 无类似复杂状态流转 |
| Webhook 接收器 | 无入站 Webhook 处理 |
| 3D 文件处理（hash、校验、预览） | 现有文件处理是基础本地存储 |

### 为什么不建独立服务

1. 共享同一用户体系和认证系统，拆分需要额外做 SSO 或 API 互调
2. 教育模块代码在非访问时零开销
3. 单 Flask 项目部署简单（一个 Gunicorn + 一个 MySQL + 一个 Redis），匹配架构文档的"最小部署形态"
4. 在 Flask 中加 Celery 是成熟模式，不需要独立服务

---

## 需要新增的技术组件

| 技术 | 用途 | 包 |
|---|---|---|
| Celery + Redis broker | 后台 Worker | `celery[redis]>=5.4` |
| `trimesh` | 3D 模型解析（体积、边界、面数） | `trimesh>=4.0` |
| `werkzeug.security` | 密码哈希（修复现有明文存储） | Flask 已有依赖 |
| MinIO（Phase 2+） | 对象存储，Phase 1 先用本地文件系统 | `minio>=7.2` |

**暂不需要：** WebSocket（客户端轮询即可）、PostgreSQL（MySQL 足够）

---

## 新增文件结构

```
BME_platform_flask/
├── models_printing.py          # 9 个新数据库模型
├── bambuddy_adapter.py         # Bambuddy REST API 封装
├── celery_app.py               # Celery 配置 + Flask 上下文集成
├── services/
│   ├── __init__.py
│   ├── credit_service.py       # 额度操作（冻结/实扣/释放/退款）
│   └── order_state.py          # 订单状态机
├── tasks/
│   ├── __init__.py
│   ├── polling.py              # Poller: 打印机状态同步
│   ├── scheduling.py           # Scheduler: 打印调度
│   └── slicing.py              # Slicer: 切片任务（Phase 4）
├── blueprints/
│   ├── print_order.py          # 客户订单 API（8 个路由）
│   ├── credit.py               # 额度 API（5 个路由）
│   ├── print_admin.py          # 后台管理 API（14 个路由）
│   ├── webhook_bambuddy.py     # Webhook 接收器（1 个路由）
│   └── worker_jobs.py          # 内部 Job 触发（3 个路由）
└── uploads/models/             # 3D 模型文件存储目录
```

---

## 新增数据库模型（models_printing.py）

与现有 `models.py` 分离，保持 812 行的教育模型不动。新文件在 `app.py` 中 import 以被 Flask-Migrate 识别。

### 1. CreditAccount（额度账户）
```
credit_account: id, user_id(FK→user,unique), available_credit(Numeric), frozen_credit(Numeric), currency_type, updated_at
```

### 2. CreditTransaction（额度流水）
```
credit_transaction: id, user_id(FK→user), order_id(FK→print_order), type(recharge/freeze/capture/release/refund/adjust), amount, before_available, after_available, before_frozen, after_frozen, reason, operator_id(FK→user), idempotency_key(unique), created_at
```
核心原则：**额度不直接改余额，全靠流水驱动。** 每次操作用 `SELECT FOR UPDATE` 锁行 + 幂等键防重。

### 3. PrintOrder（打印订单）
```
print_order: id, order_no(unique), user_id(FK→user), status(20+状态), public_status, material, color, layer_height, nozzle_size, quantity, estimate_weight_g, estimate_print_seconds, estimated_credit, frozen_credit, actual_credit, priority, due_at, customer_note, admin_note, public_progress(0-100), remaining_seconds, created_at, updated_at
```

### 4. OrderFile（订单文件）
```
order_file: id, order_id(FK→print_order), file_type(source_model/sliced_file/preview), original_filename, storage_key, content_type, size_bytes, sha256, version, scan_status(pending/clean/infected), created_at
```

### 5. Quote（报价）
```
quote: id, order_id(FK→print_order), material_cost, machine_time_cost, service_cost, post_processing_cost, risk_cost, total_credit, status(draft/sent/accepted/rejected), note, created_by(FK→user), created_at, accepted_at
```

### 6. Printer（打印机）
```
printer: id, public_name, internal_name, bambuddy_printer_id, model, capabilities(JSON), status(idle/printing/offline/error/maintenance), bambuddy_status_raw(JSON), last_seen_at, enabled_for_auto_schedule, created_at, updated_at
```

### 7. BambuddyJob（订单-Bambuddy 映射）
```
bambuddy_job: id, order_id(FK→print_order), order_no, printer_id(FK→printer), bambuddy_printer_id, bambuddy_queue_id, bambuddy_archive_id, filename, job_token(unique), bambuddy_status, dispatched_at, started_at, completed_at, last_sync_at, mapping_confidence(exact/filename/manual)
```

### 8. PrintEvent（打印事件）
```
print_event: id, order_id(FK→print_order), bambuddy_job_id(FK→bambuddy_job), source(webhook/poller/admin), event_type, raw_payload(JSON), normalized_payload(JSON), idempotency_key, created_at
```

### 9. OrderStatusLog（订单状态变更日志/时间线）
```
order_status_log: id, order_id(FK→print_order), old_status, new_status, note, changed_by(FK→user), created_at
```

---

## 新增 API 路由

### 客户订单 API — `print_order.py`（前缀 `/print-order`）

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/` | 创建订单草稿 |
| POST | `/<id>/files` | 上传 3D 模型文件 |
| GET | `/<id>/quote` | 查看报价 |
| POST | `/<id>/confirm` | 确认下单 + 冻结额度 |
| GET | `/` | 我的订单列表 |
| GET | `/<id>` | 订单详情（脱敏） |
| GET | `/<id>/progress` | 打印进度 |
| POST | `/<id>/cancel` | 取消订单 |
| GET | `/<id>/timeline` | 状态变更时间线 |

### 额度 API — `credit.py`（前缀 `/credit`）

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/me` | 我的额度余额 |
| GET | `/me/transactions` | 我的流水记录 |
| POST | `/admin/adjust` | 管理员调整额度 |
| GET | `/admin/accounts` | 所有账户列表 |
| GET | `/admin/transactions` | 所有流水查询 |

### 后台管理 API — `print_admin.py`（前缀 `/print-admin`）

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/orders` | 订单列表（筛选） |
| GET | `/orders/<id>` | 订单完整详情 |
| POST | `/orders/<id>/quote` | 创建/更新报价 |
| POST | `/orders/<id>/approve` | 审核通过 |
| POST | `/orders/<id>/reject` | 审核拒绝 |
| POST | `/orders/<id>/dispatch` | 分配打印机 + 下发 Bambuddy |
| POST | `/orders/<id>/bind-bambuddy` | 手动绑定 Bambuddy 任务 |
| POST | `/orders/<id>/mark-qc-passed` | 质检通过 + 实扣额度 |
| POST | `/orders/<id>/mark-failed` | 标记失败 |
| POST | `/orders/<id>/refund` | 退款/释放额度 |
| GET | `/printers` | 打印机状态总览 |
| POST | `/printers/sync` | 触发从 Bambuddy 同步 |
| GET | `/bambuddy/queue` | Bambuddy 队列视图 |
| GET | `/printers/<id>/camera` | 摄像头快照 |

### Webhook — `webhook_bambuddy.py`（前缀 `/internal/webhooks/bambuddy`）

| 方法 | 路径 | 安全 |
|---|---|---|
| POST | `/<secret>` | Secret path + IP 白名单 |

### 内部 Job — `worker_jobs.py`（前缀 `/internal/jobs`）

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/sync-printers` | 触发 Poller |
| POST | `/reconcile` | 触发对账 |
| POST | `/schedule` | 触发调度 |

---

## 关键服务模块

### BambuddyAdapter（bambuddy_adapter.py）
封装所有 Bambuddy REST API 调用，是系统访问 Bambuddy 的唯一入口：
- `list_printers()`, `get_printer(id)`, `get_printer_status(id)`
- `list_queue()`, `add_to_queue(archive_id, printer_id)`, `remove_queue_item(id)`
- `list_archives()`, `get_archive(id)`
- `health_check()`
- 统一错误处理、重试、调用日志

### CreditService（services/credit_service.py）
额度操作核心，每个方法都是事务性的：
- `recharge()`, `freeze()`, `capture()`, `release()`, `refund()`, `adjust()`
- 所有操作：`SELECT FOR UPDATE` 锁行 → 修改 → 写 CreditTransaction → 提交
- 幂等键防重复操作

### OrderStateMachine（services/order_state.py）
定义合法状态转换，每次转换自动写 OrderStatusLog：
```
DRAFT → FILE_UPLOADED → QUOTING → WAITING_CUSTOMER_CONFIRM → CREDIT_RESERVED → REVIEWING → APPROVED → SLICING → READY_TO_PRINT → INTERNAL_QUEUED → DISPATCHED → BAMBUDDY_QUEUED → PRINTING → PRINT_COMPLETED → QC_PENDING → READY_FOR_PICKUP → CLOSED
```

---

## 分阶段实施计划

### Phase 0：基础设施（1 周）

1. 本地部署 Bambuddy + 连接测试打印机
2. 用 Bambuddy API Browser 验证所有需要用到的接口，记录实际 payload
3. 修复密码明文存储（`auth.py` 改用 `werkzeug.security`）
4. `pip install celery[redis] trimesh`
5. 创建 `celery_app.py`
6. 创建 `models_printing.py`（9 个模型）
7. `flask db migrate` + `flask db upgrade`
8. 创建 `bambuddy_adapter.py`（只读方法）
9. 测试脚本验证 adapter 能正确读取 Bambuddy 数据

### Phase 1：客户下单 + 额度 + 人工后台（3-4 周）

**第 1-2 周：核心模型和额度系统**
- `services/credit_service.py`（冻结/实扣/释放/退款/充值/调整）
- 编写额度系统单元测试（并发冻结、双重冻结防护、幂等性）
- `services/order_state.py`（状态机）
- `blueprints/credit.py`（5 个路由）
- `blueprints/print_order.py`（客户路由）
- 文件上传按现有 `task.py` 的模式实现

**第 2-3 周：管理端**
- `blueprints/print_admin.py`（管理路由）
- 报价管理
- 确认下单 → 冻结额度 → 审核通过 → 手动改状态 → 质检通过 → 实扣额度

**第 3-4 周：通知和完善**
- 订单状态变更邮件通知（复用 Flask-Mail）
- OrderStatusLog 时间线
- Swagger 文档
- 端到端手动测试

### Phase 2：Bambuddy 状态同步（2-3 周）

**第 1 周：Poller**
- `tasks/polling.py`：`sync_printer_statuses()` 每 30 秒执行
- 更新 Printer 模型 + 活跃订单的 public_progress

**第 2 周：Webhook**
- `blueprints/webhook_bambuddy.py`
- 幂等处理 + 事件匹配到 BambuddyJob + 状态更新

**第 3 周：手动绑定和对账**
- 管理员手动绑定 Bambuddy 任务
- 对账 Worker：检测卡住的订单、不一致状态

### Phase 3：半自动调度（2 周）

- `tasks/scheduling.py`：一键下发到 Bambuddy 队列
- 管理员后台选择打印机 → 系统调用 `POST /queue` → 自动建立映射
- 完整生命周期自动化

---

## 工期估算

| 阶段 | 内容 | 工期 | 累计 |
|---|---|---|---|
| Phase 0 | 基础设施、Bambuddy 验证、建表 | 1 周 | 1 周 |
| Phase 1 | 客户下单、额度、人工后台 | 3-4 周 | 4-5 周 |
| Phase 2 | Bambuddy 状态同步、Webhook | 2-3 周 | 6-8 周 |
| Phase 3 | 半自动调度 | 2 周 | 8-10 周 |

1 名全职开发者可压缩至 5-6 周完成 Phase 0-3。

---

## 验证方式

1. **Phase 0 验证：** 运行测试脚本，确认 adapter 能读取 Bambuddy 打印机列表和状态
2. **Phase 1 验证：** 手动端到端测试 — 注册→上传模型→报价→确认冻结→审核→手动完成→实扣→查时间线
3. **Phase 2 验证：** 实际打一次印，验证 Webhook 事件到达、订单状态自动更新、客户看到进度
4. **Phase 3 验证：** 管理员后台一键下发→自动排队→自动打印→自动完成→自动扣费
5. **安全验证：** 确认客户无法访问 Bambuddy API Key、无法查看其他用户订单、Bambuddy 不暴露公网
