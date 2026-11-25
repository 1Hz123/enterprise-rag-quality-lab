# 企业制度 RAG 语料：50 篇中国政府网公开政策文件

## 目录结构

- `corpus/md/`：20 篇 Markdown 原文 + 10 篇 ODT 的 Markdown 镜像（共 30 个 .md）
- `corpus/txt/`：20 篇纯文本
- `corpus/odt/`：10 篇 OpenDocument Text（.odt）
- `corpus/questions.csv`：每篇文档一道题，含参考答案和关键词
- `../eval/corpus_questions.jsonl`：可直接给现有评测脚本用的 50 条数据集

## 说明

50 篇是去重后的独立文档；ODT 另存了 Markdown 镜像，所以 `corpus/` 下用于索引的是 50 篇。
当前 loader 的 `SUPPORTED_SUFFIXES` 是 `.md/.markdown/.txt/.pdf`，ODT 不会被索引；
要跑 RAG 请使用 `md/`、`txt/` 里的文件，或先给 loader 增加 ODT 解析。

所有正文来自中国政府网公开政策文件，原始链接写在每篇的 `source`/`来源` 字段。
文件按“20 md + 20 txt + 10 odt”分配，题目与参考答案由脚本从正文抽取，建议人工抽检。
`odf` 这里采用 OpenDocument 的文本格式 `.odt`；它不是纯文本，当前 loader 不直接解析，因此每篇都额外生成了同名 `.md` 镜像。
`data/corpus_50_docs.zip` 是同一套文件的打包备份（61 个条目：30 md + 20 txt + 10 odt + questions.csv）。

## 自检结果

- `load_directory('data/corpus')`：50 篇（README 放在 `data/` 下，避免被当成第 51 篇文档）。
- `load_dataset('eval/corpus_questions.jsonl')`：50 条全部通过 `EvalCase` 校验。
- 检索命中：50 道题 Top-5 命中 50/50，MRR 0.975，引用精确率 0.98，非法引用 0。
- 现成 extractive 基线完整评测：30/50 通过（60%）；主要卡在严格关键词召回，属于题目偏难的基线表现，不是检索不到文档。
