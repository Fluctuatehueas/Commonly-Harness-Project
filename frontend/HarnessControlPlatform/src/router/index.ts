import { createRouter, createWebHistory } from 'vue-router'

import ChatView from '@/views/ChatView.vue'
import ChatControlView from '@/views/ChatControlView.vue'
import HomeView from '@/views/HomeView.vue'
import ModelsView from '@/views/ModelsView.vue'
import PlaceholderView from '@/views/PlaceholderView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'home', component: ChatView },
    { path: '/overview', name: 'overview', component: HomeView },
    { path: '/models', name: 'models', component: ModelsView },
    { path: '/chat-control', name: 'chat-control', component: ChatControlView },
    { path: '/kb', name: 'kb', component: PlaceholderView, props: { title: '知识库', icon: 'book' } },
    { path: '/workflow', name: 'workflow', component: PlaceholderView, props: { title: '工作流', icon: 'workflow' } },
    { path: '/plugins', name: 'plugins', component: PlaceholderView, props: { title: '插件市场', icon: 'plug' } },
    { path: '/settings', name: 'settings', component: PlaceholderView, props: { title: '系统设置', icon: 'gear' } },
  ],
})

export default router
