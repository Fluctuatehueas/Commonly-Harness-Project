<script setup lang="ts">
// 输入框内的「当前智能体」入口：点击弹出面板，选择 Agent 与所属组。
// 对话页与悬浮助手共用，避免两处各维护一份选择器。
import { computed, ref, watch } from 'vue'
import { Flag } from '@element-plus/icons-vue'
import HoloTooltip from '@/components/console/HoloTooltip.vue'
import { avatarGradient, avatarInitial } from '@/components/myagent/avatar'
import { useAgentStore } from '@/stores/agents'
import { useChatStore } from '@/stores/chat'

withDefaults(defineProps<{ disabled?: boolean }>(), { disabled: false })

const chat = useChatStore()
const agents = useAgentStore()
const open = ref(false)

// Agent 下拉项：补上头像与所属组的组标（队长组带小旗帜）。
const agentOptions = computed(() =>
  agents.options.map((opt) => ({
    ...opt,
    avatar: agents.byId(opt.value)?.avatar || '',
    groups: agents.groupsOf(opt.value).map((group) => ({
      group_id: group.group_id,
      name: group.name,
      role: group.leader_agent_id === opt.value ? ('leader' as const) : ('member' as const),
    })),
  })),
)

// 当前 Agent 可用的组；只有组长组能作为会话入口（组员由组长委派）。
const groupOptions = computed(() =>
  agents.groupsOf(chat.selectedAgentId).map((group) => {
    const isLeader = group.leader_agent_id === chat.selectedAgentId
    return {
      value: group.group_id,
      name: group.name,
      role: isLeader ? '组长' : '组员',
      isLeader,
      label: `${group.name}${isLeader ? ' · 组长' : ' · 组员'}`,
    }
  }),
)

const currentName = computed(
  () =>
    agentOptions.value.find((opt) => opt.value === chat.selectedAgentId)?.label ||
    '默认智能体',
)
const currentProfile = computed(() => agents.byId(chat.selectedAgentId))
const currentInitial = computed(() =>
  avatarInitial(currentName.value, currentProfile.value?.avatar || ''),
)

// 换 Agent 后只清理失效的组选择；不自动选组，默认以独立 Agent 执行。
function syncGroupForAgent(): void {
  // 组列表还没加载完时不做判断，否则会把会话绑定的组误清空。
  if (!agents.groupsLoaded) return
  if (
    chat.selectedGroupId &&
    !groupOptions.value.some((item) => item.value === chat.selectedGroupId)
  ) {
    chat.selectedGroupId = ''
  }
}

/**
 * 选择 Agent：显式写回 store，保证下拉框显示与胶囊按钮同步刷新。
 * 下拉项的值与选项列表同源，空值兜底为默认 Agent。
 */
function selectAgent(value: string): void {
  chat.selectedAgentId = value || 'default'
  syncGroupForAgent()
}

watch(() => chat.selectedAgentId, syncGroupForAgent)
watch(
  () => agents.groupsLoaded,
  (loaded) => {
    if (loaded) syncGroupForAgent()
  },
)
</script>

