"""非 live 用例的网络闸门 —— 把"静默依赖开发机状态"变成"显式失败 + 修复指引"。

**为什么需要**（2026-09-20 实测的两个真实故障）：两个用例在**不该联网**时联了
`127.0.0.1:18003`（模型隧道）——
  - `test_no_hits_above_threshold` 漏 patch embedder（同文件兄弟用例都 patch 了）
  - `test_real_compiled_subgraph_through_supervisor` patch 错了目标
    （补 `kb_search`，代码实际走 `kb_search_expanded`）

两者表现都是"本机跑隧道就绿、不跑就红"——套件颜色取决于开发机状态而非代码对错。
这类问题**不报错、不告警**，只在环境变化时暴露。加闸门后，第一次运行就暴露。

**为什么手写而不引 pytest-socket**：`pytest-socket` 是白名单式（`--disable-socket`
+ `@pytest.mark.enable_socket`），粒度是"整台机器开/关"，无法对**环回地址**做区分。
而本项目的故障形态全在环回（隧道 18003 / Milvus 19531 / MySQL 3306 / 代理 7890），
白名单会把它们一起放行，等于拦不住。手写版还能给出"该补哪个 mock"的定向提示。

**拦截面**：`socket.socket.connect`（pymysql / 裸 socket）与
`socket.create_connection`（httpx / httpcore）—— 两条都已实测验证。

**必须放行 `socket.socketpair`**（实测踩到的坑）：Windows 上 socketpair 是
`bind + listen + connect` 模拟出来的（`cpython/Lib/socket.py`），而 asyncio 的
`ProactorEventLoop` 用它建 self-pipe。拦 connect 会把事件循环本身打坏 ——
表现为**看似无关的 500**（而非 NetworkBlockedByTest），定位成本极高。
故 socketpair 执行期间临时还原 connect。FastAPI `TestClient` 依赖此路径（anyio portal）。

`TestClient` 本身不发真实 HTTP：它用 httpx 的 ASGITransport，不经过 socket。
"""

from __future__ import annotations

import socket
from typing import Any, Callable, List, Optional, Tuple

#: 提示模板：给出两个可选修法，并指名当前用例。
_HINT = (
    "非 hermetic 用例试图联网 {addr!r}。\n"
    "  修法 1（首选）：补 mock，不必真连 —— 见 test/_support/fakes.py；\n"
    "  修法 2（确需真实服务）：给该用例加 @pytest.mark.integration。\n"
    "  当前用例：{nodeid}"
)

#: 被拦下的联网尝试 `(nodeid, address)`。
#:
#: **为什么"拦了还要记"**：拦截只能暴露"失败得响"的用例。还有一类更隐蔽的 ——
#: 联网失败后走降级路径、断言又恰好成立，于是**测试通过但从未真正验证过**
#: （实测：`test_sub_route.py` 的 `TestIntegration` 断言宽松，MySQL 连不上时
#: 返回空结果照样绿）。记录 + 会话结束汇报能把这类也捞出来。
_BLOCKED_ATTEMPTS: List[Tuple[str, str]] = []


def blocked_attempts() -> List[Tuple[str, str]]:
    """本次会话被拦下的全部联网尝试（供 conftest 的 terminal summary 汇报）。"""
    return list(_BLOCKED_ATTEMPTS)


def clear_blocked_attempts() -> None:
    _BLOCKED_ATTEMPTS.clear()


class NetworkBlockedByTest(RuntimeError):
    """非 live 用例联网 —— 要么补 mock，要么显式标记为 integration。"""


class NetworkGate:
    """可安装/卸载的全局网络闸门（幂等；卸载还原 install 前的原函数）。"""

    def __init__(self) -> None:
        self._real_connect: Optional[Callable] = None
        self._real_create_connection: Optional[Callable] = None
        self._real_socketpair: Optional[Callable] = None
        self._blocked_connect: Optional[Callable] = None

    # ── 安装 / 卸载 ────────────────────────────────────────────

    def install(self, nodeid: str = "") -> None:
        """封锁网络出口。`nodeid` 仅用于失败提示与记录（指明是哪个用例）。"""
        self._real_connect = socket.socket.connect
        self._real_create_connection = socket.create_connection

        def _record_and_raise(address: Any) -> None:
            _BLOCKED_ATTEMPTS.append((nodeid or "(未知)", repr(address)))
            raise NetworkBlockedByTest(
                _HINT.format(addr=address, nodeid=nodeid or "(未知)")
            )

        def _blocked_connect(sock_self: Any, address: Any, *a: Any, **kw: Any) -> Any:
            _record_and_raise(address)

        def _blocked_create_connection(address: Any, *a: Any, **kw: Any) -> Any:
            _record_and_raise(address)

        self._blocked_connect = _blocked_connect
        socket.socket.connect = _blocked_connect
        socket.create_connection = _blocked_create_connection
        self._guard_socketpair()

    def uninstall(self) -> None:
        """还原全部被替换的函数。"""
        if self._real_connect is not None:
            socket.socket.connect = self._real_connect
        if self._real_create_connection is not None:
            socket.create_connection = self._real_create_connection
        if self._real_socketpair is not None:
            socket.socketpair = self._real_socketpair
        self._real_connect = None
        self._real_create_connection = None
        self._real_socketpair = None
        self._blocked_connect = None

    # ── socketpair 特例 ────────────────────────────────────────

    def _guard_socketpair(self) -> None:
        """放行 socketpair 内部的 connect（它是 asyncio self-pipe 的基石）。

        不放行的后果（实测）：Windows 上 socketpair 由 bind/listen/connect 模拟，
        asyncio 的 ProactorEventLoop 建 self-pipe 时走它 —— 被拦后事件循环起不来，
        `TestClient` 全线 500，而报错与"联网"毫无表面关联。
        """
        if not hasattr(socket, "socketpair"):
            return
        self._real_socketpair = socket.socketpair
        real_connect = self._real_connect
        real_socketpair = self._real_socketpair
        blocked_connect = self._blocked_connect

        def _guarded_socketpair(*a: Any, **kw: Any) -> Any:
            socket.socket.connect = real_connect
            try:
                return real_socketpair(*a, **kw)
            finally:
                socket.socket.connect = blocked_connect

        socket.socketpair = _guarded_socketpair
