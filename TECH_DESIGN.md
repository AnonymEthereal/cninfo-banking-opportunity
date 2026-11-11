# Listed Company Opportunity Radar 技术方案 v1.0

## 1. 设计目标

本方案服务于 MVP：让 20–50 家上市公司的公告从名单导入、公告采集、正文解析、三阶段 Agent 分析一直流转到 XLSX/HTML 报告。技术设计优先保证数据可追溯、失败可见、前后端契约一致和重复执行幂等。

系统采用单机 Python 应用，不引入数据库、消息队列和容器集群。Streamlit 负责交互，核心业务以普通 Python 服务和模块实现，后续可在不改动领域模型的前提下替换为 FastAPI 或持久化存储。

## 2. 总体架构

```text
Streamlit UI
    │  Service API / Python 调用
    ▼
Application 层：CompanyListService、ScanService、ReportService
    │
    ├─ Scan Orchestrator（状态机、进度、错误汇总）
    ├─ Collector（公告查询、下载、来源校验）
    ├─ Parser（PDF/HTML 正文提取）
    ├─ Agents（事件拆解 → 分类提取 → 营销分析）
    ├─ Validator / Deduplicator（Pydantic、公告和商机去重）
    └─ Exporters（XLSX、HTML）
    │
    ▼
data/
  inputs/        名单快照
  announcements/ 原始公告与正文
  results/       扫描状态、商机 JSON、错误日志
  reports/       XLSX、HTML
```

模块只通过领域模型交互。UI 不直接调用采集器、LLM 或导出器；所有写入均由 Application 层完成。

## 3. 推荐目录

```text
app.py
config.py
requirements.txt
.env.example
core/
  models.py              # Pydantic 模型与枚举
  errors.py              # 领域错误与错误码
  clock.py               # Asia/Shanghai、交易日范围
  storage.py             # 本地 JSON/文件原子读写
  services.py            # 对外用例服务
  scanner.py             # 扫描编排与状态迁移
  llm_client.py          # 统一模型 API 客户端
collectors/
  cninfo.py              # 公告搜索和下载适配器
parsers/
  pdf_parser.py
  html_parser.py
agents/
  event_agent.py
  classify_agent.py
  marketing_agent.py
  prompts.py
exporters/
  excel_exporter.py
  html_exporter.py
templates/daily_report.html
tests/
```

## 4. 核心领域模型

### 4.1 扫描任务

`Scan` 保存任务输入、状态、进度、统计和错误。文件路径固定为 `data/results/{scan_id}/scan.json`，每次状态更新先写临时文件再原子替换。

```python
class ScanStatus(str, Enum):
    QUEUED = "queued"
    COLLECTING = "collecting"
    ANALYZING = "analyzing"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"

class Scan(BaseModel):
    scan_id: str
    companies: list[Company]
    date_range: DateRange
    status: ScanStatus
    progress: Progress
    counts: ScanCounts
    errors: list[ScanError] = []
    created_at: datetime
    updated_at: datetime
```

### 4.2 公告模型

公告原文和元数据单独保存，避免 Agent 结果覆盖原始证据：

```text
data/announcements/{announcement_id}/metadata.json
data/announcements/{announcement_id}/source.pdf
data/announcements/{announcement_id}/text.txt
```

公告去重优先使用来源唯一 ID；没有唯一 ID 时使用 `stock_code + publish_date + normalized_title + source_url` 生成稳定哈希。

### 4.3 商机模型

`Opportunity` 按 `SPEC.md` 定义。证据必须包含原文片段，能选时附 PDF 页码；`amount.value` 未披露时为 `null`，`undisclosed=true`。`analysis_status` 允许 `completed | partial | failed`。

## 5. 扫描编排

`ScanOrchestrator.run(scan_id)` 采用固定顺序：

1. `queued → collecting`：按公司和日期查询公告，保存原始文件、元数据和采集错误。
2. `collecting → analyzing`：逐份公告提取正文；正文缺失的公告写入错误并跳过 Agent。
3. 对每份正文串行执行 Agent 1、2、3；每个 Agent 的中间 JSON 单独落盘，便于失败重试和问题定位。
4. `analyzing → exporting`：读取本次任务全部有效 `Opportunity`，生成 XLSX 和 HTML。
5. 有任何可恢复错误则进入 `completed_with_errors`；没有结果但任务流程正常完成仍为 `completed`，结果数量为 0。

MVP 使用单进程后台线程执行，UI 通过轮询 `scan.json` 获取状态。线程异常必须由编排器捕获并写入 `failed`，不能让 Streamlit 页面直接崩溃。

## 6. 采集与解析

定义 `AnnouncementCollector` 协议：

```python
def collect(company: Company, date_range: DateRange) -> CollectionResult: ...
```

适配器负责请求超时、重试、限速、响应状态检查和来源 URL 记录。MVP 默认连接巨潮资讯网适配器，但采集接口不把供应商字段泄漏到领域模型。

解析器定义统一协议：

