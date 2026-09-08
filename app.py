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
    page_availability.py, page_instructor_subjects.py, page_camps.py,
    page_camp_enrollments.py, page_camp_availability.py, page_regular_enrollments.py,
    page_student_detail.py
"""

import threading
import webbrowser
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from db import ensure_db_exists, get_conn
from layout import render_page
import page_home
import page_students
import page_instructors
import page_subjects
import page_availability
import page_instructor_subjects
import page_camps
import page_camps_hub
import page_camp_enrollments
import page_camp_availability
import page_regular_enrollments
import page_follow_enrollments
import page_student_detail
import page_camp_sync_groups
import page_schedule_view
import page_run_scheduler
import page_instructor_academic_year
import page_excel_import
import page_image_import
import page_image_import_review
import page_image_import_corrections
import page_student_instructor_preferences
import page_instructor_detail
from image_import_service import DEFAULT_STORAGE_ROOT

PORT = 8000

# パス -> (render関数, handle_post関数) の対応表。
# 生徒/講師の対応可能時間だけ、同じモジュール内の別関数(_student/_instructor)を使う。
ROUTES = {
    "/": (page_home.render, page_home.handle_post),
    "/students": (page_students.render, page_students.handle_post),
    "/instructors": (page_instructors.render, page_instructors.handle_post),
    "/subjects": (page_subjects.render, page_subjects.handle_post),
    "/student-availability": (page_availability.render_student, page_availability.handle_post_student),
    "/instructor-availability": (page_availability.render_instructor, page_availability.handle_post_instructor),
    "/instructor-subjects": (page_instructor_subjects.render, page_instructor_subjects.handle_post),
    "/camps": (page_camps.render, page_camps.handle_post),
    "/camps-hub": (page_camps_hub.render, None),
    "/camp-enrollments": (page_camp_enrollments.render, page_camp_enrollments.handle_post),
    "/camp-availability-student": (page_camp_availability.render_student, page_camp_availability.handle_post_student),
    "/camp-availability-instructor": (page_camp_availability.render_instructor, page_camp_availability.handle_post_instructor),
    "/regular-enrollments": (page_regular_enrollments.render, page_regular_enrollments.handle_post),
    "/follow-enrollments": (page_follow_enrollments.render, page_follow_enrollments.handle_post),
    "/student-detail": (page_student_detail.render, None),
    "/camp-sync-groups": (page_camp_sync_groups.render, page_camp_sync_groups.handle_post),
    "/schedule-by-day": (page_schedule_view.render_by_day, None),
    "/schedule-instructor": (page_schedule_view.render_instructor_view, None),
    "/schedule-student": (page_schedule_view.render_student_view, None),
    "/run-scheduler": (page_run_scheduler.render, page_run_scheduler.handle_post),
    "/instructor-academic-year": (page_instructor_academic_year.render, page_instructor_academic_year.handle_post),
    "/excel-import": (page_excel_import.render, page_excel_import.handle_post),
    "/image-import": (page_image_import.render, page_image_import.handle_post),
    "/image-import-review": (page_image_import_review.render, page_image_import_review.handle_post),
    "/image-import-corrections": (page_image_import_corrections.render, page_image_import_corrections.handle_post),
    "/student-instructor-preferences": (
        page_student_instructor_preferences.render,
        page_student_instructor_preferences.handle_post,
    ),
    "/instructor-detail": (page_instructor_detail.render, None),
}


def parse_multipart(body: bytes, content_type: str) -> dict:
    """
    multipart/form-data のリクエストボディを解析する。
    戻り値は既存の parse_qs() 互換の {name: [value, ...]} 形式に、
    アップロードされたファイルだけ特別なキー "_files" (dict) として追加したもの。
    "_files" の中身: {field_name: {"filename": ..., "content": bytes}}
    """
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary="):].strip('"')
            break
    if boundary is None:
        return {}

    boundary_bytes = ("--" + boundary).encode("utf-8")
    segments = body.split(boundary_bytes)

    fields: dict = {}
    files: dict = {}

    for segment in segments:
        # multipartの構文上付く改行だけを除去する。strip/rstripを使うと、
        # PDF等のバイナリ本体が改行バイトで終わる場合に内容を壊してしまう。
        if segment.startswith(b"\r\n"):
            segment = segment[2:]
        if segment.endswith(b"\r\n"):
            segment = segment[:-2]
        if not segment or segment == b"--":
            continue
        if b"\r\n\r\n" not in segment:
            continue
        header_bytes, content = segment.split(b"\r\n\r\n", 1)
        headers = header_bytes.decode("utf-8", errors="replace")

        name_match = re.search(r'name="([^"]*)"', headers)
        if not name_match:
            continue
        field_name = name_match.group(1)

        filename_match = re.search(r'filename="([^"]*)"', headers)
        if filename_match:
            filename = filename_match.group(1)
            if filename:  # ファイルが選択されていない場合はfilenameが空文字列になる
                files[field_name] = {"filename": filename, "content": content}
        else:
            fields.setdefault(field_name, []).append(content.decode("utf-8", errors="replace"))

    fields["_files"] = files
    return fields


class PortalHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/image-import-preview":
            self._serve_image_import_preview(parse_qs(parsed.query))
            return
        if path == "/image-import-review-crop":
            self._serve_image_import_review_crop(parse_qs(parsed.query))
            return
        if path == "/image-import-attachment":
            self._serve_image_import_attachment(parse_qs(parsed.query))
            return
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
        raw_body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")

        if content_type.startswith("multipart/form-data"):
            fields = parse_multipart(raw_body, content_type)
        else:
            fields = parse_qs(raw_body.decode("utf-8"))

        conn = get_conn()
        try:
            message_html, qs = post_fn(fields, conn)
        except Exception as e:
            message_html = f'<div class="msg error">処理に失敗しました: {e}</div>'
            # エラー時は、送信されたfieldsをそのままqsとして使い、入力状態を維持する(ファイル情報は除く)
            qs = {k: v for k, v in fields.items() if k != "_files"}
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

    def _serve_image_import_preview(self, qs: dict):
        """DB上のpage_idに紐づく画像だけを返し、任意ファイル参照を防ぐ。"""
        try:
            page_id = int(qs.get("page_id", [""])[0])
        except ValueError:
            self.send_error(400, "invalid page_id")
            return
        kind = qs.get("kind", ["corrected"])[0]
        if kind not in {"corrected", "original", "student_name", "grade"}:
            self.send_error(400, "invalid preview kind")
            return
        conn = get_conn()
        row = conn.execute(
            "SELECT page_image_path FROM IMAGE_IMPORT_PAGES WHERE page_id=? AND is_deleted=0",
            (page_id,),
        ).fetchone()
        conn.close()
        if not row:
            self.send_error(404)
            return
        corrected = Path(row[0]).resolve()
        paths = {
            "corrected": corrected,
            "original": corrected.with_name(f"{corrected.stem}_original.png"),
            "student_name": corrected.parent / f"{corrected.stem}_regions" / "student_name.png",
            "grade": corrected.parent / f"{corrected.stem}_regions" / "grade.png",
        }
        image_path = paths[kind].resolve()
        try:
            image_path.relative_to(DEFAULT_STORAGE_ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not image_path.is_file():
            self.send_error(404)
            return
        body = image_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_image_import_review_crop(self, qs: dict):
        try:
            review_item_id = int(qs.get("review_item_id", [""])[0])
        except ValueError:
            self.send_error(400, "invalid review_item_id")
            return
        conn = get_conn()
        row = conn.execute(
            """
            SELECT r.crop_image_path FROM IMAGE_IMPORT_REVIEW_ITEMS r
            JOIN IMAGE_IMPORT_PAGES p ON p.page_id=r.page_id
            JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
            WHERE r.review_item_id=? AND r.is_deleted=0 AND p.is_deleted=0 AND b.is_deleted=0
            """,
            (review_item_id,),
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            self.send_error(404)
            return
        image_path = Path(row[0]).resolve()
        try:
            image_path.relative_to(DEFAULT_STORAGE_ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not image_path.is_file():
            self.send_error(404)
            return
        body = image_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_image_import_attachment(self, qs: dict):
        try:
            attachment_id = int(qs.get("attachment_id", [""])[0])
        except ValueError:
            self.send_error(400, "invalid attachment_id")
            return
        conn = get_conn()
        row = conn.execute(
            """
            SELECT a.crop_image_path FROM IMAGE_IMPORT_ATTACHMENTS a
            JOIN IMAGE_IMPORT_PAGES p ON p.page_id=a.page_id
            JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
            WHERE a.attachment_id=? AND a.is_deleted=0 AND p.is_deleted=0 AND b.is_deleted=0
            """,
            (attachment_id,),
        ).fetchone()
        conn.close()
        if not row:
            self.send_error(404)
            return
        image_path = Path(row[0]).resolve()
        try:
            image_path.relative_to(DEFAULT_STORAGE_ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not image_path.is_file():
            self.send_error(404)
            return
        body = image_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
