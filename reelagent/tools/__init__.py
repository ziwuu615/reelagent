"""工具层：所有可被 Agent 调用的能力都在这里注册。

注册结构的 `specs()` 输出符合 MCP tools/list 协议，
因此这一层可以零改动地由一个 MCP Server 暴露给外部 Agent 使用。
"""

from .media_tools import register_media_tools

__all__ = ["register_media_tools"]
