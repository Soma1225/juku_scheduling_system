# -*- coding: utf-8 -*-
"""
page_home.py: ホーム画面(ダッシュボード)

モックアップで合意した仕様:
- サイドバーのみがナビゲーション(カード一覧は廃止)
- 「時間割」(通常授業+講習会セッションを統合、日付を前後にめくれる)
- 「教科フォロー」(限ごとに講師ブロックを5行固定で表示)
- 「通知欄」(振替/エラー/お知らせの3タブ。振替・エラーは現時点では中身が無い将来機能)
"""

import datetime
from db import get_conn, format_grade_label
from page_instructor_academic_year import get_pending_promotions, get_at_max_unconfirmed

WEEKDAYS_JP = ["月", "火", "水", "木", "金", "土", "日"]
PERIOD_NUMBERS = [1, 2, 3, 4, 5]


# ---------------------------------------------------------
# 日付・曜日のユーティリティ
# ---------------------------------------------------------

def _parse_date(date_str: str | None) -> datetime.date:
    if date_str:
        try:
            return datetime.date.fromisoformat(date_str)
        except ValueError:
            pass
    return datetime.date.today()


def _weekday_jp(d: datetime.date) -> str:
    return WEEKDAYS_JP[d.weekday()]


# ---------------------------------------------------------
# 時間割データの取得(通常授業+講習会セッションを統合)
# ---------------------------------------------------------

def get_schedule_for_date(conn, target_date: datetime.date) -> list[dict]:
    """
    指定日の全セッションを、通常授業(曜日ベース)+講習会(日付ベース)の両方から集めて返す。
    各要素: {period, instructor_id, instructor_name, student_id, student_name,
             base_grade, subject_id, subject_name, subject_group, track}
    """
    date_str = target_date.isoformat()
    weekday = _weekday_jp(target_date)
    records = []

    # --- 通常授業(曜日ベース、その日時点で有効な契約のみ、教科フォローは専用テーブルで扱うため除外) ---
    regular_rows = conn.execute(
        """SELECT e.period_number, e.instructor_id, i.last_name || i.first_name,
                  e.student_id, s.last_name || s.first_name, s.base_grade, s.enrollment_year, s.track,
                  sub.subject_id, sub.subject_name, sub.subject_group
           FROM REGULAR_COURSE_ENROLLMENTS e
           JOIN INSTRUCTORS i ON i.instructor_id = e.instructor_id
           JOIN STUDENTS s ON s.student_id = e.student_id
           JOIN SUBJECTS sub ON sub.subject_id = e.subject_id
           WHERE e.day_of_week = ? AND e.effective_start_date <= ?
             AND (e.effective_end_date IS NULL OR e.effective_end_date > ?)
             AND sub.subject_group != '教科フォロー'""",
        (weekday, date_str, date_str),
    ).fetchall()
    for row in regular_rows:
        (period, instr_id, instr_name, student_id, student_name, base_grade,
         enrollment_year, track, subject_id, subject_name, subject_group) = row
        records.append({
            "period": period, "instructor_id": instr_id, "instructor_name": instr_name,
            "student_id": student_id, "student_name": student_name,
            "base_grade": base_grade, "track": track,
            "subject_id": subject_id, "subject_name": subject_name, "subject_group": subject_group,
        })

    # --- 講習会セッション(日付ベース、SESSIONS/ASSIGNMENTSに実際に組まれているもののみ) ---
    camp_rows = conn.execute(
        """SELECT ts.period_number, se.instructor_id, i.last_name || i.first_name,
                  a.student_id, s.last_name || s.first_name, s.base_grade, s.track,
                  sub.subject_id, sub.subject_name, sub.subject_group
           FROM SESSIONS se
           JOIN TIME_SLOTS ts ON ts.slot_id = se.slot_id
           JOIN ASSIGNMENTS a ON a.session_id = se.session_id
           JOIN INSTRUCTORS i ON i.instructor_id = se.instructor_id
           JOIN STUDENTS s ON s.student_id = a.student_id
           JOIN SUBJECTS sub ON sub.subject_id = a.subject_id
           WHERE ts.session_date = ?""",
        (date_str,),
    ).fetchall()
    for row in camp_rows:
        (period, instr_id, instr_name, student_id, student_name, base_grade,
         track, subject_id, subject_name, subject_group) = row
        records.append({
            "period": period, "instructor_id": instr_id, "instructor_name": instr_name,
            "student_id": student_id, "student_name": student_name,
            "base_grade": base_grade, "track": track,
            "subject_id": subject_id, "subject_name": subject_name, "subject_group": subject_group,
        })

    return records


