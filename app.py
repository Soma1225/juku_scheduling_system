# -*- coding: utf-8 -*-
"""
app.py

塾スケジューリングシステム データ入力ポータル(モジュール分割版)。

このファイルは「どのURLに来たら、どのページモジュールを呼ぶか」という
ルーティングだけを担当する。各ページの中身(HTML生成・DB操作)は、
page_*.py の各モジュールに分かれている。

起動:
    python app.py
    -> http://localhost:8000 にホーム画面が表示される

必要なファイル(全て同じフォルダに置くこと):
    app.py, db.py, layout.py, schema.sql,
    page_home.py, page_students.py, page_instructors.py, page_subjects.py,
    page_terms.py, page_periods.py, page_availability.py, page_instructor_subjects.py
"""

import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from db import ensure_db_exists, get_conn
from layout import render_page
import page_home
import page_students
import page_instructors
import page_subjects
import page_terms
import page_availability
import page_instructor_subjects
import page_camps
import page_camp_enrollments
import page_camp_availability
import page_regular_enrollments
import page_student_detail

PORT = 8000

# パス -> (render関数, handle_post関数) の対応表。
# 生徒/講師の対応可能時間だけ、同じモジュール内の別関数(_student/_instructor)を使う。
ROUTES = {
    "/": (page_home.render, None),
    "/students": (page_students.render, page_students.handle_post),
    "/instructors": (page_instructors.render, page_instructors.handle_post),
    "/subjects": (page_subjects.render, page_subjects.handle_post),
    "/terms": (page_terms.render, page_terms.handle_post),
    "/student-availability": (page_availability.render_student, page_availability.handle_post_student),
    "/instructor-availability": (page_availability.render_instructor, page_availability.handle_post_instructor),
    "/instructor-subjects": (page_instructor_subjects.render, page_instructor_subjects.handle_post),
    "/camps": (page_camps.render, page_camps.handle_post),
    "/camp-enrollments": (page_camp_enrollments.render, page_camp_enrollments.handle_post),
    "/camp-availability-student": (page_camp_availability.render_student, page_camp_availability.handle_post_student),
    "/camp-availability-instructor": (page_camp_availability.render_instructor, page_camp_availability.handle_post_instructor),
    "/regular-enrollments": (page_regular_enrollments.render, page_regular_enrollments.handle_post),
    "/student-detail": (page_student_detail.render, None),
}


class PortalHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path not in ROUTES:
            self.send_response(404)
            self.end_headers()
            return
        qs = parse_qs(parsed.query)
        render_fn, _ = ROUTES[path]
        content = render_fn(qs)
        self._respond(render_page(path, content))

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path not in ROUTES:
            self.send_response(404)
            self.end_headers()
            return

        render_fn, post_fn = ROUTES[path]
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        fields = parse_qs(body)

        conn = get_conn()
        try:
            message_html, qs = post_fn(fields, conn)
        except Exception as e:
            message_html = f'<div class="msg error">処理に失敗しました: {e}</div>'
            # エラー時は、送信されたfieldsをそのままqsとして使い、入力状態を維持する
            qs = {k: v for k, v in fields.items()}
        finally:
            conn.close()

        content = render_fn(qs, message_html)
        self._respond(render_page(path, content))

    def _respond(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)


def run_server(port: int = PORT, open_browser: bool = True):
    ensure_db_exists()
    server = HTTPServer(("localhost", port), PortalHandler)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    print(f"データ入力ポータルを起動しました: http://localhost:{port}")
    print("終了するには Ctrl+C を押してください")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    run_server()
