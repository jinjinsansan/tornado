# CLAUDE.md - TornadoAI (WIN5特化予想サービス)

## プロジェクト概要
WIN5のみに特化した競馬AI予想サービス「トルネードAI」。
既存のDlogic予想エンジン（port 8000）を共有しつつ、ブランドは完全分離。
WebChat（Next.js + Vercel）でAIエージェントが自然言語で買い目相談に応える。

## 重要: ブランド分離
- **Dlogic / D-Logic / ディーロジ** の名前は一切出さない
- ブランド名は「トルネードAI」
- フロントエンド: 別ドメイン
- Supabase: 別プロジェクト
- LINE: 別チャネル

## VPS接続情報
- 同じVPS: 220.158.24.157
- ポート: 5001（Dlogicの5000とは別）
- ディレクトリ: /opt/dlogic/tornado/

## アーキテクチャ
```
フロントエンド (Vercel) → nginx → port 5001 (Flask/Gunicorn)
                                       ↓
                                 Claude API (Tool Use)
                                       ↓
                                 tools/executor.py
                                       ↓
                              port 8000 (予想エンジン共有)
```

## コア機能
1. WIN5対象レース自動取得
2. 全馬指数 + 波乱度ランク（5段階）
3. 買い目ジェネレーター（予算×目標×リスク→最適化）
4. 3シナリオ（本線/中荒れ/大荒れ）
5. AIチャット相談

## デプロイ
```bash
scp -r . root@220.158.24.157:/opt/dlogic/tornado/
ssh root@220.158.24.157 "systemctl restart tornado-ai"
```
