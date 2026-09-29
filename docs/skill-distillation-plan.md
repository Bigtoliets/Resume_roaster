# Skill 蒸馏线路 · 设计与实现依据

> 版本：v1.0（2026-09-29 冻结）
> 定位：每个 ReAct 回合结束后，把该回合的真实运行轨迹蒸馏成一份持续演进的「项目运行经验」skill。
> 本文档即实现依据。编号与 `docs/agent-platform-plan.md` 的 D 编号独立，本文用 **SD** 编号。

---

## 0. 决策冻结清单

| # | 决策点 | 结论 |
| --- | --- | --- |
| SD1 | 轨迹来源 | Langfuse `GET /api/public/v2/observations`，按 `runs.langfuse_trace_id` **精确过滤**，不用"取最新" |
| SD2 | 读接口细节 | 必须显式传 `fields`，否则 io 全为 `null`；io 是原始字符串，自己 `json.loads`；`parseIoAsJson` 已废弃（传 true 直接 400） |
| SD3 | 写入可见延迟 | 实测 **2.13s** 才可读 → 必须带重试循环 |
| SD4 | 成败信号 | **来自本地库**（`runs.status` / `runs.error`）；trace 里 `level` 恒 `DEFAULT`、`status_message` 恒空 |
| SD5 | 输入形状 | **保留全文**（system prompt、完整 messages、工具返回原文），只砍 22 个恒空字段 |
| SD6 | 触发频率 | **每回合都跑**，不做规则门控 |
| SD7 | 产物 | **始终只维护一个**经验 skill：`operation-experience` |
| SD8 | 写入方式 | **全文重写**（不是增量补丁） |
| SD9 | 执行模型 | **主链路 Agent 配置的模型**（`agents.model_config_id`） |
| SD10 | 文件读写 | **由后端代码做**；模型纯文本进出，不接触文件系统 |
| SD11 | 分析线路第 1 轮 | 纯 system prompt：分析线路提示词（角色/任务）+ 其他 skill 的 name/description 目录（**排除经验 skill**）→ 输出需要哪些 skill |
| SD12 | 分析线路第 2 轮 | 第 1 轮内容 + 选中 skill 的正文 + 经验 skill 全文 + 本回合 trace → 输出**新的经验 skill 全文** |
| SD13 | 分析线路提示词 | **代码常量**（与 `app/agent/notes.py` 的 `PROJECTION_SYSTEM` 并列），页面只读 |
| SD14 | frontmatter 归属 | `name` **代码写死**；`description` **模型不许改**（锁在文件里的那一份，人要改就改文件） |
| SD15 | 主 ReAct 装配 | `agent.skill_ids`（表主键）→ 表拿 `name` → 读 `backend/skill/<name>/SKILL.md` 正文 → 拼进 system prompt |
| SD16 | `skills` 表 | **保留为索引**，删 `instructions` 列；页面只显示 name + description |
| SD17 | 表与盘一致 | **写路径 upsert + 启动对账**（目录有表无则 insert；表有目录无则标孤儿；description 漂移则 update） |
| SD18 | 触发与执行 | **后台任务 + `asyncio.Lock`**；不加"待蒸馏"标记，**没有启动补跑** |
| SD19 | 失败处理 | **降级：失败不改 md**（模型返回不合法 / 写盘失败 / 超时 / 超窗，一律放弃本回合） |
| SD20 | 产出契约 | **单文件**（只写 `SKILL.md`）；正文 **≤ 8000 字符**，超了带反馈重试 1 次，仍超则放弃；**不设行数上限** |
| SD21 | 附带文件 | **不生成** `agents/openai.yaml` |
| SD22 | 页面 | Skills 页：只展示 name + description、**可新建、不可编辑**、只允许删孤儿行；Agent 页：新增「自动蒸馏」开关 |

---

## 1. 整体流程

