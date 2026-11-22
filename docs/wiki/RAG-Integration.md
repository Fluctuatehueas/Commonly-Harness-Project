# RAG Integration

Harness does not store knowledge, chunks, embeddings, or vectors. It provides a provider registry, a unified retrieval call, and citation normalization. The customer's RAG backend owns the knowledge.

Code:

| File | Responsibility |
| :-- | :-- |
| `src/rag/types.py` | Provider, chunk, citation models |
| `src/rag/config_store.py` | Provider configuration |
| `src/rag/runtime.py` | Retrieval, discovery, health, ingestion proxying |
| `src/agent/tools/rag_tools.py` | Agent-facing RAG tools |
| `src/api/rag.py` | Management and retrieval API |

## 1. Provider configuration

Per-user providers live in `<USER_DATA_DIR>/users/<user_id>/rag/providers.json`. The repository-level `rag/providers.json` is the development default.

A provider record includes:

| Field | Meaning |
| :-- | :-- |
| `provider_id` | Stable identifier |
| `name` | Display name |
| `protocol` | `mcp` or `http` |
| `endpoint` / server reference | Where to call |
| `capabilities` | `retrieve`, `ingest`, `health`, and so on |
| `knowledge_domains` | Domain tags |
| `citation_format` | Citation granularity, typically `chunk` |
| `limits` | `top_k_max`, `max_chars_per_chunk`, `qps` |
| Enabled flag | Whether the provider participates |

## 2. Two integration protocols

| Protocol | How | When |
| :-- | :-- | :-- |
| MCP | Expose `rag_manifest` and `rag_retrieve` on an MCP server | You already have an MCP Server |
| HTTP | Provide `/retrieve` and optional `/knowledge/*` endpoints | You have a standalone retrieval service |

The payloads are identical regardless of transport, so a provider can migrate without changing the agent contract.

## 3. Manifest

`rag_manifest` is used for discovery. It must be returned as JSON text inside `content[].text`:

```json
{
  "provider_id": "rag_crm",
  "name": "CRM Knowledge Base",
  "protocol": "mcp",
  "capabilities": ["retrieve", "ingest", "health"],
  "knowledge_domains": ["contract", "product"],
  "citation_format": "chunk",
  "limits": {"top_k_max": 20, "max_chars_per_chunk": 2000, "qps": 10}
}
```

## 4. Retrieval

Input should carry:

- Query text
- `top_k`
- `user_scope` (tenant, user, roles)
- Optional domain filters

Output:

```json
{
  "chunks": [
    {
      "citation_id": "c1",
      "content": "Contract renewal must be initiated 30 days before expiry...",
      "score": 0.82,
      "source": {
        "title": "Contract Management Policy",
        "uri": "kb://contract/001",
        "page": 3
      }
    }
  ],
  "provider_id": "rag_crm"
}
```

Rules:

1. Every chunk has a stable `citation_id`.
2. `source` must allow a human to trace the statement to a document and page or section.
3. Return `chunks: []` for no results; do not raise an error.
4. Keep each `content` under the provider limit, truncate with `truncated: true` if needed.
5. `user_scope` must be passed through to retrieval. Permission filtering happens in the RAG backend, not in the model.

## 5. Agent-facing tools

`create_rag_tools()` registers tools that are always available:

- Discover providers
- Retrieve from a provider
- Return citation objects

With no provider configured, they return an empty result rather than blocking the conversation. This keeps RAG optional.

The system prompt should encourage the model to cite sources and to say "not found" rather than fabricate when retrieval returns nothing.

## 6. API

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/rag/config` | Configuration view |
| `GET` | `/rag/providers` | List providers |
| `GET` | `/rag/providers/{provider_id}` | Provider detail |
| `POST` | `/rag/providers` | Create provider |
| `PUT` | `/rag/providers/{provider_id}` | Update provider |
| `DELETE` | `/rag/providers/{provider_id}` | Delete provider |
| `POST` | `/rag/providers/{provider_id}/enable` | Enable or disable |
| `POST` | `/rag/providers/{provider_id}/probe` | Health probe |
| `POST` | `/rag/retrieve` | Unified retrieval |
| `POST` | `/rag/providers/{provider_id}/ingest` | Proxy ingestion |
| `GET` | `/rag/providers/{provider_id}/knowledge/documents` | List documents |
| `DELETE` | `/rag/providers/{provider_id}/knowledge/documents/{document_id}` | Delete document |
| `GET` | `/rag/providers/{provider_id}/knowledge/stats` | Knowledge stats |
| `POST` | `/rag/discover` | Discover providers |

Ingestion is a proxy. Harness does not parse and embed documents itself; it forwards the request to the provider when the provider advertises `ingest`.

## 7. Citations and answer quality

A RAG answer is not "better" because it is longer. Check:

| Signal | Expected |
| :-- | :-- |
| Citation presence | Every factual claim that came from knowledge has a citation |
| Traceability | Citation resolves to a real document and location |
| Empty-result behavior | The model says it could not find the answer |
| Scope correctness | The user cannot retrieve chunks outside their permissions |
| Freshness | The provider returns current documents, not stale indexes |

If answers cite plausible-sounding but nonexistent documents, the problem is usually on the provider side or in the tool contract, not in the model.

## 8. What not to do

- Do not store knowledge originals in the Harness workspace as a shadow index.
- Do not let the model perform permission filtering by prompt instruction.
- Do not return an error for a legitimate empty result.
- Do not concatenate all chunks into the prompt and hope for the best; rank, trim, and cite.
- Do not treat RAG as a substitute for business tools when the answer is a transaction.

## Next

- [[Tools-and-MCP]] for the MCP contract
- [[Integration-Mode]] for customer-side responsibilities
- [[Verification-and-Evolution]] for answer evaluation
