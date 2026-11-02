import { resolve } from 'node:path'
import { defineConfig, type ElectronViteConfigFn } from 'electron-vite'
import { loadConfigFromFile, mergeConfig, type PluginOption, type UserConfig } from 'vite'

// 外部 Vue 工程（沿用 electron-vite 的多配置能力：渲染进程直接加载它的 vite.config）
const VUE_PROJECT_ROOT = resolve(__dirname, '../../HarnessControlPlatform')

function flattenPlugins(plugins: PluginOption[] | undefined): PluginOption[] {
  const result: PluginOption[] = []
  for (const plugin of plugins ?? []) {
    if (!plugin) continue
    if (Array.isArray(plugin)) {
      result.push(...flattenPlugins(plugin))
      continue
    }
    result.push(plugin)
  }
  return result
}

function pluginName(plugin: PluginOption): string {
  if (!plugin || typeof plugin !== 'object' || Array.isArray(plugin)) return ''
  const name = (plugin as { name?: unknown }).name
  return typeof name === 'string' ? name : ''
}

const config: ElectronViteConfigFn = async (env) => {
  // 交给 Vite 自己解析外部工程配置：它能正确处理 @ → ./src 这类基于自身文件位置的别名
  const loaded = await loadConfigFromFile(
    { command: env.command, mode: env.mode, isSsrBuild: false, isPreview: false },
    resolve(VUE_PROJECT_ROOT, 'vite.config.ts'),
    VUE_PROJECT_ROOT,
  )
  const vueConfig = (loaded?.config ?? {}) as UserConfig

  const renderer = mergeConfig(vueConfig, {
    root: VUE_PROJECT_ROOT,
    // 打包后由 file:// 加载，资源必须用相对路径
    base: './',
    build: {
      outDir: resolve(__dirname, 'out/renderer'),
      emptyOutDir: true,
      // electron-vite 默认把渲染入口固定为 <项目>/src/renderer/index.html，
      // 这里必须显式指到外部 Vue 工程的 index.html。
      rollupOptions: {
        input: resolve(VUE_PROJECT_ROOT, 'index.html'),
      },
    },
    // 与后端 CORS 默认放行的 5173 对齐；端口被占用时 Vite 会自动递增
    server: { port: 5173, strictPort: false },
  })

  // vite-plugin-vue-devtools 依赖独立的浏览器调试页，Electron 自带 devtools，这里剔除
  renderer.plugins = flattenPlugins(renderer.plugins).filter(
    (plugin) => !pluginName(plugin).includes('vue-devtools'),
  )

  return {
    main: {},
    preload: {},
    renderer,
  }
}

export default defineConfig(config)
