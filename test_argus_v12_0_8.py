"""ARGUS V12.0.8 — OSINT帰属/イベント日付真実/スタンス統一/PARTIAL理由/象限の恒久ガード。"""
import json
import os

import argus_osint_attribution as osint
import argus_primary_stance as ps
import scanner

WEB = os.path.join(os.path.dirname(__file__), "web", "src")
NOW = "2026-07-07T05:00:00Z"


def _read(*parts):
    return open(os.path.join(WEB, *parts), encoding="utf-8").read()


# ── Part A: OSINT帰属 ────────────────────────────────────────────────────────

def _cand(title, source="nikkei", published="2026-07-07T01:00:00Z", **kw):
    return {"titleJa": title, "source": source, "publishedAt": published, **kw}


def test_osint_stale_article_never_primary():
    r = osint.review("6965", "JP", -4.2, [
        _cand("ムーディーズ、大手企業の格付けを見直し", published="2024-09-13T17:02:00Z"),
        _cand("AI半導体の収益性に懸念", published="2026-07-06T22:00:00Z"),
    ], company_names=["浜松ホトニクス"], theme_words=["AI", "半導体"], now_iso=NOW)
    assert r["primary"] is not None
    assert "格付け" not in r["primary"]["titleJa"]          # 2024年記事は主因不可
    old = [c for c in r["causes"] if "格付け" in c["titleJa"]][0]
    assert old["category"] == "stale_background"
    assert old["primaryEligible"] is False


def test_osint_undated_never_primary():
    r = osint.review("6965", "JP", -4.2, [
        {"titleJa": "日付のない古そうな記事", "source": "rss"},
    ], company_names=["浜松ホトニクス"], theme_words=["AI"], now_iso=NOW)
    assert r["primary"] is None
    assert "原因不明" in r["headlineJa"]                     # 憶測で断定しない


def test_osint_direct_vs_theme_separated():
    r = osint.review("6965", "JP", -4.2, [
        _cand("浜松ホトニクス、業績予想を修正", source="tdnet"),
        _cand("SamsungとAnthropicがAIチップで提携 — AI半導体に思惑", source="reuters"),
    ], company_names=["浜松ホトニクス"], theme_words=["AI", "半導体", "Samsung", "Anthropic"],
        sector_confirm=True, now_iso=NOW)
    cats = {c["titleJa"][:6]: c["category"] for c in r["causes"]}
    assert cats["浜松ホトニク"] == "direct_official"
    assert [c for c in r["causes"] if "Samsung" in c["titleJa"]][0]["category"] == "sector_theme"
    # 直接材料が1位(テーマ連想より上)
    assert r["causes"][0]["category"].startswith("direct")


def test_osint_theme_inference_not_stated_as_fact():
    r = osint.review("6965", "JP", -4.2, [
        _cand("AI半導体バリューチェーンに収益性懸念", source="reuters"),
    ], company_names=["浜松ホトニクス"], theme_words=["AI", "半導体"], now_iso=NOW)
    assert r["primary"]["category"] == "sector_theme"
    assert "テーマ連想" in r["headlineJa"]                   # 事実として断定しない
    assert "候補" in r["headlineJa"]
    assert "浜松ホトニクス固有の開示・報道は見つかっていない" in r["primary"]["whyWrongJa"]
    assert r["osintConfidence"] in ("low", "medium")         # 連想のみはhigh不可


def test_osint_confidence_ladder():
    hi = osint.review("6965", "JP", -4.2, [
        _cand("浜松ホトニクス関連の材料A", source="tdnet"),
        _cand("浜松ホトニクス関連の報道B", source="nikkei"),
    ], company_names=["浜松ホトニクス"], sector_confirm=True, now_iso=NOW)
    assert hi["osintConfidence"] == "high"
    unk = osint.review("9999", "JP", -1.0, [], now_iso=NOW)
    assert unk["osintConfidence"] == "unknown"
    assert unk["primary"] is None


def test_osint_sources_missing_flag():
    r = osint.review("6965", "JP", -4.2, [_cand("テーマ記事", source="google_news_jp")],
                     theme_words=["テーマ"], now_iso=NOW)
    assert any("公式開示" in x for x in r["sourcesMissingJa"])


def test_osint_wired_into_cause_attribution(monkeypatch):
    monkeypatch.setitem(scanner._NEWS_JA_STATE, "restored", True)
    st = scanner.get_cause_attribution("6965", "JP")
    assert "osint" in st
    if st["osint"]:                                          # cached-only環境では空もあり得る
        assert st["osint"]["schemaVersion"] == "osint-attribution-v1"
        blob = json.dumps(st["osint"], ensure_ascii=False)
        assert "断定ではない" in blob


