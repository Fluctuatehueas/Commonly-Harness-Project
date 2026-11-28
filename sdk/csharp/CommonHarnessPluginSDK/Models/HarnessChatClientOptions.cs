using CommonHarnessPluginSDK.Utils;
using System;
using System.Collections.Generic;
using System.Text;

namespace CommonHarnessPluginSDK.Models
{
    /// <summary>
    /// ChatService 客户端参数。
    /// </summary>
    public sealed class HarnessChatClientOptions
    {
        /// <summary>gRPC 地址（h2c）。默认与 Python chat_server.py 一致。</summary>
        public string Address { get; set; } = ConstantVariable.GRPC_URI;

        /// <summary>会话 ID；留空时自动生成。</summary>
        public string ThreadId { get; set; } = "";

        /// <summary>用户隔离键（Python 侧按 user_id 存会话）。</summary>
        public string UserId { get; set; } = ConstantVariable.Default_UserID;

        /// <summary>展示名。</summary>
        public string Username { get; set; } = "WPF Editor";

        /// <summary>单次流式调用的最长时长（分钟）。</summary>
        public double DeadlineMinutes { get; set; } = 10;

        /// <summary>探测用的主机名（从 Address 解析，仅用于 TcpClient 探测）。</summary>
        public string Host => Uri.TryCreate(Address, UriKind.Absolute, out Uri? uri) ? uri.Host : "127.0.0.1";

        /// <summary>探测用的端口（从 Address 解析）。</summary>
        public int Port => Uri.TryCreate(Address, UriKind.Absolute, out Uri? uri) ? uri.Port : 50051;
    }
}
