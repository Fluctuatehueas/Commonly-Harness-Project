<script setup lang="ts">
// 组模式：左侧我的团队组、中间组表单、右侧 Agent 池。
// 全部使用发丝线 + 透明背景，避免卡片化。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Flag } from '@element-plus/icons-vue'
import SegmentedSwitch from '@/components/console/SegmentedSwitch.vue'
import HoloTooltip from '@/components/console/HoloTooltip.vue'
import { useAgentStore } from '@/stores/agents'
import type { AgentGroup } from '@/api/agentGroups'
import AgentListRow from './AgentListRow.vue'
import { avatarGradient, avatarInitial } from './avatar'

const agents = useAgentStore()
const selectedId = ref('')
const saving = ref(false)
const newOpen = ref(false)

const newForm = reactive({
  name: '',
  summary: '',
  visibility: 'private' as 'private' | 'discoverable',
})

const form = reactive({
  name: '',
  summary: '',
  visibility: 'private' as 'private' | 'discoverable',
  extra_subagent_names: [] as string[],
})

const modelForm = reactive({
  model_name: '',
  base_url: '',
  api_key: '',
  thinking_enabled: false,
  context_limit: 128000,
})
const useModel = ref(false)

const VISIBILITY_OPTIONS: Array<{ value: 'private' | 'discoverable'; label: string }> = [
  { value: 'private', label: '仅组内' },
  { value: 'discoverable', label: '可被发现' },
]

const GLOBAL_SUBAGENTS = [
  { value: 'document_reader', label: '文档阅读' },
  { value: 'intent_parsing', label: '意图解析' },
  { value: 'parallel_executor', label: '并行执行' },
  { value: 'web_researcher', label: '联网检索' },
  { value: 'vision_analyst', label: '视觉分析' },
  { value: 'skill_curator', label: '技能维护' },
]

const current = computed<AgentGroup | undefined>(() =>
  agents.groups.find((group) => group.group_id === selectedId.value),
)

const members = computed(() =>
  (current.value?.members || []).map((member) => {
    const profile = agents.byId(member.agent_id)
    return {
      ...member,
      name: profile?.name || member.agent_id,
      role_title: profile?.role_title || '',
    }
  }),
)

function inGroup(agentId: string): boolean {
  return (current.value?.members || []).some((member) => member.agent_id === agentId)
}

// 右侧 Agent 池分组：已加入置顶，未加入在后，各自保持原有顺序。
const joinedAgents = computed(() => agents.items.filter((agent) => inGroup(agent.id)))
const otherAgents = computed(() => agents.items.filter((agent) => !inGroup(agent.id)))

function leaderName(group: AgentGroup): string {
  if (!group.leader_agent_id) return '未设队长'
  return agents.byId(group.leader_agent_id)?.name || group.leader_agent_id
}

function resetForm(group?: AgentGroup) {
  selectedId.value = group?.group_id || ''
  form.name = group?.name || ''
  form.summary = group?.summary || ''
  form.visibility = group?.visibility || 'private'
  form.extra_subagent_names = [...(group?.extra_subagent_names || [])]
  const model = group?.model || {}
  useModel.value = Boolean(model.model_name || model.base_url || model.has_api_key)
  modelForm.model_name = model.model_name || ''
  modelForm.base_url = model.base_url || ''
  modelForm.api_key = ''
  modelForm.thinking_enabled = Boolean(model.thinking_enabled)
  modelForm.context_limit = model.context_limit || 128000
}

function selectGroup(group: AgentGroup) {
  resetForm(group)
}

function openNew() {
  newForm.name = ''
  newForm.summary = ''
  newForm.visibility = 'private'
  newOpen.value = true
}

