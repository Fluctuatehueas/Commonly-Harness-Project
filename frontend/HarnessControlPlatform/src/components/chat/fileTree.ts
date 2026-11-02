// 文件抽屉的树结构：按 origin 分组后，把扁平路径转成目录树。
import type { FileMeta, FileOrigin } from '@/stores/files'

export interface TreeFileNode {
  kind: 'file'
  name: string
  path: string
  file: FileMeta
}

export interface TreeFolderNode {
  kind: 'folder'
  name: string
  path: string
  children: TreeNode[]
}

export type TreeNode = TreeFileNode | TreeFolderNode

export interface FileGroup {
  origin: FileOrigin
  label: string
  files: FileMeta[]
  tree: TreeNode[]
}

/** 路径分段；忽略开头的 `/` 与空段。 */
function segments(path: string): string[] {
  return path.split('/').filter(Boolean)
}

function sortNodes(nodes: TreeNode[]): TreeNode[] {
  nodes.sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === 'folder' ? -1 : 1
    return a.name.localeCompare(b.name, 'zh-CN')
  })
  for (const node of nodes) {
    if (node.kind === 'folder') node.children = sortNodes(node.children)
  }
  return nodes
}

export function buildTree(items: FileMeta[]): TreeNode[] {
  const root: TreeNode[] = []
  for (const file of items) {
    const parts = segments(file.path)
    const name = parts[parts.length - 1] ?? file.path
    let level = root
    let currentPath = ''
    for (let index = 0; index < parts.length - 1; index += 1) {
      const folderName = parts[index]
      if (folderName === undefined) continue
      currentPath += `/${folderName}`
      const existing = level.find(
        (node): node is TreeFolderNode =>
          node.kind === 'folder' && node.name === folderName,
      )
      let folder: TreeFolderNode
      if (existing) {
        folder = existing
      } else {
        folder = { kind: 'folder', name: folderName, path: currentPath, children: [] }
        level.push(folder)
      }
      level = folder.children
    }
    level.push({ kind: 'file', name, path: file.path, file })
  }
  return sortNodes(root)
}

export function countFiles(node: TreeNode): number {
  if (node.kind === 'file') return 1
  return node.children.reduce((total, child) => total + countFiles(child), 0)
}

export function buildGroups(items: FileMeta[]): FileGroup[] {
  const project = items.filter((file) => file.origin === 'project')
  const sandbox = items.filter((file) => file.origin === 'sandbox')
  const groups: FileGroup[] = [
    {
      origin: 'project',
      label: '本机项目',
      files: project,
      tree: buildTree(project),
    },
    {
      origin: 'sandbox',
      label: '沙箱工作目录',
      files: sandbox,
      tree: buildTree(sandbox),
    },
  ]
  return groups.filter((group) => group.files.length > 0)
}
