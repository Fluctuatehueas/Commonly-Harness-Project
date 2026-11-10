<script setup lang="ts">
// MyAgent：招募/编辑多个主 Agent，并为每个 Agent 绑定提示词、模型与 MCP 服务。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Plus, Refresh, UserFilled } from '@element-plus/icons-vue'
import TopNav from '@/components/console/TopNav.vue'
import AppIcon from '@/components/console/AppIcon.vue'
import { fetchMcpServers, type McpServerItem } from '@/api/mcp'
import { MODEL_CATALOG, formatContext } from '@/stores/models'
import { useAgentStore } from '@/stores/agents'
import type { AgentProfile } from '@/api/agents'

const agents = useAgentStore()
const selectedId = ref('default')
const mcpServers = ref<McpServerItem[]>([])
const mcpLoading = ref(false)
const recruitOpen = ref(false)
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
const modelOptions = computed(() =>
  MODEL_CATALOG.map((item) => ({
    value: item.id,
    label: `${item.name} · ${item.provider}`,
    context: item.defaultContext,
    baseUrl: item.baseUrl,
  })),
)
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

function onPickModel(modelId: string) {
  const spec = MODEL_CATALOG.find((item) => item.id === modelId)
  if (!spec) return
  form.model_name = spec.id
  form.base_url = spec.baseUrl
  form.context_limit = spec.defaultContext
  form.useCustomModel = true
}

function useGlobalModel() {
  form.model_name = ''
  form.base_url = ''
  form.api_key = ''
  form.thinking_enabled = false
  form.context_limit = 128000
  form.useCustomModel = false
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
    if (agents.byId(selectedId.value)) resetForm(agents.byId(selectedId.value))
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
    ElMessage.success('招募成功，可在右侧继续配置')
  } catch (error) {
    ElMessage.error(`招募失败：${(error as Error).message}`)
  }
}

onMounted(async () => {
  await Promise.all([load(), loadMcp()])
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
            <p>招募你的主 Agent：每个 Agent 可以有自己的系统提示词、模型与 MCP 工具集。</p>
          </div>
        </div>
        <div class="head-actions">
          <el-button :icon="Refresh" size="small" :loading="agents.loading" @click="load">
            刷新
          </el-button>
          <el-button type="primary" size="small" :icon="Plus" @click="recruitOpen = true">
            招募 Agent
          </el-button>
        </div>
      </header>

      <div class="columns">
        <aside class="panel roster">
          <div class="panel-head">
            <span class="panel-title">我的Agent</span>
            <el-tag size="small" effect="plain">{{ agents.items.length }} 位</el-tag>
          </div>
          <button
            v-for="agent in agents.items"
            :key="agent.id"
            type="button"
            class="agent-card"
            :class="{ active: agent.id === selectedId, disabled: !agent.enabled }"
            @click="selectedId = agent.id"
          >
            <span class="agent-avatar">{{ agent.avatar || agent.name.slice(0, 1) }}</span>
            <span class="agent-meta">
              <strong>{{ agent.name }}</strong>
              <small>{{ agent.role_title || '自定义员工' }}</small>
            </span>
            <span v-if="agent.is_default" class="agent-badge">默认</span>
            <span v-else-if="!agent.enabled" class="agent-badge muted">停用</span>
          </button>
          <el-empty
            v-if="!agents.loading && agents.items.length === 0"
            description="还没有 Agent，点击右上方招募"
            :image-size="60"
          />
          <p v-if="agents.error" class="agent-error">{{ agents.error }}</p>
        </aside>

        <section v-if="current" class="panel editor">
          <div class="panel-head">
            <span class="panel-title">
              <el-icon><UserFilled /></el-icon>
              {{ current.name }}
              <el-tag v-if="isDefault" size="small" effect="plain">系统默认 Agent</el-tag>
              <el-tag v-else size="small" type="success" effect="plain">已招募</el-tag>
            </span>
            <div class="editor-actions">
              <el-button
                v-if="!isDefault"
                size="small"
                type="danger"
                plain
                @click="removeAgent"
              >
                删除
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
            </div>
          </div>

          <div class="editor-body">
            <section class="block">
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
                <label>
                  <span>启用状态</span>
                  <el-switch
                    :model-value="current.enabled"
                    @update:model-value="(v: string | number | boolean | undefined) => agents.save(current!.id, { enabled: Boolean(v) })"
                  />
                </label>
              </div>
              <label class="full">
                <span>一句话简介</span>
                <el-input v-model="form.description" placeholder="这个 Agent 擅长什么" />
              </label>
            </section>

            <section class="block">
              <h3>
                系统提示词
                <small>与 Harness 固定的执行/安全策略叠加生效</small>
              </h3>
              <el-input
                v-model="form.system_prompt"
                type="textarea"
                :rows="14"
                resize="vertical"
                placeholder="描述这个 Agent 的身份、风格、工作方法与输出要求"
              />
              <p class="hint">
                默认 Agent 的提示词来自系统内置模板，可自由修改；招募的 Agent 可以写完全不同的风格。
              </p>
            </section>

            <section class="block">
              <h3>
                模型配置
                <small>不填写则继承当前用户的默认模型</small>
              </h3>
              <div class="model-head">
                <el-radio-group
                  :model-value="form.useCustomModel ? 'custom' : 'inherit'"
                  size="small"
                  @update:model-value="(v: string | number | boolean | undefined) => {
                    if (v === 'inherit') useGlobalModel()
                    else form.useCustomModel = true
                  }"
                >
                  <el-radio-button value="inherit">沿用全局</el-radio-button>
                  <el-radio-button value="custom">独立配置</el-radio-button>
                </el-radio-group>
              </div>
              <template v-if="form.useCustomModel">
                <div class="grid-2">
                  <label>
                    <span>模型</span>
                    <el-select
                      :model-value="form.model_name"
                      filterable
                      placeholder="选择模型"
                      @update:model-value="(v: string) => onPickModel(v)"
                    >
                      <el-option
                        v-for="item in modelOptions"
                        :key="item.value"
                        :label="item.label"
                        :value="item.value"
                      />
                    </el-select>
                  </label>
                  <label>
                    <span>上下文窗口</span>
                    <el-select v-model="form.context_limit" placeholder="窗口">
                      <el-option :value="128000" label="128K" />
                      <el-option :value="200000" label="200K" />
                      <el-option :value="256000" label="256K" />
                      <el-option :value="1000000" label="1M" />
                    </el-select>
                  </label>
                  <label>
                    <span>Base URL</span>
                    <el-input v-model="form.base_url" placeholder="https://api.deepseek.com" />
                  </label>
                  <label>
                    <span>API Key</span>
                    <el-input
                      v-model="form.api_key"
                      type="password"
                      show-password
                      autocomplete="off"
                      :placeholder="current.model?.has_api_key ? '留空保持不变' : '未配置'"
                    />
                  </label>
                </div>
                <div class="switch-row">
                  <span>思考能力</span>
                  <el-switch v-model="form.thinking_enabled" />
                </div>
                <p class="hint">
                  上下文窗口用于压缩阈值与占用展示；当前值 {{ formatContext(form.context_limit) }}。
                </p>
              </template>
            </section>

            <section class="block">
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
      </div>
    </div>

    <el-dialog v-model="recruitOpen" title="招募 Agent" width="520px">
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
        <el-button type="primary" :loading="agents.saving" @click="recruit">
          确认招募
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}

