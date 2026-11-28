using CommonHarnessPluginSDK.Attributes;
using System.Collections.Generic;

namespace CommonHarnessPluginSDK.Models
{
    /// <summary>
    /// 工具描述模型：ListTools 返回的 ToolDescriptor 由本模型映射生成。
    /// 工具名 / 描述 / 参数 Schema / 沙箱等级以 <see cref="Tools.ToolRegistry"/> 为唯一事实源。
    /// </summary>
    public sealed class ToolMetadata
    {
        public string Name { get; init; } = "";
        public string DisplayName { get; init; } = "";
        public string Description { get; init; } = "";
        public string Domain { get; init; } = "";
        public string Skill { get; init; } = "";
        public string ArgsSchemaJson { get; init; } = "{}";
        public string ResultSchemaJson { get; init; } = "";
        public SandboxLevel SandboxLevel { get; init; } = SandboxLevel.Auto;
        public bool IsWrite { get; init; }
        public bool IsDestructive { get; init; }
        public bool SupportsEditArgs { get; init; }
        public int TimeoutMs { get; init; } = 30_000;
        public string Version { get; init; } = "1.0";
    }

    /// <summary>
    /// 技能定义：工具分组 + 可选提示词片段。C# 是工具与技能清单的权威源。
    /// </summary>
    public sealed class SkillDefinition
    {
        public string Name { get; init; } = "";
        public string DisplayName { get; init; } = "";
        public string Description { get; init; } = "";
        public string PromptFragment { get; init; } = "";
        public List<ToolMetadata> Tools { get; init; } = new();
        public string MetadataJson { get; init; } = "{}";
    }
}
