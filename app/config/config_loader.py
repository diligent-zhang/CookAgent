"""
配置加载器
负责从 config.yml 和 .env 中读取配置，并合并为统一的配置字典。
支持 ${ENV_VAR} 占位符语法：config.yml 中可以用 ${LLM_API_KEY} 引用环境变量。
"""

import os
import re
from typing import Any, Dict

import yaml
from dotenv import load_dotenv

# 项目根目录（config_loader.py 在 app/config/ 下，往上两级是根目录）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_env_file():
    """加载 .env 文件到环境变量。"""
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        print(f"[WARNING] .env file not found at {env_path}")


def _resolve_env_vars(value: Any) -> Any:
    """
    递归解析配置值中的 ${ENV_VAR} 占位符。
    例如: "${LLM_API_KEY}" → os.environ["LLM_API_KEY"]
    """
    if isinstance(value, str):
        # 匹配 ${VAR_NAME} 模式
        pattern = re.compile(r'\$\{([^}]+)\}')
        matches = pattern.findall(value)
        if matches and len(matches) == 1 and value.strip() == f"${{{matches[0]}}}":
            # 整个值就是一个占位符，直接返回环境变量的值（不一定是字符串）
            return os.environ.get(matches[0], "")
        # 部分占位符，替换后返回字符串
        return pattern.sub(
            lambda m: os.environ.get(m.group(1), ""), value
        )
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def load_config() -> Dict[str, Any]:
    """
    加载完整配置。
    1. 先加载 .env
    2. 读取 config.yml
    3. 递归替换其中的 ${ENV_VAR} 占位符
    """
    _load_env_file()

    config_path = os.path.join(PROJECT_ROOT, "config.yml")
    with open(config_path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    return _resolve_env_vars(raw_config)