```text
主 ReAct 回合（成功 / 失败）
   │  sessions_router._turn_stream 收尾，SSE 发完 assistant + done
   ▼
后台任务（不挂在请求上，模块级持引用）
   │
   ├─ 取证据
   │    ├─ Langfuse: GET /api/public/v2/observations?trace_id=<runs.langfuse_trace_id>
   │    │             fields=core,basic,io,metadata,usage,model,metrics,trace_context,prompt,time
   │    │             （重试 3~5 次、间隔 0.5s，等最终一致）
   │    └─ 本地库:   runs.status / runs.error（成败信号）
   │
   ├─ 削减：保留 14 个字段，砍 22 个恒空字段（SD5）
   │
   ▼
【skill 分析线路】← 独立于主 ReAct
   第 1 轮  system: 分析线路提示词 + 【可用 Skill 目录】(name+description，排除经验 skill)
            → 模型回 {"load": ["skill-frontmatter", ...]}
   组装     代码按名字读 backend/skill/<name>/SKILL.md 正文（只读）

   第 2 轮  system: 分析线路提示词（同第 1 轮）
            user:   第 1 轮内容 + 【已加载 Skill 正文】+ 【当前经验 skill 全文】+ 【本回合 trace】
            → 模型回：新的 SKILL.md 全文
   │
   ├─ 校验：frontmatter 合法（name 强制写回 operation-experience）、正文 ≤ 8000 字符
   │       超限 → 带反馈重试 1 次 → 仍超 → 放弃
   ▼
后端代码：原子写 backend/skill/operation-experience/SKILL.md → upsert skills 表
          （任一步失败：降级，文件一字不动）
```

---

## 2. 分析线路的两轮提示词组装

### 第 1 轮（只有 system，没有 user）

```text
system:
  <分析线路提示词：角色 + 任务>

  【可用 Skill】          ← 只有 name + description；经验 skill 不在此列
  - skill-frontmatter: Write or validate the YAML frontmatter at the top of a SKILL.md…
  - writing-for-agents: Writing documents for agents. Use when creating or editing skills…

  （要求模型输出：{"load": ["..."]}）
```

**为什么经验 skill 不列进目录**：它不是候选，是被改写的对象；列进去只会让模型困惑。

**为什么两轮**：可用 skill **目录是动态的**（蒸馏线路自己会增删改写），所以"该加载哪几个"不能由代码写死，必须每次问模型。

### 第 2 轮

```text
system: <分析线路提示词（同第 1 轮）>

user:  = 第 1 轮全部内容
      + 【已加载的 Skill 正文】   ← 第 1 轮点名的那几份（只读）
      + 【当前经验 skill】全文     ← 本次要改写的那一份
      + 【本回合 trace】           ← 保留全文，砍恒空字段
```

---

## 3. 轨迹来源：Langfuse v2 observations（含实测）

### 3.1 接口

```http
GET /api/public/v2/observations
    ?trace_id=<trace_id>
    &fields=core,basic,io,metadata,usage,model,metrics,trace_context,prompt,time
```

SDK 写法：`client.api.observations.get_many(trace_id=..., fields=..., limit=...)`

**三个必须知道的坑**：

1. **不传 `fields` 就没有内容。** v2 默认只返回 `core` + `basic` 两组，`input`/`output` 全是 `null`。这很容易被误判成"Langfuse 里没存内容"。
2. **io 是原始字符串而非对象**，要自己 `json.loads`；`parseIoAsJson=true` 在 v2 上**已废弃**，传了直接 400（"Input/output fields are always returned as raw strings"）。
3. **键名陷阱（实现期又踩了一次）**：SDK 的 `model_dump()` 给的是 snake_case（`start_time` / `trace_id`），
   而 `model_dump(mode="json")` 会转成 camelCase，**并且无视 `by_alias=False`**（实测三种写法：
   `model_dump()` → `start_time`+datetime；`mode="json"` → `startTime`+str；`mode="json", by_alias=False`
   → **仍然是 `startTime`**）。选错就有一半字段被"不认识的键"静默丢掉。
   正确组合：**`model_dump()` 取蛇形键 + 自己把 datetime 转 ISO 字符串**（见 `trace_source._jsonable`）。
   代码里另有一道防呆：发现 `startTime` 就抛错，绝不静默降级。

