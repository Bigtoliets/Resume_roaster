---
name: skill-frontmatter
description: Write or validate the YAML frontmatter at the top of a SKILL.md against the Agent Skills specification. Use when creating a skill, editing its frontmatter fields, checking which fields stay portable across agents, or validating a skill package.
---

# SKILL.md frontmatter

Skill 是一个目录，其中至少含一份 `SKILL.md`；`SKILL.md` 由 YAML frontmatter 加 Markdown 正文组成。

```text
skill-name/
├── SKILL.md          # 必需：元数据 + 指示
├── scripts/          # 可选：可执行代码
├── references/       # 可选：文档
├── assets/           # 可选：模板、资源
└── ...               # 其它文件或目录
```

## 规范字段

Agent Skills 标准只定义这 6 个字段，因此它们是唯一跨客户端可移植的部分。

| 字段 | 必需 | 约束 |
| --- | --- | --- |
| `name` | 是 | 1–64 字符；仅小写字母、数字、连字符；不得以连字符开头或结尾，不得出现连续连字符；**必须与所在目录同名** |
| `description` | 是 | 1–1024 字符，非空；同时说明做什么与何时使用，并含便于检索的关键词 |
| `license` | 否 | 许可名称，或指向随附许可文件的引用 |
| `compatibility` | 否 | ≤500 字符；环境要求，如目标产品、系统依赖、网络访问。多数 skill 不需要 |
| `metadata` | 否 | 字符串键到字符串值的映射，存放规范之外的附加信息。键名应尽量唯一 |
| `allowed-tools` | 否 | 空格分隔的预授权工具串。**实验性**，各实现支持程度不一 |

最小可用形式：

```yaml
---
name: skill-name
description: What this skill does and when to use it.
---
```

带可选字段：

```yaml
---
name: pdf-processing
description: Extract PDF text, fill forms, merge files. Use when handling PDFs.
license: Apache-2.0
metadata:
  author: example-org
  version: "1.0"
---
```

`name` 的合法与非法写法：

| 写法 | 结果 |
| --- | --- |
| `pdf-processing`、`data-analysis` | 合法 |
| `PDF-Processing` | 非法：不允许大写 |
| `-pdf` | 非法：不得以连字符开头 |
| `pdf--processing` | 非法：不允许连续连字符 |

`description` 的写法对照：

- 好：`Extracts text and tables from PDF files, fills PDF forms, and merges multiple PDFs. Use when working with PDF documents or when the user mentions PDFs, forms, or document extraction.`
- 差：`Helps with PDFs.`

## 正文

frontmatter 之后的 Markdown 正文承载指示，格式不限。规范推荐包含分步指示、输入输出示例与常见边界情况。

## 渐进披露

客户端分三级加载内容，这决定了细节该放在哪一级：

1. **元数据**（约 100 tokens）：`name` 与 `description`，启动时对全部已安装 skill 加载。
2. **指示**（建议 < 5000 tokens）：skill 被激活时，整份 `SKILL.md` 正文进入上下文。
3. **资源**（按需）：`scripts/`、`references/`、`assets/` 只在需要时读取。

因此：主 `SKILL.md` 控制在 500 行以内，超出部分挪进 `references/`。引用其它文件时使用相对 skill 根的路径，并保持一层深度。

## 校验

```bash
skills-ref validate ./my-skill
```

## 各实现的扩展字段

规范只定义上表 6 个字段。不少客户端在此之上加入了自有字段；**用了就失去可移植性**——在其它客户端可能被静默忽略，也可能导致校验或打包直接失败。

- **Claude Code**：另有十余个扩展字段（调用控制、模型与努力级别、子代理、hooks、路径过滤、参数替换），并定义了命令命名与允许集规则 → 读 [references/claude-code.md](references/claude-code.md)
- **Codex**：附加校验约束，以及 UI 元数据与调用策略所在的 `agents/openai.yaml` → 读 [references/codex.md](references/codex.md)

默认只写这 6 个规范字段。只有在确实需要某平台专有能力时才添加扩展字段，并清楚其代价。

规范来源：<https://agentskills.io/specification>
