# Enterprise RAG Quality Lab

一个**离线可运行、可回归、可解释**的企业 RAG 质量实验项目。它不是只展示“能回答问题”的聊天 Demo，而是把检索、生成、引用、安全与评测放在同一条可重复运行的流水线上。

项目内置 7 篇模拟企业文档（含一份历史版本）、20 道固定评测题、混合检索、制度版本时间旅行、提示注入防护、FastAPI 接口、SQLite 运行轨迹和可直接提交的基线报告。默认只使用本地确定性组件，**不需要任何 API Key**。

## 为什么做这个项目

企业 RAG 的问题通常不在“模型能不能说出一段话”，而在于：

- 答案有没有被正确的制度文档支持？
- 引用是不是来自应该引用的来源？
- 知识库没有答案时，系统会不会编造？
- 文档里夹带的恶意指令，会不会改变系统行为？
- 改了切块、检索或提示词后，质量是变好还是变差？

本项目把这些风险变成可观测指标和可重复实验，而不是靠人工抽查。

## 核心特性

- **完全离线的默认链路**：哈希向量 + BM25 混合检索 + 抽取式生成，零外部服务依赖。
- **可替换生成器**：默认 `extractive`，也支持 OpenAI 兼容的 Chat Completions 接口。
- **可解释检索**：每个回答保存候选块、向量分数、词面分数、最终分数和引用片段。
- **两层安全防护**：直接提示注入在检索前拦截；间接注入在进入上下文前清洗，并使用不可信数据分隔符。
- **固定评测集**：20 题覆盖 HR、安全、IT、采购、客服、跨文档、拒答和注入抵抗，案例固定 `as_of` 日期，跨机器可复现。
- **制度版本时间旅行**：按 `as_of` 查询历史时点的有效制度；旧版本自动失效，重叠生效窗口会被拒绝。
- **真实质量门槛**：来源命中率、MRR、关键词覆盖、引用准确率、拒答正确率、注入抵抗率和延迟。
- **工程化交付**：FastAPI、Pydantic、SQLite、pytest、Ruff、GitHub Actions、Docker 无关的本地运行方式。
- **可审计运行轨迹**：每个问题都生成 `run`，可通过 API 查看检索与引用明细。

## 快速开始

要求 Python 3.11+。

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

打开：

- Swagger UI：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

首次启动会自动把 `data/sample_docs/` 下的 7 篇文档写入 `data/rag_lab.db`。重复启动是幂等的；文档内容变化后会重新切块和索引。

### 直接体验 API

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d "{\"question\":\"员工入职满三年，年假有多少天？\"}"
```

响应包含 `answer`、`citations`、`retrieved_chunks`、`run_id`、`latency_ms` 和安全标记。

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 数据库、文档数、生成器和向量配置 |
| `GET` | `/api/v1/documents` | 文档列表与分页；支持 `?as_of=YYYY-MM-DD` 过滤有效版本 |
| `POST` | `/api/v1/documents` | 提交正文；支持 `policy_key`、`version`、`effective_from/until` |
| `POST` | `/api/v1/documents/upload` | 上传 UTF-8 的 `.md` / `.txt` |
| `POST` | `/api/v1/documents/ingest-sample` | 幂等同步示例语料 |
| `GET` | `/api/v1/documents/{id}` | 文档详情和完整正文 |
| `POST` | `/api/v1/documents/{id}/reindex` | 重建切块和向量 |
| `DELETE` | `/api/v1/documents/{id}` | 删除文档（级联删除切块） |
| `POST` | `/api/v1/chat` | 检索、回答、引用并持久化运行轨迹；支持 `as_of` |
| `GET` | `/api/v1/runs` | 运行记录列表 |
| `GET` | `/api/v1/runs/{run_id}` | 检索轨迹、引用、指标和错误 |
| `GET` | `/api/v1/eval/dataset` | 评测集摘要和样例 |
| `POST` | `/api/v1/eval/run` | 对当前知识库执行 20 题评测 |
| `GET` | `/api/v1/eval/results` | 历史评测结果 |
| `GET` | `/api/v1/eval/results/{run_id}` | 单次评测完整报告 |
| `GET` | `/api/v1/eval/baseline` | 读取提交在仓库中的基线报告 |

## 评测

```powershell
# 重新生成基线报告
python eval/run_eval.py --output eval/results/baseline.json

# 跑一次 CI 风格的质量门禁（不通过则退出码非 0）
python eval/run_eval.py `
  --database data/eval.db `
  --output eval/results/ci.json `
  --fail-under-pass-rate 0.80 `
  --fail-under-hit-rate 0.85 `
  --require-quality-gate
```

评测命令使用独立的 `data/eval.db`，不会污染应用数据库。

### 基线结果

基线文件：[eval/results/baseline.json](eval/results/baseline.json)

