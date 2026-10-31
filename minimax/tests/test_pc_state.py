"""PowerContext 集成测试：状态写入、重启读回、幂等、超限。"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("httpx")

from mmctx.adapters.pc_state import PowerContextState  # noqa: E402
from mmctx.core.state_model import build_state, pack_state  # noqa: E402
from mmctx.ports.state import StateTooLargeError  # noqa: E402


def _state(task_id: str, seq: int = 1) -> str:
    return pack_state(
        build_state(
            task_id=task_id,
            title="测试任务",
            run_id=f"run-{seq}",
            spec_digest="digest",
            context_sha256="ctx",
            applied_commit="abc",
            status="applied",
            todo=["继续"],
            seq=seq,
            updated_at="2026-10-31T00:00:00+00:00",
        )
    )


@pytest.fixture()
def pc():
    base = os.environ.get("PC_SERVER_URL", "http://127.0.0.1:8000")
    state = PowerContextState(base, f"mmctx-test-{uuid.uuid4().hex[:8]}")
    try:
        state.health()
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"PowerContext 未启动：{error}")
    try:
        yield state
    finally:
        state.close()


def test_state_roundtrip(pc):
    task_id = f"t-{uuid.uuid4().hex[:8]}"
    text = _state(task_id, 1)
    ref = pc.save(task_id, text, seq=1)
    assert ref.entry_id
    record = pc.load_latest(task_id)
    assert record is not None
    assert record.text == text
    assert record.seq == 1
    assert record.ref.entry_id == ref.entry_id


def test_state_save_is_idempotent(pc):
    task_id = f"t-{uuid.uuid4().hex[:8]}"
    text = _state(task_id, 1)
    first = pc.save(task_id, text, seq=1)
    second = pc.save(task_id, text, seq=2)
    assert second.unchanged is True
    assert second.entry_id == first.entry_id
    # 不用 list_task_ids().count()：那个接口按 task_id 去重，对「写了两条」永远返回 1。
    assert pc.count_state_entries(task_id) == 1


def test_state_too_large_is_rejected_before_network(pc):
    with pytest.raises(StateTooLargeError):
        pc.save("t-big", "中" * 3000, seq=1)


def test_state_survives_a_new_process(pc):
    """跨进程复用：子进程写入，父进程（不同 PID、不同 client）读回。"""
    import json
    import subprocess
    import sys

    task_id = f"t-proc-{uuid.uuid4().hex[:8]}"
    script = (
        "import sys;from mmctx.adapters.pc_state import PowerContextState;"
        "from mmctx.core.state_model import build_state,pack_state;"
        f"state=PowerContextState({os.environ.get('PC_SERVER_URL', 'http://127.0.0.1:8000')!r},{pc.scope_title!r});"
        "text=pack_state(build_state(task_id=sys.argv[1],title='跨进程',run_id='run-proc',spec_digest='d',"
        "context_sha256='c',applied_commit='abc',status='applied',todo=['下一步'],seq=1,updated_at='now'));"
        "ref=state.save(sys.argv[1],text,seq=1);print(ref.entry_id)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, task_id],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
    record = pc.load_latest(task_id)
    assert record is not None
    assert json.loads(record.text)["run_id"] == "run-proc"
