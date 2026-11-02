# RAG模块

将分为两种RAG模式
1. 传统RAG
2. GraphRAG

---

|维度|更适合传统 RAG|更适合 GraphRAG|
|---|---|---|
|问题类型|单跳事实问答、FAQ、片段解释|	多跳关系推理、跨文档归纳、全局摘要（“主要主题”“主要风险”）|
|语料特征|	实体稀疏、叙事性强、文档间关联弱|	实体密集、跨文档关联强（法务、投研、医药、供应链）|
|更新频率|	实时/高频更新|	批量低频更新（周/月级）|
|规模|	小语料（<50文档）或极致成本敏感|	中大规模语料（千级文档以上），全局问题有价值|
|核心约束|	需要毫秒级响应、快速上线|	需要答案的全面性与可追溯性，能接受更高延迟与构建成本|


---
## 一、传统RAG
> 文档切块 → 向量化 → 语义相似度检索 Top-K 片段 → 拼接生成  

**RAG搭建**  

|组件|	常见选项|	适用场景|
|---|---|---|
|编排框架|	LlamaIndex / LangChain|	纯RAG优先LlamaIndex（默认配置更完善、代码量更少）；若RAG只是更大Agent工作流的一部分，LangChain更灵活|
|向量数据库|	ChromaDB / Milvus / Qdrant / pgvector|	开发验证用ChromaDB；生产按规模与运维偏好选择Milvus（大规模）、Qdrant（轻量高性能）或已有PG生态下的pgvector|
|Embedding|	BGE系列 / Qwen3 Embedding / OpenAI text-embedding-3|	中文优先BGE；需本地化或尺寸灵活选Qwen3；追求快速上线选OpenAI API|
|重排序|	BGE-Reranker / Cohere Rerank / 各类Cross-Encoder|	对检索精度有要求的场景，作为向量召回后的第二道筛选|
|生成模型|	DeepSeek / Qwen / 闭源API|	根据数据合规要求、成本与效果综合选择|


## 二、