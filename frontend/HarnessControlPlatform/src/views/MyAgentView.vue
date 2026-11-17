<script setup lang="ts">
// MyAgent：Agent 模式编辑提示词/模型/MCP；组模式管理团队组与成员。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Connection, Plus, Refresh, Setting, UserFilled } from '@element-plus/icons-vue'
import TopNav from '@/components/console/TopNav.vue'
import AppIcon from '@/components/console/AppIcon.vue'
import HoloTooltip from '@/components/console/HoloTooltip.vue'
import { fetchMcpServers, type McpServerItem } from '@/api/mcp'
import { fetchGlobalModelSettings, type GlobalModelSettings } from '@/api/settings'
import { useAgentStore } from '@/stores/agents'
import AgentGroupsDrawer from '@/components/myagent/AgentGroupsDrawer.vue'
import AgentListRow from '@/components/myagent/AgentListRow.vue'
import AgentModelDrawer from '@/components/myagent/AgentModelDrawer.vue'
import GroupWorkspace from '@/components/myagent/GroupWorkspace.vue'
import type { AgentProfile } from '@/api/agents'

const agents = useAgentStore()
const viewMode = ref<'agent' | 'group'>('agent')
const selectedId = ref('default')
const mcpServers = ref<McpServerItem[]>([])
const mcpLoading = ref(false)
const globalModel = ref<GlobalModelSettings | null>(null)
const recruitOpen = ref(false)
const modelOpen = ref(false)
const groupsOpen = ref(false)
const recruitForm = reactive({
  name: '',
  role_title: '',
  description: '',
  system_prompt: '',
})

const form = reactive({
  name: '',
  role_title: '',
  description: '',
  avatar: '',
  system_prompt: '',
  mcp_server_ids: [] as string[],
  model_name: '',
  base_url: '',
  api_key: '',
  thinking_enabled: false,
  context_limit: 128000,
  useCustomModel: false,
})

const current = computed(() => agents.byId(selectedId.value))
const isDefault = computed(() => Boolean(current.value?.is_default))
const mcpOptions = computed(() =>
  mcpServers.value.map((item) => ({
    value: item.server_id,
    label: item.description ? `${item.server_id} · ${item.description}` : item.server_id,
    integration: item.integration,
    enabled: item.enabled,
  })),
)

function resetForm(profile?: AgentProfile) {
  if (!profile) return
  form.name = profile.name
  form.role_title = profile.role_title
  form.description = profile.description
  form.avatar = profile.avatar
  form.system_prompt = profile.system_prompt
  form.mcp_server_ids = [...(profile.mcp_server_ids || [])]
  form.model_name = profile.model?.model_name || ''
  form.base_url = profile.model?.base_url || ''
  form.api_key = ''
  form.thinking_enabled = Boolean(profile.model?.thinking_enabled)
  form.context_limit = profile.model?.context_limit || 128000
  form.useCustomModel = Boolean(profile.model?.model_name || profile.model?.base_url)
}

watch(current, (profile) => resetForm(profile))

async function load() {
  await agents.load(true)
  await agents.loadGroups(true)
  const first = agents.items.find((item) => item.id === selectedId.value) || agents.items[0]
  if (first) selectedId.value = first.id
  resetForm(first)
}

async function loadMcp() {
  mcpLoading.value = true
  try {
    const data = await fetchMcpServers({ page: 1, page_size: 100 })
    mcpServers.value = data.items
  } catch {
    mcpServers.value = []
  } finally {
    mcpLoading.value = false
  }
}

async function loadGlobalModel() {
  try {
    globalModel.value = await fetchGlobalModelSettings()
  } catch {
    globalModel.value = null
  }
}

function refresh() {
  void Promise.all([load(), loadMcp(), loadGlobalModel()])
}

function applyModel(payload: {
  useCustom: boolean
  modelName: string
  baseUrl: string
  apiKey: string
  thinkingEnabled: boolean
  contextLimit: number
}) {
  form.useCustomModel = payload.useCustom
  form.model_name = payload.useCustom ? payload.modelName : ''
  form.base_url = payload.useCustom ? payload.baseUrl : ''
  form.api_key = payload.apiKey
  form.thinking_enabled = payload.useCustom ? payload.thinkingEnabled : false
  form.context_limit = payload.useCustom ? payload.contextLimit : 128000
  ElMessage.success('模型配置已应用，保存 Agent 后生效')
}

