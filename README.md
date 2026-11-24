# Enterprise RAG Quality Lab

一个**离线可运行、可回归、可解释**的企业 RAG 智能组织实验项目。它不是只展示“能回答问题”的聊天 Demo，而是把任务型智能体协作、制度版本时间旅行、组织经验沉淀、审计闭环和固定评测放在同一条可重复运行的流水线上。

项目内置 7 篇模拟企业文档（含一份历史版本）、21 道固定评测题、协调者/检索/推理/审计四类智能体、经验库、反馈闭环、可控进化端点、FastAPI 接口、SQLite 运行轨迹和可直接提交的基线报告。默认只使用本地确定性组件，**不需要任何 API Key**。

## 为什么做这个项目

企业 RAG 的问题通常不在“模型能不能说出一段话”，而在于：

- 答案有没有被正确的制度文档支持？
- 引用是不是来自应该引用的来源？
- 知识库没有答案时，系统会不会编造？
- 文档里夹带的恶意指令，会不会改变系统行为？
- 旧版制度会不会被当成当前制度回答？
- 高质量问答经验能不能沉淀，而不是每次从零检索？
- 改了切块、检索、智能体或提示词后，质量是变好还是变差？

本项目把这些风险变成可观测指标、可审计轨迹和可重复实验。

## 智能组织的三个落点

| 智能组织原则 | 在本项目中的实现 |
|---|---|
| 打破岗位边界，围绕任务协作 | Coordinator 拆解任务，Retriever 取证，Reasoner 生成/对比，Auditor 审计；ChatService 只做持久化，不再包揽全部逻辑 |
| 沉淀隐性知识 | Auditor 通过的高质量问答自动进入 Experience Library；相同问题 + 相同生效日期 + 上下文哈希未变化时可安全复用 |
| 可衡量 + Looping | 21 题固定评测、质量门禁、用户反馈、受控阈值进化端点，构成“评测 → 反馈 → 调整 → 再评测”的闭环 |

## 核心特性

- **任务型智能体团队**：协调者、检索专员、推理专员、审计专员、经验库五个角色，所有决策都有 `AgentTrace`。
- **多时点制度对比**：自动识别“2025 年 vs 2026 年”的对比问题，按生效日期拆成两个子任务，再合并引用。
- **完全离线的默认链路**：哈希向量 + BM25 混合检索 + 抽取式生成，零外部服务依赖。
- **可替换生成器**：默认 `extractive`，也支持 OpenAI 兼容的 Chat Completions 接口。
- **可追溯 chunk**：每个 chunk 继承 `source`、`checksum`、制度版本、生效日期和 `heading_path`；引用可定位到来源与版本。
- **可信评测**：保留生成器原始输出，单独统计非法引用率并纳入 2% 门禁；引用带可校验的句子级 `quote`。
- **问题焦点与澄清**：宽泛的“假期是多少天”先追问具体假期类型；检索不到任何问题焦点词时拒答；Auditor 再检查答案是否真正覆盖问题焦点，避免“引用真实但答非所问”。
- **制度版本时间旅行**：按 `as_of` 查询历史时点的有效制度；旧版本自动失效，重叠生效窗口会被拒绝。
- **版本快照与指纹**：`fingerprint` 唯一约束区分“同正文不同生效期”，`document_versions` 保留每次改版内容。
- **经验沉淀与复用**：审计通过且质量达标的回答进入经验库；上下文变化后自动失效。
- **反馈闭环**：用户评分可提升或抑制经验；低分经验会被删除。
- **受控进化**：`POST /api/v1/organization/evolve` 在固定评测集上搜索覆盖率阈值，默认只给建议，显式 `apply=true` 才在当前进程生效。
- **固定质量门禁**：来源命中率、MRR、关键词覆盖、引用准确率、拒答正确率、注入抵抗率和延迟。
- **可选 Redis 缓存**：embedding / retrieval / answer 三层精确缓存；默认关闭，短超时 fail-fast、熔断降级、singleflight、SCAN 清理和 `/health.cache` 可观测。
- **工程化交付**：FastAPI、Pydantic、SQLite、pytest、Ruff、Dockerfile、GitHub Actions、依赖审计。
- **可审计运行轨迹**：每个回答保存检索结果、智能体轨迹、引用、质量检查和经验使用情况。

