# -*- coding: utf-8 -*-
"""
scheduler.py (Step 1: 候補講師リストの組み立て)

CP-SATで実際に時間割を解く前の「下準備」部分。
生徒1人・科目1つの契約ごとに、「誰に教われるか」を優先順位付きで
組み立てる。この時点ではまだ日付・時間の割り当ては考えない。

優先順位(重複無しでこの順番に並べる):
  1. 個別指定講師(CAMP_COURSE_ENROLLMENTS.assigned_instructor_id)
  2. 通常授業の継続講師(REGULAR_COURSE_ENROLLMENTSで同じ科目を担当している講師)
  3. 最終手段: 科目さえ合っていれば誰でもよい(INSTRUCTOR_SUBJECTS登録者から自動補完)
"""

import sqlite3
import time
from db import get_conn
from page_camp_enrollments import get_max_sessions_per_day


def get_instructor_candidates(conn: sqlite3.Connection, enrollment_id: int) -> list[int]:
    """
    指定した講習会受講契約(enrollment_id)について、
    「教われる可能性のある講師」を優先順位順に並べたリストを返す。
    (重複は除去され、それぞれの講師は1回だけ登場する)
    """
    enrollment = conn.execute(
        "SELECT camp_id, student_id, subject_id, assigned_instructor_id FROM CAMP_COURSE_ENROLLMENTS "
        "WHERE enrollment_id = ?",
        (enrollment_id,),
    ).fetchone()
    if enrollment is None:
        raise ValueError(f"enrollment_id={enrollment_id} が見つかりません")

    _camp_id, student_id, subject_id, assigned_instructor_id = enrollment

    ordered_candidates: list[int] = []
    seen: set[int] = set()

    active_instructor_ids = {
        row[0] for row in conn.execute("SELECT instructor_id FROM INSTRUCTORS WHERE status = '在籍'").fetchall()
    }

    def add(instructor_id):
        # 休職・辞職中の講師は、たとえ指定講師・継続講師・優先リストに載っていても候補から除外する
        if instructor_id is not None and instructor_id not in seen and instructor_id in active_instructor_ids:
            ordered_candidates.append(instructor_id)
            seen.add(instructor_id)

    # 1. 個別指定講師
    add(assigned_instructor_id)

    # 2. 通常授業の継続講師(同じ生徒・同じ科目で、現在有効な通常授業契約があれば)
    continuing = conn.execute(
        "SELECT instructor_id FROM REGULAR_COURSE_ENROLLMENTS "
        "WHERE student_id = ? AND subject_id = ? AND effective_end_date IS NULL",
        (student_id, subject_id),
    ).fetchone()
    if continuing:
        add(continuing[0])

    # 3. 最終手段: ここまでで候補が1人も見つからなかった場合、
    #    科目さえ合っていれば誰でもよいので、INSTRUCTOR_SUBJECTSに登録されている
    #    講師を候補として補う。
    #    (候補リストの1番目を繰り返し使うほど目的関数のスコアが上がる設計のため、
    #     ここで並べる順序が「同じ生徒・同じ科目の担当講師を1人に絞る」効果を持つ)
    #
    #    ただし、習熟度だけで単純に並べると、複数の生徒が同じ科目でフォールバックを
    #    必要とした場合に「習熟度が最も高い講師1人だけに生徒が集中し、他の講師のコマが
    #    スカスカになる」という偏りが起きる。これを緩和するため、enrollment_idに応じて
    #    候補リストの並び始めをずらし(ローテーション)、生徒によって「誰を1番手にするか」を
    #    分散させる。習熟度による並び順自体は維持したまま、1番手を回していくイメージ。
    #
    #    算国(小学生低学年)だけは、講師に「算国を教えられますか」というアンケートを
    #    取っていないため、判定方法だけが他の科目と異なる:
    #    算数・国語それぞれの担当登録を見て、両方できる講師を優先、次に片方だけできる講師。
    #    (算国を"特別な優先ステップ"にはせず、フォールバックの中の判定ロジックの違いとして扱う)
    if not ordered_candidates:
        subject_row = conn.execute(
            "SELECT subject_group, grade_band FROM SUBJECTS WHERE subject_id = ?", (subject_id,)
        ).fetchone()

        if subject_row and subject_row[0] == "算国":
            grade_band = subject_row[1]
            capable = conn.execute(
                """SELECT isub.instructor_id, s.subject_group, MAX(isub.proficiency_level)
                   FROM INSTRUCTOR_SUBJECTS isub
                   JOIN SUBJECTS s ON s.subject_id = isub.subject_id
                   JOIN INSTRUCTORS i ON i.instructor_id = isub.instructor_id
                   WHERE s.grade_band = ? AND s.subject_group IN ('算数', '国語') AND i.status = '在籍'
                   GROUP BY isub.instructor_id, s.subject_group""",
                (grade_band,),
            ).fetchall()

            by_instructor: dict[int, dict[str, int]] = {}
            for instructor_id, sg, proficiency in capable:
                by_instructor.setdefault(instructor_id, {})[sg] = proficiency

            both = [
                (iid, groups.get("算数", 0) + groups.get("国語", 0))
                for iid, groups in by_instructor.items() if "算数" in groups and "国語" in groups
            ]
            either = [
                (iid, groups.get("算数", 0) + groups.get("国語", 0))
                for iid, groups in by_instructor.items() if not ("算数" in groups and "国語" in groups)
            ]
            both.sort(key=lambda t: -t[1])
            either.sort(key=lambda t: -t[1])
            fallback_ids = [iid for iid, _score in both] + [iid for iid, _score in either]
        else:
            fallback_rows = conn.execute(
                """SELECT isub.instructor_id FROM INSTRUCTOR_SUBJECTS isub
                   JOIN INSTRUCTORS i ON i.instructor_id = isub.instructor_id
                   WHERE isub.subject_id = ? AND i.status = '在籍'
                   ORDER BY isub.proficiency_level DESC, isub.instructor_id""",
                (subject_id,),
            ).fetchall()
            fallback_ids = [row[0] for row in fallback_rows]

        if fallback_ids:
            offset = enrollment_id % len(fallback_ids)
            rotated = fallback_ids[offset:] + fallback_ids[:offset]
            for instructor_id in rotated:
                add(instructor_id)

    return ordered_candidates


