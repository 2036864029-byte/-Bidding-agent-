# 文档分块与向量入库执行方案（Milvus 单库版）

本方案基于你当前的技术栈选型（LangChain 分块 + Milvus 单库存储）设计，覆盖从 Markdown 文档输入到最终向量入库的全链路，包含数据分流、结构切分、语义细分、父子块关联、统一元数据、质量校验全流程，可直接交付编程实现。

------

## 一、整体架构总览

### 1.1 设计目标

- 统一流水线：所有文档走同一条主链路，通过类型标签实现分支逻辑，避免多套代码冗余
- 结构优先：优先基于 Markdown 标题层级切分，语义分块仅作为超长块的补充
- 单库存储：Milvus 同时存储向量、完整文本、全量元数据，中小体量下架构最简
- 可扩展：后续可平滑迁移为双库分离架构，无需重构核心逻辑

### 1.2 技术栈选型

表格

| 模块     | 技术选型                               | 说明                                              |
| -------- | -------------------------------------- | ------------------------------------------------- |
| 文档输入 | Markdown 格式                          | 前置已完成格式转换，直接作为输入                  |
| 结构分块 | LangChain `MarkdownHeaderTextSplitter` | 原生支持按标题层级切分，保留层级元数据            |
| 语义分块 | LangChain `SemanticChunker`            | 基于语义相似度拆分超长文本，需搭配 Embedding 模型 |
| 向量化   | 自定义 Embedding 模型                  | 保留目前的模型不变                                |
| 向量存储 | 本地Milvus                             | 单库全量存储，支持标量过滤与向量检索              |
| 辅助校验 | 自定义 Python 函数                     | 实现条号校验、长度校验、数据完整性校验            |

### 1.3 完整流水线总览

```
Markdown文档输入
    ↓
【数据分流模块】按文件夹路径打类型标签（法规/长文档/问答）
    ↓
【结构切分模块】LangChain按标题层级切分（不同类型切分深度不同）
    ↓
【长度校验模块】每个块数字数，≤500字为原子块，>500字为超长块
    ↓ （原子块）                ↓（超长块）
  直接进入元数据生成      【语义细分模块】分类型做内部语义拆分
                                ↓
                          【父子块关联模块】建立父块与子块的ID关联
                                ↓
    ----------所有块汇合----------
             ↓
    【元数据生成模块】统一填充全量元数据字段
             ↓
    【质量校验模块】法规类做条号连续性校验，通用类做完整性校验
             ↓
    【向量化入库模块】Embedding生成 + Milvus单库写入
```

------

## 二、数据分流模块

### 2.1 分流策略

采用**文件夹物理分流**方案，实现简单、零误判，适配当前百级文档体量，后续可平滑升级为自动规则分流。

### 2.2 类型定义与判定规则

表格

| 文档类型 | 文件夹路径             | 核心特征                                            | 切分深度                            |
| -------- | ---------------------- | --------------------------------------------------- | ----------------------------------- |
| 法规类   | `./input/01_法规类/`   | 包含 “第 X 章”“第 X 条” 格式，条目短、层级规范      | 切到三级标题（###，对应单条条文）   |
| 长文档类 | `./input/02_长文档类/` | 章节下大段正文，内容较长                            | 切到二级标题（##，对应小节）        |
| 问答类   | `./input/03_问答类/`   | 以 “问：/ 答：”“Q:/A:” 或三级标题为问题，正文为答案 | 切到三级标题（###，对应单个问答对） |

### 2.3 实现逻辑

1. 遍历三个输入文件夹下的所有`.md`文件
2. 根据文件所在的父文件夹路径，自动给文档打上`doc_type`标签
3. 生成全局唯一`doc_id`，命名规则：`类型前缀_三位序号`，如`law_001`、`doc_023`、`qa_017`

------

## 三、核心分块流水线（基于 LangChain 实现）

### 3.1 模块输入输出

- 输入：带`doc_type`、`doc_id`、`doc_name`标签的 Markdown 文件
- 输出：带完整元数据的 LangChain `Document` 对象列表

### 3.2 步骤 1：文档读取与初始化

1. 读取 Markdown 文件全文
2. 初始化文档基础元数据：`doc_id`、`doc_name`、`doc_type`
3. 传入后续切分模块

### 3.3 步骤 2：Markdown 层级结构切分（核心）

#### 3.3.1 工具选型

使用 LangChain 原生的 **`MarkdownHeaderTextSplitter`**，它可以按标题层级递归切分，并自动将标题路径写入每个块的 metadata 中，完美匹配结构优先的设计原则。

#### 3.3.2 分类型切分深度配置

根据文档类型，配置不同的`headers_to_split_on`参数，控制切分的最细层级：

