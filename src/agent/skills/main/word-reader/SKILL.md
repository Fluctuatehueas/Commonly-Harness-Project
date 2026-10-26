---
name: word-reader
description: 读取、概括和提取用户上传的 Word (.docx) 文档内容，包括标题、正文段落与表格。
---

# Word 文档阅读

## 何时使用

用户上传 `.docx` 附件，或明确要求阅读、总结、翻译、摘录 Word 文档时使用本技能。
纯图片扫描件、`.doc`（旧二进制格式）不在本技能范围内。

## 读取步骤

1. 在用户消息的「附件元信息」里找到目标文件的 `host_path` 和文件名。
2. 宿主环境直接调用工具 `read_word_document(path=host_path)`，不要尝试用 `read_file` 读二进制。
3. 工具返回正文与表格文本；如果末尾出现「内容已截断」，只基于已返回部分回答，并告知用户文档较长。
4. 需要按章节处理长文档时，先用工具结果列出章节标题，再分节总结，不要把全文一次性复述给用户。

## 沙箱环境

沙箱内没有 `read_word_document` 工具时，用脚本读取沙箱中的副本：

```bash
python3 -I /skills/main/word-reader/scripts/read_docx.py /skills/uploads/report.docx
```

脚本只依赖 Python 标准库，输出纯文本；`--json` 输出结构化结果，`--max-chars` 控制截断长度。

## 输出建议

- 先给 1-3 句话的文档概览（主题、结构、篇幅）。
- 再按用户要求给要点、表格或原文摘录。
- 表格用 Markdown 表格回填，保留表头。
- 不要虚构文档中没有的内容；扫描件无法识别时明确说明。
