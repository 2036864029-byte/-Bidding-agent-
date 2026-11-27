"""把中国省界 GeoJSON 固化成前端可直接 import 的 TS 模块（构建期一次性生成）。

为什么不在运行时拉 GeoJSON：原始 458 KB，只为画一张图拉这个不值得，而且多一次
网络依赖。这里用固定参数投影 + 简化，生成精简 SVG path 入库。

用法：
    python scripts/gen_china_paths.py                # 缺数据文件时自动下载
    python scripts/gen_china_paths.py --geojson x.json

数据来源：阿里 DataV 行政区划边界（100000_full，省级）。
生成物：frontend/src/components/kanban/chinaProvinces.ts

⚠️ 生成参数（投影/容差/viewBox）与 `ChinaMapHeatmap.tsx` 里的 DEPTH、标注坐标
必须保持一致，改这里要同步改组件，否则标注框会飘。
"""

from __future__ import annotations

import argparse
import json
import math
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_GEOJSON = REPO / "frontend" / ".cache" / "china-provinces.geojson"
SRC_URL = "https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json"
OUT_TS = REPO / "frontend" / "src" / "components" / "kanban" / "chinaProvinces.ts"

# ── 投影与画布参数（与组件共享同一坐标系）────────────────────
VB_W, VB_H = 960, 560
EPAD = 22.0
DEPTH_RESERVE = 20.0     # 给挤出厚度留的底部空间
RDP_TOL = 0.45
SMALL_RING_SKIP = 40     # 点数少于此的环不简化（港澳等小岛）
MIN_LAT = 17.0           # 低于此纬度的环视为南海诸岛，丢弃

PHI1, PHI2 = math.radians(25), math.radians(47)
LMBD0 = math.radians(105)

SHORT = {
    "广西壮族自治区": "广西", "宁夏回族自治区": "宁夏", "新疆维吾尔自治区": "新疆",
    "内蒙古自治区": "内蒙古", "西藏自治区": "西藏", "香港特别行政区": "香港",
    "澳门特别行政区": "澳门",
}


