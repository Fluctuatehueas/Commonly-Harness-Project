﻿using System;

namespace CommonHarnessPluginSDK.Attributes
{
    /// <summary>
    /// 所有以 gRPC 暴露给 Python Agent 的 C# 工具方法。
    /// </summary>
    [AttributeUsage(AttributeTargets.Method, AllowMultiple = false, Inherited = false)]
    public sealed class ToolAttribute : Attribute
    {
        /// <summary>工具名：必须为 snake_case（小写字母开头，只含小写字母、数字、下划线）；留空时用方法名。</summary>
        public string Name { get; init; } = "";

        /// <summary>给模型看的工具说明；留空时回退到 [Description] 特性或工具名。</summary>
        public string Description { get; init; } = "";

        /// <summary>业务域说明（如 scene / camera / fx / ui），仅用于展示与分组统计。</summary>
        public string Domain { get; init; } = "";

        /// <summary>技能分组名（ListTools 的 SkillDefinition.Name）；留空归入 default。</summary>
        public string Skill { get; init; } = "";

        /// <summary>中文显示名（执行面板 / 轨迹展示用）；留空时用工具名。</summary>
        public string DisplayName { get; init; } = "";

        /// <summary>沙箱等级：Auto 自动执行；Confirm 需确认；AlwaysConfirm 始终确认。</summary>
        public SandboxLevel SandboxLevel { get; init; } = SandboxLevel.Auto;

        /// <summary>是否为写出型操作（决定是否必须提交到撤销栈）。</summary>
        public bool IsWrite { get; init; }

        /// <summary>是否为破坏性操作（批量删除阈值升级判定用）。</summary>
        public bool IsDestructive { get; init; }

        /// <summary>审批时是否允许修改参数（对应 ResolveApproval 的 EDIT 决策）。</summary>
        public bool SupportsEditArgs { get; init; }

        /// <summary>工具执行超时（毫秒）；小于等于 0 时取 30000。</summary>
        public int TimeoutMs { get; init; } = 30_000;

        /// <summary>工具版本号（语义化字符串，仅展示与排查用）。</summary>
        public string Version { get; init; } = "1.0";

        /// <summary>构造空特性（全部取默认值）。</summary>
        public ToolAttribute()
        {
        }

        /// <summary>以工具名构造。</summary>
        /// <param name="name">snake_case 工具名。</param>
        public ToolAttribute(string name)
        {
            Name = name;
        }
    }

    /// <summary>
    /// 沙箱等级：声明一个工具"默认是否需要向用户提问"，是审批机制的唯一判据。
    /// 判据是"可撤销性"——能被 Ctrl+Z 完整撤销的写操作属于沙箱内，可自动执行。
    /// </summary>
    public enum SandboxLevel
    {
        /// <summary>沙箱内：自动执行。适用于只读查询、纯导航，以及可被撤销栈完整回滚的写操作。</summary>
        Auto = 0,

        /// <summary>越界：需确认。适用于不可撤销的写操作（如批量删除超过阈值、清空场景）。</summary>
        Confirm = 1,

        /// <summary>破坏性：始终需确认（即使全局为"完全自动"档也建议确认，用于不可挽回的操作）。</summary>
        AlwaysConfirm = 2,
    }

    
}
