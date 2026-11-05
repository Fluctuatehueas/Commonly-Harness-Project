"""对比两份上下文压缩基线结果。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    args = parser.parse_args()
    base = load(args.baseline)
    cand = load(args.candidate)

    print(f"{'task':16s} {'baseline':>10s} {'candidate':>10s} {'compact':>8s} {'saved':>10s}")
    cand_by_id = {item["task_id"]: item for item in cand["results"]}
    regression = 0
    for item in base["results"]:
        other = cand_by_id.get(item["task_id"], {})
        base_ok = "PASS" if item["passed"] else "FAIL"
        cand_ok = "PASS" if other.get("passed") else "FAIL"
        if item["passed"] and not other.get("passed"):
            regression += 1
        print(
            f"{item['task_id']:16s} {base_ok:>10s} {cand_ok:>10s} "
            f"{other.get('compaction_count', 0):>8d} {other.get('tokens_saved', 0):>10d}"
        )
    print()
    print(f"baseline passed: {base['summary']['passed']}/{base['summary']['tasks']}")
    print(f"candidate passed: {cand['summary']['passed']}/{cand['summary']['tasks']}")
    print(f"regressions: {regression}")
    return 1 if regression else 0


if __name__ == "__main__":
    raise SystemExit(main())
