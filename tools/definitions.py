"""WIN5 tool definitions for Claude Tool Use."""

TOOLS = [
    {
        "name": "get_win5_races",
        "description": "今週（または指定日）のWIN5対象5レースを取得します。各レースの会場、レース番号、距離、出走頭数、波乱度ランクを返します。",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "対象日付 (YYYYMMDD形式)。省略時は今週の日曜。"
                }
            },
            "required": []
        }
    },
    {
        "name": "get_race_scores",
        "description": "WIN5対象レースの全馬指数を取得します。AI勝率、市場オッズ、期待値スコア、4エンジンのランクを返します。",
        "input_schema": {
            "type": "object",
            "properties": {
                "race_order": {
                    "type": "integer",
                    "description": "WIN5の何レース目か (1-5)。省略時は全5レース。"
                }
            },
            "required": []
        }
    },
    {
        "name": "get_volatility",
        "description": "WIN5対象5レースの波乱度ランク（1-5段階）を取得します。各レースの混戦度、人気信頼度、総合波乱度を返します。",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "generate_tickets",
        "description": "予算と目標払戻額からWIN5の最適な買い目を自動生成します。各レースの選択馬番、点数、想定配当レンジ、的中確率、期待値を返します。「買い目出して」「予算○円で」「○万狙いたい」等で使ってください。",
        "input_schema": {
            "type": "object",
            "properties": {
                "budget": {
                    "type": "integer",
                    "description": "予算（円）。デフォルト5000。"
                },
                "target_payout": {
                    "type": "integer",
                    "description": "目標払戻額（円）。デフォルト1000000（100万）。"
                },
                "risk_level": {
                    "type": "string",
                    "enum": ["conservative", "balanced", "aggressive"],
                    "description": "リスクレベル。conservative=堅実、balanced=バランス、aggressive=攻め。デフォルトbalanced。"
                }
            },
            "required": []
        }
    },
    {
        "name": "generate_scenarios",
        "description": "WIN5の3シナリオ（本線/中荒れ/大荒れ）を生成します。各シナリオごとに買い目、点数、想定配当を返します。「シナリオ見せて」「本線は？」「穴狙いで」等で使ってください。",
        "input_schema": {
            "type": "object",
            "properties": {
                "budget": {
                    "type": "integer",
                    "description": "予算（円）。デフォルト5000。"
                }
            },
            "required": []
        }
    },
    {
        "name": "simulate_payout",
        "description": "指定した買い目の想定払戻額を計算します。各組み合わせの配当レンジ、的中確率、期待値を返します。",
        "input_schema": {
            "type": "object",
            "properties": {
                "tickets": {
                    "type": "object",
                    "description": "買い目。{\"R1\": [1,3], \"R2\": [5], \"R3\": [2,7], \"R4\": [4], \"R5\": [1,6]}"
                }
            },
            "required": ["tickets"]
        }
    },
    {
        "name": "get_win5_history",
        "description": "過去のWIN5結果と傾向を取得します。配当レンジの分布、波乱傾向、キャリーオーバー履歴を返します。",
        "input_schema": {
            "type": "object",
            "properties": {
                "weeks": {
                    "type": "integer",
                    "description": "過去何週分か。デフォルト10。"
                }
            },
            "required": []
        }
    },
    {
        "name": "get_carryover",
        "description": "今週のWIN5キャリーオーバー情報を取得します。キャリーオーバー額と、それに応じた戦略推奨を返します。",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
]
