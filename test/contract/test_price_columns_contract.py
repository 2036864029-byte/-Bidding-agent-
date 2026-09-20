"""price_records 列契约 —— 行键必须是机器名，且模板不得引用未注册字段。

**为什么需要**：`price_records` 的表格列由**服务端下发**（`data.columns`），
`key` 是机器名、`label` 是中文表头。早期实现把**中文标签直接当行键**写进响应，
前端只能靠中文字面量认字段 —— 后端一换标签，前端就静默失效（无编译错、
无测试失败）。见 `price_engine/rendering/output_templates.py:300-303` 的注释。

第二条断言防的是另一类静默失效：模板里写错字段名时，
`_apply_output_template` 的 `if fd is None: continue` 会**无声跳过** ——
整列从响应里消失且不报错。
"""

from __future__ import annotations

import pytest

from price_engine.rendering import output_templates as ot


class _AllFlagsTrue:
    """假 intent：所有布尔条件视为成立。

    存在的理由：模板的 `conditional` 字段按 `intent.<attr>` 求值
    （见 `ot._eval_condition`），不注入这样的 stub 就测不到条件字段，
    列契约的覆盖面会缺一块。用 `__getattr__` 而非显式列出属性名 ——
    新增条件字段时本测试无需跟着改。
    """

    def __getattr__(self, name: str) -> bool:
        return True


def test_field_registry_keys_are_machine_readable():
    """字段注册表的 key 必须是机器名（ASCII），不能是中文表头。

    回归：原实现把中文标签当行键 → 前端只能靠中文字面量认字段，后端换标签即静默失效。
    """
    non_ascii = sorted(k for k in ot._FIELD_REGISTRY if not k.isascii())
    assert not non_ascii, (
        f"字段 key 出现非 ASCII（中文不能当机器键）：{non_ascii}\n"
        f"  修法：key 用 snake_case 机器名，中文放 FieldDescriptor.label。"
    )


@pytest.mark.parametrize(
    "route,qtype",
    sorted((r, q) for r, byq in ot._ROUTE_TEMPLATES.items() for q in byq),
)
def test_template_field_refs_exist_in_registry(route: str, qtype: str):
    """模板引用的字段必须都在注册表里 —— 否则运行时被静默跳过。

    `_apply_output_template` 里 `if fd is None: continue` 会无声吞掉拼错的字段名，
    该列直接从响应里消失且不报错。这条断言把"静默"变成"提交即失败"。
    """
    tpl = ot._ROUTE_TEMPLATES[route][qtype]
    referenced = set(tpl.required) | set(tpl.optional) | set(tpl.display_order)
    for extra in tpl.conditional.values():
        referenced |= set(extra)

    missing = sorted(k for k in referenced if k not in ot._FIELD_REGISTRY)
    assert not missing, (
        f"{route}/{qtype} 引用了未注册字段：{missing}\n"
        f"  这些列会在响应里静默消失（_apply_output_template 跳过 None 描述符）。"
    )


@pytest.mark.parametrize(
    "route,qtype",
    sorted((r, q) for r, byq in ot._ROUTE_TEMPLATES.items() for q in byq),
)
def test_emitted_columns_use_registry_key_and_label(route: str, qtype: str):
    """实际产出的 `columns[]` 必须：key 来自注册表、label 与注册表逐字一致。

    用"全字段有值"的记录逼出最大列集（空值会按 null_behavior 隐藏）。
    """
    registry = ot._FIELD_REGISTRY
    tpl = ot._ROUTE_TEMPLATES[route][qtype]

    # 造一条每个字段都有值的记录，保证列不因空值被隐藏
    record = {fd.source_col: f"v_{fd.key}" for fd in registry.values()}
    out = ot._apply_output_template([record], _AllFlagsTrue(), tpl, registry)

    for column in out.columns:
        assert column["key"] in registry, (
            f"{route}/{qtype}: 列 key {column['key']!r} 不在字段注册表"
        )
        assert column["label"] == registry[column["key"]].label, (
            f"{route}/{qtype}: 列 {column['key']!r} 的 label 与注册表不一致"
            f"（响应={column['label']!r}，注册表={registry[column['key']].label!r}）"
        )

    # 行键必须是 columns 声明过的机器名，不能是中文
    for row in out.rows:
        unexpected = sorted(k for k in row if k not in registry)
        assert not unexpected, (
            f"{route}/{qtype}: 行里出现未注册的键 {unexpected}（疑似中文表头当行键）"
        )
