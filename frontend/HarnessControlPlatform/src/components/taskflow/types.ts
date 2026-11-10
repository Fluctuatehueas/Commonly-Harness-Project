// 任务流前端组件共享类型

/** 简图节点右键菜单载荷 */
export interface NodeMenuPayload {
  id: string
  x: number
  y: number
  /** 所属分支名；主流步骤为 null */
  branch: string | null
  /** 该步骤是否带并行子流 */
  isFork: boolean
}
