"""Load and validate config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


_DEFAULT_CONFIG: dict[str, Any] = {
    "impala": {
        "host": "localhost",
        "port": 21050,
        "database": "default",
        "auth_mechanism": "NOSASL",
        "timeout_seconds": 30,
    },
    "model": {
        "name": "claude-opus-4-8",
        "max_tokens": 4096,
        "temperature": 0.0,
        "timeout_seconds": 60,
        "max_retries": 3,
    },
    "thresholds": {
        "broadcast_join_bytes": 536870912,
        "stats_ttl_hours": 24,
        "llm_max_iterations": 4,
        "rewrite_retry_limit": 1,
    },
    "duckdb": {
        "path": "metadata.duckdb",
    },
    "logging": {
        "level": "INFO",
    },
}


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load config.yaml; fall back to defaults for missing keys."""
    config: dict[str, Any] = {}

    # Find config file
    candidates = [
        config_path,
        Path("config.yaml"),
        Path(__file__).parent.parent / "config.yaml",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            with open(candidate) as f:
                config = yaml.safe_load(f) or {}
            break

    # Deep merge with defaults
    merged = _deep_merge(_DEFAULT_CONFIG, config)
    return merged


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