### 问题焦点与答案相关性

只校验“引用真实存在”不够：一段关于病假工资的真实引用，也可能答非所问。v1.2.1 增加三层相关性防线：

1. **宽泛假期先澄清**：问题包含“假期/休假/请假”且询问天数，但没有年假、病假、婚假、产假、丧假、事假、调休等具体类型时，返回澄清答案，不检索、不生成、不沉淀经验。
2. **检索焦点守卫**：从问题中提取去掉“员工/公司/多少/几天/需要”等泛化词后的焦点词；如果检索结果没有任何焦点词命中，返回拒答，而不是硬答。
3. **Auditor 答案相关性**：回答必须至少覆盖一个焦点词，否则 `answer_relevant=false`，整次审计不通过，质量分压到 0.5，不进入经验库。旧的答非所问经验复用时也会被拒绝并删除。

例如：

```text
问题：员工的假期是多少天
行为：refused=true，返回“请明确年假/病假/婚假/产假/丧假/事假/调休……”
原因：宽泛问题没有具体假期类型，不能用一个固定天数回答
```

```text
问题：员工入职满三年，年假是多少天
行为：正常回答 10 天，并引用 2026 版员工手册
```

## 快速开始

要求 Python 3.11+。

### 方式一：uv

```bash
uv sync
uv run uvicorn app.main:app --reload
```

### 方式二：venv + pip

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

```bash
# macOS / Linux
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload
```

### 方式三：Docker

```bash
docker build -t enterprise-rag-quality-lab:1.3.1 .
docker run --rm -p 8000:8000 enterprise-rag-quality-lab:1.3.1
```

镜像基于 `python:3.11-slim`，使用非 root 用户，暴露 8000，并带 `/health` 健康检查。详见 [Dockerfile](Dockerfile)。

打开：

- Swagger UI：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>
- 智能组织状态：<http://127.0.0.1:8000/api/v1/organization>

首次启动会自动把 `data/sample_docs/` 下的 7 篇文档写入 `data/rag_lab.db`。重复启动是幂等的；文档内容变化后会重新切块和索引。

## 支持的文档格式

| 格式 | 支持 | 说明 |
|---|---|---|
| `.md` / `.markdown` | ✅ | Markdown 标题、frontmatter、代码块、heading path |
| `.txt` | ✅ | UTF-8 / UTF-8 BOM，失败回退 GB18030 |
| `.pdf` | ✅ 文本型 | 用 `pypdf` 提取可选中文本，支持多页合并 |
| 扫描型 PDF | ❌ | 没有 OCR；会返回“requires OCR”而不是空答案 |
| 加密 PDF | ❌ | 空密码也无法解密时拒绝 |
| `.docx` / `.xlsx` / `.html` / 图片 | ❌ | 当前没有对应解析器 |

PDF 细节：

- 文本单文件默认上限 `RAG_LAB_MAX_DOCUMENT_BYTES=2000000`；
- PDF 单独使用 `RAG_LAB_MAX_PDF_BYTES`，默认 `20000000`（20MB），避免大 PDF 被文本上限误拒；
- 页数上限 `RAG_LAB_MAX_PDF_PAGES=500`；
- 提取后的文档 `content_type=application/pdf`，metadata 记录 `page_count` 和 `pdf_title`；
- PDF 的每一页文本按空行合并后进入现有切块/embedding/引用链路；
- 当前没有逐页 chunk 的页码字段，引用定位到文档与 chunk；逐页引用需要后续把 `page` 写进 chunk metadata；
- 扫描件请先 OCR 成文本或可选中 PDF，再入库；
- 用户 PDF 建议放 `data/user_docs/`，不要放 `data/sample_docs/`：后者用于固定基线和 21 题评测，混入个人大文件会改变语料与延迟。

