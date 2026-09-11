# -*- coding: utf-8 -*-
"""
layout.py

全ページ共通のHTMLレイアウト(左ナビゲーション+カードのラッパー)。
各ページモジュールは「中身のHTML(content)」だけを作り、
最終的な組み立ては全てこのモジュールに任せる。
"""

MENU_GROUPS = [
    ("基本設定", [
        ("/subjects", "科目マスタ"),
        ("/excel-import", "生徒・通常授業 Excel取り込み"),
        ("/closure-dates", "休校日設定"),
        ("/weekly-schedule-export", "通常授業 週間Excel出力"),
    ]),
    ("生徒情報", [
        ("/students", "生徒登録"),
        ("/student-detail", "生徒詳細"),
        ("/student-instructor-preferences", "生徒ごとの推奨・NG講師"),
        ("/regular-enrollments", "通常授業 契約登録"),
        ("/follow-enrollments", "教科フォロー登録"),
        ("/makeup-unscheduled", "未配置振替一覧"),
        ("/student-availability", "生徒 対応可能時間"),
        ("/schedule-student", "生徒視点の時間割"),
    ]),
    ("講師情報", [
        ("/instructors", "講師登録"),
        ("/instructor-import", "講師マスタ Excel取り込み"),
        ("/instructor-detail", "講師詳細"),
        ("/instructor-subjects", "講師 担当科目"),
        ("/instructor-search", "科目・レベルで講師検索"),
        ("/instructor-availability", "講師 対応可能時間"),
        ("/instructor-academic-year", "講師 学年更新の確認"),
        ("/schedule-instructor", "講師視点の時間割"),
    ]),
    ("講習会", [
        ("/camps-hub", "講習会"),
    ]),
    ("集計", [
        ("/utilization", "稼働率"),
        ("/instructor-performance", "講師実績確認"),
    ]),
]

# 既存コードとの互換用: グループを平坦化した (path, label) の一覧
MENU = [("/", "ホーム")] + [item for _, items in MENU_GROUPS for item in items]

ROUTE_TITLES = {path: label for path, label in MENU}
ROUTE_TITLES["/image-import-review"] = "取込結果確認"
ROUTE_TITLES["/image-import-corrections"] = "確定済みレビューの訂正"
ROUTE_TITLES["/makeup-schedule"] = "振替先を決める"

