"""工具抽象基类 + 公共工具函数。"""

from abc import ABC, abstractmethod
from pathlib import Path


class Tool(ABC):
    """统一的工具接口。新增工具只需继承 Tool 并注册到 ToolRegistry。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """工具唯一名称，如 "read"、"write"。"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具用途描述，供模型判断何时调用此工具。"""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """JSON Schema 格式的参数定义。"""
        ...

    @property
    def side_effect(self) -> bool:
        """是否有副作用（写文件/执行命令）。True → 串行执行。默认 False（只读）。"""
        return False

    @abstractmethod
    async def execute(self, **kwargs) -> "ToolResult":
        """执行工具逻辑。异常应在方法内部捕获并转为 ToolResult(error=...)。"""
        ...


def validate_path(relative_path: str, workspace_root: Path) -> Path:
    """解析并校验路径：确保最终路径在工作目录子树内。

    Raises:
        ValueError: 路径超出工作目录范围。
    """
    candidate = (workspace_root / relative_path).resolve()
    if not candidate.is_relative_to(workspace_root.resolve()):
        raise ValueError(
            f"路径超出工作目录: {relative_path}\n"
            f"  解析后路径: {candidate}\n"
            f"  工作目录:   {workspace_root}"
        )
    return candidate