def get_follow_schedule_for_date(conn, target_date: datetime.date) -> list[dict]:
    """
    指定日の教科フォローを、専用テーブル(FOLLOW_COURSE_ENROLLMENTS)から取得する。
    戻り値の形は get_schedule_for_date() と揃えている。
    """
    date_str = target_date.isoformat()
    weekday = _weekday_jp(target_date)
    records = []

    rows = conn.execute(
        """SELECT f.period_number, f.instructor_id, i.last_name || i.first_name,
                  f.student_id, s.last_name || s.first_name, s.base_grade, s.track,
                  sub.subject_id, sub.subject_name, sub.subject_group
           FROM FOLLOW_COURSE_ENROLLMENTS f
           JOIN INSTRUCTORS i ON i.instructor_id = f.instructor_id
           JOIN STUDENTS s ON s.student_id = f.student_id
           JOIN SUBJECTS sub ON sub.subject_id = f.subject_id
           WHERE f.day_of_week = ? AND f.effective_start_date <= ?
             AND (f.effective_end_date IS NULL OR f.effective_end_date > ?)""",
        (weekday, date_str, date_str),
    ).fetchall()
    for row in rows:
        (period, instr_id, instr_name, student_id, student_name, base_grade,
         track, subject_id, subject_name, subject_group) = row
        records.append({
            "period": period, "instructor_id": instr_id, "instructor_name": instr_name,
            "student_id": student_id, "student_name": student_name,
            "base_grade": base_grade, "track": track,
            "subject_id": subject_id, "subject_name": subject_name, "subject_group": subject_group,
        })
    return records


def get_attendance_status(conn, target_date: datetime.date, student_id: int, subject_id: int,
                           instructor_id: int, period_number: int) -> str:
    """出欠の現在状態を返す。記録が無ければ'未入力'。"""
    row = conn.execute(
        """SELECT status FROM ATTENDANCE_RECORDS
           WHERE session_date = ? AND student_id = ? AND subject_id = ?
             AND instructor_id = ? AND period_number = ?""",
        (target_date.isoformat(), student_id, subject_id, instructor_id, period_number),
    ).fetchone()
    return row[0] if row else "未入力"


_ATTENDANCE_CYCLE = ["未入力", "出席", "欠席"]


def toggle_attendance(conn, target_date: datetime.date, student_id: int, subject_id: int,
                       instructor_id: int, period_number: int, current_status: str) -> str:
    """出欠の状態を、未入力→出席→欠席→未入力、の順に1つ進める。次の状態を返す。"""
    if current_status not in _ATTENDANCE_CYCLE:
        current_status = "未入力"
    next_status = _ATTENDANCE_CYCLE[(_ATTENDANCE_CYCLE.index(current_status) + 1) % len(_ATTENDANCE_CYCLE)]

    date_str = target_date.isoformat()
    if next_status == "未入力":
        conn.execute(
            """DELETE FROM ATTENDANCE_RECORDS
               WHERE session_date = ? AND student_id = ? AND subject_id = ?
                 AND instructor_id = ? AND period_number = ?""",
            (date_str, student_id, subject_id, instructor_id, period_number),
        )
    else:
        conn.execute(
            """INSERT INTO ATTENDANCE_RECORDS
               (session_date, student_id, subject_id, instructor_id, period_number, status)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (session_date, student_id, subject_id, instructor_id, period_number)
               DO UPDATE SET status = excluded.status""",
            (date_str, student_id, subject_id, instructor_id, period_number, next_status),
        )
    conn.commit()
    return next_status


# ---------------------------------------------------------
# 描画: 時間割グリッド(講師ブロックをrowspanでまとめる)
# ---------------------------------------------------------

SUBJECT_BADGE_COLORS = {
    "国語": "#FBEEDD;color:#A85D22", "算数": "#FBEEDD;color:#A85D22",
    "数学": "#EAEFF7;color:#3A5A9B", "英語": "#E3F0EE;color:#1F7A66",
    "理科": "#EAF6E8;color:#3D7A2E", "社会": "#F3EAF6;color:#7A3D9B",
    "戦略指導": "#FBE5EC;color:#B0356B", "教科フォロー": "#FDF3D8;color:#9B7D1E",
}
TIMETABLE_ROWS_PER_PERIOD = 15


