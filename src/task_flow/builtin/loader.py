"""内置流加载器：读 builtin/*.json → 同一套校验编译管线 → 注册到存储。

- 校验失败记 warning 并跳过该文件，不阻断启动。
- **未被用户改动过的内置流随版本刷新**：仍归属 ``__builtin__`` 且 hash 变化时覆盖，
  这样升级仓库里的 JSON 能真正生效；用户编辑过的流（归属已转为用户）不受影响。
- **被用户删除的内置流不会复活**：删除时写入 ``task_flow_deleted`` 墓碑，加载时跳过。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import compile_flow
from ..models import FlowValidationError, TaskFlow
from ..store import BUILTIN_USER_ID, TaskFlowStore

logger = logging.getLogger(__name__)

BUILTIN_DIR = Path(__file__).resolve().parent


async def load_builtin_flows(store: TaskFlowStore) -> list[str]:
    """加载全部内置流，返回成功注册的 flow_id 列表。"""
    registered: list[str] = []
    for path in sorted(BUILTIN_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            payload["source"] = "builtin"
            payload["status"] = "published"
            flow, plan, search_doc = compile_flow(payload)
            # 用户删过就不复活；这是"内置流与用户流同等可删"的保证。
            if await store.is_builtin_deleted(flow.id):
                logger.info("内置任务流已被用户删除，跳过注册：%s", flow.id)
                continue
            existing = await store.get_flow(flow.id)
            if existing is not None:
                untouched = (
                    existing.get("user_id") == BUILTIN_USER_ID
                    and existing.get("source") == "builtin"
                )
                unchanged = existing.get("schema_hash") == plan.schema_hash
                # 用户编辑过的流归属已转为用户，必须原样保留。
                if not untouched or unchanged:
                    registered.append(flow.id)
                    continue
                logger.info("内置任务流内容有更新，覆盖为仓库版本：%s", flow.id)
            await store.upsert_flow(flow, BUILTIN_USER_ID, plan, search_doc)
            registered.append(flow.id)
            logger.info("内置任务流已注册：%s（%s）", flow.name, flow.id)
        except FlowValidationError as exc:
            logger.warning("内置任务流校验失败，已跳过 %s：\n%s", path.name, exc)
        except Exception:
            logger.exception("内置任务流加载失败，已跳过 %s", path.name)
    return registered
