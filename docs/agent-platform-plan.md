# 简历诊断 Agent 平台 · 详细执行方案

> 版本：v1.0（对齐完成于 2026-09-21）
> 定位：本地一键启动、作品集级、按可上线标准分层的 **Agent 平台 + 简历诊断场景**
> 本文档即执行依据：所有决策已冻结，凡标 ⚠️「默认采纳待确认」的条目为方案推荐值，用户可单独否决。

---

## 0. 决策冻结清单

| # | 决策点 | 结论 |
| --- | --- | --- |
| D0 | 交付形态 | 本地单机部署（Docker Compose 一键起），作品集/Demo 级 |
| D1 | 检索层 | **只用 Milvus**，取消 Elasticsearch |
| D2 | 范围切分 | **简历应用优先**，平台通用配置能力随后补齐 |
| D3 | 平台与应用关系 | 简历诊断 = 平台上的一个 **Scene**（场景），非硬编码 |
| D4 | 模型来源 | **DeepSeek**，走 OpenAI 兼容协议；模型配置 = 同协议多套配置，不做厂商特判 |
| D4.1 | 工具调用 | 一期由 LangGraph 节点固定调用；不做 LLM 自主 function calling |
| D5 | 语料维护 | 开发者自己直接维护进 Milvus；仓库内 JSONL 为 source of truth + 幂等导入脚本 |
| D5.1 | 语料构成 | ① 企业招聘信息（JD）② 面经/求职经验 |
| D6 | ES 分工 | 不适用；Milvus scalar filter 承担硬性条件过滤 |
| D7.1 | 解析原则① | **解析节点只做事实抽取 + 可验证标记，不做价值判断** |
| D7.2 | 解析原则② | **每个字段必须带原文锚点（字符区间）** |
| D7.3 | 结构化 schema | 见附录 B（ResumeProfile） |
| D7.4 | OCR | **不做**；无文本层直接拒绝并提示导出文字版 |
| D8 | 辩论机制 | Supervisor 统一编排与裁决；轮次不固定，可提前终止 |
| D8.1 | 终止判据 | Supervisor 审核**必查项覆盖率**决定是否结束 |
| D8.2 | 轮次硬约束 | 最大轮次上限（默认 5）；第 1 轮后无条件继续 |
| D8.3 | 必查项清单 | 6 项：硬性门槛/技术深度/业务产出/岗位匹配/表达逻辑/可信度（Scene 可配置） |
| D9.1 | 报告结构 | 5 区块：一句话判决 / 门槛对照 / 致命弱点 Top3 / 逐条改写 / 学习路线图 |
| D9.2 | 「一段话」 | 指区块 ①，整份报告**不是**一段话 |
| D9.3 | 路线图周期 | **3 个月 / 12 周** |
| D9.4 | 报告交付形态 | **纯文本**：可整段复制、可下载 `.txt`（判决/弱点/改写/路线图/门槛对照/辩论纪要）。**不做 PDF 导出、不做打印视图**（2026-09-21 用户改定） |
| D24 | 简历输入形态 | 支持 **PDF 与纯文本**两种输入（上传 `.pdf`/`.txt`/`.md`，或直接粘贴文本） |
| D25 | 资源关系模型 | **Agent = 人设**（Model + Prompt + Persona + Skills）；**Scene = 依据什么 + 做什么**（语料 KB + 工具 Tool + 节点装配）；**Skill = 独立文本**；**Node = 绑定 Agent + 按节点覆盖 model/prompt/params/kb + 声明 tools**，见 §15.0 |
| D25.1 | 参数优先级 | `model.default_params` → `prompt.params` → `agent.params_override` → `binding.params_override`（后者覆盖前者） |
| D25.2 | 覆盖语义 | 覆盖字段为空 = 沿用上层默认；**不存在"显式清空"第三态** |
| D25.3 | 节点名 | 由代码定义，页面只能绑定不能增删；启动期强校验，写错即告警 |
| D26 | 会话入口 | **统一对话框 + 自动路由**：会话绑定路由器 Agent，每条消息先判意图再分派链路；路由结果落库不注入模型 |
| D26.1 | 链路定义 | **链路 = 图（代码注册）+ 场景档（数据）**；同一张图可有多份场景档；加一种风格零代码，加一种流程写一张图 |
| D26.2 | 通用对话链路 | 三节点 `router → chat_retrieve（可选检索）→ chat_respond（流式）`；检索与工具同样受场景档/节点声明控制 |
| D26.3 | 会话记忆 | **滚动摘要（增量 + 水位线）**：`sessions.summary` + `summary_upto_message_id`；注入 = 装配 system + 摘要 + 最近 N 条原文 |
| D26.4 | 诊断→追问 | 诊断结束追加一条 `type=text` 的**报告摘要**进对话历史，追问走轻量对话链路，不重跑诊断 |
| D26.5 | 会话页形态 | **只有一个会话页**（`/sessions` 与 `/sessions/:id` 同一组件）：左侧历史会话列表 + 右侧对话框、紧凑输入框、等待态（三点动画 + 阶段文案）、流式输出、路由标记、Langfuse trace 徽标（会话级 + 每条消息）、报告块与辩论折叠 |
| D27 | 查询缓存 | 后端用 **cachetools `TTLCache(maxsize=500, ttl=60)`** 做 DB 查询缓存：读穿透（命中直接返回，未命中查库并写缓存），**写路径先落库、成功后再删 key**；会话内容变更按 `session:{id}` 前缀批量失效；`GET /cache/stats` 看命中率。前端另加 60s 会话缓存（切回已看过的会话 0 请求）与 markdown 结果记忆化 |
| D28 | 交互弹窗 | **不使用原生 `confirm` / `prompt`**：统一走全局 `<DialogHost />`（`confirmDialog()` / `promptDialog()` 返回 Promise）。卷宗风格：遮罩+模糊、纸面卡片、朱红「慎」印章（危险操作）或墨色「问」、宋体标题、可校验输入、Esc 取消 / Enter 确认 / 自动聚焦。预览页 `preview/chat-ui.html` 内嵌同款实现 |
| D29 | 前端导航体验 | ① 路由分包**空闲预加载**（`router/prefetch.ts`）把 dev 模式的按需编译摊到启动阶段；② 常用接口**空闲预热**（sessions/scene/六类资源）；③ `useResource` 加 30s 模块级列表缓存，切回配置页不再白屏；④ 顶部 2px 朱红**切换进度条**，让"正在切页"可见 |
| D30 | 写操作体验 | **乐观更新 + 单请求**：新增/修改直接用响应更新本地列表，删除先本地移除再请求（失败回滚），每次操作由 2 次往返降到 1 次；同时移除吸顶栏/聊天头部/输入区的 `backdrop-filter`（整条模糊会让弱显卡每次交互重绘）。实测：新建 164ms → 114ms（请求 2 → 1），删除确认后列表立即变化 |
| D31 | 编排模型 | **ReAct 单主节点 + 一切皆工具**：删除 9 节点 LangGraph、Scene 场景档与节点绑定；主循环由模型自主决定调用哪个工具（**需要 function calling**，推翻 D4.1） |
| D31.1 | 工具类别 | 工具分**有模型 / 无模型**两类：有模型的工具配 `model_config_id` + `prompt_id`（从 Prompts 库下拉选）；无模型的工具只选一个后端注册能力 |
| D31.2 | 能力注册 | 后端只保留**能力注册表**（`kb_search` / `http_request` / `pdf_extract`），页面选能力 + 填配置（知识库就绑在检索工具的配置里） |
| D31.3 | 工具所有权 | 每个工具有 `requires_approval` 开关；开启后循环在调用前挂起等用户批准（SSE 保持连接，超时按拒绝处理） |
| D31.4 | 步骤落库 | 一条助手消息 = 一次完整 ReAct：`content.steps[]` 存思考/行动/观察摘要；**完整参数与结果存 `tool_calls` 表**，前端展开时按需 `GET /tool-calls/{id}` |
| D31.5 | 会话 | 会话绑定一个 Agent（可切换，切换只影响后续消息）；Agent = 模型 + 系统提示词（从 Prompts 下拉选）+ 人设 + Skills + 可用工具 + 最大轮次 |
| D10 | Skill 定义（**已被 D25 取代，保留作沿革**） | 原定义：指令包（markdown）+ 可选挂载 prompt/tool/知识库 + 输出约束 → 现为：**Skill = 独立纯文本，不引用任何其他资源**（见 D25） |
| D10.2 | 版本快照 | 一期即做：Agent 保存时对引用资源生成不可变 JSON 快照 |
| D11 | Langfuse | 会话消息旁一个 trace 小图标，点击跳转 Langfuse 自行查看 |
| D12 | 认证 | 无登录、无多租户 |
| D13 | 编排交互 | 表单化，非 DAG 拖拽画布 |
| D18 | 第二语料 | 面经/求职经验库（外部语料，全局维护） |
| D19 | JD 异构 | 一期不处理形态异构，统一按标准 JD 录入 |
| D20 | 场景入口 | **会话即应用**：上传 PDF + 报告全部承载在会话页内，不做独立场景页 |
| D11.1 | Langfuse 部署 | **自建，独立部署于另一台机器**：`http://192.168.10.99:3000`（实测 health 200，版本 **4.38.0**）。本项目 compose **不包含** Langfuse；session/trace/span 三级映射 |
| D11.2 | Langfuse 接入配置 | `LANGFUSE_HOST`（后端上报）与 `LANGFUSE_PUBLIC_HOST`（前端跳转）分开配置；SDK 与服务端 v4 对齐，接通以"UI 中能看到测试 trace"为准 |
| D13.1 | 界面语言 | 中文，不做 i18n |
| D14 | 工程规范 | `uv` 依赖管理；SQLAlchemy 2.0 async + Alembic；前端 Element Plus；LLM 测试用录制 fixture 回放 |
| D14.1 | 执行模型 | 诊断走**请求内 SSE 长连接**，一期不引入任务队列；断连即重跑 |
| D15 | 里程碑 | 8 个里程碑，合计 **23.5 人天**，应用优先、配置模块最后 |
| D16 | 时间预算 | 未确认，按**业余每周 1.5~2.5 人天（10~15 周）**假设排期 |
| D10.3 | 配置生效模式 | `snapshot`（默认，会话创建时固化） / `follow_latest`（调试用），见 §15.4 |
| D10.4 | 密钥存储 | Fernet 对称加密后落库，主密钥在 `.env`；前端只返回掩码，见 §15.8 |
| D21 | 配置与运行时的对接 | 节点不写死任何模型/Prompt/参数，统一经 `SceneRuntime` + `node_bindings` 装配，见 §15 |
| D22 | 知识库职责边界 | **数据由开发者自己维护；系统只负责建 collection、建索引、检索** |
| D22.1 | collection 字段 | **仅 4 个**：`id` / `content` / `dense` / `sparse`（**字段名与生成方式已由 D33 修正**：文本字段是 `content`，`sparse` 是 BM25 Function 的输出）；一行 = 一篇文档，不切 chunk，见 §5.3 |
| D27 | Embedding 提供方 | 用户自有 **DashScope `text-embedding-v4`**（OpenAI 兼容端点），1024 维，已实测连通；本地 BGE-M3 降为可选 provider |
| D28 | 稀疏向量来源 | 因远程 embedding 只产出 dense，`sparse` 改由 **Milvus 2.5 内置 BM25 Function** 生成（首选）；不可用时退回客户端词袋近似（`MILVUS_SPARSE_MODE=client_side`） |
| D23 | 部署拓扑 | **本地只跑 backend + frontend**；MySQL / Milvus / Langfuse 均为**远端已部署服务**，通过 `.env` 填地址端口接入。本机无需 Docker/WSL |
| D23.1 | 环境校验 | `scripts/check_env.py`（零依赖）作为开工前置检查，全绿才进入开发；开发期不使用 docker-compose |
| D24 | 服务器侧基础设施编排 | 提供 `deploy/docker-compose.infra.yml`：**MySQL + Milvus standalone（etcd + minio + milvus）+ Attu 可视化**，不含 Langfuse；端口与 Langfuse 编排已做冲突规避（Milvus 健康检查改绑 `127.0.0.1:19091`，Attu 用宿主 8001） |

---

## 1. 项目定位与范围

### 0.9 实现状态（2026-09-21 落地并端到端实跑）

**端到端实跑结果**（真实 MySQL 9.7 / Milvus 2.5.4 / DashScope embedding / DeepSeek chat+reasoner）

| 项 | 实测 |
| --- | --- |
| 单次诊断耗时 | 107~120 秒 |
| 动态轮次 | 2~4 轮收敛（第 1 轮强制继续，覆盖率 0/6 → 4/6 → 5/6 → 6/6 触发终止） |
| 产出规模 | 门槛对照 8~10 项、致命弱点 3 条（均带简历原句锚点）、改写建议 12~16 条、路线图 12 周 |
| 检索 | 混合检索命中正确：查 "Flink checkpoint" 命中 `bigdata_jd_003`（实时计算岗）与 `bigdata_exp_001`（Flink 一面面经） |
| 诊断质量抽样 | 上传"求职意向=大数据开发"的简历并投"Java 后端"岗位，系统判为 severity 5 硬性门槛冲突并给出改投建议 |
| 观测 | Langfuse SDK 已接入（trace=一次诊断、span=每个节点、LLM 调用自动采集）；服务未启动时探活失败并静默降级，不产出死链 |

**已落地并验证**