def _subject_badge(subject_group: str, subject_name: str) -> str:
    color = SUBJECT_BADGE_COLORS.get(subject_group, "#eee;color:#555")
    label = subject_name if subject_name != subject_group else subject_group
    # 「戦略指導(面談)」のような長い名前は短縮する
    label = label.replace("戦略指導(面談)", "戦略").replace("戦略指導", "戦略")
    return f'<span class="subject-badge" style="background:{color};">{label}</span>'


def _attendance_cell(conn, target_date, rec) -> str:
    status = get_attendance_status(
        conn, target_date, rec["student_id"], rec["subject_id"], rec["instructor_id"], rec["period"]
    )
    symbol = {"未入力": "-", "出席": "○", "欠席": "×"}[status]
    css_class = {"未入力": "", "出席": "att-present", "欠席": "att-absent"}[status]
    date_str = target_date.isoformat()
    return f"""
    <form method="POST" action="/" style="display:inline;">
      <input type="hidden" name="action" value="toggle_attendance">
      <input type="hidden" name="date" value="{date_str}">
      <input type="hidden" name="student_id" value="{rec['student_id']}">
      <input type="hidden" name="subject_id" value="{rec['subject_id']}">
      <input type="hidden" name="instructor_id" value="{rec['instructor_id']}">
      <input type="hidden" name="period_number" value="{rec['period']}">
      <input type="hidden" name="current_status" value="{status}">
      <button type="submit" class="attendance-indicator {css_class}">{symbol}</button>
    </form>
    """


def _build_timetable_html(conn, target_date, records: list[dict]) -> str:
    """講師×限ごとにグループ化し、rowspanで講師ブロックをまとめた時間割表を組み立てる。"""
    by_period_instructor: dict[tuple[int, int], list[dict]] = {}
    for rec in records:
        key = (rec["period"], rec["instructor_id"])
        by_period_instructor.setdefault(key, []).append(rec)

    # 各限に登場する「講師ブロック」の並び(講師名のかな順)
    period_blocks: dict[int, list[tuple[int, str, list[dict]]]] = {p: [] for p in PERIOD_NUMBERS}
    for (period, instructor_id), recs in by_period_instructor.items():
        instructor_name = recs[0]["instructor_name"]
        period_blocks[period].append((instructor_id, instructor_name, recs))
    for p in PERIOD_NUMBERS:
        period_blocks[p].sort(key=lambda t: t[1])

    # ヘッダー
    period_times = {1: "14:20〜15:40", 2: "15:50〜17:10", 3: "17:20〜18:40", 4: "19:00〜20:20", 5: "20:30〜21:50"}
    period_marks = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤"}
    header1 = "".join(f'<th class="period-head" colspan="5">{period_marks[p]}　{period_times[p]}</th>' for p in PERIOD_NUMBERS)
    header2 = "".join(
        '<th class="sub-head">講師</th><th class="sub-head">学年</th><th class="sub-head">生徒</th>'
        '<th class="sub-head">科目</th><th class="sub-head">出欠</th>'
        for _ in PERIOD_NUMBERS
    )

    # 各限ごとに「行の並び(講師名 or 空欄, 学年, 生徒, 科目, 出欠)」のリストを作る
    period_lines: dict[int, list[tuple[str | None, dict | None]]] = {p: [] for p in PERIOD_NUMBERS}
    for p in PERIOD_NUMBERS:
        for instructor_id, instructor_name, recs in period_blocks[p]:
            for i, rec in enumerate(recs):
                period_lines[p].append((instructor_name if i == 0 else None, rec, len(recs) if i == 0 else 0))

    # Excelの「時間割一覧」と同じく各限を最低15行表示する。
    # 15件を超える実データは切り捨てず、その件数まで行を増やす。
    max_lines = max(
        TIMETABLE_ROWS_PER_PERIOD,
        max((len(v) for v in period_lines.values()), default=0),
    )
    rows_html = ""
    for line_idx in range(max_lines):
        cells = ""
        for p in PERIOD_NUMBERS:
            lines = period_lines[p]
            if line_idx >= len(lines):
                cells += '<td class="empty">-</td>' * 4
                continue
            instructor_name, rec, rowspan = lines[line_idx]
            if instructor_name is not None:
                cells += f'<td rowspan="{rowspan}" class="col-instructor">{instructor_name}</td>'
            cells += f'<td class="col-grade">{format_grade_label(rec["base_grade"])}</td>'
            cells += f'<td class="col-student">{rec["student_name"]}</td>'
            cells += f'<td class="col-subject">{_subject_badge(rec["subject_group"], rec["subject_name"])}</td>'
            cells += f'<td class="col-attendance">{_attendance_cell(conn, target_date, rec)}</td>'
        rows_html += f"<tr>{cells}</tr>"

    return f"""
    <div class="table-scroll">
    <table class="timetable">
      <thead><tr>{header1}</tr><tr>{header2}</tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    </div>
    """