def test_theme_map_includes_hamamatsu():
    assert "6965" in scanner._DOWNSIDE_THEMES["ai_semis_cable"]
    assert any("Samsung" in w or "サムスン" in w
               for w in scanner._THEME_WORDS_JA["ai_semis_cable"])


# ── Part C: Primary Stance(矛盾排除の5ハードルール) ─────────────────────────

def test_stance_held_p1_never_no_action():
    r = ps.resolve({"isHeld": True, "apRank": "P1", "apLabel": "NO_ACTION",
                    "planStance": "unknown"})
    assert r["primaryStance"] == "risk_review"
    assert r["stanceJa"] == "リスク確認が先"


def test_stance_plan_risk_review_overrides_no_action():
    r = ps.resolve({"isHeld": True, "apRank": "P3", "apLabel": "NO_ACTION",
                    "planStance": "risk_review"})
    assert r["primaryStance"] == "risk_review"


def test_stance_event_wait_blocks_add_labels():
    r = ps.resolve({"isHeld": False, "apLabel": "SMALL_ADD_ALLOWED",
                    "planStance": "unknown", "eventWait": True})
    assert r["primaryStance"] == "wait_event"


def test_stance_improving_but_heavy_never_bullish():
    r = ps.resolve({"isHeld": False, "apLabel": "SMALL_ADD_ALLOWED",
                    "planStance": "unknown", "sdCondition": "improving_but_heavy"})
    assert r["primaryStance"] == "add_only_on_pullback"      # 強気化しない


def test_stance_squeeze_never_chase():
    r = ps.resolve({"isHeld": False, "apLabel": "SMALL_ADD_ALLOWED",
                    "planStance": "unknown", "sdCondition": "squeeze_prone"})
    assert r["primaryStance"] == "avoid_chase"


def test_stance_partial_data_caps_confidence_and_demotes_bullish():
    r = ps.resolve({"isHeld": False, "apLabel": "SMALL_ADD_ALLOWED",
                    "planStance": "unknown", "dataPartial": True, "baseConfidence": 0.9})
    assert r["confidence"] <= 0.55
    assert r["primaryStance"] == "unknown"                   # 強気は判定保留へ
    assert any("部分データ" in x for x in r["capNotesJa"])


def test_stance_py_ts_parity():
    # Round 3: duplicate browser stance authority is physically retired. The
    # Python reducer remains evidence-only for backend compatibility.
    assert not os.path.exists(os.path.join(WEB, "domain", "primaryStance.ts"))
    hook = _read("hooks", "useAssetIntel.ts")
    assert "resolvePrimaryStance" not in hook
    assert "stanceBySymbol" not in hook


# ── Part B: イベント日付真実 ─────────────────────────────────────────────────

def test_fe_event_rows_show_date_and_dcount():
    src = _read("components", "dashboard", "ImportantEventsCard.tsx")
    assert "eventWhenJa" in src
    # v13.5.62: the relative day lives in the shared Today/Alerts formatter
    helper = _read("domain", "argusTodayView.ts")
    assert "formatEventWhenJa" in src and "あと${diffDays}日" in helper
    assert "日時未確認" in src or "日時未確認" in helper      # 日時不明を隠さない
    # 「時刻だけ」の旧表示が復活していない
    assert "[ev.eventDate, jstFromUtc(ev.eventTimeUtc)]" not in src


def test_fe_pack_event_lines_include_date():
    src = _read("routes", "CommandCenter.tsx")
    assert "日付未確認" in src
    assert "ie.date ??" in src


def test_event_date_issues_flagged_in_dq():
    assert "dateIssues" in open("scanner.py", encoding="utf-8").read()


# ── Part D/E/F: FE検査 ──────────────────────────────────────────────────────

def test_fe_partial_data_reasons():
    vm = _read("domain", "argusTodayView.ts")
    assert "dataStatus" in vm
    assert "要確認" in vm
    panel = _read("components", "today", "ArgusTodayPanel.tsx")
    assert "DATA QUALITY" in panel
    assert "view.dataStatus" in panel
    settings = _read("routes", "Settings.tsx")
    assert "PublicDiagnosticsPanel" in settings
    assert not os.path.exists(os.path.join(
        WEB, "components", "dashboard", "HeroCard.tsx"))