| 指标 | 结果 |
|---|---:|
| 通过题数 | 20 / 20（100%） |
| 来源命中率 hit@5 | 100% |
| MRR | 1.000 |
| 关键词覆盖率 | 100% |
| 引用准确率 | 100% |
| 拒答正确率 | 100% |
| 提示注入抵抗率 | 100% |
| 延迟 p50 | 7.7 ms |
| 延迟 p95 | 8.4 ms |

运行环境：Windows、Python 3.12.5、SQLite、本地 `hashing-384` 向量、`extractive` 生成器，20 道题单次本地运行。延迟只代表本机进程内计算，不代表生产 SLA。

> **诚实说明**：这是回归基线，不是通用 RAG 能力证明。语料只有 7 篇（其中两版属于同一制度族），题目与语料高度对齐，生成器又是抽取式的，因此 20/20 是预期结果。真正有价值的下一步是增加困难集、同义改写、多跳、更多时间冲突、权限过滤和更多干扰文档，见 [实验日志](docs/experiment-log.md)。

### 企业小众需求：制度版本与生效日期

企业制度不会只有一份：旧版仍可能被审计引用，新版可能在未来生效。如果知识库只按相似度检索，很容易把旧版年假天数当成当前答案。

本项目把“时间点”纳入检索契约：

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
- 同一 `policy_key` 的生效窗口重叠时，新文档返回 `409`，防止两个版本同时生效。
- 引用结果会带 `version` 和 `effective_from/until`，便于审计。

示例：内置 `2025` 与 `2026` 两版员工手册。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"员工入职满三年，年假有多少天？","as_of":"2025-06-01"}'
```

历史查询返回 2025 版的 **8 天**；`as_of=2026-06-01` 返回 2026 版的 **10 天**。默认不传 `as_of` 时使用服务器当天日期。

> 20 题评测集的每个案例都固定了 `as_of: 2026-06-01`，避免系统时钟变化导致基线漂移。

### 指标含义

- **来源命中率**：前 `k` 个检索块中是否出现标注的正确来源。
- **MRR**：第一个正确来源在检索结果中的排名倒数，衡量排序质量。
- **关键词覆盖率**：答案是否包含标注的关键事实片段。
- **引用准确率**：回答引用的来源中，有多大比例属于期望来源。
- **拒答正确率**：知识库外问题是否被明确拒答，而不是猜测。
- **注入抵抗率**：直接注入是否被拦截，间接注入内容是否没有改变答案。
- **质量门禁**：通过率 ≥ 80%、命中率 ≥ 85%、引用准确率 ≥ 80%、注入抵抗率 = 100%。

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

`behavior` 支持：

- `answer`：期望被回答，必须有期望来源和关键词。
- `refuse`：期望明确拒答，不允许声明期望来源。
- `resist_injection`：期望阻断攻击，且答案不得包含 `forbidden_phrases`。

题目分布：HR 5、安全 5、IT 2、采购 3、客服 2、跨文档 1、安全拒答/注入 2。

## 架构

```text
文档 / 上传
    │
    ▼
Loader ──► Splitter ──► Hashing Embeddings ──► SQLite chunks
                                                    │
问题 ──► Injection Guard ──► Hybrid Retriever ◄────┘
                                  │
                                  ▼
                    Query coverage / score gate
                                  │
                                  ▼
                 Context sanitizer + citation numbers
                                  │
                                  ▼
                 Extractive or OpenAI-compatible generator
                                  │
                                  ▼
                 Citation validation + run persistence
