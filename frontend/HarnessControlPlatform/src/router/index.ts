import { createRouter, createWebHashHistory, createWebHistory } from 'vue-router'

import ChatView from '@/views/ChatView.vue'
import HomeView from '@/views/HomeView.vue'
import McpView from '@/views/McpView.vue'
import ModelsView from '@/views/ModelsView.vue'
import PlaceholderView from '@/views/PlaceholderView.vue'
import RagView from '@/views/RagView.vue'
import SettingsView from '@/views/SettingsView.vue'
import SkillsView from '@/views/SkillsView.vue'
import TrajectoryView from '@/views/TrajectoryView.vue'

// 同一套代码同时跑在浏览器与 Electron：打包后的 Electron 通过 file:// 加载页面，
// 此时 Web History 无法解析路径，必须退化为 hash 路由。
const isFileProtocol = window.location.protocol === 'file:'

const router = createRouter({
  history: isFileProtocol
    ? createWebHashHistory(import.meta.env.BASE_URL)
    : createWebHistory(import.meta.env.BASE_URL),
  routes: [
    // 菜单顺序：工作台、对话、轨迹、我的任务图、插件市场、技能管理、MCP服务、知识库、模型管理、系统设置
    { path: '/', name: 'home', component: HomeView },
    { path: '/chat', name: 'chat', component: ChatView },
    { path: '/trajectory', name: 'workflow', component: TrajectoryView },
    { path: '/tasks', name: 'tasks', component: PlaceholderView, props: { title: '我的任务图', icon: 'target' } },
    { path: '/plugins', name: 'plugins', component: PlaceholderView, props: { title: '插件市场', icon: 'plug' } },
    { path: '/skills', name: 'skills', component: SkillsView },
    { path: '/mcp', name: 'mcp', component: McpView },
    { path: '/kb', name: 'kb', component: RagView },
    { path: '/models', name: 'models', component: ModelsView },
    { path: '/settings', name: 'settings', component: SettingsView },
    // 兼容旧地址
    { path: '/overview', redirect: '/' },
    { path: '/chat-control', redirect: '/settings' },
    { path: '/workflow', redirect: '/trajectory' },
  ],
})

export default router