def short(name: str) -> str:
    if name in SHORT:
        return SHORT[name]
    for suf in ("特别行政区", "省", "市"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def albers(lon: float, lat: float) -> tuple[float, float]:
    """Albers 等积投影，输出已翻成 SVG 坐标（y 向下）。

    数学坐标 y 向上（北为正）；直接喂给 SVG 会把中国整个上下颠倒。
    """
    lam, phi = math.radians(lon), math.radians(lat)
    n = (math.sin(PHI1) + math.sin(PHI2)) / 2
    c = math.cos(PHI1) ** 2 + 2 * n * math.sin(PHI1)
    rho = math.sqrt(c - 2 * n * math.sin(phi)) / n
    rho0 = math.sqrt(c) / n
    theta = n * (lam - LMBD0)
    return rho * math.sin(theta), -(rho0 - rho * math.cos(theta))


def rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Douglas-Peucker 简化。首尾重合时按"离起点最远的点"劈开，避免整条环被压成 2 点。"""
    if len(points) < 3:
        return points
    x1, y1 = points[0]
    x2, y2 = points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy)
    if norm == 0:
        far = max(
            range(1, len(points)),
            key=lambda i: math.hypot(points[i][0] - x1, points[i][1] - y1),
        )
        if far <= 1:
            return [points[0], points[-1]]
        return rdp(points[: far + 1], eps)[:-1] + rdp(points[far:], eps)
    best_i, best_d = 0, -1.0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = abs(dy * (px - x1) - dx * (py - y1)) / norm
        if d > best_d:
            best_i, best_d = i, d
    if best_d <= eps:
        return [points[0], points[-1]]
    return rdp(points[: best_i + 1], eps)[:-1] + rdp(points[best_i:], eps)


def rings_of(geom: dict) -> list[list[list[list[float]]]]:
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def load_geojson(path: Path) -> dict:
    if not path.exists():
        print(f"未找到 {path}，从 DataV 下载……")
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(SRC_URL, timeout=60) as resp:
            path.write_bytes(resp.read())
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geojson", default=str(DEFAULT_GEOJSON))
    args = ap.parse_args()

    raw = load_geojson(Path(args.geojson))
    feats = [f for f in raw["features"] if f["properties"].get("name")]

    provinces, xs, ys = [], [], []
    for f in feats:
        name = f["properties"]["name"]
        rings = []
        for poly in rings_of(f["geometry"]):
            for ring in poly:
                if max(lat for _lon, lat in ring) < MIN_LAT:
                    continue
                pts = [albers(lon, lat) for lon, lat in ring]
                if len(pts) > 2:
                    rings.append(pts)
                    xs.extend(p[0] for p in pts)
                    ys.extend(p[1] for p in pts)
        c = f["properties"].get("centroid") or f["properties"].get("center")
        provinces.append({
            "name": name,
            "rings": rings,
            "centroid": albers(*c) if c else None,
        })

    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    scale = min(
        (VB_W - 2 * EPAD) / (maxx - minx),
        (VB_H - 2 * EPAD - DEPTH_RESERVE) / (maxy - miny),
    )
    offx = (VB_W - (maxx - minx) * scale) / 2 - minx * scale
    offy = (VB_H - DEPTH_RESERVE - (maxy - miny) * scale) / 2 - miny * scale

    def to_px(p: tuple[float, float]) -> tuple[float, float]:
        return p[0] * scale + offx, p[1] * scale + offy

    rows, total_pts = [], 0
    for p in provinces:
        parts = []
        for ring in p["rings"]:
            pts = [to_px(q) for q in ring]
            while (
                len(pts) > 3
                and abs(pts[0][0] - pts[-1][0]) < 1e-9
                and abs(pts[0][1] - pts[-1][1]) < 1e-9
            ):
                pts = pts[:-1]
            if len(pts) < 3:
                continue
            if len(pts) >= SMALL_RING_SKIP:
                pts = rdp(pts, RDP_TOL)
            if len(pts) < 3:
                continue
            total_pts += len(pts)
            parts.append(
                f"M{pts[0][0]:.1f} {pts[0][1]:.1f}"
                + "".join(f"L{x:.1f} {y:.1f}" for x, y in pts[1:])
                + "Z"
            )
        d = "".join(parts)
        if not d:
            continue
        cx = cy = None
        if p["centroid"]:
            cx, cy = to_px(p["centroid"])
        rows.append((p["name"], short(p["name"]), d, cx, cy))

    lines = [
        "/* 本文件由 scripts/gen_china_paths.py 生成，请勿手改。",
        " *",
        " * 数据来源：阿里 DataV 100000_full（省级边界），Albers 等积投影",
        " * （标准纬线 25°/47°，中央经线 105°，y 轴已翻成 SVG 方向），",
        f" * RDP 容差 {RDP_TOL}px 简化。坐标系 viewBox {VB_W}×{VB_H}；",
        " * 挤出厚度由组件渲染时叠加，不在 path 里。",
        " *",
        " * 为什么固化：原始 GeoJSON 458 KB，运行时拉取不划算，也多一次网络依赖。",
        f" * 重跑：python scripts/gen_china_paths.py（缺数据会自动下载）",
        " */",
        "",
        "export interface ChinaProvince {",
        "  /** 全名（与接口返回的 region 字段一致，如「广西壮族自治区」） */",
        "  name: string;",
        "  /** 简称（标注用，如「广西」） */",
        "  short: string;",
        f"  /** SVG path（viewBox {VB_W}×{VB_H}） */",
        "  d: string;",
        "  /** 省中心（标注锚点用）；无质心数据时为 null */",
        "  cx: number | null;",
        "  cy: number | null;",
        "}",
        "",
        f"export const CHINA_VIEW_BOX = '0 0 {VB_W} {VB_H}';",
        "",
        "export const CHINA_PROVINCES: ChinaProvince[] = [",
    ]
    for name, sh, d, cx, cy in rows:
        cxs = "null" if cx is None else f"{cx:.1f}"
        cys = "null" if cy is None else f"{cy:.1f}"
        lines.append(
            f"  {{ name: '{name}', short: '{sh}', cx: {cxs}, cy: {cys}, d: '{d}' }},"
        )
    lines += ["];", ""]
    OUT_TS.parent.mkdir(parents=True, exist_ok=True)
    OUT_TS.write_text("\n".join(lines), encoding="utf-8")

    print(f"provinces: {len(rows)}  points: {total_pts}")
    print(f"wrote {OUT_TS.relative_to(REPO)} ({OUT_TS.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
