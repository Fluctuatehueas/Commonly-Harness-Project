"""
由2026/10/28 Deepseek 官方新推出的自研搜索引擎

该模块根据官方API进行网页搜索服务封装

请求格式：

curl https://api.deepseek.com/v1/tools/search \
  -H "Authorization: Bearer <DeepSeek API Key>" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "DeepSeek V4 模型 发布",
    "count": 5,
    "need_content": true
  }'

  
字段	类型	必填	默认值	说明
query	string	是	-	搜索词，1～128 个 Unicode 字符，不能全为空白，支持下方列出的搜索语法。
count	integer	否	10	返回条数，1～50。
ori_question	string	否	-	触发搜索的用户原始问题，1～128 个字符，有助于更好地理解搜索意图，传 null 视为未提供。
localization	string	否	""	用于优先返回本地化搜索结果的 IP 地址。
need_content	boolean	否	false	是否在 content 中返回网页正文，为 false 时 content 恒为空字符串。

搜索语法
query 中的搜索语法会原样传递给搜索服务。下表中的示例将操作符与搜索词组合使用；你可以在一个 query 中组合多个操作符。

操作符	示例	说明
site:	DeepSeek site:github.com	将结果限制在指定站点，指定多个时用空格分隔并通过 OR 连接，例如 DeepSeek site:github.com OR site:huggingface.co。
-site:	DeepSeek -site:example.com	排除指定站点，指定多个时用空格分隔。
-	DeepSeek -广告	排除包含指定关键词的网页，指定多个时用空格分隔。
-"..."	DeepSeek -"release notes"	排除包含指定短语的网页，短语内可以包含空格，指定多个时用空格分隔。
"..."	DeepSeek "精确短语"	精确匹配指定短语，指定多个时用空格分隔。
lang:	DeepSeek lang:zh,en	将结果限制在指定语言，多种语言用逗号分隔，例如 lang:zh,en。
after:	DeepSeek after:2025 或 DeepSeek after:2025-01-01	将结果限制在指定日期之后发布的网页。
before:	DeepSeek before:2026 或 DeepSeek before:2026-06-01	将结果限制在指定日期之前发布的网页。

响应：
{
  "results": [
    {
      "rerank_score": 2.4,
      "title": "DeepSeek V4 模型发布",
      "url": "https://example.com/deepseek-v4",
      "snippet": "DeepSeek V4 已上线，支持工具调用与长上下文。",
      "summary": "DeepSeek 发布了 V4 模型。此次发布改进了工具调用与长上下文能力，适合 Agent 工作流。",
      "content": "DeepSeek V4 现已正式上线……",
      "site_name": "Example News",
      "site_icon": "https://example.com/favicon.ico",
      "date": "2026-06-01T00:00:00+00:00",
      "authority": 3
    }
  ]
}

results 中每一项包含以下字段：

字段	类型	说明
rerank_score	number	排序分数，0～3 分，综合考虑相关性、权威性、时效性等维度，大于等于 1.5 分为优质结果。
title	string	标题。
url	string	结果链接。
snippet	string	短摘要，与 query 相关，通常为 100～200 字符，适用于前端展现。
summary	string	长摘要，与 query 相关，通常为 600～800 字符，适用于给模型阅读。
content	string	网页正文，need_content 为 false 或正文缺失时为空字符串。
site_name	string 或 null	站点名称，缺失时为 null。
site_icon	string 或 null	站点图标 URL，缺失时为 null。
date	string 或 null	发布时间，统一为 UTC 的 RFC 3339 格式，未知时为 null。
authority	integer	网页权威性，0～4（-1 表示未知），大于等于 3 为极权威结果。


---

FETCH API

curl https://api.deepseek.com/v1/tools/fetch \
  -H "Authorization: Bearer <DeepSeek API Key>" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/article"
  }'
请求字段：
字段	类型	必填	说明
url	string	是	要抓取的网页地址，长度不超过 8192，必须为含 host 的 HTTP/HTTPS URL，且不能携带用户名或密码。

响应：
{
  "title": "Example article",
  "content": "# Example article\n\nArticle content."
}

错误：
{
  "error": {
    "message": "'count' must be between 1 and 50",
    "type": "invalid_request_error",
    "param": null,
    "code": "invalid_request_error"
  }
}

"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

# 兼容脚本方式运行；导入时不发起任何网络请求。
SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_SEARCH_BASE_URL,
    DEEPSEEK_SEARCH_TIMEOUT,
    DEEPSEEK_WEB_SEARCH_API_KEY,
)
from agent.schema import SearchResponse, SearchResult, SearchServiceError


_SEARCH_PATH = "/v1/tools/search"  # 搜索的路由
_FETCH_PATH= "/v1/tools/fetch" # 抓取的路由
# HTTP 状态码 → (错误类型, 默认中文说明)
HTTP_ERROR_MAP: dict[int, tuple[str, str]] = {
    400: ("invalid_request_error", "请求头或请求体不合法（JSON 格式错误或字段值无效）"),
    401: ("authentication_error",  "API Key 缺失或无效"),
    402: ("insufficient_balance",  "账号余额不足"),
    403: ("fetch_forbidden",       "Fetch 请求被安全策略拒绝"),
    404: ("fetch_unavailable",     "Fetch 未能获取可用的网页内容（URL 不可用、被拦截、站点报错、内容类型不支持或无正文）"),
    408: ("request_timeout",       "读取请求体超时"),
    413: ("payload_too_large",     "请求体超过大小限制"),
    429: ("rate_limit_exceeded",   "超过 Search 或 Fetch 的 QPS 限制"),
    500: ("internal_error",        "服务器内部错误"),
    504: ("upstream_timeout",      "下游请求超时"),
}

def describe_http_error(status: int, server_msg: str | None,
                        server_code: str | None) -> tuple[str, str]:
    """返回 (code, message)。优先用服务端 message，其次用对照表默认文案。"""
    default_code, default_msg = HTTP_ERROR_MAP.get(
        status, ("http_error", f"HTTP {status}")
    )
    return (
        str(server_code or default_code),
        str(server_msg or default_msg),
    )

# ---------- 服务 ----------
class DeepSeekSearchService:
    """DeepSeek 官方 Search/Fetch API 封装，支持同步与异步调用。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEEPSEEK_SEARCH_BASE_URL,
        timeout: float = DEEPSEEK_SEARCH_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        # 官方 Search/Fetch 与 Chat 共用同一个 DeepSeek API Key；
        # 优先使用专门的搜索 Key，没配则回退到 DEEPSEEK_API_KEY。
        self._api_key = api_key or DEEPSEEK_WEB_SEARCH_API_KEY or DEEPSEEK_API_KEY
        base = (base_url or DEEPSEEK_SEARCH_BASE_URL).rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        self._base_url = base
        self._timeout = timeout
        self._headers = {"Content-Type": "application/json"}
        if self._api_key:
            self._headers["Authorization"] = f"Bearer {self._api_key}"
        self._session = session or requests.Session()
        self._session.headers.update(self._headers)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _require_key(self) -> None:
        if not self._api_key:
            raise SearchServiceError(
                "未配置 DEEPSEEK_WEB_SEARCH_API_KEY（或 DEEPSEEK_API_KEY）",
                code="missing_api_key",
            )

    # ---- 参数校验（对应文档 1~128 字符、count 1~50）----
    @staticmethod
    def _validate(
        query: str,
        count: int,
        *,
        ori_question: str | None = None,
        localization: str = "",
    ) -> None:
        if not isinstance(query, str) or not query.strip():
            raise SearchServiceError("query 不能为空白")
        if not (1 <= len(query) <= 128):
            raise SearchServiceError("query 长度需在 1~128 个字符之间")
        if isinstance(count, bool) or not isinstance(count, int) or not (1 <= count <= 50):
            raise SearchServiceError(
                "'count' must be between 1 and 50",
                code="invalid_request_error",
            )
        if ori_question is not None and (
            not isinstance(ori_question, str) or not (1 <= len(ori_question) <= 128)
        ):
            raise SearchServiceError(
                "ori_question 长度需在 1~128 个字符之间",
                code="invalid_request_error",
            )
        if not isinstance(localization, str):
            raise SearchServiceError(
                "localization 必须是字符串", code="invalid_request_error"
            )

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        session: requests.Session | None = None,
    ) -> dict[str, Any]:
        """统一处理请求、HTTP 错误与 JSON 解析。"""
        self._require_key()
        client = session or self._session
        try:
            resp = client.post(
                f"{self._base_url}{path}",
                headers=self._headers,
                json=payload,
                timeout=self._timeout,
            )
        except requests.Timeout as exc:
            raise SearchServiceError("请求超时", code="request_timeout") from exc
        except requests.RequestException as exc:
            raise SearchServiceError(f"请求失败: {exc}") from exc

        if resp.status_code >= 400:
            error: dict[str, Any] = {}
            try:
                body = resp.json()
                if isinstance(body, dict) and isinstance(body.get("error"), dict):
                    error = body["error"]
            except ValueError:
                pass
            code, message = describe_http_error(
                resp.status_code, error.get("message"), error.get("code")
            )
            raise SearchServiceError(
                message, code=code, status=resp.status_code
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise SearchServiceError(
                "搜索服务返回非 JSON 响应",
                code="invalid_response",
                status=resp.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise SearchServiceError(
                "搜索服务返回结构异常",
                code="invalid_response",
                status=resp.status_code,
            )
        return data

    def search(
        self,
        query: str,
        count: int = 10,
        *,
        ori_question: str | None = None,
        localization: str = "",
        need_content: bool = False,
    ) -> SearchResponse:
        """执行一次搜索。失败抛 SearchServiceError。"""
        return self._search(
            query,
            count,
            ori_question=ori_question,
            localization=localization,
            need_content=need_content,
            session=None,
        )

    def _search(
        self,
        query: str,
        count: int,
        *,
        ori_question: str | None,
        localization: str,
        need_content: bool,
        session: requests.Session | None,
    ) -> SearchResponse:
        self._validate(
            query, count, ori_question=ori_question, localization=localization
        )
        payload: dict[str, Any] = {
            "query": query,
            "count": count,
            "localization": localization,
            "need_content": need_content,
        }
        if ori_question:
            payload["ori_question"] = ori_question

        data = self._post(_SEARCH_PATH, payload, session=session)
        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            raise SearchServiceError(
                "搜索服务返回的 results 不是数组", code="invalid_response"
            )
        return SearchResponse(
            results=[
                SearchResult.from_dict(item)
                for item in raw_results
                if isinstance(item, dict)
            ],
            raw=data,
        )

    async def asearch(
        self,
        query: str,
        count: int = 10,
        *,
        ori_question: str | None = None,
        localization: str = "",
        need_content: bool = False,
    ) -> SearchResponse:
        """异步搜索：在线程池中执行，避免阻塞 Agent 事件循环。"""

        def _run() -> SearchResponse:
            with requests.Session() as session:
                return self._search(
                    query,
                    count,
                    ori_question=ori_question,
                    localization=localization,
                    need_content=need_content,
                    session=session,
                )

        return await asyncio.to_thread(_run)

    def fetch(self, url: str) -> dict[str, str]:
        """抓取网页正文。返回 {"title": str, "content": str}。
        失败抛 SearchServiceError（code 见 HTTP_ERROR_MAP）。
        """
        return self._fetch(url, session=None)

    def _fetch(
        self,
        url: str,
        *,
        session: requests.Session | None,
    ) -> dict[str, str]:
        # ---- 参数校验：文档要求 ≤8192、必须是含 host 的 http/https、不能带账号密码 ----
        if not url or not isinstance(url, str):
            raise SearchServiceError("url 不能为空", code="invalid_request_error")
        if len(url) > 8192:
            raise SearchServiceError(
                "url 长度不能超过 8192", code="invalid_request_error"
            )
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise SearchServiceError(
                "url 必须为含 host 的 HTTP/HTTPS 地址",
                code="invalid_request_error",
            )
        if parsed.username or parsed.password:
            raise SearchServiceError(
                "url 不能携带用户名或密码", code="invalid_request_error"
            )

        data = self._post(_FETCH_PATH, {"url": url}, session=session)
        return {
            "title": str(data.get("title") or ""),
            "content": str(data.get("content") or ""),
        }

    async def afetch(self, url: str) -> dict[str, str]:
        """异步抓取：在线程池中执行，避免阻塞 Agent 事件循环。"""

        def _run() -> dict[str, str]:
            with requests.Session() as session:
                return self._fetch(url, session=session)

        return await asyncio.to_thread(_run)

    def close(self) -> None:
        self._session.close()


# 延迟单例：导入时不依赖 API Key，也不会发起网络请求。
_web_service: DeepSeekSearchService | None = None


def get_web_service() -> DeepSeekSearchService:
    global _web_service
    if _web_service is None:
        _web_service = DeepSeekSearchService()
    return _web_service


def reset_web_service() -> None:
    """配置热更新或测试时重建单例。"""
    global _web_service
    _web_service = None
