﻿using System;
using System.Collections.Generic;

namespace CommonHarnessPluginSDK.Models
{
    public sealed class CapabilityRecord
    {
        public string Name { get; init; } = "";
        public bool Enabled { get; init; }
        public string Detail { get; init; } = "";
    }

    public sealed class CapabilitiesSnapshot
    {
        public string ContractVersion { get; init; } = "";
        public string ModelName { get; init; } = "";
        public bool ModelThinkingEnabled { get; init; }
        public bool SandboxEnabled { get; init; }
        public int PythonToolCount { get; init; }
        public int SkillCount { get; init; }
        public int SubagentCount { get; init; }
        public int McpServerCount { get; init; }
        public int SessionCount { get; init; }
        public IReadOnlyList<CapabilityRecord> Capabilities { get; init; } = Array.Empty<CapabilityRecord>();
    }

    public sealed class ModelSettingsSnapshot
    {
        public string ModelName { get; init; } = "";
        public string BaseUrl { get; init; } = "";
        public bool HasApiKey { get; init; }
        public bool ThinkingEnabled { get; init; }
    }

    public sealed class SkillRecord
    {
        public string Name { get; init; } = "";
        public string DisplayName { get; init; } = "";
        public string Description { get; init; } = "";
        public string Path { get; init; } = "";
        public string MetadataJson { get; init; } = "";
    }

    public sealed class SkillDetailRecord
    {
        public SkillRecord? Skill { get; init; }
        public string Content { get; init; } = "";
    }

    public sealed class SubagentRecord
    {
        public string Name { get; init; } = "";
        public string Description { get; init; } = "";
        public string Path { get; init; } = "";
        public string ConfigJson { get; init; } = "";

        /// <summary>必需工具（缺失会导致 Python 构图失败）。</summary>
        public IReadOnlyList<string> Tools { get; init; } = Array.Empty<string>();

        /// <summary>可选工具（C# 注册后自动绑定，未注册则跳过）。</summary>
        public IReadOnlyList<string> OptionalTools { get; init; } = Array.Empty<string>();

        /// <summary>引用的 MCP Server 名称。</summary>
        public IReadOnlyList<string> McpServers { get; init; } = Array.Empty<string>();

        /// <summary>子 Agent 技能目录。</summary>
        public IReadOnlyList<string> Skills { get; init; } = Array.Empty<string>();

        /// <summary>模型别名：main / vision，空表示继承主模型。</summary>
        public string Model { get; init; } = "";

        public string ModelText => string.IsNullOrWhiteSpace(Model) ? "继承主模型" : Model;

        public string ToolsText => Tools.Count == 0 ? "—" : string.Join(", ", Tools);

        public string OptionalToolsText => OptionalTools.Count == 0 ? "—" : string.Join(", ", OptionalTools);

        public string McpServersText => McpServers.Count == 0 ? "—" : string.Join(", ", McpServers);

        public string SkillsText => Skills.Count == 0 ? "—" : string.Join(", ", Skills);
    }

    public sealed class McpServerRecord
    {
        public string Name { get; init; } = "";
        public string Transport { get; init; } = "";
        public string Description { get; init; } = "";
        public string ConfigJson { get; init; } = "";

        /// <summary>连接失败只告警、不阻断构图。</summary>
        public bool Optional { get; init; }

        /// <summary>当前是否加载（可在设置页切换，写回 Python 的 mcp_servers.yaml）。</summary>
        public bool Enabled { get; set; }

        /// <summary>引用该 Server 的子 Agent 名称。</summary>
        public IReadOnlyList<string> UsedBySubagents { get; init; } = Array.Empty<string>();

        public string OptionalText => Optional ? "可选" : "必需";

        public string EnabledText => Enabled ? "已启用" : "未启用";

        public string UsedByText => UsedBySubagents.Count == 0 ? "无" : string.Join(", ", UsedBySubagents);
    }

    public sealed class ContextFormatRecord
    {
        public string Extension { get; init; } = "";
        public string Kind { get; init; } = "";
        public string MimeType { get; init; } = "";
    }

    public sealed class ContextFormatsSnapshot
    {
        public IReadOnlyList<ContextFormatRecord> Formats { get; init; } = Array.Empty<ContextFormatRecord>();
        public int MaxFileMb { get; init; }
        public int MaxFiles { get; init; }
    }
}