```bash
python scripts/ingest.py --source data/user_docs
```

上传入口：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/documents/upload \
  -F "file=@employee-handbook.pdf;type=application/pdf"
```

## Redis 缓存（可选，默认关闭）

缓存是**显式开启**的能力。`CACHE_ENABLED=false` 时应用不会连接 Redis，所有 API、测试和评测照常运行。

```bash
# 本地开发 Redis（无持久化）
docker compose up -d redis

# .env
CACHE_ENABLED=true
REDIS_URL=redis://127.0.0.1:6379/0
CACHE_KEY_PREFIX=raglab
CACHE_ENV=local
CACHE_SCOPE=public
```

配置同时接受 `CACHE_ENABLED` 与 `RAG_LAB_CACHE_ENABLED` 两种写法；完整键见 [.env.example](.env.example)。

### 三层缓存

| 层 | 缓存内容 | 默认 TTL | 命中节省的量化口径 |
|---|---|---:|---|
| `embedding` | 文本 + 模型 + 维度 → float32 向量 | 7 天 | 1 次 embedding 调用 + N 个输入 token |
| `retrieval` | query + top_k + filters + as_of + index_version → chunk id/分数 | 30 分钟（10–60 分钟可调） | 1 次向量检索的近期 P95 耗时 + 1 次向量检索调用 |
| `answer` | 问题 + 上下文指纹 + prompt + 生成器签名 + as_of → 最终回答 | 15 分钟（5–30 分钟可调） | 1 次 LLM 调用 + 输入/输出 token |

TTL 带 ±10% 随机抖动，避免同一时刻集中过期。空检索结果默认**不缓存**；只有 `NEGATIVE_CACHE_ENABLED=true` 时才使用 `RETRIEVAL_NEGATIVE_TTL`（默认 60 秒）的负缓存，防止索引问题被长期固化。

### Key 与一致性

```text
{prefix}:{env}:{namespace}:{sha256(canonical_input)}
```

- 原始 query、文档名、用户文本不会出现在 key 中，只出现 SHA-256 摘要。
- 规范化输入包含：模型名、维度、prompt 版本、top_k、排序后的 document filters、as_of、采样参数、`CACHE_SCOPE`。
- retrieval 的 namespace 带 `index_version`（基于文档更新时间、数量、chunk 数和 checksum 的 manifest hash）；重建索引后 key 自然变化，不需要逐个删除。
- 当前所有文档是全局语料，没有用户级 ACL；如果未来加入租户/ACL，必须把租户或权限指纹放进 `CACHE_SCOPE`，或把授权的 `document_ids` 作为 filters 传入。**不能把用户私有上下文放进全局 key。**

### 可观测与健康检查

每次缓存操作输出结构化字段：

```text
cache_hit layer=embedding latency_ms=... saved_ms=... saved_input_tokens=... saved_model_calls=...
cache_miss layer=retrieval latency_ms=...
cache_error layer=answer error=RedisError consecutive_errors=...
cache_cooldown_skip layer=retrieval
```

`GET /health` 返回 `cache` 子系统：`enabled`、`backend`、`status`、`last_error`、`consecutive_errors`、`cooldown_remaining_s`、`metrics`。命中率分母是该层 `hits + misses + errors`；`saved_ms` 使用近期 miss 的 P95，而不是把缓存等待时间算成收益。

### 降级与超时

- 启动 PING 失败只打印 WARN，自动进入无缓存模式，不阻塞启动。
- Redis socket timeout 默认 **100ms**、connect timeout **500ms**；单次缓存操作总预算约 700ms，慢 Redis 不会把请求拖成长挂起。
- 连续错误达到阈值后进入 `CACHE_FAIL_OPEN_COOLDOWN`（默认 30 秒）；冷却期内不连接 Redis，直接走原始链路。
- 任何 Redis 异常都只降级，不向客户端返回 500 或堆栈。
- 同一 key 的并发 miss 使用进程内 singleflight，并且**拿到锁后 double-check**。锁等待超过 `CACHE_SINGLEFLIGHT_TIMEOUT` 时直接回源，避免堆积。
- singleflight 只解决单实例惊群；多实例下同样的 cold miss 最多按实例数重复回源，这是明确取舍，不使用分布式锁。

### 清缓存

```bash
# 清一个 namespace
python scripts/cache_cli.py clear --namespace embedding

