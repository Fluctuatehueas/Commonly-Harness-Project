<script setup lang="ts">
// Agent 列表行：Agent 模式左栏与组模式右栏共用同一套视觉，保证两栏完全一致。
import { computed } from 'vue'
import type { AgentProfile } from '@/api/agents'
import HoloTooltip from '@/components/console/HoloTooltip.vue'
import { avatarGradient, avatarInitial } from './avatar'

const props = withDefaults(
  defineProps<{
    agent: AgentProfile
    active?: boolean
    muted?: boolean
    disabled?: boolean
    /** 头像悬停动作：add 显示 +，remove 显示 ×；不传则头像不可操作。 */
    avatarAction?: 'add' | 'remove' | null
  }>(),
  {
    active: false,
    muted: false,
    disabled: false,
    avatarAction: null,
  },
)

const emit = defineEmits<{ 'avatar-action': [] }>()

const initial = computed(() => avatarInitial(props.agent.name, props.agent.avatar))
const avatarStyle = computed(() => ({ background: avatarGradient(props.agent.id) }))
</script>

<template>
  <button
    type="button"
    class="agent-row"
    :class="{ active, muted, disabled }"
    :disabled="disabled"
  >
    <span class="avatar-shell" :class="{ 'has-action': avatarAction }">
      <span class="agent-avatar" :style="avatarStyle">{{ initial }}</span>
      <HoloTooltip
        v-if="avatarAction"
        :title="avatarAction === 'add' ? '加入本组' : '移出本组'"
        :content="avatarAction === 'add' ? '点击把该 Agent 加入当前组' : '点击把该 Agent 移出当前组'"
        placement="right"
        :width="200"
      >
        <span class="avatar-action" role="button" @click.stop="emit('avatar-action')">
          {{ avatarAction === 'add' ? '+' : '×' }}
        </span>
      </HoloTooltip>
    </span>
    <span class="row-main">
      <strong>{{ agent.name }}</strong>
      <small>{{ agent.role_title || '自定义员工' }}</small>
    </span>
    <slot />
  </button>
</template>

<style scoped>
.agent-row {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  min-height: 44px;
  padding: 6px 8px;
  text-align: left;
  color: var(--text-2);
  border: 0;
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.agent-row:hover:not(:disabled) {
  color: var(--text-1);
  background: var(--bg-hover);
}

.agent-row.active {
  color: var(--text-1);
  background: rgba(55, 208, 196, 0.08);
}

.agent-row.muted,
.agent-row:disabled {
  cursor: default;
  opacity: 0.55;
}

.avatar-shell {
  position: relative;
  display: inline-flex;
  flex: none;
}

.avatar-action {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  font-size: 15px;
  font-weight: 700;
  line-height: 1;
  color: var(--text-1);
  border-radius: 50%;
  background: rgba(4, 16, 24, 0.74);
  opacity: 0;
  pointer-events: none;
  cursor: pointer;
  transition: opacity 0.15s ease;
}

.avatar-shell.has-action:hover .avatar-action {
  opacity: 1;
  pointer-events: auto;
}

.agent-avatar {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex: none;
  font-size: 12px;
  font-weight: 700;
  color: #041018;
  border-radius: 50%;
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
</style>