# ---------------------------------------------------------
# 描画: 教科フォロー(限ごとに講師ブロックを5行固定で表示)
# ---------------------------------------------------------

def _parse_track_from_subject_name(subject_name: str) -> str:
    if "文系" in subject_name:
        return "文"
    if "理系" in subject_name:
        return "理"
    return "-"


def _build_follow_html(conn, target_date, follow_records: list[dict]) -> str:
    if not follow_records:
        return '<div class="hint">この日の教科フォローはありません</div>'

    by_period_instructor: dict[tuple[int, int], list[dict]] = {}
    for rec in follow_records:
        by_period_instructor.setdefault((rec["period"], rec["instructor_id"]), []).append(rec)

    by_period: dict[int, list[tuple[int, str, list[dict]]]] = {}
    for (period, instructor_id), recs in by_period_instructor.items():
        by_period.setdefault(period, []).append((instructor_id, recs[0]["instructor_name"], recs))

    period_marks = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤"}
    period_times = {1: "14:20〜15:40", 2: "15:50〜17:10", 3: "17:20〜18:40", 4: "19:00〜20:20", 5: "20:30〜21:50"}

    blocks_html = ""
    for period in sorted(by_period.keys()):
        rows_html = ""
        for instructor_id, instructor_name, recs in by_period[period]:
            for i in range(5):  # 教科フォローは講師1人あたり最大5人までなので5行固定
                if i < len(recs):
                    rec = recs[i]
                    track = _parse_track_from_subject_name(rec["subject_name"]) if rec["subject_name"] != "教科フォロー" else (rec["track"] or "-")
                    instr_cell = f'<td rowspan="5" class="f-instructor">{instructor_name}</td>' if i == 0 else ""
                    rows_html += (
                        f"<tr>{instr_cell}"
                        f'<td class="f-grade">{format_grade_label(rec["base_grade"])}</td>'
                        f'<td class="f-student">{rec["student_name"]}</td>'
                        f'<td>{track}</td>'
                        f'<td>{_attendance_cell(conn, target_date, rec)}</td></tr>'
                    )
                else:
                    instr_cell = f'<td rowspan="5" class="f-instructor">{instructor_name}</td>' if i == 0 else ""
                    rows_html += f'{"<tr>" + instr_cell if i == 0 else "<tr>"}<td class="empty">-</td><td class="empty">-</td><td class="empty">-</td><td class="empty">-</td></tr>'
        blocks_html += f"""
        <table class="follow-table">
          <thead><tr><th colspan="5">{period_marks[period]}　{period_times[period]}</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
        """

    return f'<div class="follow-blocks">{blocks_html}</div>'


# ---------------------------------------------------------
# 描画: 通知欄(振替/エラー/お知らせ)
# ---------------------------------------------------------

def _build_notice_panel(conn) -> str:
    missing_students = _get_students_missing_regular_enrollment(conn)
    pending_promotions = get_pending_promotions(conn)
    at_max = get_at_max_unconfirmed(conn)

    info_items = ""
    for sid, name, grade in missing_students:
        info_items += (
            f'<li><a href="/regular-enrollments?student_id={sid}">{name}（{format_grade_label(grade)}）'
            f'　通常授業が未登録です</a></li>'
        )
    for p in pending_promotions:
        info_items += (
            f'<li><span class="priority-badge">!</span>'
            f'<a href="/instructor-academic-year">{p["name"]}　{p["current_year"]} → {p["proposed_year"]} の進級確認待ち</a></li>'
        )
    for a in at_max:
        info_items += (
            f'<li><a href="/instructor-academic-year">{a["name"]}　学年が上限（{a["current_year"]}）です。'
            f'卒業・進路を個別に確認してください</a></li>'
        )

    n_info = len(missing_students) + len(pending_promotions) + len(at_max)
    info_html = f"<ul class='notice-list'>{info_items}</ul>" if info_items else '<div class="hint">お知らせはありません</div>'
    transfer_html = '<div class="hint">振替の管理機能は今後追加予定です</div>'
    error_html = '<div class="hint">エラー検知機能は今後追加予定です</div>'

    return f"""
    <div class="notice-tabs">
      <button type="button" class="notice-tab" onclick="showNoticeTab('transfer')" id="notice-tab-btn-transfer">振替</button>
      <button type="button" class="notice-tab" onclick="showNoticeTab('error')" id="notice-tab-btn-error">エラー</button>
      <button type="button" class="notice-tab notice-tab-active" onclick="showNoticeTab('info')" id="notice-tab-btn-info">お知らせ ({n_info})</button>
    </div>
    <div class="notice-body">
      <div id="notice-panel-transfer" style="display:none;">{transfer_html}</div>
      <div id="notice-panel-error" style="display:none;">{error_html}</div>
      <div id="notice-panel-info">{info_html}</div>
    </div>
    <script>
      function showNoticeTab(key) {{
        ['transfer', 'error', 'info'].forEach(function(k) {{
          document.getElementById('notice-panel-' + k).style.display = (k === key) ? 'block' : 'none';
          document.getElementById('notice-tab-btn-' + k).classList.toggle('notice-tab-active', k === key);
        }});
      }}
    </script>
    """


