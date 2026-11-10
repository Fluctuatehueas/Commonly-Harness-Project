import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

import App from './App.vue'
import router from './router'
import { installUserHeader } from './api/identity'

// 样式顺序：Element Plus 基础 → 深色变量 → 项目全局 → 主题桥接（最后覆盖）
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/global.css'
import './styles/element-theme.css'

// Element Plus 深色模式开关
document.documentElement.classList.add('dark')

// 所有请求统一携带演示用户身份，后端据此隔离数据。
installUserHeader()

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

app.mount('#app')
