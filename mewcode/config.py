"""配置层：YAML 读取、校验、ProviderConfig 数据类。"""

from dataclasses import dataclass
from pathlib import Path

import yaml


class ConfigError(Exception):
    """配置文件缺失或格式/字段错误时抛出。"""


_REQUIRED_FIELDS = ("name", "protocol", "model", "api_key", "base_url")


@dataclass
class ProviderConfig:
    """单个 LLM 供应商的配置项。"""

    name: str
    protocol: str       # "anthropic" | "openai"
    model: str
    api_key: str
    base_url: str
    thinking: bool = False


def load_config(path: str | Path) -> list[ProviderConfig]:
    """从 YAML 文件读取并校验 providers 列表。

    Raises:
        ConfigError: 文件缺失、格式错误或必要字段不完整。
    """
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

    providers_raw = raw["providers"]
    if not isinstance(providers_raw, list) or len(providers_raw) == 0:
        raise ConfigError("未配置任何 provider（providers 列表为空）")

    configs: list[ProviderConfig] = []
    for i, entry in enumerate(providers_raw):
        missing = [f for f in _REQUIRED_FIELDS if f not in entry or entry[f] is None]
        if missing:
            raise ConfigError(
                f"第 {i + 1} 个 provider 缺少必要字段: {', '.join(missing)}"
            )
        configs.append(
            ProviderConfig(
                name=entry["name"],
                protocol=entry["protocol"].lower(),
                model=entry["model"],
                api_key=entry["api_key"],
                base_url=entry["base_url"],
                thinking=entry.get("thinking", False),
            )
        )

    return configs