<template>
  <div class="composer-pop-wrap agent-picker">
    <button
      class="composer-pill agent-pill"
      :class="{ open }"
      type="button"
      :title="`当前智能体：${currentName}`"
      :disabled="disabled"
      @click="open = !open"
    >
      <span
        class="agent-pill-avatar"
        :style="{ background: avatarGradient(chat.selectedAgentId || 'default') }"
      >
        {{ currentInitial }}
      </span>
      <span class="agent-pill-name">{{ currentName }}</span>
    </button>
    <div v-if="open" class="composer-pop-mask" @click="open = false"></div>
    <Transition name="pop">
      <div v-if="open" class="composer-pop agent-pop">
        <div class="agent-pop-head">
          <span class="agent-pop-title">当前智能体</span>
          <span class="agent-pop-name">{{ currentName }}</span>
        </div>

        <div class="agent-pop-field">
          <span class="agent-pop-label">智能体</span>
          <el-select
            :model-value="chat.selectedAgentId"
            class="agent-select holo-select holo-select--agent"
            popper-class="holo-popper"
            size="small"
            placeholder="选择 Agent"
            :disabled="disabled"
            @update:model-value="selectAgent"
          >
            <!-- 选中项由当前状态直接渲染，避免下拉框停留在旧文案 -->
            <template #label>
              <span class="agent-select-value">
                <span
                  class="agent-select-avatar"
                  :style="{ background: avatarGradient(chat.selectedAgentId || 'default') }"
                >
                  {{ currentInitial }}
                </span>
                <span class="agent-select-name">{{ currentName }}</span>
              </span>
            </template>
            <el-option
              v-for="opt in agentOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            >
              <div class="agent-option">
                <span
                  class="agent-option-avatar"
                  :style="{ background: avatarGradient(opt.value) }"
                >
                  {{ avatarInitial(opt.label, opt.avatar) }}
                </span>
                <span class="agent-option-main">
                  <strong>{{ opt.label }}</strong>
                  <small>{{ opt.role }}</small>
                </span>
                <span v-if="opt.groups.length" class="agent-option-groups">
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
        </div>

        <div v-if="groupOptions.length" class="agent-pop-field">
          <span class="agent-pop-label">组</span>
          <el-select
            v-model="chat.selectedGroupId"
            class="agent-select holo-select holo-select--group"
            popper-class="holo-popper"
            size="small"
            placeholder="独立执行（不使用组）"
            :disabled="disabled"
          >
            <el-option label="独立执行（不使用组）" value="">
              <div class="agent-option">
                <span class="agent-option-avatar agent-option-avatar--none">—</span>
                <span class="agent-option-main">
                  <strong>独立执行</strong>
                  <small>不使用组</small>
                </span>
              </div>
            </el-option>
            <el-option
              v-for="opt in groupOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            >
              <div class="agent-option">
                <HoloTooltip
                  :title="opt.name"
                  :content="`角色：${opt.role}`"
                  placement="left"
                  :width="200"
                >
                  <span
                    class="group-badge group-badge--lg"
                    :style="{ background: avatarGradient(opt.value) }"
                  >
                    {{ avatarInitial(opt.name) }}
                    <i v-if="opt.isLeader" class="group-badge-flag">
                      <el-icon><Flag /></el-icon>
                    </i>
                  </span>
                </HoloTooltip>
                <span class="agent-option-main">
                  <strong>{{ opt.name }}</strong>
                  <small>{{ opt.role }}</small>
                </span>
              </div>
            </el-option>
          </el-select>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.agent-pop {
  min-width: 268px;
  gap: 9px;
  padding: 10px;
}

/* 胶囊按钮：头像 + 名称，让用户随时知道正在和谁对话 */
.agent-pill {
  gap: 6px;
  padding: 0 11px 0 5px;
}

.agent-pill-avatar {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  flex: none;
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
  color: #041018;
  border-radius: 50%;
}

.agent-pill-name {
  max-width: 96px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-pop-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 0 2px 2px;
  border-bottom: 1px solid var(--line);
}

.agent-pop-title {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--text-1);
}

.agent-pop-name {
  min-width: 0;
  overflow: hidden;
  font-size: 10.5px;
  color: var(--accent-cyan);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-pop-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 0 2px;
}

.agent-pop-label {
  font-size: 10px;
  color: var(--text-3);
}

.agent-select {
  width: 100%;
}

/* 下拉框选中项：头像 + 名称，随选择即时刷新 */
.agent-select-value {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  max-width: 100%;
}

.agent-select-avatar {
  display: grid;
  place-items: center;
  width: 16px;
  height: 16px;
  flex: none;
  font-size: 9px;
  font-weight: 700;
  line-height: 1;
  color: #041018;
  border-radius: 50%;
}

.agent-select-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---------- 全息选择器 ----------
   与青色 HOLO ARCHIVE 面板区分：紫 / 品红棱镜描边、斜向扫描纹、
   缓慢掠过的高光带，以及单侧斜切的玻璃底。 */
.holo-select {
  --holo-a: rgba(167, 139, 250, 0.9);
  --holo-b: rgba(244, 114, 182, 0.7);
  --holo-c: rgba(96, 165, 250, 0.55);
}

.holo-select :deep(.el-select__wrapper) {
  position: relative;
  overflow: hidden;
  min-height: 30px;
  padding: 0 26px 0 11px;
  border: 1px solid transparent;
  border-radius: 7px 2px 7px 2px;
  background:
    linear-gradient(135deg, rgba(31, 18, 56, 0.9), rgba(11, 9, 26, 0.94)) padding-box,
    linear-gradient(118deg, var(--holo-a), var(--holo-b) 46%, var(--holo-c)) border-box;
  box-shadow:
    inset 0 0 18px rgba(167, 139, 250, 0.14),
    0 0 0 1px rgba(6, 5, 16, 0.55);
  backdrop-filter: blur(12px);
  transition: box-shadow 0.24s ease;
}

