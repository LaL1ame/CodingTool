"""规则加载器 — 从 YAML 文件读取规则字符串并解析为 Rule 列表。

文件缺失/空时忽略；格式错误时抛错让上层知晓配置有问题。
"""

from pathlib import Path

import yaml

from codepilot.permissions.rules import Rule, parse_rule


def load_rules_from_file(path: Path) -> list[Rule]:
    """读取单个规则文件的 rules 列表。文件不存在返回空列表。"""
    path = Path(path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("rules", []) or []
    return [parse_rule(str(item)) for item in raw]


def load_rules_from_files(paths: list[Path]) -> list[Rule]:
    """读多个层级的规则文件并合并结果列表（合并判定由 policy 统一做优先级）。"""
    merged: list[Rule] = []
    for p in paths:
        merged.extend(load_rules_from_file(p))
    return merged