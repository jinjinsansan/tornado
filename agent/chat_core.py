"""WIN5 agentic loop — shared by WebChat (and future LINE Bot).

Yields chunks as the conversation progresses:
  {"type": "thinking"}              — Claude API call started
  {"type": "tool", "name": ...}     — tool being executed
  {"type": "text", "content": ...}  — final text from Claude
  {"type": "done", "text": ..., "tools_used": [...], "quick_replies": [...]}
"""

import logging

from agent.engine import (
    call_claude, build_system_prompt, extract_text, get_tool_blocks,
)
from config import MAX_TOOL_TURNS
from tools.executor import execute_tool

logger = logging.getLogger(__name__)

# Tool display names for user-facing notifications
TOOL_LABELS = {
    "get_win5_races": "WIN5レース取得",
    "get_race_scores": "全馬指数取得",
    "get_volatility": "波乱度算出",
    "generate_tickets": "買い目生成",
    "generate_scenarios": "3シナリオ生成",
    "simulate_payout": "想定払戻計算",
    "get_win5_history": "過去WIN5傾向",
    "get_carryover": "キャリーオーバー確認",
}


def _normalize_block(block):
    """Convert Anthropic SDK ContentBlock to plain dict for JSON serialization."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "type"):
        if block.type == "text":
            return {"type": "text", "text": block.text}
        if block.type == "tool_use":
            return {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            }
    return block


def get_quick_replies(tools_used: list[str]) -> list[dict]:
    """Generate context-appropriate quick reply buttons."""
    used = set(tools_used)
    items = []

    # WIN5 tools that indicate user is in analysis mode
    analysis_tools = {
        "get_win5_races", "get_race_scores", "get_volatility",
        "generate_tickets", "generate_scenarios",
    }

    if used & analysis_tools:
        # User is analyzing WIN5 — show relevant follow-ups
        if "generate_tickets" not in used:
            items.append({"label": "🎯 買い目出して", "text": "買い目出して"})
        if "generate_scenarios" not in used:
            items.append({"label": "📊 3シナリオ", "text": "3シナリオ見せて"})
        if "get_volatility" not in used:
            items.append({"label": "🌪️ 波乱度", "text": "波乱度は？"})
        if "get_race_scores" not in used:
            items.append({"label": "📈 指数", "text": "指数見せて"})
        if "get_carryover" not in used:
            items.append({"label": "💰 キャリーオーバー", "text": "キャリーオーバーは？"})
        items.append({"label": "💬 どう思う？", "text": "お前はどう思う？"})
    else:
        # Initial state — suggest starting actions
        items = [
            {"label": "🌪️ 今週のWIN5", "text": "今週のWIN5は？"},
            {"label": "🎯 買い目出して", "text": "予算5000円で買い目出して"},
            {"label": "📊 3シナリオ", "text": "3シナリオ見せて"},
        ]

    return items


def run_agent(
    user_message: str,
    history: list[dict],
) -> dict:
    """Run the WIN5 agentic loop.

    This is a generator that yields event dicts as the conversation progresses.

    Args:
        user_message: The user's message text
        history: Conversation history (list of role/content dicts)

    Yields:
        {"type": "thinking"}
        {"type": "tool", "name": "...", "label": "..."}
        {"type": "text", "content": "..."}
        {"type": "done", "text": "...", "tools_used": [...], "quick_replies": [...], "history": [...]}
    """
    system = build_system_prompt()
    tools_used: list[str] = []

    yield {"type": "thinking"}

    for turn in range(MAX_TOOL_TURNS):
        response = call_claude(history, system)

        # End turn — no more tool calls
        if response.stop_reason == "end_turn":
            text = extract_text(response)
            history.append({
                "role": "assistant",
                "content": [_normalize_block(b) for b in response.content],
            })

            yield {"type": "text", "content": text}
            yield {
                "type": "done",
                "text": text,
                "tools_used": tools_used,
                "quick_replies": get_quick_replies(tools_used),
                "history": history,
            }
            return

        # Tool use
        tool_blocks = get_tool_blocks(response)
        if not tool_blocks:
            # No tools and not end_turn — just extract text
            text = extract_text(response)
            history.append({
                "role": "assistant",
                "content": [_normalize_block(b) for b in response.content],
            })

            yield {"type": "text", "content": text}
            yield {
                "type": "done",
                "text": text,
                "tools_used": tools_used,
                "quick_replies": get_quick_replies(tools_used),
                "history": history,
            }
            return

        # Append assistant message with tool_use blocks
        history.append({
            "role": "assistant",
            "content": [_normalize_block(b) for b in response.content],
        })

        # Execute each tool
        tool_results = []
        for tb in tool_blocks:
            tool_name = tb.name
            tools_used.append(tool_name)
            label = TOOL_LABELS.get(tool_name, tool_name)

            yield {"type": "tool", "name": tool_name, "label": label}

            result = execute_tool(tool_name, tb.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tb.id,
                "content": result,
            })

        # Append tool results for next Claude call
        history.append({"role": "user", "content": tool_results})

    # Max turns reached — return whatever we have
    text = "処理が複雑すぎて完了できなかった。もう少しシンプルに聞いてくれ。"
    yield {"type": "text", "content": text}
    yield {
        "type": "done",
        "text": text,
        "tools_used": tools_used,
        "quick_replies": get_quick_replies(tools_used),
        "history": history,
    }