async function createGroup() {
  if (!newForm.name.trim()) {
    ElMessage.warning('请先填写组名')
    return
  }
  saving.value = true
  try {
    const group = await agents.createGroup({
      name: newForm.name.trim(),
      summary: newForm.summary.trim(),
      visibility: newForm.visibility,
    })
    newOpen.value = false
    resetForm(group)
    ElMessage.success('组已创建，从右侧加入成员')
  } catch (error) {
    ElMessage.error(`创建失败：${(error as Error).message}`)
  } finally {
    saving.value = false
  }
}

function modelPayload(): Record<string, unknown> {
  if (!useModel.value) return {}
  return {
    model_name: modelForm.model_name || undefined,
    base_url: modelForm.base_url || undefined,
    api_key: modelForm.api_key || undefined,
    thinking_enabled: modelForm.thinking_enabled,
    context_limit: modelForm.context_limit,
  }
}

async function save() {
  if (!current.value) return
  if (!form.name.trim()) {
    ElMessage.warning('请先填写组名')
    return
  }
  saving.value = true
  try {
    const group = await agents.saveGroup(current.value.group_id, {
      name: form.name.trim(),
      summary: form.summary.trim(),
      visibility: form.visibility,
      extra_subagent_names: form.extra_subagent_names,
      model: modelPayload(),
    })
    resetForm(group)
    ElMessage.success('已保存')
  } catch (error) {
    ElMessage.error(`保存失败：${(error as Error).message}`)
  } finally {
    saving.value = false
  }
}

