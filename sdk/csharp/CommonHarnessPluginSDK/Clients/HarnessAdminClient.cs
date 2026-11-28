﻿using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CommonHarnessPluginSDK.Models;
using Grpc.Net.Client;
using Proto = Agent.Proto;

namespace CommonHarnessPluginSDK.Clients
{
    /// <summary>
    /// 【Q1 作用】HarnessService 客户端：能力矩阵、模型配置读写、技能、子 Agent、MCP、上下文格式。
    /// <para>这些是 Agent 运行时自身的元信息，业务能力仍由 C# ToolService 提供。</para>
    /// 【Q2 调用方】WPF 设置页（Harness 页）与会话状态栏。
    /// 【Q3 创建时机】应用启动时。
    /// 【Q4 释放】自行创建的 channel 由 Dispose 释放。
    /// 【Q5 线程约束】可被任意线程 await。
    /// </summary>
    public sealed class HarnessAdminClient : IDisposable
    {
        private readonly Proto.HarnessService.HarnessServiceClient _client;
        private readonly GrpcChannel? _ownedChannel;

        public HarnessAdminClient(GrpcChannel channel)
        {
            _client = new Proto.HarnessService.HarnessServiceClient(channel);
        }

        public HarnessAdminClient(string address)
        {
            _ownedChannel = GrpcChannel.ForAddress(address);
            _client = new Proto.HarnessService.HarnessServiceClient(_ownedChannel);
        }

        /// <summary>
        /// 获取能力信息
        /// </summary>
        /// <param name="userId"></param>
        /// <param name="cancellationToken"></param>
        /// <returns></returns>
        public async Task<CapabilitiesSnapshot> GetCapabilitiesAsync(
            string userId,
            CancellationToken cancellationToken = default)
        {
            Proto.GetCapabilitiesResponse response = await _client.GetCapabilitiesAsync(
                new Proto.GetCapabilitiesRequest { UserId = userId ?? "" },
                cancellationToken: cancellationToken).ConfigureAwait(false);

            return new CapabilitiesSnapshot
            {
                ContractVersion = response.ContractVersion,
                ModelName = response.ModelName,
                ModelThinkingEnabled = response.ModelThinkingEnabled,
                SandboxEnabled = response.SandboxEnabled,
                PythonToolCount = response.PythonToolCount,
                SkillCount = response.SkillCount,
                SubagentCount = response.SubagentCount,
                McpServerCount = response.McpServerCount,
                SessionCount = response.SessionCount,
                Capabilities = response.Capabilities
                    .Select(item => new CapabilityRecord
                    {
                        Name = item.Name,
                        Enabled = item.Enabled,
                        Detail = item.Detail,
                    })
                    .ToList(),
            };
        }


        /// <summary>
        /// 获取模型配置
        /// </summary>
        /// <param name="cancellationToken"></param>
        /// <returns></returns>
        public async Task<ModelSettingsSnapshot> GetModelSettingsAsync(
            CancellationToken cancellationToken = default)
        {
            Proto.ModelSettings response = await _client.GetModelSettingsAsync(
                new Proto.GetModelSettingsRequest(),
                cancellationToken: cancellationToken).ConfigureAwait(false);
            return ToModelSettings(response);
        }

        /// <summary>
        /// 更新模型配置：只传需要修改的字段（null 表示保持不变；apiKey 空字符串表示清空）。
        /// </summary>
        public async Task<ModelSettingsSnapshot> UpdateModelSettingsAsync(
            string? modelName,
            string? baseUrl,
            string? apiKey,
            bool? thinkingEnabled,
            CancellationToken cancellationToken = default)
        {
            Proto.UpdateModelSettingsRequest request = new Proto.UpdateModelSettingsRequest();
            if (modelName is not null)
            {
                request.ModelName = modelName;
            }

            if (baseUrl is not null)
            {
                request.BaseUrl = baseUrl;
            }

            if (apiKey is not null)
            {
                request.ApiKey = apiKey;
            }

            if (thinkingEnabled is not null)
            {
                request.ThinkingEnabled = thinkingEnabled.Value;
            }

            Proto.ModelSettings response = await _client.UpdateModelSettingsAsync(
                request,
                cancellationToken: cancellationToken).ConfigureAwait(false);
            return ToModelSettings(response);
        }