def _get_students_missing_regular_enrollment(conn) -> list[tuple[int, str, int]]:
    """
    在籍中(enrollment_status='在籍')なのに、通常授業(REGULAR_COURSE_ENROLLMENTS)も
    教科フォロー(FOLLOW_COURSE_ENROLLMENTS)も1件も現在有効でない生徒を検出する。
    (戦略指導コースの生徒は、面談+教科フォローだけで完結しており、通常科目を
     持たないのが正常なため、教科フォローの登録があれば「未登録」扱いしない)
    """
    return conn.execute(
        """SELECT s.student_id, s.last_name || s.first_name, s.base_grade
           FROM STUDENTS s
           WHERE s.enrollment_status = '在籍'
             AND NOT EXISTS (
               SELECT 1 FROM REGULAR_COURSE_ENROLLMENTS e
               WHERE e.student_id = s.student_id AND e.effective_end_date IS NULL
             )
             AND NOT EXISTS (
               SELECT 1 FROM FOLLOW_COURSE_ENROLLMENTS f
               WHERE f.student_id = s.student_id AND f.effective_end_date IS NULL
             )
           ORDER BY s.last_name_kana, s.first_name_kana"""
    ).fetchall()


# ---------------------------------------------------------
# render / handle_post
# ---------------------------------------------------------

def render(qs: dict, message_html: str = "") -> str:
    target_date = _parse_date(qs.get("date", [None])[0])
    prev_date = (target_date - datetime.timedelta(days=1)).isoformat()
    next_date = (target_date + datetime.timedelta(days=1)).isoformat()

    conn = get_conn()
    records = get_schedule_for_date(conn, target_date)
    follow_records = get_follow_schedule_for_date(conn, target_date)
    timetable_html = _build_timetable_html(conn, target_date, records)
    follow_html = _build_follow_html(conn, target_date, follow_records)
    notice_html = _build_notice_panel(conn)
    conn.close()

    return f"""
    <h1>ホーム</h1>

    <h2 class="home-section-title">時間割</h2>
    <div class="date-nav">
      <a href="/?date={prev_date}"><button type="button">◀</button></a>
      <span class="date-label">{target_date.year}年{target_date.month}月{target_date.day}日（{_weekday_jp(target_date)}）</span>
      <a href="/?date={next_date}"><button type="button">▶</button></a>
    </div>
    <div class="hint">出欠マスをクリックすると、未入力/出席/欠席、を切り替えられます</div>
    {timetable_html}
    <div class="cell-legend">
      <span>出欠: <span class="attendance-indicator" style="position:static;">-</span>未入力　
      <span class="attendance-indicator att-present" style="position:static;">○</span>出席　
      <span class="attendance-indicator att-absent" style="position:static;">×</span>欠席</span>
    </div>

    <h2 class="home-section-title" style="margin-top:26px;">教科フォロー</h2>
    {follow_html}

    <h2 class="home-section-title" style="margin-top:26px;">通知欄</h2>
    {notice_html}

    {message_html}
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    action = get("action")
    if action != "toggle_attendance":
        raise ValueError(f"不明な action です: {action}")

    target_date = _parse_date(get("date"))
    toggle_attendance(
        conn, target_date,
        int(get("student_id")), int(get("subject_id")), int(get("instructor_id")), int(get("period_number")),
        get("current_status"),
    )
    return "", {"date": [target_date.isoformat()]}

