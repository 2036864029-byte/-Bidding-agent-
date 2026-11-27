# 本地绿 ≠ CI 绿 —— 2026-11-27 首次接入 GitHub Actions 的踩坑全记录

> 场景：仓库此前**没有远端**，`.github/workflows/ci.yml` 写好但一直休眠。
> 2026-11-27 首次推送到 GitHub，CI 连跑 **6 次**才全绿。
> 四次失败**没有一次是业务逻辑 bug**，全部是「本地环境替 CI 掩盖了隐式依赖」。

本文的用途：**给 AI 和人的「改依赖 / 改 CI / 写测试」前必读清单**。
AGENTS.md 里只保留硬规则摘要，细节都在这里。

---

## 1. 事故速览（六次 run 的真实形态）

| 提交 | 红的 job | 卡在哪一步 | 一句话原因 |
|---|---|---|---|
| `c63c989` | lint / test / contract | `uv sync` | runner 装了 Python 3.14 |
| `45010eb` | 同上 | `uv sync` | 只换了 pip 源，没钉 Python |
| `357354d` | test / contract | pytest **收集期** | 141 条 `No module named 'core.config'` |
| `c969f36` | test | pytest **运行期** | 1 例真连 `127.0.0.1:19531` |
| `78b6909` | test | pytest **运行期** | 17 例 `Missing credentials` |
| `5482b03` | test（pytest 已过） | `ratchet.py coverage` | 覆盖率 80.2867 < 基线 80.3807 |
| `a04aac8` | — | — | ✅ 全绿 |

**注意两个容易误读的现象：**

1. **frontend job 自始至终是绿的** —— 它走 npm，不碰 Python，所以不受这些影响。
   「四项全红」的观感来自：**lint / test / contract 共享 `uv sync` 这个前置步骤**，一处装不上就三个 job 同时红。
2. **「pytest 大量报错」和「pytest 只报十几条」是不同层**：
   前者是**收集期**（一个 import 问题复制到 141 个文件），后者是**运行期**（真的跑起来了、有具体断言/构造失败）。

---

## 2. 四层根因（逐层剥开）

### 第 1 层：解释器版本没被固定 → `uv sync` 失败

- **现象**：`error: the configured Python interpreter version (3.14) is newer than PyO3's maximum supported version (3.13)`
  + `hint: tiktoken (v0.8.0) was included because ztb-demo depends on tiktoken`
- **根因**：`pyproject.toml` 写的是 `requires-python = ">=3.11"` —— **只有下界没有上界**。
  `uv.lock` 锁死了 **157 个包的版本**，但**没锁解释器**；干净机器上 uv 会挑最新可用的 Python（3.14），
  而 `tiktoken 0.8.0` 没有 cp314 预编译 wheel → 回退源码编译（Rust / PyO3 0.22 上限 3.13）→ `cargo rustc` 失败。
- **为什么本地绿**：本机是 3.11（`.venv`），永远走 wheel 分支，根本走不到编译。
- **修法**：CI 三个 Python job 加 `UV_PYTHON: "3.12"`（`357354d`）。
- **仍未根治**：`requires-python` 无上界 —— 见 §7。

### 第 2 层：导入路径靠「怎么调用 pytest」隐含决定 → 141 条收集期错误

- **现象**：141 个测试文件在 **collection** 阶段 `ModuleNotFoundError: No module named 'core.config'`
- **根因**：CI 用 `uv run pytest`（**console script**），它**不把 CWD 放进 `sys.path`**；
  pytest 的 `prepend` 导入模式于是把 `test/` 插到最前 → `import core` 命中 **`test/core/`**
  （没有 `__init__.py`，但仍是 namespace 包）→ `core.config` 不存在。
- **为什么本地绿**：`scripts/ci/local_ci.py` 用的是 `python -m pytest`，CWD 天然在 `sys.path` 里。
- **修法**：`pyproject.toml` 的 `[tool.pytest.ini_options]` 加 `pythonpath = ["."]`（`c969f36`），
  让两种调用方式行为一致。