def build_candidates_for_camp(conn: sqlite3.Connection, camp_id: int) -> dict[int, list[int]]:
    """
    講習会全体について、全ての受講契約(enrollment_id)ごとの候補講師リストをまとめて返す。
    戻り値: {enrollment_id: [instructor_id, instructor_id, ...], ...}
    """
    enrollment_ids = [
        row[0] for row in conn.execute(
            "SELECT enrollment_id FROM CAMP_COURSE_ENROLLMENTS WHERE camp_id = ?", (camp_id,)
        ).fetchall()
    ]
    return {eid: get_instructor_candidates(conn, eid) for eid in enrollment_ids}


# ---------------------------------------------------------
# Step 2: 対応可能時間・通常授業の衝突を考慮した「候補枠」の組み立て
# ---------------------------------------------------------

def get_regular_blocked_periods(conn: sqlite3.Connection, role: str, entity_id: int) -> set[tuple[str, int]]:
    """
    生徒(role='student')または講師(role='instructor')が、
    通常授業で既に埋まっている(曜日, 限)の組み合わせを返す。
    科目を問わず、現在有効な契約が対象。
    """
    id_col = "student_id" if role == "student" else "instructor_id"
    rows = conn.execute(
        f"SELECT day_of_week, period_number FROM REGULAR_COURSE_ENROLLMENTS "
        f"WHERE {id_col} = ? AND effective_end_date IS NULL",
        (entity_id,),
    ).fetchall()
    return {(d, p) for d, p in rows}


def _weekday_jp(date_str: str) -> str:
    import datetime
    y, m, d = map(int, date_str.split("-"))
    return ["月", "火", "水", "木", "金", "土", "日"][datetime.date(y, m, d).weekday()]


