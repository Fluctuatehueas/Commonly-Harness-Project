<script setup lang="ts">
// 右上角演示用户切换：切换后整页重载，避免上一个用户的会话/工具状态残留。
import { computed } from 'vue'
import { ArrowDown } from '@element-plus/icons-vue'
import {
  DEMO_USERS,
  currentUser,
  currentUserId,
  switchUser,
} from '@/api/identity'

const active = computed(() => currentUser())

function onCommand(userId: string) {
  if (userId === currentUserId()) return
  switchUser(userId)
}
</script>

<template>
  <el-dropdown trigger="click" class="user-switcher" @command="onCommand">
    <button class="user-pill" type="button" title="切换演示用户">
      <span class="user-avatar" :style="{ background: active.accent }">
        {{ active.name.slice(0, 1) }}
      </span>
      <span class="user-meta">
        <strong>{{ active.name }}</strong>
        <small>{{ active.role }}</small>
      </span>
      <el-icon class="user-arrow"><ArrowDown /></el-icon>
    </button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item
          v-for="user in DEMO_USERS"
          :key="user.id"
          :command="user.id"
          :class="{ 'is-current': user.id === currentUserId() }"
        >
          <span class="menu-avatar" :style="{ background: user.accent }">
            {{ user.name.slice(0, 1) }}
          </span>
          <span class="menu-text">
            <strong>{{ user.name }}</strong>
            <small>{{ user.role }} · {{ user.id }}</small>
          </span>
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<style scoped>
.user-switcher {
  flex: none;
}

.user-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 30px;
  padding: 0 9px 0 5px;
  color: var(--text-1);
  border: 1px solid var(--line);
  border-radius: 999px;
  background: rgba(10, 18, 34, 0.55);
  transition: border-color 0.18s, background 0.18s;
}

.user-pill:hover {
  border-color: rgba(55, 208, 196, 0.55);
  background: rgba(55, 208, 196, 0.08);
}

.user-avatar,
.menu-avatar {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  flex: none;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
  color: #041018;
}

.user-meta {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
  text-align: left;
}

.user-meta strong {
  font-size: 11.5px;
  font-weight: 600;
}

.user-meta small {
  font-size: 9px;
  color: var(--text-3);
}

.user-arrow {
  font-size: 10px;
  color: var(--text-3);
}

.menu-avatar {
  width: 24px;
  height: 24px;
  margin-right: 8px;
}

.menu-text {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}

.menu-text strong {
  font-size: 12px;
}

.menu-text small {
  font-size: 10px;
  color: var(--text-3);
}

:deep(.el-dropdown-menu__item.is-current) {
  background: rgba(55, 208, 196, 0.08);
}
</style>
