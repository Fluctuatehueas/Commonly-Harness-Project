<script setup lang="ts">
// 按扩展名生成彩色文件徽章：Office 蓝/橙/绿、PDF 红、Markdown 紫、主流代码语言等。
import { computed } from 'vue'

interface Badge {
  label: string
  color: string
  dark?: boolean
}

const props = withDefaults(defineProps<{ ext?: string; name?: string; size?: number }>(), {
  ext: '',
  name: '',
  size: 18,
})

// 标签最多 3 个字符，保证小尺寸下仍可辨认；未知类型回退为灰色普通文件图标。
const BADGES: Record<string, Badge> = {
  // ---------- 办公文档 ----------
  '.docx': { label: 'W', color: '#2b579a' },
  '.doc': { label: 'W', color: '#2b579a' },
  '.docm': { label: 'W', color: '#2b579a' },
  '.dot': { label: 'W', color: '#2b579a' },
  '.dotx': { label: 'W', color: '#2b579a' },
  '.odt': { label: 'ODT', color: '#2b579a' },
  '.pptx': { label: 'PPT', color: '#d24726' },
  '.ppt': { label: 'PPT', color: '#d24726' },
  '.pps': { label: 'PPT', color: '#d24726' },
  '.ppsx': { label: 'PPT', color: '#d24726' },
  '.pot': { label: 'PPT', color: '#d24726' },
  '.potx': { label: 'PPT', color: '#d24726' },
  '.odp': { label: 'ODP', color: '#d24726' },
  '.xlsx': { label: 'XLS', color: '#217346' },
  '.xls': { label: 'XLS', color: '#217346' },
  '.xlsm': { label: 'XLS', color: '#217346' },
  '.xlt': { label: 'XLS', color: '#217346' },
  '.xltx': { label: 'XLS', color: '#217346' },
  '.ods': { label: 'ODS', color: '#217346' },
  '.pdf': { label: 'PDF', color: '#d93025' },
  '.rtf': { label: 'RTF', color: '#7a5af8' },
  '.csv': { label: 'CSV', color: '#1a7f5a' },
  '.txt': { label: 'TXT', color: '#64748b' },
  '.text': { label: 'TXT', color: '#64748b' },
  '.log': { label: 'LOG', color: '#64748b' },

  // ---------- 数据与配置 ----------
  '.md': { label: 'MD', color: '#6f42c1' },
  '.markdown': { label: 'MD', color: '#6f42c1' },
  '.mdx': { label: 'MD', color: '#6f42c1' },
  '.json': { label: '{ }', color: '#b7791f' },
  '.jsonc': { label: '{ }', color: '#b7791f' },
  '.yaml': { label: 'YML', color: '#7c5cbf' },
  '.yml': { label: 'YML', color: '#7c5cbf' },
  '.toml': { label: 'T', color: '#6b7280' },
  '.ini': { label: 'INI', color: '#6b7280' },
  '.cfg': { label: 'INI', color: '#6b7280' },
  '.conf': { label: 'INI', color: '#6b7280' },
  '.xml': { label: 'XML', color: '#0284c7' },
  '.env': { label: 'ENV', color: '#4b5563' },

  // ---------- 前端 / 脚本 ----------
  '.html': { label: '<>', color: '#e34f26' },
  '.htm': { label: '<>', color: '#e34f26' },
  '.xhtml': { label: '<>', color: '#e34f26' },
  '.css': { label: 'CSS', color: '#2965f1' },
  '.scss': { label: 'CSS', color: '#c6538c' },
  '.sass': { label: 'CSS', color: '#c6538c' },
  '.less': { label: 'CSS', color: '#1d365d' },
  '.js': { label: 'JS', color: '#e6b800', dark: true },
  '.mjs': { label: 'JS', color: '#e6b800', dark: true },
  '.cjs': { label: 'JS', color: '#e6b800', dark: true },
  '.jsx': { label: 'JS', color: '#e6b800', dark: true },
  '.ts': { label: 'TS', color: '#3178c6' },
  '.tsx': { label: 'TS', color: '#3178c6' },
  '.vue': { label: 'VUE', color: '#41b883' },
  '.svelte': { label: 'SVE', color: '#ff3e00' },
  '.sh': { label: 'SH', color: '#4eaa25' },
  '.bash': { label: 'SH', color: '#4eaa25' },
  '.zsh': { label: 'SH', color: '#4eaa25' },
  '.fish': { label: 'SH', color: '#4eaa25' },
  '.ps1': { label: 'PS', color: '#012456' },
  '.bat': { label: 'BAT', color: '#4d4d4d' },
  '.cmd': { label: 'BAT', color: '#4d4d4d' },

  // ---------- 后端 / 系统语言 ----------
  '.py': { label: 'PY', color: '#3776ab' },
  '.pyw': { label: 'PY', color: '#3776ab' },
  '.ipynb': { label: 'IPY', color: '#f37626' },
  '.java': { label: 'J', color: '#b07219' },
  '.kt': { label: 'KT', color: '#7f52ff' },
  '.kts': { label: 'KT', color: '#7f52ff' },
  '.scala': { label: 'SC', color: '#dc322f' },
  '.cs': { label: 'C#', color: '#68217a' },
  '.c': { label: 'C', color: '#5c6bc0' },
  '.h': { label: 'C', color: '#5c6bc0' },
  '.cpp': { label: 'C++', color: '#00599c' },
  '.cc': { label: 'C++', color: '#00599c' },
  '.cxx': { label: 'C++', color: '#00599c' },
  '.hpp': { label: 'C++', color: '#00599c' },
  '.hh': { label: 'C++', color: '#00599c' },
  '.go': { label: 'GO', color: '#00add8' },
  '.rs': { label: 'RS', color: '#b7410e' },
  '.rb': { label: 'RB', color: '#cc342d' },
  '.php': { label: 'PHP', color: '#777bb4' },
  '.swift': { label: 'SW', color: '#f05138' },
  '.dart': { label: 'DT', color: '#0175c2' },
  '.lua': { label: 'LUA', color: '#2c2d72' },
  '.r': { label: 'R', color: '#276dc3' },
  '.m': { label: 'M', color: '#438eff' },
  '.mm': { label: 'M', color: '#438eff' },
  '.sql': { label: 'SQL', color: '#00758f' },
  '.asm': { label: 'ASM', color: '#6e4c13' },
  '.s': { label: 'ASM', color: '#6e4c13' },
  '.tex': { label: 'TEX', color: '#3d6117' },

  // ---------- 压缩包 / 图片 / 媒体 / 字体 ----------
  '.zip': { label: 'ZIP', color: '#b08968' },
  '.rar': { label: 'ZIP', color: '#b08968' },
  '.7z': { label: 'ZIP', color: '#b08968' },
  '.tar': { label: 'ZIP', color: '#b08968' },
  '.gz': { label: 'ZIP', color: '#b08968' },
  '.tgz': { label: 'ZIP', color: '#b08968' },
  '.bz2': { label: 'ZIP', color: '#b08968' },
  '.xz': { label: 'ZIP', color: '#b08968' },
  '.png': { label: 'IMG', color: '#0ea5a4' },
  '.jpg': { label: 'IMG', color: '#0ea5a4' },
  '.jpeg': { label: 'IMG', color: '#0ea5a4' },
  '.gif': { label: 'IMG', color: '#0ea5a4' },
  '.webp': { label: 'IMG', color: '#0ea5a4' },
  '.bmp': { label: 'IMG', color: '#0ea5a4' },
  '.ico': { label: 'IMG', color: '#0ea5a4' },
  '.avif': { label: 'IMG', color: '#0ea5a4' },
  '.tif': { label: 'IMG', color: '#0ea5a4' },
  '.tiff': { label: 'IMG', color: '#0ea5a4' },
  '.svg': { label: 'SVG', color: '#f59e0b' },
  '.mp3': { label: 'AUD', color: '#8b5cf6' },
  '.wav': { label: 'AUD', color: '#8b5cf6' },
  '.flac': { label: 'AUD', color: '#8b5cf6' },
  '.aac': { label: 'AUD', color: '#8b5cf6' },
  '.ogg': { label: 'AUD', color: '#8b5cf6' },
  '.mp4': { label: 'VID', color: '#ef4444' },
  '.mov': { label: 'VID', color: '#ef4444' },
  '.avi': { label: 'VID', color: '#ef4444' },
  '.mkv': { label: 'VID', color: '#ef4444' },
  '.webm': { label: 'VID', color: '#ef4444' },
  '.woff': { label: 'FNT', color: '#64748b' },
  '.woff2': { label: 'FNT', color: '#64748b' },
  '.ttf': { label: 'FNT', color: '#64748b' },
  '.otf': { label: 'FNT', color: '#64748b' },
}