async function save() {
  if (!current.value) return
  const model = form.useCustomModel
    ? {
        model_name: form.model_name || undefined,
        base_url: form.base_url || undefined,
        api_key: form.api_key || undefined,
        thinking_enabled: form.thinking_enabled,
        context_limit: form.context_limit,
      }
    : {}
  try {
    await agents.save(current.value.id, {
      name: form.name,
      role_title: form.role_title,
      description: form.description,
      avatar: form.avatar,
      system_prompt: form.system_prompt,
      mcp_server_ids: form.mcp_server_ids,
      model,
    })
    ElMessage.success('已保存，下一轮对话按新配置生效')
    const profile = agents.byId(selectedId.value)
    if (profile) resetForm(profile)
  } catch (error) {
    ElMessage.error(`保存失败：${(error as Error).message}`)
  }
}

async function removeAgent() {
  if (!current.value || isDefault.value) return
  try {
    await ElMessageBox.confirm(
      `确定删除「${current.value.name}」？该 Agent 的历史会话仍保留，但之后不能再选择它执行任务。`,
      '删除 Agent',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
    await agents.remove(current.value.id)
    selectedId.value = agents.defaultAgentId
    ElMessage.success('已删除')
  } catch {
    /* 用户取消 */
  }
}

async function recruit() {
  if (!recruitForm.name.trim()) {
    ElMessage.warning('请先填写 Agent 名称')
    return
  }
  try {
    const profile = await agents.recruit({
      name: recruitForm.name.trim(),
      role_title: recruitForm.role_title.trim() || '自定义员工',
      description: recruitForm.description.trim(),
      system_prompt: recruitForm.system_prompt.trim(),
      mcp_server_ids: [],
      model: {},
    })
    recruitOpen.value = false
    recruitForm.name = ''
    recruitForm.role_title = ''
    recruitForm.description = ''
    recruitForm.system_prompt = ''
    selectedId.value = profile.id
    resetForm(profile)
    ElMessage.success('招募成功，可继续配置')
  } catch (error) {
    ElMessage.error(`招募失败：${(error as Error).message}`)
  }
}

onMounted(async () => {
  await Promise.all([load(), loadMcp(), loadGlobalModel()])
})
</script>

<template>
  <div class="app-shell">
    <TopNav />
    <div class="page">
      <header class="page-head">
        <div class="head-text">
          <span class="head-icon"><AppIcon name="cube" :size="16" /></span>
          <div>
            <h1>MyAgent</h1>
            <p v-if="viewMode === 'agent'">
              招募你的主 Agent：每个 Agent 可以有自己的系统提示词、模型与 MCP 工具集。
            </p>
            <p v-else>定义组、组模型与成员：队长统筹任务，组员负责具体执行。</p>
          </div>
        </div>
        <div class="head-actions">
          <div class="mode-split">
           <button
              type="button"
              :class="{ active: viewMode === 'group' }"
              @click="viewMode = 'group'"
            >
              组
            </button>
            <button
              type="button"
              :class="{ active: viewMode === 'agent' }"
              @click="viewMode = 'agent'"
            >
              Agent
            </button>
           
          </div>
          <el-button
            :icon="Refresh"
            circle
            size="small"
            title="刷新"
            :loading="agents.loading"
            @click="refresh"
          />
        </div>
      </header>

      <div class="mode-stage">
        <div class="mode-pane agent-pane" :class="{ off: viewMode === 'group' }">
          <aside class="list-pane">
            <div class="list-head">
              <span>我的Agent</span>
              <small>{{ agents.items.length }}</small>
            </div>
            <div class="list-scroll">
              <AgentListRow
                v-for="agent in agents.items"
                :key="agent.id"
                :agent="agent"
                :active="agent.id === selectedId"
                :muted="!agent.enabled"
                @click="selectedId = agent.id"
              >
                <span v-if="agent.is_default" class="row-badge">默认</span>
                <span v-else-if="!agent.enabled" class="row-badge muted">停用</span>
              </AgentListRow>
              <el-empty
                v-if="!agents.loading && agents.items.length === 0"
                description="还没有 Agent，点击招募"
                :image-size="50"
              />
              <p v-if="agents.error" class="agent-error">{{ agents.error }}</p>
            </div>
          </aside>

          <section v-if="current" class="form-pane">
            <header class="form-head">
              <div class="form-title">
                <el-icon><UserFilled /></el-icon>
                <strong>{{ current.name }}</strong>
                <el-tag v-if="isDefault" size="small" effect="plain">默认</el-tag>
                <el-tag v-else size="small" type="success" effect="plain">已招募</el-tag>
              </div>
              <div class="form-actions">
                <el-button size="small" :icon="Plus" @click="recruitOpen = true">
                  招募 Agent
                </el-button>
                <el-button
                  size="small"
                  type="primary"
                  :icon="Check"
                  :loading="agents.saving"
                  @click="save"
                >
                  保存
                </el-button>
                <el-button
                  v-if="!isDefault"
                  size="small"
                  text
                  type="danger"
                  @click="removeAgent"
                >
                  删除
                </el-button>
                <span class="action-divider"></span>
                <div class="drawer-dock">
                  <HoloTooltip
                    title="模型配置"
                    code="DRAWER // MODEL"
                    content="为该 Agent 单独指定模型、上下文窗口与思考能力；不配置时直接使用系统设置里的全局模型。"
                    footer="点击打开右侧抽屉"
                  >
                    <button type="button" class="dock-btn" @click="modelOpen = true">
                      <el-icon><Setting /></el-icon>
                      <span>模型配置</span>
                    </button>
                  </HoloTooltip>
                  <HoloTooltip
                    title="组管理"
                    code="DRAWER // GROUPS"
                    content="查看该 Agent 已加入的团队组，并决定它在哪些组里承担职责。队长身份决定它能否统筹并委派组员。"
                    footer="点击打开右侧抽屉"
                  >
                    <button type="button" class="dock-btn" @click="groupsOpen = true">
                      <el-icon><Connection /></el-icon>
                      <span>组管理</span>
                    </button>
                  </HoloTooltip>
                </div>
              </div>
            </header>

            <div class="form-scroll">
              <section class="form-block">
                <h3>基本资料</h3>
                <div class="grid-2">
                  <label>
                    <span>Agent 名称</span>
                    <el-input v-model="form.name" placeholder="如：代码审查员" />
                  </label>
                  <label>
                    <span>职位 / 角色标签</span>
                    <el-input v-model="form.role_title" placeholder="如：后端工程师" />
                  </label>
                  <label>
                    <span>头像字符</span>
                    <el-input v-model="form.avatar" maxlength="2" placeholder="如：A" />
                  </label>
                  <div class="inline-field">
                    <span>启用状态</span>
                    <el-switch
                      :model-value="Boolean(current?.enabled)"
                      @update:model-value="(v: string | number | boolean | undefined) => agents.save(current!.id, { enabled: Boolean(v) })"
                    />
                  </div>
                </div>
                <label>
                  <span>一句话简介</span>
                  <el-input v-model="form.description" placeholder="这个 Agent 擅长什么" />
                </label>
              </section>

              <section class="form-block">
                <h3>
                  系统提示词
                  <small>与 Harness 固定的执行/安全策略叠加生效</small>
                </h3>
                <el-input
                  v-model="form.system_prompt"
                  type="textarea"
                  :rows="16"
                  resize="vertical"
                  placeholder="描述这个 Agent 的身份、风格、工作方法与输出要求"
                />
              </section>

              <section class="form-block">
                <h3>
                  MCP 服务
                  <small>空选表示允许全部公共 MCP；集成后端只随会话绑定</small>
                </h3>
                <el-select
                  v-model="form.mcp_server_ids"
                  multiple
                  filterable
                  collapse-tags
                  collapse-tags-tooltip
                  :loading="mcpLoading"
                  placeholder="选择该 Agent 可连接的 MCP 服务"
                  class="mcp-select"
                >
                  <el-option
                    v-for="item in mcpOptions"
                    :key="item.value"
                    :label="item.label"
                    :value="item.value"
                    :disabled="!item.enabled"
                  >
                    <span>{{ item.label }}</span>
                    <el-tag v-if="item.integration" size="small" effect="plain">集成</el-tag>
                  </el-option>
                </el-select>
                <p class="hint">
                  未选任何服务时，主 Agent 会加载全部已启用的公共 MCP；选择后只暴露勾选的服务。
                </p>
              </section>
            </div>
          </section>
          <section v-else class="form-pane empty-pane">
            <el-empty description="选择或招募一个 Agent" :image-size="70" />
          </section>
        </div>

        <div class="mode-pane group-pane" :class="{ off: viewMode === 'agent' }">
          <GroupWorkspace />
        </div>
      </div>
    </div>

    <el-dialog v-model="recruitOpen" title="招募 Agent" width="500px">
      <div class="recruit-form">
        <label>
          <span>名称 <em>*</em></span>
          <el-input v-model="recruitForm.name" placeholder="如：代码审查员" />
        </label>
        <label>
          <span>职位 / 角色</span>
          <el-input v-model="recruitForm.role_title" placeholder="如：资深后端工程师" />
        </label>
        <label>
          <span>一句话简介</span>
          <el-input v-model="recruitForm.description" placeholder="擅长什么、适合什么任务" />
        </label>
        <label>
          <span>初始系统提示词</span>
          <el-input
            v-model="recruitForm.system_prompt"
            type="textarea"
            :rows="5"
            placeholder="留空则使用默认模板，招募后可继续编辑"
          />
        </label>
      </div>
      <template #footer>
        <el-button @click="recruitOpen = false">取消</el-button>
        <el-button type="primary" :loading="agents.saving" @click="recruit">确认招募</el-button>
      </template>
    </el-dialog>

    <AgentModelDrawer
      v-model="modelOpen"
      :model-name="form.model_name"
      :base-url="form.base_url"
      :has-api-key="Boolean(current?.model?.has_api_key)"
      :thinking-enabled="form.thinking_enabled"
      :context-limit="form.context_limit"
      :global-model="globalModel"
      @apply="applyModel"
    />
    <AgentGroupsDrawer
      v-if="current"
      v-model="groupsOpen"
      :agent-id="current.id"
    />
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}