### 第 3 层：测试不 hermetic → 1 例连真 Milvus + 17 例缺凭据

- **现象 A（1 例）**：`test_subgraph_blank_question_friendly` 报
  `MilvusException: Fail connecting to server on 127.0.0.1:19531`
- **根因 A**：用例只打桩了 `kb_search`，而 `_retrieve_node` 按 `settings.kb_light_decompose`
  **二选一**，该开关**默认 True** → 实际走 `kb_search_expanded` → 真去连 Milvus。
  （仓库自己早在 `test/_support/fakes.py:125` 把这个坑写下来了，还有 `KB_SEARCH_ENTRY_POINTS`
  这个常量；本用例没用它。）
- **为什么本地绿**：本机 Milvus 在跑，真检索"恰好"返回、用例照样过 —— **方向相反的依赖**：
  服务不在时才红，服务在时反而绿。
- **修法**：用 `KB_SEARCH_ENTRY_POINTS` 把**两个入口一起打桩**（`78b6909`）。

- **现象 B（17 例）**：`openai.OpenAIError: Missing credentials. Please pass an api_key ...`
- **根因 B**：`ChatOpenAI` / `OpenAIEmbeddings` 在**构造期**就要 api_key；这批用例没有打桩也没有占位凭据。
- **为什么本地绿**：本机 `.env` 里有真 key，构造期永不报错 —— 而 `.env` 被 gitignore，CI 里**不存在**。
- **修法**：`test/conftest.py` 的 `_hermetic_backends` 里补占位凭据
  （`LLM_API_KEY` / `EMBEDDING_API_KEY` / `OPENAI_API_KEY`，**只在缺失时补**，不覆盖本地真 key）（`5482b03`）。

### 第 4 层：质量门禁对跨平台噪声零容忍 → 覆盖率棘轮失败

- **现象**：`! percent 80.2867 （基线 80.3807）` → `[X] coverage 棘轮触发`
- **根因**：基线 `80.3807` 是在 **Windows/py3.11** 录的；**Linux/py3.12** 同一份代码只有 `80.2867`，
  差 **0.09pp**，来源是 `sys.platform` 分支的代码在另一个平台不可达。而 `ratchet.py` 原本零容差。
- **修法**：`scripts/ci/ratchet.py` 给 `coverage` 加 **0.15pp 容差**；
  同时保证「变好」仍对照原始基线（否则 80.2867 会被误报成"变好了"），
  且真实回退仍会拦（用 −0.5pp 的假报告验证过 → `exit 1`）（`a04aac8`）。

---

## 3. 给 AI 的硬规则（改代码/配置/测试前对照）

| # | 规则 | 违反的后果 |
|---|---|---|
| **R1** | **解释器版本必须显式钉**：CI 用 `UV_PYTHON`；本地/Dockerfile/CI 三处保持一致 | 依赖在最新 Python 上没有 wheel → 源码编译 → 失败 |
| **R2** | **导入路径靠配置，不靠调用方式**：保留 `pythonpath = ["."]` | `pytest` 与 `python -m pytest` 行为不同，CI 上大面积收集失败 |
| **R3** | **测试不得依赖本机环境**：不读 `.env`、不连 `127.0.0.1`、模型客户端一律占位凭据 + mock | 本地绿、CI 红；更糟的是「服务在时反而绿」 |
| **R4** | **门禁阈值跨平台要留容差**，但必须能拦住真实回退 | 每次换 OS/Python 版本都会假报警 |
| **R5** | **本地不等价：推之前跑两种调用方式 + 藏起 `.env` 跑一遍** | 把 CI 当第一个"真环境"来发现常识性错误，代价高 |
| **R6** | **GitHub 的 Re-run 只验抖动**：它固定重跑当时那个提交 | 会误判"明明改好了还是红" |