_LAYOUT = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>{title} - 塾スケジューリングシステム</title>
<style>
  * {{ box-sizing:border-box; }}
  html {{ overscroll-behavior: none; }}
  body {{ font-family:"Yu Gothic","Hiragino Sans",sans-serif; background:#f4f4f2; margin:0;
          overscroll-behavior: none; }}
  .layout {{ display:flex; min-height:100vh; }}
  nav {{ width:200px; background:#1F4E5F; color:#fff; padding:24px 0; flex-shrink:0; overflow-y:auto;
         position:fixed; top:0; left:0; height:100vh; }}
  nav .brand {{ font-size:14px; font-weight:bold; padding:0 20px 20px; opacity:0.85; }}
  nav .home-link {{ display:block; padding:10px 20px; color:#d8e6ea; text-decoration:none; font-size:13px;
                     border-bottom:1px solid rgba(255,255,255,0.1); margin-bottom:8px; }}
  nav .home-link:hover {{ background:#163a47; }}
  nav .home-link.active {{ background:#0F6E56; color:#fff; font-weight:bold; }}
  nav .group-title {{ padding:14px 20px 6px; font-size:11px; letter-spacing:0.05em; color:#8fb0b8;
                       text-transform:uppercase; }}
  nav .group-link {{ display:block; padding:14px 20px; margin-top:8px; color:#d8e6ea;
                      text-decoration:none; font-size:13px; border-top:1px solid rgba(255,255,255,0.1); }}
  nav .group-link:hover {{ background:#163a47; }}
  nav .group-link.active {{ background:#0F6E56; color:#fff; font-weight:bold; }}
  nav a {{ display:block; padding:8px 20px 8px 28px; color:#d8e6ea; text-decoration:none; font-size:13px; }}
  nav a:hover {{ background:#163a47; }}
  nav a.active {{ background:#0F6E56; color:#fff; font-weight:bold; }}
  main {{ flex:1; padding:40px; margin-left:200px; overscroll-behavior: none; }}
  .card {{ max-width:820px; background:#fff; border-radius:10px; padding:32px 36px;
           box-shadow:0 2px 10px rgba(0,0,0,0.08); }}
  .card.wide {{ max-width:none; width:100%; }}
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
  .menu-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:12px; margin-bottom:8px; }}
  .home-section-title {{ font-size:13px; color:#534AB7; margin-top:28px; margin-bottom:0; }}
  .home-section-title:first-of-type {{ margin-top:20px; }}
  .menu-card {{ display:flex; align-items:center; gap:14px; padding:16px 18px; border:1px solid #eee;
                border-radius:10px; text-decoration:none; color:inherit; transition:0.15s; }}
  .menu-card:hover {{ border-color:#1F4E5F; background:#f8fafb; }}
  .menu-card-icon {{ font-size:24px; flex-shrink:0; }}
  .menu-card-body {{ flex:1; }}
  .menu-card-title {{ font-size:14px; font-weight:bold; color:#1F4E5F; }}
  .menu-card-desc {{ font-size:12px; color:#888; margin-top:2px; }}
  .menu-card-arrow {{ font-size:20px; color:#ccc; }}
  .hub-grid {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:16px; margin-top:20px; }}
  .hub-card {{ border:1px solid #e5e5e5; border-radius:10px; padding:18px 20px; background:#fafbf9; }}
  .hub-card h2 {{ margin:0 0 10px; color:#1F4E5F; font-size:16px; }}
  .hub-card ul {{ margin:0; padding-left:20px; }}
  .hub-card li {{ margin:8px 0; font-size:13px; }}
  .hub-card a {{ color:#0F6E56; text-decoration:none; }}
  .hub-card a:hover {{ text-decoration:underline; }}
  @media (max-width:800px) {{ .hub-grid {{ grid-template-columns:1fr; }} }}

  /* --- ホームダッシュボード(時間割・教科フォロー・通知欄) --- */
  .date-nav {{ display:flex; align-items:center; gap:16px; margin-bottom:12px; }}
  .date-nav button {{ background:#eef2f0; border:none; width:32px; height:32px; border-radius:6px;
                       font-size:14px; color:#1F4E5F; cursor:pointer; margin-top:0; padding:0; }}
  .date-label {{ font-size:14px; font-weight:bold; color:#1F4E5F; }}

  .table-scroll {{ overflow-x:auto; }}
  table.timetable {{ border-collapse:collapse; font-size:12px; width:100%; }}
  table.timetable th, table.timetable td {{ border:1px solid #e5e3dd; padding:5px 4px; text-align:center;
                                              vertical-align:middle; height:30px; }}
  table.timetable thead th.period-head {{ background:#1F4E5F; color:#fff; font-size:11.5px; padding:6px; white-space:nowrap; }}
  table.timetable thead th.sub-head {{ background:#eef2f0; color:#666; font-weight:normal; font-size:10px; }}
  table.timetable td.empty {{ color:#ddd; }}
  table.timetable td.col-instructor {{ width:42px; }}
  table.timetable td.col-grade {{ width:26px; color:#999; font-size:10px; }}
  table.timetable td.col-student {{ width:110px; }}
  table.timetable td.col-subject {{ width:64px; }}
  table.timetable td.col-attendance {{ width:30px; }}

  .subject-badge {{ display:inline-block; padding:1px 6px; border-radius:3px; font-size:11px; }}

  .attendance-indicator {{ display:inline; font-size:14px; font-weight:bold; color:#ccc; cursor:pointer;
                            background:none; border:none; padding:0; margin:0; width:auto; }}
  .attendance-indicator.att-present {{ color:#3D7A2E; }}
  .attendance-indicator.att-absent {{ color:#B0352F; }}

  .cell-legend {{ font-size:11px; color:#999; margin-top:10px; }}

  .follow-blocks {{ display:flex; gap:14px; flex-wrap:wrap; align-items:flex-start; }}
  table.follow-table {{ border-collapse:collapse; font-size:12.5px; }}
  table.follow-table th, table.follow-table td {{ border:1px solid #e5e3dd; padding:6px; text-align:center; height:30px; }}
  table.follow-table thead th {{ color:#1F4E5F; font-weight:bold; font-size:11.5px; white-space:nowrap; }}
  table.follow-table td.f-instructor {{ width:42px; color:#333; font-weight:bold; }}
  table.follow-table td.f-grade {{ width:26px; color:#999; font-size:10px; }}
  table.follow-table td.f-student {{ width:96px; text-align:left; }}
  table.follow-table td.empty {{ color:#ddd; }}

  .notice-tabs {{ display:flex; gap:2px; }}
  .notice-tab {{ background:#eef2f0; border:none; padding:9px 20px; font-size:13px; color:#1F4E5F;
                 cursor:pointer; border-radius:6px 6px 0 0; margin-top:0; width:auto; }}
  .notice-tab.notice-tab-active {{ background:#1F4E5F; color:#fff; font-weight:bold; }}
  .notice-body {{ background:#fff; border:1px solid #e5e3dd; border-radius:0 6px 6px 6px; padding:14px 16px; min-height:60px; }}
  .notice-list {{ list-style:none; margin:0; padding:0; font-size:13px; max-height:180px; overflow-y:auto; }}
  .notice-list li {{ padding:7px 0; border-bottom:1px solid #f0f0ee; display:flex; align-items:center; gap:8px; }}
  .notice-list a {{ color:#1F4E5F; text-decoration:none; }}
  .priority-badge {{ display:inline-flex; align-items:center; justify-content:center; width:16px; height:16px;
                      border-radius:50%; background:#D3382F; color:#fff; font-size:10px; font-weight:bold; flex-shrink:0; }}
  .person-quick-view {{ color:#1F4E5F; cursor:pointer; text-decoration:underline dotted;
                        text-underline-offset:3px; user-select:none; }}
  .person-quick-view:focus {{ outline:2px solid #6aa6ba; outline-offset:2px; border-radius:2px; }}
  .person-quick-view-popover {{ position:fixed; z-index:1000; min-width:210px; max-width:320px;
                                padding:10px 12px; background:#fff; color:#333; border:1px solid #d6d6d2;
                                border-radius:7px; box-shadow:0 4px 16px rgba(0,0,0,0.18); font-size:12px;
                                line-height:1.7; }}
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
<script>
  let activePersonQuickView = null;

  function closePersonQuickView() {{
    if (activePersonQuickView) activePersonQuickView.remove();
    activePersonQuickView = null;
  }}

  function showQuickView(el, summaryHtml) {{
    closePersonQuickView();
    const popover = document.createElement('div');
    popover.className = 'person-quick-view-popover';
    popover.innerHTML = summaryHtml;
    document.body.appendChild(popover);
    const rect = el.getBoundingClientRect();
    const left = Math.min(rect.left, window.innerWidth - popover.offsetWidth - 12);
    popover.style.left = Math.max(12, left) + 'px';
    popover.style.top = Math.min(rect.bottom + 6, window.innerHeight - popover.offsetHeight - 12) + 'px';
    activePersonQuickView = popover;
  }}

  function setupPersonQuickView(el, detailUrl, summaryHtml) {{
    let clickTimer = null;
    el.addEventListener('click', function(event) {{
      event.stopPropagation();
      if (clickTimer) {{
        clearTimeout(clickTimer);
        clickTimer = null;
        closePersonQuickView();
        location.href = detailUrl;
      }} else {{
        clickTimer = setTimeout(function() {{
          clickTimer = null;
          showQuickView(el, summaryHtml);
        }}, 250);
      }}
    }});
    el.addEventListener('keydown', function(event) {{
      if (event.key === 'Enter') location.href = detailUrl;
      if (event.key === 'Escape') closePersonQuickView();
    }});
  }}

  document.addEventListener('DOMContentLoaded', function() {{
    document.querySelectorAll('.person-quick-view').forEach(function(el) {{
      setupPersonQuickView(el, el.dataset.detailUrl, el.dataset.summaryHtml);
    }});
  }});
  document.addEventListener('click', closePersonQuickView);
</script>
</body>
</html>
"""


# 表やグリッド(横に長くなりやすい要素)を含むページは、横幅を広めに使う
WIDE_PATHS = {
    "/", "/subjects", "/instructor-subjects", "/camp-enrollments",
    "/student-availability", "/instructor-availability",
    "/camp-availability-student", "/camp-availability-instructor",
    "/regular-enrollments", "/follow-enrollments", "/student-detail", "/camp-sync-groups",
    "/schedule-by-day", "/schedule-instructor", "/schedule-student", "/excel-import",
    "/image-import",
    "/image-import-review",
    "/image-import-corrections",
    "/student-instructor-preferences",
    "/camps-hub",
}


def render_page(path: str, content: str) -> bytes:
    """指定パスのタイトル・アクティブ状態を踏まえて、ページ全体のHTML(bytes)を組み立てる。"""
    home_class = "active" if path == "/" else ""
    nav_links = f'<a href="/" class="home-link {home_class}">ホーム</a>'
    for group_name, items in MENU_GROUPS:
        # グループ名と唯一のリンク名が同じ場合は、展開見出しを作らず単独リンクとして描画する。
        if len(items) == 1 and items[0][1] == group_name:
            item_path, label = items[0]
            active_class = "active" if item_path == path else ""
            nav_links += f'<a href="{item_path}" class="group-link {active_class}">{label}</a>'
            continue
        nav_links += f'<div class="group-title">{group_name}</div>'
        nav_links += "".join(
            f'<a href="{p}" class="{"active" if p == path else ""}">{label}</a>' for p, label in items
        )
    card_class = "wide" if path in WIDE_PATHS else ""
    title = ROUTE_TITLES.get(path, path)
    html = _LAYOUT.format(title=title, nav_links=nav_links, content=content, card_class=card_class)
    return html.encode("utf-8")
