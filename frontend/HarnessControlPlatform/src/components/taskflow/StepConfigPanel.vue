<script setup lang="ts">
// 步骤配置：名称 / 指令 / SKILL / 执行者 / 限制条件 / 示例 / 校验点
// Element Plus 表单 + 分组式布局：基本信息、能力与执行、约束与验收。
import { computed, onMounted } from 'vue'
import {
  Aim,
  Cpu,
  Document,
  EditPen,
  Flag,
  MagicStick,
  Select,
  Warning,
} from '@element-plus/icons-vue'
import type { StepDef } from '@/api/taskflow'
import { useAgentStore } from '@/stores/agents'
import { avatarGradient, avatarInitial } from '@/components/myagent/avatar'
import HoloTooltip from '@/components/console/HoloTooltip.vue'

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

// 执行者候选与聊天页共用同一份 MyAgent 列表：头像、岗位、所属组组标保持一致。
const agents = useAgentStore()
onMounted(() => {
  void agents.load()
  void agents.loadGroups()
})

const AGENT_PREFIX = 'agent:'

interface ExecutorGroupBadge {
  group_id: string
  name: string
  role: 'leader' | 'member'
}

interface ExecutorOption {
  value: string
  /** auto / main / agent（MyAgent）/ subagent（历史内置角色） */
  kind: 'auto' | 'main' | 'agent' | 'subagent'
  label: string
  role?: string
  /** MyAgent 原始 id：头像取色与聊天页保持一致 */
  agentId?: string
  avatar?: string
  groups?: ExecutorGroupBadge[]
}

const executorOptions = computed<ExecutorOption[]>(() => {
  const options: ExecutorOption[] = [
    { value: 'auto', kind: 'auto', label: 'Auto', role: '运行时决定' },
    { value: 'main', kind: 'main', label: '主 Agent', role: '由当前主 Agent 执行' },
  ]
  for (const opt of agents.options) {
    options.push({
      value: `${AGENT_PREFIX}${opt.value}`,
      kind: 'agent',
      label: opt.label,
      role: opt.role,
      agentId: opt.value,
      avatar: agents.byId(opt.value)?.avatar || '',
      groups: agents.groupsOf(opt.value).map((group) => ({
        group_id: group.group_id,
        name: group.name,
        role: group.leader_agent_id === opt.value ? ('leader' as const) : ('member' as const),
      })),
    })
  }
  // 历史值兜底：内置流的 subagent 角色、已被删除的 Agent 仍要能显示出来。
  const current = props.step?.executor || 'auto'
  if (!options.some((item) => item.value === current)) {
    if (current.startsWith(AGENT_PREFIX)) {
      const agentId = current.slice(AGENT_PREFIX.length)
      const profile = agents.byId(agentId)
      options.push({
        value: current,
        kind: 'agent',
        label: profile?.name || '已失效 Agent',
        role: profile?.role_title || agentId,
        agentId,
      })
    } else if (current.startsWith('subagent:')) {
      options.push({
        value: current,
        kind: 'subagent',
        label: '子智能体',
        role: current.slice('subagent:'.length),
      })
    }
  }
  return options
})

const executorLabel = computed(() => {
  const value = props.step?.executor || 'auto'
  if (value.startsWith(AGENT_PREFIX)) {
    return agents.byId(value.slice(AGENT_PREFIX.length))?.name || '已失效 Agent'
  }
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
              <el-select
                v-model="executorModel"
                :disabled="readonly"
                popper-class="tf-agent-popper"
                class="fill executor-select"
              >
                <el-option
                  v-for="opt in executorOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                >
                  <div class="agent-option">
                    <span
                      v-if="opt.kind === 'agent'"
                      class="agent-option-avatar"
                      :style="{ background: avatarGradient(opt.agentId || opt.value) }"
                    >
                      {{ avatarInitial(opt.label, opt.avatar) }}
                    </span>
                    <span
                      v-else-if="opt.kind === 'main'"
                      class="agent-option-avatar"
                      :style="{ background: avatarGradient('main') }"
                    >
                      主
                    </span>
                    <span v-else class="agent-option-avatar agent-option-avatar--auto">
                      <el-icon><MagicStick v-if="opt.kind === 'auto'" /><Cpu v-else /></el-icon>
                    </span>
                    <span class="agent-option-main">
                      <strong>{{ opt.label }}</strong>
                      <small v-if="opt.role">{{ opt.role }}</small>
                    </span>
                    <span v-if="opt.groups?.length" class="agent-option-groups">
                      <HoloTooltip
                        v-for="group in opt.groups"
                        :key="group.group_id"
                        :title="group.name"
                        :content="`角色：${group.role === 'leader' ? '组长' : '组员'}`"
                        placement="left"
                        :width="200"
                      >
                        <span
                          class="group-badge"
                          :style="{ background: avatarGradient(group.group_id) }"
                        >
                          {{ avatarInitial(group.name) }}
                          <i v-if="group.role === 'leader'" class="group-badge-flag">
                            <el-icon><Flag /></el-icon>
                          </i>
                        </span>
                      </HoloTooltip>
                    </span>
                  </div>
                </el-option>
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

/* ---------- 执行者下拉：与聊天页 Agent 选择器同款 ---------- */
.executor-select :deep(.el-select__wrapper) {
  min-height: 32px;
}

.agent-option {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 34px;
  line-height: 1.2;
}

.agent-option-avatar {
  display: grid;
  place-items: center;
  flex: none;
  width: 22px;
  height: 22px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  color: #041018;
  border-radius: 50%;
}

.agent-option-avatar--auto {
  font-size: 12px;
  color: var(--text-2);
  border: 1px dashed var(--line);
  background: transparent;
}

.agent-option-main {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex: 1;
  min-width: 0;
}

.agent-option-main strong {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-option-main small {
  flex: none;
  font-size: 10px;
  color: var(--text-3);
}

.agent-option-groups {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: none;
}

.group-badge {
  position: relative;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  color: #041018;
  border-radius: 6px;
}

.group-badge-flag {
  position: absolute;
  right: -3px;
  bottom: -3px;
  display: grid;
  place-items: center;
  width: 11px;
  height: 11px;
  font-size: 7px;
  font-style: normal;
  color: #f6c85f;
  border-radius: 50%;
  background: #0b1622;
}

/* 下拉层 teleport 到 body，只能用全局选择器命中 popper-class。 */
:global(.tf-agent-popper) {
  overflow: hidden;
  border: 1px solid rgba(167, 139, 250, 0.42);
  border-radius: 8px 2px 8px 2px;
  background:
    repeating-linear-gradient(
      116deg,
      rgba(226, 214, 255, 0.04) 0 1px,
      transparent 1px 6px
    ),
    linear-gradient(160deg, rgba(24, 15, 44, 0.98), rgba(9, 8, 20, 0.98));
  box-shadow:
    0 18px 46px rgba(0, 0, 0, 0.62),
    0 0 26px rgba(167, 139, 250, 0.2);
  backdrop-filter: blur(14px);
}

:global(.tf-agent-popper .el-select-dropdown__item) {
  height: auto;
  min-height: 38px;
  padding: 3px 10px;
  color: #cfc7ea;
  font-size: 11.5px;
  line-height: 1.2;
}

:global(.tf-agent-popper .el-select-dropdown__item.is-hovering) {
  background: linear-gradient(90deg, rgba(167, 139, 250, 0.16), rgba(244, 114, 182, 0.1));
  color: #f5efff;
}

:global(.tf-agent-popper .el-select-dropdown__item.is-selected) {
  color: #f0abfc;
  font-weight: 500;
}
</style>