.page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--topbar-h));
  padding: 0;
  overflow: hidden;
}

.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 22px 12px;
  flex: none;
}

.head-text {
  display: flex;
  align-items: center;
  gap: 10px;
}

.head-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.35);
  border-radius: 10px;
  background: rgba(55, 208, 196, 0.08);
}

.page-head h1 {
  margin: 0;
  font-size: 18px;
  letter-spacing: -0.02em;
}

.page-head p {
  margin: 3px 0 0;
  font-size: 11.5px;
  color: var(--text-3);
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* SplitButton：两个按钮共享边框，中间一条分隔线 */
.mode-split {
  display: inline-flex;
  overflow: hidden;
  border: 1px solid var(--line-strong);
  border-radius: 7px;
}

.mode-split button {
  min-width: 62px;
  padding: 5px 12px;
  font-size: 11.5px;
  color: var(--text-2);
  border: 0;
  background: transparent;
  cursor: pointer;
  transition: color 0.16s, background 0.16s;
}

.mode-split button + button {
  border-left: 1px solid var(--line-strong);
}

.mode-split button:hover {
  color: var(--text-1);
  background: var(--bg-hover);
}

.mode-split button.active {
  font-weight: 600;
  color: #041018;
  background: linear-gradient(135deg, var(--accent-cyan), var(--accent));
}

.mode-stage {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.mode-pane {
  position: absolute;
  inset: 0;
  transition:
    transform 0.42s cubic-bezier(0.22, 0.61, 0.36, 1),
    opacity 0.28s ease;
  will-change: transform;
}

.mode-pane.off {
  opacity: 0;
  pointer-events: none;
}

.agent-pane {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
}

.agent-pane.off {
  transform: translateX(64%);
}

.group-pane {
  display: grid;
}

.group-pane.off {
  transform: translateX(-26%);
}

.list-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 0 8px;
  border-right: 1px solid var(--line);
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

.row-badge {
  flex: none;
  padding: 1px 6px;
  font-size: 9px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.35);
  border-radius: 999px;
}

.row-badge.muted {
  color: var(--text-3);
  border-color: var(--line);
}

.agent-error {
  margin: 8px;
  font-size: 10.5px;
  color: var(--danger);
}

.form-pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.form-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: 56px;
  padding: 0 20px;
  border-bottom: 1px solid var(--line);
  flex: none;
}