.holo-select :deep(.el-select__wrapper)::before {
  content: '';
  position: absolute;
  inset: 0;
  background: repeating-linear-gradient(
    116deg,
    rgba(226, 214, 255, 0.055) 0 1px,
    transparent 1px 6px
  );
  pointer-events: none;
}

.holo-select :deep(.el-select__wrapper)::after {
  content: '';
  position: absolute;
  top: -70%;
  left: -45%;
  width: 38%;
  height: 240%;
  background: linear-gradient(90deg, transparent, rgba(244, 114, 182, 0.26), transparent);
  transform: rotate(14deg);
  animation: holo-sheen 5.2s ease-in-out infinite;
  pointer-events: none;
}

@keyframes holo-sheen {
  0%,
  100% {
    left: -45%;
    opacity: 0;
  }
  18% {
    opacity: 1;
  }
  60% {
    left: 105%;
    opacity: 1;
  }
  78% {
    opacity: 0;
  }
}

.holo-select :deep(.el-select__wrapper.is-hovering),
.holo-select :deep(.el-select__wrapper.is-focused) {
  box-shadow:
    inset 0 0 26px rgba(244, 114, 182, 0.2),
    0 0 16px rgba(167, 139, 250, 0.34);
}

.holo-select :deep(.el-select__selection),
.holo-select :deep(.el-select__suffix) {
  position: relative;
  z-index: 1;
}

.holo-select :deep(.el-select__placeholder),
.holo-select :deep(.el-select__selected-item) {
  color: #ece6ff;
  font-size: 11.5px;
  letter-spacing: 0.02em;
}

.holo-select :deep(.el-select__placeholder.is-transparent) {
  color: rgba(196, 181, 253, 0.62);
}

.holo-select :deep(.el-select__caret) {
  color: #c4b5fd;
  transition: transform 0.24s ease;
}

.holo-select :deep(.el-select__wrapper.is-focused) .el-select__caret {
  transform: rotate(180deg);
}

/* 组选择器走暖调：琥珀 → 品红，和冷紫的 Agent 选择器区分。 */
.holo-select--group {
  --holo-a: rgba(251, 191, 36, 0.85);
  --holo-b: rgba(244, 114, 182, 0.8);
  --holo-c: rgba(167, 139, 250, 0.5);
}

.holo-select--group :deep(.el-select__wrapper) {
  background:
    linear-gradient(135deg, rgba(54, 28, 42, 0.9), rgba(14, 9, 24, 0.94)) padding-box,
    linear-gradient(118deg, var(--holo-a), var(--holo-b) 46%, var(--holo-c)) border-box;
  box-shadow:
    inset 0 0 18px rgba(251, 191, 36, 0.12),
    0 0 0 1px rgba(6, 5, 16, 0.55);
}

/* 下拉层 teleport 到 body，必须用全局选择器命中 popper-class。 */
:global(.holo-popper) {
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

:global(.holo-popper .el-select-dropdown__item) {
  color: #cfc7ea;
  font-size: 11.5px;
}

:global(.holo-popper .el-select-dropdown__item.is-hovering) {
  background: linear-gradient(90deg, rgba(167, 139, 250, 0.16), rgba(244, 114, 182, 0.1));
  color: #f5efff;
}

:global(.holo-popper .el-select-dropdown__item.is-selected) {
  color: #f0abfc;
  font-weight: 500;
}

:global(.holo-popper .el-popper__arrow::before) {
  border-color: rgba(167, 139, 250, 0.42);
  background: rgba(24, 15, 44, 0.98);
}

/* Agent / 组下拉项：头像 + 名称 + 所属组组标（队长组右下角带小旗帜） */
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

.agent-option-avatar--none {
  color: var(--text-3);
  background: transparent;
  border: 1px dashed var(--line);
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

.group-badge--lg {
  width: 32px;
  height: 32px;
  font-size: 12px;
  border-radius: 7px;
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

.pop-enter-active,
.pop-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateY(6px) scale(0.97);
}
</style>
