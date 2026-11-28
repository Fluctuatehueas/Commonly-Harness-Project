﻿using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.ComponentModel;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using CommonHarnessPluginSDK.Attributes;
using CommonHarnessPluginSDK.Models;

namespace CommonHarnessPluginSDK.Utils
{
    /// <summary>
    /// 单个工具的注册信息。
    /// </summary>
    public sealed class ToolRegistration
    {
        public required string Name { get; init; }
        public required ToolAttribute Attribute { get; init; }
        public required MethodInfo Method { get; init; }
        public required ToolMetadata Metadata { get; init; }
        public required Func<object?[], CancellationToken, Task<ToolResult>> Invoker { get; init; }
    }

    /// <summary>
    /// [Tool] 注册表：启动时扫描方法、生成 Schema、派发 JSON 参数。
    ///           工具名、描述、参数 Schema、沙箱等级以本注册表为唯一事实源。
    /// <para>实例解析策略：静态方法直接调用；实例方法按注册时提供的工厂在<b>每次调用时</b>解析，
    /// 因此可以在 WPF 主界面尚未创建时完成工具注册（真正的 ViewModel 在首次调用工具时才需要）。</para>
    /// 【Q2 调用方】ToolRegisterService（注册）、ToolGrpcService（ListTools / CallTool）。
    /// 【Q3 创建时机】宿主启动时一次性注册；注册后只读。
    /// 【Q4 释放】进程级静态表，不释放。
    /// 【Q5 线程约束】注册阶段单线程；调用阶段多线程读取，ConcurrentDictionary 保证安全。
    /// </summary>
    public static class ToolRegistry
    {
        private static readonly ConcurrentDictionary<string, ToolRegistration> Tools =
            new ConcurrentDictionary<string, ToolRegistration>(StringComparer.Ordinal);

        private static readonly Regex NamePattern = new Regex(
            "^[a-z][a-z0-9_]*$",
            RegexOptions.Compiled | RegexOptions.CultureInvariant);

        public static int Count => Tools.Count;

        public static IReadOnlyDictionary<string, ToolRegistration> GetRegistrations()
        {
            return Tools;
        }

        public static bool TryGet(string name, out ToolRegistration registration)
        {
            return Tools.TryGetValue(name, out registration!);
        }

        /// <summary>
        /// 注册某类型上的全部 [Tool] 方法。实例方法使用"首次调用时创建并缓存"的策略。
        /// </summary>
        public static int Register(Type type)
        {
            return RegisterCore(type, () => CreateInstance(type));
        }

        /// <summary>
        /// 注册某类型上的全部 [Tool] 方法，并显式提供宿主实例。
        /// </summary>
        public static int RegisterWithInstance(Type type, object instance)
        {
            if (instance is null)
            {
                throw new ArgumentNullException(nameof(instance));
            }

            return RegisterCore(type, () => instance);
        }

        /// <summary>
        /// 注册某类型上的全部 [Tool] 方法，实例由工厂按需解析（返回 null 表示宿主暂不可用）。
        /// </summary>
        public static int RegisterWithFactory(Type type, Func<object?> factory)
        {
            if (factory is null)
            {
                throw new ArgumentNullException(nameof(factory));
            }

            return RegisterCore(type, factory);
        }

        /// <summary>
        /// 扫描程序集中所有带 [Tool] 方法的类型。
        /// </summary>
        /// <param name="assembly">目标程序集。</param>
        /// <param name="factory">实例工厂；为 null 时按需 Activator 创建。</param>
        /// <param name="log">可选日志回调（注册失败时输出原因，不中断整体扫描）。</param>
        public static int RegisterFromAssembly(Assembly assembly, Func<Type, object?>? factory = null, Action<string>? log = null)
        {
            if (assembly is null)
            {
                throw new ArgumentNullException(nameof(assembly));
            }

            int count = 0;
            foreach (Type type in assembly.GetTypes())
            {
                try
                {
                    count += factory is null
                        ? Register(type)
                        : RegisterWithFactory(type, () => factory(type));
                }
                catch (Exception ex)
                {
                    log?.Invoke($"[ToolRegistry] 跳过 {type.FullName}: {ex.Message}");
                }
            }

            return count;
        }

