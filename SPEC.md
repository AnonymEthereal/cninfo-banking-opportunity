# Listed Company Opportunity Radar — MVP Spec v1.0

## 企业输入增补（2026-11-11）

公告预览增补：新建 UI 任务先检索元数据，进入 awaiting_review，用户确认后才下载正文并分析。持久化 require_review、analysis_confirmed 和时间预算；来源重试后需重新确认。原脚本及旧任务默认保持原流程。默认单篇 600 秒、整批分析 28800 秒，可在页面调整；仍保留底层 HTTP 超时。错误详情展示阶段、类型、可用 HTTP 状态及建议，不输出原始模型响应或密钥。无公告可修改范围另建检索，无需启动模型。

失败源重试增补：支持重试采集及正文失败项，复用成功企业采集、有效正文与成功分析检查点；新发现或修复正文的公告继续分析。执行前归档原任务、公告/机会清单、报告及失败正文文件。此规则替代下文“不重试采集或解析失败”的旧限制。扫描件仍可能再次解析失败，不引入 OCR。

恢复能力增补：任务持久化 analysis_mode（旧记录默认为 unknown），历史任务可查看并恢复；unknown 必须明确原模式后执行。失败公告分析可单独重试，复用成功检查点、归档旧失败结果及任务状态；不自动重试采集或解析失败，真实模式重试有模型费用。

规模修订：根据大名单需求，取消 Excel、手动输入和已选名单的 50 家硬上限，替代下文原有 1–50 家约束。默认采集请求按企业串行执行，相邻企业间隔 0.5 秒（COLLECTION_INTERVAL_SECONDS 可配置）；现有公告分析并发和超时保护继续保留。界面允许调整日期范围，展示名单规模及大名单费用提示，并区分无公告、正常分析零商机与处理失败。取消数量上限不承诺无限资源，暂停、两阶段费用确认及采集恢复仍是后续功能。

Excel 导入交互修订：支持中文列名及代码或名称单字段输入，通过当前名录补齐身份；数字代码补前导零。名称代码冲突、非唯一或模糊名称需用户确认，不自动接受。预览有效、重复、待确认和错误行，可仅追加有效与已确认企业，问题明细可下载；重复按代码跳过，合并超过 50 家阻止添加，已有名单不变。名录不可用时未匹配企业不能自动通过。旧双字段校验接口保持兼容，并修正空值误通过问题。

后续交互修订：搜索、批量粘贴与 Excel 导入共同追加到已选名单，切换入口不覆盖选择，支持移除和清空。完整名录自动加载并缓存，失败时保留已有名单并提示降级。搜索支持名称片段、代码前缀及近似名称；近似名称必须由用户确认，不能自动生成扫描对象。

根据后续交互需求，除原 Excel 双字段上传外，支持企业列表选择及证券简称或 6 位股票代码输入。单字段输入由企业名录解析为完整 Company 后使用原扫描流程，不虚构名称或代码。初始快捷列表覆盖有限企业，用户可主动加载巨潮沪深名录；未匹配或同名歧义阻止扫描。手动输入重复企业去重并提示，Excel 原有校验规则保持兼容。完整公司法定名称及模糊名称暂不保证匹配。

## 1. 目标与边界

系统输入一份包含 `stock_code`、`company_name` 的 Excel 名单和扫描日期范围，采集真实公告，依次执行事件拆解、商机分类/金额提取、营销分析，生成可追溯的商机 JSON，并导出 XLSX 与独立 HTML。

MVP 不包含定时调度、历史补跑、全市场扫描、CRM/权限、商机评分、人工审批、跨公告语义合并和转化跟踪。采集失败必须显示为失败，不能当作“无商机”；未披露金额使用 `null` 并标记 `undisclosed=true`。

## 2. 术语和标识

- `scan_id`: 一次扫描任务的 UUID，所有接口和下载均以此关联。
- `announcement_id`: 来源公告唯一 ID；同一任务内去重键。
- `opportunity_id`: 一条独立商机 UUID；一份公告可以产生 0..N 条。
- 时间均使用 `Asia/Shanghai`；API 时间使用 ISO 8601（带时区），日期使用 `YYYY-MM-DD`。
- `status`: `queued | collecting | analyzing | exporting | completed | completed_with_errors | failed`。

## 3. 前后端一致性约定

前端只消费接口返回的数据，不自行推断业务状态或拼装商机。后端负责校验、状态迁移、去重、Agent 编排、导出和错误分类。Streamlit 可作为本地前端，以下 HTTP 契约同时作为 Python service 层函数契约。

| 页面区域 | 后端接口 | 前端可操作 | 必须展示 |
|---|---|---|---|
| 企业名单 | `POST /v1/company-lists/validate` | 上传/校验 Excel | 行数、有效数、错误行、重复行 |
| 扫描执行 | `POST /v1/scans`、`GET /v1/scans/{scan_id}` | 开始扫描、刷新状态 | 日期范围、进度、阶段、成功/失败数、错误 |
| 结果预览 | `GET /v1/scans/{scan_id}/opportunities` | 分类筛选、查看证据/来源 | 商机字段、证据、待核实项、分析状态 |
| 报告下载 | `GET /v1/scans/{scan_id}/reports/{format}` | 下载 xlsx/html | 生成状态、文件名、下载内容 |

## 4. 页面流程

