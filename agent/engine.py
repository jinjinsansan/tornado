"""Agent engine — Claude API with tool use for TornadoAI."""

import logging

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, SYSTEM_PROMPT, MAX_TOKENS
from tools.definitions import TOOLS

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def call_claude(conversation_history: list[dict], system: str, tools: list[dict] | None = None) -> object:
    return _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=[{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }],
        tools=tools or TOOLS,
        messages=conversation_history,
    )


def build_system_prompt() -> str:
    from datetime import datetime, timezone, timedelta
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][now.weekday()]
    date_line = f"\n\n## 現在の日時\n{now.strftime('%Y年%m月%d日')}（{weekday_ja}） {now.strftime('%H:%M')} JST"
    return SYSTEM_PROMPT + date_line


def extract_text(response) -> str:
    return "\n".join(b.text for b in response.content if b.type == "text")


def get_tool_blocks(response) -> list:
    return [b for b in response.content if b.type == "tool_use"]