        /// <summary>
        /// 按技能（Skill）分组返回工具清单，作为 ListTools 的响应内容。
        /// </summary>
        public static IReadOnlyList<SkillDefinition> GetSkills()
        {
            return Tools.Values
                .GroupBy(tool => tool.Metadata.Skill, StringComparer.Ordinal)
                .Select(group => new SkillDefinition
                {
                    Name = group.Key,
                    DisplayName = group.Key,
                    Description = $"C# 端工具分组：{group.Key}",
                    Tools = group
                        .Select(tool => tool.Metadata)
                        .OrderBy(tool => tool.Name, StringComparer.Ordinal)
                        .ToList(),
                })
                .OrderBy(skill => skill.Name, StringComparer.Ordinal)
                .ToList();
        }

        /// <summary>清空注册表（仅测试 / 重新注册时使用）。</summary>
        public static void Clear()
        {
            Tools.Clear();
        }

        private static int RegisterCore(Type type, Func<object?> instanceFactory)
        {
            if (type is null)
            {
                throw new ArgumentNullException(nameof(type));
            }

            if (type.IsInterface || type.IsGenericTypeDefinition)
            {
                return 0;
            }

            // 抽象类不扫描；静态类（abstract + sealed）允许只注册静态方法。
            if (type.IsAbstract && !type.IsSealed)
            {
                return 0;
            }

            List<MethodInfo> candidates = type
                .GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static)
                .Where(method => method.GetCustomAttribute<ToolAttribute>() is not null)
                .ToList();

            if (candidates.Count == 0)
            {
                return 0;
            }

            int registered = 0;
            foreach (MethodInfo method in candidates)
            {
                ToolAttribute attribute = method.GetCustomAttribute<ToolAttribute>()!;
                string name = string.IsNullOrWhiteSpace(attribute.Name)
                    ? method.Name
                    : attribute.Name.Trim();

                if (!NamePattern.IsMatch(name))
                {
                    throw new InvalidOperationException(
                        $"工具名 {name} 不合法：必须为 snake_case（小写字母开头，只含小写字母、数字、下划线）。");
                }

                if (method.IsGenericMethodDefinition)
                {
                    throw new InvalidOperationException($"工具 {name} 不能是泛型方法。");
                }

                string description = !string.IsNullOrWhiteSpace(attribute.Description)
                    ? attribute.Description
                    : method.GetCustomAttribute<DescriptionAttribute>()?.Description ?? name;

                ToolMetadata metadata = new ToolMetadata
                {
                    Name = name,
                    DisplayName = string.IsNullOrWhiteSpace(attribute.DisplayName) ? name : attribute.DisplayName,
                    Description = description,
                    Domain = attribute.Domain,
                    Skill = string.IsNullOrWhiteSpace(attribute.Skill) ? "default" : attribute.Skill,
                    ArgsSchemaJson = ToolSchemaBuilder.BuildArgsSchema(method),
                    ResultSchemaJson = ToolSchemaBuilder.BuildResultSchema(method),
                    SandboxLevel = attribute.SandboxLevel,
                    IsWrite = attribute.IsWrite,
                    IsDestructive = attribute.IsDestructive,
                    SupportsEditArgs = attribute.SupportsEditArgs,
                    TimeoutMs = attribute.TimeoutMs > 0 ? attribute.TimeoutMs : 30_000,
                    Version = attribute.Version,
                };

                ToolRegistration registration = new ToolRegistration
                {
                    Name = name,
                    Attribute = attribute,
                    Method = method,
                    Metadata = metadata,
                    Invoker = (args, cancellationToken) => ToolInvoker.InvokeAsync(
                        method,
                        method.IsStatic ? null : instanceFactory(),
                        args,
                        cancellationToken),
                };

                if (!Tools.TryAdd(name, registration))
                {
                    throw new InvalidOperationException(
                        $"工具名重复：{name}。同一进程内工具名必须唯一，禁止让模型在两个同名工具之间猜。");
                }

                registered++;
            }

            return registered;
        }

        private static object CreateInstance(Type type)
        {
            if (type.IsAbstract)
            {
                throw new InvalidOperationException(
                    $"类型 {type.FullName} 含实例 [Tool] 方法，但无法实例化抽象类。");
            }

            return Activator.CreateInstance(type)
                ?? throw new InvalidOperationException(
                    $"无法创建 {type.FullName} 的实例；请提供无参构造函数或显式传入实例。");
        }
    }
}
