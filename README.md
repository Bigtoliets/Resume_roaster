# 简历诊断 Agent 平台

大学生求职时最大的问题不是"不会写简历"，而是**不知道自己离目标岗位差多少**。
这个项目把真实招聘语料、红蓝对抗面试官、可解释的审核收敛机制拼成一条流水线：

> 上传简历（PDF 或粘贴文本）+ 填目标岗位 → 检索真实 JD / 面经 → 技术面试官与 HR 面试官多轮互相反驳
> → Supervisor 审核必查项覆盖率后收敛 → 输出**一份纯文本诊断报告**（判决 / 致命弱点 / 逐句改写 / 12 周路线图）

它同时是一个**可配置的 Agent 平台**：模型、Prompt、Tool、知识库、Skill、Agent 都能在页面上改，
简历诊断只是平台上的一个 Scene。

完整设计文档：[`docs/agent-platform-plan.md`](docs/agent-platform-plan.md)

---

## 架构一览

```text
前端 Vue 3  ──── REST + SSE ────  FastAPI  ──┬── ReAct 主循环（单节点：模型自主决定调哪个工具）
（卷宗风格 UI）                              ├── 工具：知识库检索 / 面试官 / 终裁官 / HTTP / PDF
                                             ├── LLM 网关（OpenAI 兼容 + function calling）
                                             ├── Milvus 混合检索（dense + sparse，RRF 融合）
                                             └── Langfuse 观测（trace 小图标跳转）
                        MySQL（配置 / 会话 / 消息 / tool_calls）
```

**编排模型（一句话）**：**只有一个主节点（ReAct 循环），其他能力全部是工具。**
技术面试官、HR 面试官、终裁官、知识库检索都是「工具」——有模型的工具自带 Prompt，
无模型的工具选一个后端注册能力。页面配什么，运行时就用什么。

**外部服务都部署在服务器上，本机不需要 Docker**：本地只跑 FastAPI 与 Vite，
MySQL / Milvus / Langfuse 通过 `.env` 里的地址接入。

---

## 快速开始

### 0. 检查环境

```bash
python scripts/check_env.py
```

零依赖脚本，会探测 MySQL、Milvus 端口与 Langfuse health + 密钥。全绿再继续。

### 1. 后端

```bash
cd backend
python -m pip install -e ".[dev]"          # 国内可加 -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m app.db.init_db                   # 按 ORM 建表（正式迁移用 Alembic）
python -m scripts.seed_demo                # 写入演示配置：2 模型 / 7 Prompt / 6 Agent
uvicorn app.main:app --reload --port 8000
```

启动后打开 <http://127.0.0.1:8000/docs> 看接口，<http://127.0.0.1:8000/api/v1/system/status> 看依赖状态。

### 2. 前端

```bash
cd frontend
npm install          # 已提供 pnpm-workspace.yaml，用 pnpm 亦可
npm run dev
```

打开 **<http://localhost:5173>**（Vite 默认绑定 IPv6 `::1`，写 `127.0.0.1` 会连不上）。

### 3. 语料与向量库

```bash
# 建 collection（4 字段：id / content / dense / sparse[BM25 自动生成]）与索引，幂等
python scripts/init_collections.py

# 导入语料（JSONL → id 生成 → 元数据抬头 → 双向量 → Milvus）
python scripts/ingest.py --jd data/sample/jd.jsonl

# 质检卡口（格式 / 岗位族映射 / 广告帖 / Milvus 行数）
python scripts/check_corpus.py --jd data/sample/jd.jsonl --check-milvus

# 检索命中测试：确认"写完真的能查到"
python scripts/probe_retrieval.py "Flink checkpoint 状态管理与反压"
```

语料由你自行维护：把 JD / 面经粘进 `data/*.jsonl`（只强制 `doc_id` / `job_family` / `raw_text`），
再执行上面的导入脚本。真实语料放 `data/local/`（已在 `.gitignore` 中），脱敏样例放 `data/sample/`。

### 4. 端到端冒烟（推荐每次改动后跑）

```bash
python scripts/make_demo_resume.py        # 生成一份带典型问题的演示简历 PDF
python scripts/smoke_react.py          # 上传简历 → 一条消息 → 看思考/行动/观察与最终回答
```

脚本会打印每一步工具调用与观察结果。实测：Agent 自主完成
`读简历 → 检索真实 JD/面经 → 技术面试官 → HR 面试官 → 终裁官` 五步，13 个步骤、约 48 秒。

