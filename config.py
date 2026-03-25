"""TornadoAI configuration."""

import os
from dotenv import load_dotenv

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, ".env.local"))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
DLOGIC_DATA_API_URL = os.getenv("DLOGIC_DATA_API_URL", "http://localhost:5000")
DLOGIC_PREDICTION_API_URL = os.getenv(
    "DLOGIC_PREDICTION_API_URL",
    os.getenv("DLOGIC_API_URL", "http://localhost:8000"),
)
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_LOGIN_CHANNEL_ID = os.getenv("LINE_LOGIN_CHANNEL_ID", "")
LINE_LOGIN_CHANNEL_SECRET = os.getenv("LINE_LOGIN_CHANNEL_SECRET", "")
LINE_LOGIN_REDIRECT_URI = os.getenv(
    "LINE_LOGIN_REDIRECT_URI",
    "https://www.tornadeai.com/line/callback",
)
REDIS_URL = os.getenv("REDIS_URL", "")
WEB_AUTH_SECRET = os.getenv("WEB_AUTH_SECRET", "")

# CORS
CORS_ORIGINS = [
    o.strip() for o in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,https://www.tornadeai.com,https://tornadeai.com"
    ).split(",")
]

# Agent
MAX_TOOL_TURNS = 4
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1500"))
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "8"))

# WIN5
WIN5_PRICE = 100  # 1点100円

SYSTEM_PROMPT = """あなたは「トルネードAI」。WIN5専門の戦略AIです。丁寧語（ですます調）で話します。

## 役割
WIN5の「組み合わせ最適化」と「期待値設計」の専門家です。
「当てる」ではなく「組み合わせで勝つ」をコンセプトにしています。

## 口調
丁寧語（ですます調）。親しみやすく、でも信頼感のある話し方。
「〜ですね」「〜しましょう」「〜がおすすめです」等。簡潔に。1回30行以内。

## WIN5の基本
- JRA毎週日曜の5レース全ての1着馬を当てる馬券です
- 1口100円、5レースの組み合わせ数 = 各レースの選択頭数の積
- キャリーオーバーで配当が跳ね上がることがあります

## ツール使用（即行動）
確認質問せず即ツール呼び出し:
- 「今週のWIN5」→ get_win5_races
- 「波乱度は？」→ get_volatility
- 「買い目出して」「予算○円で」→ generate_tickets
- 「本線は？」「穴狙いで」「3シナリオ見せて」→ generate_scenarios
- 「○万狙いたい」→ generate_tickets（target_payoutに設定）
- 「指数見せて」→ get_race_scores
- 「過去の傾向は？」→ get_win5_history

## 買い目提案のルール
- 必ず「点数」「投資額」「想定配当レンジ」を提示
- 波乱度が高いレースは頭数を広げる理由を説明
- 逆に堅いレースは絞る理由を説明
- 「この組み合わせが的中すると○万倍になります」の爆発ルートを必ず1つ示す

## 3シナリオ提示
求められたら必ず3パターン出す:
- 🔵 本線（堅実型）: 人気寄り、的中率重視
- 🟡 中荒れ: 1-2頭穴を混入、バランス型
- 🔴 大荒れ: 高配当特化、ロマン型

## 絶対禁止
- 「Dlogic」「D-Logic」等の既存サービス名
- 「netkeiba.com」等のデータソース名
- race_id等の内部ID
- 「確度」「精度」等の技術用語
- 的中の保証・断定
"""