### 3.2 v3 风格端点全部下线（不要再试）

实测（Langfuse **v4.38.0**，SDK 4.15.4，`events_only` 模式）：

| 端点 | 结果 |
| --- | --- |
| `/api/public/traces` | 404 `This endpoint is not available on deployments running in Langfuse v4 events_only mode` |
| `/api/public/traces/<id>` | 同上（带 `?useEventsTable=true` 也一样） |
| `/api/public/observations` | 同上 |
| `/api/public/sessions` | 同上 |
| `/api/public/metrics` | 同上 |
| `/api/public/v2/observations` | **200 ✅** |
| `/api/public/projects` / `/health` / `/datasets` | 200（元信息，无用） |

注意：**给不存在的 trace id 也返回同一句 404**——说明这是**路由级门禁**，不是"查错了表"，任何参数都救不了。

### 3.3 写入可见延迟（必须重试）

实测：写入 + `flush()` 之后立刻查询，

| 时刻 | 结果 |
| --- | --- |
| 0.10s / 0.66s / 1.51s | 0 条 |
| **2.13s** | 命中 |

所以取 trace 必须包一层重试（建议 5 次 × 0.5s）。

### 3.4 一个回合 = 一棵树

真实的 6 条 observation（`react-turn` 那次）：

```
ROOT AGENT       react-turn                io_in=98      io_out=7236    latency=16.8s
  ├─ GENERATION  llm-call                  io_in=27436   io_out=935     latency=3.0s
  ├─ TOOL        tool:resume_pdf_extract   io_in=169     io_out=13283   latency=0.04s
  ├─ GENERATION  llm-call                  io_in=14418   io_out=7271    latency=5.5s
  └─ SPAN        turn-projection           io_in=40      io_out=3446    latency=8.1s
       └─ GENERATION notes-projection      io_in=21250   io_out=4261    latency=8.1s
```

- 根节点：`AGENT react-turn`，`input={"query":..., "agent":...}`，`output={"answer":...}`
- 根节点判据：`is_root_observation=true` 或没有 `parent_observation_id`（两者一致）；`trace_name` 只在根上有值
- 名字都来自我们自己的埋点：`react-turn` / `llm-call` / `tool:<impl_key>` / `turn-projection` / `notes-projection` / `memory-rollover`

---

## 4. 输入形状：保留全文，只砍恒空字段

**保留 14 个字段**：

```
id, trace_id, type, name, parent_observation_id,
start_time, end_time, latency, model, usage_details,
input, output, session_id, level
```

**砍掉的 22 个恒空字段**（本部署上永远为空，纯噪音）：

```
status_message, version, user_id, completion_start_time, internal_model_id,
model_parameters, cost_details, total_cost, usage_pricing_tier_name,
prompt_id, prompt_name, prompt_version, time_to_first_token, model_id,
input_price, output_price, total_price, tags, release,
inputCost, outputCost, usagePricingTierId
```

另外建议一并砍掉：`metadata`（8 个键里 7 个是 OTel 资源属性，唯一有用的 `run_id` 我们本来就有）、`trace_name`、`project_id`、`environment`、`bookmarked`、`public`。

**体量（实测）**：一个回合的 io 合计 **99,843 字符**，外推约 **2 万 ~ 5 万 tokens**。

> 为什么保留全文而不做摘要：蒸馏的目标是"从真实发生的轨迹里提炼经验"。若输入本身是模型转述的，提炼结果就是二手的，无法回溯到原始 observation。而且 system prompt 全文是"模型被要求做什么"的唯一记录——只有它和实际行为都在，才能发现"提示词第 3 条与实际行为冲突"这类深层问题。

---

## 5. 文件与表

### 5.1 目录结构

```
backend/skill/
├── skill-frontmatter/            ← 一个 skill = 一个目录
│   ├── SKILL.md                  frontmatter + 正文
│   └── agents/openai.yaml        Codex 扩展（界面名/短描述）
├── writing-for-agents/
│   ├── SKILL.md
│   ├── SKILL-MECHANICS.md        ← 被 SKILL.md 以相对路径引用的披露参考
│   └── agents/openai.yaml
└── operation-experience/         ← 本项目自动维护的经验 skill
    └── SKILL.md
```

