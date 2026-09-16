"""公司简称 → 字号族解析（修复「杭州海康威视查不到中标」类问题）。

背景（2026-09-16 实测）：用户问「杭州海康威视最近中标了哪些项目？」，LLM 提取
``successful_bidder="杭州海康威视"``（无企业后缀）→ ``_is_valid_company_name`` 判无效
→ 守卫引导。而库中该中标记录的中标方是**福州海康威视数字技术有限公司**（跨地区同字号）——
精确/前缀匹配都对不上，必须做**字号族**匹配（`海康威视` → ``LIKE '%海康威视%'``）。

规则：
  1. 已是有效工商全称 → 不动（走原精确匹配，行为不变）。
  2. 简称（无后缀）→ 先查 ztb_clean.company_info / bid_project 确认前缀存在（防 LLM
     幻觉把「采购/摄像头」当公司名）→ 剥离地区前缀取**字号** → 记入
     ``intent.resolved_families[field]``，SQL 侧按 ``LIKE '%字号%'`` 匹配全家族。
  3. 确认不到 → 不动（走原守卫引导，话术不变）。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from core.rdb.mysql_pool import _get_connection, _release_connection, _get_settings
from .intent import _is_valid_company_name
from .models import SearchIntent

logger = logging.getLogger(__name__)

# 省级行政区 + 常见地级市前缀：公司名「[地区][字号][行业][组织形态]」的地区段。
# 覆盖 34 省级 + 主要城市；未收录的地区简称不剥（字号保留原样，仍可 LIKE 命中）。
_REGION_PREFIXES = (
    # 省级
    "北京", "天津", "上海", "重庆",
    "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东",
    "河南", "湖北", "湖南", "广东", "海南",
    "四川", "贵州", "云南", "陕西", "甘肃", "青海",
    "内蒙古", "广西", "西藏", "宁夏", "新疆",
    "香港", "澳门",
    # 主要城市（副省级 + 省会 + 经济强市）
    "哈尔滨", "长春", "沈阳", "大连", "石家庄", "太原", "呼和浩特",
    "济南", "青岛", "烟台", "威海", "郑州", "洛阳", "西安", "兰州",
    "西宁", "银川", "乌鲁木齐", "拉萨", "成都", "贵阳", "昆明",
    "武汉", "长沙", "南昌", "合肥", "南京", "苏州", "无锡", "常州",
    "南通", "徐州", "扬州", "镇江", "泰州", "盐城", "淮安", "连云港",
    "宿迁", "杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华",
    "衢州", "舟山", "台州", "丽水", "福州", "厦门", "泉州", "漳州",
    "莆田", "宁德", "龙岩", "三明", "南平", "广州", "深圳", "珠海",
    "佛山", "东莞", "中山", "惠州", "汕头", "江门", "茂名", "肇庆",
    "湛江", "清远", "揭阳", "梅州", "韶关", "河源", "阳江", "云浮",
    "潮州", "汕尾", "南宁", "柳州", "桂林", "梧州", "北海", "海口",
    "三亚", "昆明", "贵阳", "南昌",
    # 直辖市辖区等短前缀
    "海淀", "朝阳", "浦东",
)

# 按长度降序，优先剥长地区名（「哈尔滨」优于「哈」）
_REGION_PREFIXES_SORTED = tuple(
    sorted(set(_REGION_PREFIXES), key=len, reverse=True)
)


def strip_region_prefix(name: str) -> str:
    """剥离公司名开头的地区段，返回字号段。

    '杭州海康威视' → '海康威视'；'海康威视' → '海康威视'（无地区不动）；
    '中国移动' → '中国移动'（'中国' 非地区，不动）。
    """
    for region in _REGION_PREFIXES_SORTED:
        if name.startswith(region) and len(name) > len(region):
            return name[len(region):]
    return name


# 解析确认源：简称必须能在这些表里找到前缀，才认为是真实企业字号（防幻觉）。
# 前缀 LIKE 可走索引；各段不加 LIMIT（UNION 后统一 LIMIT 1）。
_CONFIRM_SQL = """
SELECT 1 FROM `company_info` WHERE `company_name` LIKE %s
UNION ALL
SELECT 1 FROM `bid_project` WHERE `successful_bidder` LIKE %s
UNION ALL
SELECT 1 FROM `bid_project` WHERE `purchaser` LIKE %s
LIMIT 1
"""


def _confirm_family_exists(short: str) -> bool:
    """简称是否为真实企业字号前缀（company_info / bid_project 命中其一）。"""
    settings = _get_settings()
    conn = _get_connection(settings.mysql_clean_db)
    if conn is None:
        return False
    try:
        like = short + "%"
        with conn.cursor() as cur:
            cur.execute(_CONFIRM_SQL, (like, like, like))
            return cur.fetchone() is not None
    except Exception as exc:  # noqa: BLE001  确认失败按不存在处理，不影响主流程
        logger.debug("公司字号确认查询失败: %s", exc)
        return False
    finally:
        _release_connection(conn)


def resolve_company_families(intent: SearchIntent) -> str:
    """对 intent 的公司实体字段做字号族解析（就地修改），返回动作描述或 ''。

    - company_name / purchaser / successful_bidder 三个字段都试；
    - 已是有效全称 → 跳过；
    - 简称经确认 → 剥地区取字号 → ``hard_filters[field]=字号`` 且
      ``resolved_families[field]=字号``（SQL 侧按 LIKE 匹配）。
    """
    hf = intent.hard_filters
    changes: list[str] = []

    for field in ("company_name", "purchaser", "successful_bidder"):
        value = getattr(hf, field, None)
        if not value or not isinstance(value, str):
            continue
        value = value.strip()
        if not value:
            continue
        if _is_valid_company_name(value):
            continue  # 已是全称：原精确匹配，行为不变

        if not _confirm_family_exists(value):
            continue  # 非真实企业前缀：不解析，走原守卫引导

        family = strip_region_prefix(value)
        if not family or family == value:
            # 剥不出更短字号（无地区），直接用原简称做 LIKE（如「中国移动」）
            family = value
        setattr(hf, field, family)
        intent.resolved_families[field] = family
        changes.append(f"{field}:{value}→{family}")

    if changes:
        logger.info("[COMPANY_FAMILY_RESOLVE] %s", "; ".join(changes))
    return "; ".join(changes)
