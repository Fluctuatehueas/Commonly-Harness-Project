﻿using System;
using System.Reflection;

namespace CommonHarnessPluginSDK.Utils
{
    /// <summary>
    /// 【Q1 作用】工具扫描入口。实际注册逻辑统一收敛到 <see cref="ToolRegistry"/>。
    /// 【Q2 调用方】宿主启动流程（WPF 的 App.OnStartup / 冒烟控制台）。
    /// 【Q3 调用时机】进程启动、主界面创建之前。
    /// 【Q4 释放】无。
    /// 【Q5 线程约束】注册阶段应单线程调用，避免同名工具并发注册。
    /// </summary>
    public static class ToolRegisterService
    {
        /// <summary>
        /// 注册某类型上的工具。实例由工厂按需解析（可为 null，表示宿主暂不可用）。
        /// </summary>
        public static int RegisterToolsFromType(
            Type type,
            Func<object?>? instanceFactory = null,
            Action<string>? log = null)
        {
            int count = instanceFactory is null
                ? ToolRegistry.Register(type)
                : ToolRegistry.RegisterWithFactory(type, instanceFactory);

            log?.Invoke($"[ToolRegistry] {type.Name} 注册 {count} 个工具。");
            return count;
        }

        /// <summary>
        /// 注册某类型上的工具，并绑定一个固定实例。
        /// </summary>
        public static int RegisterToolsFromObject(
            object instance,
            Action<string>? log = null)
        {
            if (instance is null)
            {
                throw new ArgumentNullException(nameof(instance));
            }

            int count = ToolRegistry.RegisterWithInstance(instance.GetType(), instance);
            log?.Invoke($"[ToolRegistry] {instance.GetType().Name} 注册 {count} 个工具。");
            return count;
        }

        /// <summary>
        /// 扫描程序集内全部 [Tool] 方法。
        /// </summary>
        public static int RegisterToolsFromAssembly(
            Assembly assembly,
            Func<Type, object?>? instanceFactory = null,
            Action<string>? log = null)
        {
            int count = ToolRegistry.RegisterFromAssembly(assembly, instanceFactory, log);
            log?.Invoke($"[ToolRegistry] 程序集 {assembly.GetName().Name} 注册 {count} 个工具。");
            return count;
        }

        /// <summary>
        /// 扫描当前已加载的全部程序集（调试 / 冒烟用；生产环境建议显式指定程序集）。
        /// </summary>
        public static int RegisterAllTools(Action<string>? log = null)
        {
            int total = 0;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    total += RegisterToolsFromAssembly(assembly, log: null);
                }
                catch (Exception ex)
                {
                    log?.Invoke($"[ToolRegistry] 扫描程序集 {assembly.FullName} 失败: {ex.Message}");
                }
            }

            log?.Invoke($"[ToolRegistry] 共注册 {total} 个工具。");
            return total;
        }
    }
}
