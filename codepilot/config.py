"""配置层：YAML 读取、校验、ProviderConfig。"""

from dataclasses import dataclass
from pathlib import Path

import yaml


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
    )
