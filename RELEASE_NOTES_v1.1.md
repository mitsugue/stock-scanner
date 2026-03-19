# STOCK SCANNER v1.1 リリースノート
**(code name: milky-honey)**

---

## 🆕 新機能

### Gemini 2.5 Flash 統合（Ph.3.5）
ClaudeとGeminiの2モデル体制を導入。Ph.3完了後にGeminiがGoogle検索グラウンディングでTOP5銘柄をリアルタイム評価し、**Claude 70% + Gemini 30%** の複合スコアでランキングを再確定します。

### キルスイッチ（Geminiによる強制除外）

| Gemini判定 | 動作 |
|-----------|------|
| red_flag あり（重大ネガティブ材料） | 🔴 無条件でTOP3除外 |
| スコア 40点未満 | 🔴 TOP3除外 |
| スコア 40〜59点 | ⚠️ 警告付きで採用（通知に⚠️マーク） |
| スコア 60点以上 | ✅ 正常採用 |
| 全銘柄キル | 📵「本日見送り推奨」をiPhoneに通知 |

### OSINTリーク検知
NewsAPI・NHK経済RSS・Reuters JP RSS・Telegram OSINTチャンネル（warmonitor3・intelslava）からリークキーワードを検知。リーク疑い記事をGeminiのプロンプト先頭に優先配置。

### 空売り残高フィルター（Ph.1）
JPX公開データから空売り比率15%超の銘柄を機関の罠として自動除外。

### Finnhub VIX 相対的急騰検知
絶対値ではなく20日移動平均との比較で判断：

| 判定 | 条件 | 影響 |
|------|------|------|
| 🚨 SPIKE | +30%以上 | Geminiスコア上限60 |
| ⚠️ ELEVATED | +15〜29% | 警戒ログ出力 |
| ✅ NORMAL | ±14%以内 | 影響なし |
| 😌 CALM | -15%以下 | 影響なし |

### Dynamic Exit Engine（Ph.5 完全リニューアル）
状態遷移型の出口戦略エンジン。固定-3%損切りを廃止し、材料・需給・時間軸を統合した6状態マシンで判断します。

**状態定義：**

| 状態 | 意味 |
|------|------|
| S0 OPEN_DISCOVERY | 寄り直後・判断保留 |
| S1 SHAKEOUT_CANDIDATE | 急落したがふるい落とし候補 |
| S2 HEALTHY_UPTREND | 健全な上昇・ホールド継続 |
| S3 DISTRIBUTION_WARN | 上で配り始めている警戒 |
| S4 THESIS_BROKEN | 材料崩壊・全決済 |
| S5 PARABOLIC_TAKEPROFIT | 急騰利確・全決済 |

**HoldScore / ExitScore（0〜100点）：**

```
HoldScore加点: 材料グレードA:+25, B:+15 / VWAP奪回:+15 / 政治テーマ:最大+20
               プラ転回復:+20 / 出来高増加で下落（吸収）:+10
HoldScore減点: VIX SPIKE:-30, ELEVATED:-15 / グレードD:-20

ExitScore加点: 材料崩壊:即100 / VWAP失敗×15 / VIX SPIKE:+35
               C/D級+-3%下落:+20 / 出来高減少で下落:+20
```

**Rule C3：** C/D級材料で60分後も利益ゼロ → 時間切れ撤退

### 材料グレード分類（A/B/C/D）
Ph.4でTOP3銘柄にグレードを付与しPh.5に引き継ぎます。

| グレード | 内容 |
|---------|------|
| A | 純業績系（上方修正・増配・自社株買い・最高益） |
| B | 業績+テーマ混合（AI・半導体・防衛・GX） |
| C | テーマ・思惑中心 |
| D | 仕手・SNS・低位煽り |

### 政治・世論スコア（POLITICAL_THEMES）
現政権テーマとセクターをマッチングし最大+20点のボーナスを付与。
対応テーマ：高市、防衛費増額、GX、半導体補助金、AI投資、インバウンド

---

## 🔧 改修

### EDINET解析：思想スコア → 業績カタリスト
経営者の理念・思想スコアを廃止。売上高・上方修正・営業利益・受注残高などの純粋な数値ベースの業績強材料のみを抽出する軽量処理に変更。

### get_news：日英両言語対応 + OSINT拡張
NewsAPIを英語・日本語の両方で取得。RSSフィード・Telegramチャンネルを追加。

### スケジューラーをJST基準に修正
scheduleライブラリ（UTC基準）を廃止。pytzでJST時刻を30秒ごとに確認する独自実装に変更。UTC 08:00（JST 17:00）に誤動作していた問題を解消。

### market_condition / macro_summary の全フェーズ引き継ぎ
Ph.1で取得した地合い・マクロサマリーをPh.2〜Ph.4のstate.updateで確実に引き継ぐよう修正。

### エラー時の即時フロントエンド通知
各フェーズのexceptでエラー発生時にsave_state(aborted=True)を呼び出し、UIのフリーズを防止。

---

## 🎨 UI改善

### 右上バッジのリアルタイム進捗表示
フェーズとアクション名が動的に切り替わります：
Ph.1 Fetching stocks 15% → Ph.1 100% DONE → Ph.2 Re-Score 30% → ...

- フェーズ完了時に 100% DONE を3秒間表示してから次フェーズへ移行
- ログからの実フェーズ検知で表示ズレを解消（lastLogsベース）
- BACKGROUND_TASK_RUNNINGグローバル変数でスキャン中判定を確実化

### VIX / S&P500パネル常時表示
ページ読み込み直後から表示。データ取得前は VIX: -- S&P500: -- を表示し、取得後に実値へ更新。

### Ph.5タブの即時表示
Ph.4完了直後からPh.5タブが表示されます。Ph.5ボタン押下後にチャートが展開し、AI評価は非同期で追記されます。

### ブラウザキャッシュ対策
- fetch('/api/state?t='+Date.now()) によるキャッシュバスター
- Cache-Control: no-cache ヘッダーをFlaskのafter_requestで全レスポンスに付与

### リロード後の状態復元
保存済みログとLOG_BUFFERをマージして返すことで、ページリロード後もスキャン履歴が維持されます。

---

## 🏗 インフラ

| 項目 | v1.0 | v1.1 |
|------|------|------|
| Railway URL | affectionate-fascination-production | milky-honey-production |
| GitHub ブランチ | main | v1.1 → main |
| Python バージョン | 3.12 | 3.12 |
| スケジューラー | schedule（UTC基準） | pytz JST基準（独自実装） |
| 環境変数（追加） | — | GEMINI_API_KEY / FINNHUB_API_KEY |
| 必須ライブラリ（追加） | — | google-genai |

---

## 🐛 主なバグ修正

| バグ | 修正内容 |
|------|---------|
| HTMLテンプレート消失（404） | Dynamic Exit Engine追加時の削除ミスを復元 |
| @app.route('/api/state') 孤立デコレータ | 削除（index()に誤接続していた） |
| get_realtime_prices 重複定義 | 1つに統合 |
| EXIT_STATE_* 定数未定義（NameError） | LOG_BUFFER前に全6定数を定義 |
| JQuants APIキー有効期限問題 | IDトークン方式で運用（JQUANTS_API_KEY） |
| timeout重複指定（SyntaxError） | 1つに統合 |
| VIX elseブロックでdisplay:noneに戻る | display:''（常時表示）に修正 |
| スキャン中にscanning:trueが永続 | BACKGROUND_TASK_RUNNING + finallyで確実にリセット |

---

*STOCK SCANNER v1.1-release — 2026年3月*