def get_enrollment_date_range(conn: sqlite3.Connection, enrollment_id: int) -> tuple[str, str]:
    """その契約の(開始日, 終了日)を返す。終了日はenrollment_end_dateが優先、無ければ講習会全体の終了日。"""
    row = conn.execute(
        """SELECT c.planned_start_date, c.planned_end_date, e.enrollment_end_date
           FROM CAMP_COURSE_ENROLLMENTS e JOIN CAMPS c ON c.camp_id = e.camp_id
           WHERE e.enrollment_id = ?""",
        (enrollment_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"enrollment_id={enrollment_id} が見つかりません")
    planned_start, planned_end, enrollment_end = row
    end_date = enrollment_end if enrollment_end else planned_end
    return planned_start, end_date


def get_candidate_slots(conn: sqlite3.Connection, enrollment_id: int, instructor_id: int) -> list[int]:
    """
    ある受講契約(enrollment_id)を、ある講師(instructor_id)が担当すると仮定した場合に、
    実際に使える可能性のある slot_id の一覧を返す(生徒/講師の対応可能時間と、
    双方の通常授業の空き状況を全て満たすもの)。
    """
    enrollment = conn.execute(
        "SELECT camp_id, student_id FROM CAMP_COURSE_ENROLLMENTS WHERE enrollment_id = ?",
        (enrollment_id,),
    ).fetchone()
    if enrollment is None:
        raise ValueError(f"enrollment_id={enrollment_id} が見つかりません")
    camp_id, student_id = enrollment

    start_date, end_date = get_enrollment_date_range(conn, enrollment_id)

    student_blocked = get_regular_blocked_periods(conn, "student", student_id)
    instructor_blocked = get_regular_blocked_periods(conn, "instructor", instructor_id)

    # 生徒が対応可能な枠 ∩ 講師が対応可能な枠 を、日付範囲内でSQL側で絞り込む
    rows = conn.execute(
        """SELECT ts.slot_id, ts.session_date, ts.period_number
           FROM TIME_SLOTS ts
           JOIN CAMP_STUDENT_AVAILABILITY sa ON sa.slot_id = ts.slot_id AND sa.is_available = 1
           JOIN CAMP_INSTRUCTOR_AVAILABILITY ia ON ia.slot_id = ts.slot_id AND ia.is_available = 1
           WHERE sa.student_id = ? AND ia.instructor_id = ?
             AND ts.session_date BETWEEN ? AND ?""",
        (student_id, instructor_id, start_date, end_date),
    ).fetchall()

    candidate_slots = []
    for slot_id, session_date, period_number in rows:
        wd = _weekday_jp(session_date)
        if (wd, period_number) in student_blocked:
            continue
        if (wd, period_number) in instructor_blocked:
            continue
        candidate_slots.append(slot_id)

    return candidate_slots


def build_slot_candidates_for_enrollment(conn: sqlite3.Connection, enrollment_id: int) -> dict[int, list[int]]:
    """
    ある受講契約について、Step1の候補講師リスト順に、それぞれの講師が使える候補枠を組み立てる。
    戻り値: {instructor_id: [slot_id, ...], ...} (Step1のordered_candidatesと同じ順序を保つ)
    """
    instructor_candidates = get_instructor_candidates(conn, enrollment_id)
    return {
        instructor_id: get_candidate_slots(conn, enrollment_id, instructor_id)
        for instructor_id in instructor_candidates
    }


# ---------------------------------------------------------
# Step 3a: CP-SAT本体(コア制約: 重複防止・コマ数の埋まり方・優先順位)
# ---------------------------------------------------------

PRIORITY_RANK_WEIGHT_BASE = 1000  # 優先順位による重みの基準値(候補数より十分大きい値)
FILL_WEIGHT = 100_000             # 「埋まったコマ数」を最優先させるための重み(他の評価より圧倒的に大きくする)
PAIRING_BONUS = 10_000  # 1つの枠に2人まとまった(1:2の稼働率が高い)場合のボーナス。優先順位より重視するが、埋まったコマ数は上書きしない

# --- 間隔・曜日ズレの評価に使う重み ---
WEEKDAY_GAP_WEIGHT = 20     # 曜日のズレ(7の倍数からの差)^2 にかける重み
INTERVAL_GAP_WEIGHT = 1     # 理想間隔からの単純な日数差にかける重み
CLUSTER_PENALTY = 250       # 同日・隣接日(1日以内)に2コマ入ったときの追加ペナルティ(曜日ズレの最大値より確実に重くする)
CLUSTER_THRESHOLD_DAYS = 1  # これ以下の日数差は「詰め込みすぎ」とみなす
SPACING_PAIR_WINDOW_MULTIPLIER = 3  # 理想間隔の何倍まで離れたペア間の評価を計算するか(遠すぎるペアは無視)
SYNC_MISMATCH_PENALTY = 5_000  # 兄弟同時受講グループが同じ日に別の限になった場合のペナルティ(ほぼ絶対条件として扱うため非常に重くする)


def _date_to_ordinal(conn: sqlite3.Connection, slot_id: int, slot_date_cache: dict[int, int]) -> int:
    """slot_idから日付の通し番号(ordinal)を取得する。同じslot_idは何度も問い合わせない。"""
    if slot_id not in slot_date_cache:
        import datetime
        date_str = conn.execute("SELECT session_date FROM TIME_SLOTS WHERE slot_id = ?", (slot_id,)).fetchone()[0]
        y, m, d = map(int, date_str.split("-"))
        slot_date_cache[slot_id] = datetime.date(y, m, d).toordinal()
    return slot_date_cache[slot_id]


def _spacing_penalty(gap_days: int) -> int:
    """2つの授業日の間隔(gap_days)から、間隔・曜日ズレのペナルティ点数を計算する。"""
    if gap_days == 0:
        return CLUSTER_PENALTY * 2  # 同日2コマは最も避けたい
    if gap_days <= CLUSTER_THRESHOLD_DAYS:
        return CLUSTER_PENALTY

    weekday_gap = gap_days % 7
    weekday_gap = min(weekday_gap, 7 - weekday_gap)  # 0(同じ曜日)〜3(最大ズレ)
    return (weekday_gap ** 2) * WEEKDAY_GAP_WEIGHT + gap_days * INTERVAL_GAP_WEIGHT


def is_regular_continuation_subject(
    conn: sqlite3.Connection, student_id: int, subject_id: int, as_of_date: str
) -> bool:
    """指定日時点で有効な、同じ生徒・科目の通常授業契約があるかを返す。

    ``effective_end_date`` は、通常授業の表示処理と同じく終了日当日を
    含まない境界として扱う。
    """
    row = conn.execute(
        """SELECT 1
           FROM REGULAR_COURSE_ENROLLMENTS
           WHERE student_id = ? AND subject_id = ?
             AND effective_start_date <= ?
             AND (effective_end_date IS NULL OR effective_end_date > ?)
           LIMIT 1""",
        (student_id, subject_id, as_of_date, as_of_date),
    ).fetchone()
    return row is not None


def _get_current_camp_assignments(
    conn: sqlite3.Connection, camp_id: int
) -> dict[int, list[tuple[int, int]]]:
    """DB上の現行割当を enrollment_id ごとに返す。

    ASSIGNMENTS は enrollment_id を持たないため、生徒・科目で講習会契約へ
    対応付ける。同じ講習会に同じ生徒・科目の契約が複数ある場合は安全に
    対応付けられないので、黙って誤固定せずエラーにする。
    """
    duplicate = conn.execute(
        """SELECT student_id, subject_id, COUNT(*)
           FROM CAMP_COURSE_ENROLLMENTS
           WHERE camp_id = ?
           GROUP BY student_id, subject_id
           HAVING COUNT(*) > 1
           LIMIT 1""",
        (camp_id,),
    ).fetchone()
    if duplicate:
        raise ValueError(
            "部分再計算を実行できません。同じ講習会に同一生徒・同一科目の契約が"
            f"複数あります(student_id={duplicate[0]}, subject_id={duplicate[1]})。"
        )

    current: dict[int, list[tuple[int, int]]] = {
        row[0]: []
        for row in conn.execute(
            "SELECT enrollment_id FROM CAMP_COURSE_ENROLLMENTS WHERE camp_id = ?", (camp_id,)
        ).fetchall()
    }
    rows = conn.execute(
        """SELECT e.enrollment_id, s.instructor_id, s.slot_id
           FROM SESSIONS s
           JOIN ASSIGNMENTS a ON a.session_id = s.session_id
           JOIN CAMP_COURSE_ENROLLMENTS e
             ON e.camp_id = s.camp_id
            AND e.student_id = a.student_id
            AND e.subject_id = a.subject_id
           WHERE s.camp_id = ?
           ORDER BY e.enrollment_id, s.slot_id, s.instructor_id""",
        (camp_id,),
    ).fetchall()
    for enrollment_id, instructor_id, slot_id in rows:
        current[enrollment_id].append((instructor_id, slot_id))
    return current


def _solve_camp_core_once(
    conn: sqlite3.Connection,
    camp_id: int,
    time_limit_seconds: float,
    fixed_assignments: dict[int, list[tuple[int, int]]] | None = None,
    required_counts: dict[int, int] | None = None,
) -> dict:
    """
    講習会を1回解く。部分再計算の段階制御は solve_camp_core が行う。
    - コア制約: 重複防止・コマ数の埋まり方・優先順位(Step3a)
    - 授業間隔・曜日ズレの評価(Step3b): 同じ契約内の2つの授業日が近すぎたり、
      理想の間隔(曜日が揃う間隔)からズレていたりすると、目的関数上でペナルティを受ける

    戻り値: {
        "assignments": {enrollment_id: [(instructor_id, slot_id), ...], ...},
        "unfulfilled": {enrollment_id: 埋まらなかったコマ数, ...},
        "status": "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | ...
    }
    """
    from ortools.sat.python import cp_model

    fixed_assignments = fixed_assignments or {}
    required_counts = required_counts or {}

    enrollments = conn.execute(
        "SELECT enrollment_id, student_id, contracted_count, format FROM CAMP_COURSE_ENROLLMENTS WHERE camp_id = ?",
        (camp_id,),
    ).fetchall()

    model = cp_model.CpModel()

    x: dict[int, dict[tuple[int, int], any]] = {}
    enrollment_info: dict[int, dict] = {}
    session_members: dict[tuple[int, int], list[tuple[int, str]]] = {}
    student_slot_vars: dict[int, dict[int, list]] = {}
    student_date_vars: dict[int, dict[str, list]] = {}  # 1日の上限コマ数チェック用(全科目合算)
    slot_date_cache: dict[int, int] = {}
    slot_date_str_cache: dict[int, str] = {}
    enrollment_rank_of: dict[int, dict[int, int]] = {}  # 候補講師リストの順位(2回計算しないためのキャッシュ)

    for enrollment_id, student_id, contracted_count, format_ in enrollments:
        enrollment_info[enrollment_id] = {
            "student_id": student_id, "contracted_count": contracted_count, "format": format_,
        }
        candidates = build_slot_candidates_for_enrollment(conn, enrollment_id)
        # 固定済みの割当は、現在の対応可能時間や講師の在籍状態が変わっていても
        # 「絶対に動かさない」ため、候補に無ければモデルへ明示的に戻す。
        for fixed_instructor_id, fixed_slot_id in fixed_assignments.get(enrollment_id, []):
            candidates.setdefault(fixed_instructor_id, [])
            if fixed_slot_id not in candidates[fixed_instructor_id]:
                candidates[fixed_instructor_id].append(fixed_slot_id)
        x[enrollment_id] = {}
        enrollment_rank_of[enrollment_id] = {
            instructor_id: rank for rank, instructor_id in enumerate(candidates.keys())
        }

        for rank, (instructor_id, slot_ids) in enumerate(candidates.items()):
            for slot_id in slot_ids:
                var = model.NewBoolVar(f"x_e{enrollment_id}_i{instructor_id}_s{slot_id}")
                x[enrollment_id][(instructor_id, slot_id)] = var
                _date_to_ordinal(conn, slot_id, slot_date_cache)  # キャッシュに乗せておく

                if slot_id not in slot_date_str_cache:
                    slot_date_str_cache[slot_id] = conn.execute(
                        "SELECT session_date FROM TIME_SLOTS WHERE slot_id = ?", (slot_id,)
                    ).fetchone()[0]
                session_date = slot_date_str_cache[slot_id]

                session_members.setdefault((instructor_id, slot_id), []).append((enrollment_id, format_))
                student_slot_vars.setdefault(student_id, {}).setdefault(slot_id, []).append(var)
                student_date_vars.setdefault(student_id, {}).setdefault(session_date, []).append(var)

    if not any(x[eid] for eid in x) and not required_counts:
        return {"assignments": {}, "unfulfilled": {eid: info["contracted_count"] for eid, info in enrollment_info.items()}, "status": "NO_CANDIDATES"}

    # --- 制約1: 契約コマ数を超えない ---
    for enrollment_id, info in enrollment_info.items():
        vars_for_enrollment = list(x[enrollment_id].values())
        total = sum(vars_for_enrollment) if vars_for_enrollment else 0
        model.Add(total <= info["contracted_count"])
        if enrollment_id in required_counts:
            model.Add(total == required_counts[enrollment_id])

    # --- 部分再計算: 現在の(講師, 枠)を等式制約で固定する ---
    for enrollment_id, assigned_pairs in fixed_assignments.items():
        for pair in assigned_pairs:
            model.Add(x[enrollment_id][pair] == 1)

    # --- 制約2: 生徒は同じ枠に二重で入らない ---
    for student_id, slot_map in student_slot_vars.items():
        for slot_id, vars_here in slot_map.items():
            if len(vars_here) > 1:
                model.Add(sum(vars_here) <= 1)

    # --- 制約3: 1つの枠(講師×日時)は最大2人まで。1:1契約が入ったら専有 ---
    pairing_bonus_vars = []
    for (instructor_id, slot_id), members in session_members.items():
        vars_here = [x[eid][(instructor_id, slot_id)] for eid, _fmt in members]
        if len(vars_here) > 1:
            model.Add(sum(vars_here) <= 2)
            # 稼働率ボーナス: この枠に実際に2人まとまったら加点する(2*paired<=合計、最大化方向なので
            # 2人揃ったときだけpaired=1にする方がスコアが上がり、自然とそちらが選ばれる)
            paired = model.NewBoolVar(f"paired_i{instructor_id}_s{slot_id}")
            model.Add(2 * paired <= sum(vars_here))
            pairing_bonus_vars.append(paired)
        for eid, fmt in members:
            if fmt == "1:1":
                this_var = x[eid][(instructor_id, slot_id)]
                other_vars = [x[oeid][(instructor_id, slot_id)] for oeid, _f in members if oeid != eid]
                if other_vars:
                    model.Add(sum(other_vars) == 0).OnlyEnforceIf(this_var)

    # --- 制約4: 1日の上限コマ数(全科目合算、個別上書きがあればそちらを優先) ---
    for student_id, date_map in student_date_vars.items():
        max_per_day, _is_override = get_max_sessions_per_day(conn, camp_id, student_id)
        for session_date, vars_here in date_map.items():
            if len(vars_here) > max_per_day:
                model.Add(sum(vars_here) <= max_per_day)

    # --- 目的関数の材料1: 埋まったコマ数 + 優先順位(Step3a) ---
    objective_terms = []
    for enrollment_id in x:
        rank_of_instructor = enrollment_rank_of[enrollment_id]
        for (instructor_id, slot_id), var in x[enrollment_id].items():
            rank = rank_of_instructor.get(instructor_id, 99)
            weight = FILL_WEIGHT + (PRIORITY_RANK_WEIGHT_BASE - rank)
            objective_terms.append(var * weight)

    # --- 目的関数の材料2: 同じ契約内での間隔・曜日ズレのペナルティ(Step3b) ---
    # (講師×枠)ごとにペアを作ると組み合わせが爆発する(大規模データでモデル構築が終わらなくなる)ため、
    # 先に「日付」単位へ集約してから、日付同士のペアだけを評価する
    # (間隔・曜日のズレは、どの講師が担当するかとは関係なく、日付だけで決まるため)。
    penalty_terms = []
    for enrollment_id, info in enrollment_info.items():
        contracted_count = info["contracted_count"]
        var_items = list(x[enrollment_id].items())
        if contracted_count <= 1 or len(var_items) < 2:
            continue  # 1コマだけの契約には間隔の概念が無い

        # 日付ごとに、その日付に該当する変数をまとめる
        vars_by_date: dict[int, list] = {}
        for (_instr, slot_id), var in var_items:
            date_ord = slot_date_cache[slot_id]
            vars_by_date.setdefault(date_ord, []).append(var)

        distinct_dates = sorted(vars_by_date.keys())
        if len(distinct_dates) < 2:
            continue

        span_days = distinct_dates[-1] - distinct_dates[0]
        ideal_interval = span_days / max(contracted_count - 1, 1) if span_days > 0 else 7
        pair_window = ideal_interval * SPACING_PAIR_WINDOW_MULTIPLIER

        # 各日付について「その日に何かしら割り当てがあるか」を表す変数(has_date)を1つだけ作る
        has_date_vars: dict[int, any] = {}
        for date_ord in distinct_dates:
            vars_here = vars_by_date[date_ord]
            expr = sum(vars_here)
            if len(vars_here) == 1:
                has_date_vars[date_ord] = vars_here[0]  # 変数が1つだけならそのまま使い回す
            else:
                has_date = model.NewBoolVar(f"has_date_e{enrollment_id}_{date_ord}")
                model.Add(has_date <= expr)
                model.Add(expr <= has_date * len(vars_here))
                has_date_vars[date_ord] = has_date

        # 日付同士のペア(高々「講習会の日数」の2乗、講師や枠の数には依存しない)だけを評価する
        for i in range(len(distinct_dates)):
            date_a = distinct_dates[i]
            for j in range(i + 1, len(distinct_dates)):
                date_b = distinct_dates[j]
                gap_days = date_b - date_a
                if gap_days > pair_window:
                    break  # distinct_datesは昇順なので、これ以降はさらに遠くなるだけ
                penalty = _spacing_penalty(gap_days)
                if penalty > 0:
                    both_selected = model.NewBoolVar(f"pair_e{enrollment_id}_{date_a}_{date_b}")
                    model.AddMultiplicationEquality(both_selected, [has_date_vars[date_a], has_date_vars[date_b]])
                    penalty_terms.append(both_selected * penalty)

    # --- 目的関数の材料3: 兄弟等 同時受講グループのペナルティ(Step3c) ---
    # 生徒×日付×限ごとの「出席している(その枠に割り当てがある)」を表す式を組み立てる。
    # 1つの(生徒,slot_id)には制約2により高々1つの変数しか1にならないため、
    # sum(vars)がそのまま0/1の"出席フラグ"として扱える(専用の変数を新設する必要がない)。
    student_date_period_expr: dict[int, dict[tuple[str, int], any]] = {}
    for student_id, slot_map in student_slot_vars.items():
        for slot_id, vars_here in slot_map.items():
            session_date = slot_date_str_cache[slot_id]
            period_number = conn.execute(
                "SELECT period_number FROM TIME_SLOTS WHERE slot_id = ?", (slot_id,)
            ).fetchone()[0]
            key = (session_date, period_number)
            expr = sum(vars_here)
            student_date_period_expr.setdefault(student_id, {})[key] = expr

    sync_groups = conn.execute(
        "SELECT sync_group_id FROM CAMP_STUDENT_SYNC_GROUPS WHERE camp_id = ?", (camp_id,)
    ).fetchall()

    for (sync_group_id,) in sync_groups:
        member_ids = [
            row[0] for row in conn.execute(
                "SELECT student_id FROM CAMP_STUDENT_SYNC_GROUP_MEMBERS WHERE sync_group_id = ?",
                (sync_group_id,),
            ).fetchall()
        ]
        # このグループの誰かが対応可能な(日付,限)の組み合わせを全て集める
        all_keys: set[tuple[str, int]] = set()
        for sid in member_ids:
            all_keys.update(student_date_period_expr.get(sid, {}).keys())

        # 日付ごとにまとめる(同じ日の中で、限が食い違っていないかを見るため)
        dates_involved = sorted(set(d for d, _p in all_keys))
        for session_date in dates_involved:
            periods_this_date = sorted(set(p for d, p in all_keys if d == session_date))
            for i in range(len(member_ids)):
                for j in range(i + 1, len(member_ids)):
                    sid_a, sid_b = member_ids[i], member_ids[j]
                    for p1 in periods_this_date:
                        expr_a = student_date_period_expr.get(sid_a, {}).get((session_date, p1))
                        if expr_a is None:
                            continue
                        for p2 in periods_this_date:
                            if p1 == p2:
                                continue
                            expr_b = student_date_period_expr.get(sid_b, {}).get((session_date, p2))
                            if expr_b is None:
                                continue
                            # 「Aがp1に出席」かつ「Bがp2に出席」なら、限が食い違っているのでペナルティ
                            mismatch = model.NewBoolVar(f"sync_mismatch_{sync_group_id}_{session_date}_{sid_a}{p1}_{sid_b}{p2}")
                            model.Add(mismatch <= expr_a)
                            model.Add(mismatch <= expr_b)
                            model.Add(mismatch >= expr_a + expr_b - 1)
                            penalty_terms.append(mismatch * SYNC_MISMATCH_PENALTY)

    pairing_bonus_terms = [p * PAIRING_BONUS for p in pairing_bonus_vars]
    model.Maximize(sum(objective_terms) + sum(pairing_bonus_terms) - sum(penalty_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    import os
    solver.parameters.num_search_workers = max(1, os.cpu_count() or 1)  # 環境のCPUコア数に応じて並列探索する
    solver.parameters.log_search_progress = True  # 長時間の求解中、進捗が分かるようにログを出す
    status = solver.Solve(model)

    status_name = solver.StatusName(status)
    assignments: dict[int, list[tuple[int, int]]] = {eid: [] for eid in enrollment_info}
    unfulfilled: dict[int, int] = {}

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for enrollment_id, var_map in x.items():
            for (instructor_id, slot_id), var in var_map.items():
                if solver.Value(var) == 1:
                    assignments[enrollment_id].append((instructor_id, slot_id))
        for enrollment_id, info in enrollment_info.items():
            filled = len(assignments[enrollment_id])
            if filled < info["contracted_count"]:
                unfulfilled[enrollment_id] = info["contracted_count"] - filled
    else:
        unfulfilled = {eid: info["contracted_count"] for eid, info in enrollment_info.items()}

    return {"assignments": assignments, "unfulfilled": unfulfilled, "status": status_name}


def solve_camp_core(
    conn: sqlite3.Connection,
    camp_id: int,
    time_limit_seconds: float = 7200.0,
    target_student_ids: set[int] | None = None,
) -> dict:
    """講習会全体、または指定生徒に限定した部分再計算を行う。

    ``target_student_ids`` が ``None`` の場合は従来どおり全契約を自由に解く。
    指定された場合は、まず対象外の全割当を固定して解き、解が無い場合だけ
    対象外生徒の講習会限定科目を解放する。通常授業の継続科目は最後まで固定する。
    """
    if target_student_ids is None:
        return _solve_camp_core_once(conn, camp_id, time_limit_seconds)

    target_student_ids = {int(student_id) for student_id in target_student_ids}
    if not target_student_ids:
        raise ValueError("部分再計算の対象生徒を1人以上選択してください")

    enrollment_rows = conn.execute(
        """SELECT enrollment_id, student_id, subject_id, contracted_count
           FROM CAMP_COURSE_ENROLLMENTS WHERE camp_id = ?""",
        (camp_id,),
    ).fetchall()
    camp_student_ids = {row[1] for row in enrollment_rows}
    unknown_ids = target_student_ids - camp_student_ids
    if unknown_ids:
        raise ValueError(
            "選択された生徒はこの講習会の契約を持っていません: "
            + ", ".join(map(str, sorted(unknown_ids)))
        )

    camp_row = conn.execute(
        "SELECT planned_start_date FROM CAMPS WHERE camp_id = ?", (camp_id,)
    ).fetchone()
    if camp_row is None:
        raise ValueError(f"camp_id={camp_id} が見つかりません")
    as_of_date = camp_row[0]

    current = _get_current_camp_assignments(conn, camp_id)
    if not any(current.values()):
        raise ValueError(
            "部分再計算の元になる既存時間割がありません。先に全体スケジューリングを実行してください"
        )
    target_enrollment_ids: set[int] = set()
    fixed_regular_ids: set[int] = set()
    limited_subject_ids: set[int] = set()
    contracted_counts: dict[int, int] = {}

    for enrollment_id, student_id, subject_id, contracted_count in enrollment_rows:
        contracted_counts[enrollment_id] = contracted_count
        if student_id in target_student_ids:
            target_enrollment_ids.add(enrollment_id)
        elif is_regular_continuation_subject(conn, student_id, subject_id, as_of_date):
            fixed_regular_ids.add(enrollment_id)
        else:
            limited_subject_ids.add(enrollment_id)

    def fixed_for(enrollment_ids: set[int]) -> dict[int, list[tuple[int, int]]]:
        return {enrollment_id: list(current.get(enrollment_id, [])) for enrollment_id in enrollment_ids}

    def required_for_fixed(enrollment_ids: set[int]) -> dict[int, int]:
        return {enrollment_id: len(current.get(enrollment_id, [])) for enrollment_id in enrollment_ids}

    all_non_target_ids = fixed_regular_ids | limited_subject_ids
    stage1_fixed = fixed_for(all_non_target_ids)
    stage1_required = required_for_fixed(all_non_target_ids)
    stage1_required.update({eid: contracted_counts[eid] for eid in target_enrollment_ids})

    started_at = time.monotonic()
    first_stage_limit = max(0.1, time_limit_seconds / 2)
    result = _solve_camp_core_once(
        conn,
        camp_id,
        first_stage_limit,
        fixed_assignments=stage1_fixed,
        required_counts=stage1_required,
    )
    feasible_statuses = {"OPTIMAL", "FEASIBLE"}
    relaxation_used = False

    if result["status"] not in feasible_statuses:
        relaxation_used = True
        elapsed = time.monotonic() - started_at
        second_stage_limit = max(0.1, time_limit_seconds - elapsed)
        stage2_required = required_for_fixed(fixed_regular_ids)
        stage2_required.update({eid: contracted_counts[eid] for eid in target_enrollment_ids})
        # 講習会限定科目は枠・講師を動かせるが、現行の割当コマ数は減らさない。
        stage2_required.update(required_for_fixed(limited_subject_ids))
        result = _solve_camp_core_once(
            conn,
            camp_id,
            second_stage_limit,
            fixed_assignments=fixed_for(fixed_regular_ids),
            required_counts=stage2_required,
        )

    changed_ids = {
        enrollment_id
        for enrollment_id in contracted_counts
        if set(current.get(enrollment_id, []))
        != set(result.get("assignments", {}).get(enrollment_id, []))
    } if result["status"] in feasible_statuses else set()

    result.update(
        {
            "partial_recalculation": True,
            "target_student_ids": sorted(target_student_ids),
            "relaxation_used": relaxation_used,
            "fixed_regular_enrollment_ids": sorted(fixed_regular_ids),
            "released_limited_enrollment_ids": sorted(limited_subject_ids) if relaxation_used else [],
            "changed_enrollment_ids": sorted(changed_ids),
            "unchanged_enrollment_ids": sorted(set(contracted_counts) - changed_ids),
            "moved_non_target_limited_enrollment_ids": sorted(changed_ids & limited_subject_ids),
            "previous_assignments": current,
        }
    )
    return result


# ---------------------------------------------------------
# Step 4: 計算結果をSESSIONS/ASSIGNMENTSへ書き込む
# ---------------------------------------------------------

def clear_camp_sessions(conn: sqlite3.Connection, camp_id: int) -> tuple[int, int]:
    """
    その講習会の既存SESSIONS/ASSIGNMENTSを削除する。
    スケジューラーを再実行したときに、古い結果と新しい結果が混ざらないようにするため。
    戻り値: (削除したASSIGNMENTS件数, 削除したSESSIONS件数)
    """
    session_ids = [
        row[0] for row in conn.execute("SELECT session_id FROM SESSIONS WHERE camp_id = ?", (camp_id,)).fetchall()
    ]
    n_assignments = 0
    if session_ids:
        placeholders = ",".join("?" * len(session_ids))
        n_assignments = conn.execute(
            f"SELECT COUNT(*) FROM ASSIGNMENTS WHERE session_id IN ({placeholders})", session_ids
        ).fetchone()[0]
        conn.execute(f"DELETE FROM ASSIGNMENTS WHERE session_id IN ({placeholders})", session_ids)
    n_sessions = conn.execute("DELETE FROM SESSIONS WHERE camp_id = ?", (camp_id,)).rowcount
    conn.commit()
    return n_assignments, n_sessions


def write_schedule_to_db(conn: sqlite3.Connection, camp_id: int, result: dict) -> dict:
    """
    solve_camp_core()の戻り値(result)を、実際にSESSIONS/ASSIGNMENTSへ書き込む。
    同じ(instructor_id, slot_id)の組み合わせに複数の生徒が割り当てられている場合
    (1:2形式)、SESSIONSは1行だけ作り、ASSIGNMENTSを複数行作る。

    戻り値: {"n_sessions": 作成したSESSIONS件数, "n_assignments": 作成したASSIGNMENTS件数}
    """
    clear_camp_sessions(conn, camp_id)

    # enrollment_id -> (student_id, subject_id) の対応を先に引いておく
    enrollment_lookup = {
        row[0]: (row[1], row[2])
        for row in conn.execute(
            "SELECT enrollment_id, student_id, subject_id FROM CAMP_COURSE_ENROLLMENTS WHERE camp_id = ?",
            (camp_id,),
        ).fetchall()
    }

    # (instructor_id, slot_id) -> session_id のキャッシュ(同じ枠を2人目が使うときに使い回すため)
    session_id_cache: dict[tuple[int, int], int] = {}
    n_sessions = 0
    n_assignments = 0

    for enrollment_id, assigned_slots in result.get("assignments", {}).items():
        if enrollment_id not in enrollment_lookup:
            continue
        student_id, subject_id = enrollment_lookup[enrollment_id]

        for instructor_id, slot_id in assigned_slots:
            key = (instructor_id, slot_id)
            if key not in session_id_cache:
                cur = conn.execute(
                    "INSERT INTO SESSIONS (camp_id, slot_id, instructor_id) VALUES (?, ?, ?)",
                    (camp_id, slot_id, instructor_id),
                )
                session_id_cache[key] = cur.lastrowid
                n_sessions += 1
            session_id = session_id_cache[key]

            conn.execute(
                "INSERT INTO ASSIGNMENTS (session_id, student_id, subject_id) VALUES (?, ?, ?)",
                (session_id, student_id, subject_id),
            )
            n_assignments += 1

    conn.commit()
    return {"n_sessions": n_sessions, "n_assignments": n_assignments}


def run_scheduler_for_camp(
    conn: sqlite3.Connection,
    camp_id: int,
    time_limit_seconds: float = 7200.0,
    target_student_ids: set[int] | None = None,
) -> dict:
    """
    講習会1つ分について、解く→DBに書き込む、までを一気に行う一番外側の関数。
    戻り値には、解いた結果のサマリと、書き込んだ件数の両方を含む。
    """
    result = solve_camp_core(
        conn,
        camp_id,
        time_limit_seconds=time_limit_seconds,
        target_student_ids=target_student_ids,
    )
    feasible = result["status"] in {"OPTIMAL", "FEASIBLE"}

    # 部分再計算に失敗した場合は、現行時間割を消さずそのまま残す。
    if target_student_ids is not None and not feasible:
        n_sessions = conn.execute(
            "SELECT COUNT(*) FROM SESSIONS WHERE camp_id = ?", (camp_id,)
        ).fetchone()[0]
        n_assignments = conn.execute(
            """SELECT COUNT(*) FROM ASSIGNMENTS a
               JOIN SESSIONS s ON s.session_id = a.session_id
               WHERE s.camp_id = ?""",
            (camp_id,),
        ).fetchone()[0]
        write_summary = {"n_sessions": n_sessions, "n_assignments": n_assignments}
    else:
        write_summary = write_schedule_to_db(conn, camp_id, result)

    summary = {
        "status": result["status"],
        "unfulfilled": result["unfulfilled"],
        "n_sessions": write_summary["n_sessions"],
        "n_assignments": write_summary["n_assignments"],
    }
    if target_student_ids is not None:
        changed_ids = result.get("changed_enrollment_ids", [])
        instructor_names = (
            {
                row[0]: f"{row[1]} {row[2]}"
                for row in conn.execute(
                    "SELECT instructor_id, last_name, first_name FROM INSTRUCTORS"
                ).fetchall()
            }
            if changed_ids
            else {}
        )
        slot_labels = (
            {
                row[0]: f"{row[1]} {row[2]}限"
                for row in conn.execute(
                    "SELECT slot_id, session_date, period_number FROM TIME_SLOTS"
                ).fetchall()
            }
            if changed_ids
            else {}
        )

        def describe_pairs(pairs: list[tuple[int, int]]) -> str:
            if not pairs:
                return "割当なし"
            return " / ".join(
                f"{slot_labels.get(slot_id, f'枠ID={slot_id}')}・"
                f"{instructor_names.get(instructor_id, f'講師ID={instructor_id}')}"
                for instructor_id, slot_id in sorted(pairs, key=lambda pair: pair[1])
            )

        enrollment_details = {
            row[0]: {
                "student_id": row[1],
                "student_name": row[2],
                "subject_name": row[3],
            }
            for row in conn.execute(
                """SELECT e.enrollment_id, e.student_id,
                          st.last_name || ' ' || st.first_name,
                          su.subject_name
                   FROM CAMP_COURSE_ENROLLMENTS e
                   JOIN STUDENTS st ON st.student_id = e.student_id
                   JOIN SUBJECTS su ON su.subject_id = e.subject_id
                   WHERE e.camp_id = ?""",
                (camp_id,),
            ).fetchall()
        }
        changed_assignments = []
        for enrollment_id in changed_ids:
            detail = enrollment_details.get(enrollment_id, {})
            before = result.get("previous_assignments", {}).get(enrollment_id, [])
            after = result.get("assignments", {}).get(enrollment_id, [])
            changed_assignments.append(
                {
                    "enrollment_id": enrollment_id,
                    **detail,
                    "before": before,
                    "after": after,
                    "before_display": describe_pairs(before),
                    "after_display": describe_pairs(after),
                    "is_non_target_limited": enrollment_id
                    in result.get("moved_non_target_limited_enrollment_ids", []),
                }
            )
        summary.update(
            {
                "partial_recalculation": True,
                "target_student_ids": result.get("target_student_ids", []),
                "relaxation_used": result.get("relaxation_used", False),
                "changed_assignments": changed_assignments,
                "n_changed_enrollments": len(changed_ids),
                "n_unchanged_enrollments": len(result.get("unchanged_enrollment_ids", [])),
                "moved_non_target_limited_enrollment_ids": result.get(
                    "moved_non_target_limited_enrollment_ids", []
                ),
                "schedule_preserved": not feasible,
            }
        )
    return summary