.form-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--text-1);
}

.form-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-divider {
  width: 1px;
  height: 18px;
  margin: 0 2px;
  background: var(--line);
}

/* 两个抽屉入口：带文字的发光按钮，比圆形图标更容易被注意到 */
.drawer-dock {
  display: flex;
  align-items: center;
  gap: 6px;
}

.dock-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 10px;
  font-size: 11.5px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.42);
  border-radius: 6px;
  background: linear-gradient(135deg, rgba(55, 208, 196, 0.14), rgba(47, 124, 246, 0.1));
  box-shadow: inset 0 0 14px rgba(55, 208, 196, 0.08);
  cursor: pointer;
  transition: color 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
}

.dock-btn:hover {
  color: #dffaff;
  border-color: rgba(160, 240, 255, 0.8);
  box-shadow:
    0 0 0 1px rgba(55, 208, 196, 0.2),
    0 0 16px rgba(55, 208, 196, 0.28);
}

.dock-btn .el-icon {
  font-size: 13px;
}

.form-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 20px 28px;
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
  gap: 12px;
  font-size: 11.5px;
  color: var(--text-2);
}

.empty-pane {
  align-items: center;
  justify-content: center;
}

.mcp-select {
  width: 100%;
}

.hint {
  margin: 0;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--text-3);
}

.recruit-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

@media (max-width: 1080px) {
  .agent-pane {
    grid-template-columns: 200px minmax(0, 1fr);
  }
}
</style>
