"""测试公共 fixtures。"""

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def workspace() -> Path:
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def workspace_with_files(workspace: Path) -> Path:
    (workspace / "readme.txt").write_text("hello world\nline two\nline three\n")
    (workspace / "code.py").write_text("import os\n\ndef hello():\n    print('hi')\n")
    (workspace / "sub").mkdir(exist_ok=True)
    (workspace / "sub" / "nested.py").write_text("x = 1\ny = 2\n")
    return workspace
