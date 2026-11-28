using Microsoft.Extensions.Configuration.UserSecrets;
using System;
using System.Collections.Generic;
using System.Text;

namespace CommonHarnessPluginSDK.Utils
{
    public class ConstantVariable
    {
        public const string GRPC_URI = "http://127.0.0.1:8000"; // 后端连接服务地址
        public const string ContraceVersion = "1.3.0"; // 协议版本号


        public const string Default_UserID = "Csharp_CommonHarness_SDK"; // CommonHarness 用户隔离ID选择(可自由配置，该项为默认值)
        public const string Default_UserName = "CsharpSDK"; // 展示名字
        
    }
}