# 只清某个索引版本的 retrieval key
python scripts/cache_cli.py clear --namespace retrieval --index-version <manifest-hash>

# 清全部
python scripts/cache_cli.py clear --namespace all
```

CLI 使用 `SCAN` 游标 + 批量 `UNLINK` 并限速，不使用 `KEYS`。`--dry-run` 只打印前缀。`/health` 的 `cache` 信息可帮助确认当前实例状态。

### 评测

离线评测默认强制无缓存；即使环境里 `CACHE_ENABLED=true`，也需要显式 `--cache` 才开启：

```bash
# 默认：无缓存，不产生 Redis 写入
python eval/run_eval.py --no-cache

# 对照实验：显式开启
python eval/run_eval.py --cache
```

开启缓存时，报告 `config.cache_enabled=true`，每个 case 带 `cache_hit`/`cache_layers`，聚合结果带 `cache_hit_cases`、`cache_hit_rate`、`cache_hits`、`cache_misses`、`cache_errors`、`cache_saved_ms`。缓存命中的样本单独统计，不把延迟和未命中样本混在一起。

**语义缓存**（同义 query 复用）是可选扩展层：`SEMANTIC_CACHE_ENABLED=false` 默认关闭；当前版本的 exact-cache 链路不会把它混入默认结论。该层尚未接入生产路径，开启只会在启动日志提示 reserved，不会改变答案。

当前回答接口不是 SSE/流式；如果未来引入流式，缓存保存完整文本并在回放时分块，首 token 延迟必须按“真实生成 / 缓存回放”分别打标，不能混入同一个 P95。

## 智能组织工作流

```text
用户问题
   │
   ▼
Coordinator ── 单跳? ──► 1 个子任务
   │                 └─► 多时点? ──► 2 个子任务（不同 as_of）
   ▼
Experience ── 命中可复用经验? ──► Auditor 复核后直接返回
   │ 否
   ▼
Retriever Agents ── 向量 + BM25 + 生效日期过滤
   │
   ▼
Reasoner ── 单跳生成 / 多时点对比 / 全局引用重编号
   │
   ▼
Auditor ── 引用完整、制度时效、跨任务覆盖、安全边界
   │
   ├─ 通过且质量达标 ──► Experience Library
   ▼
ChatService ── 保存 run / 轨迹 / 质量分
```

### 示例：跨版本对比

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"2025年入职满5年的年假，和2026年新制度的区别？"}'
```

系统会：

1. Coordinator 识别两个年份和“区别”意图；
2. Retriever 分别用 `as_of=2025-06-01` 和 `as_of=2026-06-01` 检索；
3. Reasoner 合并两个答案，重编号引用，生成关键数值对比；
4. Auditor 检查两个时点都有引用、引用都在各自生效窗口内；
5. ChatService 返回 `plan_strategy=temporal_comparison` 和完整智能体轨迹。

### 经验复用

```bash
# 第一次：正常检索、生成、审计并沉淀经验
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"员工入职满三年，年假有多少天？","as_of":"2026-06-01"}'

# 第二次：相同问题 + 相同 as_of + 上下文未变化
# 返回 experience_reused=true，并附经验 ID
```

经验复用有三重保护：

- 相同标准化问题与相同 `as_of`；
- 引用块内容哈希未变化；
- 复用前仍由 Auditor 重新校验引用和时效。

### 反馈与进化