R3 的仓库出处（写代码前先读）：
- `test/conftest.py` 头注释 —— 网络闸门为什么必须在第一个 fixture
- `test/_support/fakes.py:125` —— 为什么必须一次补两个检索入口
- `test/conftest.py::_hermetic_backends` —— 后端/凭据为什么必须钉死

---

## 4. 推之前的本地等价自检（三条）

```powershell
# ① 两种调用方式都要过（CI 用前者，local_ci 用后者）
.venv\Scripts\python.exe -m pytest test/ -q --no-cov
uv run --no-sync pytest test/ -q --no-cov

# ② 藏起 .env（等价 CI 里没有 .env）再跑一遍
Rename-Item .env .env.hidden
.venv\Scripts\python.exe -m pytest test/ -q --no-cov
Rename-Item .env.hidden .env

# ③ 本地门禁全跑（= CI 的 4 个 job）
.venv\Scripts\python.exe scripts/ci/local_ci.py
```

若要**忠实复现 CI 的失败**（读不到 CI 日志时用这招）：

```powershell
# 容器 + 把 .env 挂成空文件 + 与 ci.yml 完全相同的参数
docker run --rm -v "${PWD}:/app" -v "<空文件>:/app/.env:ro" -w /app `
  -e UV_PYTHON=3.12 -e UV_DEFAULT_INDEX=https://pypi.org/simple `
  -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_CACHE_DIR=/cache `
  ghcr.io/astral-sh/uv:python3.12-bookworm-slim `
  sh -c "uv sync --frozen --group dev -q && uv run --no-sync pytest test/ -q --cov=..."
```

**注意**：只挂载仓库而**不**遮住 `.env`，还原度不够 —— 17 例 `Missing credentials` 会继续被掩盖。

---

## 5. 判定树：CI 红了先看哪一步

```
uv sync 失败？
├─ 是 → 先看 runner 的 Python 版本（requires-python 有没有上界？CI 有没有钉 UV_PYTHON？）
│        再看包有没有该 Python 的 wheel（tiktoken/PyO3 这类 Rust 扩展最容易出事）
└─ 否 → pytest 收集期错误？
        ├─ 是 → 大概率是导入路径：pytest vs python -m、test/ 遮蔽同名包（pythonpath 是否有？）
        └─ 否 → pytest 运行期错误？
                ├─ 是 → 看错在「连不上某服务」还是「缺凭据」→ 都是测试不 hermetic
                └─ 否 → 门禁阈值（覆盖率/风格棘轮）→ 看是不是跨平台噪声
```

---

## 6. 反面教材（我这轮犯的两个诊断错误，值得记住）

1. **把「阿里云镜像在境外不可达」当成第一因**。
   实际配置里确实有 `[[tool.uv.index]] mirrors.aliyun.com`（历史提交引入），
   但真因是 Python 3.14 —— 换镜像源完全没解决问题，白跑一轮 CI。
   **教训：拿不到日志时，不要用"最像的原因"下结论。**

2. **第一次容器复现把整个仓库（含 `.env`）挂了进去**。
   于是 17 例缺凭据被 `.env` 掩盖，只看到 1 例失败 → 修完再跑 CI 还是红 → 再剥一层。
   **教训：复现 CI 必须同时复现「没有 `.env`」这一条件。**

---

## 7. 尚未根治的一条（建议尽快做）

```toml
# pyproject.toml
requires-python = ">=3.11,<3.13"   # 收窄上界，别让任何人/任何机器再装到 3.14
```

改这个要重新 `uv lock`（会动 `uv.lock`，动作较大），所以本轮没顺手做。
**它才是「从根上不再复发」的那一步** —— 目前的 CI 靠 yaml 钉 3.12，但本机、Dockerfile、
以及未来任何人执行 `uv sync` 仍可能碰到 3.14。