| 部分 | 内容 | 验证方式 |
| --- | --- | --- |
| 后端骨架 | FastAPI + 12 张 ORM 表 + 六类资源统一 CRUD（含删除引用保护）+ 测试场 + SSE 诊断 + 系统状态 | `ruff` 无告警；`app.main` 可导入；`/health` 与 `/api/v1/system/status` 实际返回 |
| LangGraph 工作流 | 9 节点 + Supervisor 覆盖率审核 + 第 1 轮强制继续 + 最大轮次兜底 + 真实流式总结 | 单测覆盖覆盖率判定与节点契约 |
| 检索与向量 | Milvus **单 collection `jd`**（id/content/dense + BM25 生成的 sparse）+ hybrid search（RRF）+ 远程 embedding（DashScope，local/fake 可切换） | `ensure_collections` 与 `/system/milvus/init` 已实现；dense 的 metric_type 从索引动态读 |
| 平台运行时 | `SceneRuntime` 装配 + 节点→Agent 绑定 + 版本快照 + 变量渲染（未声明变量报错） | 单测覆盖渲染与 Skill 注入 |
| 简历解析 | 文本层检测（无文本层直接拒绝，不做 OCR）+ PyMuPDF 提取 + LLM 结构化 + 锚点回填 | 单测覆盖锚点重定位 |
| 前端 | 卷宗设计语言 + 六个**专属配置编辑器**（工作台布局：可搜索清单 + 表单 + 测试场）+ 纯文本报告视图 + 辩论时间线 + 阶段进度 | `vite build` 通过；六类资源 CRUD 实测 26 项全过 |
| 工程化 | `.env` / `.gitignore` / `scripts/check_env.py`（零依赖连通性检查） | 实跑通过 |
| 测试 | 19 个单测（不依赖外部服务） | `pytest` 19 passed |

**偏离原方案的两处（已在实现中定案）**

1. **放弃 Element Plus**：它的默认样式会把"卷宗"设计语言压平。改为自研轻组件 + CSS 变量主题（`frontend/src/styles/theme.css`）。
2. **不写 docker-compose**：按 D23，外部服务均在服务器上，本机只跑前后端。

**当前状态**

| 服务 | 状态 |
| --- | --- |
| MySQL `192.168.10.99:3306` | ✅ 已连通（9.7.0），12 张表已建，演示配置已写入 |
| Milvus `192.168.10.99:19530` | ✅ 已连通（2.5.4），`jd` collection 已在用（真实语料）；`interview` 按 D33 下线 |
| Embedding（DashScope `text-embedding-v4`，1024 维） | ✅ 已连通，检索命中正确 |
| DeepSeek `deepseek-chat` / `deepseek-reasoner` | ✅ 已连通，端到端诊断跑通 |
| Langfuse `192.168.10.99:3000` | ⚠️ **容器未运行**（端口拒绝连接）。SDK 已在代码中接入并做探活降级，服务起来即可用 `scripts/check_langfuse.py` 验证 |

**端到端实测中修掉的真问题**（都是只有真跑才会暴露的）

1. LangGraph 1.x 节点第二个参数是 `Runtime` 而非 config → 改为 `get_config()` 注入运行时依赖；
2. `state` 未初始化 `checklist` → Supervisor 一直"无清单可判"，覆盖率全靠代码兜底；
3. `WeaknessOut.gate_comparison` 写成 `list[dict]` → 模型输出未被提前校验，到 `Verdict` 才炸（24 个校验错误）；
4. `GateItem` 字段全必填 → 模型漏字段即整轮崩，改为默认值 + 上限 10 项；
5. ORM `server_default` 字段在 flush 后读会触发同步惰性加载 → `MissingGreenlet`，改为先 `refresh`；
6. 快照含 `datetime` 无法写 JSON 列 → 加 JSON 安全转换；
7. Milvus `get_collection_stats` 对未封存段返回 0 → 改用 `count(*)` 并在写入后 `flush`；
8. Langfuse 不可用时仍生成 trace 链接（死链）→ 加真实探活，不可用即置灰。
9. **动态生成的路由把入参注解写成运行时变量**（`payload: spec.schema_in`）→ FastAPI 认不出 Pydantic 模型，退化成 query 参数，**六类资源的「新建 / 保存」从第一天起就返回 422**。改为显式写回 `__annotations__` 后再 `add_api_route` 注册；补了 24 项路由契约测试防回归。
10. **7 处配置是装饰性的或静默失效** → 按 D25 重构资源关系模型（Agent 收敛为人设、Scene 承担语料与动作、Skill 独立成纯文本、覆盖语义与优先级写死并有测试覆盖）。
11. **变量校验时机错误**：`render()` 在替换**之后**扫描 `{{...}}`，注入内容里合法的占位符（改写建议里的 `{{300}}`）被误判成"未提供的变量"，导致会话摘要整段失败。改为只校验模板自身声明的变量。
12. **会话列表 500**：迁移新增的 `sessions.summary` 是 `TEXT NULL`，历史行拿到 NULL 而 `SessionOut` 声明 `str` → 整表校验失败 → 前端拿不到会话、发送按钮静默无响应。改为可空声明 + 迁移补齐历史行 + 前端不再静默失败。
13. **删除 Tool / 知识库 500**：引用检查还在读 `Agent.tool_ids / kb_ids`（已按 D25 移除）。改为分别检查 Agent（model/prompt/skills）、场景节点（tool/kb）与场景档（kb）。
14. **孤儿 uvicorn 子进程**：`--reload` 的父进程被杀后，子进程仍占着端口跑旧代码，导致"重启无效、页面 404 / 没有输出"。清理后恢复；开发期用 `Get-NetTCPConnection -LocalPort 8000` 确认端口真正释放。
15. **切换会话卡顿**：三处原因——`router.replace` 触发 watch 导致**同一会话被加载两次**、`messages/attachments/traces` **三个请求串行**、每次渲染都 `smooth` 滚动并重复解析 markdown。改为只有 id 真变化才重载、三请求并发、切换用瞬时滚动、markdown 按消息 id 记忆化，并加前后端两级缓存（实测：首次切换 66ms，切回已看过的 0 请求）。另外收紧了会话页留白（页面内边距 12/16/14、消息区 14/20、列表项 80px → 56px）。 

**尚未实现**

Alembic 迁移文件（当前用 `app.db.init_db` 建表）· 知识库页面的检索测试已可用但尚未做分页与历史记录

### 1.1 一句话定位

大学生上传简历 PDF、填写目标岗位，系统检索真实招聘语料，由**技术面试官与 HR 面试官两个 Agent 红蓝对抗、Supervisor 审核收敛**，输出「你的差距在哪」与「未来 12 周怎么补」，并把结论锚定到简历原句与真实 JD 证据。

### 1.2 一期做什么

- 简历 PDF 上传 → 结构化解析（带原文锚点）
- 双路 Milvus 检索：JD Top10 + 面经 Top5
- 多轮红蓝辩论（动态轮次）+ Supervisor 必查项审核 + 终裁
- **纯文本诊断报告**（判决 / 致命弱点 / 逐条改写 / 12 周路线图 / 门槛对照 / 辩论纪要），可整段复制、可下载 `.txt`
- 平台侧六个配置模块（模型/Prompts/Tools/知识库/Skills/Agents）的 CRUD 与测试场
- 会话页流式输出、辩论过程可视、Langfuse trace 跳转

### 1.3 一期明确不做（非目标）

| 不做的事 | 原因 |
| --- | --- |
| OCR / 图片版简历 | 版面还原成本高，OCR 噪声会污染辩论结论；作为二期扩展点 |
| 爬虫 / JD 自动更新 | 合规与稳定性风险；一期人工维护语料 |
| Elasticsearch | 语料规模（百级）远未到需要独立全文引擎的地步 |
| 独立场景页 | 统一走会话页（D20） |
| 登录 / 多租户 / 权限 | 本地单用户使用 |
| LLM 自主 function calling | 工作流需可复现、可调试、可演示 |
| 可视化 DAG 拖拽画布 | 表单化编排足够表达一期场景 |
| 文档管理 UI（往知识库里上传文档） | 语料由脚本注入；知识库页只做连接与检索参数配置 + 检索测试 |

### 1.4 验收标准（"做完了"的定义）

1. 填好 `.env` 后 `python scripts/check_env.py` 全绿（含 Milvus 版本 ≥2.4 校验）；`uv run uvicorn app.main:app` 与 `npm run dev` 能起前后端。
2. 用 `data/` 中的种子语料执行导入脚本，Milvus 中 JD ≥ 90 条、面经 ≥ 60 篇。
3. 上传一份真实的学生简历 PDF（或粘贴文本）+ 填「大数据开发实习生」，**5 分钟内**产出一份纯文本报告。
4. 报告中每条致命弱点都能看到：简历原句 + 对应 JD 原句 + 理由。
5. 报告可一键复制全文，也能下载为 `.txt`（Windows 记事本打开不乱码）。
6. 每条消息旁的 Langfuse 图标可点击并打开对应 trace。
7. 在平台页面修改 Prompt 后重新诊断，结果发生变化（证明配置真的生效）。

---

## 2. 总体架构

### 2.1 分层

```text
┌─────────────────────────── Vue 3 前端 ───────────────────────────┐
│ Agent 构建：模型配置 / Prompts / Tools / 知识库 / Skills / Agents │
│ 运行验证：会话列表 + 会话详情（上传·流式·报告卡片·辩论折叠·导出） │
└──────────────────────────────┬───────────────────────────────────┘
                               │ REST + SSE
┌──────────────────────────────▼───────────────────────────────────┐
│                     FastAPI（API 层）                             │
│  /models /prompts /tools /knowledge-bases /skills /agents         │
│  /sessions /sessions/{id}/messages /diagnosis (SSE)               │
├───────────────────────────────────────────────────────────────────┤
│  平台内核                                                          │
│  · 资源注册表 + 版本快照（resource_versions）                      │
│  · LLM 网关（OpenAI 兼容：base_url/api_key/model/参数/流式/重试）  │
│  · 向量网关（pymilvus：collection 管理、标量过滤、混合检索）        │
│  · 可观测（Langfuse callback 挂 LangGraph 全链路）                 │
├───────────────────────────────────────────────────────────────────┤
│  Scene 层：resume_diagnosis                                        │
│  · LangGraph 工作流（parse → retrieve → debate* → verdict …）      │
│  · Scene 专属 schema / prompts / checklist / 报告组装              │
└──────────────────────────────┬───────────────────────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
     MySQL 8.0              Milvus 2.x             Langfuse
  （配置·会话·报告）      （jd / interview collection）（trace）
```

### 2.2 技术栈

| 层 | 选型 | 备注 |
| --- | --- | --- |
| 后端 | Python 3.11+ / FastAPI / Uvicorn | 全异步 |
| 依赖管理 | `uv` | 锁文件提交，保证环境可复现 |
| ORM / 迁移 | SQLAlchemy 2.0 (async) + asyncmy + Alembic | MySQL |
| 校验 | Pydantic v2 + pydantic-settings | LLM 输出强校验 |
| 工作流 | LangGraph | StateGraph + 条件边 |
| LLM | DeepSeek（OpenAI 兼容协议） | `openai` SDK 直连，base_url 可换 |
| Embedding | **DashScope `text-embedding-v4`**（OpenAI 兼容，1024 维） | 用户自有；OpenAI 兼容端点均可替换。DeepSeek 不提供 embedding，必须另配 |
| 向量库 | Milvus 2.x（standalone）+ etcd + MinIO | HNSW / COSINE |
| PDF 解析 | PyMuPDF (fitz) | 文本层检测 + 提取 |
| 可观测 | Langfuse | ✅ 已部署在远端 `192.168.10.99:3000`（v4.38.0），本项目作为客户端接入 |
| 前端 | Vue 3 + Vite + TypeScript + Pinia + Vue Router | |
| UI 组件 | Element Plus | 表单密集场景开发效率最高 |
| 流式 | SSE（`text/event-stream`） | 比 WebSocket 简单，够用 |
| 简历输入 | PyMuPDF 读 PDF；`.txt`/`.md` 直读；另支持直接粘贴文本 | 扫描件（无文本层）明确拒绝 |

### 2.3 部署拓扑（✅ 无本地容器依赖）

本机**没有也不需要** Docker/WSL。所有外部服务都已部署在服务器上，项目只作为客户端接入。

| 位置 | 组件 | 说明 |
| --- | --- | --- |
| 本地 | `backend` | FastAPI + Uvicorn，`uv run` 直接启动 |
| 本地 | `frontend` | Vite dev server（默认 5173） |
| 远端 `192.168.10.99:3000` | Langfuse | ✅ 已实测：health 200，version 4.38.0，项目 `Resume_roaster` |
| 远端（地址待填） | MySQL | 平台配置、会话、报告 |
| 远端（地址待填） | Milvus | 向量检索；**版本需 ≥2.4**（稀疏向量与 hybrid search） |

因此**开发机不需要任何容器**，环境接入只靠 `.env` + `scripts/check_env.py`。

服务器侧另有一份 `deploy/docker-compose.infra.yml`（MySQL + Milvus standalone），与 Langfuse 的编排完全独立、端口已规避冲突，部署步骤见 `deploy/README.md`。若后续要把后端一并部署到服务器，再补一个只含 backend/frontend 的 compose 即可。

原方案的服务清单（保留作参考，当前**不启用**）：

| 服务 | 说明 |
| --- | --- |
| `mysql` | 平台配置与会话数据 |
| `etcd` / `minio` / `milvus` | Milvus 标准三件套 |
| ~~langfuse~~ | **不并入本项目 compose**：用户已从 GitHub 拉取 Langfuse 源码，独立部署，本项目通过共享网络/端口访问（见 §9.5） |
| `backend` | FastAPI，代码目录挂载热重载 |
| `frontend` | Vite dev server |
| `attu`（可选） | Milvus 可视化，用于人工核对语料 |

---

## 3. 一次诊断的完整时序