最终回答是 Markdown，页面上可直接复制；思考/行动/观察默认折叠，展开时按需拉取完整工具结果。

### 5. Langfuse（可选，但建议开）

Langfuse 是**独立部署**的，不在本项目的 compose 里。容器没起来时系统会自动降级：
报告中不出现 trace 链接，图标置灰，诊断照常完成。起来之后：

```bash
python scripts/check_langfuse.py          # 发一条真实 trace，UI 里能看到才算接通
```

接入方式是**一次 ReAct 回合一个 trace**，LLM 调用经 `langfuse.openai` 包装器自动采集 prompt / completion / token。

---

## 平台六个模块

| 页面 | 做什么 |
| --- | --- |
| 模型配置 | 多套 OpenAI 兼容配置；密钥 Fernet 加密、列表只显示掩码；带「测试连通性」 |
| Prompts | messages 行编辑器（增删/排序/换 role）+ 参数面板；变量从消息里自动检测；可渲染并试跑 |
| Tools | **页面注册 + 代码实现，两者只靠 JSON 打交道**：页面填三个 JSON（入参 / 出参 / 配置）+ 选一个 `impl_key`；命中不到代码就标「未实现」；测试场贴 JSON 直接跑，**不用先保存** |
| 知识库 | collection 下拉 + Top K + 岗位族前缀过滤；**检索行为可调**（RRF k / 召回倍数 / 片段长度 / 分数下限，D35）；检索测试支持多角度（多条用 `\|` 分隔）并标出每条是 `dense` / `bm25` / `both` |
| Skills | 独立文本：只写「什么条件下必须检查什么」，markdown 编辑，不引用其他资源 |
| Agents | 主循环配置：模型 + 系统提示词 + 人设 + Skills + 可用工具（未实现的工具会标红警告）+ 最大轮次；保存即生成版本快照 |
| 会话 | **选一个 Agent 对话**：思考/行动/观察可折叠（展开时按需拉完整工具结果），工具需确认时弹框批准 |

**关系模型**：Model / Prompt / Skill / KnowledgeBase 互相独立、可复用；
**Agent 是主循环配置**（模型 + 系统提示词 + 人设 + Skills + 可用工具 + 最大轮次）；
**Tool 是"页面实例 + 代码实现"**：`tools` 表那行决定怎么跑（三个 JSON + Prompt + 模型 + 超时 + 是否需人工确认），
`app/tools/<impl_key>.py` 决定能做什么（一个 `run(input, ctx)` 函数）。

**性能**：后端用 `cachetools.TTLCache(maxsize=500, ttl=60)` 缓存 DB 查询（读穿透、写后失效，`GET /api/v1/cache/stats` 看命中率）；前端对会话数据加 60s 缓存并记忆化 markdown 渲染——实测切换会话首次 66ms、切回已看过的 0 请求。

```
Agent   = 模型 + 系统提示词 + 人设 + Skills + 可用工具 + 最大轮次
Tool    = impl_key（指向代码实现）+ 入参 JSON + 出参 JSON + 配置 JSON
          +（有模型时）Prompt + 模型 + 超时 + 失败重试 + 是否需人工确认
Session = 绑定一个 Agent（可切换，只影响后续消息）
```

**关键设计**：后端只有**一个**通用 ReAct 引擎 + 一份**工具实现注册表**（`app/tools/`）。
新增一个工具 = 加一个 `<impl_key>.py`（`@tool(...)` 装饰的 `run(input, ctx)`）+ 在 `app/tools/__init__.py` 里 import；
重启后工具页的「实现」下拉自动多一项，不需要改前端。
工具函数契约是纯 JSON：`input = {"args": 模型传的, "config": 页面配的}`，
返回 `{"ok", "summary", "data"}`；平台不做参数过滤（页面那份入参 JSON 才是权威），
命中不到实现就标「未实现」，调用时返回一条观察结果而不是崩溃。

六个页面都是同一套工作台：左侧可搜索清单（新建 / 选中 / 复制），右侧专属编辑器，
右上角保存与删除（删除需二次确认），底部是与线上同入口的测试场。改完保存即生效，
被 Agent 引用的资源会拒绝删除并列出引用方。

---

## 目录结构

