"""channels.feishu.session — 会话映射（私聊按人 / 群聊按群 / 按人限流）。"""

from channels.feishu.session import session_id_for, tenant_id_for


def test_p2p_session_isolated_per_open_id():
    a1 = session_id_for("ou_user_a", chat_type="p2p")
    a2 = session_id_for("ou_user_a", chat_type="p2p")
    b = session_id_for("ou_user_b", chat_type="p2p")
    assert a1.startswith("fs-p2p-ou_user_a")
    assert a1 == a2          # 同一人多轮上下文延续
    assert a1 != b           # 不同人互不串


def test_group_session_shared_by_chat_id():
    a = session_id_for("ou_user_a", chat_id="oc_group1", chat_type="group")
    b = session_id_for("ou_user_b", chat_id="oc_group1", chat_type="group")
    other = session_id_for("ou_user_a", chat_id="oc_group2", chat_type="group")
    assert a == b            # 群里的人共享同一上下文
    assert a.startswith("fs-group-oc_group1")
    assert a != other


def test_tenant_id_per_open_id():
    assert tenant_id_for("ou_user_a") == "feishu:ou_user_a"
    assert tenant_id_for("ou_user_a") != tenant_id_for("ou_user_b")