`SKILL.md` 格式（规范只定义 6 个字段）：

```yaml
---
name: operation-experience        # 必须与目录同名；仅小写字母/数字/连字符
description: 做什么 + 何时用 + 关键词   # 1–1024 字符，是唯一的触发指针
---

# Operation Experience
（正文）
```

### 5.2 表结构变更

| 变更 | 内容 |
| --- | --- |
| `skills` 表 | **删 `instructions` 列**；保留 `id` / `name` / `description` / `is_enabled` |
| `agents.skill_ids` | **不变**（仍是 `skills` 表主键数组），装配时用它查 name 再去读文件 |
| `agents` 表 | **新增一列**「自动蒸馏」开关（如 `auto_distill BOOLEAN DEFAULT 0`） |

### 5.3 一致策略（SD17）

- **写路径**：蒸馏写盘成功后，立刻 upsert 表里那一行（新增则 insert）。页面/主 ReAct 立刻可见。
- **启动对账**：扫 `backend/skill/*/SKILL.md` → 目录有表无则 **insert**；表有目录无则**标孤儿（不删）**；`description` 漂移则 **update**。
- 沿用既有模式：`app/main.py` 的 `sweep_interrupted_turns()` 就是启动清扫的先例。
- `is_enabled` **归表**（平台侧开关，不是 skill 内容），文件里不写非规范字段。

---

## 6. 主 ReAct 的 skill 装配（改动点）

现状（`app/agent/loop.py` 的 `build_system_prompt`）：

```python
skill_rows = [await db.get(Skill, sid) for sid in agent.skill_ids]
skills = [r for r in skill_rows if r and r.is_enabled and r.instructions]
base += "\n\n".join(f"【Skill：{r.name}】\n{r.instructions}" for r in skills)
```

改为：

```python
# 绑定的主键 → name → 读文件正文
for row in skill_rows:
    body = read_skill_body(SKILL_DIR / row.name / "SKILL.md")   # 读不到就跳过并告警
    base += f"\n\n【Skill：{row.name}】\n{body}"
```

**已知边界**：经验 skill 必须**在 Agents 页勾选**才会被主 ReAct 用上（SD15 的直接后果）。如果没人勾，蒸馏回路跑再多轮也不会影响真实产出。

---

## 7. 触发与执行（SD18）

- **触发点**：`sessions_router._turn_stream` 收尾之后（assistant 消息落库、SSE 发完 `done`）。
- **执行体**：`asyncio.create_task`，**不能挂在请求上**——`_turn_stream` 的 `finally` 里有 `if not task.done(): task.cancel()`，挂上去会被取消。需要模块级持引用避免被 GC。
- **开关**：读 `agents.auto_distill`，关闭则不起任务。
- **并发**：`asyncio.Lock` 包住"读 skill → 调模型 → 写 skill"全程，避免两个回合同时改写同一个文件。
- **不加标记、没有启动补跑**：进程在蒸馏途中死掉，这一回合的经验永久丢失。这是**有意接受**的——与"观测是旁路"同一个态度。

---

## 8. 校验与降级（SD19 / SD20）

写盘前的校验，任一不过即**放弃本回合、文件一字不动**：

| 校验 | 规则 |
| --- | --- |
| frontmatter 存在且可解析 | `---` 包夹，YAML 合法 |
| `name` | 代码**强制写回** `operation-experience`（模型写的忽略） |
| `description` | 1–1024 字符、非空；**代码强制写回**（取当前文件里的那份，模型写的丢掉） |
| 正文长度 | `len(正文) <= 8000` 字符（≈5000 tokens，对齐规范的 level-2 建议值） |
| 超限处理 | 把"你这次输出 X 字符，超过上限 8000，请压缩到 8000 以内再输出一次"作为追加一轮喂回，**最多重试 1 次**；仍超则放弃 |
| 写盘 | 临时文件 + rename（原子），避免半截文件 |