.page {
  padding: 18px 22px 28px;
}

.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
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
  gap: 8px;
}

.columns {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

.panel {
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(8, 14, 27, 0.55);
  backdrop-filter: blur(8px);
}

.roster {
  padding: 12px;
  position: sticky;
  top: calc(var(--topbar-h) + 16px);
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding-bottom: 10px;
}

.panel-title {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-1);
}

.agent-card {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 9px 10px;
  margin-bottom: 6px;
  text-align: left;
  color: var(--text-1);
  border: 1px solid transparent;
  border-radius: 9px;
  background: rgba(10, 18, 34, 0.5);
  transition: border-color 0.16s, background 0.16s;
}

.agent-card:hover {
  border-color: var(--line-strong);
  background: var(--bg-hover);
}

.agent-card.active {
  border-color: rgba(55, 208, 196, 0.6);
  background: rgba(55, 208, 196, 0.08);
}

.agent-card.disabled {
  opacity: 0.55;
}

.agent-avatar {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex: none;
  font-size: 13px;
  font-weight: 700;
  color: #041018;
  border-radius: 9px;
  background: linear-gradient(135deg, var(--accent-cyan), var(--accent));
}

.agent-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
  line-height: 1.25;
}

.agent-meta strong {
  font-size: 12.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-meta small {
  font-size: 10px;
  color: var(--text-3);
}

.agent-badge {
  flex: none;
  padding: 1px 6px;
  font-size: 9px;
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.35);
  border-radius: 999px;
}

.agent-badge.muted {
  color: var(--text-3);
  border-color: var(--line);
}

.agent-error {
  margin: 8px 0 0;
  font-size: 10.5px;
  color: var(--danger);
}

.editor {
  min-height: 520px;
  padding: 14px 16px 18px;
}

.editor-actions {
  display: flex;
  gap: 8px;
}

.editor-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.block {
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(5, 11, 22, 0.42);
}

.block h3 {
  margin: 0;
  font-size: 12.5px;
  color: var(--text-1);
}

.block h3 small {
  margin-left: 8px;
  font-size: 10px;
  font-weight: 400;
  color: var(--text-3);
}

.grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

label {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 11px;
  color: var(--text-2);
}

label.full {
  width: 100%;
}

label em {
  font-style: normal;
  color: var(--danger);
}

.model-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.switch-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11.5px;
  color: var(--text-2);
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
  gap: 11px;
}

@media (max-width: 1080px) {
  .columns {
    grid-template-columns: 1fr;
  }
  .roster {
    position: static;
  }
}
</style>