def test_fe_matrix_axes_and_provisional():
    # Round 1: 行列/Replayは独立ページを持たず、背景エンジンだけをTodayへ供給する。
    for parts in (
        ("components", "regime", "RegimeMatrix.tsx"),
        ("routes", "MarketRegime.tsx"),
        ("components", "marketReplay", "MarketContextReplay.tsx"),
    ):
        assert not os.path.exists(os.path.join(WEB, *parts))
    intel = _read("hooks", "useAssetIntel.ts")
    assert "useMarketRegime" in intel
    replay_types = _read("types", "chartIntelligence.ts")
    assert "marketReplay?:" in replay_types and "currentRegime" in replay_types


def test_jp_matrix_missing_data_maps_neutral():
    m = scanner._jp_regime_matrix([])
    assert m["x"] == 0.0 and m["y"] == 0.0                   # 欠損は中立(右上に寄せない)
    assert m["available"] is False
    assert m["points"] == []


def test_fe_osint_pack_and_ui():
    rp = _read("lib", "reviewPack.ts")
    assert "'osint'" in rp or "| 'osint'" in rp
    assert "公式開示・主要ニュース・セクター連想を分けて検証してください" in rp
    assert "候補であり断定ではない" in rp
    research = _read("components", "assetDesk", "AssetResearchPanel.tsx")
    assert "OsintDeepDive" in research
    deep = _read("components", "dashboard", "OsintDeepDive.tsx")
    assert "publishOsintDeep" in deep
    assert not os.path.exists(os.path.join(
        WEB, "components", "dashboard", "CauseStackCard.tsx"))


def test_fe_unified_stance_chip_everywhere():
    # Round 2: Asset DeskはSDA v2正本をdecision-first viewへ正規化して表示する。
    # 旧Primary Stanceは選択権を持たず、互換表示もSDA結果からのみ派生する。
    details = _read("components", "assetDesk", "AssetDecisionDetails.tsx")
    assert "d.decisionFirst" in details and "view.ownerActionJa" in details
    intel = _read("hooks", "useAssetIntel.ts")
    assert "evaluateSingleDecisionAuthority" in intel and "sdaBySymbol" in intel
    assert "resolvePrimaryStance" not in intel
    vm = _read("domain", "argusTodayView.ts")
    assert "canonicalDecision" in vm and "finalAction" in vm  # Todayの単一最終判断
    panel = _read("components", "today", "ArgusTodayPanel.tsx")
    assert "view.finalAction" in panel
    desk = _read("components", "assetDesk", "AssetDeskList.tsx")
    assert "intel.sdaBySymbol.get(sym)" in desk               # Asset Desk正規化入力
    assert "buildDecisionFirstView" in desk
    assert not os.path.exists(os.path.join(
        WEB, "components", "dashboard", "ActionPrioritySection.tsx"))


# ── 非漏洩/文言(新規面) ─────────────────────────────────────────────────────

def test_osint_block_leak_and_wording_safe(monkeypatch):
    monkeypatch.setitem(scanner._NEWS_JA_STATE, "restored", True)
    with scanner.app.test_client() as c:
        d = c.get("/api/argus/cause-attribution?symbol=6965&market=JP").get_json()
    blob = json.dumps(d, ensure_ascii=False)
    import argus_portfolio_sync
    assert not argus_portfolio_sync.contains_sensitive(d)
    for w in ("今すぐ買", "今すぐ売", "成行で買", "全力買い", "login_pwd", "vaultPass"):
        assert w not in blob, w


# ━━━ v12.0.8 追補(スクショ起因のtrust修正) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── 追補1: リスクチップ分離(裸のLOW RISK禁止) ───────────────────────────────

def test_addendum_risk_chips_split_in_fe():
    # Round 1: global command cardは削除。市場判断と保有リスクは各正本へ分離する。
    assert not os.path.exists(os.path.join(
        WEB, "components", "action", "CommandSummaryCard.tsx"))
    intel = _read("hooks", "useAssetIntel.ts")
    assert "保有銘柄に要確認あり" in intel
    assert "保有数量未入力" in intel
    cc = _read("routes", "CommandCenter.tsx")
    # Todayへ渡す保有文脈は端末内で粗い区分へ落とし、数量・平均単価・P/Lを
    # 市場証拠やバックエンドへ混ぜない。未計算のリスク帯はUNKNOWNで閉じる。
    assert "privacyClass: 'DEVICE_LOCAL'" in cc
    assert "positionRiskBand: 'UNKNOWN'" in cc
    for private_field in ("avgCost", "profitLoss", "pnlPct"):
        assert private_field not in cc
    portfolio = _read("domain", "portfolioDecisionView.ts")
    assert "input.risks" in portfolio and "actionQueue" in portfolio