```
# 不同类型的切分配置
SPLIT_CONFIG = {
    "法规": [
        ("#", "一级标题"),   # 法律名称
        ("##", "二级标题"),  # 第X章
        ("###", "三级标题")  # 第X条
    ],
    "长文档": [
        ("#", "一级标题"),   # 文档名称
        ("##", "二级标题")   # 第X节 / 小节名称
    ],
    "问答": [
        ("#", "一级标题"),   # 文档/主题名称
        ("##", "二级标题"),  # 分类主题
        ("###", "三级标题")  # 单个问题
    ]
}
```

#### 3.3.3 执行逻辑

1. 根据当前文档的`doc_type`，读取对应的切分配置
2. 实例化`MarkdownHeaderTextSplitter`，传入配置
3. 调用`split_text()`方法，传入 Markdown 全文，得到初始结构块列表
4. 每个块自动携带标题层级元数据（如`{"一级标题": "中华人民共和国招标投标法", "二级标题": "第一章 总则", "三级标题": "第一条 立法目的"}`）

### 3.4 步骤 3：原子块长度校验

**自定义简单逻辑实现，无需额外框架**，核心是判定块是否需要进一步拆分。

1. 遍历每个结构块，计算纯文本长度：`len(block.page_content)`
2. 判定阈值：**500 字**（中文场景最优检索粒度，可配置）
   - 若 `长度 ≤ 500`：标记为`atom`（原子块），直接进入元数据生成环节，不再拆分
   - 若 `长度 > 500`：标记为待拆分超长块，进入语义细分环节

### 3.5 步骤 4：超长块语义细分

#### 3.5.1 工具选型

使用 LangChain 实验模块的 **`SemanticChunker`**，基于 Embedding 模型的语义相似度拆分，保证每个子块语义连贯，避免硬切破坏语义。

```
from langchain_experimental.text_splitter import SemanticChunker
```

#### 3.5.2 分类型细分逻辑

表格

| 类型     | 细分逻辑                                                     |
| -------- | ------------------------------------------------------------ |
| 长文档类 | 直接调用 SemanticChunker 拆分，默认子块长度 300-500 字       |
| 问答类   | 1. 先提取块中的问题部分（即三级标题文本）2. 只对答案正文部分做语义拆分3. 每个拆分后的答案子块，最前面拼接完整的问题原文保证：每个子块都包含完整问题，召回时不会断章取义 |
| 法规类   | 几乎不会触发此步骤（单条法规普遍 < 200 字）                  |

### 3.6 步骤 5：父子块关联构建

#### 3.6.1 核心说明

LangChain**没有原生的自动父子分块功能**，但可以基于`Document`对象的`metadata`字段非常轻量地实现，仅需自定义少量关联逻辑。

#### 3.6.2 适用场景

仅对「超长结构块拆分出的语义子块」建立父子关系；原子块无需父子关系，直接独立存在。

#### 3.6.3 具体实现逻辑

1. **生成父块**
   - 原始超长结构块作为父块，保留完整原文
   - 生成唯一`chunk_id`，规则：`{doc_id}_parent_{序号}`
   - metadata 中新增：`chunk_type = "parent"`
   - 父块本身也存入 Milvus，用于后续上下文回溯
2. **生成子块**
   - 语义拆分后的每个小块作为子块
   - 生成唯一`chunk_id`，规则：`{doc_id}_child_{父块序号}_{子块序号}`
   - metadata 中新增：
     - `chunk_type = "child"`
     - `parent_chunk_id = "父块的chunk_id"`
     - 继承父块的所有标题层级元数据
   - 子块按顺序添加`sequence_in_parent`字段（1,2,3...），用于后续排序
3. **原子块**
   - 生成唯一`chunk_id`，规则：`{doc_id}_atom_{序号}`
   - metadata 中新增：`chunk_type = "atom"`，`parent_chunk_id = ""`（空值）

------

## 四、统一元数据规范

所有块采用**统一字段集**，不同类型按需填充，不适用字段留空。保证入库格式统一，检索逻辑通用。

表格

