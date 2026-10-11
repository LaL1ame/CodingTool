"""配置层：YAML 读取、校验、ProviderConfig、MCP server 声明。"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

import yaml

logger = logging.getLogger("codepilot.config")

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


class ConfigError(Exception):
    """配置错误。"""


_REQUIRED = ("name", "protocol", "model", "api_key", "base_url")


@dataclass
class ProviderConfig:
    name: str
    protocol: str       # "anthropic" | "openai"
    model: str
    api_key: str
    base_url: str
    thinking: bool = False


@dataclass
class AgentConfig:
    """Agent 循环的运行时配置。"""
    max_rounds: int = 10      # 迭代上限（安全网）
    max_unknown: int = 2      # 连续未知工具阈值
    prompt_refresh_every: int = 3  # plan 模式指令每 N 轮精简重复一次（F6）


@dataclass
class LogConfig:
    """日志落盘配置。file 为空则不写盘。"""
    file: str = ""
    level: str = "INFO"


@dataclass
class PermissionsConfig:
    """权限系统配置。路径留空则用默认发现。"""
    mode: str = "default"      # strict / default / permissive
    user_rules: str = ""       # 用户级规则文件
    project_rules: str = ""    # 项目级规则文件
    local_rules: str = ""      # 本地级规则文件（永久放行写入处）


def load_config(path: str | Path) -> list[ProviderConfig]:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"配置文件未找到: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML 格式错误: {e}")
    if raw is None or "providers" not in raw:
        raise ConfigError("配置文件中未找到 'providers' 字段")
    items = raw["providers"]
    if not isinstance(items, list) or len(items) == 0:
        raise ConfigError("未配置任何 provider")
    configs = []
    for i, entry in enumerate(items):
        missing = [f for f in _REQUIRED if f not in entry or entry[f] is None]
        if missing:
            raise ConfigError(f"第 {i+1} 个 provider 缺少: {', '.join(missing)}")
        configs.append(ProviderConfig(
            name=entry["name"],
            protocol=entry["protocol"].lower(),
            model=entry["model"],
            api_key=entry["api_key"],
            base_url=entry["base_url"],
            thinking=entry.get("thinking", False),
        ))
    return configs


def load_agent_config(path: str | Path) -> AgentConfig:
    """读取可选 agent: 段。文件缺失或无该段时用默认值。"""
    path = Path(path)
    if not path.exists():
        return AgentConfig()
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError:
        return AgentConfig()
    entry = (raw or {}).get("agent") or {}
    return AgentConfig(
        max_rounds=int(entry.get("max_rounds", 10)),
        max_unknown=int(entry.get("max_unknown", 2)),
        prompt_refresh_every=int(entry.get("prompt_refresh_every", 3)),
    )


def load_permissions_config(path: str | Path) -> PermissionsConfig:
    """读取可选 permissions: 段。文件缺失或无该段时用默认值。"""
    path = Path(path)
    if not path.exists():
        return PermissionsConfig()
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError:
        return PermissionsConfig()
    entry = (raw or {}).get("permissions") or {}
    return PermissionsConfig(
        mode=entry.get("mode", "default"),
        user_rules=entry.get("user_rules", ""),
        project_rules=entry.get("project_rules", ""),
        local_rules=entry.get("local_rules", ""),
    )


def load_log_config(path: str | Path) -> LogConfig:
    """读取可选 log: 段。文件缺失或无该段时用默认值（不落盘）。"""
    path = Path(path)
    if not path.exists():
        return LogConfig()
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError:
        return LogConfig()
    entry = (raw or {}).get("log") or {}
    return LogConfig(
        file=entry.get("file", ""),
        level=entry.get("level", "INFO"),
    )


@dataclass
class McpServerSpec:
    """一条 MCP server 声明(合并两层 config 后)。

    type: stdio 走本地子进程 + 管道;http 走远程 Streamable HTTP。
    read_only=True 时该 server 的工具被视为只读(side_effect=False),
    可进 plan 模式、可并行执行(默认 False 保守对待)。
    """

    name: str
    type: Literal["stdio", "http"]
    read_only: bool = False
    # — stdio —
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # — http —
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


def expand_env(value: str, env: Mapping[str, str] | None = None) -> str:
    """把字符串中的 ${VAR} 用 env(默认 os.environ)替换。

    Raises:
        ConfigError: 引用的变量未定义(符合 N4:不含糊地带病启动)。
    """
    env = env if env is not None else __import__("os").environ

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        if name not in env:
            raise ConfigError(f"环境变量未定义: {name}")
        return env[name]

    return _ENV_PATTERN.sub(_sub, value)


def _read_mcp_map(path: Path | None) -> dict:
    """读单个 YAML 的 mcp: 段。文件缺失/无该段 → 空 dict。"""
    if path is None or not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError:
        return {}
    section = (raw or {}).get("mcp")
    return section if isinstance(section, dict) else {}


def load_mcp_config(
    user_path: Path | None,
    project_path: Path | None,
    env: Mapping[str, str] | None = None,
) -> list[McpServerSpec]:
    """并读用户级 + 项目级 config.yaml 的 mcp: 段,合并 server 表(项目盖用户,F1)。

    对每条声明做 ${VAR} 展开(命令/args/env/url/headers,含 key 名)。
    未定义变量 → 该 server 记为配置错误,记 warning 后跳过(N4),不外溢崩溃。

    返回合并后**有效**的 server 列表(顺序:用户级在前,项目级同名覆盖)。
    """
    merged: dict = {}
    for p in (user_path, project_path):
        if p is not None and p.exists():
            merged.update(_read_mcp_map(p))

    specs: list[McpServerSpec] = []
    for name, raw in merged.items():
        if not isinstance(raw, dict):
            logger.warning("MCP server %r 配置不是 map,已跳过", name)
            continue
        try:
            specs.append(_build_spec(name, raw, env))
        except ConfigError as e:
            logger.warning("MCP server %r 配置无效,已跳过: %s", name, e)
    return specs


def _build_spec(name: str, raw: dict, env: Mapping[str, str] | None) -> McpServerSpec:
    spec_type = str(raw.get("type", "")).lower()
    if spec_type not in ("stdio", "http"):
        raise ConfigError(f"type 必须是 stdio|http, 实际为 {spec_type!r}")

    read_only = bool(raw.get("read_only", False))

    if spec_type == "stdio":
        command = expand_env(str(raw.get("command", "")), env)
        if not command:
            raise ConfigError("stdio server 缺少 command")
        args = [expand_env(str(a), env) for a in raw.get("args", [])]
        env_map = {expand_env(str(k), env): expand_env(str(v), env) for k, v in
                   (raw.get("env") or {}).items()}
        return McpServerSpec(
            name=name, type="stdio", read_only=read_only,
            command=command, args=args, env=env_map,
        )

    url = expand_env(str(raw.get("url", "")), env)
    if not url:
        raise ConfigError("http server 缺少 url")
    headers = {expand_env(str(k), env): expand_env(str(v), env) for k, v in
               (raw.get("headers") or {}).items()}
    return McpServerSpec(
        name=name, type="http", read_only=read_only,
        url=url, headers=headers,
    )
