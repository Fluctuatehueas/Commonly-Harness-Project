// 控制台首页 mock 数据（接入后端后可替换为 API 请求）
import type { IconName } from '@/components/console/icons'

export interface NavItem {
  key: string
  label: string
  icon: IconName
  path: string
}

export const NAV_ITEMS: NavItem[] = [
  { key: 'chat', label: '对话测试', icon: 'message', path: '/' },
  { key: 'overview', label: '总览', icon: 'home', path: '/overview' },
  { key: 'models', label: '模型管理', icon: 'cube', path: '/models' },
  { key: 'chat-control', label: '对话控制', icon: 'sliders', path: '/chat-control' },
  { key: 'kb', label: '知识库', icon: 'book', path: '/kb' },
  { key: 'workflow', label: '工作流', icon: 'workflow', path: '/workflow' },
  { key: 'plugins', label: '插件市场', icon: 'plug', path: '/plugins' },
  { key: 'settings', label: '系统设置', icon: 'gear', path: '/settings' },
]

export interface ModelCard {
  name: string
  provider: string
  initial: string
  gradient: string
  context: string
  tags: string[]
}

export const MODELS: ModelCard[] = [
  {
    name: 'GPT-4o',
    provider: 'OpenAI',
    initial: 'O',
    gradient: 'linear-gradient(135deg, #10a37f, #0d7a5f)',
    context: '128K 上下文',
    tags: ['多模态', '函数调用'],
  },
  {
    name: 'Claude 3.5 Sonnet',
    provider: 'Anthropic',
    initial: 'C',
    gradient: 'linear-gradient(135deg, #d97757, #a8543a)',
    context: '200K 上下文',
    tags: ['长文本', '工具调用'],
  },
  {
    name: 'Qwen2.5-72B',
    provider: 'Alibaba',
    initial: 'Q',
    gradient: 'linear-gradient(135deg, #615ced, #3c34b8)',
    context: '128K 上下文',
    tags: ['中文优化', '多轮对话'],
  },
  {
    name: 'DeepSeek R1',
    provider: 'DeepSeek',
    initial: 'D',
    gradient: 'linear-gradient(135deg, #4f8cff, #2456c8)',
    context: '128K 上下文',
    tags: ['强推理', '长文本'],
  },
]

export interface FlowNode {
  key: string
  title: string
  desc: string
  icon: IconName
}

export const FLOW_MAIN: FlowNode[] = [
  { key: 'input', title: '用户输入', desc: '你的需求和目标', icon: 'user' },
  { key: 'plan', title: '规划智能体', desc: '任务分解 · 方案生成', icon: 'target' },
  { key: 'exec', title: '执行智能体', desc: '工具调用 · 任务执行', icon: 'play' },
  { key: 'output', title: '结果输出', desc: '结构化结果 · 可视化', icon: 'layers' },
]

export const FLOW_KNOWLEDGE: FlowNode = {
  key: 'knowledge',
  title: '知识智能体',
  desc: '知识检索 · 信息整合',
  icon: 'database',
}

export interface QuickItem {
  key: string
  label: string
  icon: IconName
  accent: string
}

export const QUICK_ENTRIES: QuickItem[] = [
  { key: 'chat', label: '创建对话', icon: 'chatPlus', accent: '#4f8cff' },
  { key: 'workflow', label: '新建工作流', icon: 'workflow', accent: '#35e0ff' },
  { key: 'models', label: '模型广场', icon: 'cube', accent: '#8f7bff' },
  { key: 'plugins', label: '插件市场', icon: 'plug', accent: '#ffb648' },
  { key: 'kb', label: '知识库', icon: 'book', accent: '#3ddc84' },
  { key: 'settings', label: '系统设置', icon: 'gear', accent: '#9db0d6' },
]

export interface ActivityItem {
  key: string
  title: string
  desc: string
  time: string
  color: string
}

export const ACTIVITIES: ActivityItem[] = [
  { key: 'a1', title: '模型调用完成', desc: 'GPT-4o', time: '2分钟前', color: '#3ddc84' },
  { key: 'a2', title: '工作流执行成功', desc: '执行规划助手', time: '8分钟前', color: '#4f8cff' },
  { key: 'a3', title: '插件任务完成', desc: '文件搜索', time: '12分钟前', color: '#ffb648' },
  { key: 'a4', title: '会话已开始', desc: '你好，我是 Harness 助手', time: '15分钟前', color: '#35e0ff' },
  { key: 'a5', title: '系统备份完成', desc: '', time: '1小时前', color: '#8f7bff' },
]