| 字段分类 | 字段名             | 数据类型     | 是否必填 | 生成规则 / 说明            | 法规类示例                    | 问答类示例                              |
| -------- | ------------------ | ------------ | -------- | -------------------------- | ----------------------------- | --------------------------------------- |
| 主键字段 | chunk_id           | VARCHAR(64)  | 是       | 全局唯一 ID，按规则生成    | law_001_atom_001              | qa_017_child_003_01                     |
| 文档基础 | doc_id             | VARCHAR(32)  | 是       | 文档唯一 ID                | law_001                       | qa_017                                  |
| 文档基础 | doc_type           | VARCHAR(16)  | 是       | 枚举：法规 / 长文档 / 问答 | 法规                          | 问答                                    |
| 文档基础 | doc_name           | VARCHAR(255) | 是       | 文档全称                   | 中华人民共和国招标投标法      | 招投标常见问题 100 问                   |
| 块属性   | chunk_type         | VARCHAR(16)  | 是       | 枚举：atom/parent/child    | atom                          | child                                   |
| 层级路径 | hierarchy_path     | VARCHAR(512) | 是       | 完整层级路径，用 “/” 拼接  | 第一章 总则 / 第一条 立法目的 | 招标资质类 / 问题 1：什么项目必须招标？ |
| 父子关联 | parent_chunk_id    | VARCHAR(64)  | 否       | 父块 ID，原子块为空        |                               | qa_017_parent_003                       |
| 排序字段 | sequence_in_parent | INT          | 否       | 父块内的顺序号             |                               | 1                                       |
| 类型专属 | article_number     | VARCHAR(32)  | 否       | 法规专属：条文号           | 第一条                        |                                         |
| 类型专属 | question           | VARCHAR(512) | 否       | 问答专属：完整问题         |                               | 什么项目必须进行招标？                  |
| 内容字段 | content            | TEXT         | 是       | 块的完整文本内容           | 为了规范招标投标活动...       | 问：什么项目必须招标？答：...           |
| 向量字段 | embedding          | FLOAT_VECTOR | 是       | 文本对应的向量             | -                             | -                                       |

------

## 五、Milvus 单库存储设计

### 5.1 集合（Collection）设计

单集合存储所有类型数据，集合名：`rag_chunk_main`

表格

| 字段名          | 字段类型     | 约束   | 说明                                      |
| --------------- | ------------ | ------ | ----------------------------------------- |
| chunk_id        | VarChar(64)  | 主键   | 块唯一标识                                |
| embedding       | FloatVector  | 非空   | 向量维度根据 Embedding 模型定，如 1024 维 |
| doc_id          | VarChar(32)  | 有索引 | 文档 ID，用于按文档过滤                   |
| doc_type        | VarChar(16)  | 有索引 | 文档类型，用于按类型过滤                  |
| chunk_type      | VarChar(16)  | 有索引 | 块类型，用于检索时过滤原子块 / 子块       |
| content         | Text         | 非空   | 完整文本内容                              |
| hierarchy_path  | VarChar(512) |        | 层级路径                                  |
| parent_chunk_id | VarChar(64)  |        | 父块 ID                                   |
| 其他元数据字段  | 对应类型     |        | 剩余元数据字段按需建立                    |

### 5.2 索引配置

- **向量字段索引**：索引类型`HNSW`，度量类型`COSINE`（余弦相似度，适配中文向量模型）
- **标量字段索引**：`doc_id`、`doc_type`、`chunk_type`建立标量索引，加速条件过滤

### 5.3 入库执行逻辑

推荐使用**LangChain 原生 Milvus 集成**实现，代码最简，自动完成向量化与入库：

```
from langchain_community.vectorstores import Milvus

# 初始化Milvus向量存储
vector_store = Milvus(
    embedding_function=your_embedding_model,
    collection_name="rag_chunk_main",
    connection_args={"host": "your_host", "port": "19530"},
    vector_field="embedding",
    text_field="content",
    metadata_field="metadata"
)

# 批量入库，documents为所有带元数据的Document对象列表
vector_store.add_documents(documents)
```

------

## 六、质量校验模块

### 6.1 法规类专属校验：条号连续性校验

自定义 Python 函数，挂在结构切分之后、入库之前执行，保障法规分块准确率。

1. 提取所有三级标题（第 X 条）中的中文序号
2. 转换为阿拉伯数字，得到序号列表
3. 检查序号是否连续递增
4. 若出现断号（如 5→7），标记文档为校验异常，输出告警日志，可选择人工复核或自动补拆分

### 6.2 通用分块质量校验

1. **空块校验**：过滤掉文本长度 < 10 字的空块、碎块
2. **元数据完整性校验**：检查必填字段是否非空
3. **ID 唯一性校验**：检查 chunk_id 全局唯一

------

## 七、可执行代码框架（伪代码）

可直接交付 AI 编程实现，基于 Python + LangChain + Pymilvus