async function removeGroup() {
  if (!current.value) return
  try {
    await ElMessageBox.confirm(
      `删除组「${current.value.name}」？Agent 档案不会删除，只会解除组籍。`,
      '删除组',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await agents.removeGroup(current.value.group_id)
    resetForm(agents.groups[0])
    ElMessage.success('已删除')
  } catch (error) {
    ElMessage.error(`删除失败：${(error as Error).message}`)
  }
}

async function addMember(agentId?: string) {
  if (!current.value || !agentId || inGroup(agentId)) return
  try {
    await agents.addGroupMember(current.value.group_id, agentId)
  } catch (error) {
    ElMessage.error(`加入失败：${(error as Error).message}`)
  }
}

async function removeMember(agentId: string) {
  if (!current.value) return
  try {
    await agents.removeGroupMember(current.value.group_id, agentId)
  } catch (error) {
    ElMessage.error(`移除失败：${(error as Error).message}`)
  }
}

async function toggleLeader(row: { agent_id: string; role: string }) {
  if (!current.value) return
  try {
    if (row.role === 'leader') {
      await agents.clearLeader(current.value.group_id)
      ElMessage.success('已取消队长')
    } else {
      await agents.transferLeader(current.value.group_id, row.agent_id)
      ElMessage.success('已设为队长')
    }
  } catch (error) {
    ElMessage.error(`操作失败：${(error as Error).message}`)
  }
}

onMounted(async () => {
  await agents.load()
  await agents.loadGroups(true)
  resetForm(agents.groups[0])
})

// 组列表由父页面并发加载时，这里只负责在首次就绪后自动选中第一个。
watch(
  () => agents.groups.length,
  () => {
    if (!selectedId.value && agents.groups.length) resetForm(agents.groups[0])
  },
)
</script>

<template>
  <div class="group-workspace">
    <aside class="group-list">
      <div class="list-head">
        <span>我的团队组</span>
        <small>{{ agents.groups.length }}</small>
      </div>
      <div class="list-scroll">
        <button
          v-for="group in agents.groups"
          :key="group.group_id"
          type="button"
          class="list-row"
          :class="{ active: group.group_id === selectedId }"
          @click="selectGroup(group)"
        >
          <span class="row-mark">{{ avatarInitial(group.name) }}</span>
          <span class="row-main">
            <strong>{{ group.name }}</strong>
            <small>{{ group.members.length }} 人 · {{ leaderName(group) }}</small>
          </span>
          <span v-if="!group.leader_agent_id" class="row-warn">未设队长</span>
          <span v-else-if="group.visibility === 'discoverable'" class="row-flag">可发现</span>
        </button>
        <el-empty
          v-if="!agents.groups.length"
          description="还没有组，点击右上角新建"
          :image-size="50"
        />
      </div>
    </aside>

    <section class="group-editor">
      <header class="editor-head">
        <div class="editor-title">
          <h2>{{ current?.name || '团队组' }}</h2>
          <small v-if="current">
            {{ members.length }} 名成员 · {{ leaderName(current) }}
          </small>
          <small v-else>选择一个组查看与编辑</small>
        </div>
        <div class="editor-actions">
          <el-button size="small" @click="openNew">新建组</el-button>
          <el-button v-if="current" size="small" type="primary" :loading="saving" @click="save">
            保存
          </el-button>
          <el-button v-if="current" size="small" text type="danger" @click="removeGroup">
            删除
          </el-button>
        </div>
      </header>

      <div v-if="current" :key="current.group_id" class="editor-scroll">
        <section class="form-block">
          <h3>基础信息</h3>
          <div class="grid-2">
            <label>
              <span>组名</span>
              <el-input v-model="form.name" placeholder="如：数据分析组" />
            </label>
            <div class="inline-field">
              <span>可见性</span>
              <SegmentedSwitch v-model="form.visibility" :options="VISIBILITY_OPTIONS" />
            </div>
          </div>
          <label>
            <span>组概述</span>
            <el-input
              v-model="form.summary"
              type="textarea"
              :rows="3"
              placeholder="会进入成员提示词，说明这个组负责什么、不负责什么"
            />
          </label>
        </section>

        <section class="form-block">
          <h3>
            组模型
            <small>成员未单独配置模型时继承组模型，再向上继承用户默认</small>
          </h3>
          <div class="inline-field">
            <span>使用组模型</span>
            <el-switch v-model="useModel" />
          </div>
          <template v-if="useModel">
            <div class="grid-2">
              <label>
                <span>模型名</span>
                <el-input v-model="modelForm.model_name" placeholder="如 deepseek-v4-flash" />
              </label>
              <label>
                <span>上下文窗口</span>
                <el-select v-model="modelForm.context_limit">
                  <el-option :value="128000" label="128K" />
                  <el-option :value="200000" label="200K" />
                  <el-option :value="256000" label="256K" />
                  <el-option :value="1000000" label="1M" />
                </el-select>
              </label>
              <label>
                <span>Base URL</span>
                <el-input v-model="modelForm.base_url" placeholder="https://api.deepseek.com" />
              </label>
              <label>
                <span>API Key</span>
                <el-input
                  v-model="modelForm.api_key"
                  type="password"
                  show-password
                  autocomplete="off"
                  :placeholder="current.model?.has_api_key ? '留空保持不变' : '未配置'"
                />
              </label>
            </div>
            <div class="inline-field">
              <span>思考能力</span>
              <el-switch v-model="modelForm.thinking_enabled" />
            </div>
          </template>
        </section>

        <section class="form-block">
          <h3>
            额外通用子 Agent
            <small>留空表示队长只能委派本组成员</small>
          </h3>
          <el-select
            v-model="form.extra_subagent_names"
            multiple
            collapse-tags
            collapse-tags-tooltip
            placeholder="队长除本组成员外，还可委派的通用子 Agent"
          >
            <el-option
              v-for="item in GLOBAL_SUBAGENTS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </section>

        <section class="form-block">
          <h3>
            成员与队长
            <small>悬停头像显示旗帜按钮；点击设为队长，再次点击取消</small>
          </h3>
          <div class="avatar-grid">
            <div
              v-for="row in members"
              :key="row.agent_id"
              class="avatar-cell"
              :class="{ leader: row.role === 'leader' }"
            >
              <div class="avatar-wrap">
                <span class="avatar-circle" :style="{ background: avatarGradient(row.agent_id) }">
                  {{ avatarInitial(row.name) }}
                </span>
                <HoloTooltip
                  :title="row.role === 'leader' ? '取消队长' : '设为队长'"
                  :content="row.role === 'leader' ? '点击取消该成员的队长身份' : '点击把该成员设为组长'"
                  placement="top"
                  :width="200"
                >
                  <button
                    type="button"
                    class="flag-btn"
                    :class="{ on: row.role === 'leader' }"
                    @click="toggleLeader(row)"
                  >
                    <el-icon><Flag /></el-icon>
                  </button>
                </HoloTooltip>
                <HoloTooltip
                  v-if="row.role !== 'leader'"
                  title="移出组"
                  content="点击把该成员移出当前组"
                  placement="top"
                  :width="190"
                >
                  <button type="button" class="remove-btn" @click="removeMember(row.agent_id)">
                    ×
                  </button>
                </HoloTooltip>
              </div>
              <span class="avatar-name">{{ row.name }}</span>
              <small>{{ row.role === 'leader' ? '队长' : row.duty || row.role_title || '成员' }}</small>
            </div>
            <div v-if="!members.length" class="avatar-empty">
              还没有成员，从右侧 Agent 池加入
            </div>
          </div>
        </section>
      </div>

      <el-empty v-else description="选择或新建一个组" :image-size="70" />
    </section>

    <aside class="agent-pool">
      <div class="list-head">
        <span>Agent 池</span>
        <small>{{ agents.items.length }}</small>
      </div>
      <div class="list-scroll">
        <template v-if="joinedAgents.length">
          <div class="pool-divider">
            <span>已加入</span>
            <small>{{ joinedAgents.length }}</small>
          </div>
          <AgentListRow
            v-for="agent in joinedAgents"
            :key="agent.id"
            :agent="agent"
            :active="true"
            :disabled="!current || !agent.enabled"
            :avatar-action="current && agent.enabled ? 'remove' : null"
            @click="addMember(agent.id)"
            @avatar-action="removeMember(agent.id)"
          >
            <span class="pool-state">已加入</span>
          </AgentListRow>
        </template>
        <div class="pool-divider">
          <span>{{ joinedAgents.length ? '未加入' : '全部 Agent' }}</span>
          <small>{{ otherAgents.length }}</small>
        </div>
        <AgentListRow
          v-for="agent in otherAgents"
          :key="agent.id"
          :agent="agent"
          :disabled="!current || !agent.enabled"
          :avatar-action="current && agent.enabled ? 'add' : null"
          @click="addMember(agent.id)"
          @avatar-action="addMember(agent.id)"
        />
      </div>
    </aside>

    <el-dialog v-model="newOpen" title="新建组" width="480px">
      <div class="dialog-form">
        <label>
          <span>组名 <em>*</em></span>
          <el-input v-model="newForm.name" placeholder="如：数据分析组" />
        </label>
        <label>
          <span>组概述</span>
          <el-input
            v-model="newForm.summary"
            type="textarea"
            :rows="3"
            placeholder="这个组负责什么、不负责什么"
          />
        </label>
        <div class="inline-field">
          <span>可见性</span>
          <SegmentedSwitch v-model="newForm.visibility" :options="VISIBILITY_OPTIONS" />
        </div>
      </div>
      <template #footer>
        <el-button @click="newOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="createGroup">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.group-workspace {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr) 240px;
  height: 100%;
  min-height: 0;
}

