"""Tool execution — bridges Claude tool calls to WIN5 data."""

import json
import logging
import time

from config import DLOGIC_API_URL

logger = logging.getLogger(__name__)


def execute_tool(tool_name: str, tool_input: dict, context: dict | None = None) -> str:
    """Execute a tool and return the result as a JSON string."""
    start = time.monotonic()
    logger.info(f"Tool call: {tool_name} with keys={list(tool_input.keys())}")
    try:
        if tool_name == "get_win5_races":
            result = _get_win5_races(tool_input)
        elif tool_name == "get_race_scores":
            result = _get_race_scores(tool_input)
        elif tool_name == "get_volatility":
            result = _get_volatility(tool_input)
        elif tool_name == "generate_tickets":
            result = _generate_tickets(tool_input)
        elif tool_name == "generate_scenarios":
            result = _generate_scenarios(tool_input)
        elif tool_name == "simulate_payout":
            result = _simulate_payout(tool_input)
        elif tool_name == "get_win5_history":
            result = _get_win5_history(tool_input)
        elif tool_name == "get_carryover":
            result = _get_carryover(tool_input)
        else:
            result = json.dumps({"error": f"Unknown tool: {tool_name}"}, ensure_ascii=False)

        elapsed = time.monotonic() - start
        logger.info(f"Tool done: {tool_name} in {elapsed:.2f}s")
        return result
    except Exception as e:
        elapsed = time.monotonic() - start
        logger.exception(f"Tool error: {tool_name} ({elapsed:.2f}s)")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool implementations (Phase 1 stubs — to be implemented)
# ---------------------------------------------------------------------------

def _get_win5_races(params: dict) -> str:
    """TODO: WIN5対象5レース取得"""
    return json.dumps({"error": "未実装: WIN5レース取得はPhase 1-2で実装予定"}, ensure_ascii=False)


def _get_race_scores(params: dict) -> str:
    """TODO: 全馬指数取得"""
    return json.dumps({"error": "未実装: 全馬指数はPhase 1-2で実装予定"}, ensure_ascii=False)


def _get_volatility(params: dict) -> str:
    """TODO: 波乱度算出"""
    return json.dumps({"error": "未実装: 波乱度はPhase 1-3で実装予定"}, ensure_ascii=False)


def _generate_tickets(params: dict) -> str:
    """TODO: 買い目ジェネレーター"""
    return json.dumps({"error": "未実装: 買い目生成はPhase 1-4で実装予定"}, ensure_ascii=False)


def _generate_scenarios(params: dict) -> str:
    """TODO: 3シナリオ生成"""
    return json.dumps({"error": "未実装: シナリオ生成はPhase 1-5で実装予定"}, ensure_ascii=False)


def _simulate_payout(params: dict) -> str:
    """TODO: 想定払戻計算"""
    return json.dumps({"error": "未実装: 払戻計算はPhase 1-4で実装予定"}, ensure_ascii=False)


def _get_win5_history(params: dict) -> str:
    """TODO: 過去WIN5傾向"""
    return json.dumps({"error": "未実装: 過去傾向はPhase 2で実装予定"}, ensure_ascii=False)


def _get_carryover(params: dict) -> str:
    """TODO: キャリーオーバー情報"""
    return json.dumps({"error": "未実装: キャリーオーバーはPhase 2で実装予定"}, ensure_ascii=False)
