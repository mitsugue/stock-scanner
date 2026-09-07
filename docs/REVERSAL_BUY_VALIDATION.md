# 反転軸 BUY の検証計画と現在の結果（v13.5.63・2026-09-07）

GPT レビュー追加項目 2「BUY の検証を完了へ進める」への回答。BUY は検証が終わるまで
構造的に無効（`VERIFIED_SHO_BUY_ARTIFACTS` が空）のまま。ここでは **必要データ・対象期間・
合格条件・有効化までの作業** を固定し、その検証を実行できるスクリプトと、今日の結果を置く。
画面には方式の個人名を出さない（画面表記は「需給・トレンド方式」）。

## 1. 何を検証するか

BUY 候補になる反転状態は 4 つ（`argus_single_decision.BUY_ELIGIBLE_SHO_STATES`）:
`REVERSAL_EARLY` / `TECHNICAL_REBOUND` / `RECOVERY_TEST` / `CONFIRMED_ADVANCE`。
状態は本番と同じ `argus_sho.build_reversal_engine(downside_background="MIXED")` で、
^N225（分析主体）と ^VIX（第 2 軸）の完全 OHLCV から決定論に再構成する。

問い: **「その状態に入った翌営業日の終値で 1321 を買ったとき、5 / 20 営業日後に上がって
いた頻度は、無条件（毎日買う）より本当に高いか」**。

## 2. 必要データ

| 系列 | 用途 | 出所（検証用） | 本番の出所 |
|---|---|---|---|
| NIKKEI_225_INDEX 日足 OHLCV | 反転軸の分析主体 | Yahoo v8 chart `^N225` 10 年 | `_yahoo_index_ohlcv("^N225")` |
| VIX 日足 OHLCV | 第 2 軸（MACD デッドクロス） | Yahoo v8 chart `^VIX` 10 年 | `_sho_vix_rows` |
| 1321 日足終値 | 判断主体の成績 | Yahoo v8 chart `1321.T` 10 年 | J-Quants |

不完全な足（O/H/L/C/V のいずれか欠落）は補完せず除外する。各足は `availableFrom` を持ち、
判定日 D の cutoff は `D T23:59:59Z`（米国引け後）。**エントリーは D の翌営業日の終値** なので、
状態が自分の結果を見ることは構造的にできない（スクリプト内で assert）。

## 3. 対象期間と分割

- コーパス: 2016-09-07 〜 2026-09-07（10 年）
- in-sample: 〜2022-12-31（状態別の的中率をここで推定）
- holdout: 2023-01-01〜（in-sample の推定値をそのまま当てはめて採点。再推定しない）
- エピソード: 適格状態への **遷移** ごとに 1 件、5 営業日のクールダウン（replay エンジンと同じ）

## 4. 合格条件（probability-eligibility-v1 と同族）

| 条件 | 閾値 |
|---|---|
| in-sample エピソード数 | ≥ 100 |
| holdout エピソード数 | ≥ 60 |
| holdout 的中率の Wilson 95% 半幅 | ≤ 10pt |
| holdout の Brier skill（無条件の基準率に対して） | > 0（5 日・20 日とも） |
| holdout の Wilson 下限 | 基準率（無条件で上昇した頻度）より上（5 日・20 日とも） |
| 状態別 ECE（in-sample 推定 vs holdout 実現、件数加重） | ≤ 0.05 |
| 未来参照 | なし（構造的に保証） |

すべて満たして初めて **PASS**。PASS の報告 JSON を独立に再現し、コードレビューで
`VERIFIED_SHO_BUY_ARTIFACTS` に artifact identity を固定した時点で、はじめて画面に BUY が出る。

## 5. 実行方法

```bash
python3 scripts/reversal_buy_validation.py --fetch --data-dir /tmp/buyval --out /tmp/buyval/report.json
```

約 1 分。ネットワークが使えない環境では `--data-dir` に Yahoo v8 chart JSON
（`y_N225.json` / `y_VIX.json` / `y_1321_T.json`）を置く。

## 6. 2026-09-07 の結果 — **FAIL（BUY は無効のまま）**

| 項目 | 値 |
|---|---|
| 状態日数（2442 営業日） | MIXED 1547 / REVERSAL_EARLY 238 / RECOVERY_TEST 232 / FALSE_RALLY 220 / TECHNICAL_REBOUND 31 / CONFIRMED_ADVANCE 14 |
| エピソード | 220（in-sample 136・holdout 84）→ 件数条件は **満たす** |
| 5 日 | 的中 in 57.4% → holdout 64.3%、基準率 59.6%、Wilson [53.6%, 73.7%]（半幅 10.1pt）、BSS +0.019、ECE 0.072 |
| 20 日 | 的中 in 59.6% → holdout 67.9%、基準率 66.9%、Wilson [57.3%, 76.9%]、BSS +0.016、ECE 0.097 |
| 落ちた条件 | 5 日: Wilson 半幅 > 10pt・下限が基準率以下・ECE > 0.05 ／ 20 日: 下限が基準率以下・ECE > 0.05 |

読み方: 適格状態のあとに上がる頻度は無条件よりわずかに高い（BSS は正）が、**差は統計的に
区別できず**（信頼区間が基準率を含む）、状態別の的中率も in-sample と holdout で一致しない
（ECE）。したがって「BUY と言える根拠」はまだ無い。これは「校正データが必要」という説明ではなく、
実データで測った不合格。

## 7. 有効化までの作業計画

1. **判定の分解能を上げる**（最大の欠陥は holdout 84 件での区間幅）: 反転軸に加えて
   D01〜D07 の需給ファミリー（信用倍率・売り残・海外フロー）で条件付けした状態を評価に加え、
   状態の細分化で的中率の差が出るかを同じスクリプトで測る（`--conditioning` オプション追加）。
2. **サンプルを増やす**: J-Quants の 1321 日足を 2011 年まで遡り（V2 API の 10 年超は
   要プラン確認）、^N225/^VIX は 20 年に延長。件数が倍になれば Wilson 半幅は約 7pt。
3. **基準を 2 つにする**: 無条件基準に加えてモメンタム基準（20 日トレンド > 0 の日）にも
   勝つこと（`probabilityTruthEvidence.beatsMomentum` を None から実測に）。
4. **月次で再実行**して報告 JSON を `docs/evidence/` に積む。3 回連続 PASS で初めて
   レジストリ固定の PR を出す（固定は製品 PR・レビュー必須）。
5. 画面: 有効化前は「BUYは検証完了まで出ません」を維持し、タップ先に本ドキュメントの
   数値（直近の verdict と落ちた条件）を表示する（v13.5.63 で文言を接続）。

## 8. 変更履歴

- v13.5.63: 初版。スクリプト `scripts/reversal_buy_validation.py` と本結果を追加。

## 9. 本番での確認（2026-09-08・v13.5.63 / eb6f89ed）

- BUY は本番でも構造的に無効のまま（`shoBuyEligible=false`、レジストリ空）。画面の「BUYが出る条件」は本ドキュメントを指す。
- 反転軸の状態は本番の `argus_sho.build_reversal_engine`（^N225 / ^VIX の完全 OHLCV、MIXED 背景）と同じ経路で再構成しており、上記 §6 の FAIL 判定が有効化を止めている根拠である。
- 次回の再実行は §7 の計画どおり月次。PASS が 3 回続くまでレジストリ固定の PR は出さない。
