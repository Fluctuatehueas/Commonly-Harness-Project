<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch, watchEffect } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import StarField from '@/components/console/StarField.vue'
import { useChatStore } from '@/stores/chat'
import { useSessionStore } from '@/stores/sessions'

const route = useRoute()
const router = useRouter()
const chat = useChatStore()
const sessions = useSessionStore()
// 悬浮助手与托盘弹窗是透明窗口：去掉星空与全局底色，只保留内容层。
const desktopOverlay = computed(() => Boolean(route.meta.desktopOverlay))
let disposeOpenSession: (() => void) | undefined

watchEffect(() => {
  document.documentElement.classList.toggle('desktop-overlay', desktopOverlay.value)
})

onMounted(() => {
  // 不属于对话页时，托盘弹窗点选会话需要先切到对话路由；
  // 对话页自身的监听会负责加载历史，这里只做路由跳转。
  disposeOpenSession = window.harnessDesktop?.onOpenSession((threadId) => {
    if (!threadId || desktopOverlay.value || route.name === 'chat') return
    sessions.activeThreadId = threadId
    void router.push('/chat')
  })
})

onBeforeUnmount(() => disposeOpenSession?.())

// 主窗口在所有页面都上报运行中会话，避免切到设置页后会话列表停止更新。
watch(
  () => [chat.runningThreadIds, chat.waitingThreadIds, sessions.sessions] as const,
  () => {
    if (desktopOverlay.value || !window.harnessDesktop) return
    const waiting = new Set(chat.waitingThreadIds)
    const ids = new Set<string>([...chat.runningThreadIds, ...waiting])
    window.harnessDesktop.reportRunningSessions(
      [...ids].map((threadId) => ({
        threadId,
        title: sessions.sessions.find((item) => item.thread_id === threadId)?.title || '新对话',
        waiting: waiting.has(threadId),
      })),
    )
  },
  { immediate: true },
)
</script>

<template>
  <StarField v-if="!desktopOverlay" />
  <RouterView />
</template>