1. 上传 Excel；调用名单校验接口。校验失败时禁止开始扫描。
2. 展示默认日期范围（最近一个工作日至昨日，按中国交易日历计算），允许用户确认后开始扫描。
3. 创建 `scan_id` 后进入轮询；后端返回阶段和计数，前端不得把 `0` 结果解释为完成，必须等待终态。
4. 终态为 `completed` 或 `completed_with_errors` 时展示结果和下载按钮；`failed` 时展示可读错误和重试入口。

## 5. API 契约

### 5.1 校验企业名单

`POST /v1/company-lists/validate`，`multipart/form-data`，字段 `file`。

响应 `200`：

```json
{
  "valid": true,
  "companies": [{"stock_code": "000001", "company_name": "示例公司"}],
  "summary": {"total_rows": 20, "valid_rows": 19, "duplicate_rows": 1, "error_rows": 0},
  "errors": [{"row": 3, "field": "stock_code", "code": "INVALID_STOCK_CODE", "message": "股票代码格式错误"}]
}
```

规则：必填列名；股票代码为 6 位数字字符串；公司名非空；重复代码只保留首行并报告重复；单次 1–50 家。

### 5.2 创建扫描

`POST /v1/scans`，`application/json`：

```json
{
  "companies": [{"stock_code": "000001", "company_name": "示例公司"}],
  "date_range": {"start_date": "2026-11-06", "end_date": "2026-11-09", "timezone": "Asia/Shanghai"}
}
```

响应 `202`：

```json
{"scan_id":"scan_01J...","status":"queued","date_range":{"start_date":"2026-11-06","end_date":"2026-11-09","timezone":"Asia/Shanghai"}}
```

### 5.3 查询扫描状态

`GET /v1/scans/{scan_id}`，响应：

```json
{
  "scan_id":"scan_01J...", "status":"analyzing", "progress": {"stage":"agent_2", "completed":12, "total":20},
  "counts":{"companies":20,"announcements":35,"opportunities":18,"collection_failed":2,"analysis_failed":1},
  "errors":[{"scope":"announcement","announcement_id":"ANN-1","code":"PDF_PARSE_FAILED","message":"无法提取正文"}],
  "report":{"xlsx":null,"html":null}
}
```

### 5.4 查询商机

`GET /v1/scans/{scan_id}/opportunities?category=采购类&limit=100&offset=0`。

响应为 `{ "items": [Opportunity], "total": 18, "filters": {"categories": [...]}}`。仅返回该 `scan_id` 的结果；按 `opportunity_id` 去重。

### 5.5 下载报告

`GET /v1/scans/{scan_id}/reports/xlsx` 或 `/html`。未生成返回 `409 REPORT_NOT_READY`；不存在返回 `404 SCAN_NOT_FOUND`。成功返回文件流及 `Content-Disposition`。

## 6. 统一数据模型

```json
{
  "opportunity_id":"opp_01J...", "announcement_id":"ANN-...", "stock_code":"000001", "company_name":"示例公司",
  "publish_date":"2026-11-06", "announcement_title":"...", "category":"采购类", "subcategory":"招标",
  "event_summary":"...", "amount":{"value":1000000000,"currency":"CNY","type":"项目总投资","original_text":"...","undisclosed":false},
  "other_amounts":[], "counterparty":null, "project_name":null, "bank_products":[], "marketing_direction":null,
  "suggested_action":null, "evidence":[{"text":"...","source_page":1}], "pending_verification":["..."],
  "source_url":"https://...", "analysis_status":"completed", "analysis_error":null
}
```

`category` 枚举：采购类、销售类、投资并购类、融资新增类、增值类、报告类、其他类。深度营销仅对采购、投资并购、融资新增、增值四类生成；其余类的营销字段可为 `null`。金额不得把项目总投资、实际融资、拟申请授信混为同一字段。

## 7. 状态与错误语义

状态迁移：`queued → collecting → analyzing → exporting → completed|completed_with_errors`；不可逆；未捕获异常进入 `failed`。采集失败、PDF 解析失败、单个 Agent 失败均写入 `errors`，并继续处理其他公告。`completed_with_errors` 仍可下载报告，但报告必须包含异常摘要。

错误响应统一格式：

```json
{"error":{"code":"INVALID_DATE_RANGE","message":"开始日期不能晚于结束日期","details":{}}}
```

## 8. Agent 接口

- Agent 1 输入 `AnnouncementInput`，输出 `EventExtractionResult(events: 0..N, evidence[])`。
- Agent 2 输入公告和事件，输出 `OpportunityDraft`，必须给出类别、金额、证据；无披露金额为 `null`。
- Agent 3 输入 `OpportunityDraft` 和事实证据，输出营销字段及 `pending_verification`，不得把推断写成事实。
- 每一步均执行 Pydantic 校验；校验失败记录 `SCHEMA_VALIDATION_FAILED`，不伪造默认商机。

## 9. 验收标准

- 20–50 家名单可校验并启动任务。
- 真实公告保留来源 URL、标题、日期和正文；采集失败可见。
- 一份公告可产生多条商机；重复运行不产生重复公告记录。
- 七类商机可输出，四类有营销分析，其余类保留基础识别。
- XLSX 和 HTML 使用同一批 `Opportunity` 数据；空结果、部分失败、下载未就绪均有明确提示。
- 前端展示的每个状态和数字均可从上述接口响应直接得到。

