"""POST /inquiry/reference 查询层单测（mock Milvus + mock bidding 库，零网络）。

覆盖（方案 §9）：金额万元换算、≤1000 过滤、products 分组、相似度排序、
400/503 语义、地域过滤、输入过短。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import inquiry_reference as ir
from api.inquiry_reference import search_reference
from api.server import create_app
import price_engine.reference as ref
from core.vectorstore.namespace import PROJECT_ANNOUNCEMENT_COLLECTION


# ── 假 Milvus / 假 MySQL ──────────────────────────────────────────

class _FakeHit:
    """Milvus search 单条命中（score + entity）。"""

    def __init__(self, aid: str, score: float) -> None:
        self.score = score
        self.entity = {"announcement_id": aid}


class FakeMilvus:
    """脚本化 MilvusClient：has_collection / search / get_collection_stats。"""

    def __init__(self, hits: list[_FakeHit] | None = None, *, ready: bool = True) -> None:
        self.hits = hits or []
        self.ready = ready
        self.last_search_kwargs: dict = {}

    def has_collection(self, name: str) -> bool:
        return self.ready

    def get_collection_stats(self, name: str) -> dict:
        return {"row_count": len(self.hits) if self.ready else 0}

    def search(self, collection, data, anns_field, search_params, limit, filter, output_fields):
        self.last_search_kwargs = {
            "collection": collection,
            "limit": limit,
            "filter": filter,
            "search_params": search_params,
        }
        return [self.hits]


class _FakeCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def execute(self, sql: str, params=()) -> None:
        self._sql = sql

    def fetchall(self) -> list[tuple]:
        return self._rows


class _FakeConn:
    """脚本化连接：按 SQL 关键字分发 announcement / product 行集。"""

    def __init__(self, announcements: list[tuple], products: list[tuple]) -> None:
        self.announcements = announcements
        self.products = products

    def cursor(self):
        class _Ctx:
            def __enter__(s):
                return _FakeCursor([])

            def __exit__(s, *a):
                return False

        return _Ctx()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_conn(
    announcements: list[tuple] | None = None,
    products: list[tuple] | None = None,
) -> _FakeConn:
    """带记录 execute 的连接：按 SQL 内容返回对应行集。"""
    ann = announcements or []
    prod = products or []

    class _RecordingCursor(_FakeCursor):
        def __init__(self, rows):
            super().__init__(rows)

        def execute(self, sql, params=()):
            self._sql = sql
            self._params = params

        def fetchall(self):
            if "FROM project_product_item" in self._sql:
                return prod
            if "FROM announcement" in self._sql:
                # 模拟 SQL 层金额过滤：award_amount（下标 5）> floor（params[-1]）
                floor = float(self._params[-1])
                return [r for r in ann if float(r[5] or 0) > floor]
            return []

    class _RecordingConn(_FakeConn):
        def cursor(self):
            class _Ctx:
                def __enter__(s):
                    return _RecordingCursor([])

                def __exit__(s, *a):
                    return False

            return _Ctx()

    return _RecordingConn(ann, prod)


# ── 纯函数 ────────────────────────────────────────────────────────

class TestParsePrice:
    def test_plain_number(self):
        assert ir._parse_price("152000") == 152000

    def test_thousands_separator(self):
        assert ir._parse_price("1,586,000.00") == 1586000

    def test_multi_value_takes_first(self):
        # 多值（中文逗号分隔）只取首段
        assert ir._parse_price("601990.00，326869.00，496680.00") == 601990.0

    def test_decimal_kept(self):
        assert ir._parse_price("152000.50") == 152000.5

    def test_none_and_garbage(self):
        assert ir._parse_price(None) is None
        assert ir._parse_price("") is None
        assert ir._parse_price("面议") is None


class TestAmountStats:
    def test_basic(self):
        stats = ir._amount_stats([100.0, 200.0, 300.0])
        assert stats == {
            "min_wan": 100.0, "median_wan": 200.0,
            "max_wan": 300.0, "avg_wan": 200.0, "sample_count": 3,
        }

    def test_even_median(self):
        assert ir._amount_stats([1.0, 2.0, 3.0, 4.0])["median_wan"] == 2.5

    def test_empty_returns_none(self):
        assert ir._amount_stats([]) is None


# ── search_reference 流程（mock Milvus + mock DB）──────────────────

def _settings():
    from core.config.settings import Settings

    return Settings()


class TestSearchReference:
    def test_text_too_short(self, monkeypatch):
        with pytest.raises(ValueError):
            search_reference(_settings(), "太短")

    def test_collection_not_ready(self, monkeypatch):
        monkeypatch.setattr(ref, "_milvus_client", lambda s: FakeMilvus(ready=False))
        with pytest.raises(LookupError):
            search_reference(_settings(), "采购监控摄像头项目招标概况")

    def test_full_flow_wan_and_sorting(self, monkeypatch):
        hits = [
            _FakeHit("A1", 0.82),
            _FakeHit("A2", 0.71),
            _FakeHit("A3", 0.35),
            _FakeHit("A4", 0.28),  # 低于阈值 0.30 → 应被丢弃
        ]
        monkeypatch.setattr(ref, "_milvus_client", lambda s: FakeMilvus(hits))
        monkeypatch.setattr(
            ref, "get_embeddings",
            lambda s: type("E", (), {"embed_query": lambda self, t: [0.0] * 1024})(),
        )
        # 回表结果与 Milvus 顺序不同，验证按 score 重排 + 金额过滤
        conn = _make_conn(
            announcements=[
                ("A2", "PNO2", "项目二", "采购人B", "北京市", 8_000_000.0, None, "https://ccgp.gov.cn/2"),
                ("A3", "PNO3", "项目三", "采购人C", "广东省", 500.0, None, "https://ccgp.gov.cn/3"),  # ≤1000 脏行 → 过滤
                ("A1", "PNO1", "项目一", "采购人A", "新疆维吾尔自治区", 2_000_000.0, None, "https://ccgp.gov.cn/1"),
            ],
            products=[
                ("A1", "监控摄像机", "监控摄像机", "海康", "DS-2CD", "10", "台", "1500"),
                ("A1", "硬盘录像机", "硬盘录像机", None, "DS-7608", "1", "台", "2500"),
                ("A2", "摄像头", "摄像头", "大华", None, "5", "台", "800"),
            ],
        )
        monkeypatch.setattr(ref, "_get_connection", lambda db: conn)
        monkeypatch.setattr(ref, "_release_connection", lambda c: None)

        result = search_reference(_settings(), "采购120台监控摄像头用于校园安防", top_k=5)

        assert [p["announcement_id"] for p in result["projects"]] == ["A1", "A2"]
        assert result["projects"][0]["winning_amount_wan"] == 200.0  # 2,000,000 元 → 万元
        assert result["projects"][1]["winning_amount_wan"] == 800.0
        # products 分组
        assert [p["product_name"] for p in result["projects"][0]["products"]] == [
            "监控摄像机", "硬盘录像机",
        ]
        assert result["projects"][0]["products"][0]["unit_price_yuan"] == 1500
        # 金额统计（过滤后 2 条）
        assert result["amount_stats"]["sample_count"] == 2
        assert result["amount_stats"]["min_wan"] == 200.0
        assert result["amount_stats"]["max_wan"] == 800.0
        assert result["total"] == 2

    def test_no_hits_above_threshold(self, monkeypatch):
        monkeypatch.setattr(ref, "_milvus_client", lambda s: FakeMilvus([_FakeHit("A1", 0.10)]))
        result = search_reference(_settings(), "采购120台监控摄像头用于校园安防")
        assert result["total"] == 0
        assert result["projects"] == []
        assert result["amount_stats"] is None

    def test_region_passed_as_filter(self, monkeypatch):
        client = FakeMilvus([_FakeHit("A1", 0.75)])
        monkeypatch.setattr(ref, "_milvus_client", lambda s: client)
        monkeypatch.setattr(
            ref, "get_embeddings",
            lambda s: type("E", (), {"embed_query": lambda self, t: [0.0] * 1024})(),
        )
        conn = _make_conn(
            announcements=[("A1", "PNO", "项目", "采购人", "新疆维吾尔自治区",
                            5_000_000.0, None, "https://ccgp.gov.cn/1")],
        )
        monkeypatch.setattr(ref, "_get_connection", lambda db: conn)
        monkeypatch.setattr(ref, "_release_connection", lambda c: None)

        search_reference(_settings(), "采购监控摄像头项目招标概况", region="新疆维吾尔自治区")
        assert client.last_search_kwargs["filter"] == 'region == "新疆维吾尔自治区"'

    def test_min_amount_wan_raises_floor(self, monkeypatch):
        hits = [_FakeHit("A1", 0.9), _FakeHit("A2", 0.8)]
        monkeypatch.setattr(ref, "_milvus_client", lambda s: FakeMilvus(hits))
        monkeypatch.setattr(
            ref, "get_embeddings",
            lambda s: type("E", (), {"embed_query": lambda self, t: [0.0] * 1024})(),
        )
        seen_floor = {}

        class _Cur:
            def execute(self, sql, params=()):
                self._sql = sql
                self._params = params
                if "FROM announcement" in sql:
                    seen_floor["floor"] = params[-1]

            def fetchall(self):
                if "FROM announcement" in self._sql:
                    return [("A1", "PNO", "项目", "采购人", "新疆", 20_000_000.0, None, "https://x")]
                return []

        class _Conn:
            def cursor(self):
                class _Ctx:
                    def __enter__(s):
                        return _Cur()

                    def __exit__(s, *a):
                        return False

                return _Ctx()

        monkeypatch.setattr(ref, "_get_connection", lambda db: _Conn())
        monkeypatch.setattr(ref, "_release_connection", lambda c: None)

        search_reference(_settings(), "采购120台监控摄像头用于校园安防", min_amount_wan=1000.0)
        assert seen_floor["floor"] == 10_000_000.0  # 1000 万 → 1e7 元


# ── 端点级（create_app + TestClient）──────────────────────────────

def _app_client(monkeypatch) -> TestClient:
    from api.server import create_app

    return TestClient(create_app(get_agent_fn=lambda: None))


class TestReferenceEndpoint:
    def test_400_short_text(self, monkeypatch):
        client = _app_client(monkeypatch)
        resp = client.post("/inquiry/reference", json={"text": "太短"})
        assert resp.status_code == 400
        assert "至少 10 字" in resp.json()["detail"]

    def test_503_collection_not_ready(self, monkeypatch):
        monkeypatch.setattr(ref, "_milvus_client", lambda s: FakeMilvus(ready=False))
        client = _app_client(monkeypatch)
        resp = client.post(
            "/inquiry/reference", json={"text": "采购120台监控摄像头用于校园安防项目"}
        )
        assert resp.status_code == 503
        assert "build_announcement_index.py" in resp.json()["detail"]

    def test_200_flow(self, monkeypatch):
        # 端点在 server.py 命名空间里绑定的是 `search_reference`，需 patch 那里
        import api.server as server_mod

        monkeypatch.setattr(
            server_mod, "search_reference",
            lambda settings, text, **kw: {
                "query": text,
                "total": 1,
                "amount_stats": {"min_wan": 10.0, "median_wan": 10.0,
                                 "max_wan": 10.0, "avg_wan": 10.0, "sample_count": 1},
                "projects": [{"announcement_id": "A1", "project_no": "P",
                              "project_name": "项目", "purchaser": "采购人",
                              "region": "新疆", "winning_amount_wan": 10.0,
                              "winning_date": "2026-09-11", "source_url": "https://x",
                              "score": 0.8, "products": []}],
            },
        )
        client = _app_client(monkeypatch)
        resp = client.post(
            "/inquiry/reference",
            json={"text": "采购120台监控摄像头用于校园安防项目", "top_k": 5},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["projects"][0]["winning_amount_wan"] == 10.0
        assert body["projects"][0]["source_url"] == "https://x"
