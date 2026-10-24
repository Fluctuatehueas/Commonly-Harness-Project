# 端到端验证：模型写文件 → 文件API读取 → 编辑回写
import json
import urllib.request

BASE = "http://127.0.0.1:8012"


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def put(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


result = post("/chat", {
    "message": "请把文字「测试文件」写入 /t.md 文件，然后一句话确认",
    "user_id": "e2e_user",
    "username": "e2e",
})
tid = result["thread_id"]
print("THREAD", tid)
print("ANSWER", (result.get("answer") or "")[:80])

listing = get(f"/threads/{tid}/files?user_id=e2e_user")
print("LIST", json.dumps(listing, ensure_ascii=False)[:300])

paths = [f["path"] for f in listing["files"]]
target = next((p for p in paths if p.endswith("t.md")), None)
assert target, f"未找到 t.md: {paths}"

content = get(f"/threads/{tid}/files/content?user_id=e2e_user&path={urllib.parse.quote(target)}")
print("READ", repr(content["content"])[:120])
assert "测试文件" in content["content"]

saved = put(f"/threads/{tid}/files/content?user_id=e2e_user", {"path": target, "content": "edited by console\nsecond line"})
print("PUT", saved)

again = get(f"/threads/{tid}/files/content?user_id=e2e_user&path={urllib.parse.quote(target)}")
print("REREAD", repr(again["content"])[:120])
assert "edited by console" in again["content"]

thinking = get("/settings/model")
print("SETTINGS", thinking)
assert "thinking_enabled" in thinking

print("E2E_ALL_PASS")
