<script setup lang="ts">
// 流全局配置：名称 / 描述 / 触发示例 / MCP 服务（流级，所有步骤共享）
// 使用 Element Plus 表单控件，视觉上与深空控制台一致。
import { computed } from 'vue'
import { Close, Plus, Promotion, Connection } from '@element-plus/icons-vue'
import type { TaskFlowSchema } from '@/api/taskflow'

const props = withDefaults(
  defineProps<{
    flow: TaskFlowSchema
    mcpOptions: { id: string; label: string }[]
    readonly?: boolean
  }>(),
  { readonly: false },
)

const mcpModel = computed({
  get: () => [...props.flow.mcp_services],
  set: (value: string[]) => {
    if (props.readonly) return
    props.flow.mcp_services.splice(0, props.flow.mcp_services.length, ...value)
  },
})

function addExample() {
  if (props.readonly) return
  props.flow.trigger_examples.push('')
}

function removeExample(index: number) {
  if (props.readonly) return
  props.flow.trigger_examples.splice(index, 1)
}
</script>

<template>
  <section class="flow-global">
    <header class="section-head">
      <span class="head-icon"><el-icon><Promotion /></el-icon></span>
      <div class="head-text">
        <strong>全局配置</strong>
        <small>名称、描述与触发语义决定路由能否命中这条流程</small>
      </div>
      <el-tag v-if="readonly" type="info" size="small" effect="plain">内置流只读</el-tag>
    </header>

    <div class="global-grid">
      <el-form label-position="top" :disabled="readonly" class="global-form">
        <div class="field-row">
          <el-form-item label="流程名称">
            <el-input
              v-model="flow.name"
              maxlength="50"
              show-word-limit
              placeholder="例如：深度研究报告"
            />
          </el-form-item>
          <el-form-item label="流程描述">
            <el-input
              v-model="flow.description"
              type="textarea"
              :rows="1"
              maxlength="500"
              placeholder="一句话说明这条流程解决什么问题"
            />
          </el-form-item>
        </div>

        <el-form-item label="触发示例">
          <template #label>
            <span class="label-line">
              触发示例
              <em>用户这样说时更容易命中该流程</em>
            </span>
          </template>
          <div class="example-list">
            <div v-for="(_, i) in flow.trigger_examples" :key="i" class="example-row">
              <el-input
                v-model="flow.trigger_examples[i]"
                maxlength="200"
                placeholder="例如：帮我调研一下 XX 方案的优劣"
              />
              <el-button
                v-if="!readonly"
                :icon="Close"
                text
                class="row-del"
                title="移除示例"
                @click="removeExample(i)"
              />
            </div>
            <el-button v-if="!readonly" :icon="Plus" text size="small" @click="addExample">
              添加示例
            </el-button>
            <span v-if="!flow.trigger_examples.length" class="empty-hint">
              暂无触发示例，添加后路由命中率更高
            </span>
          </div>
        </el-form-item>
      </el-form>

      <div class="mcp-field">
        <span class="label-line">
          <el-icon><Connection /></el-icon>
          MCP 服务
          <em>流级配置，所有步骤共享</em>
        </span>
        <el-select
          v-model="mcpModel"
          multiple
          filterable
          clearable
          :disabled="readonly"
          placeholder="选择本流程可用的 MCP 服务"
          class="mcp-select"
        >
          <el-option v-for="m in mcpOptions" :key="m.id" :label="m.id" :value="m.id">
            <span class="opt-id">{{ m.id }}</span>
            <span class="opt-desc">{{ m.label }}</span>
          </el-option>
        </el-select>
        <p v-if="!mcpOptions.length" class="empty-hint">暂无可用 MCP 服务</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.flow-global {
  position: relative;
  padding: 16px 18px 4px;
  border-top: 1px solid var(--line);
  background:
    linear-gradient(180deg, rgba(91, 140, 255, 0.045), transparent 42%),
    transparent;
}

/* 左侧竖向强调线：仪器面板的分区感 */
.flow-global::before {
  content: '';
  position: absolute;
  top: 16px;
  bottom: 16px;
  left: 0;
  width: 2px;
  background: linear-gradient(180deg, var(--accent), transparent 72%);
  opacity: 0.7;
}

.section-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.head-icon {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.28);
  border-radius: var(--radius-sm);
  background: rgba(55, 208, 196, 0.06);
}

.head-text {
  flex: 1;
  min-width: 0;
}

.head-text strong {
  display: block;
  font-size: 13.5px;
  letter-spacing: -0.01em;
}

.head-text small {
  font-size: 10.5px;
  color: var(--text-3);
}

.global-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(240px, 0.7fr);
  gap: 18px;
  align-items: start;
}

.field-row {
  display: grid;
  grid-template-columns: minmax(180px, 0.8fr) minmax(0, 1.2fr);
  gap: 12px;
}

.label-line {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-3);
}

.label-line em {
  font-style: normal;
  font-size: 10px;
  color: var(--text-3);
  opacity: 0.72;
}

.example-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}

.example-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.row-del {
  flex: none;
  color: var(--text-3);
}

.row-del:hover {
  color: var(--danger);
}

.mcp-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: rgba(126, 154, 198, 0.025);
}

.mcp-select {
  width: 100%;
}

.opt-id {
  font-family: var(--font-mono);
  font-size: 12px;
}

.opt-desc {
  margin-left: 8px;
  font-size: 11px;
  color: var(--text-3);
}

.empty-hint {
  font-size: 10.5px;
  color: var(--text-3);
}

@media (max-width: 1080px) {
  .global-grid,
  .field-row {
    grid-template-columns: 1fr;
  }
}
</style>
