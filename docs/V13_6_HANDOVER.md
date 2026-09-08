# v13.6.0 引き継ぎ文書（13.5 系の到達点・2026-09-08）

v13.6.0（統合 AI と画面リニューアル、Codex + Astra）に引き継ぐための一枚。13.5 系は
「実データを取得・保存・判断・端末表示する」ことを本番で通し、判断の勝手な合格や見えない
警告を排した状態で終える。新しい統合 AI・新予測エンジンはここには入っていない。

## 1. 本番の識別情報（この文書の時点）

| 面 | 識別子 | 確認方法 |
|---|---|---|
| バックエンド | Render `argus-backend-3j2m`、`/healthz` の `backendVersion` / `buildSha` | `curl https://argus-backend-3j2m.onrender.com/healthz` |
| フロント | GitHub Pages `https://mitsugue.github.io/argus/`、`__ARGUS_VERSION__` | ページソースの先頭 8KB |
| 製品バージョン | `product-version.json` / `backend-version.json` / `web/package.json` の三点同期 | `test_argus_deploy_scope.py::test_no_stale_version_pin_survives_a_bump` |
| 配信経路 | 製品 PR → `deploy-pages.yml`（証明書・readiness・acceptance）／Recovery PR → Render 自動デプロイ + `backend-warm-after-deploy.yml` | Actions |

到達バージョンと確認結果は末尾「7. 完了時の本番確認」に記す。

## 2. 未解決事項（v13.6.0 で扱うもの）

1. **BUY は構造的に無効のまま。** 反転軸 BUY の検証は 2026-09-07 に FAIL
   （`docs/REVERSAL_BUY_VALIDATION.md`）。有効化には同ドキュメント §7 の計画（条件細分化、
   サンプル延長、モメンタム基準、月次 3 回 PASS）とコードレビューでのレジストリ固定が必要。
2. **予測は「類似局面の頻度」で、検証済み確率ではない。** walk-forward BSS ≈ −0.003〜+0.003。
   `probability-eligibility-v1` の全条件を満たすまで確率表示はしない。
3. **信用需給（二市場信用残）は週次の公式 xls を取り込む運用。** `jpx-credit-weekly.yml`
   （水・木 19:30 JST）が JPX の週次ファイルを取得し Market Ledger に入れる。未公表週は
   gap として報告するのみ。2026-07-17 / 07-24 は JPX 側に公表ファイルが無い。
4. **ニュース取込は 1 プロセス内の逐次処理。** 読み出しはロックを待たない（13.5.66）が、
   1 サイクル内の AI 解析は逐次で、バックフィル時は数十分かかる。並列化は未実施。
5. **Recovery ゲート（checkpoint-v2）の測定契約。** allocator の絶対上限は「使用中バイト」と
   「復元元サイズ相対」に分けた（`docs/checkpoint-v2-mapping-attribution.md` v13.5.64）。
   本番スナップショットが今後さらに大きくなれば `allocatorAnonymousBytes`（256 MiB）に
   当たる可能性がある。上限を触る前に同ドキュメントの実測表を更新すること。
6. **Recovery マージは Pages を走らせない。** 証明書を持たないため。代わりに
   `backend-warm-after-deploy.yml` が Render の反映を待ってウォームする。
7. **コスト方針の使用台帳は永続ルートへ write-through**（13.5.66）。ジャーナル
   スナップショットが古くても再デプロイ後に union 復元する。並行実行は予約行で防ぐ。
8. **Tachibana（立花）ライブは読み取り専用シャドー。** 本番の秘密鍵ファイルの形式問題
   （`AUTH_KEY_PARSE_FAILED`）はオーナー側の再アップロード待ち。
9. **EC2 ブリッジ（moomoo）は US のみ。** JP はブリッジ対象外（J-Quants / Tachibana）。
10. **画面に方式の個人名を出さない。** 画面は「需給・トレンド方式/条件」。コード内の識別子
    （`argus_sho`、`shoConditioning` 等）は据え置き。

## 3. 利用中のデータ源

| データ | 供給元 | 頻度 / 遅延 | 状態の見方 |
|---|---|---|---|
| 日本株 日足・週足 | J-Quants V2（Standard、`JQUANTS_API_KEY`） | 日次、16:30 JST 更新、完了セッション後 15 分再確認 | `decision-evidence` の `marketTruth.status` |
| 米国株 日足 | Twelve Data（BASIC、9 銘柄 warm、閉場時 6 時間ごと cold fill） | 日次 | `data-quality/status` |
| 指数 ^N225 / ^VIX / SPY | Yahoo v8 chart（OHLCV、PIT 付与） | 日次 | `marketView.sourceStatus` |
| 二市場信用残（D01） | JPX 公式週次 xls → `ops/imports/*.csv`（〜2026-07-10）+ Market Ledger 取込（それ以降） | 週次、金曜締め・水曜公表 | Today「方式と根拠の詳細」の 信用残 行 |
| 1570 信用倍率（D02） | J-Quants 週次 | 週次 | `marketView.projection.families.D02` |
| 海外投資家フロー（D05） | 台帳（現在 missing） | 週次 | `sourceStatus.foreignFlow` |
| VIX（D06） | Yahoo ^VIX（FRED は鍵未設定） | 日次 | `sourceIssues` |
| 決算（D07） | J-Quants fins/summary | 随時 | `families.D07` |
| 為替・金利 | `/api/argus/rates`（FRED / Yahoo） | 日次 | Today MACRO 行 |
| イベント台帳 | `argus_important_events`（BLS/FOMC/BOJ/財務省入札など、31 日先） | 2 時間ごと更新 | `/api/argus/important-events` |
| ニュース | Gmail 購読メール（日経・BOJ・OFAC 等、認証送信元のみ） | 75 秒ごと取込 | `/api/argus/news-intake/health` |
| 仮想通貨 | CoinGecko（SYMBOL_TO_COINGECKO） | 準リアルタイム | Holdings のバッジ |
| 投信 | 投信総合ライブラリー | 日次 | 同上 |

