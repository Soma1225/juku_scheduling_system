# -*- coding: utf-8 -*-
"""page_home.py: ホーム画面(カード型メニュー)"""

from layout import MENU

ICONS = {
    "/students": "🎓", "/instructors": "🧑‍🏫", "/subjects": "📚", "/terms": "🗓️",
    "/periods": "⏰", "/student-availability": "🗂️", "/instructor-availability": "🗂️",
    "/instructor-subjects": "🔗",
}
DESCRIPTIONS = {
    "/students": "生徒の氏名・学年などを登録します",
    "/instructors": "講師の氏名を登録します",
    "/subjects": "指導する科目の一覧を登録します",
    "/terms": "前期・後期などの学期を登録します",
    "/periods": "1〜5限の時刻を編集します",
    "/student-availability": "生徒の通塾可能な曜日・限を登録します",
    "/instructor-availability": "講師の勤務可能な曜日・限を登録します",
    "/instructor-subjects": "講師が担当できる科目を登録します",
}


def render(qs: dict, message_html: str = "") -> str:
    cards = "".join(
        f"""
        <a class="menu-card" href="{path}">
          <div class="menu-card-icon">{ICONS.get(path, "📄")}</div>
          <div class="menu-card-body">
            <div class="menu-card-title">{label}</div>
            <div class="menu-card-desc">{DESCRIPTIONS.get(path, "")}</div>
          </div>
          <div class="menu-card-arrow">›</div>
        </a>
        """
        for path, label in MENU[1:]
    )
    return f"""
    <h1>データ入力ポータル</h1>
    <div class="hint">入力したい項目を選んでください</div>
    <div class="menu-grid">{cards}</div>
    """
