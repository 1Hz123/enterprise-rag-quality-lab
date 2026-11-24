# 依赖、License 与漏洞审计

审计日期：2026-11-14；代码复核：2026-11-24
项目版本：1.2.1
范围：`uv.lock` 锁定的 Python 依赖 + `app/` 源码
环境：Windows、Python 3.12.5、uv 0.13.1

## 1. 工具与命令

| 工具 | 版本 | 用途 |
|---|---|---|
| `pip-audit` | 2.10.1 | PyPI advisory 数据库依赖 CVE 扫描 |
| `bandit` | 1.9.4 | Python 源码静态安全扫描 |
| `pip-licenses` | 5.5.5 | 依赖 License 清单 |
| `uv` | 0.13.1 | 锁文件、隔离审计环境 |

复现：

```bash
uv lock
uv export --no-dev --no-emit-project --format requirements-txt --no-hashes \
  -o .audit/runtime-requirements.txt
uv export --all-extras --all-groups --no-emit-project --format requirements-txt \
  --no-hashes -o .audit/all-requirements.txt

uv venv .audit/venv
uv pip install --python .audit/venv/bin/python -r .audit/runtime-requirements.txt
uvx pip-audit -r .audit/runtime-requirements.txt --format json

uv venv .audit/allvenv
uv pip install --python .audit/allvenv/bin/python -r .audit/all-requirements.txt
uvx pip-audit --path .audit/allvenv/lib/python3.12/site-packages --format json

uvx bandit -r app -ll -f json -o docs/audit/bandit.json
uvx --from pip-licenses pip-licenses --python .audit/allvenv/bin/python \
  --format=json --with-urls -o docs/audit/dependency-licenses-all.json
```

Windows 路径把 `/bin/python` 换成 `\Scripts\python.exe`，site-packages 换成 `Lib\site-packages`。

## 2. License 结论

### 汇总

| License 类型 | 包数量（全依赖） | 风险判断 |
|---|---:|---|
| MIT / MIT License | 29 | 宽松 |
| Apache-2.0 / Apache Software License | 14 | 宽松，含专利授权 |
| BSD-3-Clause / BSD-2-Clause / BSD License | 11 | 宽松 |
| Apache-2.0 OR BSD-2-Clause | 1 | 双许可，宽松 |
| PSF-2.0 / PSF License | 2 | 宽松 |
| MPL-2.0（certifi） | 1 | 文件级弱 copyleft，仅开发依赖 |

> v1.2 新增 `redis`（MIT）、`hiredis`（BSD-3-Clause）和开发依赖 `fakeredis`（BSD-3-Clause）。上面的包数量来自 v1.1 的 License JSON 快照；新增依赖不改变“无 GPL/AGPL/LGPL”的结论，完整重新生成 License JSON 仍需在镜像源恢复后执行。

**结论：**

- 没有 GPL、AGPL、LGPL 依赖。
- 运行时 25 个包（含 `redis` MIT、`hiredis` BSD-3-Clause）全部为宽松许可。
- `certifi`（MPL-2.0）只通过开发依赖 `httpx` 引入，不进入运行时镜像。
- v1.2 新增的 `fakeredis` / `sortedcontainers` 仅为开发测试依赖，不是运行时依赖。
- 项目自身为 MIT License。
- 没有修改第三方源码；如分发容器，保留依赖包的 License/Notice 即可。

### 运行时直接依赖

| 包 | 版本 | License |
|---|---|---|
| fastapi | 0.162.0 | MIT |
| pydantic | 2.14.2 | MIT |
| pydantic-settings | 2.16.2 | MIT |
| python-multipart | 0.0.33 | Apache-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |
| redis | 8.1.0 | MIT |
| hiredis | 3.5.0 | BSD-3-Clause |

完整清单和 URL 见 [dependency-licenses-all.json](audit/dependency-licenses-all.json) 与 [dependency-licenses.json](audit/dependency-licenses.json)。

## 3. 漏洞扫描结论

### 运行时依赖

```text
dependencies: 25
known vulnerabilities: 0
fixes: 0
```

报告：[pip-audit-runtime.json](audit/pip-audit-runtime.json)

### 全依赖（含 dev / security）

```text
dependencies: 67
known vulnerabilities: 0
fixes: 0
```

报告：[pip-audit-all.json](audit/pip-audit-all.json)

### 修复记录

初次全依赖扫描发现：

| 包 | 版本 | 漏洞 | 修复 |
|---|---|---|---|
| pytest | 8.4.2 | PYSEC-2026-1845 / CVE-2025-71176（开发环境 tmp 目录问题） | 升级到 pytest 9.2.0 |

升级后 70 项测试全部通过，全依赖 CVE 归零。

## 4. Bandit 源码扫描

最终结果：**0 findings**。

首次扫描有 4 个 medium/low 发现，均已处理：

| 规则 | 位置 | 处理 |
|---|---|---|
| B608 动态 SQL | 经验库上下文哈希查询 | 改为固定 SQL + `json_each(?)` 参数化 |
| B608 动态 SQL | 文档列表 `as_of` 查询 | 拆成静态参数化 SQL |
| B310 URL open | OpenAI 兼容生成器 | 强制校验 `http/https`，并加 justification `# nosec B310` |

最终报告：[bandit.json](audit/bandit.json)

## 5. 容器化

- [Dockerfile](../Dockerfile)：`python:3.11-slim`，非 root 用户，`EXPOSE 8000`，`HEALTHCHECK`。
- [.dockerignore](../.dockerignore)：排除 Git、虚拟环境、数据库、测试、文档和审计临时目录。
- CI 增加 `container` job：构建镜像、启动容器、轮询 `/health`、失败时输出日志、最后停止容器。

本地 Docker daemon 在本次审计时未运行，因此没有完成本机镜像启动；Dockerfile 和 smoke test 已纳入 CI。构建命令：

```bash
docker build -t enterprise-rag-quality-lab:1.2.1 .
docker run --rm -p 8000:8000 enterprise-rag-quality-lab:1.2.1
```

## 6. CI 安全门禁

[ci.yml](../.github/workflows/ci.yml) 现有三个 job：

1. `quality`：Python 3.11/3.12，Ruff、pytest、评测质量门禁。
2. `security`：`pip-audit`、`pip-licenses` copyleft 拦截、Bandit。
3. `container`：Docker 构建与 `/health` 冒烟测试。

## 7. 残余风险与边界

- 漏洞数据库是时点结果，CI 每次构建会重新扫描。
- License 信息来自已安装包的元数据；极少数包可能只提供 classifier 而非 SPDX 标识。
- 本报告不包含基础设施 CVE、容器基础镜像 CVE 和依赖供应链投毒检测；生产环境应增加镜像扫描（Trivy/Grype）、SBOM、签名和镜像仓库策略。
- 未执行 DAST、密钥扫描和渗透测试。
- 当前项目仍是质量实验室，不包含认证、租户隔离、PII 脱敏和限流。