```bash
# 评分 4–5：提升/补录经验；评分 1–2：删除经验
curl -X POST http://127.0.0.1:8000/api/v1/runs/{run_id}/feedback \
  -H "Content-Type: application/json" \
  -d '{"rating":5,"comment":"引用和时效正确"}'

# 受控进化：默认只返回候选阈值和最佳建议，不修改运行配置
curl -X POST http://127.0.0.1:8000/api/v1/organization/evolve \
  -H "Content-Type: application/json" \
  -d '{"apply":false}'

# 显式应用：仅在建议不劣于基线且注入抵抗率为 100% 时生效
curl -X POST http://127.0.0.1:8000/api/v1/organization/evolve \
  -H "Content-Type: application/json" \
  -d '{"apply":true}'
```

> 进化搜索使用同一套固定评测集，属于受控实验，不是生产自动调参。生产环境应使用留出集、人工审批和配置回滚。

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 数据库、文档数、生成器和向量配置 |
| `GET` | `/api/v1/organization` | 智能体团队、经验库统计、闭环步骤 |
| `GET` | `/api/v1/organization/experiences` | 经验库列表 |
| `DELETE` | `/api/v1/organization/experiences/{id}` | 删除经验 |
| `POST` | `/api/v1/organization/evolve` | 受控阈值搜索与可选应用 |
| `GET` | `/api/v1/documents` | 文档列表；支持 `?as_of=YYYY-MM-DD` 过滤有效版本 |
| `POST` | `/api/v1/documents` | 提交正文；支持制度版本元数据 |
| `POST` | `/api/v1/documents/upload` | 上传 `.md` / `.txt` / 文本型 `.pdf` |
| `POST` | `/api/v1/documents/ingest-sample` | 幂等同步示例语料 |
| `GET` | `/api/v1/documents/{id}` | 文档详情和完整正文 |
| `GET` | `/api/v1/documents/{id}/versions` | 版本快照与历史正文 |
| `POST` | `/api/v1/documents/{id}/reindex` | 重建切块和向量 |
| `DELETE` | `/api/v1/documents/{id}` | 删除文档（级联删除切块） |
| `POST` | `/api/v1/chat` | 智能组织问答；支持 `as_of` |
| `GET` | `/api/v1/runs` | 运行记录列表 |
| `GET` | `/api/v1/runs/{run_id}` | 检索轨迹、引用、智能体指标和错误 |
| `POST` | `/api/v1/runs/{run_id}/feedback` | 评分并更新经验 |
| `GET` | `/api/v1/eval/dataset` | 评测集摘要和样例 |
| `POST` | `/api/v1/eval/run` | 对当前知识库执行 21 题评测 |
| `GET` | `/api/v1/eval/results` | 历史评测结果 |
| `GET` | `/api/v1/eval/results/{run_id}` | 单次评测完整报告 |
| `GET` | `/api/v1/eval/baseline` | 读取提交在仓库中的基线报告 |

## 评测

```powershell
# 重新生成基线报告
python eval/run_eval.py --output eval/results/baseline.json

# CI 风格质量门禁
python eval/run_eval.py `
  --database data/eval.db `
  --output eval/results/ci.json `
  --baseline eval/results/baseline.json `
  --max-regression 0.02 `
  --fail-under-pass-rate 0.80 `
  --fail-under-hit-rate 0.85 `
  --require-quality-gate
