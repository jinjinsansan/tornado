"""WIN5 agentic loop — shared by WebChat (and future LINE Bot).

Yields chunks as the conversation progresses:
  {"type": "thinking"}              — Claude API call started
  {"type": "tool", "name": ...}     — tool being executed
  {"type": "text", "content": ...}  — final text from Claude
  {"type": "done", "text": ..., "tools_used": [...], "quick_replies": [...]}
"""

import json
import logging
import re

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
    "get_wide_races": "ワイド レース一覧",
    "generate_wide": "ワイド 買い目生成",
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
    wide_tools = {"get_wide_races", "generate_wide"}

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
        items.append({"label": "💰 ワイド5倍", "text": "ワイドで1000円→5000円が欲しい"})
    elif used & wide_tools:
        if "get_wide_races" not in used:
            items.append({"label": "📋 レース一覧", "text": "今日のワイド レース一覧"} )
        if "generate_wide" not in used:
            items.append({"label": "💰 ワイド5倍", "text": "中山11Rでワイド 1000円→5000円が欲しい"})
        items.append({"label": "🎯 WIN5", "text": "今週のWIN5は？"})
    else:
        # Initial state — suggest starting actions
        items = [
            {"label": "🌪️ 今週のWIN5", "text": "今週のWIN5は？"},
            {"label": "🎯 買い目出して", "text": "予算5000円で買い目出して"},
            {"label": "📊 3シナリオ", "text": "3シナリオ見せて"},
            {"label": "💰 ワイド5倍", "text": "中山11Rでワイド 1000円→5000円が欲しい"},
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
    last_ticket: dict | None = None
    last_scenarios: dict | None = None

    yield {"type": "thinking"}

    # ---------------------------------------------------------------------
    # Fast-path router for Wide mode (deterministic, avoids model missing tools)
    # ---------------------------------------------------------------------
    msg = (user_message or "").strip()
    if "ワイド" in msg:
        # 1) Race list request
        if any(k in msg for k in ("一覧", "レース一覧", "今日のレース", "対象レース")):
            tool_name = "get_wide_races"
            tools_used.append(tool_name)
            yield {"type": "tool", "name": tool_name, "label": TOOL_LABELS.get(tool_name, tool_name)}
            raw = execute_tool(tool_name, {})
            try:
                data = json.loads(raw) if isinstance(raw, str) else {}
            except Exception:
                data = {}

            if isinstance(data, dict) and data.get("error"):
                text = f"申し訳ございません。ワイドのレース一覧取得に失敗しました。\n\n原因: {data.get('error')}"
                history.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
                yield {"type": "text", "content": text}
                yield {
                    "type": "done",
                    "text": text,
                    "tools_used": tools_used,
                    "quick_replies": get_quick_replies(tools_used),
                    "history": history,
                    "ticket": last_ticket,
                    "scenarios": last_scenarios,
                }
                return

            races = data.get("races") or []
            date = str(data.get("date") or "")
            ready = bool(data.get("ready"))
            title_date = f"{date[:4]}/{date[4:6]}/{date[6:8]}" if len(date) == 8 else date

            lines = []
            lines.append(f"## ワイド対象レース一覧（{title_date}）")
            lines.append("（会場 + レース番号で指定できます）")
            lines.append("")
            if not ready:
                lines.append("※ データ準備中です（前日10:30以降に利用可能）")
                lines.append("")

            # Keep concise: top 12 only
            for r in races[:12]:
                v = r.get("venue", "")
                n = r.get("race_number", 0)
                name = r.get("race_name", "")
                t = r.get("start_time", "")
                d = r.get("distance", "")
                tail = " / ".join(x for x in (t, d) if x)
                lines.append(f"- {v}{n}R {name}{(' / ' + tail) if tail else ''}")
            if len(races) > 12:
                lines.append(f"- …ほか {len(races) - 12}件")

            lines.append("")
            lines.append("次は、例えばこう聞いてください：")
            lines.append("「中山11Rでワイド 1000円→5000円が欲しい」")

            text = "\n".join(lines)
            history.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
            yield {"type": "text", "content": text}
            yield {
                "type": "done",
                "text": text,
                "tools_used": tools_used,
                "quick_replies": get_quick_replies(tools_used),
                "history": history,
                "ticket": last_ticket,
                "scenarios": last_scenarios,
            }
            return

        # 2) Generate if we can parse venue+R and amounts
        venue_match = re.search(r"(中山|阪神|中京|東京|京都|新潟|福島|小倉|札幌|函館)", msg)
        race_match = re.search(r"(\d{1,2})\s*R", msg, flags=re.I)
        money = [int(x.replace(",", "")) for x in re.findall(r"(\d[\d,]*)\s*円", msg)]

        # Support "1000円→5000円"
        arrow = re.search(r"(\d[\d,]*)\s*円?\s*[→\\-]\s*(\d[\d,]*)\s*円?", msg)
        budget = None
        target_payout = None
        if arrow:
            budget = int(arrow.group(1).replace(",", ""))
            target_payout = int(arrow.group(2).replace(",", ""))
        elif len(money) >= 2:
            budget, target_payout = money[0], money[1]
        elif len(money) == 1:
            budget = money[0]
            mult = re.search(r"(\d+(?:\.\d+)?)\s*倍", msg)
            if mult:
                target_payout = int(round(budget * float(mult.group(1))))

        if venue_match and race_match and budget and target_payout:
            tool_name = "generate_wide"
            tools_used.append(tool_name)
            yield {"type": "tool", "name": tool_name, "label": TOOL_LABELS.get(tool_name, tool_name)}
            raw = execute_tool(tool_name, {
                "venue": venue_match.group(1),
                "race_number": int(race_match.group(1)),
                "budget": int(budget),
                "target_payout": int(target_payout),
            })
            try:
                data = json.loads(raw) if isinstance(raw, str) else {}
            except Exception:
                data = {}

            if isinstance(data, dict) and data.get("error"):
                text = f"申し訳ございません。{data.get('error')}"
            else:
                rec = (data.get("recommended") or {}) if isinstance(data, dict) else {}
                pair = rec.get("pair") or []
                odds = rec.get("wide_odds") or {}
                pay = rec.get("expected_payout_range") or {}
                hit = rec.get("hit_probability_est") or 0
                venue = data.get("venue", "")
                rn = data.get("race_number", "")
                name = data.get("race_name", "")
                tm = data.get("target_multiplier", 0)
                mm = rec.get("multiplier_mid", 0)

                def _p(i):
                    try:
                        return f"{pair[i]['horse_number']} {pair[i]['horse_name']}"
                    except Exception:
                        return ""

                o_min = odds.get("min", 0)
                o_max = odds.get("max", o_min)
                payout_min = pay.get("min", 0)
                payout_max = pay.get("max", payout_min)

                text = "\n".join([
                    f"## ワイド買い目（{venue}{rn}R {name}）",
                    f"**予算** ¥{int(budget):,} / **目標払戻** ¥{int(target_payout):,}（目標 {float(tm):.2f}倍）",
                    "",
                    f"### ✅ おすすめ",
                    f"- **{_p(0)} × {_p(1)}**",
                    f"- ワイド倍率（100円あたり）: {float(o_min):.1f}{f'–{float(o_max):.1f}' if o_max != o_min else ''}倍",
                    f"- 想定払戻: ¥{int(payout_min):,}{f'〜¥{int(payout_max):,}' if payout_max != payout_min else ''}",
                    f"- 的中率（目安）: {float(hit) * 100:.2f}%",
                    "",
                    f"※ 目標に近い（推定 {float(mm):.2f}倍）×当たりやすさを優先して選んでいます。",
                ])

            history.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
            yield {"type": "text", "content": text}
            yield {
                "type": "done",
                "text": text,
                "tools_used": tools_used,
                "quick_replies": get_quick_replies(tools_used),
                "history": history,
                "ticket": last_ticket,
                "scenarios": last_scenarios,
            }
            return

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
                "ticket": last_ticket,
                "scenarios": last_scenarios,
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
                "ticket": last_ticket,
                "scenarios": last_scenarios,
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
            try:
                parsed = json.loads(result) if isinstance(result, str) else None
                if tool_name == "generate_tickets" and isinstance(parsed, dict) and parsed.get("tickets"):
                    last_ticket = parsed
                elif tool_name == "generate_scenarios" and isinstance(parsed, dict) and parsed.get("main"):
                    last_scenarios = parsed
            except Exception:
                pass
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
        "ticket": last_ticket,
        "scenarios": last_scenarios,
    }
