# -*- coding: utf-8 -*-
"""講習会関連画面への入口をまとめたハブページ。"""

from html import escape


HUB_SECTIONS = [
    ("講習会自体", [
        ("/camps", "講習会マスタ"),
    ]),
    ("生徒情報", [
        ("/camp-enrollments", "講習会 受講科目回数登録"),
        ("/camp-sync-groups", "兄弟等 同時受講グループ"),
        ("/camp-availability-student", "生徒 講習会中の対応可能時間"),
        ("/image-import", "記入用紙 PDF取り込み"),
        ("/image-import-review", "取込結果確認"),
        ("/image-import-corrections", "確定済みレビューの訂正"),
    ]),
    ("講師情報", [
        ("/camp-availability-instructor", "講師 講習会中の対応可能時間"),
    ]),
    ("実行・確認", [
        ("/run-scheduler", "スケジューリングの実行"),
        ("/schedule-by-day", "日付単位の授業スケジュール"),
    ]),
]


def render(qs: dict, message_html: str = "") -> str:
    cards = []
    for title, links in HUB_SECTIONS:
        link_items = "".join(
            f'<li><a href="{escape(path, quote=True)}">{escape(label)}</a></li>'
            for path, label in links
        )
        cards.append(
            f'<section class="hub-card"><h2>{escape(title)}</h2><ul>{link_items}</ul></section>'
        )

    return f"""
    <h1>講習会</h1>
    <div class="hint">講習会の登録からスケジューリング結果の確認まで、目的に応じた画面を選択してください。</div>
    <div class="hub-grid">{"".join(cards)}</div>
    {message_html}
    """