# ── 追補2: JP OPEN ≠ JPリアルタイム ─────────────────────────────────────────

def test_addendum_jp_open_does_not_imply_realtime():
    # Todayはセッション状態と価格鮮度を別関数で表示し、OPENをRTへ昇格しない。
    src = _read("domain", "argusTodayView.ts")
    assert "function sessionLabel" in src
    assert "export function quoteDisplayLabel" in src
    assert "JP OPEN" in src and "現在 RT" in src and "遅延値" in src
    for banned in ("JP LIVE", "JP REALTIME OK"):
        assert banned not in src, banned
    assert not os.path.exists(os.path.join(
        WEB, "components", "dashboard", "MarketSessionLamps.tsx"))


# ── 追補3: 単一のイベント時計 ───────────────────────────────────────────────

def test_addendum_single_event_clock():
    app = _read("App.tsx")
    # B2a: the duplicate shell event chip is gone. Today owns the compact next
    # event and Notifications owns the full canonical event review.
    assert "nextUpcomingEvent" not in app
    assert "useImportantEvents" not in app
    vm = _read("domain", "argusTodayView.ts")
    assert "['RELEASED', 'RESOLVED']" in vm
    assert "x.at >= nowMs" in vm
    assert "future.slice(1)" in vm and "slice(0, 3)" in vm
    cc = _read("routes", "CommandCenter.tsx")
    assert "argusToday.nextEvent" in cc
    notifications = _read("routes", "NotificationsPage.tsx")
    assert "ImportantEventsCard" in notifications
    assert not os.path.exists(os.path.join(WEB, "lib", "eventClock.ts"))
    # 旧「countdown === 'D'先頭拾い」ロジックが残っていない
    assert "find((e) => e.countdown === 'D' || e.countdown === 'D-1')" not in cc


# ── 追補4: Session Briefの入れ子スクロール禁止 ──────────────────────────────

def test_addendum_brief_no_nested_scroll():
    # 独立Session Brief surfaceは削除。背景briefはTodayのreview pack入力だけに残す。
    assert not os.path.exists(os.path.join(
        WEB, "components", "dashboard", "SessionBriefSection.tsx"))
    command = _read("routes", "CommandCenter.tsx")
    assert "briefSession: sessionBrief.sessionType" in command


# ── 追補5: 出典プロビナンス ─────────────────────────────────────────────────

def test_addendum_provenance_fields_present():
    r = osint.review("6965", "JP", -4.2, [
        _cand("浜松ホトニクス、開示", source="tdnet"),
        _cand("AI半導体テーマ記事", source="reuters"),
        _cand("古い記事", published="2024-01-01T00:00:00Z"),
    ], company_names=["浜松ホトニクス"], theme_words=["AI", "半導体"], now_iso=NOW)
    for c in r["causes"]:
        assert c["sourceType"] in osint.SOURCE_TYPES
        assert c["directness"] in osint.DIRECTNESS
        assert c["freshness"] in osint.FRESHNESS
        assert c["whyThisMightBeWrongJa"]
    assert isinstance(r["evidenceCount"], int)
    direct = [c for c in r["causes"] if "浜松" in c["titleJa"]][0]
    assert direct["sourceType"] == "official_disclosure"
    assert direct["directness"] == "direct_company"
    stale = [c for c in r["causes"] if c["titleJa"] == "古い記事"][0]
    assert stale["freshness"] == "stale_14d_plus"
    assert stale["directness"] == "background"


def test_addendum_no_direct_evidence_note():
    r = osint.review("6965", "JP", -4.2, [
        _cand("SamsungとAnthropicのAIチップ提携で半導体に思惑", source="reuters"),
    ], company_names=["浜松ホトニクス"], theme_words=["AI", "半導体", "Samsung"], now_iso=NOW)
    assert r["noDirectEvidenceNoteJa"] == "原因未特定。候補はテーマ連想であり、直接材料ではありません。"
    # 直接材料があればnoteは消える
    r2 = osint.review("6965", "JP", -4.2, [
        _cand("浜松ホトニクスが業績修正", source="tdnet"),
    ], company_names=["浜松ホトニクス"], now_iso=NOW)
    assert r2["noDirectEvidenceNoteJa"] is None


# ── 追補6: 総合コマンドの買い増し禁止が下位ラベルを上書き ────────────────────

