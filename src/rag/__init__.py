"""RAG Provider 适配层。

Harness 只做 Provider 注册、检索编排、引用归一化与灌入转发，
不保存知识原文、切片与向量。
"""

from rag.runtime import rag_runtime

__all__ = ["rag_runtime"]
