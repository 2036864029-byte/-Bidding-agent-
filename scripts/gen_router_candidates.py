"""router 金标候选生成（E1-3）— LLM few-shot 造题 + 主动学习过滤。

对每个意图：用 registry 的 route_tool_doc + examples 做 few-shot，LLM 批量生成
口语化问句（实体用 XX公司/某项目 等占位）。生成后跑当前 router → 标记 match/mismatch
（mismatch 即主动学习要人工优先核对的分歧样本）。

门禁语义（run_router_intent_eval.py）：expected 为人工粗标签，核心指标是
改动前后逐题分类一致 + 准确率不降。故候选集重点是**覆盖各意图的多样问法**。

用法：
    python scripts/gen_router_candidates.py --target 50    # 每意图 50 条
    python scripts/gen_router_candidates.py --write        # 合并写回金标
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

GOLDEN = PROJECT_ROOT / "eval" / "router_intent_set.json"
CAND = PROJECT_ROOT / "eval" / "router_candidates.jsonl"
REVIEW = PROJECT_ROOT / "eval" / "router_review.jsonl"

INTENTS = ["knowledge_qa", "price_inquiry", "general_chat", "doc_qa", "fallback", "deep_agent"]

_FALLBACK_DOC = (
    "兜底引导：意图无法确定、模棱两可时使用。\n"
    "**注意**：必须是与招投标法规知识、企业/项目业务数据、文档分析完全无关的"
    "生活/娱乐/情感/时事/计算类问题（如天气、写诗、算数、情感倾诉、人生建议、"
    "无关生活常识）。**不要**生成与公司工商信息、法定代表人、企业风险、产品/"
    "行业相关的问句（那些属于 price_inquiry），也不要生成文档/合同解读类（doc_qa）"
    "或问候闲聊（general_chat——除非是明显无法归类的）。"
)


def _registry_specs():
    from agent.registry import CapabilityRegistry, _seed_deep_agent, _seed_doc_agent, _seed_echo_demo, seed_builtins
    from dataclasses import replace

    reg = CapabilityRegistry()
    seed_builtins(reg)
    for spec in (_seed_echo_demo(), _seed_doc_agent(), replace(_seed_deep_agent(), enabled=True)):
        reg.register(spec)
    return {s.id: s for s in reg.all()}


def _gen_llm():
    """生成用 LLM：deepseek-chat，temperature 稍高保证多样。"""
    from core.config.settings import Settings
    from core.llm.deepseek import DeepSeekProvider

    settings = Settings()
    return DeepSeekProvider(settings).build(temperature=0.8)


def _prompt_for(intent_id: str, spec) -> str:
    if intent_id == "fallback":
        doc = _FALLBACK_DOC
        examples = "今天天气怎么样？\n1+1等于几\n帮我写一封入党申请书\n"
    else:
        doc = spec.route_tool_doc or spec.route_desc
        examples = "\n".join(spec.examples or [])
    return (
        f"你是招投标智能助手的路由意图造题器。请为意图「{intent_id}」生成一批"
        f"用户可能输入的口语化问句。\n\n该意图的触发规则：\n{doc}\n\n"
        f"参考示例：\n{examples or '（无）'}\n\n"
        "要求：\n"
        "- 问句自然、口语化，贴近真实用户提问\n"
        "- 实体/对象用「XX公司」「某公司」「某项目」「某文件」等占位符（不要用真实具体名称）\n"
        "- 每条一行，不要编号、不要解释，只输出问句本身\n"
        "- 问法要多样（陈述式、疑问式、省略式、场景式），避免与示例雷同"
    )


def _generate(llm, intent_id: str, spec, n: int, seed: int = 42) -> list[str]:
    """批量生成 n 条问句（每次调 LLM 出 8 条，凑够 n）。"""
    rng = random.Random(seed)
    batch = 8
    prompt_tpl = _prompt_for(intent_id, spec)
    out: list[str] = []
    while len(out) < n:
        text = llm.invoke(f"{prompt_tpl}\n请生成 {batch} 条。\n").content
        for line in text.splitlines():
            line = line.strip().lstrip("0123456789.、）) ")
            if line and len(line) >= 4 and not line.startswith("请生成"):
                out.append(line)
        out = list(dict.fromkeys(out))  # 去重保序
        if len(out) == 0:
            break  # 防死循环
    rng.shuffle(out)  # 打散，写盘顺序随机
    return out[:n]


def _route_questions(qs: list[str]) -> dict[str, str]:
    """用当前 router 跑分类（主动学习过滤）。"""
    from agent.router import build_router_node
    from core.config.settings import Settings
    from core.llm.registry import create_llm_for_slot
    from langchain_core.messages import HumanMessage

    from run_router_intent_eval import build_registry

    llm = create_llm_for_slot("router", Settings())
    reg = build_registry(enable_deep_agent=False)   # 线上默认：deep_agent 关
    node = build_router_node(llm, registry=reg)
    got: dict[str, str] = {}
    for i, q in enumerate(qs, 1):
        got[q] = node({"messages": [HumanMessage(content=q)]}).get("router_intent", "fallback")
        if i % 50 == 0:
            print(f"  ...router 分类 {i}/{len(qs)}", flush=True)
    return got


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=50, help="每启用意图目标条数")
    parser.add_argument("--deep-agent-target", type=int, default=10,
                        help="deep_agent 目标条数（禁用意图，恒 mismatch，少量即可）")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    specs = _registry_specs()
    llm = _gen_llm()
    print(f"意图: {INTENTS}")

    # ① LLM 生成
    all_cands: list[dict] = []
    for intent_id in INTENTS:
        spec = specs.get(intent_id)
        n = args.target if intent_id != "deep_agent" else args.deep_agent_target
        qs = _generate(llm, intent_id, spec, n, seed=args.seed)
        for q in qs:
            all_cands.append({"q": q, "expected": intent_id, "source": "llm-fewshot"})
        print(f"  生成 {intent_id}: {len(qs)} 条", flush=True)

    # ② 跑当前 router（主动学习）
    print("\n② 跑当前 router 分类…", flush=True)
    got = _route_questions([c["q"] for c in all_cands])
    for c in all_cands:
        c["router_got"] = got[c["q"]]
        c["match"] = c["router_got"] == c["expected"]

    # ③ 覆盖率报告
    from collections import Counter
    cnt = Counter((c["expected"], c["match"]) for c in all_cands)
    print("\n③ 每意图 match/mismatch:")
    for k in sorted(cnt):
        print(f"   {k}: {cnt[k]}")

    CAND.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in all_cands) + "\n",
                    encoding="utf-8")
    REVIEW.write_text(
        "\n".join(json.dumps({**c, "review_ok": None}, ensure_ascii=False) for c in all_cands) + "\n",
        encoding="utf-8")
    print(f"\n候选已写: {CAND}（{len(all_cands)} 条）")
    print(f"待复核: {REVIEW}（含 router 分类与 match 标记，mismatch 优先人工核对）")

    if args.write:
        old = json.loads(GOLDEN.read_text(encoding="utf-8"))
        existing = {i["q"] for i in old["items"]}
        added = [{"q": c["q"], "expected": c["expected"]} for c in all_cands if c["q"] not in existing]
        old["items"] += added
        GOLDEN.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"合并写回 {GOLDEN} → 共 {len(old['items'])} 条（新增 {len(added)}）")


if __name__ == "__main__":
    main()