function extensionOf(name: string): string {
  const index = name.lastIndexOf('.')
  return index >= 0 ? name.slice(index) : ''
}

const badge = computed<Badge>(() => {
  const key = (props.ext || extensionOf(props.name)).toLowerCase()
  return BADGES[key] || { label: '', color: '#5b6b82' }
})

const fontSize = computed(() => {
  const length = badge.value.label.length
  if (length <= 1) return 12
  if (length === 2) return 9
  return 7.4
})
</script>

<template>
  <svg
    class="file-type-icon"
    :width="size"
    :height="size"
    viewBox="0 0 32 32"
    aria-hidden="true"
  >
    <path
      :fill="badge.color"
      d="M7 1.5h12.4L27 8.8v19.7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-25a2 2 0 0 1 2-2z"
    />
    <path
      fill="#ffffff"
      fill-opacity="0.32"
      d="M19.4 1.5 27 8.8h-5.6a2 2 0 0 1-2-2z"
    />
    <text
      v-if="badge.label"
      x="16"
      y="22.4"
      text-anchor="middle"
      :font-size="fontSize"
      font-weight="700"
      :fill="badge.dark ? '#1e293b' : '#ffffff'"
      font-family="ui-monospace, SFMono-Regular, Menlo, monospace"
    >
      {{ badge.label }}
    </text>
  </svg>
</template>

<style scoped>
.file-type-icon {
  display: block;
  flex: none;
  filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.35));
}
</style>
