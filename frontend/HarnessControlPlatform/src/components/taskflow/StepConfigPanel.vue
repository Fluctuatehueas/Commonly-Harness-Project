<script setup lang="ts">
// 步骤配置：名称 / 指令 / SKILL / 执行者 / 限制条件 / 示例 / 校验点
// Element Plus 表单 + 分组式布局：基本信息、能力与执行、约束与验收。
import { computed } from 'vue'
import {
  Aim,
  Cpu,
  Document,
  EditPen,
  MagicStick,
  Select,
  Warning,
} from '@element-plus/icons-vue'
import type { StepDef } from '@/api/taskflow'

const props = withDefaults(
  defineProps<{
    step: StepDef | null
    /** 面板标题，如「步骤 2」「步骤 2.1 · 中文资料」 */
    title?: string
    stepIndex?: string
    skillOptions: { name: string; label: string }[]
    readonly?: boolean
    /** 422 校验中属于该步骤的错误文案 */
    error?: string
  }>(),
  { title: '', stepIndex: '', readonly: false, error: '' },
)

const skillsAuto = computed(() => !props.step || props.step.skills === 'auto')

const skillModel = computed({
  get: () => (Array.isArray(props.step?.skills) ? props.step.skills : []),
  set: (value: string[]) => {
    if (props.step && !props.readonly && props.step.skills !== 'auto') {
      props.step.skills = value
    }
  },
})

function setSkillsAuto(auto: boolean) {
  if (!props.step || props.readonly) return
  props.step.skills = auto ? 'auto' : []
}

const checkpoint = computed({
  get: () => !!props.step?.checkpoint,
  set: (value: boolean) => {
    if (props.step && !props.readonly) props.step.checkpoint = value
  },
})

const executorModel = computed({
  get: () => props.step?.executor || 'auto',
  set: (value: string) => {
    if (props.step && !props.readonly) props.step.executor = value
  },
})

const executorOptions = computed(() => {
  const base = [
    { value: 'auto', label: 'Auto · 运行时决定' },
    { value: 'main', label: '主 Agent' },
  ]
  const current = props.step?.executor || 'auto'
  if (current.startsWith('subagent:')) {
    base.push({ value: current, label: `子智能体 · ${current.slice('subagent:'.length)}` })
  }
  return base
})

const executorLabel = computed(() => {
  const value = props.step?.executor || 'auto'
  if (value.startsWith('subagent:')) return `子智能体 ${value.slice('subagent:'.length)}`
  return value === 'main' ? '主 Agent' : 'Auto'
})
</script>

