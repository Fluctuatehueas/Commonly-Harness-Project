"""上下文压缩基线回放脚本（骨架，可直接运行）。

用法：
    python test/context_eval/run_eval.py --list
    python test/context_eval/run_eval.py --mode off --execute
    python test/context_eval/run_eval.py --mode on  --execute
    python test/context_eval/compare.py results/off.json results/on.json

说明：
- 每个任务先上传一个按 seed_lines 生成的长文本附件，再按 turns 逐轮对话；
- 记录 compaction 事件、最终回答、会话 token 与上下文占用；
- 判定口径：最终回答必须包含 expect_all，且至少包含一个 expect_any；
- 真正执行会产生模型调用费用，必须显式加 ``--execute``。
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]
TASKS_FILE = BASE_DIR / "tasks.json"
TMP_DIR = PROJECT_ROOT / "useless" / "context_eval_tmp"
RESULTS_DIR = BASE_DIR / "results"


def load_tasks() -> list[dict[str, Any]]:
    return json.loads(TASKS_FILE.read_text(encoding="utf-8"))


def make_seed(task: dict[str, Any]) -> Path:
    """生成用于撑大上下文的伪日志文本（不包含真实业务数据）。"""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_DIR / f"{task['id']}.txt"
    lines: list[str] = []
    total = int(task.get("seed_lines", 200))
    for index in range(total):
        code = "ERROR" if index % 7 == 0 else ("WARN" if index % 11 == 0 else "INFO")
        lines.append(
            f"2026-11-05T10:{index % 60:02d}:00Z {code} "
            f"stage=segment-{index // 50 + 1} path=/workspace/logs/{task['id']}.log "
            f"code=E{400 + index % 40} msg=event-{index} detail=..."
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def http_json(url: str, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def upload_file(base_url: str, user_id: str, path: Path) -> str:
    boundary = f"----harness{uuid.uuid4().hex}"
    payload = path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/context/upload?user_id={user_id}",
        data=body,
        method="POST",
    )
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(request, timeout=60) as response:
        return str(json.loads(response.read().decode("utf-8"))["id"])


def send_turn(
    base_url: str,
    user_id: str,
    thread_id: str,
    message: str,
    attachments: list[str] | None = None,
) -> dict[str, Any]:
    """发送一轮对话并消费 SSE，返回最终回答与压缩指标。"""
    body = json.dumps(
        {
            "message": message,
            "thread_id": thread_id,
            "user_id": user_id,
            "attachments": attachments or [],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/stream",
        data=body,
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    result: dict[str, Any] = {
        "content": "",
        "compactions": [],
        "usage": {},
        "session_usage": {},
        "error": "",
    }
    with urllib.request.urlopen(request, timeout=600) as response:
        for raw in response:
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            if event_type == "compaction_start":
                result["compactions"].append(
                    {
                        "id": event.get("compaction_id"),
                        "trigger": event.get("trigger_type"),
                        "used": event.get("used_tokens"),
                        "limit": event.get("limit"),
                    }
                )
            elif event_type == "compaction_end":
                for item in result["compactions"]:
                    if item.get("id") == event.get("compaction_id"):
                        item.update(
                            {
                                "after": event.get("after_tokens"),
                                "saved": event.get("saved_tokens"),
                                "degraded": event.get("degraded"),
                            }
                        )
            elif event_type == "done":
                result["content"] = str(event.get("content") or "")
                result["usage"] = event.get("usage") or {}
                result["session_usage"] = event.get("session_usage") or {}
            elif event_type == "error":
                result["error"] = str(event.get("message") or "")
    return result


def judge(task: dict[str, Any], content: str) -> bool:
    if not content:
        return False
    if not all(word in content for word in task.get("expect_all", [])):
        return False
    any_words = task.get("expect_any", [])
    return any(word in content for word in any_words) if any_words else True


def run_fresh_session(base_url: str, user_id: str) -> str:
    session = http_json(
        f"{base_url}/history?user_id={user_id}",
        method="POST",
    )
    return str(session["thread_id"])


def execute_task(base_url: str, user_id: str, task: dict[str, Any]) -> dict[str, Any]:
    seed = make_seed(task)
    file_id = upload_file(base_url, user_id, seed)
    thread_id = run_fresh_session(base_url, user_id)
    turns: list[dict[str, Any]] = []
    compactions: list[dict[str, Any]] = []
    peak_context = 0
    started = time.monotonic()
    for index, message in enumerate(task["turns"]):
        turn = send_turn(
            base_url,
            user_id,
            thread_id,
            message,
            attachments=[file_id] if index == 0 else [],
        )
        compactions.extend(turn["compactions"])
        session_usage = turn.get("session_usage") or {}
        peak_context = max(peak_context, int(session_usage.get("context_used") or 0))
        turns.append(turn)
        if turn.get("error"):
            break
    content = turns[-1]["content"] if turns else ""
    return {
        "task_id": task["id"],
        "category": task.get("category"),
        "thread_id": thread_id,
        "passed": judge(task, content),
        "turns": len(turns),
        "compactions": compactions,
        "compaction_count": len(compactions),
        "tokens_saved": sum(int(item.get("saved") or 0) for item in compactions),
        "peak_context": peak_context,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "final_chars": len(content),
        "error": next((turn.get("error") for turn in turns if turn.get("error")), ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="上下文压缩基线回放")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--user-id", default="console_user")
    parser.add_argument("--mode", default="on", help="结果文件标签，如 off / on / tuned")
    parser.add_argument("--task-id", default="", help="只跑指定任务")
    parser.add_argument("--execute", action="store_true", help="真正执行（会消耗模型额度）")
    parser.add_argument("--list", action="store_true", help="只列出任务")
    args = parser.parse_args()

    tasks = load_tasks()
    if args.task_id:
        tasks = [task for task in tasks if task["id"] == args.task_id]
    if args.list:
        for task in tasks:
            print(f"{task['id']:16s} {task['category']:18s} {task['title']}")
        return 0
    if not args.execute:
        print(f"共 {len(tasks)} 条任务。确认后加 --execute 真正回放（会消耗模型额度）。")
        return 0

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for task in tasks:
        print(f"[{task['id']}] running ...")
        results.append(execute_task(args.base_url, args.user_id, task))
        print(f"[{task['id']}] passed={results[-1]['passed']} compactions={results[-1]['compaction_count']}")

    output = RESULTS_DIR / f"{args.mode}.json"
    output.write_text(
        json.dumps(
            {
                "mode": args.mode,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "summary": {
                    "tasks": len(results),
                    "passed": sum(1 for item in results if item["passed"]),
                    "compactions": sum(item["compaction_count"] for item in results),
                    "tokens_saved": sum(item["tokens_saved"] for item in results),
                },
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"结果写入 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
