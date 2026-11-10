"""内置流加载器：读 builtin/*.json → 同一套校验编译管线 → 注册到存储。

- 校验失败记 warning 并跳过该文件，不阻断启动。
- 已存在且 schema_hash 未变的流跳过写入；变化的覆盖更新（随系统版本发布）。
- 内置流 user_id 固定为 __builtin__，source=builtin，status=published，只读。
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
            existing = await store.get_flow(flow.id)
            # 只有「内容未变 + 归属与标记都正确」才跳过；
            # 否则重写一行（例如历史遗留把内置流误写成 user 的脏数据）
            if (
                existing
                and existing.get("schema_hash") == plan.schema_hash
                and existing.get("source") == "builtin"
                and existing.get("user_id") == BUILTIN_USER_ID
            ):
                registered.append(flow.id)
                continue
            await store.upsert_flow(flow, BUILTIN_USER_ID, plan, search_doc)
            registered.append(flow.id)
            logger.info("内置任务流已注册：%s（%s）", flow.name, flow.id)
        except FlowValidationError as exc:
            logger.warning("内置任务流校验失败，已跳过 %s：\n%s", path.name, exc)
        except Exception:
            logger.exception("内置任务流加载失败，已跳过 %s", path.name)
    return registered
