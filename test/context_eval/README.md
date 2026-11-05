# 上下文压缩基线回放

用途：验证“开启上下文压缩”是否在长任务上造成成功率退化，并记录压缩时机、节省 token 与上下文峰值。

## 目录

| 文件 | 说明 |
| --- | --- |
| `tasks.json` | 10 条脱敏长任务，覆盖工具密集、约束密集、多轮修订三类 |
| `run_eval.py` | 回放脚本：上传长文本附件 → 逐轮发消息 → 收集 SSE 与压缩事件 |
| `compare.py` | 对比两份结果，输出成功率回归 |
| `results/` | 回放结果（运行时生成） |
| `../../useless/context_eval_tmp/` | 生成的伪日志文本（运行时生成，可随时清理，遵循项目临时产物规范） |

## 运行

先启动后端，再分别跑两种模式（模式由后端环境变量决定）：

```bash
# 关闭压缩
HARNESS_CONTEXT_COMPACT_ENABLED=false python main.py

python test/context_eval/run_eval.py --mode off --execute

# 开启压缩（默认）
python test/context_eval/run_eval.py --mode on --execute

# 对比
python test/context_eval/compare.py results/off.json results/on.json
```

无 `--execute` 时只列出任务，不产生模型调用。

## 判定口径

- `expect_all`：最终回答必须包含的全部关键词；
- `expect_any`：至少包含一个；
- 回归：baseline 通过而 candidate 未通过的条目数量。

## 注意

- 会真实消耗模型额度；
- 生成的长文本不包含真实业务数据；
- 压缩阈值改变后，旧结果不可直接复用，需要重新跑基线。
