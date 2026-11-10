/**
 * ECharts 配置归一化：避免标题、图例、绘图区在顶部互相重叠。
 *
 * 模型生成的 option 经常同时写 `title: { top: ... }` 和默认顶部的 legend，
 * 两者会挤在同一行。这里做保守排版修正，不改变用户显式指定的底部图例。
 */
export function normalizeChartOption(raw: unknown): Record<string, unknown> {
  if (!raw || typeof raw !== 'object') return {}
  const option = JSON.parse(JSON.stringify(raw)) as Record<string, unknown>

  const title = firstObject(option.title)
  const legend = firstObject(option.legend)
  const grid = firstObject(option.grid)

  const hasTitle = title !== null && !isBottom(title)
  const hasLegend = legend !== null
  const legendAtTop = legend !== null && isTop(legend)
  const titleHasSubtext = Boolean(title?.subtext)

  // 标题和顶部图例同时存在时，把图例下移，给标题让出一行。
  if (hasTitle && legendAtTop && legend) {
    legend.top = titleHasSubtext ? 48 : 34
  }

  const titleSpace = hasTitle ? (titleHasSubtext ? 78 : 58) : 0
  const legendSpace = hasLegend && legendAtTop ? 30 : 0
  const requiredTop = titleSpace + legendSpace
  if (requiredTop > 0 && grid) {
    const current = numericTop(grid.top)
    if (current == null || current < requiredTop) {
      grid.top = requiredTop
    }
  } else if (requiredTop > 0 && !grid) {
    option.grid = { top: requiredTop }
  }

  return option
}

function firstObject(value: unknown): Record<string, unknown> | null {
  if (Array.isArray(value)) {
    return value.length && value[0] && typeof value[0] === 'object'
      ? (value[0] as Record<string, unknown>)
      : null
  }
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
}

function isBottom(value: Record<string, unknown>): boolean {
  return value.bottom != null || value.top === 'bottom'
}

function isTop(value: Record<string, unknown>): boolean {
  if (value.bottom != null) return false
  if (value.top == null || value.top === 'top') return true
  const top = numericTop(value.top)
  return top != null && top < 48
}

function numericTop(value: unknown): number | null {
  if (typeof value === 'number') return value
  if (typeof value === 'string') {
    const parsed = Number.parseFloat(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}