.group-list,
.agent-pool {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 0 8px;
  border-right: 1px solid var(--line);
}

.agent-pool {
  border-right: 0;
  border-left: 1px solid var(--line);
}

.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  height: 40px;
  padding: 0 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
  flex: none;
}

.list-head small {
  font-size: 10px;
  font-weight: 400;
  color: var(--text-3);
}

.list-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-bottom: 12px;
}

.list-row {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  min-height: 42px;
  padding: 6px 8px;
  text-align: left;
  color: var(--text-2);
  border: 0;
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.list-row:hover:not(:disabled) {
  color: var(--text-1);
  background: var(--bg-hover);
}

.list-row.active {
  color: var(--text-1);
  background: rgba(55, 208, 196, 0.08);
}

.row-mark {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: none;
  font-size: 12px;
  font-weight: 700;
  color: #041018;
  border-radius: 4px;
  background: linear-gradient(135deg, #f6c85f, #f08a5d);
}

.row-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
  line-height: 1.3;
}

.row-main strong {
  font-size: 12.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-main small {
  font-size: 10px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-warn,
.row-flag,
.pool-divider {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 8px 4px;
  font-size: 9.5px;
  letter-spacing: 0.04em;
  color: var(--text-3);
}

.pool-divider:first-child {
  padding-top: 4px;
}

.pool-state {
  flex: none;
  font-size: 9px;
  color: var(--text-3);
}

.row-warn {
  color: rgba(246, 200, 95, 0.85);
}

.group-editor {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.editor-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: 56px;
  padding: 0 18px;
  border-bottom: 1px solid var(--line);
  flex: none;
}

.editor-title h2 {
  margin: 0;
  font-size: 15px;
  color: var(--text-1);
}

.editor-title small {
  font-size: 10px;
  color: var(--text-3);
}

.editor-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.editor-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 18px 24px;
}

.form-block {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 0;
  border-bottom: 1px solid var(--line);
}

.form-block:last-child {
  border-bottom: 0;
}

.form-block h3 {
  margin: 0;
  font-size: 12.5px;
  color: var(--text-1);
}

.form-block h3 small {
  margin-left: 8px;
  font-size: 10px;
  font-weight: 400;
  color: var(--text-3);
}

.grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 11px;
  color: var(--text-2);
}

label em {
  font-style: normal;
  color: var(--danger);
}

.inline-field {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 11.5px;
  color: var(--text-2);
}

.avatar-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 18px;
  padding: 4px 0;
}