```
# 1. 导入依赖
import os
from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_experimental.text_splitter import SemanticChunker
from langchain_community.vectorstores import Milvus
from langchain_core.documents import Document
# 导入你的Embedding模型

# 2. 全局配置
INPUT_DIRS = {
    "法规": "./input/01_法规类/",
    "长文档": "./input/02_长文档类/",
    "问答": "./input/03_问答类/"
}
SPLIT_HEADERS = {
    "法规": [("#", "一级标题"), ("##", "二级标题"), ("###", "三级标题")],
    "长文档": [("#", "一级标题"), ("##", "二级标题")],
    "问答": [("#", "一级标题"), ("##", "二级标题"), ("###", "三级标题")]
}
MAX_ATOM_LENGTH = 500

# 3. 主流程函数
def process_all_docs():
    all_docs_to_insert = []
    
    # 遍历每个类型
    for doc_type, dir_path in INPUT_DIRS.items():
        for file_name in os.listdir(dir_path):
            if not file_name.endswith(".md"):
                continue
            # 处理单个文档
            chunks = process_single_doc(dir_path + file_name, doc_type)
            all_docs_to_insert.extend(chunks)
    
    # 批量入库
    vector_store = init_milvus()
    vector_store.add_documents(all_docs_to_insert)

# 4. 单个文档处理流程
def process_single_doc(file_path, doc_type):
    # 4.1 读取文件，初始化元数据
    with open(file_path, 'r', encoding='utf-8') as f:
        text = f.read()
    doc_id = generate_doc_id(doc_type)
    doc_name = os.path.basename(file_path).replace(".md", "")
    base_metadata = {"doc_id": doc_id, "doc_type": doc_type, "doc_name": doc_name}
    
    # 4.2 结构切分
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=SPLIT_HEADERS[doc_type],
        strip_headers=False
    )
    struct_chunks = splitter.split_text(text)
    
    final_chunks = []
    parent_seq = 0
    atom_seq = 0
    
    # 4.3 遍历每个结构块
    for idx, chunk in enumerate(struct_chunks):
        # 合并基础元数据
        chunk.metadata.update(base_metadata)
        # 生成层级路径
        chunk.metadata["hierarchy_path"] = build_hierarchy_path(chunk.metadata)
        
        content_len = len(chunk.page_content)
        
        # 4.4 原子块：直接保留
        if content_len <= MAX_ATOM_LENGTH:
            chunk.metadata["chunk_type"] = "atom"
            chunk.metadata["chunk_id"] = f"{doc_id}_atom_{atom_seq:03d}"
            chunk.metadata["parent_chunk_id"] = ""
            chunk.metadata["content"] = chunk.page_content
            final_chunks.append(chunk)
            atom_seq += 1
            
        # 4.5 超长块：语义细分 + 父子关联
        else:
            # 生成父块
            parent_chunk_id = f"{doc_id}_parent_{parent_seq:03d}"
            parent_chunk = Document(
                page_content=chunk.page_content,
                metadata={
                    **chunk.metadata,
                    "chunk_id": parent_chunk_id,
                    "chunk_type": "parent",
                    "parent_chunk_id": "",
                    "content": chunk.page_content
                }
            )
            final_chunks.append(parent_chunk)
            
            # 语义细分（问答类做特殊处理）
            if doc_type == "问答":
                child_chunks = split_qa_semantic(chunk)
            else:
                semantic_splitter = SemanticChunker(embedding_model)
                child_chunks = semantic_splitter.create_documents([chunk.page_content])
            
            # 子块关联父ID
            for i, child in enumerate(child_chunks):
                child.metadata.update(chunk.metadata)
                child.metadata["chunk_id"] = f"{doc_id}_child_{parent_seq:03d}_{i:03d}"
                child.metadata["chunk_type"] = "child"
                child.metadata["parent_chunk_id"] = parent_chunk_id
                child.metadata["sequence_in_parent"] = i + 1
                child.metadata["content"] = child.page_content
                final_chunks.append(child)
            
            parent_seq += 1
    
    # 4.6 法规类专属校验
    if doc_type == "法规":
        check_article_continuity(final_chunks)
    
    return final_chunks

# 5. 辅助函数（生成ID、构建层级路径、问答拆分、条号校验等）
def generate_doc_id(doc_type):
    # 实现doc_id生成逻辑
    pass

def build_hierarchy_path(metadata):
    # 从metadata中提取各级标题，拼接成层级路径
    pass

def split_qa_semantic(chunk):
    # 实现问答类专属拆分逻辑：提取问题，拆分答案，拼接前缀
    pass

def check_article_continuity(chunks):
    # 实现法规条号连续性校验
    pass

def init_milvus():
    # 初始化Milvus连接
    pass
```

------

## 八、后续扩展建议

1. **数据量达到 50 万 +**：可将`content`大文本字段从 Milvus 迁移到 MySQL，平滑升级为双库分离架构，核心检索逻辑不变
2. **类型增多**：在`INPUT_DIRS`和`SPLIT_HEADERS`中新增配置即可，无需改动主流程
3. **召回优化**：检索时默认只查`atom`和`child`类型块，需要上下文时再通过`parent_chunk_id`回溯父块