```text
用户                    前端                 后端                     LangGraph / 外部
 │  选择「简历诊断 Agent」 │                     │                            │
 │───────────────────────▶│ POST /sessions      │                            │
 │  上传 PDF + 填岗位      │────────────────────▶│ 存文件·建 message(file)      │
 │───────────────────────▶│ POST /sessions/{id}/diagnosis (SSE)              │
 │                        │────────────────────▶│ 载入 Agent 及其资源版本快照   │
 │                        │                     │───────────────────────────▶│
 │                        │◀── stage: parsing_resume ───────────────────────│
 │                        │                     │ 文本层检测→拒绝 or 提取       │
 │                        │◀── stage: retrieving ───────────────────────────│
│                        │                     │ 并发混合检索 jd / interview   │
 │                        │                     │ interview_chunks（Milvus）   │
 │                        │◀── stage: debating round 1 ─────────────────────│
 │                        │◀── token / utterance: tech_lead(1) ─────────────│
 │                        │◀── token / utterance: hrbp(1) ──────────────────│
 │                        │◀── supervisor: decision=continue + 覆盖率 ───────│
 │                        │        …… 重复直至 terminate ───────────────────│
 │                        │◀── stage: verdict / advice / roadmap ───────────│
 │                        │◀── report: 完整报告 JSON ───────────────────────│
 │  看到报告卡片           │◀── done（落库 message(report)）─────────────────│
 │  点击 Langfuse 图标      │ GET 跳转 {LANGFUSE_HOST}/trace/{trace_id}        │
 │  点击导出 PDF           │ POST /messages/{id}/export → Playwright 渲染     │
```

---

## 4. LangGraph 工作流详细设计

### 4.1 State 定义

```python
class DiagnosisState(TypedDict, total=False):
    # 输入
    session_id: str
    target_role: str                 # 用户输入的目标岗位
    resume_file_path: str

    # 解析产物
    resume_raw_text: str
    resume: dict                     # ResumeProfile（附录 B）

    # 检索产物
    jd_hits: list[dict]              # Top10 JobPostingHit
    interview_hits: list[dict]       # Top5 InterviewHit

    # 辩论过程
    round: int
    transcript: list[dict]           # list[Utterance]
    findings_pool: dict[str, dict]   # finding_id -> Finding（去重聚合）
    supervisor_checks: list[dict]    # 每轮审核快照
    checklist: list[dict]            # 6 项必查项及覆盖状态
    max_rounds: int                  # 默认 5

    # 产物
    verdict: dict
    advice: list[dict]
    roadmap: dict
    report: dict
    errors: list[str]
```

### 4.2 节点清单

| 节点 | 模型 | 职责 | 关键约束 |
| --- | --- | --- | --- |
| `parse_resume` | deepseek-chat | PDF 文本层检测 → PyMuPDF 提取 → LLM 结构化抽取 → Pydantic 校验 | 只抽取不判断；必带锚点；校验失败重试 1 次 |
| `retrieve_context` | 无（纯检索） | 并发两路：`jd` Top10、`interview` Top5 | **dense + sparse 混合检索**（RRFRanker 融合）；可选按 `id` 前缀收窄岗位族 |
| `debate_router` | deepseek-chat（Supervisor） | 路由 + 轮次计数 + 覆盖率审核 + 终止判定 | 硬规则优先：round==1 必 continue；round>=max 必 terminate |
| `tech_lead_review` | deepseek-chat | 技术面试官发言 | 每条 finding 必须带 resume_anchor + jd_evidence |
| `hrbp_review` | deepseek-chat | HR 面试官发言 | 同上；且第 2 轮起必须对上轮 findings 逐条表态 |
| `verdict` | deepseek-reasoner | 终裁：初筛通过判断 + 致命弱点排序 + 分歧点 | 不得引入新 finding，只能聚合既有条目 |
| `resume_advice` | deepseek-chat | 逐条改写建议（原句 → 改写） | 每条必须绑定 anchor，且改写句可直接复制 |
| `roadmap` | deepseek-reasoner | 12 周路线图 | 每周必须含「产出物 + 验收标准」，禁止纯学习清单 |
| `compose_report` | 无（模板） | 组装 Report 落库 | 纯代码，不调模型 |

### 4.3 红蓝人格设定（Prompt 骨架）

**Tech Lead（技术面试官）**

```text
你是某大厂技术面试官，负责初筛。你的判断依据**只能**来自：
1) 候选人简历（已结构化，每条结论必须引用 resume_anchor 中的原句）
2) 检索到的真实 JD（引用 jd_id 与其原文）

铁律：
- 严禁提出任何没有简历原文或 JD 原文支撑的质疑。
- JD 未提及的要求，视为"信息未披露"，不得作为扣分项；也不得用行业常识替代该 JD 的要求。
- 你只关心技术深度：技术名词是否落到场景、是否体现取舍与权衡、是否踩过坑。
- 反例（必须抓出来）：写了 Redis 却没提缓存穿透/雪崩/击穿的处理；写了"高并发"却无 QPS 量级；
  写了"优化了性能"却无优化前后对比。

输出严格为 JSON（不得有额外文字）：
{ "round": int, "speaker": "tech_lead", "stance": "pass|borderline|reject",
  "findings": [ { "id": "F-tech-{round}-{seq}", "category": "tech_depth",
      "severity": 1-5, "claim": "...", "resume_anchor": {"text": "...", "start": int, "end": int},
      "jd_evidence": {"jd_id": "...", "text": "..."}, "suggestion": "..." } ],
  "rebuttals": [ { "target_finding_id": "F-hr-01", "verdict": "agree|partial|reject", "reason": "..." } ],
  "new_finding_count": int }
```

**HRBP（HR 面试官）**

```text
你是某大厂 HRBP，负责初筛。你关注：
- 项目背景是否过长、是否突出个人贡献（vs 团队）
- 是否有可验证的业务指标（数字、比例、规模）
- 简历结构、时间线连续性、表达逻辑是否清晰
- 岗位硬性门槛（学历、毕业年份、可实习时长、城市）是否匹配 JD
输出 JSON 结构与 Tech Lead 完全相同，speaker 为 "hrbp"，category 取
business_impact | expression | credibility | hard_requirement 之一。
第 2 轮起，你必须对 Tech Lead 上轮提出的每条 finding 表态（agree/partial/reject）。
```

**Supervisor（审核与路由）**

```text
你是诊断流程的审核者，不参与挑刺，只判断"诊断是否已达到要求"。
输入：必查项清单 + 本轮双方全部 findings + 既有 findings_pool。
对每个必查项判断是否"已覆盖"：该项至少有一条 finding，且已被对方回应过一次（存在指向它的 rebuttal）。
输出：
{ "round": int, "decision": "continue|terminate",
  "coverage": { "hard_requirement": bool, "tech_depth": bool, "business_impact": bool,
                "project_fit": bool, "expression": bool, "credibility": bool },
  "uncovered": ["..."], "open_disputes": ["..."],
  "reason": "一句话说明为什么继续/结束", "new_findings_count": int }
```

**Roadmap（路线图）**

```text
输入：致命弱点 Top3 + 目标岗位 JD 高频要求 + 面经考点。
输出 12 周计划，每周必须包含：week / 目标 / 产出物（能直接写进简历的那种）/ 验收标准 / 对应考点。
禁止出现"学习 XX 基础"这类无产出目标；禁止堆砌技术名词。
第 1~2 周必须先修复简历中已有的短板（把已有项目补上量化指标与技术细节），让候选人能立刻开始投递。
```

### 4.4 终止判据

必查项清单（Scene 可配置，默认 6 项）：

| key | 判定"已覆盖"的标准 |
| --- | --- |
| `hard_requirement` | 学历/毕业年份/到岗时间/实习时长 vs JD 硬性要求已给出结论 |
| `tech_depth` | 每项核心技能与 JD 必需技能已比对，"只有名词没有场景"的项已被点名 |
| `business_impact` | 个人贡献 vs 团队、量化结果是否缺失已给出结论 |
| `project_fit` | 项目技术栈与目标岗位的重合与缺失已明确 |
| `expression` | 篇幅分配、流水账、逻辑断层等结构问题已指出 |
| `credibility` | 时间线空白、内部矛盾、声称与证据不符已核查 |

判定顺序（**硬规则用代码，语义判断用模型**）：

```python
if state["round"] == 1:
    decision = "continue"          # 第 1 轮双方互不可见，跨角色反驳尚未发生
elif state["round"] >= state["max_rounds"]:
    decision = "terminate"         # 安全阀，防死循环烧钱
else:
    decision = llm_supervisor_decision(state)   # 全部必查项已覆盖 → terminate
```

### 4.5 上下文注入与 token 预算

| 轮次 | 注入内容 |
| --- | --- |
| 第 1 轮 | 完整 ResumeProfile(JSON) + JD Top10 摘要（每份 ~200 字）+ 面经考点摘要 |
| 第 2 轮起 | **只追加**对方上一轮 findings + 本轮需回应的开放争议点，**不重传**简历与 JD 原文 |

DeepSeek 上下文有限，且一轮辩论要吞下简历 + 10 份 JD + 历史 findings。按上述策略，单次会话约 3~6 万 tokens，成本在可忽略量级。**禁止实现成每轮全量重传**，否则第 4 轮即可能爆窗口。

### 4.6 异常与降级

| 场景 | 处理 |
| --- | --- |
| PDF 无文本层 | 立即终止，返回明确提示"请导出文字版 PDF"，不做 OCR |
| LLM 返回非法 JSON | Pydantic 校验失败 → 追加"只输出 JSON"重试 1 次 → 仍失败则记录 `errors`，用宽松解析补齐必需字段 |
| Milvus 检索为空 | 降级为"仅基于简历诊断"，并在报告顶部标注"未检索到匹配语料，结论可靠性下降" |
| LLM 接口超时/限流 | 指数退避重试 2 次；仍失败则阶段事件报错，保留已完成内容 |
| 辩论超过时间上限 | 单次诊断总时长上限 5 分钟，超时强制进入 `verdict` |
| Langfuse 未启动 | trace 记录静默失败，不阻塞主流程，图标置灰 |

---

## 5. 数据结构设计

### 5.1 ResumeProfile（附录 B 已定，此处为字段说明）

核心设计：`skills[].evidence` 记录"声称 vs 证据"，`projects[].depth_signals` 记录"是否体现架构决策/取舍/排障"，`anchors` 记录每个结论在原文中的字符区间。所有下游结论都必须能指回 `anchors`。

### 5.2 辩论与报告 schema

```python
# 一次发言
Utterance = {
  "round": int, "speaker": "tech_lead|hrbp",
  "stance": "pass|borderline|reject",
  "findings": [Finding], "rebuttals": [Rebuttal], "new_finding_count": int,
}
Finding = {
  "id": str, "category": str, "severity": 1..5, "claim": str,
  "resume_anchor": {"text": str, "start": int, "end": int},
  "jd_evidence": {"jd_id": str, "text": str},
  "suggestion": str,
  "raised_by": "tech_lead|hrbp", "round": int,
  "status": "open|accepted|dismissed",   # 由 rebuttals 聚合而来
}
Verdict = {
  "pass_screening": bool, "confidence": float,
  "headline": str,                     # 报告区块①：一句话判决
  "fatal_weaknesses": [Finding],      # 按 severity 排序的 Top3
  "disputes": [str],                   # 双方未达成一致的点
  "gate_comparison": [                 # 报告区块②：门槛对照
    {"item": "学历", "requirement": "本科及以上", "candidate": "本科(2027届)",
     "match": True, "jd_ref": "jd_012"}
  ],
}
RewriteAdvice = { "finding_id": str, "original": str, "rewritten": str, "reason": str,
                  "resume_anchor": {...} }
RoadmapWeek = { "week": int, "goal": str, "deliverable": str, "acceptance": str, "topics": [str] }
Report = { "headline": str, "gate_comparison": [...], "fatal_weaknesses": [...],
           "rewrites": [...], "roadmap": [RoadmapWeek], "debate_summary": {...},
           "meta": {"model_versions": {...}, "langfuse_trace_id": str, "elapsed_ms": int} }
```

### 5.3 Milvus Collection 设计（✅ 极简：4 个字段，D22）

**职责边界**：数据由开发者自己维护；系统只负责**建 collection / 建索引 / 检索**。字段压到 4 个，且**一行 = 一篇文档**（不切 chunk）。

**线上真实 schema（D33 定稿，以此为准）**——只有一个 collection `jd`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `VARCHAR(128)` PK | 行标识（形如 `{job_family}_{doc_id}` 或语料自己的 md5 前缀 + 序号） |
| `content` | `VARCHAR(65535)`，`enable_analyzer=true` | 整篇原文（JD 全文）。**注意字段名是 content，不是 raw_text** |
| `dense` | `FLOAT_VECTOR(1024)` | 远程 embedding 稠密向量（DashScope text-embedding-v4，语义召回） |
| `sparse` | `SPARSE_FLOAT_VECTOR`，`is_function_output=true` | **由 `bm25_func`（BM25 Function）从 `content` 自动生成** |

**链路三条硬约定**：

1. **写入不传 sparse**：BM25 由 Milvus 自己算，入库只给 `id` / `content` / `dense`（源 JSONL 里的
   `raw_text` 在 ingest 时映射到 `content`，源文件字段名不用改）。
2. **检索时稀疏那一路传文本**：`AnnSearchRequest(data=[query], anns_field="sparse", param={"metric_type":"BM25"})`，
   不是传向量——Milvus 用同一个 analyzer 把查询文本算成稀疏向量。
3. **dense 的 `metric_type` 动态从 collection 索引读取**：线上是 `IP`，代码里硬编码 `COSINE`
   会直接报 `metric type not match: expected=IP actual=COSINE`。见 `milvus_client.index_metric()`。