> 不设行数上限。
>
> **重试是必需的**：如果只是"超限就拒绝写盘"，模型下一回合看到的仍是没变过的旧文件，会**每回合都写出超限内容 → 每回合都被拒 → 经验永远长不大**。带反馈的重试给它一次剪枝的机会。

---

## 9. 页面

### Skills 页

- 列出所有 skill：**只有 name + description**（`instructions` 字段从页面和 schema 中删除）
- **可新建**：填 name + description → 后端建目录 + 写最小 `SKILL.md`（frontmatter + `# 标题` + 占位）
  - 校验：name 仅小写字母/数字/连字符、1–64、不得首尾连字符、不得连续连字符、须与目录同名；description 1–1024；重名拒绝；name 不得含 `/`、`\`、`..`
  - 写盘后立刻 upsert 表
  - 不生成 `agents/openai.yaml`
- **不可编辑**（description 也不可改）：因为文件是源、表是缓存，页面改的值会被启动对账静默回滚
- **删除**：只允许删**孤儿行**（磁盘上已不存在、表里还留着的）；不允许从页面删磁盘目录

### Agent 页

- 新增「**自动蒸馏**」开关（`agents.auto_distill`），控制这个 Agent 的回合结束后是否触发蒸馏

---

## 10. 实现状态（2026-09-29）

### 新增

```
backend/app/skill/store.py            文件层：读写 SKILL.md + 格式校验 + 原子写
backend/app/skill/sync.py             表与目录一致：启动对账 + 写路径 upsert
backend/app/skill/trace_source.py     取轨迹：Langfuse v2 + 重试 + 字段削减
backend/app/skill/distill_prompts.py  两轮提示词（代码常量，页面只读）
backend/app/skill/distill.py          两轮调用 → 校验 → 原子写盘 → 后台任务
backend/skill/operation-experience/SKILL.md   经验 skill 的初始骨架
backend/scripts/migrate_skill_files.py        迁移（删 5 个旧列 + 加 auto_distill）
backend/tests/test_skill_store.py             37 项测试
```

### 改动

| 文件 | 改了什么 |
| --- | --- |
| `app/db/models.py` | `skills` 删 `instructions`；`agents` 加 `auto_distill` |
| `app/schemas/resource.py` | `SkillIn/Out` 去 `instructions`、加 `is_orphan`；`AgentIn/Out` 加 `auto_distill` |
| `app/api/v1/resource_router.py` | skills 三处特殊化：新建落盘、编辑 405、删除限孤儿 |
| `app/agent/loop.py` | `build_system_prompt` 的 skill 正文改为**读文件** |
| `app/main.py` | 启动时跑 `sync_skill_index()`（对账） |
| `app/api/v1/sessions_router.py` | 回合结束后（成功/失败都）`schedule(run_id)` |
| `scripts/seed_demo.py` | 两个 skill 改成"落盘 + 建索引行"，名字改成合法 slug |
| `pyproject.toml` | 加 `pyyaml` |
| `SkillsView.vue` / `AgentsView.vue` / `ResourceWorkbench.vue` | 页面按 SD22 改造 |

### 实现期踩到并修掉的两个坑

1. **`model_dump(mode="json")` 无视 `by_alias=False`** —— 键变成 `startTime`/`traceId`，
   `start_time`、`trace_id`、`session_id` 会被削减逻辑当成"没见过的键"**静默丢掉**，
   结果是一份几乎没有元数据的轨迹（现象：排序乱、`KeyError: start_time`）。
   修法：`model_dump()` 取蛇形键 + `_jsonable()` 自己转 ISO；并加防呆（见 §3.1）。
2. **`json.dumps(trace)` 遇 datetime 直接抛 TypeError** —— 被 `except` 吞掉后表现为
   "蒸馏静默失败"。这也是第 1 条的诱因（本想用 `mode="json"` 解决时间序列化，结果引入键名问题）。

### 第一次真跑（run#154 / Agent #36 / deepseek-reasoner）

```
取轨迹 6 条 → 35 千字符；两轮调用；产出 2,177 字符的 SKILL.md
```

模型自己写出 6 条带证据的经验，其中一条是**它自己发现的真问题**：

> `app/agent/tool_docs.py` 的 `TOOL_DOCS` 里**没有 `recall`**，于是
> `render_tools_text()` 会把 `"以下工具没有在 app/agent/tool_docs.py 里登记说明，请补上：recall"`
> 拼进 **运行时 system prompt** —— 一句维护者待办渗进了模型每回合的上下文。

这条不是预先写进提示词的，是从轨迹里推断出来的，可视为本机制有效的第一个证据。

### 后续增补（同日）

**① `description` 改为锁定（SD14 修订）**

原方案让模型随内容更新 `description`。实际跑了几轮后发现它会**漂成"当前有哪些条目"的罗列**
（实测从一句触发指针涨到 457 字符，变成一份正文目录）。这会破坏它唯一的用途——决定
"什么时候该召回这个 skill"——而且要跨很多回合才看得出来。

改法：`normalize_skill_text(..., force_description=...)` 强制写回**当前文件里的那一份**
（首次用 `store.SEED_DESCRIPTION`）。模型改不动；人要改就直接改文件的 frontmatter。
第 2 轮提示词也同步改成"frontmatter 原样抄回来，不要尝试改写 description"。

**② 蒸馏链路接入 Langfuse**

整条链路现在是一棵 `skill-distill` 树（`as_type="agent"`，tag `skill-distill`，
按 `session_id` 与主循环聚在同一个会话下）：

```text
AGENT       skill-distill            input={run_id, question, skill}
  ├─ SPAN        trace-fetch         output={observations, fetched}
  ├─ GENERATION  skill-distill-round1
  ├─ SPAN        load-skills         output={loaded, missing, chars}
  ├─ GENERATION  skill-distill-round2
  └─ SPAN        write-skill         output={ok, path, chars}
        （超长重试时再多一条 GENERATION skill-distill-compress）
