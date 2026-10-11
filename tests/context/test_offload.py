"""层1 工具结果存盘单测(F1/F2/N5)。"""

import asyncio

from codepilot.context.offload import ToolResultOffloader, OffloadedResult
from codepilot.protocols import ToolResult


def _res(output="", name="read", error=None):
    return ToolResult(call_id="c1", name=name, output=output, error=error)


async def _run(coro):
    return await coro


# —— F1: 单结果 > N 存盘 ——

def test_small_result_not_offloaded(workspace):
    off = ToolResultOffloader(workspace / "ctx")
    r = _res("small output")
    assert asyncio.run(off.offload(r, threshold_n=1000)) is None


def test_large_result_offloaded_with_path_and_preview(workspace):
    off = ToolResultOffloader(workspace / "ctx")
    big = "L" * 10_000  # 2500 token > 1000
    r = _res(big)
    res = asyncio.run(off.offload(r, threshold_n=1000))
    assert res is not None
    assert res.path.startswith(str(workspace / "ctx"))
    # 完整内容已落盘
    assert asyncio.run(off.reopen_text(res.path)) == big
    # 预览是前 200 字符(或更短)
    assert len(res.preview) <= 200


def test_display_text_mentions_path_and_read(workspace):
    off = ToolResultOffloader(workspace / "ctx")
    res = asyncio.run(off.offload(_res("Z" * 5000), threshold_n=1000))
    txt = res.display_text()
    assert "已存盘" in txt and "read" in txt and txt.startswith("[工具结果已存盘]")


# —— F2: 同消息多结果合计 > M ——

def test_batch_under_threshold_untouched(workspace):
    off = ToolResultOffloader(workspace / "ctx")
    results = [_res("a"), _res("b"), _res("c")]
    processed, saved = asyncio.run(off.offload_oversized(results, threshold_m=4000))
    assert saved == 0
    assert all(isinstance(x, ToolResult) for x in processed)


def test_batch_oversized_offload_largest_to_threshold(workspace):
    off = ToolResultOffloader(workspace / "ctx")
    # 三个结果:小/中/巨大;合计超过 M=1000
    results = [
        _res("A" * 100),    # ~25 token
        _res("B" * 1000),   # ~250
        _res("C" * 6000),   # ~1500
    ]
    processed, saved = asyncio.run(off.offload_oversized(results, threshold_m=1000))
    assert saved > 0
    # 最大那个应被存盘(OffloadedResult),其余保留或后续处理
    assert isinstance(processed[2], OffloadedResult)
    # 处理后(存盘抵免后)合计应 ≤ M
    remaining = sum(
        estimate_tokens(x) for x in processed if not isinstance(x, OffloadedResult))
    assert remaining <= 1000


def estimate_tokens(x):
    from codepilot.context.estimator import estimate_text
    return estimate_text(x.output or "", kind="code")


# —— 重读 ——

def test_reopen_text_reads_back(workspace):
    off = ToolResultOffloader(workspace / "ctx")
    big = "x" * 5000
    res = asyncio.run(off.offload(_res(big), threshold_n=1000))
    assert asyncio.run(off.reopen_text(res.path)) == big


def test_reopen_missing_returns_empty(workspace):
    off = ToolResultOffloader(workspace / "ctx")
    assert asyncio.run(off.reopen_text(str(workspace / "nope.txt"))) == ""


# —— N5: 磁盘写失败降级 ——

def test_degraded_when_write_fails(workspace, monkeypatch):
    off = ToolResultOffloader(workspace / "ctx")

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.write_text", _boom)
    res = asyncio.run(off.offload(_res("Y" * 5000), threshold_n=1000))
    # 降级:返回占位(路径为空)且不抛异常,不丢对话
    assert res is not None
    assert res.path == ""