```text
backend/
  app/
    api/v1/        # 六类资源 CRUD + 测试场 + 会话 + SSE 诊断 + 系统状态
    core/          # 配置、JSON 日志、密钥加解密
    db/            # ORM 模型（12 张表）、会话、建表
    agent/         # ReAct 运行时：loop（主循环）/ approvals（人工确认）/ prompts（内置默认提示词）
    tools/         # 工具实现：一个 impl_key 一个模块 + base（类型）/ registry（注册与唯一执行入口）/ _llm_runner（有模型工具的共享辅助）
    llm/           # OpenAI 兼容网关：JSON 强校验 + 重试 + 宽松兜底
    services/      # 引用关系与版本快照、会话历史压缩
    vector/        # BGE-M3 双向量编码、Milvus 混合检索
  scripts/         # seed_demo.py（演示配置）、migrate_tools_package.py（工具包迁移）
frontend/
  src/api/         # 请求封装 + SSE 客户端
  src/components/  # 工作台、表单控件、步骤时间线、主题化对话框
  src/views/       # 六个配置页 + 会话页
  src/utils/clone.ts     # 深拷贝（memo：不能用 structuredClone 处理响应式对象）
  src/styles/theme.css   # 设计语言：面试庭审卷宗
scripts/check_env.py
data/              # 语料 source of truth（样例入库，真实语料不入库）
docs/agent-platform-plan.md
```

## 脚本清单

| 脚本 | 用途 | 是否依赖外部服务 |
| --- | --- | --- |
| `scripts/check_env.py` | 端口级连通性检查（零依赖） | 否（只探测端口） |
| `scripts/migrate_react.py` | ReAct 改造迁移：补列、建 `tool_calls`、删除场景两张表 | 是 |
| `backend/scripts/migrate_tools_package.py` | 工具包迁移（幂等）：加 `impl_key`、保证三个 JSON 列存在、删冗余列、用代码默认值兜底 | 是 |
| `backend/scripts/migrate_kb_retrieval.py` | 知识库检索参数化迁移：加 `rrf_k` / `recall_multiplier` / `snippet_chars` / `min_score` 四列 | 是 |
| `backend/scripts/rebuild_jd.py` | 按 `【岗位 N】` 重建 jd 语料（一条岗位一行）——**会写入向量库**，默认先 `--dry-run` | 是 |
| `scripts/smoke_react.py` | ReAct 回合冒烟：上传简历 → 一条消息 → 观察思考/行动/观察与最终回答 | 是 |
| `scripts/check_services_deep.py` | 凭据级校验：登录 MySQL、连 Milvus、查版本 ≥2.4 | 是 |
| `scripts/check_embedding.py` | 校验 embedding 端点可用、维度是否匹配 | 是 |
| `scripts/init_collections.py` | 建 `jd` collection（含 `bm25_func`）与索引（幂等，可 `--recreate`） | 是 |
| `scripts/ingest.py` | JSONL → dense + content → Milvus（sparse 由 BM25 Function 生成；`--dry-run` 可离线看切分） | 写入时是 |
| `scripts/check_corpus.py` | 语料质检卡口（FAIL 即退出码非 0） | `--check-milvus` 时是 |
| `scripts/probe_retrieval.py` | 给定 query 看混合检索命中与分数 | 是 |
| `scripts/make_demo_resume.py` | 生成带典型问题的演示简历 PDF | 否 |
| `scripts/smoke_crud.py` | 六类资源 CRUD 冒烟 + 删除保护（26 项断言） | 是 |
| `scripts/check_langfuse.py` | 发一条 trace 验证可观测接通 | 是 |

---

## 设计语言

"面试庭审卷宗"：暖纸底 + 墨黑重字 + 朱红印章；标题与报告正文用宋体系衬线，
数据与标签用等宽，报告做成一份可导出的判决书——一句话判决配朱红印章、
致命弱点按严重度编号、每条结论下面挂着简历原句与 JD 原文的引用块、
辩论过程是一份可展开的庭审记录。

---

## 已知边界（一期明确不做）

OCR / 扫描件 · JD 自动爬取 · Elasticsearch · 登录与多租户 · **PDF 导出与打印视图（改为纯文本交付）** ·
LLM 自主 function calling · 可视化 DAG 拖拽画布 · 任务队列（诊断走请求内 SSE，断连即重跑）。

## 二期

语料自动更新 · OCR · 多用户与权限 · 任务队列与断点续传 · 报告模板美化 ·
Prompt A/B 对比 · 评测集与自动打分 · Skill 市场。