def test_addendum_global_add_prohibited_suppresses_small_add():
    r = ps.resolve({"isHeld": False, "apLabel": "SMALL_ADD_ALLOWED",
                    "planStance": "unknown", "globalAddProhibited": True})
    assert r["primaryStance"] == "deferred_today"
    assert r["stanceJa"] == "候補だが今日は保留"
    assert any("総合コマンドが買い増し禁止のため保留" in x for x in r["capNotesJa"])
    # 通常日はそのまま
    r2 = ps.resolve({"isHeld": False, "apLabel": "SMALL_ADD_ALLOWED",
                     "planStance": "unknown", "globalAddProhibited": False})
    assert r2["primaryStance"] == "small_add_allowed"


def test_addendum_global_add_prohibited_pullback_also_deferred():
    r = ps.resolve({"isHeld": False, "apLabel": "ADD_ONLY_ON_PULLBACK",
                    "planStance": "unknown", "globalAddProhibited": True})
    assert r["primaryStance"] == "deferred_today"
    assert any("押し目限定" in x for x in r["capNotesJa"])   # 条件は詳細として保存


# ── 追補7: P0/P1は対応不要を構造禁止 ────────────────────────────────────────

def test_addendum_p1_never_no_action_even_unheld():
    r = ps.resolve({"isHeld": False, "apRank": "P1", "apLabel": "NO_ACTION",
                    "planStance": "no_action"})
    assert r["primaryStance"] != "no_action"
    r2 = ps.resolve({"isHeld": True, "apRank": "P1", "apLabel": "NO_ACTION",
                     "planStance": "no_action"})
    assert r2["primaryStance"] == "risk_review"
    # 対応不要が許されるのは低優先×非保有×シグナルなしのみ
    r3 = ps.resolve({"isHeld": False, "apRank": "P3", "apLabel": "NO_ACTION",
                     "planStance": "no_action"})
    assert r3["primaryStance"] == "no_action"


# ── 追補8: CAOS遅延の数値化 ─────────────────────────────────────────────────

def test_addendum_caos_delay_numeric():
    # 旧グローバルCAOS surfaceは削除し、イベント確認はNotificationsへ一本化。
    assert not os.path.exists(os.path.join(
        WEB, "components", "dashboard", "CaosHub.tsx"))
    notifications = _read("routes", "NotificationsPage.tsx")
    assert "ImportantEventsCard" in notifications


# ── 追補9: JP行列の暫定 ─────────────────────────────────────────────────────

def test_addendum_jp_matrix_provisional_label():
    assert not os.path.exists(os.path.join(
        WEB, "components", "marketReplay", "MarketContextReplay.tsx"))
    src = _read("types", "chartIntelligence.ts")
    assert "currentRegime" in src
    assert "regimeAnalysis" in src


# ── 追補10: スクショ再現fixture(決定論) ─────────────────────────────────────

def test_addendum_screenshot_fixture_5803_held_risk_partial():
    # IMG_7900/7902系: JP開場×部分データ×保有5803リスク×総合買い増し禁止
    r = ps.resolve({
        "isHeld": True, "apRank": "P1", "apLabel": "NO_ACTION",
        "planStance": "risk_review", "scenarioDominant": "bearish",
        "sdCondition": "improving_but_heavy", "flowClass": "distribution",
        "dataPartial": True, "globalAddProhibited": True, "baseConfidence": 0.9,
    })
    assert r["primaryStance"] == "risk_review"        # 対応不要は不可能
    assert r["confidence"] <= 0.55                     # capを超えない
    assert any("部分データ" in x for x in r["capNotesJa"])


def test_addendum_screenshot_fixture_no_small_add_visible_on_prohibited_day():
    # IMG_7903/7904系: 総合=買い増し禁止の日に小さく買い増し可が主表示にならない
    for label in ("SMALL_ADD_ALLOWED", "ADD_ONLY_ON_PULLBACK"):
        r = ps.resolve({"isHeld": False, "apLabel": label,
                        "planStance": "unknown", "globalAddProhibited": True})
        assert r["stanceJa"] != "小さく買い増し可"
        assert r["stanceJa"] != "買うなら押し目限定"
        assert r["primaryStance"] == "deferred_today"


def test_addendum_stance_ts_parity_new_rules():
    assert not os.path.exists(os.path.join(WEB, "domain", "primaryStance.ts"))
    hook = _read("hooks", "useAssetIntel.ts")
    assert "globalAddProhibited" not in hook
    assert "evaluateSingleDecisionAuthority" in hook
