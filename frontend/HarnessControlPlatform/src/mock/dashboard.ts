// 控制台首页 mock 数据（接入后端后可替换为 API 请求）
import type { IconName } from '@/components/console/icons'

export interface NavItem {
  key: string
  label: string
  icon: IconName
  path: string
  /** 工作台悬停全息屏中的模块简介 */
  description: string
}

export const NAV_ITEMS: NavItem[] = [
  {
    key: 'home',
    label: '工作台',
    icon: 'home',
    path: '/',
    description: '轨道式模块总览：把对话、轨迹、任务流、技能与 MCP 收在同一张控制台里，快速进入任意工作区域。',
  },
  {
    key: 'chat',
    label: '对话',
    icon: 'message',
    path: '/chat',
    description: '与主 Agent 实时对话。SSE 流式输出推理、工具调用、子 Agent 委派与人工介入，可切换你招募的 Agent。',
  },
  {
    key: 'workflow',
    label: '轨迹',
    icon: 'workflow',
    path: '/trajectory',
    description: '回放每一次任务执行的轨迹：上下文占用、工具调用、HITL 审批与经验命中，用于排障与效果评估。',
  },
  {
    key: 'tasks',
    label: '我的任务流',
    icon: 'target',
    path: '/tasks',
    description: '把稳定的多步骤流程固化为任务流；命中后按步骤执行并实时展示产出，也支持从任意步骤中途接管。',
  },
  {
    key: 'plugins',
    label: '插件市场',
    icon: 'plug',
    path: '/plugins',
    description: '发现并安装可复用的插件与工具包。当前为占位页，后续会接入技能、MCP 与任务流的一键安装。',
  },
  {
    key: 'skills',
    label: '技能管理',
    icon: 'sparkles',
    path: '/skills',
    description: '管理你的私有技能库：安装、启停、分配与查看 SKILL.md。每个用户拥有独立技能目录，互不可见。',
  },
  {
    key: 'mcp',
    label: 'MCP服务',
    icon: 'server',
    path: '/mcp',
    description: '接入 MCP 工具服务：粘贴厂商 JSON 即可导入，查看连接状态、工具清单与运行日志。配置按用户隔离。',
  },
  {
    key: 'kb',
    label: '知识库',
    icon: 'book',
    path: '/kb',
    description: '注册 RAG Provider 并统一检索。Harness 只做适配与引用归一化，知识原文与向量仍归你的知识服务。',
  },
  {
    key: 'models',
    label: 'MyAgent',
    icon: 'cube',
    path: '/models',
    description: '招募并管理你的主 Agent：编辑系统提示词、绑定 MCP 服务、为每个 Agent 选择独立模型；对话时自由切换。',
  },
  {
    key: 'settings',
    label: '系统设置',
    icon: 'gear',
    path: '/settings',
    description: '配置模型、沙箱、上下文压缩与归档策略。所有设置按当前用户保存，切换用户不会互相影响。',
  },
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