        /// <summary>
        /// 列出技能列表
        /// </summary>
        /// <param name="cancellationToken"></param>
        /// <returns>技能记录</returns>
        public async Task<IReadOnlyList<SkillRecord>> ListSkillsAsync(
            CancellationToken cancellationToken = default)
        {
            Proto.ListSkillsResponse response = await _client.ListSkillsAsync(
                new Proto.ListSkillsRequest(),
                cancellationToken: cancellationToken).ConfigureAwait(false);
            return response.Skills.Select(ToSkill).ToList();
        }


        /// <summary>
        /// 获取技能详情
        /// </summary>
        /// <param name="name"></param>
        /// <param name="cancellationToken"></param>
        /// <returns></returns>
        public async Task<SkillDetailRecord> GetSkillAsync(
            string name,
            CancellationToken cancellationToken = default)
        {
            Proto.GetSkillResponse response = await _client.GetSkillAsync(
                new Proto.GetSkillRequest { Name = name ?? "" },
                cancellationToken: cancellationToken).ConfigureAwait(false);
            return new SkillDetailRecord
            {
                Skill = ToSkill(response.Skill),
                Content = response.Content,
            };
        }

        /// <summary>
        /// 列出子Agent信息
        /// </summary>
        /// <param name="cancellationToken"></param>
        /// <returns></returns>
        public async Task<IReadOnlyList<SubagentRecord>> ListSubagentsAsync(
            CancellationToken cancellationToken = default)
        {
            Proto.ListSubagentsResponse response = await _client.ListSubagentsAsync(
                new Proto.ListSubagentsRequest(),
                cancellationToken: cancellationToken).ConfigureAwait(false);
            return response.Subagents
                .Select(item => new SubagentRecord
                {
                    Name = item.Name,
                    Description = item.Description,
                    Path = item.Path,
                    ConfigJson = item.ConfigJson,
                    Tools = item.Tools.ToList(),
                    OptionalTools = item.OptionalTools.ToList(),
                    McpServers = item.McpServers.ToList(),
                    Skills = item.Skills.ToList(),
                    Model = item.Model,
                })
                .ToList();
        }

        /// <summary>
        /// 列出MCP信息
        /// </summary>
        /// <param name="cancellationToken"></param>
        /// <returns></returns>
        public async Task<IReadOnlyList<McpServerRecord>> ListMcpServersAsync(
            CancellationToken cancellationToken = default)
        {
            Proto.ListMcpServersResponse response = await _client.ListMcpServersAsync(
                new Proto.ListMcpServersRequest(),
                cancellationToken: cancellationToken).ConfigureAwait(false);
            return response.Servers
                .Select(item => new McpServerRecord
                {
                    Name = item.Name,
                    Transport = item.Transport,
                    Description = item.Description,
                    ConfigJson = item.ConfigJson,
                    Optional = item.Optional,
                    Enabled = item.Enabled,
                    UsedBySubagents = item.UsedBySubagents.ToList(),
                })
                .ToList();
        }

        /// <summary>
        /// 列出上下文格式信息
        /// </summary>
        /// <param name="cancellationToken"></param>
        /// <returns></returns>
        public async Task<ContextFormatsSnapshot> ListContextFormatsAsync(
            CancellationToken cancellationToken = default)
        {
            Proto.ListContextFormatsResponse response = await _client.ListContextFormatsAsync(
                new Proto.ListContextFormatsRequest(),
                cancellationToken: cancellationToken).ConfigureAwait(false);
            return new ContextFormatsSnapshot
            {
                MaxFileMb = response.MaxFileMb,
                MaxFiles = response.MaxFiles,
                Formats = response.Formats
                    .Select(item => new ContextFormatRecord
                    {
                        Extension = item.Extension,
                        Kind = item.Kind,
                        MimeType = item.MimeType,
                    })
                    .ToList(),
            };
        }
       
        private static ModelSettingsSnapshot ToModelSettings(Proto.ModelSettings settings)
        {
            return new ModelSettingsSnapshot
            {
                ModelName = settings.ModelName,
                BaseUrl = settings.BaseUrl,
                HasApiKey = settings.HasApiKey,
                ThinkingEnabled = settings.ThinkingEnabled,
            };
        }

        private static SkillRecord ToSkill(Proto.SkillSummary skill)
        {
            return new SkillRecord
            {
                Name = skill.Name,
                DisplayName = skill.DisplayName,
                Description = skill.Description,
                Path = skill.Path,
                MetadataJson = skill.MetadataJson,
            };
        }

        public void Dispose()
        {
            _ownedChannel?.Dispose();
        }
    }
}