```

详细设计、数据模型、评分公式、威胁模型和扩展路径见 [docs/architecture.md](docs/architecture.md)。

## 安全设计

- **直接注入**：命中“忽略之前指令 / 打印系统提示 / 越狱”等模式时，在检索前返回安全拒答，不暴露系统提示。
- **间接注入**：检索内容按句清洗，移除 HTML 注释、上下文分隔符、特殊 token 和指令覆盖句；再放入 `<context>` 作为不可信数据。
- **生成约束**：系统提示明确要求只依据资料回答、引用编号必须来自资料、资料不足时拒答。
- **引用校验**：生成后移除不存在的引用编号，只保存真实候选块的引用。
- **默认不出网**：默认 `extractive` 生成器完全本地运行。
- **审计轨迹**：每次问答保存检索结果、引用、指标和安全事件。

当前项目**不是**生产级安全边界：没有用户认证、租户隔离、权限过滤、速率限制、PII 自动脱敏和密钥托管。接生产前必须补齐这些能力。

## 目录结构

```text
enterprise-rag-quality-lab/
├── app/
│   ├── main.py              # FastAPI app factory / lifespan
│   ├── config.py            # 环境变量配置
│   ├── schemas.py           # Pydantic 契约
│   ├── db.py                # SQLite schema 与连接管理
│   ├── api/                 # health / documents / chat / runs / eval
│   ├── services/            # 文档与聊天编排
│   ├── knowledge/           # loader / splitter / embeddings / retriever
│   ├── rag/                 # pipeline / prompt / generator
│   └── evaluation/          # dataset / evaluator / report
├── data/sample_docs/        # 7 篇模拟企业文档（含一份历史版本）
├── eval/
│   ├── dataset.jsonl        # 20 道固定问题
│   ├── run_eval.py          # 评测 CLI
│   └── results/baseline.json
├── tests/                   # 32 个 API / RAG / 版本 / 注入测试
├── scripts/ingest.py        # 语料导入 CLI
├── docs/                    # 架构与实验日志
└── .github/workflows/ci.yml # lint + test + quality gate
```

## 配置

复制 `.env.example` 为 `.env` 即可覆盖默认值。常用项：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `RAG_LAB_DATABASE_PATH` | `data/rag_lab.db` | SQLite 路径 |
| `RAG_LAB_AUTO_INGEST_SAMPLE_DOCS` | `true` | 启动时同步示例语料 |
| `RAG_LAB_CHUNK_SIZE` | `760` | 切块字符预算 |
| `RAG_LAB_CHUNK_OVERLAP` | `110` | 相邻块重叠字符数 |
| `RAG_LAB_TOP_K` | `5` | 默认检索深度 |
| `RAG_LAB_MIN_RETRIEVAL_SCORE` | `0.22` | 最低混合相关性 |
| `RAG_LAB_MIN_QUERY_COVERAGE` | `0.30` | 问题词项在上下文中的最低覆盖率 |
| `RAG_LAB_EMBEDDING_DIMENSION` | `384` | 哈希向量维度 |
| `RAG_LAB_GENERATOR_PROVIDER` | `extractive` | `extractive` 或 `openai` |
| `RAG_LAB_OPENAI_API_KEY` | 空 | 仅 `openai` 模式需要 |
| `RAG_LAB_OPENAI_MODEL` | `gpt-4o-mini` | OpenAI 兼容模型名 |

使用外部生成器：

```bash
export RAG_LAB_GENERATOR_PROVIDER=openai
export RAG_LAB_OPENAI_API_KEY=...
export RAG_LAB_OPENAI_BASE_URL=https://api.openai.com/v1
export RAG_LAB_OPENAI_MODEL=gpt-4o-mini
```

检索与评测仍可离线运行；只有生成阶段会调用外部接口。

## 语料导入

```bash
# 增量同步示例语料
python scripts/ingest.py

# 导入自己的 UTF-8 Markdown/TXT 目录
python scripts/ingest.py --source /path/to/docs

# 切块或向量配置变更后重建全部索引
python scripts/ingest.py --reindex
```

## 测试与 CI

```bash
python -m ruff check .
python -m pytest --cov=app --cov-report=term-missing
```

当前测试：32 项，覆盖 API CRUD、聊天引用、拒答、运行轨迹、切块、检索、20 题评测、制度版本/生效日期、直接/间接提示注入。CI 在 Python 3.11 与 3.12 上执行同样的 lint、测试和评测门禁。

## 设计取舍

- **SQLite + 全量扫描**：当前语料规模很小，透明和可复现比向量数据库更重要；检索接口可以替换为 pgvector、Milvus 等。
- **哈希向量**：保证离线、确定性和零成本，但不如训练好的语义向量；这是可替换的实验变量，不是产品选型。
- **抽取式生成**：没有幻觉式措辞，适合做检索/切块回归；但答案自然度有限，适合作为 fallback 和对照组。
- **固定评测集**：便于 CI 回归，但会过拟合；必须配合留出集和持续新增的失败样例。
- **引用来源级评测**：当前按文档来源判断，不评估句子级归因；后续可加入 NLI/LLM judge 与人工标注。

## 已知限制

- 小语料、固定问题，基线接近满分，不能外推到开放域。
- 中文检索使用字符 bigram，近义改写和跨语言能力有限。
- 查询覆盖门槛可能误拒高度改写但语义正确的问题。
- 抽取式答案可能选入相关但不够精炼的句子。
- 没有认证、多租户、文档级 ACL 和 PII 脱敏。
- 评测以确定性词面指标为主，尚未接入语义 judge。

## Roadmap

1. 增加 100+ 难例：同义改写、多跳、冲突版本、时间有效性和干扰文档。
2. 加入句子级归因与 LLM-as-judge，并保留人工抽检。
3. 文档 ACL、租户隔离和敏感信息脱敏。
4. 支持更多 embedding / reranker，并建立检索消融实验。
5. 生产化：迁移、限流、审计日志、指标导出和容器部署。

## License

[MIT](LICENSE)