```python
def parse(document_path: Path, content_type: str) -> ParsedDocument: ...
```

优先处理文本型 PDF；扫描件、加密 PDF、正文为空时返回 `PDF_PARSE_FAILED`。禁止用标题或模型猜测补齐正文。

## 7. Agent 技术实现

### Agent 1：事件拆解

输入公告元数据和正文，输出 `EventExtractionResult`。提示词要求只引用正文事实，可输出 0..N 个事件；每个事件包含 `event_id`、`summary`、`evidence[]`、`worth_analyzing`。

### Agent 2：分类与金额

输入公告和事件，输出 `OpportunityDraft`。使用枚举约束七大类；金额由 `value/currency/type/original_text/undisclosed` 组成。模型不得将不同金额类型合并。

### Agent 3：营销分析

仅对采购、投资并购、融资新增、增值四类执行深度提示词；其余类别返回基础识别结果和空营销字段。所有推断写入 `marketing_direction`、`suggested_action` 或 `pending_verification`，不得写入事实字段。

统一 `LLMClient.generate_json(schema, prompt)`：设置超时、最大重试次数和模型名；每次响应先解析 JSON，再执行 Pydantic 校验。连续失败记录 `LLM_TIMEOUT`、`LLM_INVALID_JSON` 或 `SCHEMA_VALIDATION_FAILED`。

## 8. 去重和幂等

- 同一 `scan_id` 内公告按 `announcement_id` 去重。
- 商机按 `opportunity_id` 去重；生成 ID 时使用公告 ID + 事件 ID 的稳定哈希，避免重复运行产生重复记录。
- 创建扫描前可计算输入指纹；相同公司和日期范围仍创建新 `scan_id`，但复用已保存公告文件，减少重复下载。
- 报告写入临时文件后改名，防止 UI 下载到半成品。

## 9. 文件存储约定

```text
data/results/{scan_id}/
  scan.json
  announcements.json
  opportunities.jsonl
  errors.jsonl
  agents/{announcement_id}/agent1.json
  agents/{announcement_id}/agent2.json
  agents/{announcement_id}/agent3.json
data/reports/{scan_id}/report.xlsx
data/reports/{scan_id}/report.html
```

敏感配置只从环境变量读取：`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`HTTP_TIMEOUT_SECONDS`。`.env` 不提交版本库。

## 10. 接口实现建议

先实现 Service 层，再由 Streamlit 适配：

```python
validate_company_file(file_bytes) -> CompanyValidationResult
create_scan(request: CreateScanRequest) -> ScanCreated
get_scan(scan_id: str) -> Scan
list_opportunities(scan_id: str, category=None, limit=100, offset=0) -> OpportunityPage
get_report_path(scan_id: str, format: ReportFormat) -> Path
```

这些函数的入参、返回值和错误码严格映射 `SPEC.md` 的 HTTP 契约。未来接入 FastAPI 时只需增加路由层，不修改 Service 和领域模型。

## 11. 可观测性与安全

每条日志带 `scan_id`、`announcement_id`、阶段和耗时；日志中不得打印 API Key、完整公告正文或完整模型响应。对外错误返回稳定错误码，内部日志保留异常堆栈。下载接口只允许访问由 `scan_id` 解析出的固定目录，拒绝路径穿越。

## 12. 测试策略

- 单元测试：日期范围、Excel 校验、模型校验、金额规则、状态迁移、去重和错误映射。
- 适配器测试：使用固定 HTTP/PDF fixtures，验证真实响应格式变化时的失败提示。
- Agent 测试：使用脱敏固定公告和 mock LLM，验证 JSON Schema、证据必填和金额不混淆。
- 端到端测试：20 家 fixture 名单，覆盖正常、无商机、采集失败、PDF 失败、重复运行、报告下载。

## 13. 分阶段实施

### 阶段 1：基础工程与采集

完成目录、配置、模型、名单校验、日期计算、巨潮采集适配器、PDF 解析和本地存储。验收：给定股票代码和日期可保存真实公告或明确错误。

### 阶段 2：Agent 流程

接入统一 LLM 客户端，完成三个 Agent、中间结果落盘、Schema 校验、状态机和去重。验收：固定公告可产生符合模型的 0..N 条商机。

### 阶段 3：报告与 UI

完成 XLSX/HTML 导出、Streamlit 三个页面区域、轮询状态和下载。验收：同一批 JSON 生成两种报告，部分失败可见。

### 阶段 4：MVP 回归

用 20–50 家真实企业执行端到端测试，记录采集覆盖、失败类型、模型异常和人工复核问题；不把准确率阈值作为 MVP 阻断条件。

## 14. 首轮技术决策

1. 先做本地文件存储，避免 MVP 引入数据库迁移和部署复杂度。
2. 先做串行 Agent，确保日志、证据和失败定位清晰；后续再按公告级别并发。
3. 先验证巨潮采集稳定性，再投入 UI 美化和复杂编排。
4. 所有输出从统一 `Opportunity` 模型生成，禁止 Excel 和 HTML 各自解析模型结果。

