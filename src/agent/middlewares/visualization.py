"""可视化输出协议中间件。

给主 Agent 注入一份「可被前端渲染」的输出约定：

- ``echarts`` 代码块：内容为 ECharts option JSON，前端渲染为可交互图表；
- ``mermaid`` 代码块：流程图 / 时序图 / 关系图，前端渲染为 SVG；
- Markdown 图片：本地图片用会话内相对路径，前端通过会话文件接口读取。

中间件只追加提示，不改写模型输出，也不引入额外模型调用。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage


VISUALIZATION_PROTOCOL = """
## 可视化输出协议（前端会直接渲染）

当用户需要图表、图示或图片时，不要只给文字描述，按下面格式输出：

1. 数据图表：使用 ```echarts 代码块，内容必须是合法 JSON 的 ECharts option。
   示例：
   ```echarts
   {"title":{"text":"月度趋势"},"tooltip":{"trigger":"axis"},"xAxis":{"type":"category","data":["1月","2月"]},"yAxis":{"type":"value"},"series":[{"type":"line","data":[12,18]}]}
   ```
   - 只使用真实数据；数据来自工具结果、用户提供或明确的假设，并在正文说明来源。
   - 需要多图时输出多个 echarts 代码块，每个块一个完整 option。

2. 流程图 / 时序图 / 关系图：使用 ```mermaid 代码块。
   - 优先使用兼容性最好的 `flowchart TD`、`sequenceDiagram`、`graph TD`。
   - 不要依赖需要较新渲染器的实验语法。

3. 图片：使用 Markdown 图片语法。
   - 沙箱内文件：`![说明](sandbox:/相对路径.png)`
   - 项目内文件：`![说明](project:/相对路径.png)`
   - 外部图片可直接使用 http(s) 链接。
   - 不要编造不存在的图片路径。

4. 图表和图示用于帮助理解，正文仍要给出结论与关键依据。
""".strip()


class VisualizationMiddleware(AgentMiddleware):
    """把可视化输出协议追加到系统提示词。"""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        system_message = request.system_message or SystemMessage(content="")
        content = system_message.content
        if isinstance(content, str):
            if VISUALIZATION_PROTOCOL in content:
                return await handler(request)
            merged = f"{content}\n\n{VISUALIZATION_PROTOCOL}".strip()
        else:
            merged = [*content, {"type": "text", "text": VISUALIZATION_PROTOCOL}]
        return await handler(
            request.override(system_message=SystemMessage(content=merged))
        )
