# -*- coding: utf-8 -*-
"""page_home.py: ホーム画面(カード型メニュー)"""

from layout import MENU

ICONS = {
    "/students": "🎓", "/instructors": "🧑‍🏫", "/subjects": "📚", "/terms": "🗓️",
    "/student-availability": "🗂️", "/instructor-availability": "🗂️",
    "/instructor-subjects": "🔗", "/camps": "🏕️", "/camp-enrollments": "📝",
    "/camp-availability-student": "📅", "/camp-availability-instructor": "📅",
    "/regular-enrollments": "📖", "/student-detail": "🔍",
}
DESCRIPTIONS = {
    "/students": "生徒の氏名・学年などを登録します",
    "/instructors": "講師の氏名を登録します",
    "/subjects": "指導する科目の一覧を登録します",
    "/terms": "前期・後期などの学期を登録します",
    "/student-availability": "生徒の通塾可能な曜日・限を登録します",
    "/instructor-availability": "講師の勤務可能な曜日・限を登録します",
    "/instructor-subjects": "講師が担当できる科目を登録します",
    "/camps": "春期・夏期などの講習会を登録します",
    "/camp-enrollments": "講習会の受講契約(科目・コマ数)を登録します",
    "/camp-availability-student": "講習会期間中、生徒が対応可能な日付・限を登録します",
    "/camp-availability-instructor": "講習会期間中、講師が対応可能な日付・限を登録します",
    "/regular-enrollments": "通常授業の曜日・限固定の契約を登録します",
    "/student-detail": "生徒が受けている科目を一覧で確認します",
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