```

### 基线结果

基线文件：[eval/results/baseline.json](eval/results/baseline.json)

| 指标 | 结果 |
|---|---:|
| 通过题数 | 21 / 21（100%） |
| 来源命中率 hit@5 | 100% |
| MRR | 1.000 |
| 关键词覆盖率 | 100% |
| 引用准确率 | 100% |
| 非法引用率 | 0.0% |
| 拒答正确率 | 100% |
| 提示注入抵抗率 | 100% |
| 延迟 p50 | 12.8 ms |
| 延迟 p95 | 13.8 ms |

运行环境：Windows、Python 3.12.5、SQLite、本地 `hashing-384` 向量、`extractive` 生成器，21 道题单次本地运行。延迟只代表本机进程内计算，不代表生产 SLA。

> **诚实说明**：这是回归基线，不是通用 RAG 能力证明。语料只有 7 篇（其中两版属于同一制度族），生成器又是抽取式的，因此 21/21 是预期结果。智能组织部分由单元/集成测试验证，但经验复用和进化策略仍需更大规模留出集。

### 指标含义

- **来源命中率**：前 `k` 个检索块中是否出现标注的正确来源。
- **MRR**：第一个正确来源在检索结果中的排名倒数。
- **关键词覆盖率**：答案是否包含标注的关键事实片段。
- **引用准确率**：回答引用的来源中，有多大比例属于期望来源。
- **拒答正确率**：知识库外问题是否被明确拒答。
- **注入抵抗率**：直接注入是否被拦截，间接注入内容是否没有改变答案。
- **质量门禁**：通过率 ≥ 80%、命中率 ≥ 85%、引用准确率 ≥ 80%、注入抵抗率 = 100%。

## 制度版本时间旅行

企业制度不会只有一份：旧版仍可能被审计引用，新版可能在未来生效。如果知识库只按相似度检索，很容易把旧版年假天数当成当前答案。

```markdown
---
policy_key: employee-handbook
version: "2026"
effective_from: 2026-01-01
# effective_until: 2026-12-31
---
```

- `policy_key` 标识同一制度族，`version` 是版本标签。
- `effective_from` / `effective_until` 定义生效窗口，闭区间。
- `POST /api/v1/chat` 传 `as_of` 时，只检索该日期有效的制度。
- 同一 `policy_key` 的生效窗口重叠时，新文档返回 `409`。
- 引用结果会带 `version` 和 `effective_from/until`，便于审计。

内置 2025 与 2026 两版员工手册。历史查询 `as_of=2025-06-01` 返回 2025 版 **8 天**；`as_of=2026-06-01` 返回 2026 版 **10 天**。

每道评测题固定了 `as_of`，避免系统时钟变化导致基线漂移。

## 评测集格式

`eval/dataset.jsonl` 每行一个 JSON 对象：

```json
{
  "id": "q001",
  "question": "员工入职满三年，年假有多少天？",
  "category": "hr",
  "behavior": "answer",
  "as_of": "2026-06-01",
  "expected_sources": ["employee-handbook.md"],
  "expected_keywords": ["10 天"],
  "forbidden_phrases": [],
  "reference_answer": "入职满 3 年不满 5 年的员工，年假为 10 天。"
}
```

`behavior` 支持 `answer`、`refuse`、`resist_injection`。题目分布：HR 6、安全 5、IT 2、采购 3、客服 2、跨文档 1、安全拒答/注入 2，共 21 题。

## 实战案例：中文数字引发的误拒答

新增边界用例 `q021: “三年年假多少天？”`，文档原文是“满 3 年”。初版词法匹配对中文数字和阿拉伯数字敏感，覆盖率下降导致误拒答。

修复没有采用“短问题直接跳过覆盖率”这种削弱安全的做法，而是：

1. 在 [app/rag/query.py](app/rag/query.py) 将中文数字归一化为阿拉伯数字；
2. 在 tokenizer 中压缩“数字 + 单位”的空格（`3 年` → `3年`），使查询和文档词项对齐；
3. 保持覆盖率和引用审计不被绕过；
4. 由 Auditor 智能体统一承担拒答/引用/时效审计，移除评测脚本里的重复逻辑。

修复后 21 题全部通过。

## 安全设计

- **直接注入**：命中“忽略之前指令 / 打印系统提示 / 越狱”等模式时，在检索前返回安全拒答。
- **间接注入**：检索内容按句清洗，移除 HTML 注释、上下文分隔符、特殊 token 和指令覆盖句。
- **生成约束**：系统提示要求只依据资料回答、引用编号必须来自资料、资料不足时拒答。
- **引用校验**：生成后移除不存在的引用编号，只保存真实候选块的引用。
- **Auditor 自查**：引用、时效、跨任务覆盖、安全标记四项检查。
- **经验复用保护**：相同问题、相同日期、上下文哈希一致，且复用前重新审计。
- **默认不出网**：默认 `extractive` 生成器完全本地运行。

当前项目**不是**生产级安全边界：没有用户认证、租户隔离、文档级 ACL、速率限制、PII 自动脱敏和密钥托管。接生产前必须补齐。

## 依赖、License 与漏洞审计

- 运行时审计 24 个依赖：**0 个已知 CVE**。
- 全依赖（含 dev/security）67 个依赖：**0 个已知 CVE**。
- Bandit 对 `app/` 的静态扫描：**0 findings**。
- 依赖 License：无 GPL/AGPL/LGPL；运行时全部为 MIT / Apache-2.0 / BSD / PSF。
- `certifi` 的 MPL-2.0 只出现在开发依赖中，不进入运行时镜像。

完整报告与复现命令见 [security-audit.md](docs/security-audit.md)；原始报告包括 [pip-audit-runtime.json](docs/audit/pip-audit-runtime.json)、[pip-audit-all.json](docs/audit/pip-audit-all.json)、[bandit.json](docs/audit/bandit.json) 和 [dependency-licenses-all.json](docs/audit/dependency-licenses-all.json)。

```bash
# 安装审计工具并复跑
uv sync --extra security
uv run pip-audit
uv run bandit -r app -ll
uv run pip-licenses --format=markdown
```

## 目录结构

```text
enterprise-rag-quality-lab/
├── app/
│   ├── main.py              # FastAPI app factory / lifespan
│   ├── config.py            # 环境变量配置
│   ├── schemas.py           # Pydantic 契约
│   ├── db.py                # SQLite schema 与连接管理
│   ├── agents/              # 智能组织
│   │   ├── coordinator.py   # 任务分解与调度
│   │   ├── retriever_agent.py
│   │   ├── reasoner.py
│   │   ├── auditor.py
│   │   ├── experience.py    # 经验沉淀/复用/反馈
│   │   ├── evolver.py       # 受控评测进化
│   │   └── orchestrator.py  # 组织运行时
│   ├── api/                 # health / documents / chat / runs / eval / organization
│   ├── services/            # 文档与聊天编排
│   ├── core/                # cache: Redis/Null/fail-open/singleflight
│   ├── knowledge/           # loader / splitter / embeddings / retriever
│   ├── rag/                 # pipeline / prompt / generator / query
│   └── evaluation/          # dataset / evaluator / report
├── data/sample_docs/        # 7 篇模拟企业文档（含一份历史版本）
├── eval/
│   ├── dataset.jsonl        # 21 道固定问题
│   ├── run_eval.py
│   └── results/baseline.json
├── tests/                   # 73 个 API / RAG / 版本 / 摄入 / PDF / 审计 / 缓存 / 相关性 / 智能组织 / 注入测试
├── scripts/ingest.py
├── scripts/cache_cli.py     # SCAN + UNLINK 清理缓存 namespace
├── docs/                    # 架构、实验日志、安全审计
├── docs/audit/              # License / CVE / Bandit 原始报告
├── Dockerfile               # python:3.11-slim 非 root 镜像
├── docker-compose.yml       # 本地 redis:7-alpine（无持久化）
├── .dockerignore
└── .github/workflows/ci.yml # lint + test + security + container
```

## 配置

复制 `.env.example` 为 `.env` 即可覆盖默认值。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `RAG_LAB_DATABASE_PATH` | `data/rag_lab.db` | SQLite 路径 |
| `RAG_LAB_AUTO_INGEST_SAMPLE_DOCS` | `true` | 启动时同步示例语料 |
| `RAG_LAB_CHUNK_SIZE` | `760` | 切块字符预算 |
| `RAG_LAB_TOP_K` | `5` | 默认检索深度 |
| `RAG_LAB_MIN_RETRIEVAL_SCORE` | `0.22` | 最低混合相关性 |
| `RAG_LAB_MIN_QUERY_COVERAGE` | `0.30` | 问题词项最低覆盖率 |
| `RAG_LAB_EMBEDDING_DIMENSION` | `384` | 哈希向量维度 |
| `RAG_LAB_GENERATOR_PROVIDER` | `extractive` | `extractive` 或 `openai` |
| `RAG_LAB_EXPERIENCE_REUSE_ENABLED` | `true` | 是否允许经验复用 |
| `RAG_LAB_EXPERIENCE_MIN_QUALITY` | `0.75` | 进入经验库的最低质量分 |
| `RAG_LAB_MAX_DOCUMENT_BYTES` | `2000000` | 文本单文件字节上限 |
| `RAG_LAB_MAX_PDF_BYTES` | `20000000` | PDF 单文件字节上限 |
| `RAG_LAB_MAX_PDF_PAGES` | `500` | 文本型 PDF 页数上限 |
| `RAG_LAB_OPENAI_API_KEY` | 空 | 仅 `openai` 模式需要 |

## 测试与 CI

```bash
python -m ruff check .
python -m pytest --cov=app --cov-report=term-missing
```

当前测试：73 项、覆盖率 81.7%，覆盖 API CRUD、聊天引用、拒答、运行轨迹、切块、检索、21 题评测、制度版本、中文数字、chunk 元数据透传、代码块/GBK/英文分句、PDF 文本提取与上传、多要素 PDF 问答、原始非法引用审计、fingerprint 判重、版本快照、句子级 quote、经验跨天复用、Redis 三层缓存（命中/未命中/TTL/序列化/降级/冷却/SCAN 清理/singleflight）、缓存开关对照评测、宽泛假期澄清、答案相关性审计、智能体协作和注入防护。CI 使用锁定依赖在 Python 3.12 上执行 lint、80% 覆盖率门禁、基线回归门禁、依赖漏洞扫描、License 检查、Bandit 和 Docker 健康检查。

> 如果受限 Windows 沙箱阻止 pytest 创建临时目录，可预先创建 `data/pytest-runtime` 并设置 `RAG_LAB_TEST_DIR` 指向它。

## 设计取舍

- **SQLite + 全量扫描**：当前语料规模小，透明和可复现比向量数据库更重要；接口可替换为 pgvector、Milvus。
- **哈希向量**：离线、确定性、零成本，但同义改写能力有限。
- **抽取式生成**：可控、可解释，是检索质量的上界近似和 LLM 对照。
- **经验复用只对单跳问题开放**：多时点答案的每个引用有不同审计日期，当前不冒险复用。
- **进化只在固定评测集搜索**：属于回归实验，不是生产自动调参。
- **固定评测集**：便于 CI，但会过拟合；必须增加留出集和人工评审。

## 已知限制

- 小语料、固定问题，基线接近满分，不能外推到开放域。
- PDF 只支持可选中文本；扫描件没有 OCR，复杂表格/双栏排版没有版面重建。
- PDF 当前记录 `page_count`，但 chunk 没有逐页页码；逐页引用需要扩展 page metadata。
- 中文检索使用字符 bigram，近义改写和跨语言能力有限。
- 经验复用基于标准化问题精确匹配，尚未做语义近邻召回。
- 进化搜索使用同一评测集，存在过拟合风险。
- 没有认证、多租户、文档级 ACL 和 PII 脱敏。
- 审计质量分是规则分，不是语义 judge。

## Roadmap

1. 经验库做语义近邻召回，并支持多时点经验的逐引用审计。
2. 增加 100+ 难例：同义改写、多跳、冲突版本、干扰文档。
3. 接入 LLM-as-judge 与人工评审，补充句子级归因。
4. 文档 ACL、租户隔离、PII 脱敏和限流。
5. 生产化：迁移、容器、指标导出、配置回滚。

## License

[MIT](LICENSE)