> 历史：早期设计是 `id/dense/sparse/raw_text` 四字段 + `jd`/`interview` 两个 collection，
> 由客户端计算 sparse。D33 起 `interview` 下线、文本字段改名 `content`、sparse 交给 BM25 Function。

**一行 = 一篇文档的理由**：JD 通常 300~800 字，面经 1000~3000 字，均在远程 embedding 单条上限内（已设 `EMBEDDING_MAX_CHARS=8000` 作超长保护），整篇向量化即可。这同时天然解决了"检索结果可能全来自同一篇文档"的聚合问题——一行就是一篇文章，不需要额外做去重字段。**扩展规则**：若个别超长面经超出上限，用 `{id}#2` 后缀拆行，检索后按 `#` 前缀归并取最高分即可。

**岗位族过滤（可选）**：没有独立的 `job_family` 字段，改为利用 `id` 前缀。需要按岗位族收窄时用 `id like "bigdata_%"`；不需要时全库检索，靠 query 文本与双路融合自己找准。百级数据量下两种做法都够快，因此**过滤不是必需步骤**，而是可选的收窄手段。

**明确放弃的能力及理由**：不按学历/经验年限做标量过滤，入库时也不调 LLM 抽结构化字段——一期只要"找出最匹配的 10 份 JD"，双路混合检索完全够用，硬性门槛交给面试官 Agent 读原文判断，准确度反而更高（还能遵守"JD 没写不等于不要求"这条原则）。

### 5.4 语料 JSONL 格式与导入（✅ 极简）

JSONL 是你手工维护的 **source of truth**（字段可以多，方便你自己看），Milvus 里只落 4 个字段，二者由 ingest 脚本转换。

`data/jd.jsonl`：

```json
{"doc_id": "jd_001", "job_family": "大数据开发", "raw_text": "岗位职责：...\n任职要求：...",
 "company": "某大厂", "title": "大数据开发实习生", "publish_date": "2026-08-12",
 "source_url": "https://..."}
```

`data/interview.jsonl` 同构（`stage` 可选）。

**来源信息不丢的约定**：因为 Milvus 只有 4 个字段，ingest 时把可选元数据拼成一行抬头写进 `raw_text`：

```text
【公司：某大厂｜岗位：大数据开发实习生｜发布：2026-08-12｜来源：https://...】
岗位职责：……
任职要求：……
```

这样报告里照样能展示"引自某大厂 2026-08 的 JD"，而 schema 不变。抬头由 ingest 脚本统一生成，你只需在 JSONL 里填字段，不用手写。

导入脚本 `python -m scripts.ingest --jd data/jd.jsonl --interview data/interview.jsonl --recreate`：

1. 读取 JSONL → 校验 `doc_id` / `job_family` / `raw_text` 必备（缺失即报错并指出行号，**不做模型补全**）；
2. 生成 `id = {岗位族英文前缀}_{doc_id}`，并拼接元数据抬头；
3. 超长文本（>8192 token）才按 `#2` 拆行，否则**一行 = 一篇文档**；
4. 调远程 embedding（DashScope，自动分批 10 条）得到 `dense`；`sparse` 由 Milvus BM25 Function 在写入时生成；
5. 幂等写入：先删同 `id` 旧行，再插入。

岗位族到英文前缀的映射写在 `scripts/job_families.py`（如 `大数据开发 → bigdata`），**避免手写前缀写歪**。

配套 `scripts/init_collections.py`：创建 `jd` collection，声明 `id` / `content` / `dense` / `sparse` 四字段、
加 `bm25_func` 并建索引——**"系统负责建表"的唯一入口**，幂等可重复执行。

---

## 6. MySQL 表设计

统一约定：所有表含 `id`(BIGINT 自增) / `created_at` / `updated_at` / `deleted_at`(软删)；资源类表含 `name`(唯一) / `description` / `is_enabled`。

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `model_configs` | `base_url`, `api_key_cipher`(Fernet 密文), `model_name`, `default_params`(JSON), `timeout_s`, `is_default` | 多套 OpenAI 兼容配置 |
| `prompts` | `messages`(JSON 数组：`[{role,content,order}]`), `params`(JSON: temperature/top_p/max_tokens), `variables`(JSON) | 变量占位符形如 `{{resume_json}}` |
| `tools` | `params_schema`(JSON), `returns_schema`(JSON), `impl_type`(http/python/builtin), `impl_config`(JSON), `test_cases`(JSON) | 页面提供 JSON 测试场 |
| `knowledge_bases` | `vector_store`(milvus), `collection`, `embedding_model`, `chunk_config`(JSON), `top_k`, `filter_fields`(JSON) | 只做连接与检索参数配置；建表/建索引由 `init_collections.py` 负责，数据由开发者自行维护 |
| `skills` | `instructions`(TEXT/markdown) | **纯文本指令，不引用任何其他资源**（D25） |
| `agents` | `persona`, `model_config_id`, `prompt_id`, `skill_ids`(JSON), `params_override`(JSON), `agent_type`(general\|diagnosis) | 人设单元；**不含工具与语料**（D25）。`agent_type` 决定会话页是否显示上传入口 |
| `resource_versions` | `resource_type`, `resource_id`, `version`, `snapshot`(JSON) | **不可变**；Agent 保存时对引用资源打快照 |
| `sessions` | `agent_id`, `agent_version_snapshot`(JSON), `config_mode`(snapshot\|follow_latest), `title`, `status`, `langfuse_session_id`, `summary`(TEXT), `summary_upto_message_id`, `last_route` | 会话创建时即固化 Agent 版本；`summary` + 水位线构成滚动摘要（D26.3） |
| `messages` | `session_id`, `role`, `type`(text\|file\|progress\|report\|debate_trace), `content`(JSON), `tokens`, `latency_ms`, `run_id`, `langfuse_trace_id` | 富类型消息 |
| `runs` | `session_id`, `target_role`, `status`(running\|succeeded\|failed\|timeout), `rounds`, `elapsed_ms`, `error`, `langfuse_trace_id` | 一次诊断的执行记录 |
| `reports` | `run_id`, `report_json`(JSON), `pdf_path` | 报告持久化，便于导出与复现 |
| `attachments` | `session_id`, `filename`, `stored_path`, `size`, `sha256` | 简历 PDF |
| `scene_node_bindings` | `scene_key`, `node_name`, `agent_id`, `model_config_id`, `prompt_id`, `kb_ids`(JSON), `tool_ids`(JSON), `params_override`(JSON) | 节点装配：绑定 Agent + 按节点覆盖；**语料与工具在此声明**（D25） |
| `scene_configs` | `scene_key`, `graph_key`, `name`, `intent`, `kb_ids`(JSON), `history_window`, `summary_trigger`, `is_enabled` | 场景档：链路 = 图（代码注册）+ 这份数据配置（D26.1） |

关键点：

- `api_key_cipher` 存 Fernet 密文，主密钥在 `.env` 的 `SECRET_KEY`；明文密钥既不落库也不进 git（详见 §15.8）。
- `resource_versions.snapshot` 与 Agent 强绑定，保证"改了 Prompt 之后历史会话仍可复现"。

### 6.1 数据模型现状与待补（对照实现代码）

| # | 现状 | 风险 | 建议 |
| --- | --- | --- | --- |
| 1 | 报告**双写**：`reports.report_json` 与 `messages(type=report).content` 各存一份相同内容 | 两处可能不同步，读写来源不一致时会出现"同一次诊断两个结果" | 定 `reports` 为权威数据，`messages` 退化为渲染副本（或只存 `run_id` 引用） |
| 2 | **ResumeProfile 不落库**，只活在 LangGraph state 与 Langfuse trace 里 | 无法"同一份简历换个岗位再诊断"，也无法做 Prompt A/B 回归对比 | `runs` 增加 `resume_json`，或新建 `resumes` 表按 `sha256` 去重 |
| 3 | `reports` 只有 `run_id`，没有 `session_id` | 查"某会话下的全部报告"必须 join `runs` | 增加冗余 `session_id` 列 + 索引 |
| 4 | `messages` 只有 `session_id` 单列索引，而实际查询是 `session_id + type in (...) + id desc` | 会话变长后历史查询变慢 | 增加复合索引 `(session_id, type, id)` |
| 5 | 附件与导出 PDF 存磁盘（`UPLOAD_DIR`），库里只存路径 | 只备份 MySQL 会得到"报告在、简历丢"的状态 | 备份必须 `mysqldump` + `uploads/` 目录成对执行，README 写明 |

---

## 7. API 契约

### 7.1 REST

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| CRUD | `/api/v1/{models\|prompts\|tools\|knowledge-bases\|skills\|agents}` | 六类资源统一 CRUD |
| POST | `/api/v1/models/{id}/test` | 连通性测试（发一条 hello，返回延迟与模型回显） |
| POST | `/api/v1/prompts/{id}/test` | 传入变量 JSON，返回渲染后的 messages 与试跑输出 |
| POST | `/api/v1/tools/{id}/test` | **工具测试场**：输入 JSON → 返回输出/报错栈 |
| POST | `/api/v1/knowledge-bases/{id}/search` | 检索测试：`{query, filters, top_k}` → 命中列表（含分数） |
| POST | `/api/v1/agents/{id}/debug` | 单轮调试：给定输入直接调用该 Agent |
| GET/POST | `/api/v1/sessions` | 会话列表 / 创建（须指定 `agent_id`） |
| GET | `/api/v1/sessions/{id}/messages` | 消息流水 |
| POST | `/api/v1/sessions/{id}/attachments` | 上传简历 PDF（≤10MB，仅 `application/pdf`） |
| **POST** | **`/api/v1/sessions/{id}/diagnosis`** | **SSE 主入口**：`{target_role}` |
| POST | `/api/v1/messages/{id}/export-pdf` | 导出该条报告为 PDF，返回文件流 |
| GET | `/api/v1/runs/{id}` | 诊断执行详情（轮次、耗时、trace） |

### 7.2 SSE 事件契约

```text
event: stage      data: {"stage":"parsing_resume","status":"running","msg":"正在解析简历"}
event: stage      data: {"stage":"retrieving","status":"done","jd_count":10,"interview_count":5}
event: stage      data: {"stage":"debating","status":"running","round":1}
event: token      data: {"speaker":"tech_lead","round":1,"delta":"简历里写了 Redis，"}
event: utterance  data: { ...完整 Utterance JSON... }
event: supervisor data: {"round":1,"decision":"continue","coverage":{...},"reason":"..."}
event: token      data: {"speaker":"roadmap","delta":"第 1 周："}
event: report     data: { ...完整 Report JSON... }
event: error      data: {"code":"LLM_TIMEOUT","msg":"...","retryable":true}
event: done       data: {"run_id":123,"elapsed_ms":84000}
```

设计说明：`stage` 让用户知道系统在干什么（一轮辩论数十秒，没有阶段反馈会被当成卡死）；`utterance` 与 `token` 并存，前端用 `token` 做打字机效果、用 `utterance` 做最终结构化渲染；`supervisor` 事件让"为什么继续下一轮"变得可见。

---

## 8. 前端设计

### 8.1 信息架构

```text
左侧导航
├─ Agent 构建
│   ├─ 模型配置      /models
│   ├─ Prompts       /prompts
│   ├─ Tools         /tools
│   ├─ 知识库        /knowledge-bases
│   ├─ Skills        /skills
│   └─ Agents        /agents
└─ 运行验证
    └─ 会话          /sessions  →  /sessions/:id
```

### 8.2 页面清单

| 页面 | 核心交互 |
| --- | --- |
| 模型配置 | 列表 + 表单；「测试连通性」按钮显示延迟与回显 |
| Prompts | messages 列表编辑器（增删排序 role/content）+ 参数面板（temperature/top_p/max_tokens）+ 变量预览 + 试跑对话框 |
| Tools | JSON Schema 编辑器（参数/返回）+ 测试场：左输入 JSON、右输出结果与耗时 |
| 知识库 | Milvus 连接与 collection 配置 + chunk 参数 + top_k + **检索测试**（输入 query 看命中与分数） |
| Skills | markdown 指令编辑器（纯文本，不挂载任何资源） |
| Agents | 人设配置（模型 + Prompt + Persona + Skills）+ 参数覆盖 + 「去调试」跳转会话 |
| 场景档 / 节点装配 | 链路配置（图 + 语料 KB + 路由意图 + 历史窗口）；节点绑定 Agent 并声明该节点可用的工具与语料 |
| 会话列表 | 新建会话（选 Agent，默认「简历诊断 Agent」）+ 历史列表 |
| **会话详情**（核心） | 见 8.3 |

### 8.3 会话详情页（承载整个应用）

```text
┌ 会话 · 简历诊断 Agent ─────────────────────────────┐
│  [＋ 上传简历 PDF]  [目标岗位：大数据开发实习生] [开始诊断] │
├────────────────────────────────────────────────────┤
│  ▸ 阶段进度条：解析简历 → 检索语料 → 辩论(第2轮) → 定稿    │
│                                                    │
│  🗂 简历.pdf  messages[0]                           │
│                                                    │
│  🔍 已检索到 10 份 JD / 5 篇面经             [trace] │
│                                                    │
│  ⚖️ 辩论过程（可折叠）                          [trace] │
│     ├ 第1轮 技术面试官：…（发现 3 条，最高严重度 5）      │
│     ├ 第1轮 HR 面试官：…（反驳 1 条）                   │
│     ├ Supervisor：6 项覆盖 3 项，继续第 2 轮             │
│     └ 第2轮 ……                                      │
│                                                    │
│  📋 诊断报告（卡片）                            [trace] │
│     ① 一句话判决                                    │
│     ② 门槛对照表（✅/❌）                             │
│     ③ 致命弱点 Top3（每条带简历原句 + JD 证据）          │
│     ④ 逐条改写建议（原句 → 改写，可一键复制）            │
│     ⑤ 12 周路线图（周维度卡片）                        │
│                                  [导出 PDF] [复制 Markdown] │
└────────────────────────────────────────────────────┘
```

