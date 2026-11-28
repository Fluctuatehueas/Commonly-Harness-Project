using System.Text;

namespace CommonHarnessPluginSDK.Models
{
    /// <summary>
    /// 按 <c>call_id</c> 累积工具参数增量片段.
    /// UI 层消费 <see cref="HarnessChatEventKind.ToolCall"/> 时逐片段 Append，
    ///           收到 <see cref="HarnessChatEventKind.ToolResult"/> 后 Release。
    /// 每次发起一轮聊天流时创建。工具结果到达后按 call_id 移除对应缓冲；整轮结束时丢弃实例
    /// </summary>
    public sealed class ToolCallAccumulator
    {
        private readonly Dictionary<string, StringBuilder> _buffers =
            new Dictionary<string, StringBuilder>(StringComparer.Ordinal);

        /// <summary>
        /// 【Q1 作用】追加一个参数片段，返回当前已累积的完整文本。
        /// 【Q2 调用方】UI 消费 ToolCall 事件时。
        /// 【Q3 调用时机】每个 <c>is_partial=true</c> 的工具调用事件。
        /// </summary>
        ///
        /// <param name="callId">
        /// 累积键。Python 侧保证同一次工具调用的 <c>call_id</c> 在整条流中稳定；
        /// 为空时退化为按空串累积（仅用于极端兜底，正常情况下不会发生）。
        /// </param>
        /// <param name="fragment">本次片段文本；首个片段可能只有 <c>{</c> 或空串，属于正常现象。</param>
        ///
        /// <returns>该 call_id 当前已累积的全部参数文本（未闭合的 JSON 也是合法中间态）。</returns>
        public string Append(string callId, string fragment)
        {
            string key = callId ?? "";
            if (!_buffers.TryGetValue(key, out StringBuilder? buffer))
            {
                buffer = new StringBuilder();
                _buffers[key] = buffer;
            }

            buffer.Append(fragment ?? "");
            return buffer.ToString();
        }

        /// <summary>工具结果到达后释放缓存。</summary>
        ///
        /// <param name="callId">要释放的累积键。</param>
        public void Release(string callId)
        {
            _buffers.Remove(callId ?? "");
        }

        /// <summary>整轮结束时清空全部缓冲（取消 / 异常收尾用）。</summary>
        public void Clear()
        {
            _buffers.Clear();
        }
    }
}
