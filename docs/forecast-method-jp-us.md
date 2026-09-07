# ARGUS 予測方式 対応表（v13.5.62・2026-09-07）

GPT レビュー項目 3「日本・米国の予測方式を確定する」への回答。画面の「類似局面の頻度」と
「MARKET SIGNALS」「反転/下方」がそれぞれ何を、どの入力で、どう算出し、何を検証して
いるかを 1 表にまとめる。**割合は類似局面での出現頻度であり、検証済み予測確率ではない。**

## 1. 日本（1321 / 1306 を判断主体、指数を表示系列）

| 要件 | 入力（PIT 結合・知識ラグ付き） | 算式 | 出力 | 検証 |
|---|---|---|---|---|
| SHO 7 ファミリー（D01〜D07） | D01 二市場信用残（JPX CSV + 台帳）、D02 1570 信用倍率（J-Quants 週次）、D03 日米相対力（1321 vs SPY 20 営業日）、D04 EPS/PER（ライセンス未取得＝欠測）、D05 海外投資家フロー（台帳）、D06 VIX 水準/10 日変化（Yahoo ^VIX）、D07 決算反応（J-Quants fins/summary + 日足） | `argus_sho.evaluate_d01_d07`：各ファミリーの条件成立/不成立/判定不能を命題台帳（sealed registry）どおりに決定論で評価 | `families[D0x].conditionMet`、`marketSignals SIG-01..07 activeCount/7` | 命題台帳の SHA 固定、`test_argus_sho*`、本番の sourceStatus で各入力の供給元を明示 |
| 反転/下方（市場観） | ^N225 日足、^VIX 日足 | `argus_sho.build_reversal_engine`（反転: RECOVERY_TEST 等、下方: MIXED 等） | `reversal.reversalState / downsideState` | 行動権限なし（`actionAuthority=false`）。SDA 入力にしない |
| チャート予測（上限/下限/本線/無効・UP/RANGE/DOWN） | 判断主体の日足（J-Quants 10 年）、SHO 状態の日次系列（creditRatio, creditShortTn, vixLevel, vixChange10, rs20） | `argus_today_intelligence`（today-replay-calibration-v3-sho-conditioned）：SHO 状態でスケールした特徴空間で類似局面 kNN → 1/5/20 営業日先の終値方向の出現頻度・ATR14 の帯・支持抵抗 | `directionProbabilities`（頻度）、`upside/downside/invalidation`、`shoConditioning{currentFeatureKeys, coverageDays, sourceIssues}` | walk-forward Brier / BSS、独立 holdout。現在 BSS ≈ −0.003〜−0.007 ＝ **予測力未証明** → 画面は「検証済み確率ではない」と表示、BUY は構造的に無効 |
| 切替の明示 | SHO 状態の全特徴が取れない日 | 無条件の類似局面へ切替 | `shoConditioningJa` = 「SHO条件なし（無条件の類似局面）」または「SHO条件を取得できず無条件の類似局面へ切替中」 | 供給設定障害（例: FRED 鍵未設定）は `sourceIssues` で名指し |

## 2. 米国（SPY / QQQ）

| 要件 | 入力 | 算式 | 出力 | 検証 |
|---|---|---|---|---|
| SHO 7 ファミリー | **適用外**（信用残・1570・海外フローは日本固有） | — | Today の MARKET SIGNALS は日本の値を表示（米国選択時も同じ文書・同じ時刻） | — |
| 反転/下方 | 適用外 | — | 米国では表示しない | — |
| チャート予測 | SPY/QQQ 日足（Twelve Data）、条件は米国側の代替入力: `spy-qqq-relative`（QQQ/SPY 20 営業日）、`us-volume-proxy`（出来高比）、`market-regime`、VIX 水準/変化 | 同じ replay エンジン。SHO の日本固有特徴は使わず、上記の代替特徴で条件付け | 同上 | 同上（BSS 未証明） |
| 需給 | NASDAQ 対 SPY 相対、出来高比 | 決定論 | 「米国株」ブロックの US 需給 | 表示のみ |

## 3. 画面上の表記ルール

- 「類似局面の頻度 — 検証済み確率ではない（xD・参考のみ）」: 頻度である旨を常に表示。
- 「終値方向（検証済み）」: 確率適格性（`probability-eligibility-v1`: n≥100/60、holdout 再現、基準予測優位、Wilson 幅 ≤10pt、ECE ≤0.05、breadth 鮮度、分割整合）を全て満たした場合のみ。現在は未達。
- 「方式と根拠の詳細」（タップ）: 条件付け状態、実効 n、BSS、不確実性の理由。
- MARKET SIGNALS と市場要約の「成立 x/7」は同じ market-view 文書・同じ情報時刻（HH:MM 時点）から描画。
