namespace CommonHarnessPluginSDK.Models
{
    /// <summary>
    /// 文件实体信息
    /// </summary>
    public sealed class ThreadFileEntry
    {
        public string Path { get; init; } = "";
        public long Size { get; init; }
        public string ModifiedAt { get; init; } = "";
    }

    /// <summary>
    /// 文件内容与编码形式
    /// </summary>
    public sealed class ThreadFileContent
    {
        public string Path { get; init; } = "";
        public string Content { get; init; } = "";
        public string Encoding { get; init; } = "";
    }
}
