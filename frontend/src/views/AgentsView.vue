<script setup lang="ts">
/** Agent：一个 ReAct 主节点 = 模型 + Prompt + 人设 + Skills + 可用工具 + 最大轮次。 */
import { onMounted } from 'vue'

import FormRow from '@/components/form/FormRow.vue'
import JsonField from '@/components/form/JsonField.vue'
import RefMulti from '@/components/form/RefMulti.vue'
import RefSelect from '@/components/form/RefSelect.vue'
import ResourceWorkbench from '@/components/ResourceWorkbench.vue'
import { useResource } from '@/composables/useResource'
import { labelOf, useRefs } from '@/composables/useRefs'

const {
  items,
  filtered,
  draft,
  selectedId,
  isNew,
  loading,
  saving,
  message,
  error,
  search,
  load,
  createNew,
  select,
  duplicate,
  save,
  remove,
} = useResource<any>('agents', () => ({
  name: '',
  description: '',
  persona: '',
  agent_type: 'diagnosis',
  model_config_id: null,
  prompt_id: null,
  skill_ids: [],
  tool_ids: [],
  max_rounds: 12,
  parallel_tools: true,
  params_override: {},
  is_enabled: true,
}))

const skills = useRefs('skills')
const tools = useRefs('tools')
useRefs('prompts')

function names(slug: string, ids: number[] | undefined) {
  const list = ids || []
  return list.length ? list.map((id) => labelOf(slug, id)).join('、') : '—'
}

onMounted(load)
</script>

<template>
  <section>
    <header class="rise" style="margin-bottom: 18px; max-width: 82ch">
      <div class="eyebrow">06 · 平台资源</div>
      <h1 style="font-size: 26px; margin: 6px 0 6px">Agents</h1>
      <p style="color: var(--ink-2); margin: 0">
        一个 Agent 就是 ReAct 主循环：它决定用哪个模型、哪套系统提示词、挂哪些 Skills，
        以及**能用哪些工具**。会话里选它开始对话。
      </p>
      <p class="hint" style="margin-top: 6px">
        其他流程节点（面试官、终裁官）都在 Tools 页；知识库不直接挂在这里，由检索工具承载。
      </p>
    </header>

    <ResourceWorkbench
      index="06"
      title="Agent 配置"
      subtitle="主循环 = 模型 + 系统提示词 + 人设 + Skills + 工具 + 最大轮次；所有引用都用下拉/多选"
      :items="filtered"
      :selected-id="selectedId"
      :draft-name="draft.name"
      :is-new="isNew"
      :is-loading="loading"
      :is-saving="saving"
      :message="message"
      :error="error"
      :search="search"
      @update:search="search = $event"
      @create="createNew"
      @select="select"
      @save="save()"
      @refresh="load"
      @remove="remove"
      @duplicate="duplicate"
    >
      <div class="grid grid--2">
        <FormRow label="名称" required hint="会话里显示的名字，如「简历诊断官」">
          <input v-model="draft.name" placeholder="简历诊断官" />
        </FormRow>
        <FormRow label="类型">
          <select v-model="draft.agent_type">
            <option value="diagnosis">diagnosis（求职/诊断类）</option>
            <option value="general">general（通用对话）</option>
          </select>
        </FormRow>
        <FormRow label="模型" hint="主循环用哪个模型（工具可以各自覆盖）">
          <RefSelect slug="models" v-model="draft.model_config_id" empty-label="— 用 .env 默认模型 —" />
        </FormRow>
        <FormRow label="系统提示词" hint="从 Prompts 库选；留空用内置的默认 ReAct 提示词">
          <RefSelect slug="prompts" v-model="draft.prompt_id" empty-label="— 用内置默认 —" />
        </FormRow>
        <FormRow label="最大轮次" hint="ReAct 循环上限，防止无限调工具烧钱">
          <input type="number" v-model.number="draft.max_rounds" min="1" max="30" />
        </FormRow>
        <FormRow
          label="同轮工具并发"
          hint="模型一轮里同时调多个工具时并发执行（比如技术面试官 + HR 面试官），能省掉一半等待"
        >
          <label class="row" style="gap: 6px">
            <input type="checkbox" style="width: auto" v-model="draft.parallel_tools" />
            <span>{{ draft.parallel_tools ? '并发执行' : '按顺序执行' }}</span>
          </label>
        </FormRow>
        <FormRow label="启用">
          <label class="row" style="gap: 6px">
            <input type="checkbox" style="width: auto" v-model="draft.is_enabled" />
            <span>{{ draft.is_enabled ? '启用' : '停用' }}</span>
          </label>
        </FormRow>
      </div>

      <FormRow label="描述">
        <input v-model="draft.description" placeholder="这个 Agent 负责什么" />
      </FormRow>

      <FormRow label="人设覆盖（可选）" hint="追加到系统提示词后面，用来快速改口吻（比如更毒舌）">
        <textarea rows="4" spellcheck="false" v-model="draft.persona" />
      </FormRow>

      <FormRow label="挂载 Skills" hint="Skill 的指令会作为附加约束拼进系统提示词">
        <RefMulti slug="skills" v-model="draft.skill_ids" empty-hint="还没有 Skill" />
      </FormRow>

      <FormRow
        label="可用工具"
        hint="ReAct 只能调用这里勾选的工具；带「未实现」标记的在后端没有对应代码，调了会失败"
      >
        <RefMulti
          slug="tools"
          v-model="draft.tool_ids"
          empty-hint="还没有工具，先去 Tools 页创建"
        />
      </FormRow>

      <FormRow label="参数覆盖（JSON）" hint="覆盖模型默认参数，如 {&quot;temperature&quot;: 0.3}">
        <JsonField v-model="draft.params_override" :rows="4" />
      </FormRow>

      <div class="card" style="background: var(--paper-2); border-style: dashed">
        <div class="card__body">
          <div class="eyebrow" style="margin-bottom: 8px">主循环预览</div>
          <div class="mono" style="font-size: 11.5px; line-height: 2">
            <div>模型：{{ draft.model_config_id ? labelOf('models', draft.model_config_id) : '（.env 默认）' }}</div>
            <div>
              系统提示词：{{ draft.prompt_id ? labelOf('prompts', draft.prompt_id) : '（内置默认 ReAct 提示词）' }}
            </div>
            <div>Skills：{{ names('skills', draft.skill_ids) }}</div>
            <div>可用工具（{{ (draft.tool_ids || []).length }} 个）：{{ names('tools', draft.tool_ids) }}</div>
            <div>最大轮次：{{ draft.max_rounds }}</div>
          </div>
          <p class="hint" style="margin-top: 8px">
            可选 Skill {{ skills.items.length }} 个 · 可选工具 {{ tools.items.length }} 个
          </p>
        </div>
      </div>
    </ResourceWorkbench>
  </section>
</template>