.avatar-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  width: 78px;
}

.avatar-wrap {
  position: relative;
  width: 52px;
  height: 52px;
}

.avatar-circle {
  display: grid;
  place-items: center;
  width: 52px;
  height: 52px;
  font-size: 18px;
  font-weight: 700;
  color: #041018;
  border-radius: 50%;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

.avatar-cell.leader .avatar-circle {
  outline: 2px solid rgba(246, 200, 95, 0.9);
  outline-offset: 2px;
}

.flag-btn,
.remove-btn {
  position: absolute;
  display: grid;
  place-items: center;
  width: 19px;
  height: 19px;
  padding: 0;
  font-size: 11px;
  color: #231a05;
  border: 1px solid rgba(246, 200, 95, 0.8);
  border-radius: 50%;
  background: rgba(8, 14, 27, 0.92);
  cursor: pointer;
  opacity: 0;
  transform: scale(0.85);
  transition: opacity 0.14s ease, transform 0.14s ease, background 0.14s ease;
}

.flag-btn {
  top: -3px;
  right: -3px;
  color: rgba(246, 200, 95, 0.9);
}

.flag-btn.on,
.avatar-cell:hover .flag-btn,
.avatar-cell:hover .remove-btn {
  opacity: 1;
  transform: scale(1);
}

.flag-btn.on {
  color: #231a05;
  background: linear-gradient(135deg, #f6c85f, #f08a5d);
}

.remove-btn {
  top: -3px;
  left: -3px;
  color: var(--text-2);
  border-color: var(--line-strong);
}

.remove-btn:hover {
  color: var(--danger);
  border-color: rgba(255, 96, 96, 0.55);
}

.avatar-name {
  max-width: 78px;
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.avatar-cell small {
  max-width: 78px;
  font-size: 9.5px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.avatar-empty {
  padding: 14px 0;
  font-size: 11px;
  color: var(--text-3);
}

.dialog-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

@media (max-width: 1200px) {
  .group-workspace {
    grid-template-columns: 200px minmax(0, 1fr) 200px;
  }
}
</style>
