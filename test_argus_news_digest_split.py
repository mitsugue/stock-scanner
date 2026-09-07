"""v13.5.62 — a digest mail is several articles (GPT review item 5)."""
import argus_news_intelligence as ni

DIGEST = {
    "messageId": "m-1", "subject": "日経ニュースメール 9/7 夕版",
    "fromDomain": "nikkei.com", "receivedAt": "2026-09-07T09:51:00Z",
    "headers": [("subject", "日経ニュースメール 9/7 夕版")],
    "excerpt": ("日経ニュースメール 9/7 夕版 ━ 注目ニュース ━━━━━━━\n"
                "◆円半年ぶりに154円台に上昇 円安抑止へ思惑、「有事のドル買い」後退（有料会員限定）\n"
                "7日の外国為替市場で円が一段と上昇している。\n"
                "◆ホルムズ海峡でタンカー攻撃 原油先物が急伸\n"
                "供給不安から原油高・リスクオフ。\n"
                "◆東証の上場企業、自社株買いが最多"),
}


def test_digest_mail_is_split_into_one_message_per_article():
    parts = ni.split_digest_message(DIGEST)
    assert [p["subject"] for p in parts] == [
        "円半年ぶりに154円台に上昇 円安抑止へ思惑、「有事のドル買い」後退",
        "ホルムズ海峡でタンカー攻撃 原油先物が急伸",
        "東証の上場企業、自社株買いが最多",
    ]
    assert [p["messageId"] for p in parts] == ["m-1#1", "m-1#2", "m-1#3"]
    assert all(p["digestOf"] == "m-1" and p["digestCount"] == 3 for p in parts)
    assert "ホルムズ" not in parts[0]["excerpt"] and "円が一段と上昇" in parts[0]["excerpt"]
    assert parts[0]["fromDomain"] == "nikkei.com" and parts[0]["headers"] == DIGEST["headers"]


def test_each_article_is_classified_on_its_own_text():
    parts = ni.split_digest_message(DIGEST)
    yen = ni.classify_event(parts[0]["subject"], parts[0]["excerpt"])
    oil = ni.classify_event(parts[1]["subject"], parts[1]["excerpt"])
    assert yen["eventType"] != oil["eventType"] or oil["eventType"] in ("IRAN", "OIL", "GEOPOLITICS")
    headline = ni.summarize_headline_ja(subject=parts[0]["subject"], excerpt=parts[0]["excerpt"],
                                        taxonomy=yen, ai_analysis=None, source="nikkei")
    assert headline.startswith("円半年ぶり")


def test_a_single_article_mail_is_not_split():
    single = {**DIGEST, "subject": "緩和的な財政政策と金融政策は新たな日米摩擦を招く懸念がある",
              "excerpt": "本文。◆ が一つだけ"}
    assert ni.split_digest_message(single) == [single]
    assert ni.is_digest_message(single) is False
