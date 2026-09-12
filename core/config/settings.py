# 功能：集中加载和管理全局配置参数（由 public_kb/config.py 上提，Phase 0 / A-1）。
"""
core.config.settings — 全局单一配置中心。

来源：public_kb/config.py（364 行整体上提）。保持 @dataclass Settings +
os.getenv + 根 .env 加载方式不变，仅包路径迁移；public_kb/config.py
改为 `from core.config.settings import Settings` re-export + DeprecationWarning。

使用方式：
    from core.config.settings import Settings
    settings = Settings()                          # 从 .env 自动加载
    settings = Settings(milvus_host="192.168.1.1") # 或显式覆盖
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# 自动加载项目根目录的 .env（本模块位于 core/config/，上溯两级到项目根）
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ENV_PATH = os.path.join(_PROJECT_ROOT, ".env")
load_dotenv(_ENV_PATH)

# W3-4 内容安全：默认的有害执行请求关键词（动作导向，见 Settings.safety_harmful_keywords）。
# 只拦「可执行的有害指令」；不拦合法话题词（围标/串标/行贿受贿/伪造公章罪等
# "认定/责任"类知识问题——误伤即能力受损）。关键词表可经 env 扩展。
_DEFAULT_SAFETY_KEYWORDS = [
    # 明确动作请求（前缀 + 有害动作）
    "帮我伪造", "替我伪造", "帮我虚", "帮我篡改", "帮我删除", "帮我删掉",
    "帮我作弊", "帮我骗",
    # 直接祈使（改数据/删记录/收买评标等可执行指令）
    "伪造标书", "伪造中标", "伪造业绩", "虚开中标", "收买评标",
    "删除处罚", "删除不良", "删掉处罚", "删掉不良", "修改中标", "篡改中标",
    "篡改记录", "删除记录", "开后门", "给回扣", "索贿",
]


@dataclass
class CitationRuleConfig:
    """引用来源校验规则开关 — 法规类专业场景溯源规则集。

    每条规则 fail-soft：只产出结构化校验报告，不阻断回答返回。
    测评系统依据 citation_validation 做自动化评估。
    """

    # R1: 每条引用必须有 Milvus 行级 chunk_id
    require_chunk_id: bool = True
    # R2: 每条引用必须有内容派生 chunk_uid
    require_chunk_uid: bool = True
    # R3: 数据源位置（doc_name / chapter）必须非空且可定位
    require_source_location: bool = True
    # R4: 原文片段必须完整（非空、未被截断）
    require_full_text: bool = True
    # R5: 无遗漏 — 所有进入 LLM 上下文的 chunk 必须全部出现在 citations 中
    check_context_completeness: bool = True
    # R6: 回答中的【来源N】标记必须全部解析到有效引用（无幻觉引用）
    check_marker_validity: bool = True
    # R7: 严格模式 — 上下文 chunk 必须全部被回答标记引用（默认关，
    #     开启后未标记即判失败；关闭时未标记 chunk 仅记入 uncited_chunks）
    enforce_all_context_cited: bool = False


@dataclass
class Settings:
    """公共知识库全局配置，所有字段均可通过构造参数覆盖。"""

    # ============================================================
    # Milvus 连接
    # ============================================================
    milvus_host: str = field(
        default_factory=lambda: os.getenv("MILVUS_HOST", "localhost")
    )
    milvus_port: str = field(
        default_factory=lambda: os.getenv("MILVUS_PORT", "19530")
    )
    milvus_uri: str = field(
        default_factory=lambda: os.getenv("MILVUS_URI", "")
    )
    milvus_token: str = field(
        default_factory=lambda: os.getenv("MILVUS_TOKEN", "")
    )
    milvus_timeout: int = field(
        default_factory=lambda: int(os.getenv("MILVUS_TIMEOUT", "30"))
    )
    collection_name: str = field(
        default_factory=lambda: os.getenv("MILVUS_COLLECTION", "public_kb")
    )
    collection_schema_version: str = "public_kb_v2"
    # BM25 关键词检索（2026-09-08 重构后默认开）：sparse 字段 + 服务端 BM25 Function
    # + SPARSE_INVERTED_INDEX 索引。检索端对无 sparse 字段的旧集合自动降级纯稠密。
    enable_bm25: bool = field(
        default_factory=lambda: os.getenv("ENABLE_MILVUS_BM25", "true").lower()
        in {"1", "true", "yes"}
    )
    # 实验集合命名空间：``public_kb_*``（不含生产集合 public_kb 本身）可重建/删除
    milvus_experiment_prefix: str = "public_kb_"
    # 受保护生产集合：禁止经重建/清空接口删除
    milvus_protected_collection: str = "public_kb"
    # dense 语义检索索引（2026-09-08 由 IVF_FLAT 迁移 HNSW）；HNSW 建索引参数见下，
    # 检索参数走 core/vectorstore/index_params.build_dense_search_params（HNSW→ef）。
    milvus_dense_index_type: str = "HNSW"
    milvus_sparse_index_type: str = "SPARSE_INVERTED_INDEX"
    # HNSW 建索引参数（Milvus v2.6.x 合法范围：M∈[4,64]、efConstruction∈[8,512]）
    milvus_hnsw_m: int = 16
    milvus_hnsw_ef_construction: int = 128
    # HNSW 检索参数（ef 需 ≥ 单路候选数 hybrid_dense_limit=30）
    milvus_hnsw_search_ef: int = 64
    bm25_k1: float = 1.2
    bm25_b: float = 0.75
    bm25_analyzer_type: str = "chinese"

    @property
    def resolved_milvus_uri(self) -> str:
        """返回显式 URI；未配置时兼容现有 host/port。"""
        return self.milvus_uri or f"http://{self.milvus_host}:{self.milvus_port}"

    # ============================================================
    # Embedding 模型
    # ============================================================
    embedding_model: str = field(
        default_factory=lambda: os.getenv(
            "EMBEDDING_MODEL", "BAAI/bge-m3"
        )
    )
    embedding_api_key: str = field(
        default_factory=lambda: os.getenv(
            "EMBEDDING_API_KEY",
            os.getenv(
                "SILICONFLOW_API_KEY",
                os.getenv("CLOSEAI_API_KEY", os.getenv("DEEPSEEK_API_KEY", "")),
            ),
        )
    )
    embedding_base_url: str = field(
        default_factory=lambda: os.getenv(
            "EMBEDDING_BASE_URL",
            os.getenv("SILICONFLOW_BASE_URL", os.getenv("CLOSEAI_BASE_URL", "")),
        )
    )
    # BAAI/bge-m3 中文专用，1024 维，8192 token 上限
    embedding_dim: int = 1024

    # ============================================================
    # LLM 问答模型
    # ============================================================
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-chat")
    )
    llm_api_key: str = field(
        default_factory=lambda: os.getenv(
            "LLM_API_KEY",
            os.getenv("DEEPSEEK_API_KEY", os.getenv("CLOSEAI_API_KEY", "")),
        )
    )
    llm_base_url: str = field(
        default_factory=lambda: os.getenv(
            "LLM_BASE_URL",
            os.getenv("DEEPSEEK_BASE_URL", os.getenv("CLOSEAI_BASE_URL", "")),
        )
    )
    llm_temperature: float = 0.0

    # ============================================================
    # MinerU 解析
    # ============================================================
    mineru_timeout: int = 3600  # OCR 版 PDF 需要较长超时
    # magic-pdf 输出目录（临时中间产物）
    # 注意（M6 治理）：此目录存放 MinerU 解析的中间 Markdown 缓存，仅用于
    # 断点续跑，勿与 DATA 的组织化目录（cleaned_v1 等）混放或提交入库。
    mineru_output_dir: str = field(
        default_factory=lambda: os.path.join(
            _PROJECT_ROOT, "DATA", "raw_data"
        )
    )

    # ── MinerU 远程解析服务（三档路由 Tier C / 部署补充方案 §4）──
    # 本地侧只依赖 HTTP 协议，换部署位置只改 base_url（PDF_PARSE_BASE_URL 优先）。
    mineru_api_base_url: str = field(
        default_factory=lambda: os.getenv(
            "PDF_PARSE_BASE_URL", os.getenv("MINERU_API_BASE_URL", "")
        )
    )
    mineru_api_token: str = field(
        default_factory=lambda: os.getenv("MINERU_API_TOKEN", "")
    )
    mineru_api_timeout: int = field(
        default_factory=lambda: int(os.getenv("MINERU_API_TIMEOUT", "1800"))
    )

    # ── PDF 三档路由（三档路由计划 §7）──
    # 总开关：关闭时回退旧的 MinerUParser 全量链路（M1 行为不变）。
    pdf_tiered_routing_enabled: bool = field(
        default_factory=lambda: os.getenv(
            "PDF_TIERED_ROUTING_ENABLED", "false"
        ).lower() in {"1", "true", "yes"}
    )
    pdf_tiered_two_col_confidence: float = field(
        default_factory=lambda: float(os.getenv("PDF_TIERED_TWO_COL_CONFIDENCE", "0.80"))
    )
    pdf_tiered_min_text_chars: int = field(
        default_factory=lambda: int(os.getenv("PDF_TIERED_MIN_TEXT_CHARS", "80"))
    )
    pdf_tiered_image_area_ratio: float = field(
        default_factory=lambda: float(
            os.getenv("PDF_TIERED_IMAGE_AREA_RATIO", "0.35")
        )
    )
    pdf_tiered_img_min_text_chars: int = field(
        # 修复①（2026-09-05）：图片占比高须叠加"文本层稀疏"才送 MinerU——
        # OCR 扫描件整页是背景图 + 完整文本层，img 高但 text≥该值走本地 fast_text。
        default_factory=lambda: int(
            os.getenv("PDF_TIERED_IMG_MIN_TEXT_CHARS", "200")
        )
    )
    pdf_tiered_background_img_ratio: float = field(
        # 覆盖页面积 ≥ 该比例的单张图片视为背景/OCR 扫描底图剔除。
        # 0.85 → 0.70：实测 OCR 书扫描底图覆盖 0.75~0.86，0.85 拦不住；
        # book1/book3 在 0.70 下路由不变（探针验证），仅修复 book2 图片误判。
        default_factory=lambda: float(
            os.getenv("PDF_TIERED_BACKGROUND_IMG_RATIO", "0.70")
        )
    )
    pdf_tiered_expand_boundary_pages: int = field(
        default_factory=lambda: int(
            os.getenv("PDF_TIERED_EXPAND_BOUNDARY_PAGES", "1")
        )
    )
    pdf_tiered_fast_max_workers: int = field(
        default_factory=lambda: int(os.getenv("PDF_TIERED_FAST_MAX_WORKERS", "4"))
    )
    pdf_tiered_profile_max_workers: int = field(
        default_factory=lambda: int(
            os.getenv("PDF_TIERED_PROFILE_MAX_WORKERS", "16")
        )
    )
    pdf_tiered_book_timeout_sec: int = field(
        # 单本导出超时：超时则终止该本并继续下一本。默认 2h 是**兜底上限**
        # （防卡死），不是预期耗时——实测单本首次解析 6s~8min（2026-09-12
        # manifest：range 数 书1=6 / 书2=1 / 书3=17；电子书走本地 fast_text
        # 秒级，扫描 OCR 书因文本层碎片多较慢）。
        # 注：2026-09-05「修复①」前曾因误判双栏导致 45~50 范围/册、耗时
        # 1~1.5h，该数据已作废。
        default_factory=lambda: int(
            os.getenv("PDF_TIERED_BOOK_TIMEOUT_SEC", "7200")
        )
    )
    pdf_tiered_allow_partial: bool = field(
        default_factory=lambda: os.getenv(
            "PDF_TIERED_ALLOW_PARTIAL", "false"
        ).lower() in {"1", "true", "yes"}
    )
    pdf_tiered_manifest_dir: str = field(
        default_factory=lambda: os.getenv(
            "PDF_TIERED_MANIFEST_DIR",
            os.path.join(
                _PROJECT_ROOT, "DATA", "raw_data", "_pdf_tiered_manifest"
            ),
        )
    )
    # 页眉识别（A1-1/A1-3）：跨页重复即页眉，不做按位置的硬切（旧 top-ratio 误删正文首行）。
    pdf_header_repeat_min: int = field(
        default_factory=lambda: int(
            os.getenv("PDF_HEADER_REPEAT_MIN", "2")
        )
    )  # Signal A：归一化（去页码前缀）后出现在多少个不同页 + 出现 >=2 个不同页码 → 页眉候选
    pdf_header_repeat_plain: int = field(
        default_factory=lambda: int(os.getenv("PDF_HEADER_REPEAT_PLAIN", "5"))
    )  # Signal B：无页码、原样出现在多少个不同页 → 页眉候选（与 cleaner/assembler 重复页眉口径一致）
    pdf_header_max_len: int = field(
        default_factory=lambda: int(os.getenv("PDF_HEADER_MAX_LEN", "25"))
    )  # 页眉候选最大长度（字符）——正文行通常更长且不跨页重复

    # ============================================================
    # PDF 结构适配（任务 M1）— 电子书类 PDF 的表格原子块/目录过滤/双栏打标
    # ============================================================
    enable_pdf_structure: bool = field(
        default_factory=lambda: os.getenv(
            "ENABLE_PDF_STRUCTURE", "true"
        ).lower() in {"1", "true", "yes"}
    )
    # A3-3：外部 markdown（cleaned_v1）治理开关——引用误读校正/列表项降级/元信息剥离
    enable_external_md_normalize: bool = field(
        default_factory=lambda: os.getenv(
            "ENABLE_EXTERNAL_MD_NORMALIZE", "true"
        ).lower() in {"1", "true", "yes"}
    )
    pdf_min_table_rows: int = 2  # 连续 | 表格行达到该行数才视为表格原子段
    enable_pdf_toc_filter: bool = True  # 剔除点线目录行（"……(82)"）
    enable_pdf_reflow_flag: bool = True  # 疑似双栏乱序块打标（供人工抽检）
    # A3-5/前置区跳过（用户决策 2026-09-05）：封面/CIP/序言/目录等前置页在
    # 解析层直接跳过（pdf_router 正文起点识别），不再产出——书名由 doc_name
    # 承载。识别失败的书由 clean_markdown 的 A3-5 剥离兜底。
    pdf_skip_front_matter: bool = field(
        default_factory=lambda: os.getenv(
            "PDF_SKIP_FRONT_MATTER", "true"
        ).lower() in {"1", "true", "yes"}
    )
    # Phase A 后置排版重构（2026-09-07 灰度开关删除后收口）：目录残标题剔除 + 引用误标题
    # 降级（relayout_pipeline，R1/R2）。R4/R5（标题续行补全/44 字句末折行）已由解析层
    # 段落化（pdf_paragraph）取代、随 pdf_layout_v2 开关一并删除。书路径 clean 最后兜底；
    # external 不经过。
    enable_pdf_relayout: bool = field(
        default_factory=lambda: os.getenv(
            "ENABLE_PDF_RELAYOUT", "true"
        ).lower() in {"1", "true", "yes"}
    )
    # 页数上限（试点/分批重处理）：>0 时只解析前 N 页（0 = 全量）。
    # 用于 P6 验收的前 N 页试点（先行确认版面质量再跑整书）。
    pdf_parse_page_limit: int = field(
        default_factory=lambda: int(os.getenv("PDF_PARSE_PAGE_LIMIT", "0") or 0)
    )

    # ============================================================
    # ingestion CLI 路径（9.3 Track B）— 默认输入/工作目录，CLI 显式参数覆盖
    # ============================================================
    # 源文档目录（批量解析 --files 缺省读取）；中间产物/checkpoint 工作目录
    # （--work-dir 缺省启用），与 DATA/raw_data（MinerU 中间产物）分离。
    ingestion_input_dir: str = field(
        default_factory=lambda: os.getenv(
            "INGESTION_INPUT_DIR",
            os.path.join(_PROJECT_ROOT, "DATA", "input"),
        )
    )
    ingestion_work_dir: str = field(
        default_factory=lambda: os.getenv(
            "INGESTION_WORK_DIR",
            os.path.join(_PROJECT_ROOT, "DATA", "work"),
        )
    )

    # ============================================================
    # 切片参数
    # ============================================================
    chunk_max_chars: int = 2000  # 单块最大字符数（bge-m3 限 8192 token，中文约 1 token/字，留足余量）
    chunk_overlap_chars: int = 100  # 句子切分时的重叠字符数
    # 类型化分块阈值（Phase B/C 优化）
    chunk_max_chars_law: int = 700
    chunk_max_chars_law_compilation: int = 700
    chunk_max_chars_qa: int = 900
    chunk_max_chars_book_practical: int = 1000
    # 注（W1-6）：原 `chunk_max_parent_chars` 已删除——typed_chunker 一期不落 parent
    # 行（child 只指向合成父标识），检索期父块拼接上限由 retrieval_parent_max_chars
    # 控制，该字段从未被任何代码读取。
    # 批量 embedding 并发
    embedding_batch_size: int = 100
    embedding_max_workers: int = 4

    # ============================================================
    # 检索参数（P1-3：扩大候选池并放宽阈值，降低长尾问题漏召）
    # ============================================================
    retrieval_top_k: int = 11  # E5/多跳决策：5→11，多跳第二块进窗口（对照 A 双段全中 +5.7pp、MRR 不降）
    similarity_threshold: float = 0.45  # 降级路径阈值（低于此值直接拒答），主路径使用自适应阈值

    # ── 混合检索参数 ──
    hybrid_dense_limit: int = 30   # 稠密向量检索候选数
    hybrid_sparse_limit: int = 30  # 稀疏向量检索候选数
    hybrid_fusion_limit: int = 30  # RRF 融合后取 Top-N
    nprobe: int = 32               # IVF 检索探测单元数（显式控制精度）
    rrf_k: int = 60                # RRF 融合参数 k

    # ── 父子块检索（RAG 检索链路优化 P1/P2）──
    retrieval_group_by_parent: bool = True   # 按 parent_chunk_id 分组召回，避免同父碎片占满 top-K
    retrieval_group_size: int = 2            # 每组保留子块数（进 rerank 条数 ≈ hybrid_fusion_limit × 本值）
    retrieval_parent_assembly: bool = True   # 命中子块拼回完整父块，写入 metadata["context_text"]
    retrieval_parent_max_chars: int = 6000   # 父块拼接长度熔断上限
    kb_light_decompose: bool = True          # Layer 1：knowledge_qa 统一轻量补检（基线 top-11 + 每子句 top-5 并集，max_chunks=20）。S1 已验生成侧不稀释，2026-09-10 默认开启；置 False 一键回退
    retrieval_context_warn_chars: int = 20000  # 单轮检索上下文总长告警阈值（只告警不截断）
    reranker_model: str = "BAAI/bge-reranker-v2-m3"  # Cross-Encoder 重排序模型
    rerank_high_confidence_score: float = field(
        default_factory=lambda: float(os.getenv("RERANK_HIGH_CONFIDENCE_SCORE", "0.75"))
    )
    rerank_medium_confidence_score: float = field(
        default_factory=lambda: float(os.getenv("RERANK_MEDIUM_CONFIDENCE_SCORE", "0.50"))
    )
    rerank_high_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("RERANK_HIGH_CONFIDENCE_THRESHOLD", "0.40"))
    )
    rerank_medium_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("RERANK_MEDIUM_CONFIDENCE_THRESHOLD", "0.45"))
    )
    rerank_low_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("RERANK_LOW_CONFIDENCE_THRESHOLD", "0.50"))
    )
    reranker_timeout: float = field(
        default_factory=lambda: float(os.getenv("RERANKER_TIMEOUT", "30"))
    )
    reranker_max_retries: int = field(
        default_factory=lambda: int(os.getenv("RERANKER_MAX_RETRIES", "2"))
    )
    reranker_retry_backoff_seconds: float = field(
        default_factory=lambda: float(os.getenv("RERANKER_RETRY_BACKOFF_SECONDS", "0.25"))
    )
    strict_hybrid_validation: bool = field(
        default_factory=lambda: os.getenv(
            "STRICT_HYBRID_VALIDATION", "false"
        ).lower() in {"1", "true", "yes"}
    )

    # ============================================================
    # 超时与重试参数（任务2）
    # ============================================================
    llm_timeout: int = field(
        default_factory=lambda: int(os.getenv("LLM_TIMEOUT", "60"))
    )
    llm_max_retries: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "1"))
    )
    embedding_timeout: int = field(
        default_factory=lambda: int(os.getenv("EMBEDDING_TIMEOUT", "30"))
    )
    embedding_max_retries: int = field(
        default_factory=lambda: int(os.getenv("EMBEDDING_MAX_RETRIES", "1"))
    )

    # ============================================================
    # SQL 查询超时（任务3）
    # ============================================================
    sql_query_timeout: int = field(
        default_factory=lambda: int(os.getenv("SQL_QUERY_TIMEOUT", "15"))
    )

    # ============================================================
    # 线程池（W0-1）— "整条查询"与"单条 SQL"必须分池
    # ============================================================
    # 同池嵌套会导致并发 ≥ worker 数时内层任务永不调度（审计 A1）。两者独立配置。
    query_pool_max_workers: int = field(
        default_factory=lambda: int(os.getenv("QUERY_POOL_MAX_WORKERS", "8"))
    )
    sql_pool_max_workers: int = field(
        default_factory=lambda: int(os.getenv("SQL_POOL_MAX_WORKERS", "8"))
    )

    # ============================================================
    # 全局并发闸门（C1）— 保护上游 LLM/检索，防并发打爆
    # ============================================================
    # 进程级「在飞请求」上限；超限等待 wait_timeout 后快速失败（503 + Retry-After），
    # 不无限排队。0 = 关闭（便于灰度/回退）。多 worker 下总上限 ≈ workers × 本值。
    # 与入口限流（rate_limit_per_minute，按客户端）互补：本项管**全局容量**。
    # 建议值按上游速率上限实测标定（见 scripts/load_test_chat.py）。
    max_inflight_requests: int = field(
        default_factory=lambda: int(os.getenv("MAX_INFLIGHT_REQUESTS", "32"))
    )
    inflight_wait_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("INFLIGHT_WAIT_TIMEOUT_SECONDS", "5"))
    )

    # ============================================================
    # price 召回链熔断（P3 D-6）— 全局 deadline 与全表扫描放行开关
    # ============================================================
    # RecallPipeline 全局召回预算：到点未命中即熔断返回空（不再裸进全表扫描）。
    # 全表扫描兜底默认禁入，仅在显式配置下放行。
    price_recall_deadline_ms: int = field(
        default_factory=lambda: int(os.getenv("PRICE_RECALL_DEADLINE_MS", "3000"))
    )
    price_allow_full_scan: bool = field(
        default_factory=lambda: os.getenv("PRICE_ALLOW_FULL_SCAN", "false").lower()
        in {"1", "true", "yes"}
    )

    # ============================================================
    # 检索结果缓存（W4-2）— 版本化 TTL 缓存
    # ============================================================
    # 只缓存"事实层"（KB 检索结果 / price 查询记录），不缓存 LLM 最终答案。
    # 版本化失效：`cache_corpus_version` 为空时用 MILVUS_COLLECTION 名当语料版本
    # （重建换集合名 → 旧缓存自动失效）；price 数据版本用 `cache_price_version`
    # 手动 bump（无天然版本信号），并以较短 TTL 兜底。
    cache_enabled: bool = field(
        default_factory=lambda: os.getenv("CACHE_ENABLED", "true").lower() in {"1", "true", "yes"}
    )
    cache_maxsize: int = field(default_factory=lambda: int(os.getenv("CACHE_MAXSIZE", "512")))
    cache_kb_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("CACHE_KB_TTL_SECONDS", "3600"))
    )
    cache_price_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("CACHE_PRICE_TTL_SECONDS", "300"))
    )
    cache_corpus_version: str = field(
        default_factory=lambda: os.getenv("CACHE_CORPUS_VERSION", "")
    )
    cache_price_version: str = field(
        default_factory=lambda: os.getenv("CACHE_PRICE_VERSION", "v1")
    )
    # 缓存后端：memory（进程内 cachetools，默认）| redis（多副本共享，W4-2b）
    cache_backend: str = field(
        default_factory=lambda: os.getenv("CACHE_BACKEND", "memory")
    )
    cache_redis_url: str = field(
        default_factory=lambda: os.getenv("CACHE_REDIS_URL", "redis://localhost:6379/0")
    )

    # ============================================================
    # 全局总超时（任务4）— 单次业务节点最大允许执行时间
    # ============================================================
    node_total_timeout: int = field(
        default_factory=lambda: int(os.getenv("NODE_TOTAL_TIMEOUT", "45"))
    )

    # ============================================================
    # MySQL 连接（ztb_clean 结构化数据）— Phase 0 / A-10 配置收口。
    # 由 price_inquiry/db.py 的 os.getenv 直读上提；默认值留空、缺失时由
    # core/rdb/mysql_pool 报错，不再硬编码内网地址（原 db.py:25-26 曾硬编码
    # 192.168.10.120 / iflytek）。
    # ============================================================
    mysql_host: str = field(
        default_factory=lambda: os.getenv("MYSQL_HOST", "")
    )
    mysql_user: str = field(
        default_factory=lambda: os.getenv("MYSQL_USER", "")
    )
    mysql_password: str = field(
        default_factory=lambda: os.getenv("MYSQL_PASSWORD", "")
    )
    mysql_port: int = field(
        default_factory=lambda: int(os.getenv("MYSQL_PORT", "3306"))
    )
    mysql_clean_db: str = field(
        default_factory=lambda: os.getenv("MYSQL_CLEAN_DB", "ztb_clean")
    )
    # 连接池上限（W0-10）：无上限时并发下池会无限增长（审计 A5）。
    mysql_pool_max: int = field(
        default_factory=lambda: int(os.getenv("MYSQL_POOL_MAX", "16"))
    )
    # socket 读/写超时（W0-10）：SSDictCursor 流式读大表需要较长窗口；
    # 单条 SQL 的**业务**超时由 _execute_sql_with_timeout 用线程池控制，
    # 此处只防 socket 层卡死。旧实现试图 setdefault 覆盖但从未生效。
    mysql_read_timeout: int = field(
        default_factory=lambda: int(os.getenv("MYSQL_READ_TIMEOUT", "300"))
    )

    # ============================================================
    # 功能开关（任务1）
    # ============================================================
    enable_auto_semantic_bootstrap: bool = field(
        default_factory=lambda: os.getenv(
            "ENABLE_AUTO_SEMANTIC_BOOTSTRAP", "false"
        ).lower() in {"1", "true", "yes"}
    )

    # ============================================================
    # knowledge_qa 子图护栏失败重试开关（方案 Phase 6 / F-3）
    # 默认 true：护栏（R1~R7）失败时把失败原因注入 prompt 重生成一次（总 ≤2 次），
    # 提高引用合规率；false = 返回答案 + 校验报告（P6 前现状语义）。
    kb_guardrail_retry: bool = field(
        default_factory=lambda: os.getenv("KB_GUARDRAIL_RETRY", "true").lower()
        in {"1", "true", "yes"}
    )

    # ============================================================
    # 内容安全前置（W3-4）— 路由前的确定性输入闸门
    # ============================================================
    # 问题长度上限（字符）：超长即拒绝（防注入/耗 token 的攻击向量）。
    max_question_chars: int = field(
        default_factory=lambda: int(os.getenv("MAX_QUESTION_CHARS", "2000"))
    )
    # 明确的可执行有害请求关键词（逗号分隔，env 可扩展）：动作导向，只拦
    # 「帮我伪造… / 删掉处罚记录 / 篡改…」这类可执行有害指令；不拦「围标串标/
    # 行贿受贿」等合法话题词（那些是知识库正常覆盖的专业问题）。
    safety_harmful_keywords: list = field(
        default_factory=lambda: [
            k.strip() for k in os.getenv("SAFETY_HARMFUL_KEYWORDS", "").split(",")
            if k.strip()
        ] or _DEFAULT_SAFETY_KEYWORDS
    )

    # ============================================================
    # 可观测 trace（E0-3）— OTel → Langfuse（未配置密钥时全链路 no-op）
    # ============================================================
    # 开关与密钥缺一即不启用（dev/test 默认关，零开销、零网络依赖）。
    enable_tracing: bool = field(
        default_factory=lambda: os.getenv("ENABLE_TRACING", "false").lower()
        in {"1", "true", "yes"}
    )
    langfuse_host: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_HOST", "http://localhost:3000")
    )
    langfuse_public_key: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_PUBLIC_KEY", "")
    )
    langfuse_secret_key: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_SECRET_KEY", "")
    )
    tracing_service_name: str = field(
        default_factory=lambda: os.getenv("TRACING_SERVICE_NAME", "ztb-agent")
    )

    # ============================================================
    # 入库去重（任务 M2）— 基于 chunk_uid 的文本块级去重与幂等
    # ============================================================
    enable_dedup: bool = field(
        default_factory=lambda: os.getenv("ENABLE_DEDUP", "true").lower()
        in {"1", "true", "yes"}
    )

    # ============================================================
    # 法条时效性（任务 M3）— 检索时按施行日期过滤（默认关，不动现网行为）
    # ============================================================
    enable_effective_filter: bool = field(
        default_factory=lambda: os.getenv(
            "ENABLE_EFFECTIVE_FILTER", "false"
        ).lower() in {"1", "true", "yes"}
    )

    # ============================================================
    # 上传限制（W0-8）— /files/upload 的大小与类型闸门
    # ============================================================
    # 大小上限（MB）：整文件读入内存，无上限时可被单请求打爆（审计 A/D 类）。
    max_upload_mb: int = field(
        default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "20"))
    )
    # 扩展名白名单：与 public_kb.ingestion.router._EXT_TO_FORMAT 对齐；
    # 未命中直接 400，避免任意二进制走"嗅探失败 → 默认 text"的宽松路径。
    # 图片类型（W4-3 图片 OCR）：走 RapidOCR 本地识别，数据不出服务端。
    allowed_upload_exts: tuple[str, ...] = (
        ".pdf", ".csv", ".txt", ".md", ".markdown",
        ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
    )

    # 图片 OCR（W4-3）：RapidOCR 本地推理；关闭时上传图片降级返回（不产出乱码）。
    image_ocr_enabled: bool = field(
        default_factory=lambda: os.getenv("IMAGE_OCR_ENABLED", "true").lower()
        in {"1", "true", "yes"}
    )

    # 任务队列（W4-3 队列解耦）：后台 worker 数（异步上传解析用）
    job_queue_workers: int = field(
        default_factory=lambda: int(os.getenv("JOB_QUEUE_WORKERS", "2"))
    )

    # ============================================================
    # 会话与检查点（W2-4）
    # ============================================================
    # 记忆后端：memory（进程内，重启丢失）| sqlite | postgres | redis。
    # 服务态默认 sqlite —— MemorySaver 不跨进程共享，多 worker 下会话会串。
    checkpointer_backend: str = field(
        default_factory=lambda: os.getenv("CHECKPOINTER_BACKEND", "sqlite")
    )
    checkpointer_sqlite_path: str = field(
        default_factory=lambda: os.getenv(
            "CHECKPOINTER_SQLITE_PATH",
            os.path.join(_PROJECT_ROOT, "DATA", "checkpoints.db"),
        )
    )
    # 会话空闲过期（秒）：超时清理其检查点；0 = 不过期。
    session_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("SESSION_TTL_SECONDS", "86400"))
    )
    # 惰性清理的扫描间隔（秒）——每次 touch 时若距上次扫描超过该间隔则扫一次。
    session_sweep_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("SESSION_SWEEP_INTERVAL_SECONDS", "300"))
    )

    # ============================================================
    # 入口限流（W2-5）— 进程内令牌桶；0 = 关闭
    # ============================================================
    rate_limit_per_minute: int = field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
    )
    rate_limit_burst: int = field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_BURST", "20"))
    )

    # ============================================================
    # 鉴权与审计（W3-0）
    # ============================================================
    # 逗号分隔的 API Key；为空 = 关闭鉴权（本地/内网）。原始 Key 不落任何日志。
    api_keys: str = field(default_factory=lambda: os.getenv("API_KEYS", ""))
    auth_exempt_paths: str = field(
        default_factory=lambda: os.getenv(
            "AUTH_EXEMPT_PATHS", "/health,/metrics,/docs,/openapi.json,/redoc"
        )
    )
    # 审计日志落盘路径（空 = 只进 audit logger，输出到 stderr）
    audit_log_path: str = field(
        default_factory=lambda: os.getenv("AUDIT_LOG_PATH", "")
    )
    # 审计是否记录用户问题（含企业名等个人信息，截断 120 字符；可关闭）
    audit_log_questions: bool = field(
        default_factory=lambda: os.getenv("AUDIT_LOG_QUESTIONS", "true").lower()
        in {"1", "true", "yes"}
    )

    # ============================================================
    # 系统提示词
    # ============================================================
    system_prompt: str = (
        "你是一个招投标领域的专业顾问，基于权威的公共知识库资料回答问题。\n"
        "请严格依据下方提供的参考资料作答，不要添加任何资料中没有的信息。\n"
        "如果参考资料不足以回答问题，请明确告知用户无法回答。"
    )

    # ============================================================
    # 引用溯源（任务：回答附带被引用 chunk 的完整来源信息）
    # ============================================================
    enable_inline_citations: bool = True  # LLM 回答内联标注【来源N】标记
    citation_rules: CitationRuleConfig = field(
        default_factory=CitationRuleConfig
    )