组件拆解：`ReportCard`（5 区块，`GateComparisonTable` / `WeaknessList` / `RewriteList` / `RoadmapTimeline` 四个子组件）、`DebateTimeline`（折叠 + 逐轮轮次卡）、`StageProgress`（阶段条）、`LangfuseBadge`（小图标，`target="_blank"`）。

### 8.4 流式与状态

- 用 `fetch` + `ReadableStream` 手工解析 SSE（需自定义 `Authorization`/请求体时浏览器 `EventSource` 不够用）；
- Pinia store 按 `session_id` 维护 `messages[]`，`token` 事件累积到"正在生成"的临时消息，收到 `utterance`/`report` 后替换为结构化内容；
- 断线重连：SSE 断开后允许"重新诊断"，不做断点续传（一期弃用）；
- 导出 PDF：`POST /messages/{id}/export-pdf` → 后端 Playwright 打开隐藏打印路由 `/print/session/{sid}/message/{mid}` → 等 `networkidle` → `page.pdf(A4, print_background=True)`。

---

## 9. 工程规范

### 9.1 目录结构

```text
resume-roaster/
├─ .gitignore
├─ .env.example
├─ docs/agent-platform-plan.md          # 本文档
├─ data/                                # 语料 source of truth
│   ├─ jd.jsonl
│   └─ interview.jsonl
├─ scripts/                             # 运维脚本（仓库根）
│   ├─ check_env.py                     # 外部服务连通性检查（零依赖，已实现）
│   ├─ init_collections.py              # 建 collection 与索引
│   ├─ ingest.py                        # JSONL → 切分/编码 → Milvus
│   ├─ check_corpus.py                  # 语料质检卡口
│   └─ check_langfuse.py                # 发测试 trace 验证可观测接通
├─ backend/
│   ├─ pyproject.toml                   # uv 管理
│   ├─ alembic/                         # 迁移
│   ├─ app/
│   │   ├─ main.py
│   │   ├─ core/                        # config / logging / deps / errors
│   │   ├─ db/                          # session / base / models
│   │   ├─ schemas/                     # pydantic DTO
│   │   ├─ api/v1/                      # routers（六类资源 + sessions + runs）
│   │   ├─ services/                    # 业务逻辑（资源 CRUD / 版本快照 / 导出）
│   │   ├─ llm/                         # OpenAI 兼容网关：重试/流式/token 计数/JSON 强校验
│   │   ├─ vector/                      # milvus 客户端 / collection 定义 / 检索
│   │   ├─ obs/                         # langfuse 集成与降级
│   │   └─ graph/                       # state.py / nodes/ / build.py / supervisor.py
│   ├─ scenes/resume_diagnosis/         # 场景专属：prompts / schemas / checklist / report
│   ├─ scripts/                         # 仅后端内部辅助（如 seed_demo）
│   └─ tests/                           # pytest
└─ frontend/
    ├─ src/views/                       # 各配置页 + sessions
    ├─ src/components/report/           # ReportCard 及其子组件
    ├─ src/stores/                      # pinia
    └─ src/api/                         # 请求封装 + SSE 客户端
```

### 9.2 规范

| 项 | 约定 |
| --- | --- |
| 依赖 | `uv sync`；`pyproject.toml` + `uv.lock` 提交 |
| 代码风格 | `ruff format` + `ruff check`（含 import 排序）；行宽 100 |
| 类型 | 对外接口与 LLM 输出全部 Pydantic v2 模型；mypy 可选 |
| 迁移 | Alembic，禁止手改表结构；每次模型变更生成一个 revision |
| 异步 | 全链路 async；Milvus 与 LLM 调用不得阻塞事件循环 |
| 错误 | 统一异常基类 + 全局 handler；对外返回 `{code, msg, detail}` |
| 日志 | `logging` + JSON 格式，含 `session_id` / `run_id` / `node` 上下文 |
| 提交 | 小步提交，提交信息用 `feat:/fix:/docs:/refactor:` 前缀 |

### 9.3 测试策略

| 层 | 测什么 | 手段 |
| --- | --- | --- |
| 单测 | 锚点计算、chunk 切分、覆盖率判定、终止判据（硬规则部分）、JSON 校验失败重试 | pytest，全部不依赖外部服务 |
| 集成 | Milvus 检索正确性、Alembic 迁移、资源 CRUD | 连远端真实 Milvus/MySQL（复用 `.env` 配置），必要时用独立测试库与 collection |
| LLM | 节点输出 schema 合规性 | 用**录制的 fixture**回放，不做全真端到端（不稳定、烧钱） |
| 手工 | 完整诊断链路、PDF 导出、trace 跳转 | 发布前跑一遍 §1.4 验收清单 |

### 9.4 环境变量（`.env.example`）

```bash
# MySQL（远端）
MYSQL_HOST= MYSQL_PORT=3306 MYSQL_USER= MYSQL_PASSWORD= MYSQL_DB=resume_roaster
# Milvus（远端，需 ≥2.4）
MILVUS_HOST= MILVUS_PORT=19530 MILVUS_USER= MILVUS_PASSWORD= MILVUS_DB=default
# Langfuse（远端，已实测连通）
LANGFUSE_BASE_URL=http://192.168.10.99:3000
LANGFUSE_HOST=http://192.168.10.99:3000
LANGFUSE_PUBLIC_HOST=http://192.168.10.99:3000
LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY=
# 大模型
DEEPSEEK_API_KEY= DEEPSEEK_BASE_URL=https://api.deepseek.com
DEFAULT_CHAT_MODEL=deepseek-chat DEFAULT_REASONER_MODEL=deepseek-reasoner
# Embedding（远程，OpenAI 兼容；示例为 DashScope）
EMBEDDING_PROVIDER=remote
EMBEDDING_API_KEY= EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_MODEL=text-embedding-v4 EMBEDDING_DIM=1024 EMBEDDING_BATCH_SIZE=10
# 安全与运行时
SECRET_KEY= UPLOAD_DIR=./uploads MAX_ROUNDS=5 DIAGNOSIS_TIMEOUT_S=300
```

完整模板与注释见仓库根目录 `.env.example`；`.env` 已被 `.gitignore` 忽略。

### 9.5 Langfuse 集成（✅ 已部署并实测连通）

**现状**：Langfuse 已独立部署在 `http://192.168.10.99:3000`，版本 `4.38.0`（`/api/public/health` 返回 200）。它**不并入本项目 compose**，本项目只作为客户端接入。

**配置**：

