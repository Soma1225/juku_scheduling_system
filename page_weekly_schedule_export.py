"""通常授業の週間時間割Excelを、生徒用・講師用・教室全体用に出力する入口。"""

import html
import datetime

from db import get_conn


def render(qs: dict, message_html: str = "") -> str:
    selected_student = qs.get("student_id", [""])[0]
    selected_instructor = qs.get("instructor_id", [""])[0]
    default_start = datetime.date.today()
    start_date = html.escape(qs.get("start_date", [default_start.isoformat()])[0])
    end_date = html.escape(
        qs.get("end_date", [(default_start + datetime.timedelta(days=6)).isoformat()])[0]
    )
    conn = get_conn()
    students = conn.execute(
        """SELECT student_id,last_name||' '||first_name FROM STUDENTS
           WHERE enrollment_status='在籍' ORDER BY last_name_kana,first_name_kana"""
    ).fetchall()
    instructors = conn.execute(
        """SELECT instructor_id,last_name||' '||first_name FROM INSTRUCTORS
           WHERE status='在籍' ORDER BY last_name_kana,first_name_kana"""
    ).fetchall()
    conn.close()

    student_options = "".join(
        f'<option value="{sid}"{" selected" if str(sid) == selected_student else ""}>{html.escape(name)}</option>'
        for sid, name in students
    )
    instructor_options = "".join(
        f'<option value="{iid}"{" selected" if str(iid) == selected_instructor else ""}>{html.escape(name)}</option>'
        for iid, name in instructors
    )
    return f"""
    <h1>通常授業 週間Excel出力</h1>
    <div class="hint">通常授業と教科フォローを出力します。生徒用は指定期間の暦日、講師用・教室全体用は曜日別です。</div>
    {message_html}
    <div class="hub-grid">
      <section class="hub-card">
        <h2>生徒用（日付カレンダー）</h2>
        <label>生徒</label>
        <select id="weekly-student"><option value="">選択してください</option>{student_options}</select>
        <label>開始日</label>
        <input id="student-calendar-start" type="date" value="{start_date}" required>
        <label>終了日（開始日を含め最大32日）</label>
        <input id="student-calendar-end" type="date" value="{end_date}" required>
        <button type="button" onclick="downloadStudentCalendar()">生徒用Excelをダウンロード</button>
      </section>
      <section class="hub-card">
        <h2>講師用</h2>
        <label>講師</label>
        <select id="weekly-instructor"><option value="">選択してください</option>{instructor_options}</select>
        <button type="button" onclick="downloadWeekly('instructor','weekly-instructor')">講師用Excelをダウンロード</button>
      </section>
      <section class="hub-card">
        <h2>教室全体用</h2>
        <p class="hint">在籍中の全生徒・全講師を、月～土×5限・各15行で出力します。</p>
        <button type="button" onclick="location.href='/weekly-schedule-export-download?kind=classroom'">教室全体用Excelをダウンロード</button>
      </section>
    </div>
    <script>
    function downloadWeekly(kind, selectId) {{
      const value = document.getElementById(selectId).value;
      if (!value) {{ alert('対象者を選択してください'); return; }}
      const key = kind === 'student' ? 'student_id' : 'instructor_id';
      location.href = '/weekly-schedule-export-download?kind=' + kind + '&' + key + '=' + encodeURIComponent(value);
    }}
    function downloadStudentCalendar() {{
      const studentId = document.getElementById('weekly-student').value;
      const startDate = document.getElementById('student-calendar-start').value;
      const endDate = document.getElementById('student-calendar-end').value;
      if (!studentId) {{ alert('生徒を選択してください'); return; }}
      if (!startDate || !endDate) {{ alert('開始日と終了日を指定してください'); return; }}
      location.href = '/weekly-schedule-export-download?kind=student&student_id=' +
        encodeURIComponent(studentId) + '&start_date=' + encodeURIComponent(startDate) +
        '&end_date=' + encodeURIComponent(endDate);
    }}
    </script>
    """