<template>
  <section class="step-cfg">
    <header class="section-head">
      <span v-if="stepIndex" class="step-badge">{{ stepIndex }}</span>
      <div class="head-text">
        <strong>{{ step?.name || '步骤配置' }}</strong>
        <small v-if="title">{{ title }}</small>
        <small v-else>在左侧简图中选中一个步骤开始配置</small>
      </div>
      <div v-if="step" class="head-tags">
        <el-tag size="small" effect="plain" type="info">{{ executorLabel }}</el-tag>
        <el-tag v-if="step.checkpoint" size="small" effect="plain" type="success">校验点</el-tag>
        <el-tag v-if="step.parallel?.length" size="small" effect="plain" type="primary">
          {{ step.parallel.length }} 条并行
        </el-tag>
      </div>
    </header>

    <el-empty v-if="!step" :image-size="72" description="尚未选中步骤">
      <span class="empty-tip">单击左侧圆点选择要配置的步骤</span>
    </el-empty>

    <div v-else class="cfg-body">
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
        class="cfg-error"
      />

      <!-- 01 基本信息 -->
      <section class="block">
        <header class="block-head">
          <el-icon><EditPen /></el-icon>
          <span>基本信息</span>
          <em>01</em>
        </header>
        <el-form label-position="top" :disabled="readonly">
          <el-form-item label="步骤名称">
            <el-input v-model="step.name" maxlength="50" placeholder="例如：需求澄清" />
          </el-form-item>
          <el-form-item>
            <template #label>
              <span class="label-line">指令 <em>这一步要做什么，用自然语言描述</em></span>
            </template>
            <el-input
              v-model="step.instruction"
              type="textarea"
              :rows="4"
              maxlength="2000"
              show-word-limit
              placeholder="例如：确认研究主题边界、输出格式与篇幅要求，不开始检索"
            />
          </el-form-item>
        </el-form>
      </section>

      <!-- 02 能力与执行 -->
      <section class="block">
        <header class="block-head">
          <el-icon><Cpu /></el-icon>
          <span>能力与执行</span>
          <em>02</em>
        </header>
        <el-form label-position="top" :disabled="readonly">
          <div class="two-col">
            <el-form-item>
              <template #label>
                <span class="label-line">
                  <el-icon><MagicStick /></el-icon>
                  SKILL
                </span>
              </template>
              <div class="skill-switch">
                <el-switch
                  :model-value="skillsAuto"
                  :disabled="readonly"
                  @update:model-value="(v: string | number | boolean) => setSkillsAuto(Boolean(v))"
                />
                <span class="switch-text">自动选择（Auto）</span>
              </div>
            </el-form-item>
            <el-form-item>
              <template #label>
                <span class="label-line">
                  <el-icon><Aim /></el-icon>
                  执行者
                </span>
              </template>
              <el-select v-model="executorModel" :disabled="readonly" class="fill">
                <el-option
                  v-for="opt in executorOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
          </div>

          <el-form-item v-if="!skillsAuto" label="指定 SKILL">
            <el-select
              v-model="skillModel"
              multiple
              filterable
              clearable
              :disabled="readonly"
              placeholder="选择一个或多个技能"
              class="fill"
            >
              <el-option
                v-for="s in skillOptions"
                :key="s.name"
                :label="s.name"
                :value="s.name"
              >
                <span class="opt-id">{{ s.name }}</span>
                <span class="opt-desc">{{ s.label }}</span>
              </el-option>
            </el-select>
            <p v-if="!skillOptions.length" class="hint">暂无可用技能</p>
          </el-form-item>
        </el-form>
      </section>

      <!-- 03 约束与验收 -->
      <section class="block">
        <header class="block-head">
          <el-icon><Warning /></el-icon>
          <span>约束与验收</span>
          <em>03</em>
        </header>
        <el-form label-position="top" :disabled="readonly">
          <el-form-item>
            <template #label>
              <span class="label-line">限制条件 <em>这一步不允许做什么，可留空</em></span>
            </template>
            <el-input
              v-model="step.constraints"
              type="textarea"
              :rows="2"
              maxlength="1000"
              placeholder="例如：只澄清，不执行检索"
            />
          </el-form-item>
          <el-form-item>
            <template #label>
              <span class="label-line">
                <el-icon><Document /></el-icon>
                示例（可选）
                <em>期望产出的格式 / 结构 / 风格，校验时作为对照基准</em>
              </span>
            </template>
            <el-input
              v-model="step.example"
              type="textarea"
              :rows="3"
              maxlength="4000"
              placeholder="例如：# 调研报告&#10;## 结论&#10;## 对比分析&#10;## 引用来源"
            />
          </el-form-item>
          <el-form-item>
            <div class="checkpoint-row">
              <el-switch v-model="checkpoint" :disabled="readonly" />
              <div class="checkpoint-text">
                <span class="cp-title">
                  <el-icon><Select /></el-icon>
                  校验点
                </span>
                <span class="cp-desc">完成后插入校验节点，不合格自动重试并兜底</span>
              </div>
            </div>
          </el-form-item>
        </el-form>
      </section>
    </div>
  </section>
</template>

<style scoped>
.step-cfg {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: rgba(11, 18, 32, 0.42);
}

.section-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
}

.step-badge {
  display: grid;
  place-items: center;
  min-width: 30px;
  height: 30px;
  padding: 0 8px;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.4);
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
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.head-text small {
  font-size: 10.5px;
  color: var(--text-3);
}

.head-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.empty-tip {
  font-size: 11px;
  color: var(--text-3);
}

.cfg-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 16px 16px;
  overflow: auto;
}

.cfg-error {
  margin-top: 10px;
}

/* 每个分组：左侧编号 + 竖线，形成仪表盘分区 */
.block {
  position: relative;
  padding: 12px 0 2px 14px;
  border-left: 1px solid var(--line);
}

.block-host {
  border-left-color: var(--line);
}

.block-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-2);
  letter-spacing: 0.02em;
}

.block-head em {
  margin-left: auto;
  font-style: normal;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-3);
  opacity: 0.6;
}

.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.skill-switch {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
}

.switch-text {
  font-size: 12px;
  color: var(--text-2);
}

.label-line {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  color: var(--text-3);
}

.label-line em {
  font-style: normal;
  font-size: 10px;
  opacity: 0.75;
}

.checkpoint-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: rgba(126, 154, 198, 0.025);
  width: 100%;
}

.checkpoint-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.cp-title {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-1);
}

.cp-desc {
  font-size: 10.5px;
  color: var(--text-3);
}

.fill {
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

.hint {
  margin: 6px 0 0;
  font-size: 10.5px;
  color: var(--text-3);
}
</style>
