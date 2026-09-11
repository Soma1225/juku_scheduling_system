"""科目・最低レベルから、在籍講師と現在の空き枠を検索する画面。"""

from __future__ import annotations

import datetime
import html

from db import get_conn
from instructor_search import search_instructors


def render(qs: dict, message_html: str = "") -> str:
    subject_id = qs.get("subject_id", [""])[0]
    minimum_level = qs.get("minimum_level", ["1"])[0]
    conn = get_conn()
    subjects = conn.execute(
        """SELECT subject_id,grade_band||'／'||subject_name
           FROM SUBJECTS
           ORDER BY CASE grade_band WHEN '小学生低学年' THEN 1 WHEN '小学生高学年' THEN 2
                    WHEN '中学生' THEN 3 ELSE 4 END,
                    course_category,subject_group,subject_name"""
    ).fetchall()
    subject_options = "".join(
        f'<option value="{item_id}"{" selected" if str(item_id) == subject_id else ""}>'
        f'{html.escape(label)}</option>'
        for item_id, label in subjects
    )

    results_html = '<div class="hint">科目と最低レベルを選択してください。</div>'
    if subject_id:
        try:
            report = search_instructors(
                conn,
                subject_id=int(subject_id),
                minimum_level=int(minimum_level),
                as_of_date=datetime.date.today().isoformat(),
            )
            rows = "".join(
                f"""<tr>
                  <td>{html.escape(item['instructor_name'])}</td>
                  <td>レベル{item['proficiency_level']}</td>
                  <td>{html.escape('、'.join(f'{day}{period}限' for day, period in item['free_slots']) or '空きなし')}</td>
                </tr>"""
                for item in report["instructors"]
            )
            if rows:
                results_html = f"""
                <div class="hint">基準日：{report['as_of_date']}／対象学期：{html.escape(report['term_name'])}</div>
                <table><tr><th>講師</th><th>レベル</th><th>空いている曜日・限</th></tr>{rows}</table>
                """
            else:
                results_html = (
                    f'<div class="hint">対象学期：{html.escape(report["term_name"])}。'
                    '条件に合う在籍講師はいません。</div>'
                )
        except (TypeError, ValueError) as exc:
            results_html = f'<div class="msg error">{html.escape(str(exc))}</div>'
    conn.close()

    return f"""
    <h1>科目・レベルによる講師検索</h1>
    <div class="hint">担当可能な講師と、今日時点で1:2上限・対応不可に該当しない枠を表示します。</div>
    {message_html}
    <form method="GET" action="/instructor-search">
      <label>科目</label>
      <select name="subject_id" required onchange="this.form.submit()">
        <option value="">選択してください</option>{subject_options}
      </select>
      <label>最低レベル</label>
      <select name="minimum_level" onchange="this.form.submit()">
        <option value="1"{" selected" if minimum_level == "1" else ""}>1以上（誰でも）</option>
        <option value="2"{" selected" if minimum_level == "2" else ""}>2のみ</option>
      </select>
    </form>
    {results_html}
    """
