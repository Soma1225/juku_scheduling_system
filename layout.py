# -*- coding: utf-8 -*-
"""
layout.py

全ページ共通のHTMLレイアウト(左ナビゲーション+カードのラッパー)。
各ページモジュールは「中身のHTML(content)」だけを作り、
最終的な組み立ては全てこのモジュールに任せる。
"""

MENU = [
    ("/", "ホーム"),
    ("/students", "生徒登録"),
    ("/instructors", "講師登録"),
    ("/subjects", "科目マスタ"),
    ("/terms", "学期マスタ"),
    ("/student-availability", "生徒 対応可能時間"),
    ("/instructor-availability", "講師 対応可能時間"),
    ("/instructor-subjects", "講師 担当科目"),
    ("/regular-enrollments", "通常授業 契約登録"),
    ("/student-detail", "生徒詳細"),
    ("/camps", "講習会マスタ"),
    ("/camp-enrollments", "講習会 受講契約"),
    ("/camp-availability-student", "生徒 講習会中の対応可能時間"),
    ("/camp-availability-instructor", "講師 講習会中の対応可能時間"),
]

ROUTE_TITLES = {path: label for path, label in MENU}

_LAYOUT = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>{title} - 塾スケジューリングシステム</title>
<style>
  * {{ box-sizing:border-box; }}
  body {{ font-family:"Yu Gothic","Hiragino Sans",sans-serif; background:#f4f4f2; margin:0; }}
  .layout {{ display:flex; min-height:100vh; }}
  nav {{ width:200px; background:#1F4E5F; color:#fff; padding:24px 0; flex-shrink:0; }}
  nav .brand {{ font-size:14px; font-weight:bold; padding:0 20px 20px; opacity:0.85; }}
  nav a {{ display:block; padding:10px 20px; color:#d8e6ea; text-decoration:none; font-size:13px; }}
  nav a:hover {{ background:#163a47; }}
  nav a.active {{ background:#0F6E56; color:#fff; font-weight:bold; }}
  main {{ flex:1; padding:40px; }}
  .card {{ max-width:600px; background:#fff; border-radius:10px; padding:32px 36px;
           box-shadow:0 2px 10px rgba(0,0,0,0.08); }}
  .card.wide {{ max-width:760px; }}
  h1 {{ font-size:20px; margin-bottom:8px; color:#1F4E5F; }}
  .hint {{ font-size:12px; color:#888; margin-bottom:20px; }}
  label {{ display:block; font-size:13px; color:#333; margin-top:14px; margin-bottom:4px; }}
  input, select {{ width:100%; padding:8px 10px; font-size:14px; border:1px solid #ccc; border-radius:6px; }}
  button {{ margin-top:20px; width:100%; padding:12px; font-size:15px; color:#fff;
            border:none; border-radius:6px; cursor:pointer; background:#1F4E5F; }}
  button:hover {{ opacity:0.9; }}
  .msg {{ margin-top:16px; padding:10px 14px; border-radius:6px; font-size:13px; }}
  .msg.success {{ background:#E1F5EE; color:#0F6E56; }}
  .msg.error {{ background:#FAECE7; color:#993C1D; }}
  .req {{ color:#993C1D; }}
  table {{ width:100%; border-collapse:collapse; margin-top:10px; }}
  th, td {{ text-align:left; padding:6px 4px; font-size:13px; border-bottom:1px solid #f0f0f0; }}
  th {{ color:#888; font-weight:normal; }}
  table.grid th, table.grid td {{ text-align:center; border:1px solid #e5e5e5; padding:6px; }}
  table.grid th:first-child, table.grid td:first-child {{ background:#f7f6f3; font-weight:bold; }}
  table.grid .period-label {{ white-space:nowrap; font-size:11px; }}
  table.grid th.closed-day, table.grid td.closed-day {{ background:#2c2c2a; color:#888; font-size:11px; }}
  input[type=checkbox] {{ width:18px; height:18px; }}
  .row-form {{ display:flex; gap:6px; align-items:center; }}
  .row-form select {{ width:70px; padding:4px; }}
  .row-form button {{ margin:0; padding:5px 10px; font-size:12px; width:auto; }}
  .btn-update {{ background:#0F6E56; }}
  .btn-remove {{ background:#993C1D; }}
  .menu-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:20px; }}
  .menu-card {{ display:flex; align-items:center; gap:14px; padding:16px 18px; border:1px solid #eee;
                border-radius:10px; text-decoration:none; color:inherit; transition:0.15s; }}
  .menu-card:hover {{ border-color:#1F4E5F; background:#f8fafb; }}
  .menu-card-icon {{ font-size:24px; flex-shrink:0; }}
  .menu-card-body {{ flex:1; }}
  .menu-card-title {{ font-size:14px; font-weight:bold; color:#1F4E5F; }}
  .menu-card-desc {{ font-size:12px; color:#888; margin-top:2px; }}
  .menu-card-arrow {{ font-size:20px; color:#ccc; }}
</style>
</head>
<body>
<div class="layout">
  <nav>
    <div class="brand">塾スケジューリング<br>データ入力</div>
    {nav_links}
  </nav>
  <main>
    <div class="card {card_class}">
      {content}
    </div>
  </main>
</div>
</body>
</html>
"""


def render_page(path: str, content: str) -> bytes:
    """指定パスのタイトル・アクティブ状態を踏まえて、ページ全体のHTML(bytes)を組み立てる。"""
    nav_links = "".join(
        f'<a href="{p}" class="{"active" if p == path else ""}">{label}</a>' for p, label in MENU
    )
    card_class = "wide" if path == "/" else ""
    title = ROUTE_TITLES.get(path, path)
    html = _LAYOUT.format(title=title, nav_links=nav_links, content=content, card_class=card_class)
    return html.encode("utf-8")
