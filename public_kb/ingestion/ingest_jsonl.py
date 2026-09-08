# 功能：documents.jsonl 断点续传入库 CLI。
"""从分块产物 documents.jsonl 断点续传入库 Milvus。

透传至 ``sinks.jsonl_ingest_runner.ingest_jsonl_with_resume``；
进度文件默认 ``{jsonl}.progress.json``，中断后重启自动跳过已写入行。

用法：
    python -m public_kb.ingestion.ingest_jsonl \
        DATA/work/chunks/law_001.documents.jsonl
"""

from __future__ import annotations

from public_kb.ingestion.sinks.jsonl_ingest_runner import main

if __name__ == "__main__":
    raise SystemExit(main())