```bash
LANGFUSE_HOST=http://192.168.10.99:3000            # 后端上报用
LANGFUSE_PUBLIC_HOST=http://192.168.10.99:3000     # 前端拼跳转链接用（浏览器可达地址）
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

**接入步骤**：

1. 在 Langfuse UI 中创建组织与项目，复制 `pk-...` / `sk-...` 填入 `.env`。
2. 安装与服务端 v4 对齐的 Python SDK。
3. **连通性验证（必做）**：跑一次 `scripts/check_langfuse.py`，发一条测试 trace，在 UI 中能看到才算接通——不采用"配了就算数"的判断方式。
4. 后端在本机直接运行（无容器），局域网地址可直连；`check_env.py` 已把 health 与密钥校验纳入检查。

**其他约定**：

- 用 LangGraph/LangChain 的 CallbackHandler 挂到整个图，自动获得每个节点的 prompt / completion / token / 延迟。
- 映射关系：`Langfuse session = 平台 session_id`，`trace = 一次 diagnosis run`，`span = 一个 graph 节点`。
- 前端只拼跳转链接，**不内嵌 trace UI**。
- Langfuse 不可用时全部静默降级（`obs/` 层捕获异常），图标置灰，绝不拖垮主流程。

---

## 10. 语料准备计划

### 10.1 来源与合规

从公开岗位页（公司官网招聘、牛客、实习僧、BOSS 直聘等）**人工复制**岗位描述与面经，仅用于本地研究与演示，不对外分发。因此仓库里分两层：

- `data/sample/*.jsonl`：**脱敏改写版样例语料**（公司统一写为"某大厂/某中厂"），约 25 条 JD + 15 篇面经，随仓库提交，保证别人 clone 后能跑通全流程。
- `data/local/*.jsonl`：完整真实语料（≥90 条 JD + ≥60 篇面经），加入 `.gitignore` 不入库，用于真实演示与效果验证。

### 10.2 数量与结构

| 语料 | 目标量 | 最小可用量 | 结构 |
| --- | --- | --- | --- |
| JD | 90（3 个岗位族 × 30） | 40 | 3 个岗位族、覆盖大厂/中厂、发布时间跨度 ≥6 个月 |
| 面经 | 60（3 个岗位族 × 20） | 20 | 含具体问题描述，剔除广告与培训招生帖 |

岗位族建议：**Java 后端开发 / 大数据开发 / 数据分析（偏 AI 应用）**——选这三个是因为它们的技术栈与你自己动手做过的项目重合度最高，演示时被追问细节不会露怯。（⚠️ 待你最终确认）

### 10.3 录入规范与成本（✅ 从简）

按 D22，入库只强制三个字段，其余元数据可选。这大幅降低了录入负担——你只需要把原文粘对，不必逐条填表。

| 字段 | 要求 |
| --- | --- |
| `doc_id` | 必填，唯一（如 `jd_001`） |
| `job_family` | 必填，决定 `id` 前缀（由脚本按映射表生成），三个岗位族用统一写法 |
| `raw_text` | 必填。JD 必须包含"任职要求/岗位要求"段落；面经必须包含具体问题描述 |
| `company` / `title` / `city` / `stage` / `publish_date` / `source_url` | 可选，填了就作为来源信息展示 |

人工成本估算：JD 每条约 2 分钟（复制 + 起个 doc_id），90 条 ≈ 3 小时；面经每条 2~3 分钟，60 篇 ≈ 2.5~3 小时。建议拆成 4~6 个批次录入，每批 1 小时，不要指望一口气录完。

### 10.4 质检脚本 `scripts/check_corpus.py`

输出并断言：语料总条数、岗位族分布、发布时间分布、`id` 前缀与 `job_family` 映射一致性、必填字段空值率、疑似重复（`doc_id` 与原文相似度）、疑似广告帖（关键词命中）。**该脚本作为 M1 与演示前的卡口**，任一断言不过就不允许进入演示流程。

---

## 11. 里程碑与排期（✅ 已确认）

| 里程碑 | 人天 | 交付物 | 验收标准 |
| --- | --- | --- | --- |
| **M0 地基与环境接入** | 1.5 | 仓库骨架、依赖管理、`.env` + `check_env.py`、Alembic、前端布局与左侧导航 | `check_env.py` 全绿；`/health` 通；前端能打开空壳页 |
| **M1 语料与检索** | 2.5 | 种子语料、ingest 脚本、Milvus collection、检索服务 | `check_corpus` 断言全通过；检索测试返回合理命中与分数 |
| **M2 简历解析** | 2 | 上传接口、文本层检测、PyMuPDF 提取、LLM 结构化抽取、锚点计算 | 3 份不同版式真实简历解析成功且锚点可定位；图片版 PDF 被拒绝 |
| **M3 会话与流式骨架** | 2 | sessions/messages/attachments、SSE 通道、会话页骨架 | 假数据下阶段推进与流式输出可见；刷新后历史保留 |
| **M4 LangGraph 主线** | 4.5 | State、9 节点、Supervisor 覆盖率审核、终止硬规则、资源 DB 化 + 版本快照 | 真实简历跑通全链路，轮次 2~5 动态变化；checklist 永不覆盖时跑到 max_rounds |
| **M5 报告与导出** | 2.5 | Report 组装、报告卡片与子组件、辩论时间线、Langfuse 图标、Playwright 导出 | 5 区块完整；弱点带原句与 JD 证据；PDF 与页面一致；trace 可跳转 |
| **M6 平台六模块** | 5 | 六类资源 CRUD + 测试场 + Agent 组合与版本快照 | 改 Prompt 后诊断结果变化；可切换 chat/reasoner；可查历史版本 |
| **M7 语料补足与打磨** | 3 | 语料扩量、演示脚本、README 与架构图、一键启动验证、录屏 | 演示脚本连跑 3 遍不翻车；新机器全流程 ≤15 分钟 |
| **合计** | **23** | | |

排期映射：业余每周 1.5~2.5 人天 → **10~15 周**；每周 2.4 人天（≈19 小时）→ 恰好 10 周；全职 5 人天/周 → **5 周**。

砍单顺序（时间不够时）：M6 压到 2 人天（Tools/Skills 页降级为只读 + JSON 手改，去掉测试场）→ M7 压到 1 人天（语料减半）。**M0~M5 不可砍**，那是"能演示"的底线。

---

## 12. 风险清单与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| 语料量不足 | 检索恒定命中同几条，辩论退化为互相编造，"消除信息差"价值归零 | 设定最小语料硬门槛（40 JD / 20 面经）+ `check_corpus` 卡口 + 演示前必查 |
| LLM 输出非法 JSON | 流程中断 | Pydantic 校验 + 重试 1 次 + 宽松兜底解析 + 记录 `errors` |
| 辩论轮次不稳定 | 演示不可控 | 硬规则兜底（round==1 必继续、round>=max 必终止）+ 演示前固定用例连跑 3 次 |
| 单次诊断过慢 | 用户以为卡死 | 阶段事件反馈 + 并发双路检索 + 总时长上限 5 分钟 |
| 远程 embedding 接口不可用/超限 | 入库与检索中断 | 分批 10 条 + 超时重试；失败时入库脚本中断并报行号，不做静默跳过 |
| DeepSeek 无 embedding 接口 | 检索层无法实现 | 已改用独立的 OpenAI 兼容 embedding 端点（DashScope text-embedding-v4，1024 维） |
| 远程 embedding 只产出 dense | 4 字段设计中的 sparse 无来源 | 由 Milvus 2.5 内置 BM25 Function 从 raw_text 生成；不支持时退回 client_side 词袋近似 |
| 远端 Milvus 版本 < 2.4 | 稀疏向量与 hybrid search 不可用，4 字段设计失效 | `check_env.py` 与 M1 均校验服务端版本；低于 2.4 则先升级或退回纯稠密检索（需改 §5.3） |
| 远端服务网络抖动 | 检索或上报失败 | LLM/检索调用带超时与重试；Langfuse 静默降级；报告头部标注语料不可用 |
| 调 prompt 变成玄学 | 无法判断改动是否有效 | 版本快照 + 固定黄金用例集（3 份简历 × 3 个岗位 = 9 条）用于改动前后对比 |
| 范围持续膨胀 | 项目烂尾 | §1.3 非目标清单 + 里程碑封版，新想法一律记入 §14 二期 |

---

## 13. 演示脚本（3 分钟版）

| 时间 | 动作 | 想传达的信息 |
| --- | --- | --- |
| 0:00 | 展示左侧导航六个配置模块 | "这是一个可配置的 Agent 平台，不是一个写死的脚本" |
| 0:20 | 打开 Agents 与节点装配，展示「技术面试官」人设、它挂的 Skill，以及节点上声明的语料与工具 | "Agent 是人设、Scene 决定依据与动作、Skill 是纯文本经验，三者解耦且可版本化" |
| 0:40 | 新建会话 → 上传简历 PDF + 输入"大数据开发实习生" → 开始诊断 | 交互只有两步，用户不需要学怎么跟机器人说话 |
| 1:00 | 展示阶段推进与检索结果（10 份 JD / 5 篇面经） | 结论建立在真实语料上，不是模型凭记忆编的 |
| 1:15 | 展开辩论过程：第一轮双方发言 → Supervisor 显示"6 项覆盖 3 项，继续" | 核心亮点：多智能体对抗 + 可解释的动态终止 |
| 1:50 | 报告卡片：一句话判决 / 门槛对照 / 致命弱点 Top3（指简历原句 + JD 证据） | 每条结论可追溯，不是泛泛而谈 |
| 2:20 | 逐条改写建议，复制一条 | "降维打击"落到可直接粘贴的句子上 |
| 2:35 | 12 周路线图，指出第 1~2 周就能立刻开始投递 | 建议可执行，不是学习清单 |
| 2:45 | 导出 PDF + 点击 Langfuse 图标 | 产物可交付，过程可观测 |
| 2:55 | 回 Prompts 页改一句话，说明改动即时生效 | 平台能力闭环 |

---

## 14. 二期及以后（不做进一期）

语料自动更新（爬虫/订阅）· 扫描件 OCR · 多用户与权限 · 任务队列与断点续传 · 报告模板美化与品牌化 · Prompt A/B 对比实验 · 评测集与自动打分（golden set + LLM-as-judge）· Skill 市场与共享 · 简历多版本对比 · 投递进度管理 · 移动端适配。

---

## 15. 配置与运行时的对接机制

### 15.9 ReAct 架构（D31 定稿，**取代 §15.0~15.8 与旧 9 节点图**）

```text
用户消息 ──▶ Agent（唯一主节点 = ReAct 循环）
                │
                ├─ 模型输出 tool_calls？ ── 否 ──▶ 最终回答（流式）
                │                        └─ 是 ──▶ 逐个执行工具
                │                                   ├─ requires_approval？→ 挂起等批准
                │                                   ├─ 执行（能力注册表 / 有模型的工具）
                │                                   └─ 观察结果回灌 → 下一轮
                └─ 达到 max_rounds → 强制收尾

工具（唯一能力单元）
  ├─ 有模型（kind=llm）  ：Prompt（Prompts 库下拉）+ 模型（可留空沿用 Agent）
  └─ 无模型（kind=builtin）：选后端注册能力（kb_search / http_request / pdf_extract）+ 填配置
```

**资源关系（取代 D25 的旧版本）**

```text
Model / Prompt / Skill / KnowledgeBase  ← 互相独立、可复用
Agent   = 模型 + 系统提示词 + 人设 + Skills + 可用工具 + 最大轮次
Tool    = 能力 + 配置 +（有模型时）Prompt + 模型 + 是否需人工确认
Session = 绑定一个 Agent（可切换，只影响后续消息）
```

**关键实现（D32 起：工具实现全部收拢到 `app/tools/`）**

| 文件 | 职责 |
| --- | --- |
| `app/tools/<impl_key>.py` | 一个工具一个模块：`@tool(...)` 声明元信息与参数结构，`run(ctx, **params)` 是真实实现 |
| `app/tools/registry.py` | 装饰器 + 注册表 + **唯一执行入口** `execute_tool`；参数按代码 schema 收口、页面配置优先 |
| `app/tools/_llm_runner.py` | 有模型工具的共享辅助：按页面配置准备客户端与渲染好的 `system` / `user` |
| `app/tools/base.py` | `ToolContext` / `ToolSpec` 类型 |
| `app/agent/loop.py` | ReAct 主循环：只有"怎么想"，工具执行一律委托给 `app/tools/` |
| `app/agent/approvals.py` | 人工确认门：SSE 挂起 + `POST /tool-calls/{id}/approve` 唤醒 |
| `app/db/models.py` | `tools.impl_key` 指向代码实现；`param_overrides` 只存页面覆盖的描述/必填/默认值 |
| `POST /sessions/{id}/messages` | 唯一入口：SSE 推 `stage / step / token / approval_required / assistant / done` |

**新增一个工具（不需要改前端）**

```python
# app/tools/web_search.py
@tool(
    impl_key="web_search",
    title="联网搜索",
    description="……",
    # 下面三个只是"新建工具时带出的默认值"，之后在页面上随便改
    params_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    returns_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    config={"top_n": 5},
)
async def run(input: dict, ctx: ToolContext) -> dict:
    args = input["args"]      # 模型这次传的（页面那份入参 JSON 约束出来的）
    config = input["config"]  # 页面「配置 JSON」里存的值
    return {"ok": True, "summary": "……", "data": {...}}
```

再在 `app/tools/__init__.py` 的 import 列表里加一行，重启后端即可。

**工具函数的契约（D32.10）**

```text
run(input, ctx) -> {"ok": bool, "summary": str, "data": any}
  input = {"args": 模型传的, "config": 页面配置 JSON}
  ctx   = {tool, config, db, llm?, system?, user?}   # 只放没法序列化的活对象
```

- 平台**不按代码 schema 过滤参数**：页面上的「入参 JSON」才是权威，模型传什么就原样交给函数。
- 函数自己容错：取不到值就返回 `{"ok": false, "summary": "缺少 query"}`，让模型自己改。
- 平台会把返回值归一化成 `{ok, summary, data}`；`summary` 是回灌给模型的观察结果。

**实测**：一句"帮我诊断这份简历，目标大数据开发实习生"，Agent 自主完成
`读简历 → 检索真实 JD/面经 → 技术面试官 → HR 面试官 → 终裁官` 五步工具调用，
13 个步骤、48 秒、2512 字报告；工具详情展开时按需拉取。

### 15.10 配置页的数据链路（"这数据到底来自哪里"）

**页面上的每一格配置都是数据库里的行；只有"工具实现"是代码。** 这条边界是 D32 的核心。
工具页与具体工具无关：**入参 JSON / 出参 JSON / 配置 JSON 三个框 + 测试场**（D32.10）。

| 页面元素 | 来源 | 说明 |
| --- | --- | --- |
| 六类资源列表（模型 / Prompts / Tools / 知识库 / Skills / Agents） | MySQL 表 | `GET /api/v1/{slug}` → SQLAlchemy → 对应表，`deleted_at IS NULL` |
| 工具页「类别」（有模型 / 无模型） | **后端代码** | 由实现声明，页面不选；有模型时页面出现「Prompt（必选）+ 模型」两项 |
| 工具页「实现」下拉 | **后端代码注册表** | `app/tools/` 下所有 `@tool(...)`，`GET /api/v1/tools/impls` |
| 工具页三个 JSON 框 | **数据库**（页面权威） | `tools.params_schema` / `tools.returns_schema` / `tools.impl_config` |
| 工具页测试场 | 不落库执行 | `POST /api/v1/tools/test`，用表单当前值拼临时工具对象跑一次 |
| 工具页的三态徽标 | 代码 + 数据库对照 | `已实现` / `未实现`（impl_key 命中不到代码）/ `未指定实现` |
| 工具页「绑定知识库」 | MySQL `knowledge_bases` | 存进该工具的 `impl_config.knowledge_base_ids` |
| Agent 页各下拉（模型 / Prompts / Skills / Tools） | MySQL 上表 | 前端 `useRefs` 拉 `/api/v1/{slug}`，提交时只存 id 数组 |
| 会话里选 Agent、消息、附件、工具调用 | MySQL | `sessions` / `messages` / `attachments` / `tool_calls` |
| Langfuse 徽标 | 会话表里的 `langfuse_session_id` | 只是链接，不在本平台存 trace 详情 |

**"实现"为什么不入库**：实现必须有对应的 Python 函数才能执行。页面可以凭空写一个
`impl_key`，但它只会得到一条"配置齐全但一调就报错"的死数据 —— 所以工具页把它标成
「未实现」，调用时返回一条观察结果让模型换个办法，而不是把整个回合炸掉。
新增实现 = 在 `app/tools/` 加一个模块（见 §15.9）；页面的工作是**选实现 + 填配置**。

**两个已经踩过的坑（排查同类问题时先看这两条）**

1. **动态路由抢静态子路径**：通用详情路由 `/tools/{item_id}` 若注册在
   `/tools/impls` 之前，`impls` 会被当成 int 解析 → **422**，
   工具页的实现下拉永远是空的（看起来就像"页面没连后台"）。
   已在 `app/api/v1/router.py` 里固定顺序：测试场路由（带静态子路径）先注册，
   并有回归测试 `tests/test_resource_routes.py::test_impls_endpoint_returns_registered_impls`。
2. **`structuredClone` 撞上 Vue 响应式对象**：`structuredClone(reactiveProxy)` 会抛
   `DataCloneError`。这类调用通常紧跟在 `selectedId = row.id` 之后，异常被吞掉，
   表现就是"左边高亮选中了、右边表单还是空的" —— 最容易被误读成"页面没连数据库"。
   前端统一用 `src/utils/clone.ts` 的 `cloneDeep`（JSON 深拷贝）。
3. **渲染期字段名不一致会整块崩掉**：知识库测试场原本读 `hit.raw_text`，而后端
   `search_knowledge_bases` 返回的片段字段是 `hit.text`。结果是：接口 200、数据也拿到了，
   但渲染时 `undefined.slice()` 抛错，Vue 把这次更新整个丢弃 ——
   页面停在「执行中…」、命中列表一直是空的。**这条症状和"没连后台"一模一样，但其实是连上了。**
   排查手法：监听 `pageerror` / `console`，不要只看 Network 面板的 200。
4. **悬空引用**：`tools.prompt_id` / `agents.prompt_id` 是裸 id，没有外键。
   如果 `prompts` 表被清空，工具照样能列出，但 Prompt 下拉全空、`prompt_id` 指向不存在的行。
   恢复方式：`python -m scripts.seed_demo`（幂等 upsert，会把 Prompt 建回来并把工具/Agent 重新绑定）。

**自查命令（三条就能判断"页面是不是真的接了库"）**

```powershell
# 1. 后端直连库的返回
curl.exe -s http://127.0.0.1:8000/api/v1/tools
curl.exe -s http://127.0.0.1:8000/api/v1/tools/impls

# 2. 浏览器里实际渲染出来的（前端只认这个）
#    http://localhost:5173/tools → 左侧列表条数应与第 1 步一致
```

注意 `preview/` 目录下的单文件 HTML 是**早期界面示意**（内存假数据，用来定风格），
真实应用一律以 `http://localhost:5173` 为准。

### 15.-1 会话链路（D26 定稿，**已被 D31 取代，仅作历史记录**）

```text
用户消息 ──▶ router（路由器 Agent，输出 {scene_key, confidence, reason}）
                │  只落库与展示，不注入模型
                ├─ graph_key = resume_diagnosis ──▶ 现有诊断链路（解析→检索→红蓝辩论→终裁→报告）
                │      缺简历/岗位 → ask 事件主动追问，不报错
                └─ graph_key = chat ──▶ chat_retrieve（可选，受工具声明与场景语料控制）
                                        └─▶ chat_respond（流式；上下文 = system + 会话摘要 + 最近 N 条）
诊断结束追加 type=text 的「报告摘要」进历史 ⇒ 追问走 chat 即可带上报告结论
```

会话记忆采用**滚动摘要 + 水位线**：`sessions.summary` 存摘要，`summary_upto_message_id` 记录覆盖位置；
每轮结束若未摘要消息数 ≥ 场景档的 `summary_trigger`，就用 `conversation_summary` 节点做**增量压缩**；
摘要失败自动降级为"只注入最近 N 条"，不阻塞对话。

### 15.0 最终关系模型（D25 定稿，取代 15.3 的早期版本）

```text
Skill   独立文本：只写「什么条件下必须检查什么」，不引用任何资源
Prompt  身份模板：每个 Agent 唯一（不再被 Skill 抢占）
Model   可复用配置：Agent 默认 → 节点可覆盖
KB      语料：Scene 默认 → 节点可覆盖          ← 依据什么
Tool    动作：节点级声明                        ← 做什么
Agent   人设 = Model + Prompt + Persona + Skills
Node    绑定 Agent + 覆盖(model/prompt/params/kb) + 声明 tools
```

**参数优先级**：`model.default_params` → `prompt.params` → `agent.params_override` → `binding.params_override`
**资源合并**：一律并集；覆盖字段为空表示"沿用上层默认"，没有第三态
**快照单位**：会话创建时固化的是**整份装配**（场景语料 + 节点绑定 + 被引用 Agent 的人设），不是单个 Agent

**这次修掉的 7 处"装饰性配置"**（都是关系模糊的具体表现）：

| 原状 | 现状 |
| --- | --- |
| `Agent.tool_ids` 无人消费 | 删除；工具改为节点级声明，检索节点真调用 `milvus_search` |
| `Agent.kb_ids` 无人消费 | 删除；语料归 Scene，检索节点逐个 KB 按各自 collection/top_k/前缀检索 |
| `Agent.params_override` 被绑定层忽略 | 纳入四层优先级的第三层，真实生效 |
| `Prompt.params` 从未参与调用 | 纳入第二层，真实生效 |
| `Skill.prompt_id/tool_ids/kb_ids/output_schema` 全被忽略 | 删除，Skill 瘦身为纯文本 |
| `KnowledgeBase` 整表未参与运行 | 成为 Scene 的默认语料，逐库检索并在报告里列出命中数 |
| 绑定键 `debate_router` 与代码调用 `supervisor` 不一致（静默失效） | 统一为代码节点名 + 启动期强校验，写错即告警 |

### 15.1 总体思路

页面上的配置是**数据**，运行时行为是**装配结果**。二者之间必须有一层显式的资源解析器，而不是"图里直接写死 prompt 字符串"。规则只有一条：

> **LangGraph 节点里不允许出现任何写死的模型名、Prompt 文本或检索参数；一律通过 `SceneRuntime` 获取。**

```text
配置页 CRUD ──▶ MySQL 资源表 ──┐
                              │ ① 会话创建时打快照（resource_versions）
                              ▼
                      session.agent_version_snapshot (JSON)
                              │ ② 运行时装配
                              ▼
   ┌──────────────── ResourceResolver.build_runtime() ────────────────┐
   │ model  : LLMClient(base_url, api_key, model_name, params)          │
   │ prompt : 渲染后的 messages（变量注入）                             │
   │ skills : Agent 的 instructions 追加为 system 段（纯文本）           │
   │ tools  : 本节点声明的工具（按 impl_type 分发到 handler）            │
   │ kb     : 本节点声明的检索器（collection + filter 构造 + top_k）     │
   └───────────────────────────────┬──────────────────────────────────┘
                                   ▼
                    LangGraph 各节点按 node_bindings 取用
```

### 15.2 六个页面 → 运行时字段的对照

| 页面 | 用户改什么 | 影响运行时哪个部分 | 何时生效 |
| --- | --- | --- | --- |
| 模型配置 | base_url / 模型名 / 参数 / 密钥 | 对应 Agent 的 `LLMClient` | 下一个会话 |
| Prompts | messages 列表 / 参数 / 变量 | 节点的基础 messages 模板 | 下一个会话 |
| Tools | 参数与返回 schema / 实现配置 | 工具注册表条目 | 下一个会话（HTTP 工具即时，builtin 需代码） |
| 知识库 | collection / chunk 参数 / top_k / 过滤字段 | `Retriever` 的查询参数 | 下一个会话；检索测试立刻可见 |
| Skills | 指令 markdown | 追加进该 Agent 的 system 段（不引入任何资源） | 下一个会话 |
| Agents | 人设 / 模型 / Prompt / Skills / 参数覆盖 | `scene_node_bindings` 指向的 Agent 快照 | 下一个会话 |
| 节点装配 | 节点绑定哪个 Agent、该节点用哪些工具与语料 | `SceneRuntime` 的节点级装配结果 | 下一个会话 |

### 15.3 关键实现：节点如何找到自己的配置

Scene 配置里有一张**节点 → Agent** 的绑定表（存 DB，Agents 页面可改）：

```yaml
node_bindings:
  parse_resume:     agent_resume_parser     # 建议用便宜快的模型
  tech_lead_review: agent_tech_lead         # deepseek-chat
  hrbp_review:      agent_hrbp              # deepseek-chat
  debate_router:    agent_supervisor        # deepseek-chat
  verdict:          agent_supervisor        # 覆盖为 deepseek-reasoner
  resume_advice:    agent_advisor
  roadmap:          agent_roadmap_coach     # deepseek-reasoner
```

节点执行时统一走同一个入口，不允许自己拼 prompt：

```python
async def tech_lead_review(state: DiagnosisState, rt: SceneRuntime) -> dict:
    agent_rt = rt.for_node("tech_lead_review")           # 取该节点绑定的 Agent
    messages = agent_rt.render(                          # 注入变量、追加 skills 指令
        vars={
            "resume_json": state["resume"],
            "jd_hits": state["jd_hits"],
            "interview_hits": state["interview_hits"],
            "round": state["round"],
            "peer_findings": rt.peer_findings(state),    # 第2轮起才有值
        }
    )
    text = await agent_rt.llm.complete(messages, json_mode=True)
    return {"transcript": [Utterance.parse(text)]}       # Pydantic 强校验
```

这样"在 Prompts 页改一句话 → 下次诊断的面试官就换了口吻"是自然结果，不需要任何额外热加载机制。

### 15.4 配置文件与会话快照的关系

| 模式 | 行为 | 用途 |
| --- | --- | --- |
| `snapshot`（默认） | 会话创建时固化 Agent 及其引用资源的 JSON 快照，之后改配置不影响该会话 | 可复现、可对比、可写进 Langfuse 溯源 |
| `follow_latest` | 每次运行都读取当前最新配置 | 调 Prompt 时反复试跑，不用新建会话 |

两种模式在 `sessions.config_mode` 上切换，前端在会话设置里给个开关。**默认必须是 snapshot**，否则你永远说不清"上次那个好结果是怎么来的"。

### 15.5 变量渲染

Prompt 文本用 Jinja2 渲染，变量白名单固定（`resume_json` / `jd_hits` / `interview_hits` / `round` / `peer_findings` / `target_role` / `checklist`）。渲染规则：

- 未声明的变量一律报错，**不允许静默渲染成空字符串**——静默留空是调试噩梦的源头；
- Prompts 页面提供"变量预览"，用样例数据渲染出最终 messages 给你看；
- 渲染后的完整 messages 落进 `runs` 记录，并在 Langfuse 里可见。

### 15.6 工具与知识库怎么"真连上"

**Tools** 按 `impl_type` 分发：

| impl_type | 页面能做什么 | 后端如何执行 |
| --- | --- | --- |
| `builtin` | 只能选用与调参，不能新增 | 调用代码里注册的 handler（如 `milvus_search` / `pdf_extract` / `export_pdf`） |
| `http` | 可完整新增：URL、方法、Header、Body 模板 | `httpx` 发起请求，响应按 `returns_schema` 校验 |
| `python` | 一期不支持页面新增 | 预留：需在代码中注册后才能被页面选用 |

**这一点必须在前端写清楚**：页面上"新增一个工具"能造出 HTTP 工具，但造不出新的内置能力。否则用户会以为拖两下就能长出新本事。

测试场 `POST /tools/{id}/test` 必须调用与线上**完全相同**的执行入口（同一函数、同一参数解析），否则测试场就会变成"测的时候好用、跑的时候报错"。

**知识库**同理：知识库页面本质上是"一个 Retriever 的配置 + 一个调试入口"。检索测试与辩论节点的检索走同一个 `Retriever.search()`,只是测试入口允许任意 query。

### 15.7 配置的引用完整性与删除保护

| 情形 | 处理 |
| --- | --- |
| 删除被 Agent 引用的 Prompt / Skill | 阻断删除，返回引用它的 Agent 列表（引导用户先解绑） |
| 删除被节点装配引用的 Tool / 知识库 | 阻断删除，返回引用它的场景档与节点列表 |
| 删除被会话引用的 Agent | 允许，会话因有快照仍可查看与复现；仅禁止新建会话时选择 |
| 停用（`is_enabled=false`）资源 | 运行时跳过该资源；若该资源是节点的必需项（如节点绑定的 Agent），则报配置错误而非静默降级 |
| 模型密钥 | 见 15.8 |

### 15.8 密钥怎么存（容易被忽略的一环）

页面上的"模型配置"需要填 API Key，但密钥**绝不能明文落库、更不能进 git**。一期方案：

- DB 只存密文：用 `cryptography.Fernet` 对称加密，主密钥放 `.env`（`SECRET_KEY`）；
- 前端查询时只返回掩码（`sk-****abcd`），不返回明文；
- 运行时解密后注入 `LLMClient`，进程内缓存；
- 若解密失败（换了主密钥），模型配置页给出明确提示"密钥无法解密，请重新填写"。

---

## 附录 A：决策日志（含被否决项）

| 决策 | 结论 | 备注 |
| --- | --- | --- |
| Elasticsearch | **取消** | 语料规模百级，Milvus scalar filter 足够 |
| OCR / 扫描件支持 | **不做** | 一度回复"支持"，经澄清后确认不做 |
| 独立场景页 | **不做** | 改为"会话即应用" |
| LLM 自主 function calling | **一期不做 → 已被 D31 推翻** | 早期由节点固定调用；D31 改为 ReAct 单主节点，模型自主选工具 |
| 可视化 DAG 拖拽画布 | **不做** | 表单化编排 |
| 登录 / 多租户 / 权限 | **不做** | 本地单用户 |
| JD 自动爬取 | **不做** | 人工维护 + 幂等导入脚本 |
| 任务队列（Celery 等） | **不做** | 请求内 SSE，断连即重跑 |
| 路线图周期 | 3 个月 / 12 周 | 中途曾改为 2 个月，最终回到 3 个月 |
| JD 形态异构处理 | **不做** | 用户撤回该议题，统一按标准 JD 录入 |
| 候选人博文作为能力证据库 | **否决** | 第二语料定为面经/求职经验库（外部语料） |
| 知识库文档管理 UI | **不做** | 只做连接配置 + 检索测试 |
| "一段话"输出 | 仅指报告的区块① | 整份报告为 5 区块结构化产出 |
| D32.1 工具定义的归属 | 页面注册 + 代码实现 | 页面工具行 = 注册表（可增删改），代码 = 实现池；`impl_key` 提升为独立列，命中不到代码就显示「未实现」；同一实现允许被多行工具复用（各传各的配置） |
| D32.2 工具代码位置 | **全部收拢到 `app/tools/`** | 一个实现一个模块，代码里声明：`impl_key`、显示名、给模型看的说明、参数 schema、执行函数（提示词模板不在此列，见 D32.3） |
| D32.3 提示词来源 | 页面**必选** Prompt，代码不写模板 | 有模型的工具必须在 Tools 页从 Prompts 库下拉选一条并绑定 `prompt_id`（必填）；代码里不存模板，只声明参数与执行函数；平台在调用前用页面配置渲染好 `system` / `user` 再交给函数体（本条修正 D32.3 初版"废掉 prompt_id"） |
| D32.4 执行期参数合并 | **页面配置优先** | `arguments` 只保留代码 `params_schema` 声明过的键，其余丢弃；与 `impl_config` 合并时 `impl_config` 赢（模型改不了知识库/URL/前缀等管理员配置）；必填缺失返回 `ok=false` 观察结果，不打断整回合 |
| D32.5 工具函数体 | **每个工具都写函数体（含有模型的）** | `app/tools/` 下一个模块一个工具，模块内有真实执行函数；有模型的工具不是"只写模板声明"，模板与模型都作为页面传入的参数进入函数 |
| D32.6 参数 schema 边界 | ~~结构锁代码，措辞放开页面~~ **已被 D32.10 推翻** | 初版想限制页面只改描述/必填/默认值；实际评审后确认页面应完全自由（见 D32.10） |
| D32.7 页面可配项清单 | Prompt（必选）、模型、参数描述/必填/默认值、`config_fields` 内的配置、超时、人工确认、启用开关 | 其余（`impl_key`、参数键名与结构、执行逻辑）一律由代码决定，页面只读 |
| D32.8 函数体签名 | 统一 `run(ctx, **params)` | `ctx` 携带 `llm` / `system` / `user` / `config` / `db` / `tool`；`params` 是模型生成的参数（已按代码 schema 过滤）。分开放是为了让「页面配置」与「模型参数」的优先级是结构性的，不靠记规则 |
| D32.9 目录划分 | `app/tools/` = 能做什么，`app/agent/` = 怎么想 | 一个 `impl_key` 一个文件（文件名即 `impl_key`）；`tools/base.py` 放类型、`tools/registry.py` 放装饰器与执行入口、`tools/_llm_runner.py` 放有模型工具的共享辅助；`agent/` 只留 `loop.py` / `approvals.py` / `prompts.py`；旧的 `agent/registry.py`、`agent/llm_tool.py`、`agent/pdf.py` 迁移后删除，不留双份 |
| D32.10 前端与实现解耦 | **页面只认 JSON**（**推翻 D32.6 的"结构锁代码"**） | Tools 页与具体工具无关：入参 JSON / 出参 JSON / 配置 JSON 三个编辑框 + 测试场；入参 JSON 由**页面权威**，代码里那份只是新建工具时带出的默认值；执行时不再按代码 schema 过滤参数，模型传什么就原样交给工具函数 |
| D33 向量库 schema 对齐 | **jd 单表 + BM25 Function** | 线上 collection 只有 4 个字段：`id` / `content`（enable_analyzer）/ `dense`(FloatVector 1024) / `sparse`（**BM25 Function 的输出字段**）；`interview` 表下线。因此（1）文本字段名是 `content` 不是 `raw_text`；（2）写入时**不传 sparse**，由 Milvus 从 content 自动算；（3）检索时稀疏那一路**传查询文本**（`data=[query]` + `metric_type=BM25`）而不是向量；（4）dense 的 `metric_type` **动态从 collection 索引读取**（线上是 `IP`，硬编码 `COSINE` 会直接报 `metric type not match`） |
| D34 语料切分 | 一条岗位 = 一篇文档 | 原导入按固定字数切（~1150 字/块），一条岗位的「薪资/经验/技术标签/职责」被切散到不同行，向量成了多岗位的均值 → 语义检索接近随机。重建为按 `【岗位 N】` 切分、一行一个岗位（100 条），语义查询实测由「三层架构与 MVC」变为「大数据开发工程师」 |
| D35 检索链路参数化 | 多角度 × 双路 × 可调参数 | （1）**多 query**：`queries: [...]` 一次覆盖多个角度，被多角度命中的文档分数累加排前；（2）**来源标注**：每条结果带 `retrieval = dense / bm25 / both`，便于判断是语义还是关键词命中（为此不用 Milvus 自带 hybrid_search，改为分路搜索后自算 RRF）；（3）**知识库可调参数**：`top_k` / `rrf_k` / `recall_multiplier` / `snippet_chars` / `min_score` 全部落到 `knowledge_bases` 表，页面可改；（4）**分数阈值**：低于 `min_score` 不返回，避免噪音进模型上下文 |
| D36 工具执行硬化 | 每个可调行为都要有页面落点 | 页面上新增：**工具**的「失败重试次数」`tools.retry_times`（只对超时/限流/服务端错误重试，上限 3）、**Agent**的「同轮工具并发」`agents.parallel_tools`（一轮里多个 tool_call 并发执行，省一半等待）。工具的「工具内超时」`timeout_s` 从死配置变成真的会中断执行。另加两条不开放开关的安全底线：`pdf_extract` 只能读上传目录内的文件、`http_request` 只允许 http/https；检索结果加 `MAX_TOTAL_CHARS=24000` 总预算，避免观察结果被硬截断成非法 JSON |
| D37 Langfuse 降级可恢复 | 失败进冷却，不再永久关闭观测 | 事故：`get_client()` 把"初始化失败"也写进 `_initialized`，于是一次网络抖动 / Langfuse 重启 / **换了 key 没重启后端**，就会让整个进程生命周期内再也不产生 trace，而对话照常（观测是旁路、静默降级），症状是"对话正常但 Langfuse 一条数据都没有"。改为：失败后进入 60s 冷却（冷却期内不重复探活），冷却结束自动重试；成功则永久缓存。另外 Langfuse v4 起服务端跑在 **events_only 模式**，写入走 OTLP `/api/public/otel/v1/traces`，旧的 `/api/public/traces` 查询接口已下线，`check_langfuse.py` 也从 2.x 的 `client.trace()` 改成了 `start_as_current_observation` |
| D38 工具白名单 | 页面勾选 = 白名单，工具信息不再进 prompt | 原先工具信息塞了两份：`build_system_prompt` 里列一遍「可用工具 + 描述」，`tools=` 参数里又有完整 schema —— 费 token，且描述与 schema 可能出现两份真相。改为：（1）**system prompt 只放人设 / 提示词 / Skills**，不再列工具；（2）`resolve_whitelist()` 把 Agent 勾选的工具分成「可调用 / 被挡下」，未实现、未指定、被停用的一律拦下；（3）执行前按白名单校验 tool_call.name，不在白名单的直接判死并回一条观察结果，不进执行器。**注意**：`tools=` 参数必须保留 —— 那是 function calling 的前提，模型靠它知道有哪些工具、参数长什么样；去掉的是 prompt 里的重复列表 |
| D39 trace 可读性 | 工具调用成为一等公民，input 不含 schema | 两个症状：（1）trace 里每个 generation 的 input 都是三十多 KB 的 tools 定义 —— 因为 langfuse 的 OpenAI 包装器把**整个请求 payload**当 input 记录；（2）模型发了 tool_calls 却在 trace 里看不见 —— v4 不会把 generation output JSON 里的 `tool_calls` 渲染成工具调用。改为：（1）`stream_chat` 结束后用 `update_current_generation()` 把 input 覆盖成**只有 messages**，并把 `tool_calls` 明确写进 output，metadata 带 `requested_model` / `finish_reason`；（2）`execute_tool()` 外面包一层 `as_type="tool"` 的 observation（v4 语义化类型），UI 渲染成「工具名 + 参数 + 结果」卡片。实测一次 5 工具的对话：7 个 generation 里带 tools 的 **0 个**、带 tool_calls 的 4 个，`type=tool` 的 observation 5 个 |
| D40 trace 结构按官方规范收敛 | agent / tool / generation 三层 + session 聚合 | 对照 Langfuse 官方《What does a good trace look like?》逐条对齐：（1）回合根节点从普通 span 改成 **`as_type="agent"`**（官方：trace 里只要有非 span/event/generation 的类型才会渲染 Agent Graph）；（2）用 **`propagate_attributes(session_id=...)`** 把多轮对话聚成一次 session（官方：一轮 = 一个 trace，一次会话 = 一个 session）；（3）**root 的 input/output 精简成「用户问题 → 最终回答」**（官方：trace 列表读的是 root 的 io，原始 payload 该进 metadata）；（4）generation 的 output 必须是**标准 OpenAI assistant 消息格式**——`tool_calls` 放在 assistant 消息的 `tool_calls` 数组里、每个 `arguments` 是 **JSON 字符串**；官方明确说只有这个形态才会渲染成工具调用卡片（D39 初版写成 `{"name","arguments":{...}}` 反而渲染不出来）。 |
| D41 提示词协议必须与运行时一致 | **二选一，不能混用** | 事故复现两次：`prompt_agent_main` 被改成"文本协议版"（要求模型输出 `Thought:` + `Action: {...}` 文本），但运行时走的是原生 function calling。模型照 prompt 输出文本、没有 tool_calls → 循环判定"这是最终答案"→ **一次工具都没调，13 步变 0 步**，用户侧表现为"一步就卡住"。同一版 prompt 里还带着两个**渲染不出来**的占位符 `{{TOOL_DOCS}}` / `{{user_question}}`（`collect_prompt_variables` 只提供 `tools` / `max_rounds` / `agent_name`），会原样留在提示词里变成噪音。结论：**文本协议（给不支持 function calling 的模型）与原生 tool_calls 是两条互斥路线**——选 A（当前实现，推荐）就别在 prompt 里要求输出 Action；选 B 就必须补一个解析 `Action` JSON 的运行时。另：prompt 里的"多轮交互示例"约 2000 字，属于每次请求都重复付费的冗余 |
| D42 两条协议都支持 | 运行时兼容文本协议与 function calling | 用户确认提示词沿用文本协议版，因此补上缺失的那一半：`loop.py` 新增 `parse_text_action()`（花括号配对解析 `Action: {...}`，容忍中文冒号和代码围栏），文本协议下把 Thought/Action 作为普通 assistant 消息、把结果以 `Observation: {...}` 回灌，`{"tool":"finish"}` 视为收尾。原生 `tool_calls` 路径原样保留 —— **两条路都认**，以后提示词用哪版都不会"一步卡住"。`DEFAULT_AGENT_SYSTEM` 同步换成文本协议版（并去掉 2000 字交互示例，占原提示词 2/3），占位符统一成运行时可渲染的 `{{tools}}` / `{{max_rounds}}`。实测：23 步 / 8 次工具调用 / 91.8 秒通过 |
| D43 generation 改手动上报 + 只展示增量 | 不依赖 SDK 的隐式 generation | 两个问题：（1）`update_current_generation()` 覆盖不生效 —— 它依赖 SDK 上下文中"当前 generation"，在 SSE（任务上下文会切换）里经常找不到，11 个 generation 只有 1 个被覆盖成功；（2）多轮 agent 每轮都要重发完整上下文（LLM 无状态），若每轮都把全量 messages 展示在 trace 里，越往后越长、根本没法读。改为：**弃用 `langfuse.openai` 包装器，手动 `as_type="generation"` 建观测并显式持有句柄**，input/output/usage 全部显式给；`stream_chat(new_from=...)` 只把**本轮新增的消息**作为 input，完整上下文的规模（`context_messages` / `context_chars`）与 `new_messages` 记进 metadata。token 用量靠 `stream_options={"include_usage": true}` 取（DeepSeek 兼容）。实测 10 轮：input 每轮 2~3 条增量，`context_chars` 从 3058 递增到 22476，全程可见且不冗余。踩坑记录：`new_from` 最初写成"本轮调用后的长度"，导致增量恒为 0，正确值是**本轮调用前的长度**（即本轮追加内容的起点） |
| D44 会话页交互三改 | 新建才开会话 / 不显示中间态 / 明确"没死机" | （1）**进页面不再自动建空会话**（原来 `openTarget` 在没有会话时会 `POST /sessions` 造一个），现在只有点「＋ 新建对话」才创建；没有打开会话时输入框与发送按钮禁用并给出引导文案。实测：进页面会话数 15→15，点新建才 15→16。（2）**不再实时渲染流式 token**（中途半截文本），流式期间只展示思考/行动/观察的时间线，最终回答在 `assistant` 事件后整段出现；（3）等待期间给出「正在思考 · 已用 N 秒」+ 跳动圆点 + 实时展开的步骤时间线，长任务（60~90 秒）能明确看出还在跑 |
| D45 Top K 就是最终条数上限 | 不再随角度数放大，也不被模型覆盖 | 两个问题：（1）`cap = top_k × len(queries)` —— 知识库页配 10、4 个角度会返回最多 40 条，用户配的"条数"形同虚设；（2）模型传入的 `top_k` 直接覆盖页面配置，改页面没用。改为：**页面配的 Top K 就是最终返回条数的上限**，多角度只提升召回质量（同一文档被多角度命中则分数累加、排更前），**不放大条数**；模型可以把条数要得更少，但不能超过页面配置（D32.4 配置优先）。实测：知识库配 10、传 `top_k=4` + 3 个角度 → 返回 4 条（旧逻辑返回 12 条）。新增 2 个回归测试锁住这两条语义 |
| D46 检索条数只由页面决定 | 工具参数里彻底去掉 `top_k` | D45 还留了"模型可以要得更少"的口子，实测立刻被钻：模型每次调用都自己传 `top_k=8`，用户怎么改页面都"一直是 top 8"。改为把 `top_k` **从工具参数里彻底移除**（代码默认 schema + 数据库里那条页面配置都清掉），检索条数只由知识库页的「默认 Top K」决定；`search_knowledge_bases()` 内部仍保留 `top_k` 形参，只给「知识库测试场」用。实测：页面配 3、故意传 `top_k=8` + 3 个角度 → 返回 3 条 |
| D47 人工确认卡片（HITL） | 内联卡片 + 回合内记忆 | 工具配 `requires_approval` 时，执行前弹**内联卡片**（嵌在思考/行动之间，不再是浮层弹窗）：显示工具名、说明、参数、倒计时，两个按钮「确认 / 取消」+「本回合不再问我」复选框。记忆规则都只作用于**本回合**（下一条用户消息进来就重置）：**取消无条件记住**（避免"取消→模型换参数再来→又弹卡片"的骚扰循环），确认要勾选才记住。超时与取消**措辞分开**（超时让模型换个不需要确认的做法，取消让它停止这个方向）。决策完成后卡片**写进步骤流并落库**，回合结束后仍能看到"哪些工具被确认/取消过"。实现要点：待确认项**独立于 checkpoint 全局登记**（`_pending: dict[tool_call_id, PendingApproval]`），否则没启用后台任务时审批接口找不到等待项、点了没反应 |

## 附录 B：ResumeProfile 结构化 schema

```jsonc
{
  "basic": {
    "name": null, "phone": null, "email": null, "city": null,
    "intended_role": "大数据开发实习生",
    "graduation_year": 2027,
    "education": [{ "school": "", "degree": "", "major": "", "start": "", "end": "", "gpa": null }]
  },
  "skills": [
    { "name": "Redis", "claimed_level": "熟悉", "evidence": "项目A中用于会话缓存", "mentions": 2 }
  ],
  "projects": [{
    "name": "", "role": "", "period": "", "team_size": null,
    "background": "一句话背景",
    "tech_stack": ["SpringBoot", "MySQL"],
    "contribution_raw": "原文摘录",
    "highlights": [{
      "type": "tech_depth | business_metric | engineering",
      "desc": "", "evidence": "", "has_metric": true, "metric": "QPS 200 → 1200"
    }],
    "depth_signals": { "has_architecture_decision": false, "has_tradeoff": false, "has_troubleshooting": false },
    "anchors": [{ "field": "contribution", "start": 412, "end": 507 }]
  }],
  "internships": [], "awards": [], "self_evaluation": "",
  "red_flags": ["技能罗列无证据", "时间线有 5 个月空白"]
}
```

## 附录 C：开放项（开始编码前需落地）

| # | 事项 | 状态 |
| --- | --- | --- |
| 1 | 三个岗位族的最终名单 | 建议 Java 后端 / 大数据开发 / 数据分析，待确认 |
| 2 | 实际每周可投入工时 | 未确认，暂按每周 1.5~2.5 人天排期 |
| 3 | DeepSeek API Key 与额度 | 未配置 |
| 4 | Embedding 端点（DashScope text-embedding-v4） | ✅ 已对接并实测（1024 维，分批上限 10 条） |
| 4.1 | Milvus BM25 Function 是否可用（决定 sparse 生成方式） | 待防火墙放通后验证 |
| 5 | 真实语料录入（约 8~12 小时人工） | 未开始 |
| 6 | 3 份用于调试的真实简历样本（不同版式） | 未准备 |
| 7 | 演示用目标岗位（演示脚本里用"大数据开发实习生"） | 待定 |