```

根节点的 `output` 直接给出 `{updated, loaded_skills, body_chars}`，或失败时的
`{updated: false, reason}`（`no-trace` / `round1-failed` / `round2-failed` /
`invalid` / `still-too-long` / `write-failed`）—— 排查"为什么这次没更新"时不用翻日志。

## 11. 已知代价与风险

| 项 | 说明 |
| --- | --- |
| 自我评判偏差 | 蒸馏用的是主链路同一模型（SD9）。它刚跑完这个回合，再让它评判自己犯的错，倾向于自我合理化。当前无缓解手段 |
| 长回合可能超窗 | deepseek-chat 窗口 65536，而输入可达 2~5 万 tokens。超窗会失败——因 SD19 而**安全失败**（该回合不学习） |
| 无补跑 | 进程在蒸馏途中死掉＝该回合经验丢失（SD18 有意为之） |
| 逐轮全文重写 | A 方案（SD8）下模型每回合重写整份文件，存在**内容漂移**风险。缓解：8000 字符上限 + 带反馈重试 + 启动对账；**不缓解**：模型可能悄悄删掉某条已有经验（除非引入版本快照） |
| 经验 skill 需手勾 | SD15 的后果：不在 Agents 页勾选，整条回路对主链路没有任何影响 |
| 每回合 2 次 LLM 调用 | 第 1 轮选 skill、第 2 轮产出全文，成本约为单次调用的 2 倍 |

---

## 附录 A：实测环境

```
Langfuse 服务端  v4.38.0（events_only 模式）
Python SDK      4.15.4
embedding       remote / qwen3.7-text-embedding / 1024
模型配置        deepseek-chat（65536 / max_tokens 8192）
                deepseek-reasoner（120000 / max_tokens 8192）
```

## 附录 B：本次实测产生的文件（均在 .gitignore 内，勿提交）

```
data/local/trace-dumps/trace_raw.json        接口原样返回
data/local/trace-dumps/trace_readable.json   解析版：turn + steps[]，io 已解析，tool_calls.arguments 已拆成对象
```
