# RAG 模块

集成模式下 Harness 只做 **Provider 注册、检索编排、引用归一化与灌入转发**，
不保存知识原文、切片、向量与 Embedding。

完整设计见：[RAG 模块项目设计](../../docs/core_design/RAG模块设计.md)

## 文件

| 文件 | 职责 |
| :-- | :-- |
| `types.py` | Provider 配置、运行状态、错误、MCP 默认工具名 |
| `config_store.py` | `providers.json` 原子读写、校验、密钥掩码、鉴权解析 |
| `runtime.py` | 注册、健康检查、检索、灌入/知识管理转发、MCP 发现 |
| `../api/rag.py` | `/rag/*` REST API |
| `../agent/tools/rag_tools.py` | Agent 的 `knowledge_search` 工具 |

## 支持的接入方式

1. **HTTP/OpenAPI Provider**：`endpoint` 指向 RAG 服务的 API base。
2. **MCP Provider**：复用已接入的 MCP 服务，通过 `tools` 映射调用检索/灌入工具。
3. **MCP 发现**：MCP 后端暴露 `rag_manifest` 工具，Harness 调用后自动注册 Provider。

## 配置与密钥

- 源码模式：`rag/providers.json`
- 打包模式：`USER_DATA_DIR/rag/providers.json`
- 覆盖：`HARNESS_RAG_ROOT` / `HARNESS_RAG_CONFIG_PATH`
- 密钥只保存 `secret_ref`（环境变量名或 `${VAR}`），运行时解析，前端不回显明文。