## 4. 利用中の AI モデルと予算

| 用途 | モデル（既定） | 環境変数 | 予算・上限 |
|---|---|---|---|
| イベント事前/事後シナリオ | `gpt-6-astra`（不可時のみ `gpt-5.6-terra` に代替、両方を記録） | `ARGUS_OPENAI_MODEL_EVENT` / `_FALLBACK` | 1 日 6 回、$0.08/回見積、イベント枠予備 $0.50 |
| ニュース解析（news_intel） | `gpt-5.6-terra`（難案件のみ `gpt-5.6-sol` へ 1 回昇格） | `OPENAI_MODEL` / `ARGUS_OPENAI_SOL_MODEL` | SCHEDULED 日次 $2.00 − 予備 $0.50 |
| 見出し翻訳 | Gemini（`GEMINI_API_KEY`） | — | 同上の枠 |
| 単価（公式・2026-09-07 参照） | astra $10/$1(cached)/$50、terra $2/$0.2/$12、sol $4/$0.4/$20 per 1M | `_AI_PRICING` | `OPENAI_PRICE_*` で上書き可 |
| 全体 | 日次 $5、月次 $80、緊急予備 $2（推定値のハード停止） | `AI_DAILY_BUDGET_USD` 等 | `/api/argus/ai-cost`（admin） |

方針モード: `SCHEDULED_AI`（`ARGUS_EVENT_AI_OPT_IN=1`）。公開ステータス
`/api/argus/cost-policy` に 鍵の有無・枠予算・実行回数・直近実行・直近見送り理由・台帳の
耐久性 を分けて出す。

## 5. 既存の判断仕様（SDA v2、変更なし）

- 5 アクション: BUY / HOLD / WAIT / REDUCE / EXIT。BUY は ①リスク制約なし ②需給・トレンドの
  反転状態が反転初期・自律反発・回復試験・上昇確認のいずれかで **検証済み** ③検証済み買い成立
  レジストリ（`VERIFIED_SHO_BUY_ARTIFACTS`、現在は空）の本番採用 ④保有側の追加許可。
- 確度 = min(基準値 {BUY 70, HOLD 60, WAIT 45, REDUCE 70, EXIT 80}%, riskKernel 上限)。
  データ不足時は 25% 固定。
- REDUCE は保有の含み損 −25% 以下（`positionExposure`）で `REDUCE_RISK`。
- WAIT の 3 種: データ不足（評価未了）／リスク制約／買い条件不成立。
- 判断の正本は `decision-evidence`（8 銘柄/要求、端末側は 8 件ずつ全登録銘柄）。
- MARKET SIGNALS（7 条件、日本固有）は市場観であり行動権限を持たない
  （`actionAuthority=false`）。米国選択時は「日本市場の値」と明示。

## 6. 既存の予測仕様（変更なし）

- チャート予測: `argus_today_intelligence`（today-replay-calibration-v3-sho-conditioned）。
  10 年日足の類似局面 kNN（trend20 / momentum5 / atrPct / closeLocation / volumeRatio）に、
  日本は 信用倍率・売り残高・VIX 水準・VIX 10 日変化・対 SPY 相対力、米国は VIX 水準・
  VIX 10 日変化・対 SPY 相対力 で条件付け（知識ラグ付き PIT 結合、結合窓 信用 45 日・VIX 10 日）。
  出力は 1/5/20 営業日先の方向の **出現頻度**、ATR14 帯、支持抵抗。
- 反転/下方軸: `argus_sho.build_reversal_engine`（^N225 と ^VIX の MACD/SAR/BB/RSI）、
  状態 REVERSAL_EARLY / TECHNICAL_REBOUND / RECOVERY_TEST / CONFIRMED_ADVANCE / FALSE_RALLY /
  MIXED。行動権限なし。
- 対応表: `docs/forecast-method-jp-us.md`。BUY 検証: `docs/REVERSAL_BUY_VALIDATION.md`。

## 7. 完了時の本番確認（記入）

完了報告の各項目（配信識別子・実画面・定期実行での生成→保存→表示・再デプロイ後の台帳・
取込中のニュース応答）は最終報告に記載し、ここには識別子のみを残す。

- バックエンド: （最終報告で記入）
- フロント: （最終報告で記入）
