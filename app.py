import json
import os
import random
import re
import hashlib
import secrets
import uuid
import base64
from datetime import date, datetime, timedelta
from fractions import Fraction
from html import escape
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, quote_plus, urlparse

from flask import Flask, jsonify, redirect, request, send_from_directory, session, url_for, get_flashed_messages
from flask_sqlalchemy import SQLAlchemy
from openai import OpenAI
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from email_utils import send_password_reset_email, send_welcome_email

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


UPLOAD_DIR = os.path.join(app.root_path, "static", "images", "questions")
UPLOAD_URL_PREFIX = "/static/images/questions/"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_IMAGE_UPLOAD_SIZE = 2 * 1024 * 1024


def save_question_image_upload(file_storage) -> tuple[str | None, str | None]:
    if not file_storage or not file_storage.filename:
        return None, None

    original_name = secure_filename(file_storage.filename)
    if not original_name:
        return None, "Invalid image filename."

    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return None, "Invalid file type. Allowed: png, jpg, jpeg, webp."

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_IMAGE_UPLOAD_SIZE:
        return None, "Image size must be 2MB or less."

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    timestamp = int(datetime.utcnow().timestamp())
    random_part = random.randint(100000, 999999)
    filename = secure_filename(f"question_{timestamp}_{random_part}.{ext}")
    file_storage.save(os.path.join(UPLOAD_DIR, filename))
    return f"{UPLOAD_URL_PREFIX}{filename}", None


SUPPORTED_MEDIA = {"English", "Sinhala"}

VALID_GRADES = [str(n) for n in range(1, 11)] + ["OL", "AL"]
GRADE_LABELS_EN = {"OL": "O/L", "AL": "A/L"}
GRADE_LABELS_SI = {"OL": "සාමාන්‍ය පෙළ", "AL": "උසස් පෙළ"}


def normalize_grade(value: str | None) -> str:
    raw = (value or "").strip().upper().replace(" ", "")
    if raw in {"O/L", "O-L", "OL"}:
        return "OL"
    if raw in {"A/L", "A-L", "AL"}:
        return "AL"
    return raw


def is_valid_grade(value: str | None) -> bool:
    return normalize_grade(value) in VALID_GRADES


def display_grade(value: str | None, medium: str = "English") -> str:
    grade = normalize_grade(value)
    if medium == "Sinhala":
        return GRADE_LABELS_SI.get(grade, grade)
    return GRADE_LABELS_EN.get(grade, grade)


def grade_options_html(selected: str = "") -> str:
    selected_grade = normalize_grade(selected)
    options = []
    for grade in VALID_GRADES:
        label = GRADE_LABELS_EN.get(grade, grade)
        sel = " selected" if grade == selected_grade else ""
        options.append(f"<option value=\"{grade}\"{sel}>{label}</option>")
    return "".join(options)

UI_TEXT = {
    "English": {
        "student_registration": "Student Registration",
        "name": "Name",
        "grade": "Grade",
        "medium": "Medium",
        "email": "Email",
        "mobile": "Mobile",
        "register": "Register",
        "language": "Language",
        "change_language": "Change Language",
        "selected_language": "Selected language",
        "submit": "Submit",
        "no_questions": "No SkillScan questions available for Grade {grade} yet.",
        "test_title": "SkillScan Test - Grade {grade} {subject}",
        "result_title": "SkillScan Test Result",
        "total_questions": "Total questions",
        "correct_answers": "Correct answers",
        "percentage_score": "Percentage score",
        "level": "Level",
        "xp": "XP",
        "xp_sinhala": "ලකුණු",
        "progress_to_next_level": "Progress to next level",
        "wrong_answers": "Wrong Answers",
        "question": "Question",
        "student_answer": "Student answer",
        "correct_answer": "Correct answer",
        "explanation": "Explanation",
        "try_again": "Try Again",
        "not_answered": "Not answered",
        "excellent_no_wrong": "Excellent! No wrong answers.",
        "topic_analysis": "Topic Analysis",
        "topic": "Topic",
        "classification": "Classification",
        "recommended_next_steps": "Recommended Next Steps",
        "practice_title": "Practice Mode",
        "practice_score": "Score",
        "back_to_dashboard": "Back to Dashboard",
        "topic_name": "Topic",
        "difficulty_label": "Difficulty",
        "retest_weak_topics": "Retest Weak Topics",
        "no_weak_topics_retest": "No weak topics to retest",
    },
    "Sinhala": {
        "student_registration": "ශිෂ්‍ය ලියාපදිංචිය",
        "name": "නම",
        "grade": "ශ්‍රේණිය",
        "medium": "මාධ්‍යය",
        "email": "ඊමේල්",
        "mobile": "ජංගම දුරකථන",
        "register": "ලියාපදිංචි කරන්න",
        "language": "භාෂාව",
        "change_language": "භාෂාව මාරු කරන්න",
        "selected_language": "තෝරාගත් භාෂාව",
        "submit": "යවන්න",
        "no_questions": "{grade} ශ්‍රේණිය සඳහා SkillScan ප්‍රශ්න තවම නොමැත.",
        "test_title": "SkillScan පරීක්ෂණය - {grade} ශ්‍රේණිය {subject}",
        "result_title": "SkillScan පරීක්ෂණ ප්‍රතිඵලය",
        "total_questions": "මුළු ප්‍රශ්න ගණන",
        "correct_answers": "නිවැරදි පිළිතුරු",
        "percentage_score": "ප්‍රතිශත ලකුණු",
        "level": "මට්ටම",
        "xp": "XP",
        "xp_sinhala": "ලකුණු",
        "progress_to_next_level": "ඊළඟ මට්ටමට ප්‍රගතිය",
        "wrong_answers": "වැරදි පිළිතුරු",
        "question": "ප්‍රශ්නය",
        "student_answer": "ඔබේ පිළිතුර",
        "correct_answer": "නිවැරදි පිළිතුර",
        "explanation": "විස්තරය",
        "try_again": "නැවත උත්සාහ කරන්න",
        "not_answered": "පිළිතුර ලබා නැත",
        "excellent_no_wrong": "ඉතා හොඳයි! වැරදි පිළිතුරු නොමැත.",
        "topic_analysis": "මාතෘකා විශ්ලේෂණය",
        "topic": "මාතෘකාව",
        "classification": "වර්ගීකරණය",
        "recommended_next_steps": "ඊළඟ පියවර නිර්දේශ",
        "practice_title": "අභ්‍යාස මාදිලිය",
        "practice_score": "ලකුණ",
        "back_to_dashboard": "ඩෑෂ්බෝඩ් වෙත ආපසු",
        "topic_name": "මාතෘකාව",
        "difficulty_label": "අපහසුතා මට්ටම",
        "retest_weak_topics": "දුර්වල කොටස් නැවත පරීක්ෂා කරන්න",
        "no_weak_topics_retest": "නැවත පරීක්ෂා කිරීමට දුර්වල කොටස් නොමැත",
    },
}


def resolve_medium(value: str | None, default: str = "English") -> str:
    medium = (value or default).strip()
    return medium if medium in SUPPORTED_MEDIA else default


def t(medium: str, key: str) -> str:
    return UI_TEXT[resolve_medium(medium)][key]


def is_short_answer_question(question: "Question") -> bool:
    return (question.question_type or "mcq").strip().lower() == "short_answer"

def is_box_input_question(question: "Question") -> bool:
    return (question.question_type or "mcq").strip().lower() == "box_input"


def extract_box_keys(template: str) -> list[str]:
    seen = []
    for key in re.findall(r"\[box(\d+)\]", template or "", flags=re.IGNORECASE):
        box_key = f"box{int(key)}"
        if box_key not in seen:
            seen.append(box_key)
    return seen


def parse_box_answers_json(raw: str) -> tuple[dict[str, str], str | None]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}, "Correct Box Answers must be valid JSON."
    if not isinstance(payload, dict):
        return {}, "Correct Box Answers must be a JSON object."
    return {str(k).strip().lower(): str(v).strip() for k, v in payload.items()}, None


def render_box_template_with_inputs(question: "Question", input_prefix: str) -> str:
    template = escape(question.box_template or "")
    def repl(match):
        num = match.group(1)
        key = f"box{int(num)}"
        return f"<input type='text' name='{input_prefix}_{question.id}_{key}' class='box-input' inputmode='text' autocomplete='off'>"
    html = re.sub(r"\[box(\d+)\]", repl, template, flags=re.IGNORECASE)
    return f"<pre class='box-layout'>{html}</pre>"


def evaluate_box_question(question: "Question", form) -> tuple[bool, dict[str, str], dict[str, str]]:
    expected, _ = parse_box_answers_json(question.box_answers or "{}")
    student_answers = {}
    all_correct = True
    for key in extract_box_keys(question.box_template or ""):
        value = (form.get(f"qbox_{question.id}_{key}") or "").strip()
        student_answers[key] = value
        if value.casefold() != (expected.get(key, "").strip().casefold()):
            all_correct = False
    return all_correct and bool(expected), student_answers, expected


def is_matching_pairs_question(question: "Question") -> bool:
    return (question.question_type or "mcq").strip().lower() == "matching_pairs"


def parse_matching_items(raw: str) -> list[str]:
    lines = [line.strip() for line in (raw or "").splitlines() if line.strip()]
    return lines


def parse_matching_answers_json(raw: str) -> tuple[dict[str, str], str | None]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}, "Correct Matches JSON must be valid JSON."
    if not isinstance(payload, dict):
        return {}, "Correct Matches JSON must be a JSON object."
    return {str(k).strip(): str(v).strip() for k, v in payload.items()}, None


def evaluate_matching_pairs_question(question: "Question", form, medium_key: str) -> tuple[bool, dict[str, str], dict[str, str]]:
    left = json.loads(getattr(question, f"matching_left_{medium_key}") or "[]")
    answers = json.loads(getattr(question, f"matching_answers_{medium_key}") or "{}")
    student_answers = {}
    mapping_json = (form.get(f"qmatch_map_{question.id}") or "").strip()
    if mapping_json:
        try:
            payload = json.loads(mapping_json)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            student_answers = {str(k).strip(): str(v).strip() for k, v in payload.items()}
    if not student_answers:
        for i, left_item in enumerate(left):
            student_answers[str(left_item)] = (form.get(f"qmatch_{question.id}_{i}") or "").strip()
    all_correct = True
    for left_item in left:
        selected = student_answers.get(str(left_item), "").strip()
        if selected != (answers.get(str(left_item), "").strip()):
            all_correct = False
    return all_correct and bool(left) and bool(answers), student_answers, answers


def render_matching_pairs_inputs(question: "Question", medium_key: str) -> str:
    left = json.loads(getattr(question, f"matching_left_{medium_key}") or "[]")
    right = json.loads(getattr(question, f"matching_right_{medium_key}") or "[]")
    opts = "".join([f"<option value='{escape(item)}'>{escape(item)}</option>" for item in right])
    left_html = "".join(
        [f"<button type='button' class='mp-item mp-left-item' data-value='{escape(str(item))}'>{escape(str(item))}</button>" for item in left]
    )
    right_html = "".join(
        [f"<button type='button' class='mp-item mp-right-item' data-value='{escape(str(item))}'>{escape(str(item))}</button>" for item in right]
    )
    fallback_rows = []
    for i, left_item in enumerate(left):
        fallback_rows.append(
            f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:10px;align-items:center;margin:8px 0;'><div>{escape(str(left_item))}</div><select name='qmatch_{question.id}_{i}'><option value=''>-- select answer --</option>{opts}</select></div>"
        )
    return f"""
    <style>
      .matching-container {{max-width:700px;width:fit-content;margin:0 auto 0 20px;display:flex;justify-content:flex-start;}}
      .matching-pairs-board {{position:relative;border:1px solid #d9d9d9;border-radius:12px;background:#fffdf8;padding:18px;min-height:140px;display:flex;justify-content:flex-start;}}
      .matching-columns {{display:grid;grid-template-columns:220px 220px;gap:90px;justify-content:start;align-items:start;width:auto;position:relative;z-index:1;}}
      .matching-column,.mp-column {{display:flex;flex-direction:column;gap:12px;width:220px;}}
      .matching-item,.mp-item {{text-align:left;background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:8px 12px;min-height:42px;font-size:16px;line-height:1.25;touch-action:none;display:flex;align-items:center;}}
      .mp-lines {{position:absolute;inset:0;width:100%;height:100%;pointer-events:auto;}}
      .mp-item.selected {{background:#f4f4f4;}}
      .mp-item.connected {{background:#f7f7f7;border-color:#111;}}
      .matching-pairs-fallback {{display:none;}}
      @media (max-width: 1024px) and (min-width: 769px) {{
        .matching-columns {{gap:70px;}}
      }}
      @media (max-width: 768px) {{
        .matching-container {{max-width:100%;width:auto;margin:0 0 0 20px;padding-left:0;}}
        .matching-pairs-board {{padding:14px;}}
        .matching-columns {{grid-template-columns:1fr;gap:30px;width:100%;}}
        .matching-column,.mp-column {{width:100%;max-width:100%;}}
        .matching-item,.mp-item {{font-size:14px;}}
      }}
    </style>
    <div class='matching-pairs-widget matching-container' data-question-id='{question.id}'>
      <input type='hidden' name='qmatch_map_{question.id}' class='mp-mapping-input' value='{{}}'>
      <div class='matching-pairs-board'>
        <svg class='mp-lines' aria-hidden='true'></svg>
        <div class='matching-columns'>
          <div class='matching-column mp-column'>{left_html}</div>
          <div class='matching-column mp-column'>{right_html}</div>
        </div>
      </div>
      <div class='matching-pairs-fallback'>{"".join(fallback_rows)}</div>
    </div>
    <script>
    (function() {{
      if (window.__matchingPairsInitDone) return;
      window.__matchingPairsInitDone = true;
      const supportsInteractive = !!(window.SVGElement && document.createElementNS && (window.PointerEvent || ("ontouchstart" in window) || ("onmousedown" in window)));
      const getPoint = (event) => {{
        if (event.touches && event.touches[0]) return {{x:event.touches[0].clientX,y:event.touches[0].clientY}};
        if (event.changedTouches && event.changedTouches[0]) return {{x:event.changedTouches[0].clientX,y:event.changedTouches[0].clientY}};
        return {{x:event.clientX,y:event.clientY}};
      }};
      const widgets = Array.from(document.querySelectorAll(".matching-pairs-widget"));
      widgets.forEach((widget) => {{
        const board = widget.querySelector(".matching-pairs-board");
        const svg = widget.querySelector(".mp-lines");
        const fallback = widget.querySelector(".matching-pairs-fallback");
        if (!supportsInteractive || !board || !svg) {{
          fallback.style.display = "block";
          board.style.display = "none";
          return;
        }}
        fallback.style.display = "none";
        const leftItems = Array.from(widget.querySelectorAll(".mp-left-item"));
        const rightItems = Array.from(widget.querySelectorAll(".mp-right-item"));
        const hiddenInput = widget.querySelector(".mp-mapping-input");
        const mapLeftToRight = new Map();
        const mapRightToLeft = new Map();
        let activeLeft = null;
        let dragLine = null;
        const edgePoint = (el, fromLeft=true) => {{
          const r = el.getBoundingClientRect(); const b = board.getBoundingClientRect();
          return {{x:(fromLeft ? r.right : r.left)-b.left, y:r.top + (r.height/2)-b.top}};
        }};
        const saveMapping = () => {{ hiddenInput.value = JSON.stringify(Object.fromEntries(mapLeftToRight)); }};
        const redraw = () => {{
          const boardRect = board.getBoundingClientRect();
          svg.setAttribute("viewBox", `0 0 ${{boardRect.width}} ${{boardRect.height}}`);
          svg.innerHTML = "";
          mapLeftToRight.forEach((rightVal, leftVal) => {{
            const l = leftItems.find(i => i.dataset.value === leftVal); const r = rightItems.find(i => i.dataset.value === rightVal);
            if (!l || !r) return;
            const p1 = edgePoint(l, true); const p2 = edgePoint(r, false);
            const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
            const mid = (p1.x + p2.x) / 2;
            line.setAttribute("d", `M ${{p1.x}} ${{p1.y}} C ${{mid}} ${{p1.y}}, ${{mid}} ${{p2.y}}, ${{p2.x}} ${{p2.y}}`);
            line.setAttribute("stroke", "#111"); line.setAttribute("stroke-width", "2.5"); line.setAttribute("fill", "none"); line.dataset.left = leftVal;
            line.style.pointerEvents = "stroke";
            line.addEventListener("click", () => {{ const rv = mapLeftToRight.get(leftVal); mapLeftToRight.delete(leftVal); if (rv) mapRightToLeft.delete(rv); updateStates(); redraw(); saveMapping(); }});
            svg.appendChild(line);
          }});
          if (dragLine) svg.appendChild(dragLine);
        }};
        const updateStates = () => {{
          leftItems.forEach(i => i.classList.toggle("connected", mapLeftToRight.has(i.dataset.value)));
          rightItems.forEach(i => i.classList.toggle("connected", mapRightToLeft.has(i.dataset.value)));
        }};
        const beginDrag = (leftItem, event) => {{
          event.preventDefault();
          activeLeft = leftItem; leftItems.forEach(i => i.classList.remove("selected")); leftItem.classList.add("selected");
          dragLine = document.createElementNS("http://www.w3.org/2000/svg", "path");
          dragLine.setAttribute("stroke", "#111"); dragLine.setAttribute("stroke-width", "2.5"); dragLine.setAttribute("fill", "none");
          redraw();
        }};
        const moveDrag = (event) => {{
          if (!activeLeft || !dragLine) return;
          const from = edgePoint(activeLeft, true); const p = getPoint(event); const b = board.getBoundingClientRect();
          const to = {{x: p.x - b.left, y: p.y - b.top}}; const mid = (from.x + to.x)/2;
          dragLine.setAttribute("d", `M ${{from.x}} ${{from.y}} C ${{mid}} ${{from.y}}, ${{mid}} ${{to.y}}, ${{to.x}} ${{to.y}}`);
        }};
        const endDrag = (event) => {{
          if (!activeLeft) return;
          const pt = getPoint(event); const target = document.elementFromPoint(pt.x, pt.y);
          const rightTarget = target && target.closest ? target.closest(".mp-right-item") : null;
          if (rightTarget) {{
            const leftVal = activeLeft.dataset.value; const rightVal = rightTarget.dataset.value;
            const prevRight = mapLeftToRight.get(leftVal); if (prevRight) mapRightToLeft.delete(prevRight);
            const prevLeft = mapRightToLeft.get(rightVal); if (prevLeft) mapLeftToRight.delete(prevLeft);
            mapLeftToRight.set(leftVal, rightVal); mapRightToLeft.set(rightVal, leftVal); saveMapping();
          }}
          leftItems.forEach(i => i.classList.remove("selected"));
          activeLeft = null; dragLine = null; updateStates(); redraw();
        }};
        leftItems.forEach((leftItem) => {{
          leftItem.addEventListener("mousedown", (e) => beginDrag(leftItem, e));
          leftItem.addEventListener("touchstart", (e) => beginDrag(leftItem, e), {{passive:false}});
        }});
        document.addEventListener("mousemove", moveDrag, {{passive:false}});
        document.addEventListener("touchmove", moveDrag, {{passive:false}});
        document.addEventListener("mouseup", endDrag);
        document.addEventListener("touchend", endDrag);
        window.addEventListener("resize", redraw);
        updateStates(); redraw(); saveMapping();
      }});
    }})();

    """


def get_questions_for_homework(grade: str, subject: str, topic_en: str, topic_si: str, difficulty_level: int, chapter_id: int | None = None):
    effective_difficulty = db.func.coalesce(Question.difficulty_level, 1)
    filters = [Question.grade == grade, Question.subject == subject, effective_difficulty == difficulty_level]
    if chapter_id:
        filters.append(Question.chapter_id == chapter_id)
    else:
        filters.append(db.or_(Question.topic_en == topic_en, Question.topic_si == topic_si, Question.topic == topic_en))
    return Question.query.filter(*filters).order_by(Question.id.asc()).all()


def get_chapter_display_for_medium(item, medium: str) -> str:
    if medium == "Sinhala":
        return (getattr(item, "chapter_si", None) or getattr(item, "topic_si", None) or getattr(item, "topic_en", None) or "")
    return (getattr(item, "chapter_en", None) or getattr(item, "topic_en", None) or getattr(item, "topic", None) or "")


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    grade = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=True)
    parent_email = db.Column(db.String(120), nullable=True)
    mobile = db.Column(db.String(20), nullable=False)
    medium = db.Column(db.String(20), nullable=False, default="English")
    password_hash = db.Column(db.String(255), nullable=True)
    xp = db.Column(db.Integer, nullable=False, default=0)
    level = db.Column(db.Integer, nullable=False, default=1)
    current_streak = db.Column(db.Integer, nullable=False, default=0)
    longest_streak = db.Column(db.Integer, nullable=False, default=0)
    last_activity_date = db.Column(db.Date, nullable=True)
    is_premium = db.Column(db.Boolean, nullable=False, default=False)
    subscription_end_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    class_id = db.Column(db.Integer, nullable=True)
    school_id = db.Column(db.Integer, nullable=False)
    profile_image_url = db.Column(db.Text, nullable=True)


def generate_student_username_for_id(student_id: int) -> str:
    year = datetime.utcnow().year
    return f"SLIS{year}{student_id:05d}"


def ensure_student_username_schema() -> None:
    db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS username VARCHAR(50)"))
    db.session.execute(
        db.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_student_username_unique
            ON student(username)
            WHERE username IS NOT NULL
            """
        )
    )
    db.session.execute(
        db.text(
            """
            UPDATE student
            SET username = 'SLIS' || TO_CHAR(CURRENT_DATE, 'YYYY') || LPAD(id::text, 5, '0')
            WHERE username IS NULL
            """
        )
    )
    db.session.commit()


def ensure_family_registration_schema() -> None:
    db.session.execute(db.text("ALTER TABLE student DROP CONSTRAINT IF EXISTS student_mobile_key"))
    db.session.execute(db.text("DROP INDEX IF EXISTS student_mobile_key"))
    db.session.execute(db.text("ALTER TABLE student DROP CONSTRAINT IF EXISTS student_parent_email_key"))
    db.session.execute(db.text("DROP INDEX IF EXISTS student_parent_email_key"))
    db.session.commit()


def run_startup_migrations() -> None:
    """Apply safe, idempotent schema/data migrations required at runtime."""
    db.create_all()
    db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS profile_image_url TEXT"))
    db.session.execute(db.text("ALTER TABLE syllabus_module ADD COLUMN IF NOT EXISTS image_si_url TEXT"))
    db.session.execute(db.text("ALTER TABLE syllabus_module ADD COLUMN IF NOT EXISTS image_en_url TEXT"))
    db.session.commit()
    ensure_student_username_schema()
    ensure_family_registration_schema()


def student_initials(name: str | None) -> str:
    parts = [part for part in (name or "").strip().split() if part]
    if not parts:
        return "ST"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return f"{parts[0][0]}{parts[1][0]}".upper()


LESSON_IMAGE_BUCKET = (os.environ.get("SUPABASE_BUCKET") or "lesson-images").strip() or "lesson-images"
MAX_LESSON_IMAGE_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_ACTIVITY_IMAGE_UPLOAD_SIZE = 1 * 1024 * 1024
IMAGE_CONTENT_TYPE_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


def upload_lesson_image_to_supabase(lesson_id: int, slide_ref: int | str, file_storage) -> tuple[str | None, str | None]:
    if not file_storage or not file_storage.filename:
        return None, None

    original_name = secure_filename(file_storage.filename)
    if not original_name:
        return None, "Invalid image filename."

    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    if ext not in IMAGE_CONTENT_TYPE_BY_EXT:
        return None, "Invalid file type. Allowed: png, jpg, jpeg, webp."

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_LESSON_IMAGE_UPLOAD_SIZE:
        return None, "Each lesson image must be 5MB or less."

    content_type = IMAGE_CONTENT_TYPE_BY_EXT[ext]
    uploaded_content_type = (file_storage.mimetype or "").lower()
    if uploaded_content_type and uploaded_content_type not in set(IMAGE_CONTENT_TYPE_BY_EXT.values()):
        return None, "Invalid image MIME type. Allowed: PNG, JPG, JPEG, and WebP."

    supabase_url = (os.environ.get("SUPABASE_URL") or "").strip()
    supabase_key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not supabase_url or not supabase_key:
        return None, "Supabase storage is not configured."

    safe_slide_ref = secure_filename(str(slide_ref)) or "new"
    object_filename = secure_filename(
        f"{uuid.uuid4().hex}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{ext}"
    )
    object_name = f"lesson-{lesson_id}/slide-{safe_slide_ref}/{object_filename}"

    image_bytes = file_storage.read()
    file_storage.stream.seek(0)
    upload_url = f"{supabase_url.rstrip('/')}/storage/v1/object/{LESSON_IMAGE_BUCKET}/{object_name}"
    req = Request(
        upload_url,
        data=image_bytes,
        method="POST",
        headers={
            "Authorization": f"Bearer {supabase_key}",
            "apikey": supabase_key,
            "Content-Type": content_type,
            "x-upsert": "true",
        },
    )
    try:
        urlopen(req, timeout=30).read()
    except (HTTPError, URLError, TimeoutError) as exc:
        return None, f"Failed to upload lesson image: {exc}"
    public_url = f"{supabase_url.rstrip('/')}/storage/v1/object/public/{LESSON_IMAGE_BUCKET}/{object_name}"
    return public_url, None



def upload_activity_image_to_supabase(lesson_id: int, slide_ref: int | str | None, file_storage) -> tuple[str | None, str | None, str | None]:
    if not file_storage or not file_storage.filename:
        return None, None, "Missing image file."

    original_name = secure_filename(file_storage.filename)
    if not original_name:
        return None, None, "Invalid image filename."

    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    if ext not in IMAGE_CONTENT_TYPE_BY_EXT:
        return None, None, "Invalid file type. Upload png, jpg, jpeg, or webp images only."

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_ACTIVITY_IMAGE_UPLOAD_SIZE:
        return None, None, "Activity images must be 1MB or less."

    uploaded_content_type = (file_storage.mimetype or "").lower()
    allowed_mimes = set(IMAGE_CONTENT_TYPE_BY_EXT.values())
    if uploaded_content_type and uploaded_content_type not in allowed_mimes:
        return None, None, "Invalid image MIME type. Upload PNG, JPG, JPEG, or WebP images only."

    supabase_url = (os.environ.get("SUPABASE_URL") or "").strip()
    supabase_key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    bucket_name = (os.environ.get("SUPABASE_BUCKET") or LESSON_IMAGE_BUCKET or "lesson-images").strip() or "lesson-images"
    if not supabase_url or not supabase_key:
        return None, None, "Supabase storage is not configured."

    safe_slide_ref = secure_filename(str(slide_ref or "temp")) or "temp"
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    object_filename = secure_filename(f"{timestamp}-{uuid.uuid4().hex[:10]}-{original_name}")
    object_name = f"lesson-{lesson_id}/slide-{safe_slide_ref}/{object_filename}"

    image_bytes = file_storage.read()
    file_storage.stream.seek(0)
    upload_url = f"{supabase_url.rstrip('/')}/storage/v1/object/{bucket_name}/{object_name}"
    req = Request(
        upload_url,
        data=image_bytes,
        method="POST",
        headers={
            "Authorization": f"Bearer {supabase_key}",
            "apikey": supabase_key,
            "Content-Type": IMAGE_CONTENT_TYPE_BY_EXT[ext],
            "x-upsert": "false",
        },
    )
    try:
        urlopen(req, timeout=30).read()
    except (HTTPError, URLError, TimeoutError) as exc:
        return None, object_name, f"Failed to upload activity image: {exc}"
    public_url = f"{supabase_url.rstrip('/')}/storage/v1/object/public/{bucket_name}/{object_name}"
    return public_url, object_name, None


def parse_tap_correct_picture_activity(activity_json: str | dict | None) -> dict:
    if not activity_json:
        return {}
    if isinstance(activity_json, dict):
        payload = activity_json
    else:
        try:
            payload = json.loads(activity_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    if not isinstance(payload, dict):
        return {}
    activity_type = str(payload.get("activity_type") or payload.get("type") or "").strip().lower()
    if activity_type != "tap_correct_picture":
        return {}
    items = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        image_url = str(item.get("image_url") or "").strip()
        if not image_url:
            continue
        items.append({"image_url": image_url, "correct": bool(item.get("correct"))})
    return {
        "activity_type": "tap_correct_picture",
        "title": str(payload.get("title") or "").strip(),
        "instruction": str(payload.get("instruction") or "").strip(),
        "allow_multiple": True,
        "items": items,
        "success_message": str(payload.get("success_message") or "සුභ පැතුම්! ඔබ නිවැරදි පින්තූර තෝරා ඇත.").strip(),
        "wrong_message": str(payload.get("wrong_message") or "නැවත උත්සාහ කරන්න.").strip(),
    }


def build_tap_correct_picture_activity_json(title: str, instruction: str, items: list[dict], success_message: str | None = None, wrong_message: str | None = None) -> str:
    payload = {
        "activity_type": "tap_correct_picture",
        "title": (title or "").strip(),
        "instruction": (instruction or "").strip(),
        "allow_multiple": True,
        "items": [{"image_url": str(item.get("image_url") or "").strip(), "correct": bool(item.get("correct"))} for item in items if str(item.get("image_url") or "").strip()],
        "success_message": (success_message or "සුභ පැතුම්! ඔබ නිවැරදි පින්තූර තෝරා ඇත.").strip(),
        "wrong_message": (wrong_message or "නැවත උත්සාහ කරන්න.").strip(),
    }
    return json.dumps(payload, ensure_ascii=False)


def validate_tap_correct_picture_items(items: list[dict]) -> str | None:
    if len(items) < 2:
        return "Tap-correct-picture requires at least 2 images."
    if not any(bool(item.get("correct")) for item in items):
        return "Tap-correct-picture requires at least 1 correct image."
    return None

def parse_image_grid_activity(activity_json: str | dict | None) -> list[dict]:
    if not activity_json:
        return []
    if isinstance(activity_json, dict):
        payload = activity_json
    else:
        try:
            payload = json.loads(activity_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(payload, dict) or payload.get("type") != "image_grid":
        return []
    images = payload.get("images")
    if not isinstance(images, list):
        return []
    normalized = []
    for image in images:
        if not isinstance(image, dict):
            continue
        url = str(image.get("url") or "").strip()
        if not url:
            continue
        normalized.append({
            "url": url,
            "caption_en": str(image.get("caption_en") or "").strip(),
            "caption_si": str(image.get("caption_si") or "").strip(),
        })
    return normalized


def build_image_grid_activity_json(images: list[dict]) -> str:
    normalized = []
    for image in images:
        url = str(image.get("url") or "").strip()
        if not url:
            continue
        normalized.append({
            "url": url,
            "caption_en": str(image.get("caption_en") or "").strip(),
            "caption_si": str(image.get("caption_si") or "").strip(),
        })
    return json.dumps({"type": "image_grid", "images": normalized}, ensure_ascii=False)


def upload_profile_image_to_supabase(student_id: int, image_bytes: bytes, content_type: str) -> tuple[str | None, str | None]:
    supabase_url = (os.environ.get("SUPABASE_URL") or "").strip()
    supabase_key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    bucket_name = (os.environ.get("SUPABASE_BUCKET") or "student-profile-images").strip()
    if not supabase_url or not supabase_key:
        return None, "Supabase storage is not configured."
    ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    ext = ext_map.get(content_type, "webp")
    object_name = secure_filename(f"student_{student_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{ext}")
    upload_url = f"{supabase_url.rstrip('/')}/storage/v1/object/{bucket_name}/{object_name}"
    req = Request(
        upload_url,
        data=image_bytes,
        method="POST",
        headers={
            "Authorization": f"Bearer {supabase_key}",
            "apikey": supabase_key,
            "Content-Type": content_type,
            "x-upsert": "true",
        },
    )
    try:
        urlopen(req, timeout=20).read()
    except (HTTPError, URLError, TimeoutError) as exc:
        return None, f"Failed to upload profile image: {exc}"
    public_url = f"{supabase_url.rstrip('/')}/storage/v1/object/public/{bucket_name}/{object_name}"
    return public_url, None


def upload_subject_image_to_supabase(subject_id: int | None, medium_key: str, image_bytes: bytes, content_type: str) -> tuple[str | None, str | None]:
    supabase_url = (os.environ.get("SUPABASE_URL") or "").strip()
    supabase_key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    bucket_name = "subject-images"
    if not supabase_url or not supabase_key:
        return None, "Supabase storage is not configured."
    ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    ext = ext_map.get(content_type, "webp")
    subject_ref = subject_id if subject_id else "new"
    object_name = secure_filename(
        f"subject_{subject_ref}_{medium_key}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{ext}"
    )
    upload_url = f"{supabase_url.rstrip('/')}/storage/v1/object/{bucket_name}/{object_name}"
    req = Request(
        upload_url,
        data=image_bytes,
        method="POST",
        headers={
            "Authorization": f"Bearer {supabase_key}",
            "apikey": supabase_key,
            "Content-Type": content_type,
            "x-upsert": "true",
        },
    )
    try:
        urlopen(req, timeout=20).read()
    except (HTTPError, URLError, TimeoutError) as exc:
        return None, f"Failed to upload subject image: {exc}"
    public_url = f"{supabase_url.rstrip('/')}/storage/v1/object/public/{bucket_name}/{object_name}"
    return public_url, None


def upload_module_image_to_supabase(module_id: int | None, medium_key: str, image_bytes: bytes, content_type: str) -> tuple[str | None, str | None]:
    supabase_url = (os.environ.get("SUPABASE_URL") or "").strip()
    supabase_key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    bucket_name = "module-images"
    if not supabase_url or not supabase_key:
        return None, "Supabase storage is not configured."
    ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    ext = ext_map.get(content_type, "webp")
    module_ref = module_id if module_id else "new"
    object_name = secure_filename(f"module_{module_ref}_{medium_key}.{ext}")
    upload_url = f"{supabase_url.rstrip('/')}/storage/v1/object/{bucket_name}/{object_name}"
    req = Request(upload_url, data=image_bytes, method="POST", headers={
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key,
        "Content-Type": content_type,
        "x-upsert": "true",
    })
    try:
        urlopen(req, timeout=20).read()
    except (HTTPError, URLError, TimeoutError) as exc:
        return None, f"Failed to upload module image: {exc}"
    public_url = f"{supabase_url.rstrip('/')}/storage/v1/object/public/{bucket_name}/{object_name}"
    return public_url, None


class School(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_name = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Teacher(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    school_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class SchoolAdmin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    role = db.Column(db.String(20), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Class(db.Model):
    __tablename__ = "class"

    id = db.Column(db.Integer, primary_key=True)
    class_name = db.Column(db.String(120), nullable=False)
    grade = db.Column(db.String(20), nullable=False)
    teacher_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)




def is_tap_select_image_question(question: "Question") -> bool:
    return (question.question_type or "mcq").strip().lower() == "tap_select_image"


def is_drag_drop_group_container_question(question: "Question") -> bool:
    return (question.question_type or "mcq").strip().lower() == "drag_drop_group_container"


def normalize_local_image_url(url: str | None) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://", "//", "data:", "/")):
        return value
    return "/" + value


def parse_drag_items_json(raw: str) -> tuple[list[dict], str | None]:
    try:
        payload = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return [], "Drag Items JSON must be valid JSON."
    if not isinstance(payload, list) or not payload:
        return [], "Drag Items JSON must be a non-empty JSON array."
    normalized = []
    for item in payload:
        if not isinstance(item, dict):
            return [], "Each drag item must be an object."
        item_id = str(item.get("id") or "").strip()
        group = str(item.get("group") or "").strip()
        if not item_id or not group:
            return [], "Each drag item must include id and group."
        normalized.append({
            "id": item_id,
            "group": group,
            "label_en": str(item.get("label_en") or "").strip(),
            "label_si": str(item.get("label_si") or "").strip(),
            "image_url": str(item.get("image_url") or "").strip(),
        })
    return normalized, None


def parse_tap_areas_json(raw: str) -> tuple[list[dict], str | None]:
    try:
        payload = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return [], "Selectable Areas JSON must be valid JSON."
    if not isinstance(payload, list) or not payload:
        return [], "Selectable Areas JSON must be a non-empty JSON array."
    normalized = []
    for item in payload:
        if not isinstance(item, dict):
            return [], "Each selectable area must be an object."
        area_id = str(item.get("id") or "").strip()
        if not area_id:
            return [], "Each selectable area must include a non-empty id."
        try:
            x = float(item.get("x")); y = float(item.get("y")); w = float(item.get("width")); h = float(item.get("height"))
        except (TypeError, ValueError):
            return [], "Each area must include numeric x, y, width, and height."
        if w <= 0 or h <= 0:
            return [], "Each area width/height must be greater than 0."
        normalized.append({"id": area_id, "x": x, "y": y, "width": w, "height": h})
    return normalized, None


def render_tap_select_image_input(question: "Question", input_prefix: str = "q") -> str:
    areas = json.loads(question.tap_areas_json or "[]")
    areas_json = escape(json.dumps(areas, ensure_ascii=False))
    selected_name = f"answer_{question.id}"
    empty_note = "<p class='tap-select-empty-msg'>Tap areas not configured yet.</p>" if not areas else ""
    return f"""
    <div class='tap-select-wrap' data-areas='{areas_json}' data-hidden-name='{selected_name}'>
      <img src='{escape(normalize_local_image_url(question.image_url or ""))}' alt='Tap select question image' class='tap-select-image'>
      <svg class='tap-select-overlay' viewBox='0 0 100 100' preserveAspectRatio='none'></svg>
      <input type='hidden' name='{selected_name}' value=''>
      {empty_note}
    </div>
    """


def tap_select_common_assets() -> str:
    return """
    <style>
      .tap-select-wrap { position: relative; display: inline-block; max-width: 420px; width: 100%; }
      .tap-select-image { width: 100%; height: auto; display: block; border: 1px solid #ddd; border-radius: 6px; }
      .tap-select-overlay { position: absolute; inset: 0; width: 100%; height: 100%; z-index: 2; }
      .tap-area { fill: transparent; opacity: 0; stroke: none; stroke-width: 0; cursor: pointer; pointer-events: all; }
      .tap-area.selected { fill: rgba(0, 255, 0, 0.25); opacity: 1; stroke: #22aa22; stroke-width: 2; }
      @media (hover: hover) and (pointer: fine) {
        .tap-area:hover { fill: rgba(0,0,0,0.05); opacity: 1; }
      }
      .tap-area.review-wrong { fill: rgba(239,68,68,0.35); stroke: rgba(220,38,38,0.8); }
      .tap-area.review-correct { fill: rgba(34,197,94,0.35); stroke: rgba(22,163,74,0.9); }
      .tap-select-empty-msg { margin-top: 8px; color: #b45309; font-size: 14px; }
    </style>
    <script>
      function initTapSelectUI(root=document) {
        root.querySelectorAll('.tap-select-wrap').forEach((wrap) => {
          const svg = wrap.querySelector('.tap-select-overlay');
          const hidden = wrap.querySelector("input[type='hidden']");
          if (!svg || !hidden) return;
          if (svg.dataset.ready === '1') return;
          svg.dataset.ready = '1';
          let areas = [];
          try { areas = JSON.parse(wrap.dataset.areas || "[]"); } catch(e) { areas = []; }
          if (!Array.isArray(areas) || !areas.length) return;
          const draw = (selectedId) => {
            svg.innerHTML = "";
            areas.forEach((area) => {
              const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
              rect.setAttribute("x", area.x); rect.setAttribute("y", area.y);
              rect.setAttribute("width", area.width); rect.setAttribute("height", area.height);
              rect.setAttribute("class", "tap-area" + (selectedId === area.id ? " selected" : ""));
              rect.dataset.areaId = area.id;
              const choose = (ev) => { ev.preventDefault(); hidden.value = area.id; draw(area.id); };
              rect.addEventListener("click", choose);
              rect.addEventListener("pointerdown", choose);
              svg.appendChild(rect);
            });
          };
          draw(hidden.value || "");
        });
      }
      document.addEventListener("DOMContentLoaded", () => initTapSelectUI(document));
    </script>
    """


def evaluate_tap_select_question(question: "Question", form) -> tuple[bool, str, str]:
    student_answer = (form.get(f"answer_{question.id}") or "").strip()
    correct_answer = (question.correct_area_id or "").strip()
    return bool(student_answer) and student_answer == correct_answer, student_answer, correct_answer


def render_tap_select_review(question: "Question", selected_area_id: str, correct_area_id: str) -> str:
    areas = json.loads(question.tap_areas_json or "[]")
    if not areas:
        return "<p>Tap areas not configured yet.</p>"
    rects = []
    for area in areas:
        cls = "tap-area"
        if area.get("id") == selected_area_id and selected_area_id != correct_area_id:
            cls += " review-wrong"
        if area.get("id") == correct_area_id:
            cls += " review-correct"
        rects.append(
            f"<rect class='{cls}' x='{area.get('x',0)}' y='{area.get('y',0)}' width='{area.get('width',0)}' height='{area.get('height',0)}'></rect>"
        )
    return f"""
    <div class='tap-select-wrap'>
      <img src='{escape(normalize_local_image_url(question.image_url or ""))}' alt='Tap select review image' class='tap-select-image'>
      <svg class='tap-select-overlay' viewBox='0 0 100 100' preserveAspectRatio='none'>{"".join(rects)}</svg>
    </div>
    """


def render_drag_drop_group_container_input(question: "Question", medium_key: str = "en") -> str:
    items = json.loads(question.drag_items_json or "[]")
    items_html = []
    for item in items:
        item_src = normalize_local_image_url(str(item.get("image_url", "")))
        raw_group = str(item.get('group', ''))
        safe_group = re.sub(r'[^a-z0-9-]+', '-', raw_group.strip().lower()).strip('-')
        class_suffix = f" dd-item-{safe_group}" if safe_group else ""
        items_html.append(
            f"<img class='dd-item{class_suffix}' data-id='{escape(str(item.get('id','')))}' "
            f"data-group='{escape(raw_group)}' src='{escape(item_src)}'>"
        )
    basket_src = normalize_local_image_url(question.drag_container_image_url or "")
    return f"""
    <div class='drag-drop-question' data-question-id='{question.id}'>
      <div class='drag-items-row'>{"".join(items_html)}</div>
      <div class='dd-drop-zone'>
        <img class='dd-basket' src='{escape(basket_src)}'>
      </div>
      <input type='hidden' name='answer_{question.id}' id='answer_{question.id}' value=''>
    </div>
    """


def drag_drop_group_assets() -> str:
    return """
    <style>
      .drag-items-row{display:flex !important;flex-direction:row !important;flex-wrap:wrap !important;align-items:flex-end !important;gap:18px !important;margin:12px 0 18px 0 !important;}
      .dd-item{width:168px !important;height:168px !important;min-width:168px !important;max-width:168px !important;min-height:168px !important;max-height:168px !important;object-fit:contain !important;flex:0 0 auto !important;display:inline-block !important;cursor:grab !important;touch-action:none !important;user-select:none !important;position:relative;left:auto;top:auto;}
      @media (max-width:768px){.dd-item{width:120px !important;height:120px !important;min-width:120px !important;max-width:120px !important;min-height:120px !important;max-height:120px !important;}}
      .dd-item-carrot{transform:scale(0.85);transform-origin:bottom center;}
      .dd-item-beet,.dd-item-beetroot,.dd-item-radish{transform:scale(1.00);transform-origin:bottom center;}
      .dd-item-pumpkin{transform:scale(1.25);transform-origin:bottom center;}
      .dd-drop-zone{position:relative !important;width:min(92vw,430px) !important;height:230px !important;border:2px dashed #aaa !important;border-radius:12px !important;overflow:hidden !important;}
      .dd-basket{position:absolute;inset:0;width:100% !important;height:100% !important;object-fit:contain !important;pointer-events:none !important;}
      .interactive-drag-drop-question{max-width:100%;overflow:hidden;}
      .interactive-drag-drop-question .drag-items-row{display:flex !important;flex-direction:row !important;flex-wrap:wrap !important;justify-content:center !important;align-items:flex-end !important;gap:10px !important;margin:8px 0 10px 0 !important;}
      .interactive-drag-drop-question .dd-item{width:72px !important;height:72px !important;min-width:72px !important;max-width:72px !important;min-height:72px !important;max-height:72px !important;}
      .interactive-drag-drop-question .dd-drop-zone{width:min(86vw,360px) !important;height:185px !important;margin:0 auto !important;}
      @media (max-width:768px){
        .interactive-drag-drop-question .dd-item{width:58px !important;height:58px !important;min-width:58px !important;max-width:58px !important;min-height:58px !important;max-height:58px !important;}
        .interactive-drag-drop-question .dd-drop-zone{width:min(84vw,320px) !important;height:165px !important;}
      }
    </style>
    <script>
    function initDragGroupUI(root=document){
      root.querySelectorAll('.drag-drop-question').forEach((wrap)=>{
        const questionId = wrap.dataset.questionId || '';
        const bank = wrap.querySelector('.drag-items-row');
        const dropZone = wrap.querySelector('.dd-drop-zone');
        const hidden = wrap.querySelector(`input[id='answer_${questionId}']`) || wrap.querySelector('#interactive_drag_answer') || wrap.querySelector('.drag-answer-json');
        if (!bank || !dropZone || !hidden) return;
        const clamp = (n,min,max)=>Math.min(Math.max(n,min),max);
        const itemSize = (el)=>{ const rect = el.getBoundingClientRect(); return Math.max(rect.width || 0, rect.height || 0, 56); };
        const save = ()=>{
          const placed = {};
          [...dropZone.querySelectorAll('.dd-item')].forEach((el)=>{
            const id = el.dataset.id || '';
            placed[id] = {id, group: el.dataset.group || '', x: parseFloat(el.style.left) || 0, y: parseFloat(el.style.top) || 0};
          });
          hidden.value = JSON.stringify(placed);
        };
        wrap.querySelectorAll('.dd-item').forEach((el)=>{
          if (el.dataset.ddBound === '1') return;
          el.dataset.ddBound = '1';
          el.addEventListener('pointerdown',(e)=>{
            if (e.button !== undefined && e.button !== 0) return;
            const rect = el.getBoundingClientRect();
            const state = {el, dx: e.clientX - rect.left, dy: e.clientY - rect.top, startWrap: wrap};
            el.style.position = 'fixed';
            el.style.left = rect.left + 'px';
            el.style.top = rect.top + 'px';
            el.style.zIndex = '99999';
            el.style.pointerEvents = 'none';
            document.body.appendChild(el);
            const move = (ev)=>{
              el.style.left = (ev.clientX - state.dx) + 'px';
              el.style.top = (ev.clientY - state.dy) + 'px';
            };
            const end = (ev)=>{
              document.removeEventListener('pointermove', move);
              document.removeEventListener('pointerup', end);
              document.removeEventListener('pointercancel', end);
              const zoneRect = dropZone.getBoundingClientRect();
              const inside = ev.clientX >= zoneRect.left && ev.clientX <= zoneRect.right && ev.clientY >= zoneRect.top && ev.clientY <= zoneRect.bottom;
              if (inside) {
                dropZone.appendChild(el);
                el.style.position = 'absolute';
                el.style.pointerEvents = '';
                const size = itemSize(el);
                el.style.left = clamp(ev.clientX - zoneRect.left - size / 2, 0, Math.max(0, zoneRect.width - size)) + 'px';
                el.style.top = clamp(ev.clientY - zoneRect.top - size / 2, 0, Math.max(0, zoneRect.height - size)) + 'px';
              } else {
                bank.appendChild(el);
                el.style.position = 'relative';
                el.style.left = '';
                el.style.top = '';
                el.style.pointerEvents = '';
              }
              el.style.zIndex = '';
              save();
            };
            document.addEventListener('pointermove', move);
            document.addEventListener('pointerup', end);
            document.addEventListener('pointercancel', end);
            e.preventDefault();
          });
        });
        save();
      });
    }
    document.addEventListener('DOMContentLoaded',()=>initDragGroupUI(document));
    window.initDragGroupUI = initDragGroupUI;
    </script>
    """


def evaluate_drag_drop_group_container_question(question: "Question", form) -> tuple[bool, str]:
    raw = (form.get(f"answer_{question.id}") or "").strip()
    try:
        placed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return False, raw
    required = json.loads(question.drag_items_json or "[]")
    placed_items = list(placed.values()) if isinstance(placed, dict) else placed
    if len(placed_items) < len(required):
        return False, raw
    group_points = {}
    for item in placed_items:
        g = str(item.get("group") or "")
        group_points.setdefault(g, []).append((float(item.get("x", 0)), float(item.get("y", 0))))
    threshold = 120.0
    for points in group_points.values():
        if len(points) < 2:
            continue
        ax = sum(p[0] for p in points) / len(points); ay = sum(p[1] for p in points) / len(points)
        avg = sum((((p[0]-ax)**2 + (p[1]-ay)**2) ** 0.5) for p in points) / len(points)
        if avg > threshold:
            return False, raw
    return True, raw



class SubjectMaster(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    grade = db.Column(db.String(20), nullable=False)
    subject_code = db.Column(db.String(50), nullable=False)
    subject_name_en = db.Column(db.String(150), nullable=False)
    subject_name_si = db.Column(db.String(150), nullable=False)
    image_si_url = db.Column(db.Text, nullable=True)
    image_en_url = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


def get_subjects_for_grade(grade: str | None, active_only: bool = True):
    normalized = normalize_grade(grade)
    query = SubjectMaster.query
    if normalized:
        query = query.filter_by(grade=normalized)
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(SubjectMaster.subject_name_en.asc()).all()


def subject_options_html(selected_grade: str = "", selected_subject: str = "", active_only: bool = True) -> str:
    subjects = get_subjects_for_grade(selected_grade, active_only=active_only)
    options = ["<option value=''>Select subject</option>"]
    for item in subjects:
        sel = " selected" if item.subject_name_en == (selected_subject or "") else ""
        options.append(f"<option value=\"{escape(item.subject_name_en)}\"{sel}>{escape(item.subject_name_en)} ({escape(item.subject_code)})</option>")
    return "".join(options)


def subject_options_html_by_id(selected_grade: str = "", selected_subject_id: str = "", active_only: bool = True) -> str:
    subjects = get_subjects_for_grade(selected_grade, active_only=active_only)
    options = ["<option value=''>Select subject</option>"]
    selected_value = (selected_subject_id or "").strip()
    for item in subjects:
        sel = " selected" if str(item.id) == selected_value else ""
        options.append(f"<option value=\"{item.id}\"{sel}>{escape(item.subject_name_en)} ({escape(item.subject_code)})</option>")
    return "".join(options)


def _syllabus_terms_for_grade_subject(grade: str | None, subject_id: str | None):
    normalized_grade = normalize_grade(grade)
    normalized_subject_id = (subject_id or "").strip()
    if not normalized_grade or not normalized_subject_id.isdigit():
        return []

    subject_obj = SubjectMaster.query.get(int(normalized_subject_id))
    if not subject_obj:
        app.logger.error(
            "TERMS LOOKUP DEBUG grade=%s subject_id=%s subject_keys=%s terms=%s",
            normalized_grade,
            normalized_subject_id,
            [],
            0,
        )
        return []

    subject_keys = [subject_obj.subject_code, subject_obj.subject_name_en, subject_obj.subject_name_si]
    lookup_subjects = [x for x in subject_keys if x]
    terms = (
        SyllabusTerm.query.filter(SyllabusTerm.grade == normalized_grade)
        .filter(SyllabusTerm.subject.in_(lookup_subjects))
        .order_by(SyllabusTerm.term_number.asc())
        .all()
    )
    app.logger.error(
        "TERMS LOOKUP DEBUG grade=%s subject_id=%s subject_keys=%s terms=%s",
        normalized_grade,
        normalized_subject_id,
        lookup_subjects,
        len(terms),
    )
    return terms


def dependent_dropdown_script(
    grade_selector: str = "select[name='grade']",
    subject_selector: str = "select[name='subject']",
    term_selector: str = "select[name='term_id']",
    module_selector: str = "select[name='module_id']",
    chapter_selector: str = "select[name='chapter_id']",
    debug_selector: str = "#syllabus-debug-message",
) -> str:
    return f"""
    <script>
      (function () {{
        const gradeEl = document.querySelector({json.dumps(grade_selector)});
        const subjectEl = document.querySelector({json.dumps(subject_selector)});
        const termEl = document.querySelector({json.dumps(term_selector)});
        const moduleEl = document.querySelector({json.dumps(module_selector)});
        const chapterEl = document.querySelector({json.dumps(chapter_selector)});
        const debugEl = document.querySelector({json.dumps(debug_selector)});
        if (!gradeEl || !subjectEl) return;
        const setOptions = (el, items, placeholder, selectedValue = "") => {{
          if (!el) return;
          const opts = [`<option value="">${{placeholder}}</option>`];
          for (const item of items) {{
            const selected = String(item.id) === String(selectedValue) ? " selected" : "";
            opts.push(`<option value="${{item.id}}"${{selected}}>${{item.label}}</option>`);
          }}
          el.innerHTML = opts.join("");
        }};
        const resetChain = (from) => {{
          if (from <= 1) setOptions(termEl, [], "Select term");
          if (from <= 2) setOptions(moduleEl, [], "Select module");
          if (from <= 3) setOptions(chapterEl, [], "Select chapter");
        }};
        const get = async (url) => (await fetch(url)).json();
        const loadSubjects = async () => {{
          const grade = gradeEl.value.trim();
          const subject = subjectEl.value.trim();
          if (!grade) return;
          const payload = await get(`/api/subjects?grade=${{encodeURIComponent(grade)}}`);
          const options = (payload.subjects || []).map(s => ({{ id: s.id, label: `${{s.subject_name_en}} (${{s.subject_code}})` }}));
          const selected = options.some(s => s.id === subject) ? subject : "";
          setOptions(subjectEl, options, "Select subject", selected);
        }};
        const loadTerms = async () => {{
          resetChain(1);
          if (debugEl) debugEl.textContent = "";
          const grade = gradeEl.value.trim();
          const subject = subjectEl.value.trim();
          if (!grade || !subject) return;
          const payload = await get(`/api/syllabus/terms?grade=${{encodeURIComponent(grade)}}&subject=${{encodeURIComponent(subject)}}`);
          setOptions(termEl, payload.terms || [], "Select term", termEl?.dataset.selected || "");
          if ((payload.terms || []).length === 0 && debugEl) {{
            debugEl.textContent = "No terms found for selected grade and subject";
          }}
        }};
        const loadModules = async () => {{
          resetChain(2);
          const termId = termEl?.value || "";
          if (!termId) return;
          const payload = await get(`/api/syllabus/modules?term_id=${{encodeURIComponent(termId)}}`);
          setOptions(moduleEl, payload.modules || [], "Select module", moduleEl?.dataset.selected || "");
        }};
        const loadChapters = async () => {{
          resetChain(3);
          const moduleId = moduleEl?.value || "";
          if (!moduleId) return;
          const payload = await get(`/api/syllabus/chapters?module_id=${{encodeURIComponent(moduleId)}}`);
          setOptions(chapterEl, payload.chapters || [], "Select chapter", chapterEl?.dataset.selected || "");
        }};
        gradeEl.addEventListener("change", async () => {{ await loadSubjects(); await loadTerms(); }});
        subjectEl.addEventListener("change", loadTerms);
        termEl?.addEventListener("change", loadModules);
        moduleEl?.addEventListener("change", loadChapters);
        (async () => {{
          await loadSubjects();
          await loadTerms();
          if (termEl?.value) await loadModules();
          if (moduleEl?.value) await loadChapters();
        }})();
      }})();
    </script>
    """

class SyllabusTerm(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    grade = db.Column(db.String(20), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    term_number = db.Column(db.Integer, nullable=False)
    term_name_en = db.Column(db.String(150), nullable=False)
    term_name_si = db.Column(db.String(150), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class SyllabusModule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    term_id = db.Column(db.Integer, nullable=False)
    module_order = db.Column(db.Integer, nullable=False)
    module_name_en = db.Column(db.String(150), nullable=False)
    module_name_si = db.Column(db.String(150), nullable=False)
    image_si_url = db.Column(db.Text, nullable=True)
    image_en_url = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class SyllabusChapter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    module_id = db.Column(db.Integer, nullable=False)
    chapter_order = db.Column(db.Integer, nullable=False)
    chapter_name_en = db.Column(db.String(150), nullable=False)
    chapter_name_si = db.Column(db.String(150), nullable=False)
    competency_levels = db.Column(db.String(255), nullable=True)
    estimated_periods = db.Column(db.Integer, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)




class Lesson(db.Model):
    __tablename__ = "lesson"

    id = db.Column(db.Integer, primary_key=True)
    chapter_id = db.Column(db.Integer, nullable=False)
    lesson_order = db.Column(db.Integer, nullable=False)
    lesson_title_en = db.Column(db.String(200), nullable=False)
    lesson_title_si = db.Column(db.String(200), nullable=False)
    lesson_type = db.Column(db.String(50), nullable=False, default="standard")
    thumbnail_url = db.Column(db.Text, nullable=True)
    estimated_minutes = db.Column(db.Integer, nullable=False, default=10)
    xp_reward = db.Column(db.Integer, nullable=False, default=10)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class LessonSlide(db.Model):
    __tablename__ = "lesson_slide"

    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.Integer, nullable=False)
    slide_order = db.Column(db.Integer, nullable=False)
    slide_type = db.Column(db.String(50), nullable=False, default="explanation")
    title_en = db.Column(db.String(200), nullable=True)
    title_si = db.Column(db.String(200), nullable=True)
    content_en = db.Column(db.Text, nullable=True)
    content_si = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.Text, nullable=True)
    video_url = db.Column(db.Text, nullable=True)
    audio_url = db.Column(db.Text, nullable=True)
    activity_json = db.Column(db.Text, nullable=True)
    xp_reward = db.Column(db.Integer, nullable=False, default=10)
    is_required = db.Column(db.Boolean, nullable=False, default=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudentLessonProgress(db.Model):
    __tablename__ = "student_lesson_progress"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False)
    lesson_id = db.Column(db.Integer, nullable=False)
    current_slide_order = db.Column(db.Integer, nullable=False, default=1)
    completion_percent = db.Column(db.Float, nullable=False, default=0)
    is_completed = db.Column(db.Boolean, nullable=False, default=False)
    last_opened_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class StudentLessonAnswer(db.Model):
    __tablename__ = "student_lesson_answer"

    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.Integer, nullable=False)
    slide_id = db.Column(db.Integer, nullable=False)
    student_id = db.Column(db.Integer, nullable=False)
    selected_answer = db.Column(db.Text, nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    answered_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class StudentSkillMastery(db.Model):
    __tablename__ = "student_skill_mastery"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False)
    subject_id = db.Column(db.Integer, nullable=True)
    module_id = db.Column(db.Integer, nullable=True)
    chapter_id = db.Column(db.Integer, nullable=False)
    lesson_id = db.Column(db.Integer, nullable=False)
    skill_code = db.Column(db.String(120), nullable=False)
    skill_name_en = db.Column(db.String(255), nullable=False)
    skill_name_si = db.Column(db.String(255), nullable=False)
    mastery_score = db.Column(db.Float, nullable=False, default=0)
    total_attempts = db.Column(db.Integer, nullable=False, default=0)
    correct_attempts = db.Column(db.Integer, nullable=False, default=0)
    wrong_attempts = db.Column(db.Integer, nullable=False, default=0)
    last_answered_at = db.Column(db.DateTime, nullable=True)
    status_en = db.Column(db.String(50), nullable=False, default="Weak")
    status_si = db.Column(db.String(50), nullable=False, default="දුර්වලයි")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class StudentAiAssistanceLog(db.Model):
    __tablename__ = "student_ai_assistance_log"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False)
    lesson_id = db.Column(db.Integer, nullable=False)
    slide_id = db.Column(db.Integer, nullable=False)
    assistance_type = db.Column(db.String(40), nullable=False)
    triggered_reason = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class ChapterLearningContent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chapter_id = db.Column(db.Integer, nullable=False)
    content_order = db.Column(db.Integer, nullable=False, default=1)
    content_type = db.Column(db.String(20), nullable=False)  # video, note, activity, practice, test
    title_en = db.Column(db.String(200), nullable=False)
    title_si = db.Column(db.String(200), nullable=False)
    content_url = db.Column(db.Text, nullable=True)
    content_body_en = db.Column(db.Text, nullable=True)
    content_body_si = db.Column(db.Text, nullable=True)
    is_required = db.Column(db.Boolean, nullable=False, default=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudentChapterProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False)
    chapter_id = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="locked")
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudentContentProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False)
    content_id = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="not_started")
    completed_at = db.Column(db.DateTime, nullable=True)


class StudentSubjectOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False)
    subject_id = db.Column(db.Integer, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class StudentSelectedSubject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False)
    subject_id = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class VideoInteraction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content_id = db.Column(db.Integer, nullable=False)
    question_id = db.Column(db.Integer, nullable=False)
    trigger_seconds = db.Column(db.Integer, nullable=False)
    pause_video = db.Column(db.Boolean, nullable=False, default=True)
    required_answer = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    question = db.relationship(
        "Question",
        foreign_keys=[question_id],
        primaryjoin="VideoInteraction.question_id == Question.id",
        lazy="joined",
        uselist=False,
        viewonly=True,
    )
    content = db.relationship(
        "ChapterLearningContent",
        foreign_keys=[content_id],
        primaryjoin="VideoInteraction.content_id == ChapterLearningContent.id",
        lazy="joined",
        uselist=False,
        viewonly=True,
    )


class StudentVideoInteractionAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False)
    content_id = db.Column(db.Integer, nullable=False)
    interaction_id = db.Column(db.Integer, nullable=False)
    question_id = db.Column(db.Integer, nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    retry_count = db.Column(db.Integer, nullable=False, default=0)
    answered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudentVideoAnalytics(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False)
    content_id = db.Column(db.Integer, nullable=False)
    watch_percent = db.Column(db.Float, nullable=False, default=0)
    all_required_answered = db.Column(db.Boolean, nullable=False, default=False)
    popup_score = db.Column(db.Float, nullable=False, default=0)
    retry_count = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    grade = db.Column(db.String(20), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    topic = db.Column(db.String(150), nullable=False)
    topic_en = db.Column(db.String(150), nullable=False)
    topic_si = db.Column(db.String(150), nullable=False)
    term_id = db.Column(db.Integer, nullable=True)
    module_id = db.Column(db.Integer, nullable=True)
    chapter_id = db.Column(db.Integer, nullable=True)
    chapter_en = db.Column(db.String(150), nullable=True)
    chapter_si = db.Column(db.String(150), nullable=True)
    question_text_en = db.Column(db.Text, nullable=False)
    question_text_si = db.Column(db.Text, nullable=False)
    option_a_en = db.Column(db.Text, nullable=False)
    option_a_si = db.Column(db.Text, nullable=False)
    option_b_en = db.Column(db.Text, nullable=False)
    option_b_si = db.Column(db.Text, nullable=False)
    option_c_en = db.Column(db.Text, nullable=False)
    option_c_si = db.Column(db.Text, nullable=False)
    option_d_en = db.Column(db.Text, nullable=False)
    option_d_si = db.Column(db.Text, nullable=False)
    question_type = db.Column(db.String(20), nullable=False, default="mcq")
    correct_answer_text = db.Column(db.Text, nullable=True)
    box_template = db.Column(db.Text, nullable=True)
    box_answers = db.Column(db.Text, nullable=True)
    matching_left_en = db.Column(db.Text, nullable=True)
    matching_right_en = db.Column(db.Text, nullable=True)
    matching_answers_en = db.Column(db.Text, nullable=True)
    matching_left_si = db.Column(db.Text, nullable=True)
    matching_right_si = db.Column(db.Text, nullable=True)
    matching_answers_si = db.Column(db.Text, nullable=True)
    tap_areas_json = db.Column(db.Text, nullable=True)
    correct_area_id = db.Column(db.String(100), nullable=True)
    drag_items_json = db.Column(db.Text, nullable=True)
    drag_container_image_url = db.Column(db.Text, nullable=True)
    drag_groups_json = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.Text, nullable=True)
    correct_option = db.Column(db.String(1), nullable=False)
    explanation_en = db.Column(db.Text, nullable=False)
    explanation_si = db.Column(db.Text, nullable=False)
    difficulty_level = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudentResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=True)
    grade = db.Column(db.String(20), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    medium = db.Column(db.String(20), nullable=False)
    score = db.Column(db.Float, nullable=False, default=0)
    level = db.Column(db.String(50), nullable=False)
    total_questions = db.Column(db.Integer, nullable=False, default=0)
    correct_answers = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)




class PracticeAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=True)
    grade = db.Column(db.String(20), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    topic_en = db.Column(db.String(150), nullable=False)
    topic_si = db.Column(db.String(150), nullable=False)
    medium = db.Column(db.String(20), nullable=False)
    score = db.Column(db.Float, nullable=False, default=0)
    total_questions = db.Column(db.Integer, nullable=False, default=0)
    correct_answers = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudentQuestionAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=True)
    question_id = db.Column(db.Integer, nullable=False)
    source_type = db.Column(db.String(20), nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class StudentTopicPerformance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_result_id = db.Column(
        db.Integer,
        db.ForeignKey("student_result.id", ondelete="CASCADE"),
        nullable=False,
    )
    topic_en = db.Column(db.String(150), nullable=False)
    topic_si = db.Column(db.String(150), nullable=False)
    correct_count = db.Column(db.Integer, nullable=False, default=0)
    total_count = db.Column(db.Integer, nullable=False, default=0)
    percentage = db.Column(db.Float, nullable=False, default=0)
    status_en = db.Column(db.String(50), nullable=False)
    status_si = db.Column(db.String(50), nullable=False)


class HomeworkAssignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, nullable=False)
    teacher_id = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    grade = db.Column(db.String(20), nullable=False)
    subject = db.Column(db.String(100), nullable=False, default="Math")
    topic_en = db.Column(db.String(150), nullable=False)
    topic_si = db.Column(db.String(150), nullable=False)
    term_id = db.Column(db.Integer, nullable=True)
    module_id = db.Column(db.Integer, nullable=True)
    chapter_id = db.Column(db.Integer, nullable=True)
    difficulty_level = db.Column(db.Integer, nullable=False, default=1)
    due_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class HomeworkSubmission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    homework_id = db.Column(db.Integer, nullable=False)
    student_id = db.Column(db.Integer, nullable=False)
    score = db.Column(db.Float, nullable=False, default=0)
    total_questions = db.Column(db.Integer, nullable=False, default=0)
    correct_answers = db.Column(db.Integer, nullable=False, default=0)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ClassTest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, nullable=False)
    teacher_id = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    grade = db.Column(db.String(20), nullable=False)
    subject = db.Column(db.String(100), nullable=False, default="Math")
    topic_en = db.Column(db.String(150), nullable=False)
    topic_si = db.Column(db.String(150), nullable=False)
    term_id = db.Column(db.Integer, nullable=True)
    module_id = db.Column(db.Integer, nullable=True)
    chapter_id = db.Column(db.Integer, nullable=True)
    difficulty_level = db.Column(db.Integer, nullable=False, default=1)
    test_date = db.Column(db.Date, nullable=False)
    duration_minutes = db.Column(db.Integer, nullable=False, default=30)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ClassTestSubmission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_test_id = db.Column(db.Integer, nullable=False)
    student_id = db.Column(db.Integer, nullable=False)
    score = db.Column(db.Float, nullable=False, default=0)
    total_questions = db.Column(db.Integer, nullable=False, default=0)
    correct_answers = db.Column(db.Integer, nullable=False, default=0)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudentTopicProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False)
    grade = db.Column(db.String(20), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    topic_en = db.Column(db.String(150), nullable=False)
    topic_si = db.Column(db.String(150), nullable=False)
    latest_score = db.Column(db.Float, nullable=False, default=0)
    mastery_level_en = db.Column(db.String(50), nullable=False)
    mastery_level_si = db.Column(db.String(50), nullable=False)
    attempts_count = db.Column(db.Integer, nullable=False, default=1)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudentRevisionQueue(db.Model):
    __tablename__ = "student_revision_queue"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False, index=True)
    subject_id = db.Column(db.Integer, nullable=True)
    module_id = db.Column(db.Integer, nullable=True)
    chapter_id = db.Column(db.Integer, nullable=True)
    lesson_id = db.Column(db.Integer, nullable=True)
    skill_code = db.Column(db.String(120), nullable=False)
    revision_reason = db.Column(db.String(120), nullable=False)
    priority_score = db.Column(db.Float, nullable=False, default=0)
    due_date = db.Column(db.Date, nullable=False, index=True)
    is_completed = db.Column(db.Boolean, nullable=False, default=False, index=True)
    interval_days = db.Column(db.Integer, nullable=False, default=1)
    successful_revisions = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

class ParentNotification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False)
    parent_email = db.Column(db.String(120), nullable=False)
    message_en = db.Column(db.Text, nullable=False)
    message_si = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


def get_homework_summary_for_class(class_id: int):
    assignments = (
        HomeworkAssignment.query.filter_by(class_id=class_id)
        .order_by(HomeworkAssignment.due_date.asc(), HomeworkAssignment.id.desc())
        .all()
    )
    total_students = Student.query.filter_by(class_id=class_id).count()
    summary_rows = []
    for assignment in assignments:
        submissions = HomeworkSubmission.query.filter_by(homework_id=assignment.id).all()
        submission_count = len(submissions)
        average_score = (
            round(sum(item.score for item in submissions) / submission_count, 1)
            if submission_count
            else None
        )
        summary_rows.append(
            {
                "assignment": assignment,
                "total_students": total_students,
                "submission_count": submission_count,
                "average_score": average_score,
            }
        )
    return summary_rows






def ensure_gamification_columns() -> None:
    db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS xp INTEGER"))
    db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS level INTEGER"))
    db.session.execute(db.text("UPDATE student SET xp = 0 WHERE xp IS NULL"))
    db.session.execute(db.text("UPDATE student SET level = 1 WHERE level IS NULL"))
    db.session.commit()




def normalize_existing_grade_data() -> None:
    grade_updates = {"O/L": "OL", "A/L": "AL", "O-L": "OL", "A-L": "AL"}
    for source, target in grade_updates.items():
        db.session.execute(db.text("UPDATE student SET grade = :target WHERE grade = :source"), {"target": target, "source": source})
        db.session.execute(db.text("UPDATE question SET grade = :target WHERE grade = :source"), {"target": target, "source": source})
        db.session.execute(db.text("UPDATE student_result SET grade = :target WHERE grade = :source"), {"target": target, "source": source})
        db.session.execute(db.text("UPDATE practice_attempt SET grade = :target WHERE grade = :source"), {"target": target, "source": source})
        db.session.execute(db.text("UPDATE student_topic_progress SET grade = :target WHERE grade = :source"), {"target": target, "source": source})
    db.session.commit()


def ensure_streak_columns() -> None:
    db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS current_streak INTEGER"))
    db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS longest_streak INTEGER"))
    db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS last_activity_date DATE"))
    db.session.execute(db.text("UPDATE student SET current_streak = 0 WHERE current_streak IS NULL"))
    db.session.execute(db.text("UPDATE student SET longest_streak = 0 WHERE longest_streak IS NULL"))
    db.session.commit()


def ensure_subscription_columns() -> None:
    db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS is_premium BOOLEAN"))
    db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS subscription_end_date DATE"))
    db.session.execute(db.text("UPDATE student SET is_premium = FALSE WHERE is_premium IS NULL"))
    db.session.commit()


def has_active_premium(student: Student | None) -> bool:
    if not student:
        return False
    if not student.is_premium:
        return False
    if student.subscription_end_date and student.subscription_end_date < date.today():
        return False
    return True


def expire_subscription_if_needed(student: Student | None) -> bool:
    if not student or not student.is_premium:
        return False
    if not student.subscription_end_date or student.subscription_end_date >= date.today():
        return False
    student.is_premium = False
    student.subscription_end_date = None
    db.session.commit()
    return True


def get_subscription_expired_message(medium: str) -> str:
    if resolve_medium(medium) == "Sinhala":
        return "ඔබගේ Premium අවසන් වී ඇත. දිගටම භාවිතා කිරීමට නැවත සක්‍රීය කරන්න."
    return "Your premium has expired. Please renew to continue."


def get_daily_practice_count(student_id: int | None) -> int:
    if not student_id:
        return 0
    today_start = datetime.combine(date.today(), datetime.min.time())
    return (
        StudentQuestionAttempt.query.filter(
            StudentQuestionAttempt.student_id == student_id,
            StudentQuestionAttempt.source_type == "Practice",
            StudentQuestionAttempt.created_at >= today_start,
        ).count()
    )


def get_daily_retest_count(student_id: int | None) -> int:
    if not student_id:
        return 0
    today_start = datetime.combine(date.today(), datetime.min.time())
    return (
        StudentQuestionAttempt.query.filter(
            StudentQuestionAttempt.student_id == student_id,
            StudentQuestionAttempt.source_type == "RetestWeak",
            StudentQuestionAttempt.created_at >= today_start,
        ).count()
    )


def update_student_streak(student_id: int | None) -> tuple[int, int]:
    if not student_id:
        return 0, 0

    student = db.session.get(Student, student_id)
    if not student:
        return 0, 0

    today = date.today()
    last_date = student.last_activity_date

    if last_date == today:
        current = student.current_streak or 0
    elif last_date == today - timedelta(days=1):
        current = (student.current_streak or 0) + 1
    else:
        current = 1

    student.current_streak = current
    student.longest_streak = max(student.longest_streak or 0, current)
    student.last_activity_date = today
    return student.current_streak, student.longest_streak


def get_streak_feedback(student_id: int | None) -> dict[str, int | bool]:
    if not student_id:
        return {"increased": False, "restarted": False, "current": 0}

    student = db.session.get(Student, student_id)
    if not student:
        return {"increased": False, "restarted": False, "current": 0}

    previous_streak = student.current_streak or 0
    previous_last_date = student.last_activity_date
    today = date.today()
    was_reset = bool(previous_last_date and previous_last_date < today - timedelta(days=1) and previous_streak > 0)
    current_streak, _ = update_student_streak(student_id)
    return {
        "increased": current_streak > previous_streak,
        "restarted": was_reset and current_streak == 1,
        "current": current_streak,
    }

def get_topic_sinhala(topic_en: str) -> str:
    topic_map = {
        "Fractions": "භාග",
        "Decimals": "දශම",
        "Perimeter": "පරිමිතිය",
        "Factors": "සාධක",
        "Percentages": "ප්‍රතිශත",
    }
    return topic_map.get(topic_en, topic_en)


def create_parent_notification(
    student_id: int | None,
    topic_en: str,
    topic_si: str,
    score: float,
    improved: bool,
    streak_increased: bool,
) -> None:
    if not student_id:
        return

    student = db.session.get(Student, student_id)
    if not student or not (student.parent_email or "").strip():
        return

    message_en = None
    message_si = None
    if score < 50:
        message_en = f"Your child needs improvement in {topic_en}"
        message_si = f"ඔබගේ දරුවා {topic_si} දුර්වලයි"
    elif improved:
        message_en = f"Good progress in {topic_en}"
        message_si = f"{topic_si} හි හොඳ ප්‍රගතියක්"
    elif streak_increased:
        message_en = "Your child is learning consistently"
        message_si = "ඔබගේ දරුවා අඛණ්ඩව ඉගෙන ගනී"

    if not message_en or not message_si:
        return

    db.session.add(
        ParentNotification(
            student_id=student_id,
            parent_email=student.parent_email.strip(),
            message_en=message_en,
            message_si=message_si,
        )
    )



def _fraction_pair(same_denominator: bool = False) -> tuple[int, int, int, int]:
    denominators = [2, 3, 4, 5, 6, 8, 10, 12]
    d1 = random.choice(denominators)
    d2 = d1 if same_denominator else random.choice(denominators)
    n1 = random.randint(1, d1 - 1)
    n2 = random.randint(1, d2 - 1)
    return n1, d1, n2, d2


def _format_fraction(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}" if value.denominator != 1 else str(value.numerator)


def _build_fraction_options(correct: Fraction) -> tuple[list[str], str]:
    distractors = {
        correct + Fraction(1, max(2, correct.denominator)),
        correct - Fraction(1, max(2, correct.denominator)),
        Fraction(correct.numerator + 1, correct.denominator),
    }
    distractors = [d for d in distractors if d > 0 and d != correct]
    while len(distractors) < 3:
        delta = Fraction(random.randint(1, 3), random.choice([2, 3, 4, 5]))
        candidate = correct + delta if random.choice([True, False]) else correct - delta
        if candidate > 0 and candidate != correct and candidate not in distractors:
            distractors.append(candidate)
    choices = [_format_fraction(correct)] + [_format_fraction(d) for d in distractors[:3]]
    random.shuffle(choices)
    correct_option = "ABCD"[choices.index(_format_fraction(correct))]
    return choices, correct_option


def build_generated_question(grade: str, subject: str, topic: str, difficulty_level: int) -> dict:
    topic_clean = (topic or "Fractions").strip() or "Fractions"
    topic_lower = topic_clean.lower()

    if "fraction" in topic_lower or "භාග" in topic_lower:
        operator = random.choice(["+", "-"])
        if difficulty_level in {1, 2}:
            n1, d1, n2, d2 = _fraction_pair(same_denominator=True)
            if operator == "-" and n1 < n2:
                n1, n2 = n2, n1
            question_en = f"What is {n1}/{d1} {operator} {n2}/{d2}?"
            question_si = f"{n1}/{d1} {operator} {n2}/{d2} කීයද?"
        elif difficulty_level == 3:
            n1, d1, n2, d2 = _fraction_pair(same_denominator=False)
            while d1 == d2:
                n1, d1, n2, d2 = _fraction_pair(same_denominator=False)
            if operator == "-" and Fraction(n1, d1) < Fraction(n2, d2):
                n1, d1, n2, d2 = n2, d2, n1, d1
            question_en = f"What is {n1}/{d1} {operator} {n2}/{d2}?"
            question_si = f"{n1}/{d1} {operator} {n2}/{d2} කීයද?"
        else:
            whole1 = random.randint(1, 4)
            whole2 = random.randint(1, 4)
            fn1, fd1, fn2, fd2 = _fraction_pair(same_denominator=False)
            while fd1 == fd2:
                fn1, fd1, fn2, fd2 = _fraction_pair(same_denominator=False)
            f1 = Fraction(whole1 * fd1 + fn1, fd1)
            f2 = Fraction(whole2 * fd2 + fn2, fd2)
            if operator == "-" and f1 < f2:
                f1, f2 = f2, f1
                whole1, whole2, fn1, fd1, fn2, fd2 = whole2, whole1, fn2, fd2, fn1, fd1
            question_en = f"What is {whole1} {fn1}/{fd1} {operator} {whole2} {fn2}/{fd2}?"
            question_si = f"{whole1} {fn1}/{fd1} {operator} {whole2} {fn2}/{fd2} කීයද?"
            n1, d1, n2, d2 = f1.numerator, f1.denominator, f2.numerator, f2.denominator

        result = Fraction(n1, d1) + Fraction(n2, d2) if operator == "+" else Fraction(n1, d1) - Fraction(n2, d2)
        options, correct_option = _build_fraction_options(result)
        explanation_en = f"Compute {n1}/{d1} {operator} {n2}/{d2} and simplify to {_format_fraction(result)}."
        explanation_si = f"{n1}/{d1} {operator} {n2}/{d2} ගණනය කර {_format_fraction(result)} ලෙස සරල කරන්න."
    else:
        a = random.randint(1, 40)
        b = random.randint(1, 40)
        operator = random.choice(["+", "-"])
        if operator == "-" and a < b:
            a, b = b, a
        result = a + b if operator == "+" else a - b
        question_en = f"What is {a} {operator} {b}?"
        question_si = f"{a} {operator} {b} කීයද?"
        options = [str(result), str(result + 1), str(max(0, result - 1)), str(result + 2)]
        random.shuffle(options)
        correct_option = "ABCD"[options.index(str(result))]
        explanation_en = "Solve the arithmetic operation."
        explanation_si = "ගණිත ක්‍රියාව විසඳන්න."

    return {
        "grade": grade,
        "subject": subject,
        "topic": topic_clean,
        "topic_en": topic_clean,
        "topic_si": get_topic_sinhala(topic_clean),
        "question_text_en": question_en,
        "question_text_si": question_si,
        "option_a_en": options[0],
        "option_a_si": options[0],
        "option_b_en": options[1],
        "option_b_si": options[1],
        "option_c_en": options[2],
        "option_c_si": options[2],
        "option_d_en": options[3],
        "option_d_si": options[3],
        "correct_option": correct_option,
        "explanation_en": explanation_en,
        "explanation_si": explanation_si,
        "difficulty_level": difficulty_level,
    }


FRONTEND_BUILD_DIR = os.path.join(app.root_path, "build", "web")


@app.route("/")
def home() -> object:
    return send_from_directory(FRONTEND_BUILD_DIR, "index.html")


@app.route("/join")
@app.route("/join/")
def join_page() -> object:
    return send_from_directory(FRONTEND_BUILD_DIR, "join/index.html")


@app.route("/register-form", methods=["GET", "POST"])
@app.route("/register-form/", methods=["GET", "POST"])
def register_form() -> object:
    if request.method == "GET":
        template_path = os.path.join(FRONTEND_BUILD_DIR, "register-form", "index.html")
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()

        schools = School.query.order_by(School.school_name.asc(), School.id.asc()).all()
        school_options = "".join(
            f'<option value="{school.id}">{escape(school.school_name)}</option>'
            for school in schools
        )
        html = html.replace(
            '<select name="school_id"><option value="">Select your school</option></select>',
            f'<select name="school_id"><option value="">Select your school</option>{school_options}</select>',
        )
        return html
    return register_student()


@app.route("/<path:path>")
def frontend_static_or_spa(path: str) -> object:
    file_path = os.path.join(FRONTEND_BUILD_DIR, path)
    if os.path.isfile(file_path):
        return send_from_directory(FRONTEND_BUILD_DIR, path)
    return send_from_directory(FRONTEND_BUILD_DIR, "index.html")


@app.route("/create-db")
def create_db() -> str:
    db.create_all()
    return "Database tables created successfully"


@app.route("/register", methods=["POST"])
def register_student():
    is_form_submission = request.content_type and "application/x-www-form-urlencoded" in request.content_type

    if is_form_submission:
        data = request.form.to_dict()
    else:
        data = request.get_json(silent=True)

    if not data:
        if is_form_submission:
            return "<h2>Error: Invalid or missing form data</h2><p><a href='/register-form'>Back</a></p>", 400
        return jsonify({"success": False, "message": "Invalid or missing JSON body"}), 400

    required_fields = ["name", "grade", "mobile", "medium", "password", "confirm_password"]
    if Student.email.nullable is False:
        required_fields.append("email")
    missing_fields = [field for field in required_fields if not str(data.get(field, "")).strip()]
    if missing_fields:
        if is_form_submission:
            return (
                f"<h2>Error: Missing required fields: {', '.join(missing_fields)}</h2>"
                "<p><a href='/register-form'>Back</a></p>",
                400,
            )
        return (
            jsonify(
                {
                    "success": False,
                    "message": f"Missing required fields: {', '.join(missing_fields)}",
                }
            ),
            400,
        )

    password = str(data.get("password", ""))
    confirm_password = str(data.get("confirm_password", ""))
    if password != confirm_password:
        msg = "Password and confirm password do not match"
        if is_form_submission:
            return f"<h2>Error: {msg}</h2><p><a href='/register-form'>Back</a></p>", 400
        return jsonify({"success": False, "message": msg}), 400

    medium = data["medium"].strip()
    if medium not in SUPPORTED_MEDIA:
        msg = "Invalid medium. Use 'English' or 'Sinhala'"
        if is_form_submission:
            return f"<h2>Error: {msg}</h2><p><a href='/register-form'>Back</a></p>", 400
        return jsonify({"success": False, "message": msg}), 400

    grade = normalize_grade(data.get("grade"))
    if not is_valid_grade(grade):
        msg = "Invalid grade. Use one of: 1-10, OL, AL"
        if is_form_submission:
            return f"<h2>Error: {msg}</h2><p><a href='/register-form'>Back</a></p>", 400
        return jsonify({"success": False, "message": msg}), 400

    school_id_raw = str(data.get("school_id", "")).strip()
    school_id = None
    if school_id_raw:
        if not school_id_raw.isdigit() or not School.query.get(int(school_id_raw)):
            msg = "Invalid school selected"
            if is_form_submission:
                return f"<h2>Error: {msg}</h2><p><a href='/register-form'>Back</a></p>", 400
            return jsonify({"success": False, "message": msg}), 400
        school_id = int(school_id_raw)

    email = str(data.get("email", "")).strip()
    parent_email = str(data.get("parent_email", "")).strip()
    mobile = data["mobile"].strip()

    if Student.query.filter_by(email=email).first():
        if is_form_submission:
            return "<h2>Error: Email already exists</h2><p><a href='/register-form'>Back</a></p>", 409
        return jsonify({"success": False, "message": "Email already exists"}), 409

    student = Student(
        name=data["name"].strip(),
        grade=grade,
        medium=medium,
        email=email,
        username=f"TMP-{uuid.uuid4().hex}",
        parent_email=parent_email,
        mobile=mobile,
        password_hash=generate_password_hash(password),
        school_id=school_id,
    )

    db.session.add(student)
    db.session.flush()
    student.username = generate_student_username_for_id(student.id)
    db.session.commit()
    recipients = []
    if student.email:
        recipients.append(student.email.strip().lower())
    if student.parent_email:
        recipients.append(student.parent_email.strip().lower())
    recipients = list(dict.fromkeys(recipients))

    if recipients:
        try:
            send_welcome_email(
                student_name=student.name,
                recipients=recipients,
                grade=display_grade(student.grade, student.medium),
                medium=student.medium,
                username=student.username or "",
                plain_password=password,
            )
        except Exception:
            app.logger.exception(
                "Failed to send welcome email for student_id=%s to recipients=%s",
                student.id,
                recipients,
            )
    else:
        app.logger.warning("No email available for welcome email.")

    if is_form_submission:
        safe_email = quote_plus(student.email or "")
        return f"""
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>SLIS Registration Success</title>
            <style>
              :root {{
                --slis-blue-start: #2a7de1;
                --slis-blue-end: #1f56bf;
                --slis-bg-start: #f6fbff;
                --slis-bg-end: #dceeff;
                --slis-text: #13315f;
                --slis-subtle: #5b6f92;
                --success: #13a968;
              }}
              * {{
                box-sizing: border-box;
              }}
              body {{
                margin: 0;
                min-height: 100vh;
                font-family: "Inter", "Segoe UI", Roboto, Arial, sans-serif;
                background: linear-gradient(145deg, var(--slis-bg-start), var(--slis-bg-end));
                color: var(--slis-text);
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
              }}
              .success-card {{
                width: 100%;
                max-width: 520px;
                background: #ffffff;
                border-radius: 24px;
                box-shadow: 0 24px 56px rgba(33, 88, 174, 0.16);
                padding: 32px 28px;
                text-align: center;
              }}
              .brand {{
                font-weight: 800;
                font-size: 1.15rem;
                letter-spacing: 0.6px;
                margin-bottom: 18px;
                color: #1f56bf;
              }}
              .checkmark {{
                width: 74px;
                height: 74px;
                border-radius: 50%;
                margin: 0 auto 18px;
                display: flex;
                align-items: center;
                justify-content: center;
                background: rgba(19, 169, 104, 0.12);
                color: var(--success);
                font-size: 2.2rem;
                font-weight: 700;
              }}
              h1 {{
                margin: 0 0 10px;
                font-size: clamp(1.5rem, 4.8vw, 2rem);
                line-height: 1.2;
              }}
              p {{
                margin: 0;
                color: var(--slis-subtle);
                line-height: 1.55;
                font-size: 1rem;
              }}
              .redirecting {{
                margin-top: 18px;
                color: #355f99;
                font-weight: 600;
              }}
              .progress {{
                margin: 16px auto 22px;
                width: 100%;
                height: 8px;
                border-radius: 999px;
                background: #e7efff;
                overflow: hidden;
              }}
              .progress-bar {{
                height: 100%;
                width: 40%;
                border-radius: inherit;
                background: linear-gradient(90deg, var(--slis-blue-start), var(--slis-blue-end));
                animation: loadProgress 1.15s ease-in-out infinite;
                transform-origin: left center;
              }}
              .login-now {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 100%;
                max-width: 260px;
                text-decoration: none;
                color: #fff;
                font-weight: 700;
                padding: 12px 18px;
                border-radius: 12px;
                background: linear-gradient(135deg, var(--slis-blue-start), var(--slis-blue-end));
                box-shadow: 0 12px 24px rgba(33, 88, 174, 0.24);
              }}
              .login-now:hover {{
                filter: brightness(1.04);
              }}
              @keyframes loadProgress {{
                0% {{
                  transform: translateX(-110%);
                }}
                100% {{
                  transform: translateX(300%);
                }}
              }}
              @media (max-width: 480px) {{
                .success-card {{
                  border-radius: 20px;
                  padding: 26px 18px;
                }}
              }}
            </style>
          </head>
          <body>
            <main class="success-card">
              <div class="brand">SLIS • Spiral Learning Intelligence System</div>
              <div class="checkmark" aria-hidden="true">✓</div>
              <h1>Account Created Successfully!</h1>
              <p>Your login details have been sent to the student and parent email.</p>
              <p class="redirecting">Redirecting to login…</p>
              <div class="progress" role="status" aria-label="Redirecting progress">
                <div class="progress-bar"></div>
              </div>
              <a class="login-now" href="/login?email={safe_email}">Go to Login Now</a>
            </main>
            <script>
              setTimeout(function () {{
                window.location.href = "/login?email={safe_email}";
              }}, 2500);
            </script>
          </body>
        </html>
        """

    return (
        jsonify(
            {
                "success": True,
                "message": "Student registered successfully",
                "student": {
                    "id": student.id,
                    "name": student.name,
                    "grade": student.grade,
                    "medium": student.medium,
                    "email": student.email,
                    "username": student.username,
                    "parent_email": student.parent_email,
                    "mobile": student.mobile,
                    "school_id": student.school_id,
                },
            }
        ),
        201,
    )


@app.route("/students", methods=["GET"])
def get_students():
    students = Student.query.order_by(Student.id.asc()).all()
    return (
        jsonify(
            {
                "success": True,
                "students": [
                    {
                        "id": student.id,
                        "name": student.name,
                        "grade": student.grade,
                        "medium": student.medium,
                        "email": student.email,
                        "parent_email": student.parent_email,
                        "mobile": student.mobile,
                    }
                    for student in students
                ],
            }
        ),
        200,
    )


VALID_PASSWORD_RESET_ROLES = {"student", "parent", "teacher", "school_admin"}


def get_user_for_role_by_email(role: str, email: str):
    if role == "student":
        return Student.query.filter_by(email=email).first()
    if role == "teacher":
        return Teacher.query.filter_by(email=email).first()
    if role == "school_admin":
        return SchoolAdmin.query.filter_by(email=email).first()
    if role == "parent":
        return Student.query.filter_by(parent_email=email).order_by(Student.id.asc()).first()
    return None


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    message = ""
    if request.method == "POST":
        role = (request.form.get("role") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        if role in VALID_PASSWORD_RESET_ROLES and email:
            user = get_user_for_role_by_email(role, email)
            if user and getattr(user, "id", None):
                raw_token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
                expires_at = datetime.utcnow() + timedelta(minutes=30)
                db.session.add(
                    PasswordResetToken(
                        user_id=user.id,
                        role=role,
                        token_hash=token_hash,
                        expires_at=expires_at,
                    )
                )
                db.session.commit()
                reset_link = f"{request.url_root.rstrip('/')}/reset-password?token={quote_plus(raw_token)}"
                send_password_reset_email([email], reset_link)
        message = "If this email is registered, password reset instructions will be sent shortly."

    safe_message = f"<p class='notice'>{escape(message)}</p>" if message else ""
    return f"""
    <!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Forgot Password</title>
    <style>{LOGIN_PAGE_STYLES}
    .notice {{margin: 0 0 12px;color:#1f2a44;font-size:.9rem;background:#e8f0ff;border-radius:10px;padding:10px 12px;}}
    </style></head><body class="login-page"><div class="login-card"><div class="brand"><img class="login-logo" src="/static/images/SLIS LOGO.png" alt="SLIS logo"><p>Spiral Learning Intelligence System</p></div>
    {safe_message}
    <form method="post" action="/forgot-password"><div class="field"><label for="role">Role</label><select id="role" name="role" required>
    <option value="student">Student</option><option value="parent">Parent</option><option value="teacher">Teacher</option><option value="school_admin">School Admin</option></select></div>
    <div class="field"><label for="email">Email address</label><input id="email" type="email" name="email" required></div>
    <button type="submit">Send Reset Link</button></form></div></body></html>
    """


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    token = (request.args.get("token") or request.form.get("token") or "").strip()
    notice = ""
    if request.method == "POST":
        new_password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        if new_password != confirm_password:
            notice = "Passwords do not match."
        else:
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""
            record = PasswordResetToken.query.filter_by(token_hash=token_hash).first() if token_hash else None
            is_valid_record = record and not record.used_at and record.expires_at >= datetime.utcnow()
            if not is_valid_record:
                notice = "This reset link is invalid or has expired."
            else:
                user = get_user_for_role_by_email(record.role, "")  # placeholder for typing
                if record.role == "student":
                    user = db.session.get(Student, record.user_id)
                elif record.role == "teacher":
                    user = db.session.get(Teacher, record.user_id)
                elif record.role == "school_admin":
                    user = db.session.get(SchoolAdmin, record.user_id)
                elif record.role == "parent":
                    user = db.session.get(Student, record.user_id)
                if user:
                    user.password_hash = generate_password_hash(new_password)
                    record.used_at = datetime.utcnow()
                    db.session.commit()
                    return redirect(url_for("login", reset="success"))
                notice = "This reset link is invalid or has expired."

    safe_notice = f"<p class='notice'>{escape(notice)}</p>" if notice else ""
    safe_token = escape(token, quote=True)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Reset Password</title>
    <style>{LOGIN_PAGE_STYLES}.notice {{margin:0 0 12px;color:#1f2a44;font-size:.9rem;background:#e8f0ff;border-radius:10px;padding:10px 12px;}}</style></head>
    <body class="login-page"><div class="login-card"><div class="brand"><img class="login-logo" src="/static/images/SLIS LOGO.png" alt="SLIS logo"><p>Spiral Learning Intelligence System</p></div>
    {safe_notice}<form method="post" action="/reset-password"><input type="hidden" name="token" value="{safe_token}">
    <div class="field"><label for="password">New Password</label><input id="password" type="password" name="password" required></div>
    <div class="field"><label for="confirm_password">Confirm Password</label><input id="confirm_password" type="password" name="confirm_password" required></div>
    <button type="submit">Reset Password</button></form></div></body></html>"""


LOGIN_PAGE_STYLES = """
              :root {
                --slis-blue: #1e66f5;
                --slis-blue-dark: #184bb8;
                --slis-card-bg: rgba(255, 255, 255, 0.72);
                --slis-border: rgba(255, 255, 255, 0.48);
              }
              * { box-sizing: border-box; }
              body.login-page {
                margin: 0; min-height: 100vh; min-height: 100svh; font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(rgba(255, 255, 255, 0.30), rgba(240, 248, 255, 0.38)), url('/static/images/login-bg.webp');
                background-size: cover; background-position: center; background-repeat: no-repeat; display: flex; align-items: center; justify-content: center; padding: 14px 20px 64px; overflow-x: hidden;
              }
              .login-card { width: min(88vw, 360px); background: var(--slis-card-bg); border: 1px solid var(--slis-border); border-radius: 26px; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.18), 0 24px 60px rgba(20, 56, 120, 0.15), inset 0 1px 0 rgba(255, 255, 255, 0.72); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px); padding: 22px 28px 14px; }
              .brand { text-align: center; margin-bottom: 10px; } .login-logo { width: clamp(95px, 21vw, 102px); height: auto; object-fit: contain; display: block; margin: 0 auto 6px auto; } .brand p { margin: 2px 0 14px; color: #3d4a67; font-size: 0.88rem; }
              .field { margin-bottom: 10px; } label { display:block;font-weight:600;margin-bottom:6px;color:#1f2a44;font-size:.9rem; }
              input, select, button { width:100%; border-radius:12px; border:1px solid #c8d8ff; padding:8px 14px; font-size:.94rem; }
              input, select { background: rgba(255,255,255,.92); color:#1a2540; min-height:42px; } input:focus, select:focus { outline:2px solid rgba(30, 102, 245, 0.25); border-color: var(--slis-blue); }
              button { border:none; background: linear-gradient(135deg, var(--slis-blue), var(--slis-blue-dark)); color:#fff; font-weight:700; cursor:pointer; margin-top:0; min-height:46px; }
              @media (max-width: 640px) { body.login-page { padding:14px; } .login-card { width:min(92vw, 360px); border-radius:24px; padding:20px 20px 14px; } }
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        prefill_email = escape((request.args.get("email") or "").strip(), quote=True)
        reset_message = ""
        if (request.args.get("reset") or "").strip() == "success":
            reset_message = "<p style='margin:0 0 10px;color:#1f2a44;background:#e8f0ff;border-radius:10px;padding:10px 12px;font-size:.9rem;'>Password reset successful. Please log in.</p>"
        return f"""
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>SLIS Login</title>
            <style>
              :root {{
                --slis-blue: #1e66f5;
                --slis-blue-dark: #184bb8;
                --slis-card-bg: rgba(255, 255, 255, 0.72);
                --slis-border: rgba(255, 255, 255, 0.48);
              }}
              * {{ box-sizing: border-box; }}
              body.login-page {{
                margin: 0;
                min-height: 100vh;
                min-height: 100svh;
                font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
                background:
                  linear-gradient(
                    rgba(255, 255, 255, 0.30),
                    rgba(240, 248, 255, 0.38)
                  ),
                  url('/static/images/login-bg.webp');
                background-size: cover;
                background-position: center;
                background-repeat: no-repeat;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 14px 20px 64px;
                overflow-x: hidden;
              }}
              .login-card {{
                width: min(88vw, 360px);
                background: var(--slis-card-bg);
                border: 1px solid var(--slis-border);
                border-radius: 26px;
                box-shadow:
                  0 12px 40px rgba(0, 0, 0, 0.18),
                  0 24px 60px rgba(20, 56, 120, 0.15),
                  inset 0 1px 0 rgba(255, 255, 255, 0.72);
                backdrop-filter: blur(14px);
                -webkit-backdrop-filter: blur(14px);
                padding: 22px 28px 14px;
                animation: cardFadeIn 560ms ease-out;
              }}
              @keyframes cardFadeIn {{
                from {{
                  opacity: 0;
                  transform: translateY(12px);
                }}
                to {{
                  opacity: 1;
                  transform: translateY(0);
                }}
              }}
              .brand {{
                text-align: center;
                margin-bottom: 10px;
              }}
              .login-logo {{
                width: clamp(95px, 21vw, 102px);
                height: auto;
                object-fit: contain;
                display: block;
                margin: 0 auto 6px auto;
              }}
              .brand h1 {{
                margin: 0;
                color: var(--slis-blue-dark);
                font-size: 1.5rem;
                letter-spacing: 0.5px;
              }}
              .brand p {{
                margin: 2px 0 14px;
                color: #3d4a67;
                font-size: 0.88rem;
              }}
              .field {{
                margin-bottom: 10px;
              }}
              label {{
                display: block;
                font-weight: 600;
                margin-bottom: 6px;
                color: #1f2a44;
                font-size: 0.9rem;
              }}
              input, select, button {{
                width: 100%;
                border-radius: 12px;
                border: 1px solid #c8d8ff;
                padding: 8px 14px;
                font-size: 0.94rem;
              }}
              input, select {{
                background: rgba(255, 255, 255, 0.92);
                color: #1a2540;
                min-height: 42px;
              }}
              input:focus, select:focus {{
                outline: 2px solid rgba(30, 102, 245, 0.25);
                border-color: var(--slis-blue);
              }}
              button {{
                border: none;
                background: linear-gradient(135deg, var(--slis-blue), var(--slis-blue-dark));
                color: #fff;
                font-weight: 700;
                cursor: pointer;
                margin-top: 0;
                min-height: 46px;
              }}
              button:hover {{
                filter: brightness(1.03);
              }}
              @media (max-width: 640px) {{
                body.login-page {{
                  padding: 14px;
                }}
                .login-card {{
                  width: min(92vw, 360px);
                  border-radius: 24px;
                  padding: 20px 20px 14px;
                  backdrop-filter: blur(12px);
                  -webkit-backdrop-filter: blur(12px);
                }}
              }}
            </style>
          </head>
          <body class="login-page">
            <div class="login-card">
              <div class="brand">
                <img class="login-logo" src="/static/images/SLIS LOGO.png" alt="SLIS logo">
                <p>Spiral Learning Intelligence System</p>
              </div>
              {reset_message}
              <form method="post" action="/login">
                <div class="field">
                  <label for="role">Role</label>
                  <select id="role" name="role">
                    <option value="student" selected>Student</option>
                    <option value="parent">Parent</option>
                    <option value="teacher">Teacher</option>
                    <option value="school_admin">School Admin</option>
                  </select>
                </div>
                <div class="field">
                  <label for="login_id">Username or Email</label>
                  <input id="login_id" type="text" name="login_id" value="{prefill_email}" required>
                </div>
                <div class="field">
                  <label for="password">Password</label>
                  <input id="password" type="password" name="password" required>
                </div>
                <div style="text-align:right;margin:-2px 0 10px;font-size:14px;">
                  <a href="/forgot-password" style="color:#184bb8;font-size:.88rem;font-weight:600;text-decoration:none;">Forgot Password?</a>
                </div>
                <button type="submit">Login</button>
              </form>
            </div>
          </body>
        </html>
        """

    role = (request.form.get("role") or "student").strip()
    login_id = (request.form.get("login_id") or "").strip()
    password = request.form.get("password") or ""

    try:
        ensure_gamification_columns()
        ensure_streak_columns()
        ensure_subscription_columns()
    except Exception:
        db.session.rollback()

    if role == "parent":
        session["parent_logged_in"] = False
        session["teacher_logged_in"] = False
        session["school_admin_logged_in"] = False
        request.form = request.form.copy()
        request.form["email"] = login_id
        return parent_login()

    if role == "teacher":
        session["parent_logged_in"] = False
        session["teacher_logged_in"] = False
        session["school_admin_logged_in"] = False
        request.form = request.form.copy()
        request.form["email"] = login_id
        return teacher_login()

    if role == "school_admin":
        school_admin = SchoolAdmin.query.filter_by(email=login_id).first()
        if school_admin and check_password_hash(school_admin.password_hash, password):
            session["school_admin_logged_in"] = True
            session["school_id"] = school_admin.school_id
            return redirect("/school-admin/dashboard")

        school_admin_email, school_admin_password = get_school_admin_credentials()
        if login_id == school_admin_email and password == school_admin_password:
            school = School.query.order_by(School.id.asc()).first()
            if not school:
                return "<h2>No school found</h2><p>Run <code>/update-school-db</code> first.</p>", 400
            session["school_admin_logged_in"] = True
            session["school_id"] = school.id
            return redirect("/school-admin/dashboard")

        return "<h2>Invalid email or password</h2><p><a href='/login'>Try again</a></p>", 401

    student = Student.query.filter((Student.email == login_id) | (Student.username == login_id)).first()
    if not student or not student.password_hash or not check_password_hash(student.password_hash, password):
        return "<h2>Invalid email or password</h2><p><a href='/login'>Try again</a></p>", 401

    expired_now = expire_subscription_if_needed(student)
    session["student_id"] = student.id
    if expired_now:
        session["subscription_expired_message"] = get_subscription_expired_message(student.medium)
    return redirect(url_for("student_dashboard"))


@app.route("/student-dashboard", methods=["GET"])
def student_dashboard():
    student_id = session.get("student_id")
    previous_result = None
    if student_id:
        student_for_result = db.session.get(Student, student_id)
        previous_result = (
            StudentResult.query.filter_by(
                student_id=student_id,
                grade=normalize_grade(student_for_result.grade) if student_for_result else "6",
                subject="Math",
            )
            .order_by(StudentResult.created_at.desc(), StudentResult.id.desc())
            .first()
        )
    if not student_id:
        return redirect(url_for("login"))

    student = db.session.get(Student, student_id)
    if not student:
        session.pop("student_id", None)
        return redirect(url_for("login"))

    profile_image_url = ""
    if student:
        profile_image_url = (
            getattr(student, "profile_image_url", None)
            or getattr(student, "photo_url", None)
            or getattr(student, "avatar_url", None)
            or ""
        )

    avatar_initials = "S"
    if student and getattr(student, "name", None):
        avatar_initials = "".join([part[0].upper() for part in student.name.split()[:2]])

    expired_now = expire_subscription_if_needed(student)
    expired_message = session.pop("subscription_expired_message", None)
    if expired_now and not expired_message:
        expired_message = get_subscription_expired_message(student.medium)

    result_history = (
        StudentResult.query.filter_by(student_id=student.id)
        .order_by(StudentResult.created_at.desc(), StudentResult.id.desc())
        .all()
    )
    latest_result = result_history[0] if result_history else None
    practice_attempts = (
        PracticeAttempt.query.filter_by(student_id=student.id)
        .order_by(PracticeAttempt.created_at.desc(), PracticeAttempt.id.desc())
        .limit(5)
        .all()
    )
    ui_text = {
        "en": {
            "dashboard": "Student Dashboard",
            "name": "Name",
            "grade": "Grade",
            "medium": "Medium",
            "my_learning_path": "My Learning Path",
            "latest_result": "SkillScan Mastery",
            "date": "Date",
            "score": "Score",
            "level": "Learning Level",
            "xp": "Skill Points",
            "xp_sinhala": "කුසලතා ලකුණු",
            "progress_to_next_level": "Next Level Progress",
            "correct_answers": "Correct Answers",
            "result_history": "Result History",
            "topic_performance": "Topic-wise Performance (Latest Result)",
            "latest_practice_attempts": "Latest Practice Attempts",
            "improvement": "Improvement",
            "improved": "Improved",
            "same": "Same",
            "dropped": "Dropped",
            "take_test": "Take SkillScan Test",
            "logout": "Logout",
            "progress_overview": "Progress Overview",
            "topic_trend": "Topic Trend",
            "last_score": "Last Score",
            "previous_score": "Previous Score",
            "trend": "Trend",
            "current_streak": "Learning Streak",
            "longest_streak": "Longest streak",
            "leaderboard": "Leaderboard",
            "goal_completed_today": "Goal Completed Today",
            "complete_one_activity_today": "Complete 1 activity today",
        },
        "si": {
            "dashboard": "ශිෂ්‍ය ඩෑෂ්බෝඩ්",
            "name": "නම",
            "grade": "ශ්‍රේණිය",
            "medium": "මාධ්‍යය",
            "my_learning_path": "මගේ ඉගෙනුම් මාර්ගය",
            "latest_result": "SkillScan ප්‍රවීණතාව",
            "date": "දිනය",
            "score": "ලකුණු",
            "level": "ඉගෙනුම් මට්ටම",
            "xp": "කුසලතා ලකුණු",
            "xp_sinhala": "කුසලතා ලකුණු",
            "progress_to_next_level": "ඊළඟ මට්ටමට ප්‍රගතිය",
            "correct_answers": "නිවැරදි පිළිතුරු",
            "result_history": "ප්‍රතිඵල ඉතිහාසය",
            "topic_performance": "මාතෘකා අනුව ක්‍රියාකාරීත්වය",
            "latest_practice_attempts": "අවසන් අභ්‍යාස උත්සාහ",
            "improvement": "ප්‍රගතිය",
            "improved": "වැඩිදියුණු වී ඇත",
            "same": "වෙනසක් නැත",
            "dropped": "අඩු වී ඇත",
            "take_test": "SkillScan පරීක්ෂණය ආරම්භ කරන්න",
            "logout": "ඉවත් වන්න",
            "progress_overview": "ප්‍රගති සාරාංශය",
            "topic_trend": "මාතෘකා ප්‍රවණතාවය",
            "last_score": "අවසාන ලකුණ",
            "previous_score": "පෙර ලකුණ",
            "trend": "ප්‍රවණතාවය",
            "current_streak": "අඛණ්ඩ ඉගෙනුම් දින",
            "longest_streak": "දිගම අඛණ්ඩ දින",
            "leaderboard": "ප්‍රමුඛ ලැයිස්තුව",
            "goal_completed_today": "අද ඉලක්කය සම්පූර්ණයි",
            "complete_one_activity_today": "අද එක ක්‍රියාවක් සම්පූර්ණ කරන්න",
        },
    }
    language = "si" if student.medium == "Sinhala" else "en"
    text = ui_text[language]
    today_start = datetime.combine(date.today(), datetime.min.time())
    completed_activity_today = (
        StudentResult.query.filter(
            StudentResult.student_id == student.id,
            StudentResult.created_at >= today_start,
        ).first()
        is not None
        or PracticeAttempt.query.filter(
            PracticeAttempt.student_id == student.id,
            PracticeAttempt.created_at >= today_start,
        ).first()
        is not None
    )
    level_translations = {
        "Foundation Weak": "පදනම දුර්වල",
        "Basic Learner": "මූලික ඉගෙනුමකරු",
        "Developing Learner": "වර්ධනය වන ඉගෙනුමකරු",
        "Strong Learner": "ශක්තිමත් ඉගෙනුමකරු",
        "Advanced Learner": "උසස් ඉගෙනුමකරු",
    }

    def display_level(level: str) -> str:
        if language == "si":
            return level_translations.get(level, level)
        return level

    topic_rows = ""
    if latest_result:
        latest_topics = (
            StudentTopicPerformance.query.filter_by(student_result_id=latest_result.id)
            .order_by(StudentTopicPerformance.id.asc())
            .all()
        )
        medium_key = "en" if student.medium == "English" else "si"
        topic_rows = "".join(
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'>{getattr(topic, f'topic_{medium_key}')}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{topic.correct_count}/{topic.total_count}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{topic.percentage}%</td>
              <td style='border:1px solid #ccc;padding:8px;'>{getattr(topic, f'status_{medium_key}')}</td>
            </tr>
            """
            for topic in latest_topics
        )

    history_rows = "".join(
        f"""
        <tr>
          <td style='border:1px solid #ccc;padding:8px;'>{result.created_at.strftime('%Y-%m-%d %H:%M:%S')}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{result.score}%</td>
          <td style='border:1px solid #ccc;padding:8px;'>{display_level(result.level)}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{result.correct_answers}/{result.total_questions}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{result.medium}</td>
        </tr>
        """
        for result in result_history
    )

    practice_medium_key = "en" if student.medium == "English" else "si"
    topic_history: dict[str, list[PracticeAttempt]] = {}
    all_practice_attempts = (
        PracticeAttempt.query.filter_by(student_id=student.id)
        .order_by(PracticeAttempt.created_at.desc(), PracticeAttempt.id.desc())
        .all()
    )
    for attempt in all_practice_attempts:
        topic_key = attempt.topic_en
        topic_history.setdefault(topic_key, []).append(attempt)

    improvement_by_attempt: dict[int, str] = {}
    for attempts_for_topic in topic_history.values():
        if len(attempts_for_topic) < 2:
            continue
        latest_attempt = attempts_for_topic[0]
        previous_attempt = attempts_for_topic[1]
        if latest_attempt.score > previous_attempt.score:
            improvement_by_attempt[latest_attempt.id] = text["improved"]
        elif latest_attempt.score < previous_attempt.score:
            improvement_by_attempt[latest_attempt.id] = text["dropped"]
        else:
            improvement_by_attempt[latest_attempt.id] = text["same"]

    practice_summary_rows = "".join(
        f"""
        <div class='practice-summary-row'>
          <div class='practice-topic-icon'>√x</div>
          <div class='practice-topic-main'>
            <strong>{getattr(attempt, f'topic_{practice_medium_key}')}</strong>
            <span>{'Practice attempt' if language == 'en' else 'අභ්‍යාස උත්සාහය'}</span>
          </div>
          <div class='practice-score'>{attempt.score}%</div>
          <div class='practice-status {('improved' if improvement_by_attempt.get(attempt.id) == text['improved'] else 'dropped' if improvement_by_attempt.get(attempt.id) == text['dropped'] else 'same')}'>{improvement_by_attempt.get(attempt.id, text['same'])}</div>
        </div>
        """
        for attempt in practice_attempts
    )
    chart_result_history = list(reversed(result_history))
    chart_labels = [item.created_at.strftime("%Y-%m-%d") for item in chart_result_history]
    chart_result_scores = [item.score for item in chart_result_history]

    chart_practice_history = list(reversed(all_practice_attempts))
    chart_practice_points = [
        {"x": attempt.created_at.strftime("%Y-%m-%d"), "y": attempt.score}
        for attempt in chart_practice_history
    ]

    topic_trend_rows = []
    for topic_key, attempts_for_topic in topic_history.items():
        if len(attempts_for_topic) < 2:
            continue
        latest_attempt = attempts_for_topic[0]
        previous_attempt = attempts_for_topic[1]
        if latest_attempt.score > previous_attempt.score:
            trend_symbol = "↑"
            trend_text = text["improved"]
        elif latest_attempt.score < previous_attempt.score:
            trend_symbol = "↓"
            trend_text = text["dropped"]
        else:
            trend_symbol = "→"
            trend_text = text["same"]

        topic_name = getattr(latest_attempt, f"topic_{practice_medium_key}")
        topic_trend_rows.append(
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'>{topic_name}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{latest_attempt.score}%</td>
              <td style='border:1px solid #ccc;padding:8px;'>{previous_attempt.score}%</td>
              <td style='border:1px solid #ccc;padding:8px;'>{trend_symbol} {trend_text}</td>
            </tr>
            """
        )

    recommendations = get_student_recommendations(student.id)
    weak_topics_for_dashboard = get_student_weak_topics(student.id)
    generate_student_revision_queue(student.id)
    revision_due_items = StudentRevisionQueue.query.filter_by(student_id=student.id, is_completed=False).filter(StudentRevisionQueue.due_date <= date.today()).order_by(StudentRevisionQueue.priority_score.desc()).limit(50).all()
    revision_due_count = len(revision_due_items)
    revision_weak_topic = revision_due_items[0].skill_code if revision_due_items else (weak_topics_for_dashboard[0].get("topic_en") if weak_topics_for_dashboard else "None")
    revision_estimated_minutes = revision_due_count * 8
    recommended_dynamic_questions = get_dynamic_practice_questions(student.id, limit=10)
    class_tests = ClassTest.query.filter_by(class_id=student.class_id).order_by(ClassTest.test_date.asc(), ClassTest.id.asc()).all() if student.class_id else []
    upcoming_tests = [item for item in class_tests if item.test_date >= date.today()]
    next_test = upcoming_tests[0] if upcoming_tests else None
    rec_rows = "".join(
        f"""
        <tr>
          <td style='border:1px solid #ccc;padding:8px;'>{rec['topic_si'] if language == 'si' else rec['topic_en']}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{rec['mastery_level_si'] if language == 'si' else rec['mastery_level_en']}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{rec['action_si'] if language == 'si' else rec['action_en']}</td>
          <td style='border:1px solid #ccc;padding:8px;'><a href='{rec['action_url']}'>{rec['action_si'] if language == 'si' else rec['action_en']}</a></td>
        </tr>
        """
        for rec in recommendations
    )

    next_rec = get_student_next_recommendation(student.id)
    continue_cards = []
    default_module_cover = "/static/images/default-module-cover.jpg"
    progress_seed = [22, 35, 48, 57, 63, 71, 84, 90]
    modules = (
        db.session.query(SyllabusModule, SyllabusTerm, SubjectMaster)
        .join(SyllabusTerm, SyllabusModule.term_id == SyllabusTerm.id)
        .join(
            SubjectMaster,
            db.and_(
                SubjectMaster.grade == SyllabusTerm.grade,
                db.or_(
                    SubjectMaster.subject_code == SyllabusTerm.subject,
                    SubjectMaster.subject_name_en == SyllabusTerm.subject,
                    SubjectMaster.subject_name_si == SyllabusTerm.subject,
                ),
            ),
        )
        .filter(SubjectMaster.grade == normalize_grade(student.grade))
        .filter(SubjectMaster.is_active.is_(True))
        .order_by(SyllabusTerm.term_number.asc(), SyllabusModule.module_order.asc())
        .limit(5)
        .all()
    )
    for index, (module, _, subject) in enumerate(modules):
        progress_value = progress_seed[index % len(progress_seed)]
        module_image = module.image_si_url if student.medium == "Sinhala" else module.image_en_url
        module_image = (module_image or "").strip() or default_module_cover
        module_name = module.module_name_si if student.medium == "Sinhala" else module.module_name_en
        subject_name = subject.subject_name_si if student.medium == "Sinhala" else subject.subject_name_en
        continue_label = "ඉදිරියට" if language == "si" else "Continue"
        continue_cards.append(
            f"<article class='continue-module-card'><img src='{escape(module_image)}' alt='{escape(module_name)}' class='continue-module-cover' loading='lazy'><div class='continue-module-body'><h4 class='continue-module-title'>{escape(module_name)}</h4><p class='continue-module-subject'>{escape(subject_name)}</p><div class='continue-module-progress-row'><span>{progress_value}%</span><button type='button' class='continue-module-play' aria-label='{escape(continue_label)}'>▶</button></div><div class='continue-module-progress-bar'><span class='continue-module-progress-fill' style='width:{progress_value}%;'></span></div></div></article>"
        )
    rec_type_label = {
        "continue_lesson": "Continue" if language == "en" else "ඉදිරියට",
        "revision": "Revision" if language == "en" else "නැවත අධ්‍යයනය",
        "practice": "Practice" if language == "en" else "පුහුණුව",
        "challenge": "Challenge" if language == "en" else "අභියෝගය",
        "next_chapter": "Next Chapter" if language == "en" else "ඊළඟ පරිච්ඡේදය",
    }
    if next_rec:
        rec_progress = int(next_rec.get("progress_percent") or 0)
        rec_title = next_rec.get("recommended_lesson_si") if language == "si" else next_rec.get("recommended_lesson_en")
        rec_reason = next_rec.get("reason_si") if language == "si" else next_rec.get("reason_en")
        rec_type = str(next_rec.get("type") or "continue_lesson")
        continue_cards.insert(
            0,
            f"<article class='continue-module-card' style='border:2px solid #93c5fd;'><img src='/static/images/default-module-cover.jpg' alt='Recommended' class='continue-module-cover' loading='lazy'><div class='continue-module-body'><h4 class='continue-module-title'>{escape(str(rec_title or ''))}</h4><p class='continue-module-subject'>{escape(rec_type_label.get(rec_type, rec_type))} • {escape(str(next_rec.get('mastery_status') or ''))}</p><p class='continue-module-subject'>Reason: {escape(str(rec_reason or ''))}</p><p class='continue-module-subject'>~{int(next_rec.get('estimated_time') or 10)} min</p><div class='continue-module-progress-row'><span>{rec_progress}%</span><a href='{escape(str(next_rec.get('next_url') or '#'))}' class='continue-module-play' aria-label='Continue' style='display:inline-flex;align-items:center;justify-content:center;text-decoration:none;'>▶</a></div><div class='continue-module-progress-bar'><span class='continue-module-progress-fill' style='width:{rec_progress}%;'></span></div></div></article>",
        )
    continue_empty = "මෙම ශ්‍රේණියට මොඩියුල නොමැත." if language == "si" else "No modules available for your grade."
    continue_html = "".join(continue_cards) or f"<div class='card' style='padding:16px;'>{continue_empty}</div>"
    recommended_practice_card = ""
    if weak_topics_for_dashboard:
        top_weak = weak_topics_for_dashboard[0]
        recommended_practice_card = (
            f"<div class='card' style='margin-top:10px'><h3>{'නිර්දේශිත පුහුණුව' if language=='si' else 'Recommended Practice'}</h3>"
            f"<p><strong>{'දුර්වල මාතෘකාව' if language=='si' else 'Weak Topic'}:</strong> {escape(str(top_weak.get('topic_si') if language=='si' else top_weak.get('topic')))}</p>"
            f"<p><strong>{'නිර්දේශිත ප්‍රශ්න' if language=='si' else 'Recommended Questions'}:</strong> {len(recommended_dynamic_questions)}</p>"
            f"<p><strong>{'Mastery Badge'}:</strong> {escape(str(top_weak.get('mastery_badge') or 'WEAK'))}</p>"
            f"<a href='/student/recommended-practice' style='display:inline-block;background:#2563eb;color:#fff;padding:8px 12px;border-radius:8px;text-decoration:none;'>{'පුහුණුව අරඹන්න' if language=='si' else 'Start Practice'}</a></div>"
        )

    latest_html = ""
    if latest_result:
        latest_html = f"""
        <h2>{text["latest_result"]}</h2>
        <p><strong>{text["date"]}:</strong> {latest_result.created_at.strftime('%Y-%m-%d %H:%M:%S')}</p>
        <p><strong>{text["score"]}:</strong> {latest_result.score}%</p>
        <p><strong>{text["level"]}:</strong> {display_level(latest_result.level)}</p>
        <p><strong>{text["correct_answers"]}:</strong> {latest_result.correct_answers}/{latest_result.total_questions}</p>
        """
    else:
        latest_html = f"<h2>{text['latest_result']}</h2><p>No results yet.</p>"

    return f"""
    <!doctype html><html lang='{'si' if language == 'si' else 'en'}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{text["dashboard"]}</title><script src='https://cdn.jsdelivr.net/npm/chart.js'></script>
    <style>body{{margin:0;font-family:Inter,Arial,sans-serif;background:#edf2fa;color:#0f172a}}.app{{display:grid;grid-template-columns:252px 1fr;min-height:100vh}}.side{{background:linear-gradient(180deg,#061a4f 0%,#0f347a 55%,#123f91 100%);color:#dbeafe;padding:8px 14px 18px}}.sidebar-brand{{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:8px 10px 14px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.10);margin:0 0 12px}}.sidebar-brand img{{width:82px;height:82px;object-fit:contain;display:block;margin:0 auto 6px}}.sidebar-brand-title{{color:#ffffff;font-size:12px;font-weight:700;line-height:1.2;white-space:nowrap;text-align:center}}.sidebar-nav{{display:flex;flex-direction:column;gap:0}}.nav-section-title{{margin:14px 6px 5px;font-size:10px;font-weight:800;letter-spacing:.08em;color:rgba(219,234,254,.58);text-transform:uppercase}}.nav-link{{display:flex;align-items:center;gap:10px;min-height:32px;padding:6px 10px;margin:2px 0;border-radius:10px;color:#eaf2ff;text-decoration:none;font-size:14px;font-weight:650;background:transparent;transition:160ms ease}}.nav-link:hover,.nav-link.active{{background:rgba(59,130,246,.38);box-shadow:inset 0 0 0 1px rgba(255,255,255,.08)}}.nav-icon{{width:18px;height:18px;flex:0 0 18px;opacity:.95;color:#dbeafe}}.nav-icon svg{{width:18px;height:18px;display:block;stroke:currentColor;stroke-width:1.9;fill:none;stroke-linecap:round;stroke-linejoin:round}}.side-footer-link{{display:flex;align-items:center;gap:10px;margin-top:12px;padding:8px 10px;color:rgba(219,234,254,.92);text-decoration:none;font-size:13px}}.main{{padding:0 16px 8px;background:#edf2fa}}.dashboard-content,.dashboard-shell,.main-content{{background:#edf2fa}}.dashboard-content-inner{{width:100%;max-width:none}}.card{{background:rgba(255,255,255,0.18);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);border:1px solid rgba(255,255,255,0.10);border-radius:18px;box-shadow:0 4px 14px rgba(15,23,42,.03),inset 0 1px 0 rgba(255,255,255,.08)}}.top{{background:rgba(255,255,255,0.12);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.08);border-radius:18px;box-shadow:0 4px 14px rgba(15,23,42,.025),inset 0 1px 0 rgba(255,255,255,.06)}}.dashboard-topbar{{display:flex;align-items:center;justify-content:space-between;margin-bottom:0px;padding:0;margin-top:0px}}.dashboard-topbar-spacer{{flex:1}}.top{{width:calc(100% - 330px);max-width:600px;min-height:64px;padding:6px 18px;margin-top:-20px;display:flex;align-items:center;gap:18px}}.greeting-left{{display:flex;align-items:center;gap:14px;min-width:0}}.student-avatar{{width:58px;height:58px;border-radius:50%;object-fit:cover;border:3px solid rgba(255,255,255,0.9);box-shadow:0 8px 22px rgba(15,23,42,0.16);background:linear-gradient(135deg,#dbeafe,#eff6ff);display:flex;align-items:center;justify-content:center;color:#1e3a8a;font-weight:800;font-size:20px;overflow:hidden}}.greeting-copy h2{{margin:0;font-size:22px;line-height:1.15}}.greeting-copy small{{display:block;margin-top:3px;color:#64748b}}.change-photo-link{{display:inline-block;margin-top:3px;font-size:12px;font-weight:700;color:#2563eb;text-decoration:none;border:0;background:transparent;cursor:pointer;padding:0;width:auto;min-height:auto}}.header-actions{{display:flex;align-items:center;gap:4px;margin-left:auto;background:transparent}}.header-icon-btn,.header-action-btn{{width:36px;height:36px;border:0;border-radius:12px;background:rgba(255,255,255,0.18);color:#0f172a;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;position:relative;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,0.15);box-shadow:none}}.header-icon-btn:hover,.header-action-btn:hover{{background:rgba(255,255,255,0.28)}}.header-icon-btn svg,.header-action-btn svg,.student-menu-btn .menu-caret,.student-mini-profile .menu-caret{{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}}.notification-badge{{position:absolute;top:-3px;right:-3px;min-width:14px;height:14px;border-radius:999px;background:#ef4444;color:#fff;font-size:9px;font-weight:800;display:flex;align-items:center;justify-content:center;border:2px solid #fff}}.country-flag-wrap{{width:36px;height:36px;border-radius:12px;background:rgba(255,255,255,0.18);display:flex;align-items:center;justify-content:center;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,0.15);box-shadow:none}}.country-flag-img{{width:20px;height:14px;object-fit:cover;border-radius:2px;display:block}}.student-menu{{position:relative}}.student-menu-btn,.student-mini-profile{{border:0;background:rgba(255,255,255,0.18);border-radius:16px;padding:6px 10px;display:flex;align-items:center;gap:6px;cursor:pointer;color:#0f172a;min-height:34px;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,0.15);box-shadow:none}}.student-menu-btn:hover,.student-mini-profile:hover{{background:rgba(255,255,255,0.28)}}.header-avatar{{width:24px;height:24px;border-radius:50%;object-fit:cover;background:#dbeafe;color:#1e3a8a;font-size:10px;font-weight:800;display:flex;align-items:center;justify-content:center;overflow:hidden;flex:0 0 auto}}.student-menu-copy{{text-align:left;line-height:1.15}}.student-menu-copy strong{{display:block;font-size:11px;line-height:1.1;font-weight:800;max-width:140px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.student-menu-copy small{{display:block;font-size:9px;line-height:1;color:#64748b}}.student-dropdown,.notification-dropdown{{display:none;position:absolute;right:0;top:calc(100% + 8px);width:210px;background:rgba(255,255,255,.82);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border:1px solid rgba(255,255,255,.55);border-radius:16px;box-shadow:0 14px 34px rgba(15,23,42,0.12);padding:8px;z-index:9999}}.notification-dropdown{{width:230px;right:46px}}.student-dropdown.open,.notification-dropdown.open{{display:block}}.student-dropdown a,.student-dropdown button,.notification-dropdown div{{width:100%;border:0;background:transparent;padding:10px 12px;border-radius:10px;text-align:left;color:#0f172a;font-weight:650;cursor:pointer;text-decoration:none;display:block}}.student-dropdown a:hover,.student-dropdown button:hover{{background:#eff6ff}}.grid{{width:100%;display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:8px 0 14px}}.card{{padding:14px}}.kpi-card{{position:relative;overflow:hidden;border-radius:18px;padding:12px 14px;min-height:72px;border:1px solid rgba(255,255,255,.32);box-shadow:0 8px 18px rgba(15,23,42,.05)}}.kpi-title{{font-size:11px;font-weight:700;color:#475569;margin-bottom:6px;line-height:1.2}}.kpi-value{{font-size:18px;font-weight:850;color:#0f172a;line-height:1}}.kpi-subtitle{{margin-top:4px;font-size:10px;font-weight:600;color:#64748b;line-height:1.2}}.kpi-icon{{position:absolute;top:10px;right:10px;width:28px;height:28px;border-radius:10px;display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,.55);box-shadow:0 4px 12px rgba(15,23,42,.05);font-size:14px}}.kpi-blue{{background:linear-gradient(135deg,rgba(219,234,254,.75),rgba(191,219,254,.45));}}.kpi-gold{{background:linear-gradient(135deg,rgba(254,243,199,.78),rgba(253,230,138,.42));}}.kpi-pink{{background:linear-gradient(135deg,rgba(252,231,243,.72),rgba(233,213,255,.45));}}.kpi-green{{background:linear-gradient(135deg,rgba(209,250,229,.72),rgba(187,247,208,.42));}}.kpi-orange{{background:linear-gradient(135deg,rgba(255,237,213,.76),rgba(254,215,170,.44));}}.dashboard-main-grid{{display:grid;grid-template-columns:minmax(0,1fr) 430px;gap:24px;align-items:start}}.dashboard-left-column{{display:flex;flex-direction:column;gap:10px;min-width:0;overflow:hidden}}.dashboard-right-column{{display:flex;flex-direction:column;gap:20px;align-self:start;padding-top:0;margin-top:0;min-width:0;position:relative;z-index:2}}.today-schedule-card{{margin-top:0 !important;align-self:stretch}}.dashboard-left-column .grid,.dashboard-left-column .continue-learning-section{{width:100% !important;max-width:100% !important;box-sizing:border-box}}table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid rgba(148,163,184,.28);text-align:left}}.photo-modal{{display:none;position:fixed;inset:0;background:rgba(15,23,42,0.45);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);z-index:99999;align-items:center;justify-content:center;padding:16px}}.photo-modal.is-open{{display:flex !important}}.photo-modal-card{{position:relative;width:min(100%,460px);background:linear-gradient(160deg,rgba(255,255,255,0.95),rgba(239,246,255,0.9));border:1px solid rgba(191,219,254,0.8);border-radius:24px;padding:24px;box-shadow:0 26px 70px rgba(15,23,42,0.24)}}.photo-modal-close{{position:absolute;right:14px;top:14px;border:0;background:#eff6ff;color:#1d4ed8;border-radius:999px;width:34px;height:34px;font-size:22px;cursor:pointer;display:flex;align-items:center;justify-content:center}}.photo-modal h3{{margin:0 0 4px;font-size:22px;color:#0f172a}}.photo-modal-help{{margin:0 0 16px;color:#475569;font-size:13px;line-height:1.45}}.upload-picker{{display:block;border:2px dashed #93c5fd;border-radius:18px;padding:18px 14px;text-align:center;background:linear-gradient(180deg,#f8fbff,#eff6ff);cursor:pointer;transition:all .2s ease}}.upload-picker:hover{{border-color:#2563eb;transform:translateY(-1px)}}.upload-picker svg{{width:34px;height:34px;color:#2563eb}}.upload-picker strong{{display:block;margin-top:8px;font-size:15px;color:#0f172a}}.upload-picker span{{display:block;margin-top:3px;font-size:13px;color:#475569}}.upload-picker small{{display:block;margin-top:6px;color:#64748b}}#profilePhotoInput{{position:absolute;opacity:0;pointer-events:none}}.image-preview{{margin-top:12px;display:none;justify-content:center}}.image-preview img{{width:104px;height:104px;border-radius:50%;object-fit:cover;border:4px solid #bfdbfe;box-shadow:0 8px 20px rgba(37,99,235,.2)}}#cameraStream,#cameraPreview{{width:100%;border-radius:16px;background:#0f172a;display:none;margin-top:12px;object-fit:cover;max-height:240px}}.photo-modal-actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}}.photo-btn{{border:0;border-radius:12px;min-height:42px;padding:10px 14px;font-weight:700;cursor:pointer;font-size:14px}}.photo-btn.primary{{background:#2563eb;color:#fff;flex:1 1 150px}}.photo-btn.secondary{{background:#dbeafe;color:#1e40af;flex:1 1 150px}}.photo-btn.ghost{{background:transparent;border:1px solid #cbd5e1;color:#334155;flex:1 1 100px}}.continue-learning-section{{width:100%;max-width:none;margin-top:12px;margin-bottom:14px;background:#ffffff;border-radius:16px;padding:10px 14px;box-shadow:0 4px 12px rgba(15,23,42,0.06)}}.continue-learning-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}}.continue-learning-title-wrap{{text-align:left}}.continue-learning-title{{margin:0;font-size:18px;font-weight:800}}.continue-learning-subtitle{{margin-top:2px;font-size:11px;color:#64748b}}.continue-learning-view-all{{border:0;background:#eff6ff;color:#2563eb;border-radius:10px;padding:6px 12px;font-size:11px;font-weight:800;cursor:pointer}}.continue-learning-grid{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;overflow:hidden}}.continue-module-card{{background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 4px 12px rgba(15,23,42,0.06);width:100% !important;min-width:0 !important;max-width:none !important;height:165px}}.continue-module-cover{{width:100%;height:90px;object-fit:cover;display:block}}.continue-module-body{{padding:5px 6px}}.continue-module-title{{font-size:10px;font-weight:800;line-height:1.1;margin:0 0 3px}}.continue-module-subject{{font-size:8px;color:#64748b;margin:0 0 5px}}.continue-module-progress-row{{display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;font-size:8px}}.continue-module-progress-bar{{height:3px;border-radius:999px;background:#e5e7eb;overflow:hidden}}.continue-module-progress-fill{{display:block;height:100%;background:linear-gradient(90deg,#2563eb,#06b6d4)}}.continue-module-play{{width:18px;height:18px;border-radius:50%;border:0;background:#0f172a;color:#fff;cursor:pointer;font-size:8px;line-height:1}}.student-insights-row{{display:grid;grid-template-columns:1fr 1.45fr;gap:14px;margin:14px 0 16px}}.insight-card{{background:rgba(255,255,255,.88);border:1px solid rgba(203,213,225,.72);border-radius:22px;box-shadow:0 16px 34px rgba(15,23,42,.07);padding:16px}}.insight-card-header{{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:10px}}.insight-card-title{{margin:0;font-size:20px;font-weight:800;line-height:1.2;color:#0f172a}}.insight-card-subtitle{{margin:4px 0 0;font-size:12px;color:#64748b}}.analytics-pill{{display:inline-flex;align-items:center;justify-content:center;padding:7px 12px;border-radius:999px;border:1px solid rgba(148,163,184,.38);background:rgba(248,250,252,.95);font-size:11px;font-weight:750;color:#1e293b}}.subject-row{{display:grid;grid-template-columns:28px 1fr 34px 90px 38px;gap:10px;align-items:center;padding:9px 0;border-bottom:1px solid rgba(226,232,240,.8)}}.subject-row:last-child{{border-bottom:0}}.subject-icon{{width:24px;height:24px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#1f2937}}.subject-name{{font-size:15px;font-weight:650;color:#0f172a}}.subject-grade{{font-size:20px;font-weight:700;color:#0f172a;text-align:center}}.subject-progress-track{{height:6px;border-radius:999px;background:#e2e8f0;overflow:hidden}}.subject-progress-fill{{display:block;height:100%;border-radius:999px}}.subject-percent{{font-size:18px;font-weight:700;color:#334155;text-align:right}}.subject-report-link{{display:inline-flex;margin-top:12px;font-size:16px;font-weight:800;color:#2563eb;text-decoration:none}}.subject-report-link:hover{{text-decoration:underline}}.analytics-chart-wrap{{margin-top:2px;border-radius:16px;padding:10px 8px 4px;background:linear-gradient(180deg,rgba(248,250,252,.94),rgba(241,245,249,.65));border:1px solid rgba(226,232,240,.8)}}.analytics-chart{{width:100%;height:auto;display:block}}.analytics-chart .grid-line{{stroke:#e2e8f0;stroke-width:1}}.analytics-chart .axis-label{{fill:#64748b;font-size:10px;font-weight:600}}.analytics-chart .area-fill{{fill:url(#analyticsAreaGradient)}}.analytics-chart .line-path{{fill:none;stroke:#2563eb;stroke-width:2.8}}.analytics-chart .point{{fill:#2563eb;stroke:#ffffff;stroke-width:1.8}}.analytics-chart .focus-line{{stroke:#94a3b8;stroke-dasharray:4 4;opacity:.7}}.analytics-chart .focus-text{{fill:#2563eb;font-size:11px;font-weight:800}}.learning-stat-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:12px}}.learning-stat-chip{{display:flex;align-items:center;gap:8px;padding:10px 12px;border:1px solid rgba(203,213,225,.7);border-radius:12px;background:rgba(248,250,252,.96)}}.learning-stat-icon{{width:24px;height:24px;border-radius:8px;display:flex;align-items:center;justify-content:center;background:#dbeafe;color:#1d4ed8;font-size:12px}}.learning-stat-copy small{{display:block;font-size:11px;color:#64748b;line-height:1.2}}.learning-stat-copy strong{{display:block;font-size:23px;line-height:1.2;color:#0f172a}}.schedule-card{{background:rgba(255,255,255,.84);border:1px solid rgba(203,213,225,.65);border-radius:22px;box-shadow:0 16px 30px rgba(15,23,42,.08);padding:14px 14px 10px}}.schedule-card-header{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:10px}}.schedule-card-title{{margin:0;font-size:15px;font-weight:800;line-height:1.25}}.schedule-card-subtitle{{margin:2px 0 0;font-size:10px;color:#64748b}}.schedule-view-link{{border:1px solid rgba(59,130,246,.18);background:#f8fbff;color:#2563eb;border-radius:999px;padding:4px 9px;font-size:10px;font-weight:700;text-decoration:none;white-space:nowrap}}.schedule-list{{position:relative;margin:0;padding:0;list-style:none}}.schedule-list::before{{content:'';position:absolute;left:11px;top:5px;bottom:8px;width:2px;background:linear-gradient(180deg,rgba(59,130,246,.16),rgba(14,165,233,.2))}}.schedule-item{{position:relative;display:grid;grid-template-columns:58px 28px 1fr auto;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid rgba(226,232,240,.75)}}.schedule-item:last-child{{border-bottom:0;padding-bottom:2px}}.schedule-dot{{position:absolute;left:7px;top:50%;transform:translateY(-50%);width:9px;height:9px;border-radius:50%;border:2px solid rgba(255,255,255,.92);box-shadow:0 0 0 3px rgba(255,255,255,.55)}}.schedule-time{{font-size:10px;font-weight:700;color:#0f172a;padding-left:20px;white-space:nowrap}}.schedule-icon{{width:24px;height:24px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:13px;color:#1d4ed8}}.schedule-content-title{{margin:0;font-size:11px;font-weight:750;line-height:1.2}}.schedule-content-subtitle{{margin:1px 0 0;font-size:9px;color:#64748b}}.schedule-action{{border:0;background:linear-gradient(135deg,#2563eb,#1d4ed8);color:#fff;border-radius:999px;padding:4px 9px;font-size:9px;font-weight:700;cursor:pointer;min-width:44px}}.schedule-icon-weekly,.schedule-icon-chapter{{background:#e0e7ff}}.schedule-icon-recorded{{background:#ede9fe}}.schedule-icon-live{{background:#dcfce7}}.schedule-icon-activity{{background:#fee2e2}}.progress-summary-card{{background:rgba(255,255,255,.82);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(203,213,225,.72);border-radius:22px;box-shadow:0 16px 30px rgba(15,23,42,.08);padding:16px}}.progress-summary-header{{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:14px}}.progress-summary-header h3{{margin:0;font-size:18px;font-weight:800}}.progress-term-pill{{display:inline-flex;align-items:center;justify-content:center;padding:6px 12px;border-radius:999px;border:1px solid rgba(148,163,184,.38);background:rgba(248,250,252,.9);font-size:11px;font-weight:700;color:#334155}}.progress-donut-wrap{{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}}.progress-donut{{position:relative;width:148px;height:148px;border-radius:50%;background:conic-gradient(#56c983 0 78%,#3b82f6 78% 93%,#d9dee7 93% 100%);display:flex;align-items:center;justify-content:center;flex:0 0 148px}}.progress-donut::before{{content:'';position:absolute;inset:16px;background:#fff;border-radius:50%;box-shadow:inset 0 0 0 1px rgba(226,232,240,.7)}}.progress-donut-center{{position:relative;z-index:1;text-align:center}}.progress-donut-center strong{{display:block;font-size:36px;line-height:1;font-weight:850;color:#0f172a}}.progress-donut-center span{{display:block;margin-top:4px;font-size:13px;font-weight:600;color:#64748b}}.progress-legend{{flex:1;min-width:180px;display:flex;flex-direction:column;gap:10px}}.progress-legend-row{{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:10px;font-size:14px;color:#475569}}.progress-dot{{width:10px;height:10px;border-radius:50%}}.progress-detail-link{{display:inline-flex;margin-top:14px;font-size:13px;font-weight:800;color:#2563eb;text-decoration:none}}.progress-detail-link:hover{{text-decoration:underline}}.practice-summary-card{{background:rgba(255,255,255,.88);border:1px solid rgba(203,213,225,.72);border-radius:22px;box-shadow:0 16px 34px rgba(15,23,42,.07);padding:16px}}.practice-summary-header{{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:12px}}.practice-summary-header h3{{margin:0;font-size:18px;font-weight:850}}.practice-summary-header p{{margin:3px 0 0;font-size:11px;color:#64748b}}.practice-summary-pill{{border:1px solid rgba(148,163,184,.35);background:#f8fbff;color:#334155;border-radius:999px;padding:5px 10px;font-size:11px;font-weight:800;white-space:nowrap}}.practice-summary-list{{display:flex;flex-direction:column;gap:8px}}.practice-summary-row{{display:grid;grid-template-columns:34px 1fr 52px 86px;gap:8px;align-items:center;padding:10px;border:1px solid rgba(226,232,240,.85);border-radius:16px;background:linear-gradient(135deg,rgba(255,255,255,.95),rgba(248,251,255,.78))}}.practice-topic-icon{{width:34px;height:34px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-weight:900;color:#2563eb;background:#dbeafe}}.practice-topic-main strong{{display:block;font-size:12px;line-height:1.2}}.practice-topic-main span{{display:block;margin-top:2px;font-size:10px;color:#64748b}}.practice-score{{font-size:13px;font-weight:900;color:#2563eb;text-align:right}}.practice-status{{border-radius:999px;padding:5px 8px;font-size:10px;font-weight:800;text-align:center}}.practice-status.improved{{background:#dcfce7;color:#16a34a}}.practice-status.same{{background:#e0f2fe;color:#0284c7}}.practice-status.dropped{{background:#fee2e2;color:#dc2626}}.practice-summary-link{{display:block;margin-top:12px;color:#2563eb;font-size:12px;font-weight:850;text-decoration:none}}.practice-summary-empty{{padding:16px;border:1px dashed rgba(148,163,184,.45);border-radius:14px;background:rgba(248,250,252,.85);font-size:11px;font-weight:700;color:#64748b;text-align:center}}@media(max-width:768px){{.progress-summary-card{{padding:14px}}.progress-summary-header h3{{font-size:16px}}.progress-term-pill{{font-size:10px;padding:5px 10px}}.progress-donut-wrap{{justify-content:center}}.progress-legend{{min-width:100%}}.progress-legend-row{{font-size:13px}}}}#captureBtn{{display:none}}@media(max-width:1100px){{.top{{width:100%;max-width:none}}.grid,.continue-learning-section{{width:100%}}}}@media(max-width:1000px){{.app{{grid-template-columns:1fr}}.grid{{grid-template-columns:repeat(2,1fr)}}.dashboard-main-grid{{grid-template-columns:1fr}}.dashboard-left-column{{order:1}}.dashboard-right-column{{order:2}}}}@media(max-width:900px){{.dashboard-topbar{{padding:0;align-items:flex-start}}.header-actions{{margin-left:auto;justify-content:flex-end;max-width:100%}}.student-insights-row{{grid-template-columns:1fr}}}}@media(max-width:768px){{.sidebar-brand-title{{font-size:10px}}.nav-link{{font-size:13px;padding:6px 9px;gap:8px;min-height:30px}}.nav-icon,.nav-icon svg{{width:16px;height:16px;flex-basis:16px}}.top{{align-items:flex-start;flex-direction:column;min-height:0}}.student-avatar{{width:52px;height:52px;font-size:16px}}.greeting-left{{gap:10px}}.student-menu-copy{{display:none}}.header-icon-btn{{width:34px;height:34px}}.photo-modal-card{{padding:20px}}.continue-learning-grid{{display:flex;overflow-x:auto;gap:14px;padding-bottom:4px}}.continue-module-card{{width:180px;min-width:180px;min-height:300px}}.schedule-item{{grid-template-columns:62px 26px 1fr auto}}}}</style>
    </head><body><div class='app'><aside class='side'><div class='sidebar-brand'><img src='/static/images/SLIS LOGO.png' alt='SLIS logo'><div class='sidebar-brand-title'>Spiral Learning Intelligence System</div></div>
    <a class='nav-link active' href='/student-dashboard'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='M3 10.5 12 3l9 7.5'></path><path d='M5 9.5V21h14V9.5'></path></svg></span><span>{text['dashboard']}</span></a>
    <div class='nav-section-title'>{'ඉගෙනුම' if language=='si' else 'LEARN'}</div>
    <a class='nav-link' href='/student/learning-path'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='M4 5h6a4 4 0 0 1 4 4v10H8a4 4 0 0 0-4 4z'></path><path d='M20 5h-6a4 4 0 0 0-4 4v10h6a4 4 0 0 1 4 4z'></path></svg></span><span>{'මගේ විෂයයන්' if language=='si' else 'My Subjects'}</span></a>
    <a class='nav-link' href='/student/tests'><span class='nav-icon'><svg viewBox='0 0 24 24'><rect x='3' y='7' width='13' height='10' rx='2'></rect><path d='m16 10 5-3v10l-5-3z'></path></svg></span><span>{'සජීවී පන්ති' if language=='si' else 'Live Classes'}</span></a>
    <a class='nav-link' href='/student/homework'><span class='nav-icon'><svg viewBox='0 0 24 24'><rect x='7' y='3' width='10' height='4' rx='1'></rect><rect x='5' y='6' width='14' height='15' rx='2'></rect><path d='M9 12h6M9 16h6'></path></svg></span><span>{'පැවරුම්' if language=='si' else 'Assignments'}</span></a>
    <a class='nav-link' href='/student/learning-path'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='M7 3h7l5 5v13H7z'></path><path d='M14 3v5h5M9 13h6M9 17h6'></path></svg></span><span>{'අධ්‍යයන ද්‍රව්‍ය' if language=='si' else 'Study Materials'}</span></a>
    <a class='nav-link' href='/student/tests'><span class='nav-icon'><svg viewBox='0 0 24 24'><rect x='4' y='4' width='16' height='16' rx='2'></rect><path d='m8 12 2.5 2.5L16 9'></path></svg></span><span>{'ඇගයීම්' if language=='si' else 'Assessments'}</span></a>
    <a class='nav-link' href='/test'><span class='nav-icon'><svg viewBox='0 0 24 24'><rect x='7' y='7' width='10' height='10' rx='2'></rect><path d='M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2'></path></svg></span><span>AI Tutor</span></a>
    <a class='nav-link' href='/leaderboard'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='M8 21h8'></path><path d='M12 17v4'></path><path d='M7 4h10v3a5 5 0 0 1-10 0z'></path><path d='M7 6H5a2 2 0 0 0 0 4h2M17 6h2a2 2 0 0 1 0 4h-2'></path></svg></span><span>{'ප්‍රමුඛතා වගුව' if language=='si' else 'Lead Table'}</span></a>
    <a class='nav-link' href='/student/learning-path'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='M3 6h8a3 3 0 0 1 3 3v11H6a3 3 0 0 0-3 3z'></path><path d='M21 6h-8a3 3 0 0 0-3 3v11h8a3 3 0 0 1 3 3z'></path></svg></span><span>{'පුස්තකාලය' if language=='si' else 'Library'}</span></a>
    <div class='nav-section-title'>{'සොයා බලන්න' if language=='si' else 'EXPLORE'}</div>
    <a class='nav-link' href='#'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='m12 2 8 4v12l-8 4-8-4V6z'></path><path d='m4 6 8 4 8-4M12 10v12'></path></svg></span><span>{'ව්‍යාපෘති' if language=='si' else 'Projects'}</span></a>
    <a class='nav-link' href='#'><span class='nav-icon'><svg viewBox='0 0 24 24'><circle cx='12' cy='8' r='5'></circle><path d='M8 13v7l4-2 4 2v-7'></path></svg></span><span>{'තරඟ' if language=='si' else 'Competitions'}</span></a>
    <a class='nav-link' href='#'><span class='nav-icon'><svg viewBox='0 0 24 24'><circle cx='8' cy='9' r='3'></circle><circle cx='16' cy='9' r='3'></circle><path d='M3 20a5 5 0 0 1 10 0M11 20a5 5 0 0 1 10 0'></path></svg></span><span>{'සමාජ සහ සංගම්' if language=='si' else 'Clubs & Societies'}</span></a>
    <a class='nav-link' href='#'><span class='nav-icon'><svg viewBox='0 0 24 24'><circle cx='12' cy='12' r='9'></circle><path d='m12 7 4 2-4 2-4-2 4-2zm0 4v6'></path></svg></span><span>{'වෘත්තීය මාර්ගෝපදේශනය' if language=='si' else 'Career Guidance'}</span></a>
    <div class='nav-section-title'>{'සහාය' if language=='si' else 'SUPPORT'}</div>
    <a class='nav-link' href='#'><span class='nav-icon'><svg viewBox='0 0 24 24'><rect x='3' y='5' width='18' height='14' rx='2'></rect><path d='m4 7 8 6 8-6'></path></svg></span><span>{'පණිවිඩ' if language=='si' else 'Messages'}</span></a>
    <a class='nav-link' href='#'><span class='nav-icon'><svg viewBox='0 0 24 24'><circle cx='12' cy='12' r='9'></circle><path d='M9.5 9a2.5 2.5 0 1 1 4.3 1.7c-.9.8-1.8 1.3-1.8 2.8'></path><circle cx='12' cy='17' r='1'></circle></svg></span><span>{'උදව් මධ්‍යස්ථානය' if language=='si' else 'Help Center'}</span></a>
    <a class='side-footer-link' href='/logout'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4'></path><path d='m16 17 5-5-5-5M21 12H9'></path></svg></span><span>{text['logout']}</span></a></aside>
    <main class='main'><div class='dashboard-topbar'><div class='dashboard-topbar-spacer'></div><div class='header-actions'><button class='header-icon-btn header-action-btn' type='button' id='headerSearchBtn' aria-label='Search'><svg viewBox='0 0 24 24'><circle cx='11' cy='11' r='7'></circle><path d='m20 20-3.5-3.5'></path></svg></button><button class='header-icon-btn header-action-btn notification-btn' type='button' id='notificationBtn' aria-label='Notifications'><svg viewBox='0 0 24 24'><path d='M15 17H5.5a1.5 1.5 0 0 1-1.2-2.4l1.1-1.4A6.7 6.7 0 0 0 6.8 9V8a5.2 5.2 0 1 1 10.4 0v1a6.7 6.7 0 0 0 1.4 4.2l1.1 1.4a1.5 1.5 0 0 1-1.2 2.4H15'></path><path d='M10 17a2 2 0 1 0 4 0'></path></svg><span class='notification-badge'>5</span></button><div class='notification-dropdown' id='notificationDropdown'><div>{'නව දැනුම්දීම් නොමැත' if language=='si' else 'No new notifications'}</div></div><button class='header-icon-btn header-action-btn' type='button' id='headerMessageBtn' aria-label='Messages'><svg viewBox='0 0 24 24'><path d='M4 6h16v9a2 2 0 0 1-2 2H9l-5 4V8a2 2 0 0 1 2-2z'></path></svg></button><div class='country-flag-wrap' aria-label='Sri Lanka'><img src='/static/images/sl-flag.png' alt='Sri Lanka' class='country-flag-img'></div><div class='student-menu'><button class='student-menu-btn student-mini-profile' type='button' id='studentMenuBtn' aria-haspopup='true' aria-expanded='false'><span class='header-avatar'>{f"<img src='{escape(profile_image_url)}' alt='Student photo' class='header-avatar'>" if profile_image_url else avatar_initials}</span><span class='student-menu-copy'><strong>{escape(student.name)}</strong><small>{f"{escape(str(student.grade))} ශ්‍රේණියේ ශිෂ්‍යයා" if language=='si' else f"Grade {escape(str(student.grade))} Student"}</small></span><svg viewBox='0 0 24 24' class='menu-caret'><path d='m6 9 6 6 6-6'></path></svg></button><div class='student-dropdown' id='studentDropdown'><a href='/student/profile'>{'මගේ පැතිකඩ' if language=='si' else 'My Profile'}</a><a href='/student/account-settings'>{'ගිණුම් සැකසුම්' if language=='si' else 'Account Settings'}</a><button type='button' id='changePhotoMenuBtn'>{'ඡායාරූපය වෙනස් කරන්න' if language=='si' else 'Change Photo'}</button><a href='/logout'>{'ඉවත් වන්න' if language=='si' else 'Logout'}</a></div></div></div></div><div class='top'><div class='greeting-left'><div class='student-avatar'>{f"<img src='{escape(profile_image_url)}' alt='Student photo' class='student-avatar'>" if profile_image_url else avatar_initials}</div><div class='greeting-copy'><h2>{'සුභ දිනක්, ' if language=='si' else 'Good Morning, '}{student.name}!</h2><small>{'ඉදිරියට යන්න. ඔබේ අනාගතය අද ගොඩනැගෙයි.' if language=='si' else 'Keep going. Your future is being built today.'}</small><button type='button' id='changePhotoBtn' class='change-photo-link' onclick='window.openStudentPhotoModal && window.openStudentPhotoModal();'>{'ඡායාරූපය වෙනස් කරන්න' if language=='si' else 'Change Photo'}</button></div></div></div>
    <div id='photoUploadModal' class='photo-modal' aria-hidden='true'><div class='photo-modal-card'><button type='button' id='closePhotoModal' class='photo-modal-close' onclick='window.closeStudentPhotoModal && window.closeStudentPhotoModal();' aria-label='Close'>×</button><h3>{'පැතිකඩ ඡායාරූපය යාවත්කාලීන කරන්න' if language=='si' else 'Update Profile Photo'}</h3><p class='photo-modal-help'>{'ඔබේ පැතිකඩට හොඳින් ගැළපෙන පැහැදිලි ඡායාරූපයක් තෝරන්න.' if language=='si' else 'Choose a clear, friendly photo that fits your learning profile.'}</p><form id='photoForm' method='post' action='/student/profile-photo' enctype='multipart/form-data'><label for='profilePhotoInput' class='upload-picker'><svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.8'><circle cx='12' cy='8' r='4'></circle><path d='M5 20c.4-3.2 3.1-5.5 7-5.5s6.6 2.3 7 5.5'></path></svg><strong>Choose a profile photo</strong><span>පැතිකඩ ඡායාරූපයක් තෝරන්න</span><small>JPG, PNG, WEBP up to 2MB</small></label><input id='profilePhotoInput' name='profile_photo' type='file' accept='image/jpeg,image/png,image/webp'><div id='filePreviewWrap' class='image-preview'><img id='filePreview' alt='Profile photo preview'></div><input type='hidden' id='cameraImageData' name='camera_image_data'><video id='cameraStream' autoplay playsinline></video><canvas id='cameraCanvas' style='display:none'></canvas><img id='cameraPreview' alt='Preview'><div class='photo-modal-actions'><button type='submit' class='photo-btn primary'>{'සුරකින්න' if language=='si' else 'Save Photo'}</button><button type='button' id='startCameraBtn' class='photo-btn secondary'>{'කැමරාව භාවිතා කරන්න' if language=='si' else 'Use Camera'}</button><button type='button' id='captureBtn' class='photo-btn secondary'>{'ඡායාරූපය ලබාගන්න' if language=='si' else 'Capture'}</button><button type='button' class='photo-btn ghost' onclick='window.closeStudentPhotoModal && window.closeStudentPhotoModal();'>{'අවලංගු කරන්න' if language=='si' else 'Cancel'}</button></div></form></div></div>
    {f"<p style='padding:10px;border-radius:8px;background:#fff3cd;color:#7a4f00;border:1px solid #ffe69c;'>{expired_message}</p>" if expired_message else ""}
    <div class='dashboard-content-inner'><section class='dashboard-main-grid'><div class='dashboard-left-column'><section class='grid'><div class='card kpi-card kpi-blue'><div class='kpi-title'>{text['xp']}</div><div class='kpi-value'>{student.xp}</div><div class='kpi-subtitle'>{'Keep growing!' if language=='en' else 'ඉදිරියටම යමු!'}</div><div class='kpi-icon'>⭐</div></div><div class='card kpi-card kpi-gold'><div class='kpi-title'>{text['level']}</div><div class='kpi-value'>{student.level}</div><div class='kpi-subtitle'>{'Rise to new heights!' if language=='en' else 'ඉහළ මට්ටම් කරා යමු!'}</div><div class='kpi-icon'>🏆</div></div><div class='card kpi-card kpi-pink'><div class='kpi-title'>{text['current_streak']}</div><div class='kpi-value'>{student.current_streak or 0}</div><div class='kpi-subtitle'>{'Stay consistent daily!' if language=='en' else 'දිනපතා අඛණ්ඩව ඉගෙනගන්න!'}</div><div class='kpi-icon'>🔥</div></div><div class='card kpi-card kpi-green'><div class='kpi-title'>{text['latest_result']}</div><div class='kpi-value'>{latest_result.score if latest_result else 0}%</div><div class='kpi-subtitle'>{'You are improving!' if language=='en' else 'ඔබ ප්‍රගතිය කරමින්!'}</div><div class='kpi-icon'>🎯</div></div><div class='card kpi-card kpi-orange'><div class='kpi-title'>{text['progress_to_next_level']}</div><div class='kpi-value'>{student.xp % 100}%</div><div class='kpi-subtitle'>{'Next milestone ahead!' if language=='en' else 'ඊළඟ ඉලක්කය ඉදිරියේ!'}</div><div class='kpi-icon'>📈</div></div><div class='card kpi-card kpi-blue'><div class='kpi-title'>{'Revision Needed' if language=='en' else 'නැවත අධ්‍යයනය අවශ්‍යයි'}</div><div class='kpi-value'>{revision_due_count}</div><div class='kpi-subtitle'>{escape(str(revision_weak_topic))} • ~{revision_estimated_minutes} min</div><div class='kpi-icon'>🧠</div></div></section>
    <section class='continue-learning-section'><div class='continue-learning-header'><div class='continue-learning-title-wrap'><h3 class='continue-learning-title'>{'ඉදිරියට ඉගෙන ගන්න' if language=='si' else 'Continue Learning'}</h3><p class='continue-learning-subtitle'>{'ඔබ නතර කළ තැනින් නැවත ආරම්භ කරන්න' if language=='si' else 'Pick up where you left off'}</p></div><button type='button' class='continue-learning-view-all'>{'සියල්ල බලන්න' if language=='si' else 'View All'}</button></div><div class='continue-learning-grid'>{continue_html}</div></section><section class='student-insights-row'><div class='insight-card subjects-overview-card'><div class='insight-card-header'><div><h3 class='insight-card-title'>විෂය සාරාංශය</h3><p class='insight-card-subtitle'>මෙම වාරයේ ක්‍රියාකාරීත්වය</p></div></div><div class='subject-row'><span class='subject-icon' style='background:#dbeafe;color:#1d4ed8'>⚛</span><span class='subject-name'>Physics</span><span class='subject-grade'>A</span><span class='subject-progress-track'><span class='subject-progress-fill' style='width:85%;background:#22c55e'></span></span><span class='subject-percent'>85%</span></div><div class='subject-row'><span class='subject-icon' style='background:#dbeafe;color:#2563eb'>∑</span><span class='subject-name'>Mathematics</span><span class='subject-grade'>A-</span><span class='subject-progress-track'><span class='subject-progress-fill' style='width:78%;background:#34d399'></span></span><span class='subject-percent'>78%</span></div><div class='subject-row'><span class='subject-icon' style='background:#fee2e2;color:#dc2626'>🧪</span><span class='subject-name'>Chemistry</span><span class='subject-grade'>B+</span><span class='subject-progress-track'><span class='subject-progress-fill' style='width:72%;background:#f97316'></span></span><span class='subject-percent'>72%</span></div><div class='subject-row'><span class='subject-icon' style='background:#dcfce7;color:#16a34a'>🧬</span><span class='subject-name'>Biology</span><span class='subject-grade'>A</span><span class='subject-progress-track'><span class='subject-progress-fill' style='width:82%;background:#22c55e'></span></span><span class='subject-percent'>82%</span></div><div class='subject-row'><span class='subject-icon' style='background:#ffedd5;color:#ea580c'>A</span><span class='subject-name'>English</span><span class='subject-grade'>B+</span><span class='subject-progress-track'><span class='subject-progress-fill' style='width:74%;background:#fb923c'></span></span><span class='subject-percent'>74%</span></div><div class='subject-row'><span class='subject-icon' style='background:#ede9fe;color:#7c3aed'>⌨</span><span class='subject-name'>ICT</span><span class='subject-grade'>A-</span><span class='subject-progress-track'><span class='subject-progress-fill' style='width:80%;background:#3b82f6'></span></span><span class='subject-percent'>80%</span></div><a class='subject-report-link' href='/student/results'>සම්පූර්ණ වාර්තාව බලන්න</a></div><div class='insight-card learning-analytics-card'><div class='insight-card-header'><div><h3 class='insight-card-title'>ඉගෙනුම් විශ්ලේෂණය</h3><p class='insight-card-subtitle'>ඔබේ ඉගෙනුම් ක්‍රියාකාරකම්</p></div><span class='analytics-pill'>මෙම සතිය</span></div><div class='analytics-chart-wrap'><svg class='analytics-chart' viewBox='0 0 520 230' role='img' aria-label='Learning analytics chart'><defs><linearGradient id='analyticsAreaGradient' x1='0' y1='0' x2='0' y2='1'><stop offset='0%' stop-color='#60a5fa' stop-opacity='.36'></stop><stop offset='100%' stop-color='#60a5fa' stop-opacity='.06'></stop></linearGradient></defs><line class='grid-line' x1='44' y1='26' x2='492' y2='26'></line><line class='grid-line' x1='44' y1='69' x2='492' y2='69'></line><line class='grid-line' x1='44' y1='112' x2='492' y2='112'></line><line class='grid-line' x1='44' y1='155' x2='492' y2='155'></line><line class='grid-line' x1='44' y1='198' x2='492' y2='198'></line><text class='axis-label' x='18' y='202'>0h</text><text class='axis-label' x='18' y='159'>2h</text><text class='axis-label' x='18' y='116'>4h</text><text class='axis-label' x='18' y='73'>6h</text><text class='axis-label' x='18' y='30'>8h</text><path class='area-fill' d='M44 198 L74 145 L104 126 L134 129 L164 122 L194 96 L224 102 L254 126 L284 132 L314 116 L344 82 L374 72 L404 83 L434 102 L464 98 L492 126 L492 198 L44 198 Z'></path><path class='line-path' d='M44 198 L74 145 L104 126 L134 129 L164 122 L194 96 L224 102 L254 126 L284 132 L314 116 L344 82 L374 72 L404 83 L434 102 L464 98 L492 126'></path><line class='focus-line' x1='374' y1='50' x2='374' y2='198'></line><text class='focus-text' x='354' y='42'>6h 35m</text><circle class='point' cx='74' cy='145' r='3.8'></circle><circle class='point' cx='104' cy='126' r='3.8'></circle><circle class='point' cx='134' cy='129' r='3.8'></circle><circle class='point' cx='164' cy='122' r='3.8'></circle><circle class='point' cx='194' cy='96' r='3.8'></circle><circle class='point' cx='224' cy='102' r='3.8'></circle><circle class='point' cx='254' cy='126' r='3.8'></circle><circle class='point' cx='284' cy='132' r='3.8'></circle><circle class='point' cx='314' cy='116' r='3.8'></circle><circle class='point' cx='344' cy='82' r='3.8'></circle><circle class='point' cx='374' cy='72' r='5.2'></circle><circle class='point' cx='404' cy='83' r='3.8'></circle><circle class='point' cx='434' cy='102' r='3.8'></circle><circle class='point' cx='464' cy='98' r='3.8'></circle><circle class='point' cx='492' cy='126' r='3.8'></circle><text class='axis-label' x='64' y='216'>Mon</text><text class='axis-label' x='138' y='216'>Tue</text><text class='axis-label' x='210' y='216'>Wed</text><text class='axis-label' x='282' y='216'>Thu</text><text class='axis-label' x='360' y='216'>Fri</text><text class='axis-label' x='430' y='216'>Sat</text><text class='axis-label' x='486' y='216' text-anchor='end'>Sun</text></svg></div><div class='learning-stat-grid'><div class='learning-stat-chip'><span class='learning-stat-icon'>🕒</span><span class='learning-stat-copy'><small>Study Time</small><strong>26h 45m</strong></span></div><div class='learning-stat-chip'><span class='learning-stat-icon'>📖</span><span class='learning-stat-copy'><small>Lessons Completed</small><strong>28</strong></span></div><div class='learning-stat-chip'><span class='learning-stat-icon'>✅</span><span class='learning-stat-copy'><small>Quizzes Taken</small><strong>15</strong></span></div></div></div></section><div class='card'><h3>{text['topic_trend']}</h3><table><thead><tr><th>{'මාතෘකාව' if language=='si' else 'Topic'}</th><th>{text['last_score']}</th><th>{text['previous_score']}</th><th>{text['trend']}</th></tr></thead><tbody>{''.join(topic_trend_rows) if topic_trend_rows else "<tr><td colspan='4'>No topic trend data available.</td></tr>"}</tbody></table></div><div class='card' style='margin-top:10px'><h3>{text['result_history']}</h3><table><thead><tr><th>{text['date']}</th><th>{text['score']}</th><th>{text['level']}</th><th>{text['correct_answers']}</th><th>{text['medium']}</th></tr></thead><tbody>{history_rows if history_rows else "<tr><td colspan='5'>No results found.</td></tr>"}</tbody></table></div></div><aside class='dashboard-right-column'><div class='schedule-card today-schedule-card'><div class='schedule-card-header'><div><h3 class='schedule-card-title'>{'අද දින කාලසටහන' if language=='si' else "Today's Schedule"}</h3><p class='schedule-card-subtitle'>{datetime.now().strftime('%A, %d %b %Y')}</p></div><a class='schedule-view-link' href='/student/tests'>{'දිනදර්ශනය බලන්න' if language=='si' else 'View Calendar'}</a></div><ul class='schedule-list'><li class='schedule-item'><span class='schedule-dot' style='background:#2563eb'></span><div class='schedule-time'>08:00 AM</div><div class='schedule-icon schedule-icon-weekly'>📝</div><div><p class='schedule-content-title'>{'Weekly Test' if language=='en' else 'සතිපතා පරීක්ෂණය'}</p><p class='schedule-content-subtitle'>{'Mathematics' if language=='en' else 'ගණිතය'}</p></div><button class='schedule-action' type='button'>{'Start' if language=='en' else 'ආරම්භ'}</button></li><li class='schedule-item'><span class='schedule-dot' style='background:#8b5cf6'></span><div class='schedule-time'>09:30 AM</div><div class='schedule-icon schedule-icon-recorded'>🎬</div><div><p class='schedule-content-title'>{'Recorded Lesson' if language=='en' else 'පටිගත පාඩම'}</p><p class='schedule-content-subtitle'>{'Science' if language=='en' else 'විද්‍යාව'}</p></div><button class='schedule-action' type='button'>{'Watch' if language=='en' else 'නරඹන්න'}</button></li><li class='schedule-item'><span class='schedule-dot' style='background:#10b981'></span><div class='schedule-time'>11:00 AM</div><div class='schedule-icon schedule-icon-live'>📡</div><div><p class='schedule-content-title'>{'Live Class' if language=='en' else 'සජීවී පන්තිය'}</p><p class='schedule-content-subtitle'>{'English' if language=='en' else 'ඉංග්‍රීසි'}</p></div><button class='schedule-action' type='button'>{'Join' if language=='en' else 'එකතු වන්න'}</button></li><li class='schedule-item'><span class='schedule-dot' style='background:#f59e0b'></span><div class='schedule-time'>02:00 PM</div><div class='schedule-icon schedule-icon-chapter'>📘</div><div><p class='schedule-content-title'>{'Chapter Test' if language=='en' else 'අධ්‍යාය පරීක්ෂණය'}</p><p class='schedule-content-subtitle'>{'ICT' if language=='en' else 'තොරතුරු තාක්ෂණය'}</p></div><button class='schedule-action' type='button'>{'Start' if language=='en' else 'ආරම්භ'}</button></li><li class='schedule-item'><span class='schedule-dot' style='background:#ef4444'></span><div class='schedule-time'>04:00 PM</div><div class='schedule-icon schedule-icon-activity'>🎯</div><div><p class='schedule-content-title'>{'Activity' if language=='en' else 'ක්‍රියාකාරකම'}</p><p class='schedule-content-subtitle'>{'Sinhala' if language=='en' else 'සිංහල'}</p></div><button class='schedule-action' type='button'>{'Open' if language=='en' else 'විවෘත'}</button></li></ul></div><div class='progress-summary-card'><div class='progress-summary-header'><h3>ප්‍රගති සාරාංශය</h3><span class='progress-term-pill'>මෙම වාරය</span></div><div class='progress-donut-wrap'><div class='progress-donut'><div class='progress-donut-center'><strong>78%</strong><span>Overall</span></div></div><div class='progress-legend'><div class='progress-legend-row'><span class='progress-dot' style='background:#56c983'></span><span>Completed</span><strong>78%</strong></div><div class='progress-legend-row'><span class='progress-dot' style='background:#3b82f6'></span><span>In Progress</span><strong>15%</strong></div><div class='progress-legend-row'><span class='progress-dot' style='background:#d9dee7'></span><span>Not Started</span><strong>7%</strong></div></div></div><a class='progress-detail-link' href='/student/learning-path'>විස්තරාත්මක ප්‍රගතිය බලන්න</a></div>{recommended_practice_card}<div class='practice-summary-card'><div class='practice-summary-header'><div><h3>අවසන් අභ්‍යාස සාරාංශය</h3><p>ඔබේ අලුත්ම පුහුණු ක්‍රියාකාරකම්</p></div><span class='practice-summary-pill'>අලුත්ම 5</span></div><div class='practice-summary-list'>{practice_summary_rows if practice_summary_rows else "<div class='practice-summary-empty'>තවම අභ්‍යාස උත්සාහ නොමැත.</div>"}</div><a class='practice-summary-link' href='/student/learning-path'>සම්පූර්ණ වාර්තාව බලන්න</a></div><div class='card'><h3>{text['topic_performance']}</h3><table><thead><tr><th>Topic</th><th>Correct/Total</th><th>%</th><th>Status</th></tr></thead><tbody>{topic_rows if topic_rows else "<tr><td colspan='4'>No topic performance available.</td></tr>"}</tbody></table></div></aside></section></div></main></div>
    <script>
(function () {{
  const modal = document.getElementById("photoUploadModal");
  const closeBtn = document.getElementById("closePhotoModal");
  const startBtn = document.getElementById("startCameraBtn");
  const captureBtn = document.getElementById("captureBtn");
  const video = document.getElementById("cameraStream");
  const preview = document.getElementById("cameraPreview");
  const canvas = document.getElementById("cameraCanvas");
  const hiddenData = document.getElementById("cameraImageData");
  const fileInput = document.getElementById("profilePhotoInput");
  const filePreviewWrap = document.getElementById("filePreviewWrap");
  const filePreview = document.getElementById("filePreview");

  let stream = null;

  function stopCamera() {{
    if (stream) {{
      stream.getTracks().forEach(function (track) {{
        track.stop();
      }});
      stream = null;
    }}
    if (video) {{
      video.style.display = "none";
      video.srcObject = null;
    }}
    if (captureBtn) {{
      captureBtn.style.display = "none";
    }}
  }}

  if (fileInput && filePreviewWrap && filePreview) {{
    fileInput.addEventListener("change", function () {{
      const selected = fileInput.files && fileInput.files[0];
      hiddenData.value = "";
      if (!selected) {{
        filePreviewWrap.style.display = "none";
        filePreview.removeAttribute("src");
        return;
      }}
      const reader = new FileReader();
      reader.onload = function (event) {{
        filePreview.src = String(event.target?.result || "");
        filePreviewWrap.style.display = "flex";
        if (preview) {{
          preview.style.display = "none";
        }}
      }};
      reader.readAsDataURL(selected);
    }});
  }}

  window.openStudentPhotoModal = function () {{
    if (!modal) {{
      alert("Photo upload modal not found.");
      return;
    }}
    modal.classList.add("is-open");
    modal.style.display = "flex";
    modal.setAttribute("aria-hidden", "false");
  }};

  window.closeStudentPhotoModal = function () {{
    if (!modal) return;
    modal.classList.remove("is-open");
    modal.style.display = "none";
    modal.setAttribute("aria-hidden", "true");
    stopCamera();
  }};

  if (closeBtn) {{
    closeBtn.addEventListener("click", function (event) {{
      event.preventDefault();
      window.closeStudentPhotoModal();
    }});
  }}

  if (modal) {{
    modal.addEventListener("click", function (event) {{
      if (event.target === modal) {{
        window.closeStudentPhotoModal();
      }}
    }});
  }}

  document.addEventListener("keydown", function (event) {{
    if (event.key === "Escape") {{
      window.closeStudentPhotoModal();
    }}
  }});

  if (startBtn && video && captureBtn) {{
    startBtn.addEventListener("click", async function (event) {{
      event.preventDefault();

      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {{
        alert("Camera is not supported on this device. Please upload a photo.");
        return;
      }}

      try {{
        stream = await navigator.mediaDevices.getUserMedia({{ video: true }});
        video.srcObject = stream;
        video.style.display = "block";
        captureBtn.style.display = "inline-block";
      }} catch (error) {{
        alert("Camera permission denied. Please use file upload.");
      }}
    }});
  }}

  if (captureBtn && video && canvas && hiddenData && preview) {{
    captureBtn.addEventListener("click", function (event) {{
      event.preventDefault();

      const context = canvas.getContext("2d");
      canvas.width = video.videoWidth || 640;
      canvas.height = video.videoHeight || 480;
      context.drawImage(video, 0, 0, canvas.width, canvas.height);

      const data = canvas.toDataURL("image/webp", 0.9);
      hiddenData.value = data;
      if (fileInput) {{
        fileInput.value = "";
      }}
      if (filePreviewWrap) {{
        filePreviewWrap.style.display = "none";
      }}
      preview.src = data;
      preview.style.display = "block";

      stopCamera();
    }});
  }}

  console.log("SLIS student photo modal loaded");
}})();
</script>
    
    </body></html>
    """


@app.route("/student/profile-photo", methods=["POST"])
def upload_student_profile_photo():
    student_id = session.get("student_id")
    if not student_id:
        return redirect("/login")
    student = db.session.get(Student, student_id)
    if not student:
        session.pop("student_id", None)
        return redirect("/login")
    image_bytes = b""
    content_type = ""
    uploaded_file = request.files.get("profile_photo")
    if uploaded_file and uploaded_file.filename:
        content_type = (uploaded_file.mimetype or "").lower().strip()
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            return "<p>Invalid file type. Please upload JPG, PNG, or WEBP image.</p><p><a href='/student-dashboard'>Back</a></p>", 400
        image_bytes = uploaded_file.read()
    else:
        camera_data = (request.form.get("camera_image_data") or "").strip()
        if camera_data.startswith("data:image/") and ";base64," in camera_data:
            header, encoded = camera_data.split(",", 1)
            content_type = header.split(";")[0].replace("data:", "").strip().lower()
            if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                return "<p>Invalid camera image format.</p><p><a href='/student-dashboard'>Back</a></p>", 400
            try:
                image_bytes = base64.b64decode(encoded, validate=True)
            except Exception:
                return "<p>Invalid image data from camera capture.</p><p><a href='/student-dashboard'>Back</a></p>", 400
    if not image_bytes:
        return "<p>Please select or capture an image first.</p><p><a href='/student-dashboard'>Back</a></p>", 400
    if len(image_bytes) > 2 * 1024 * 1024:
        return "<p>Image size must be 2MB or less.</p><p><a href='/student-dashboard'>Back</a></p>", 400
    uploaded_url, error = upload_profile_image_to_supabase(student.id, image_bytes, content_type)
    if error:
        return f"<p>{escape(error)}</p><p><a href='/student-dashboard'>Back</a></p>", 500
    student.profile_image_url = uploaded_url
    db.session.commit()
    return redirect("/student-dashboard")



def get_latest_student_result(student_id: int):
    return (
        StudentResult.query.filter_by(student_id=student_id)
        .order_by(StudentResult.created_at.desc(), StudentResult.id.desc())
        .first()
    )


def classify_topic(score: float) -> tuple[str, str]:
    if score < 50:
        return "Weak", "දුර්වල"
    if score < 80:
        return "Improving", "දියුණු වෙමින්"
    return "Strong", "ශක්තිමත්"


def classify_mastery(score: float) -> tuple[str, str]:
    if score < 50:
        return "Weak", "දුර්වල"
    if score < 80:
        return "Improving", "වැඩිදියුණු වෙමින්"
    return "Mastered", "සම්පූර්ණ කර ඇත"


def upsert_student_topic_progress(student_id: int | None, grade: str, subject: str, topic_en: str, topic_si: str, score: float) -> None:
    if not student_id:
        return
    mastery_en, mastery_si = classify_mastery(score)
    existing = StudentTopicProgress.query.filter_by(
        student_id=student_id,
        grade=grade,
        subject=subject,
        topic_en=topic_en,
    ).first()
    now = datetime.utcnow()
    if existing:
        existing.latest_score = score
        existing.mastery_level_en = mastery_en
        existing.mastery_level_si = mastery_si
        existing.attempts_count = (existing.attempts_count or 0) + 1
        existing.last_updated = now
        existing.topic_si = topic_si or existing.topic_si
        return
    db.session.add(
        StudentTopicProgress(
            student_id=student_id,
            grade=grade,
            subject=subject,
            topic_en=topic_en,
            topic_si=topic_si,
            latest_score=score,
            mastery_level_en=mastery_en,
            mastery_level_si=mastery_si,
            attempts_count=1,
            last_updated=now,
        )
    )


def get_student_recommendations(student_id: int) -> list[dict[str, str]]:
    student = db.session.get(Student, student_id)
    if not student:
        return []
    recommendations = []
    progress_rows = (
        StudentTopicProgress.query.filter_by(student_id=student_id)
        .order_by(StudentTopicProgress.last_updated.desc(), StudentTopicProgress.id.desc())
        .all()
    )
    for row in progress_rows:
        if row.mastery_level_en == "Weak":
            action_en, action_si, action_url = "Practice + Retest", "පුහුණුව + නැවත පරීක්ෂණය", "/retest-weak"
        elif row.mastery_level_en == "Improving":
            action_en = "Intermediate Practice"
            action_si = "අතරමැදි පුහුණුව"
            action_url = f"/practice?grade={quote_plus(row.grade)}&subject={quote_plus(row.subject)}&topic={quote_plus(row.topic_en)}&medium={quote_plus(student.medium)}"
        else:
            action_en = "Challenge Practice or Next Topic"
            action_si = "අභියෝගාත්මක පුහුණුව හෝ ඊළඟ මාතෘකාව"
            action_url = f"/practice?grade={quote_plus(row.grade)}&subject={quote_plus(row.subject)}&topic={quote_plus(row.topic_en)}&medium={quote_plus(student.medium)}"
        recommendations.append(
            {
                "topic_en": row.topic_en,
                "topic_si": row.topic_si,
                "mastery_level_en": row.mastery_level_en,
                "mastery_level_si": row.mastery_level_si,
                "action_en": action_en,
                "action_si": action_si,
                "action_url": action_url,
            }
        )
    return recommendations


def get_student_weak_topics(student_id: int) -> list[dict[str, object]]:
    topic_rows = (
        StudentTopicProgress.query.filter_by(student_id=student_id)
        .order_by(StudentTopicProgress.last_updated.desc(), StudentTopicProgress.id.desc())
        .all()
    )
    weak_topics: list[dict[str, object]] = []
    for row in topic_rows:
        mastery_score = float(row.latest_score or 0)
        recent_attempts = (
            db.session.query(StudentQuestionAttempt.is_correct)
            .join(Question, Question.id == StudentQuestionAttempt.question_id)
            .filter(StudentQuestionAttempt.student_id == student_id, Question.topic == row.topic_en)
            .order_by(StudentQuestionAttempt.created_at.desc(), StudentQuestionAttempt.id.desc())
            .limit(5)
            .all()
        )
        recent_total = len(recent_attempts)
        recent_correct = sum(1 for item in recent_attempts if item.is_correct)
        recent_accuracy = (recent_correct / recent_total * 100) if recent_total else None
        repeated_wrong = (
            db.session.query(db.func.count(StudentQuestionAttempt.id))
            .join(Question, Question.id == StudentQuestionAttempt.question_id)
            .filter(
                StudentQuestionAttempt.student_id == student_id,
                Question.topic == row.topic_en,
                StudentQuestionAttempt.is_correct.is_(False),
            )
            .scalar()
            or 0
        )
        weakness_reasons: list[str] = []
        if mastery_score < 40:
            weakness_reasons.append("mastery_score_below_40")
        if recent_accuracy is not None and recent_accuracy < 50:
            weakness_reasons.append("last_5_accuracy_below_50")
        if repeated_wrong >= 3:
            weakness_reasons.append("repeated_wrong_answers")
        if weakness_reasons:
            suggested_action = "Do easier practice + explanation review" if mastery_score < 40 else "Do targeted practice and retest"
            weak_topics.append(
                {
                    "topic": row.topic_en,
                    "topic_si": row.topic_si,
                    "grade": row.grade,
                    "subject": row.subject,
                    "mastery_score": round(mastery_score, 2),
                    "weakness_reason": ", ".join(weakness_reasons),
                    "suggested_action": suggested_action,
                    "mastery_badge": "WEAK",
                }
            )
    return weak_topics


def get_dynamic_practice_questions(student_id: int, limit: int = 10) -> list[Question]:
    student = db.session.get(Student, student_id)
    if not student:
        return []
    weak_topics = get_student_weak_topics(student_id)
    developing_topics = (
        StudentTopicProgress.query.filter(
            StudentTopicProgress.student_id == student_id,
            StudentTopicProgress.latest_score >= 40,
            StudentTopicProgress.latest_score <= 70,
        )
        .order_by(StudentTopicProgress.latest_score.asc(), StudentTopicProgress.last_updated.desc())
        .all()
    )
    ranked_topics = weak_topics + [
        {"topic": row.topic_en, "grade": row.grade, "subject": row.subject, "mastery_score": float(row.latest_score or 0), "mastery_badge": "DEVELOPING"}
        for row in developing_topics
    ]
    recent_attempt_ids = [
        row[0]
        for row in db.session.query(StudentQuestionAttempt.question_id)
        .filter(StudentQuestionAttempt.student_id == student_id)
        .order_by(StudentQuestionAttempt.created_at.desc(), StudentQuestionAttempt.id.desc())
        .limit(30)
        .all()
    ]
    attempted_ids = {
        row[0]
        for row in db.session.query(StudentQuestionAttempt.question_id)
        .filter(StudentQuestionAttempt.student_id == student_id)
        .all()
    }
    selected: list[Question] = []
    used_ids: set[int] = set()
    for topic_item in ranked_topics:
        if len(selected) >= limit:
            break
        mastery_score = float(topic_item.get("mastery_score") or 0)
        if mastery_score < 40:
            difficulty_range = [1, 2]
        elif mastery_score <= 70:
            difficulty_range = [2, 3]
        else:
            difficulty_range = [3, 4, 5]
        base = Question.query.filter_by(
            grade=str(topic_item.get("grade") or normalize_grade(student.grade)),
            subject=str(topic_item.get("subject") or "Math"),
            topic=str(topic_item.get("topic") or ""),
        ).filter(db.func.coalesce(Question.difficulty_level, 1).in_(difficulty_range))
        unanswered = [q for q in base.order_by(Question.id.asc()).limit(limit * 2).all() if q.id not in attempted_ids and q.id not in recent_attempt_ids and q.id not in used_ids]
        fallback = [q for q in base.order_by(Question.id.asc()).limit(limit * 2).all() if q.id not in recent_attempt_ids and q.id not in used_ids]
        for q in (unanswered + fallback):
            if len(selected) >= limit:
                break
            if q.id in used_ids:
                continue
            selected.append(q)
            used_ids.add(q.id)
    return selected[:limit]

def get_student_next_recommendation(student_id: int) -> dict[str, object]:
    student = db.session.get(Student, student_id)
    if not student:
        return {}

    def lesson_payload(lesson: Lesson, rec_type: str, reason_en: str, reason_si: str, progress: float = 0, slide_order: int = 1, mastery: str = "Unknown"):
        chapter = db.session.get(SyllabusChapter, lesson.chapter_id)
        return {
            "type": rec_type,
            "lesson_id": lesson.id,
            "chapter_id": lesson.chapter_id,
            "recommended_lesson": lesson.lesson_title_si if student.medium == "Sinhala" else lesson.lesson_title_en,
            "recommended_lesson_en": lesson.lesson_title_en,
            "recommended_lesson_si": lesson.lesson_title_si,
            "reason_en": reason_en,
            "reason_si": reason_si,
            "progress_percent": int(progress or 0),
            "mastery_status": mastery,
            "estimated_time": int(lesson.estimated_minutes or 10),
            "slide_order": int(slide_order or 1),
            "next_url": url_for("student_lesson_page", lesson_id=lesson.id),
            "chapter_name_en": chapter.chapter_name_en if chapter else "",
            "chapter_name_si": chapter.chapter_name_si if chapter else "",
        }

    unfinished = (
        StudentLessonProgress.query.filter_by(student_id=student_id, is_completed=False)
        .order_by(StudentLessonProgress.updated_at.desc(), StudentLessonProgress.id.desc())
        .first()
    )
    if unfinished:
        lesson = db.session.get(Lesson, unfinished.lesson_id)
        if lesson and lesson.is_active:
            return lesson_payload(
                lesson,
                "continue_lesson",
                f"You stopped at slide {unfinished.current_slide_order}.",
                f"ඔබ {unfinished.current_slide_order} වන ස්ලයිඩ් එකේ නතර වුණා.",
                unfinished.completion_percent,
                unfinished.current_slide_order,
                "In Progress",
            )

    weak_mastery = (
        StudentSkillMastery.query.filter(
            StudentSkillMastery.student_id == student_id,
            StudentSkillMastery.mastery_score < 40,
        )
        .order_by(StudentSkillMastery.mastery_score.asc(), StudentSkillMastery.updated_at.desc())
        .first()
    )
    if weak_mastery:
        lesson = db.session.get(Lesson, weak_mastery.lesson_id)
        if lesson and lesson.is_active:
            return lesson_payload(
                lesson,
                "revision",
                "Weak mastery detected (<40). Review this lesson.",
                "දුර්වල දක්ෂතා මට්ටම (<40) හඳුනාගත් නිසා මේ පාඩම නැවත බලන්න.",
                weak_mastery.mastery_score,
                1,
                weak_mastery.status_en or "Weak",
            )

    repeated_wrong = (
        db.session.query(StudentLessonAnswer.lesson_id, db.func.count(StudentLessonAnswer.id).label("wrong_count"))
        .filter(StudentLessonAnswer.student_id == student_id, StudentLessonAnswer.is_correct.is_(False))
        .group_by(StudentLessonAnswer.lesson_id)
        .having(db.func.count(StudentLessonAnswer.id) >= 3)
        .order_by(db.desc("wrong_count"))
        .first()
    )
    if repeated_wrong:
        lesson = db.session.get(Lesson, repeated_wrong.lesson_id)
        if lesson and lesson.is_active:
            return lesson_payload(
                lesson,
                "practice",
                "Repeated wrong answers found. Start easier practice + explanation/video support.",
                "නැවත නැවත වැරදි පිළිතුරු ඇති නිසා පහසු අභ්‍යාස + විස්තර/වීඩියෝ සහාය ලබාදේ.",
                0,
                1,
                "Needs Practice",
            )

    latest_completed = (
        StudentLessonProgress.query.filter_by(student_id=student_id, is_completed=True)
        .order_by(StudentLessonProgress.completed_at.desc(), StudentLessonProgress.id.desc())
        .first()
    )
    if latest_completed:
        completed_lesson = db.session.get(Lesson, latest_completed.lesson_id)
        if completed_lesson:
            next_lesson = (
                Lesson.query.filter(
                    Lesson.chapter_id == completed_lesson.chapter_id,
                    Lesson.is_active.is_(True),
                    Lesson.lesson_order > completed_lesson.lesson_order,
                )
                .order_by(Lesson.lesson_order.asc())
                .first()
            )
            if next_lesson:
                return lesson_payload(next_lesson, "continue_lesson", "Next lesson in this chapter is ready.", "මෙම පරිච්ඡේදයේ ඊළඟ පාඩම සූදානම්.", 0, 1, "Ready")

    unlocked_next = (
        StudentChapterProgress.query.filter_by(student_id=student_id, status="unlocked")
        .order_by(StudentChapterProgress.created_at.asc(), StudentChapterProgress.id.asc())
        .first()
    )
    if unlocked_next:
        next_lesson = Lesson.query.filter_by(chapter_id=unlocked_next.chapter_id, is_active=True).order_by(Lesson.lesson_order.asc()).first()
        if next_lesson:
            return lesson_payload(next_lesson, "next_chapter", "Strong mastery detected. Next chapter unlocked.", "ශක්තිමත් දක්ෂතා හඳුනාගෙන ඇත. ඊළඟ පරිච්ඡේදය විවෘත කර ඇත.", 0, 1, "Strong")
    return {}


def ensure_revision_queue_tables() -> None:
    db.session.execute(db.text("""
        CREATE TABLE IF NOT EXISTS student_revision_queue (
            id SERIAL PRIMARY KEY,
            student_id INTEGER NOT NULL,
            subject_id INTEGER NULL,
            module_id INTEGER NULL,
            chapter_id INTEGER NULL,
            lesson_id INTEGER NULL,
            skill_code VARCHAR(120) NOT NULL,
            revision_reason VARCHAR(120) NOT NULL,
            priority_score DOUBLE PRECISION NOT NULL DEFAULT 0,
            due_date DATE NOT NULL,
            is_completed BOOLEAN NOT NULL DEFAULT FALSE,
            interval_days INTEGER NOT NULL DEFAULT 1,
            successful_revisions INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
    """))
    db.session.execute(db.text("CREATE INDEX IF NOT EXISTS idx_revision_queue_student_due ON student_revision_queue(student_id, due_date)"))
    db.session.execute(db.text("CREATE INDEX IF NOT EXISTS idx_revision_queue_student_open ON student_revision_queue(student_id, is_completed, priority_score DESC)"))
    db.session.commit()


REVISION_SUCCESS_INTERVALS = [1, 3, 7, 14, 30]


def _next_revision_interval(successful_revisions: int, is_correct: bool) -> tuple[int, int]:
    if is_correct:
        new_success_count = max(0, int(successful_revisions or 0)) + 1
        index = min(len(REVISION_SUCCESS_INTERVALS) - 1, new_success_count - 1)
        return REVISION_SUCCESS_INTERVALS[index], new_success_count
    return 1, 0


def generate_student_revision_queue(student_id: int) -> int:
    ensure_revision_queue_tables()
    now = datetime.utcnow()
    stale_cutoff = now - timedelta(days=7)
    mastery_rows = StudentSkillMastery.query.filter_by(student_id=student_id).all()
    queued_count = 0
    for mastery in mastery_rows:
        reasons = []
        priority = 0.0
        score = float(mastery.mastery_score or 0)
        if score < 60:
            reasons.append('low_mastery')
            priority += min(40, 60 - score)
        if not mastery.last_answered_at or mastery.last_answered_at <= stale_cutoff:
            reasons.append('inactive_7_days')
            priority += 20
        wrong_streak = StudentQuestionAttempt.query.join(Question, Question.id == StudentQuestionAttempt.question_id).filter(
            StudentQuestionAttempt.student_id == student_id,
            StudentQuestionAttempt.is_correct.is_(False),
            Question.topic_en == mastery.skill_code,
        ).order_by(StudentQuestionAttempt.created_at.desc()).limit(3).count()
        if wrong_streak >= 2:
            reasons.append('repeated_wrong_answers')
            priority += 25
        perf = StudentTopicProgress.query.filter_by(student_id=student_id, topic_en=mastery.skill_code).first()
        if perf and float(perf.latest_score or 0) < 70:
            reasons.append('low_quiz_accuracy')
            priority += 20
        if not reasons:
            continue
        existing = StudentRevisionQueue.query.filter_by(student_id=student_id, skill_code=mastery.skill_code, is_completed=False).first()
        due = date.today()
        if existing:
            existing.revision_reason = ','.join(sorted(set(reasons)))
            existing.priority_score = max(float(existing.priority_score or 0), priority)
            existing.due_date = min(existing.due_date, due)
            existing.subject_id = mastery.subject_id
            existing.module_id = mastery.module_id
            existing.chapter_id = mastery.chapter_id
            existing.lesson_id = mastery.lesson_id
        else:
            db.session.add(StudentRevisionQueue(student_id=student_id, subject_id=mastery.subject_id, module_id=mastery.module_id, chapter_id=mastery.chapter_id, lesson_id=mastery.lesson_id, skill_code=mastery.skill_code, revision_reason=','.join(sorted(set(reasons))), priority_score=priority, due_date=due, is_completed=False, interval_days=1, successful_revisions=0))
        queued_count += 1
    db.session.commit()
    return queued_count


def ensure_chapter_learning_tables() -> None:
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS student_selected_subject (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_student_selected_subject_unique
            ON student_selected_subject (student_id, subject_id)
            """
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE INDEX IF NOT EXISTS idx_student_selected_subject_lookup
            ON student_selected_subject (student_id, is_active, sort_order)
            """
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS student_subject_order (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_student_subject_order_unique
            ON student_subject_order (student_id, subject_id)
            """
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE INDEX IF NOT EXISTS idx_student_subject_order_lookup
            ON student_subject_order (student_id, sort_order)
            """
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS chapter_learning_content (
                id SERIAL PRIMARY KEY,
                chapter_id INTEGER NOT NULL,
                content_order INTEGER NOT NULL DEFAULT 1,
                content_type VARCHAR(20) NOT NULL,
                title_en VARCHAR(200) NOT NULL,
                title_si VARCHAR(200) NOT NULL,
                content_url TEXT,
                content_body_en TEXT,
                content_body_si TEXT,
                is_required BOOLEAN NOT NULL DEFAULT TRUE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(
        db.text(
            """
            ALTER TABLE chapter_learning_content
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE
            """
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS student_chapter_progress (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL,
                chapter_id INTEGER NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'locked',
                completed_at TIMESTAMP NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS student_content_progress (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL,
                content_id INTEGER NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'not_started',
                completed_at TIMESTAMP NULL
            )
            """
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS video_interaction (
                id SERIAL PRIMARY KEY,
                content_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                trigger_seconds INTEGER NOT NULL,
                pause_video BOOLEAN NOT NULL DEFAULT TRUE,
                required_answer BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS student_video_interaction_attempt (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL,
                content_id INTEGER NOT NULL,
                interaction_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                is_correct BOOLEAN NOT NULL DEFAULT FALSE,
                retry_count INTEGER NOT NULL DEFAULT 0,
                answered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS student_video_analytics (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL,
                content_id INTEGER NOT NULL,
                watch_percent DOUBLE PRECISION NOT NULL DEFAULT 0,
                all_required_answered BOOLEAN NOT NULL DEFAULT FALSE,
                popup_score DOUBLE PRECISION NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.commit()




def ensure_lesson_engine_tables() -> None:
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS lesson (
                id SERIAL PRIMARY KEY,
                chapter_id INTEGER NOT NULL,
                lesson_order INTEGER NOT NULL,
                lesson_title_en VARCHAR(200) NOT NULL,
                lesson_title_si VARCHAR(200) NOT NULL,
                lesson_type VARCHAR(50) NOT NULL DEFAULT 'standard',
                thumbnail_url TEXT,
                estimated_minutes INTEGER NOT NULL DEFAULT 10,
                xp_reward INTEGER NOT NULL DEFAULT 10,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS lesson_slide (
                id SERIAL PRIMARY KEY,
                lesson_id INTEGER NOT NULL,
                slide_order INTEGER NOT NULL,
                slide_type VARCHAR(50) NOT NULL DEFAULT 'explanation',
                title_en VARCHAR(200),
                title_si VARCHAR(200),
                content_en TEXT,
                content_si TEXT,
                image_url TEXT,
                video_url TEXT,
                audio_url TEXT,
                activity_json TEXT,
                xp_reward INTEGER NOT NULL DEFAULT 10,
                is_required BOOLEAN NOT NULL DEFAULT TRUE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(db.text("ALTER TABLE lesson_slide ADD COLUMN IF NOT EXISTS content_en TEXT"))
    db.session.execute(db.text("ALTER TABLE lesson_slide ADD COLUMN IF NOT EXISTS content_si TEXT"))
    db.session.execute(db.text("ALTER TABLE lesson_slide ADD COLUMN IF NOT EXISTS image_url TEXT"))
    db.session.execute(db.text("ALTER TABLE lesson_slide ADD COLUMN IF NOT EXISTS video_url TEXT"))
    db.session.execute(db.text("ALTER TABLE lesson_slide ADD COLUMN IF NOT EXISTS audio_url TEXT"))
    db.session.execute(db.text("ALTER TABLE lesson_slide ADD COLUMN IF NOT EXISTS activity_json TEXT"))
    db.session.execute(db.text("ALTER TABLE lesson_slide ADD COLUMN IF NOT EXISTS xp_reward INTEGER NOT NULL DEFAULT 10"))
    db.session.execute(db.text("ALTER TABLE lesson_slide ADD COLUMN IF NOT EXISTS is_required BOOLEAN NOT NULL DEFAULT TRUE"))
    db.session.execute(db.text("ALTER TABLE lesson_slide ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"))
    db.session.execute(db.text("ALTER TABLE lesson_slide ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"))
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS student_lesson_progress (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL,
                lesson_id INTEGER NOT NULL,
                current_slide_order INTEGER NOT NULL DEFAULT 1,
                completion_percent DOUBLE PRECISION NOT NULL DEFAULT 0,
                is_completed BOOLEAN NOT NULL DEFAULT FALSE,
                last_opened_at TIMESTAMP NULL,
                completed_at TIMESTAMP NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS student_lesson_answer (
                id SERIAL PRIMARY KEY,
                lesson_id INTEGER NOT NULL,
                slide_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                selected_answer TEXT NOT NULL,
                is_correct BOOLEAN NOT NULL DEFAULT FALSE,
                answered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(db.text("CREATE INDEX IF NOT EXISTS idx_lesson_chapter_order ON lesson (chapter_id, lesson_order)"))
    db.session.execute(db.text("CREATE INDEX IF NOT EXISTS idx_lesson_slide_lesson_order ON lesson_slide (lesson_id, slide_order)"))
    db.session.execute(db.text("CREATE INDEX IF NOT EXISTS idx_student_lesson_progress_lookup ON student_lesson_progress (student_id, lesson_id)"))
    db.session.execute(db.text("CREATE INDEX IF NOT EXISTS idx_student_lesson_answer_lookup ON student_lesson_answer (student_id, lesson_id, slide_id)"))
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS student_skill_mastery (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL,
                subject_id INTEGER NULL,
                module_id INTEGER NULL,
                chapter_id INTEGER NOT NULL,
                lesson_id INTEGER NOT NULL,
                skill_code VARCHAR(120) NOT NULL,
                skill_name_en VARCHAR(255) NOT NULL,
                skill_name_si VARCHAR(255) NOT NULL,
                mastery_score DOUBLE PRECISION NOT NULL DEFAULT 0,
                total_attempts INTEGER NOT NULL DEFAULT 0,
                correct_attempts INTEGER NOT NULL DEFAULT 0,
                wrong_attempts INTEGER NOT NULL DEFAULT 0,
                last_answered_at TIMESTAMP NULL,
                status_en VARCHAR(50) NOT NULL DEFAULT 'Weak',
                status_si VARCHAR(50) NOT NULL DEFAULT 'දුර්වලයි',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(db.text("CREATE INDEX IF NOT EXISTS idx_student_skill_mastery_lookup ON student_skill_mastery (student_id, lesson_id, skill_code)"))
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS student_ai_assistance_log (
                id SERIAL PRIMARY KEY,
                student_id INTEGER NOT NULL,
                lesson_id INTEGER NOT NULL,
                slide_id INTEGER NOT NULL,
                assistance_type VARCHAR(40) NOT NULL,
                triggered_reason VARCHAR(80) NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(db.text("CREATE INDEX IF NOT EXISTS idx_student_ai_assistance_lookup ON student_ai_assistance_log (student_id, lesson_id, slide_id)"))
    db.session.commit()


def mastery_status_labels(score: float) -> tuple[str, str]:
    clamped = max(0.0, min(100.0, float(score)))
    if clamped <= 30:
        return "Weak", "දුර්වලයි"
    if clamped <= 60:
        return "Developing", "වර්ධනය වෙමින්"
    if clamped <= 85:
        return "Good", "හොඳයි"
    return "Mastered", "ප්‍රගුණයි"

def _ordered_chapters_for_student(student: Student):
    return (
        db.session.query(SyllabusChapter, SyllabusModule, SyllabusTerm)
        .join(SyllabusModule, SyllabusModule.id == SyllabusChapter.module_id)
        .join(SyllabusTerm, SyllabusTerm.id == SyllabusModule.term_id)
        .filter(
            SyllabusTerm.grade == normalize_grade(student.grade),
            SyllabusTerm.subject == "Math",
            SyllabusChapter.is_active.is_(True),
        )
        .order_by(SyllabusTerm.term_number.asc(), SyllabusModule.module_order.asc(), SyllabusChapter.chapter_order.asc())
        .all()
    )




def normalize_youtube_embed_url(url: str | None) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None

    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    if host.endswith("youtu.be"):
        video_id = path.strip("/").split("/")[0]
    elif "youtube.com" in host:
        if path.startswith("/watch"):
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        elif path.startswith("/shorts/"):
            video_id = path.split("/shorts/", 1)[1].split("/", 1)[0]
        elif path.startswith("/embed/"):
            video_id = path.split("/embed/", 1)[1].split("/", 1)[0]
        else:
            video_id = ""
    else:
        return None

    video_id = (video_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,}", video_id):
        return None
    return f"https://www.youtube.com/embed/{video_id}"


def extract_youtube_video_id(url: str | None) -> str | None:
    normalized = normalize_youtube_embed_url(url)
    if not normalized:
        return None
    return normalized.rsplit("/", 1)[-1]

@app.route("/admin/chapters/content/<int:chapter_id>", methods=["GET", "POST"])
def admin_chapter_content(chapter_id: int):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    ensure_chapter_learning_tables()
    ensure_lesson_engine_tables()
    ensure_revision_queue_tables()
    chapter = db.session.get(SyllabusChapter, chapter_id)
    if not chapter:
        return "<h2>Chapter not found</h2>", 404

    if request.method == "POST":
        action = (request.form.get("action") or "add_content").strip()
        if action == "add_interaction":
            content_id = int(request.form.get("content_id") or 0)
            question_id = int(request.form.get("question_id") or 0)
            trigger_seconds = int(request.form.get("trigger_seconds") or 0)
            if content_id and question_id and trigger_seconds >= 0:
                db.session.add(VideoInteraction(
                    content_id=content_id,
                    question_id=question_id,
                    trigger_seconds=trigger_seconds,
                    pause_video=(request.form.get("pause_video") or "yes") == "yes",
                    required_answer=(request.form.get("required_answer") or "yes") == "yes",
                ))
                db.session.commit()
            return redirect(url_for("admin_chapter_content", chapter_id=chapter_id))
        if action == "delete_content":
            content_id = int(request.form.get("content_id") or 0)
            content = ChapterLearningContent.query.filter_by(id=content_id, chapter_id=chapter_id).first()
            if content:
                content.is_active = False
                db.session.commit()
            return redirect(url_for("admin_chapter_content", chapter_id=chapter_id))

        db.session.add(ChapterLearningContent(
            chapter_id=chapter_id,
            content_order=int(request.form.get("content_order") or 1),
            content_type=(request.form.get("content_type") or "video").strip().lower(),
            title_en=(request.form.get("title_en") or "").strip(),
            title_si=(request.form.get("title_si") or "").strip(),
            content_url=(request.form.get("content_url") or "").strip() or None,
            content_body_en=(request.form.get("content_body_en") or "").strip() or None,
            content_body_si=(request.form.get("content_body_si") or "").strip() or None,
            is_required=(request.form.get("is_required") or "yes") == "yes",
            is_active=True,
        ))
        db.session.commit()
        return redirect(url_for("admin_chapter_content", chapter_id=chapter_id))

    rows = ChapterLearningContent.query.filter_by(chapter_id=chapter_id, is_active=True).order_by(ChapterLearningContent.content_order.asc(), ChapterLearningContent.id.asc()).all()
    video_rows = [r for r in rows if r.content_type == "video"]
    questions = Question.query.order_by(Question.id.desc()).limit(200).all()
    question_options = "".join([f"<option value='{q.id}'>Q{q.id} - {escape((q.question_text_en or '')[:80])}</option>" for q in questions])
    try:
        interactions = VideoInteraction.query.join(ChapterLearningContent, VideoInteraction.content_id == ChapterLearningContent.id).filter(ChapterLearningContent.chapter_id == chapter_id).order_by(VideoInteraction.content_id.asc(), VideoInteraction.trigger_seconds.asc()).all()
    except Exception:
        interactions = []

    list_html = "".join(
        f"<tr><td>{r.content_order}</td><td>{r.content_type}</td><td>{escape(r.title_en)}</td><td>{'Yes' if r.is_required else 'No'}</td><td><a href='/admin/chapter-content/edit/{r.id}'>Edit</a> | <form method='post' style='display:inline;' onsubmit=\"return confirm('Deactivate this content?');\"><input type='hidden' name='action' value='delete_content'><input type='hidden' name='content_id' value='{r.id}'><button type='submit'>Delete</button></form></td></tr>"
        for r in rows
    )
    inter_html = "".join([
        f"<tr><td>{i.content_id}</td><td>Q{i.question_id}</td><td>{i.trigger_seconds}s</td><td>{'Yes' if i.pause_video else 'No'}</td><td>{'Yes' if i.required_answer else 'No'}</td></tr>"
        for i in interactions
    ])
    video_options = "".join([f"<option value='{v.id}'>{v.id} - {escape(v.title_en)}</option>" for v in video_rows])

    return f"""<h1>Chapter Content Manager</h1>
    <p><a href='/admin/syllabus'>Back</a></p>
    <table border='1' cellpadding='6'><tr><th>Order</th><th>Type</th><th>Title</th><th>Required</th><th>Action</th></tr>{list_html or "<tr><td colspan='5'>No content yet</td></tr>"}</table>
    <h2>Add Content</h2>
    <form method='post'>
      <input type='hidden' name='action' value='add_content'>
      <p>Content Type <select name='content_type'><option>video</option><option>note</option><option>activity</option><option>practice</option><option>test</option></select></p>
      <p>Title EN <input name='title_en' required></p><p>Title SI <input name='title_si' required></p>
      <p>URL <input name='content_url'></p><p>Body EN <textarea name='content_body_en'></textarea></p><p>Body SI <textarea name='content_body_si'></textarea></p>
      <p>Order <input type='number' name='content_order' value='1'></p>
      <p>Required <select name='is_required'><option value='yes'>Yes</option><option value='no'>No</option></select></p>
      <button type='submit'>Save</button>
    </form>

    <h2>Interactive Questions (Video)</h2>
    <table border='1' cellpadding='6'><tr><th>Content</th><th>Question</th><th>Trigger</th><th>Pause</th><th>Required</th></tr>{inter_html or "<tr><td colspan='5'>No interactions yet</td></tr>"}</table>
    <form method='post'>
      <input type='hidden' name='action' value='add_interaction'>
      <p>Video Content <select name='content_id' required>{video_options}</select></p>
      <p>Question <select name='question_id' required>{question_options}</select></p>
      <p>Trigger (seconds) <input type='number' min='0' name='trigger_seconds' required></p>
      <p>Pause Video <select name='pause_video'><option value='yes'>Yes</option><option value='no'>No</option></select></p>
      <p>Required Answer <select name='required_answer'><option value='yes'>Yes</option><option value='no'>No</option></select></p>
      <button type='submit'>Save Interaction</button>
    </form>"""


@app.route("/admin/chapter-content/edit/<int:content_id>", methods=["GET", "POST"])
def admin_chapter_content_edit(content_id: int):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    ensure_chapter_learning_tables()
    content = db.session.get(ChapterLearningContent, content_id)
    if not content:
        return "<h2>Content not found</h2>", 404

    if request.method == "POST":
        action = (request.form.get("action") or "update_content").strip().lower()
        if action == "add_interaction":
            if content.content_type == "video":
                question_id = int(request.form.get("question_id") or 0)
                trigger_seconds = int(request.form.get("trigger_seconds") or 0)
                if question_id > 0:
                    db.session.add(VideoInteraction(
                        content_id=content.id,
                        question_id=question_id,
                        trigger_seconds=trigger_seconds,
                        pause_video=(request.form.get("pause_video") or "yes") == "yes",
                        required_answer=(request.form.get("required_answer") or "yes") == "yes",
                    ))
                    db.session.commit()
            return redirect(url_for("admin_chapter_content_edit", content_id=content.id))

        content.content_type = (request.form.get("content_type") or content.content_type).strip().lower()
        content.title_en = (request.form.get("title_en") or "").strip()
        content.title_si = (request.form.get("title_si") or "").strip()
        content.content_url = (request.form.get("content_url") or "").strip() or None
        content.content_body_en = (request.form.get("content_body_en") or "").strip() or None
        content.content_body_si = (request.form.get("content_body_si") or "").strip() or None
        content.content_order = int(request.form.get("content_order") or content.content_order or 1)
        content.is_required = (request.form.get("is_required") or "yes") == "yes"
        db.session.commit()
        return redirect(url_for("admin_chapter_content", chapter_id=content.chapter_id))

    interactions = []
    question_options = ""
    if content.content_type == "video":
        interactions = VideoInteraction.query.filter_by(content_id=content.id).order_by(VideoInteraction.trigger_seconds.asc(), VideoInteraction.id.asc()).all()
        questions = Question.query.order_by(Question.id.desc()).limit(200).all()
        question_options = "".join([f"<option value='{q.id}'>Q{q.id} - {escape((q.question_text_en or '')[:80])}</option>" for q in questions])

    interaction_rows = "".join([
        f"<tr><td>{escape(((i.question.question_text_en if i.question else 'Question not found') or '')[:120])}</td><td>{i.trigger_seconds}</td><td>{'Yes' if i.pause_video else 'No'}</td><td>{'Yes' if i.required_answer else 'No'}</td><td><a href='/admin/video-interaction/edit/{i.id}'>Edit</a> | <form method='post' action='/admin/video-interaction/delete/{i.id}' style='display:inline;' onsubmit=\"return confirm('Delete this interaction?');\'><button type='submit'>Delete</button></form></td></tr>"
        for i in interactions
    ])
    preview_section = ""
    if content.content_type == "video" and content.content_url:
        normalized_video_url = normalize_youtube_embed_url(content.content_url)
        if normalized_video_url:
            preview_section = f"<p><strong>Preview:</strong><br><iframe width='560' height='315' src='{escape(normalized_video_url)}' title='YouTube video preview' frameborder='0' allowfullscreen></iframe></p>"
        else:
            preview_section = "<p><strong>Preview:</strong> Invalid video URL</p>"

    interaction_section = ""
    if content.content_type == "video":
        interaction_section = f"""
    <h2>Interactive Questions</h2>
    <table border='1' cellpadding='6'><tr><th>Question</th><th>Trigger Seconds</th><th>Pause Video</th><th>Required Answer</th><th>Action</th></tr>{interaction_rows or "<tr><td colspan='5'>No interactions yet</td></tr>"}</table>
    <h3>Add Interaction</h3>
    <form method='post'>
      <input type='hidden' name='action' value='add_interaction'>
      <p>Question <select name='question_id' required>{question_options}</select></p>
      <p>Trigger seconds <input type='number' min='0' name='trigger_seconds' required></p>
      <p>Pause Video <select name='pause_video'><option value='yes'>Yes</option><option value='no'>No</option></select></p>
      <p>Required Answer <select name='required_answer'><option value='yes'>Yes</option><option value='no'>No</option></select></p>
      <button type='submit'>Add Interaction</button>
    </form>"""

    return f"""<h1>Edit Chapter Content</h1>
    <p><a href='/admin/chapters/content/{content.chapter_id}'>Back to Chapter Content</a></p>
    <form method='post'>
      <input type='hidden' name='action' value='update_content'>
      <p>Content Type <select name='content_type'>
        <option {'selected' if content.content_type == 'video' else ''}>video</option>
        <option {'selected' if content.content_type == 'note' else ''}>note</option>
        <option {'selected' if content.content_type == 'activity' else ''}>activity</option>
        <option {'selected' if content.content_type == 'practice' else ''}>practice</option>
        <option {'selected' if content.content_type == 'test' else ''}>test</option>
      </select></p>
      <p>Title EN <input name='title_en' value='{escape(content.title_en)}' required></p>
      <p>Title SI <input name='title_si' value='{escape(content.title_si)}' required></p>
      <p>URL <input name='content_url' value='{escape(content.content_url or "")}'></p>
      {preview_section}
      <p>Body EN <textarea name='content_body_en'>{escape(content.content_body_en or "")}</textarea></p>
      <p>Body SI <textarea name='content_body_si'>{escape(content.content_body_si or "")}</textarea></p>
      <p>Order <input type='number' name='content_order' value='{content.content_order}'></p>
      <p>Required <select name='is_required'><option value='yes' {'selected' if content.is_required else ''}>Yes</option><option value='no' {'selected' if not content.is_required else ''}>No</option></select></p>
      <button type='submit'>Update</button>
    </form>
    {interaction_section}"""


@app.route("/admin/video-interaction/edit/<int:interaction_id>", methods=["GET", "POST"])
def admin_video_interaction_edit(interaction_id: int):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    ensure_chapter_learning_tables()
    interaction = db.session.get(VideoInteraction, interaction_id)
    if not interaction:
        return "<h2>Interaction not found</h2>", 404

    if request.method == "POST":
        interaction.question_id = int(request.form.get("question_id") or interaction.question_id)
        interaction.trigger_seconds = int(request.form.get("trigger_seconds") or interaction.trigger_seconds or 0)
        interaction.pause_video = (request.form.get("pause_video") or "yes") == "yes"
        interaction.required_answer = (request.form.get("required_answer") or "yes") == "yes"
        db.session.commit()
        return redirect(url_for("admin_chapter_content_edit", content_id=interaction.content_id))

    questions = Question.query.order_by(Question.id.desc()).limit(200).all()
    question_options = "".join([
        f"<option value='{q.id}' {'selected' if q.id == interaction.question_id else ''}>Q{q.id} - {escape((q.question_text_en or '')[:80])}</option>"
        for q in questions
    ])
    return f"""<h1>Edit Video Interaction</h1>
    <p><a href='/admin/chapter-content/edit/{interaction.content_id}'>Back to Edit Chapter Content</a></p>
    <form method='post'>
      <p>Question <select name='question_id' required>{question_options}</select></p>
      <p>Trigger seconds <input type='number' min='0' name='trigger_seconds' value='{interaction.trigger_seconds}' required></p>
      <p>Pause Video <select name='pause_video'><option value='yes' {'selected' if interaction.pause_video else ''}>Yes</option><option value='no' {'selected' if not interaction.pause_video else ''}>No</option></select></p>
      <p>Required Answer <select name='required_answer'><option value='yes' {'selected' if interaction.required_answer else ''}>Yes</option><option value='no' {'selected' if not interaction.required_answer else ''}>No</option></select></p>
      <button type='submit'>Update Interaction</button>
    </form>"""


@app.route("/admin/video-interaction/delete/<int:interaction_id>", methods=["POST"])
def admin_video_interaction_delete(interaction_id: int):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    ensure_chapter_learning_tables()
    interaction = db.session.get(VideoInteraction, interaction_id)
    if not interaction:
        return "<h2>Interaction not found</h2>", 404
    content_id = interaction.content_id
    db.session.delete(interaction)
    db.session.commit()
    return redirect(url_for("admin_chapter_content_edit", content_id=content_id))

@app.route("/learning-path", methods=["GET"])
def learning_path() -> str:
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))

    student = db.session.get(Student, student_id)
    if not student:
        session.pop("student_id", None)
        return redirect(url_for("login"))


    latest_result = (
        StudentResult.query.filter_by(student_id=student.id)
        .order_by(StudentResult.created_at.desc(), StudentResult.id.desc())
        .first()
    )

    language = "si" if student.medium == "Sinhala" else "en"
    labels = {
        "en": {
            "title": "My Learning Path",
            "student_name": "Student Name",
            "latest_score": "Latest SkillScan Score",
            "topic": "Topic",
            "percentage": "Percentage",
            "status": "Status",
            "recommendation": "Personalized Next Step",
            "practice": "Practice",
            "no_result": "No SkillScan result found. Please complete a SkillScan test first.",
            "back": "Back to Dashboard",
            "weak": "Weak",
            "improving": "Improving",
            "strong": "Strong",
            "weak_actions": "Practice topic → Review explanation → Retake topic after practice",
            "improving_actions": "Do intermediate practice for this topic",
            "strong_actions": "Do challenge practice for this topic",
        },
        "si": {
            "title": "මගේ ඉගෙනුම් මාර්ගය",
            "student_name": "ශිෂ්‍ය නම",
            "latest_score": "අවසන් SkillScan ලකුණ",
            "topic": "මාතෘකාව",
            "percentage": "ප්‍රතිශතය",
            "status": "තත්ත්වය",
            "recommendation": "පුද්ගලීකරණය කළ ඊළඟ පියවර",
            "practice": "පුහුණුව",
            "no_result": "SkillScan ප්‍රතිඵල නොමැත. කරුණාකර පළමුව SkillScan පරීක්ෂණයක් අවසන් කරන්න.",
            "back": "ඩෑෂ්බෝඩ් වෙත ආපසු",
            "weak": "දුර්වල",
            "improving": "වැඩිදියුණු වෙමින්",
            "strong": "ශක්තිමත්",
            "weak_actions": "මාතෘකාව පුහුණු කරන්න → විස්තරය නැවත සමාලෝචනය කරන්න → පුහුණුවෙන් පසු නැවත උත්සාහ කරන්න",
            "improving_actions": "මෙම මාතෘකාව සඳහා මධ්‍යම මට්ටමේ පුහුණුව කරන්න",
            "strong_actions": "මෙම මාතෘකාව සඳහා අභියෝගාත්මක පුහුණුව කරන්න",
        },
    }[language]

    if not latest_result:
        return f"""
        <!doctype html>
        <html lang='{'si' if language == 'si' else 'en'}'>
          <head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{labels["title"]}</title></head>
          <body>
            <h1>{labels["title"]}</h1>
            <p>{labels["no_result"]}</p>
            <p><a href='/student-dashboard'>{labels["back"]}</a></p>
      </body>
        </html>
        """

    topic_performance = (
        StudentTopicPerformance.query.filter_by(student_result_id=latest_result.id)
        .order_by(StudentTopicPerformance.id.asc())
        .all()
    )
    medium_key = "si" if language == "si" else "en"

    topic_rows = []
    next_steps = []
    for topic in topic_performance:
        if topic.percentage < 50:
            status_en, status_si = "Weak", "දුර්වල"
            action_text = labels["weak_actions"]
        elif topic.percentage < 80:
            status_en, status_si = "Improving", "වැඩිදියුණු වෙමින්"
            action_text = labels["improving_actions"]
        else:
            status_en, status_si = "Strong", "ශක්තිමත්"
            action_text = labels["strong_actions"]

        status_text = status_si if language == "si" else status_en
        topic_name = getattr(topic, f"topic_{medium_key}")
        practice_label = f"{topic.topic_si} පුහුණුව ආරම්භ කරන්න" if language == "si" else f"Start {topic.topic_en} Practice"
        practice_href = (
            f"/practice?grade={latest_result.grade}&subject={latest_result.subject}"
            f"&topic={quote_plus(topic.topic_en)}&medium={quote_plus(student.medium)}"
        )

        topic_rows.append(
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'>{topic_name}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{topic.percentage}%</td>
              <td style='border:1px solid #ccc;padding:8px;'>{status_text}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{action_text}</td>
              <td style='border:1px solid #ccc;padding:8px;'><a href='{practice_href}'>{practice_label}</a></td>
            </tr>
            """
        )
        next_steps.append(f"<li><strong>{topic_name}:</strong> {action_text}</li>")

    return f"""
    <!doctype html>
    <html lang='{'si' if language == 'si' else 'en'}'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>{labels["title"]}</title>
      </head>
      <body>
        <h1>{labels["title"]}</h1>
        <p><strong>{labels["student_name"]}:</strong> {student.name}</p>
        <p><strong>{labels["latest_score"]}:</strong> {latest_result.score}%</p>

        <h2>{labels["recommendation"]}</h2>
        <ul>
          {''.join(next_steps) if next_steps else "<li>-</li>"}
        </ul>

        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels["topic"]}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels["percentage"]}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels["status"]}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels["recommendation"]}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels["practice"]}</th>
            </tr>
          </thead>
          <tbody>
            {''.join(topic_rows) if topic_rows else "<tr><td colspan='5' style='border:1px solid #ccc;padding:8px;'>-</td></tr>"}
          </tbody>
        </table>
        <p><a href='/student-dashboard'>{labels["back"]}</a></p>
      </body>
    </html>
    """




def get_parent_credentials() -> tuple[str, str]:
    return (
        os.environ.get("PARENT_EMAIL", "parent@spiral.com"),
        os.environ.get("PARENT_PASSWORD", "parent123"),
    )


@app.route("/parent-login", methods=["GET", "POST"])
def parent_login():
    if request.method == "GET":
        return """
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Parent Login</title>
          </head>
          <body>
            <h1>Parent Login</h1>
            <form method="post" action="/parent-login">
              <label>Email: <input type="email" name="email" required></label><br><br>
              <label>Password: <input type="password" name="password" required></label><br><br>
              <button type="submit">Login</button>
            </form>
      </body>
        </html>
        """

    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    parent_email, parent_password = get_parent_credentials()
    if email != parent_email or password != parent_password:
        return "<h2>Invalid parent credentials</h2><p><a href='/parent-login'>Try again</a></p>", 401

    session["parent_logged_in"] = True
    return redirect(url_for("parent_dashboard"))


@app.route("/parent-dashboard", methods=["GET"])
def parent_dashboard():
    db.create_all()
    if session.get("parent_logged_in") is not True:
        return redirect(url_for("parent_login"))

    logged_in_parent_email = os.environ.get("PARENT_EMAIL", "").strip()
    if not logged_in_parent_email:
        return "<h2>Parent email is not configured.</h2>", 500

    students = (
        Student.query.filter_by(parent_email=logged_in_parent_email)
        .order_by(Student.created_at.desc(), Student.id.desc())
        .all()
    )

    rows: list[str] = []
    for student in students:
        latest_result = get_latest_student_result(student.id)
        weak_topics = "-"
        if latest_result:
            weak_topic_records = (
                StudentTopicPerformance.query.filter_by(student_result_id=latest_result.id)
                .filter(StudentTopicPerformance.status_en.in_(["Weak", "Improving"]))
                .order_by(StudentTopicPerformance.id.asc())
                .all()
            )
            if weak_topic_records:
                topic_attr = "topic_en" if student.medium == "English" else "topic_si"
                weak_topics = ", ".join(getattr(topic, topic_attr) for topic in weak_topic_records)

        latest_attempt = (
            PracticeAttempt.query.filter_by(student_id=student.id)
            .order_by(PracticeAttempt.created_at.desc(), PracticeAttempt.id.desc())
            .first()
        )
        attempt_text = "-"
        if latest_attempt:
            topic_name = latest_attempt.topic_en if student.medium == "English" else latest_attempt.topic_si
            attempt_text = f"{topic_name} ({latest_attempt.score}%, {latest_attempt.correct_answers}/{latest_attempt.total_questions})"

        rows.append(
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'>{student.name}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{student.grade}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{student.medium}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{f'{latest_result.score}%' if latest_result else '-'}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{latest_result.level if latest_result else '-'}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{weak_topics}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{attempt_text}</td>
            </tr>
            """
        )
    student_map = {student.id: student for student in students}
    latest_notifications = (
        ParentNotification.query.filter_by(parent_email=logged_in_parent_email)
        .order_by(ParentNotification.created_at.desc(), ParentNotification.id.desc())
        .limit(10)
        .all()
    )
    notification_rows: list[str] = []
    for notification in latest_notifications:
        student = student_map.get(notification.student_id)
        student_name = student.name if student else f"Student #{notification.student_id}"
        student_medium = student.medium if student else "English"
        message = notification.message_si if student_medium == "Sinhala" else notification.message_en
        safe_student_name = escape(student_name)
        safe_message = escape(message)
        parent_mobile = (student.mobile or "").strip() if student else ""
        whatsapp_link = f"https://wa.me/{parent_mobile}?text={quote_plus(message)}"
        whatsapp_button_html = (
            f"<a href='{whatsapp_link}' target='_blank'>Send via WhatsApp</a>"
            if parent_mobile
            else "-"
        )
        notification_rows.append(
            f"<tr><td style='border:1px solid #ccc;padding:8px;'>{safe_student_name}</td>"
            f"<td style='border:1px solid #ccc;padding:8px;'>{safe_message}</td>"
            f"<td style='border:1px solid #ccc;padding:8px;'>{notification.created_at.strftime('%Y-%m-%d %H:%M')}</td>"
            f"<td style='border:1px solid #ccc;padding:8px;'>{whatsapp_button_html}</td></tr>"
        )

    table_rows = "".join(rows)
    return f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Parent Dashboard</title>
      </head>
      <body>
        <h1>Parent Dashboard</h1>
        <p><a href='/parent-logout'><button type='button'>Logout</button></a></p>
        <h2>Chapter Progress</h2>
        <p><a href='/parent/chapter-progress'>View Child Chapter Progress</a></p>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Student Name</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Grade</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Medium</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Latest SkillScan Score</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Level</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Weak Topics</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Latest Practice Attempt</th>
            </tr>
          </thead>
          <tbody>{table_rows if table_rows else "<tr><td colspan='7' style='border:1px solid #ccc;padding:8px;'>No student linked to this parent account yet.</td></tr>"}</tbody>
        </table>
        <h2 style='margin-top:24px;'>Latest Notifications</h2>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Student</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Message</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Date</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Action</th>
            </tr>
          </thead>
          <tbody>{''.join(notification_rows) if notification_rows else "<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>No notifications yet.</td></tr>"}</tbody>
        </table>
      </body>
    </html>
    """


@app.route("/parent-logout", methods=["GET"])
def parent_logout():
    session.pop("parent_logged_in", None)
    return redirect(url_for("parent_login"))


@app.route("/parent/chapter-progress", methods=["GET"])
def parent_chapter_progress():
    if session.get("parent_logged_in") is not True:
        return redirect(url_for("parent_login"))
    logged_in_parent_email = os.environ.get("PARENT_EMAIL", "").strip()
    students = Student.query.filter_by(parent_email=logged_in_parent_email).order_by(Student.name.asc()).all()
    if not students:
        return "<h2>Child not linked</h2>", 404
    sections = []
    for student in students:
        progresses = StudentChapterProgress.query.filter_by(student_id=student.id).order_by(StudentChapterProgress.chapter_id.asc()).all()
        current = next((p for p in progresses if p.status == "in_progress"), None)
        completed = [p for p in progresses if p.status == "completed"]
        rows = "".join(f"<tr><td>{p.chapter_id}</td><td>{escape(p.status)}</td><td>{p.completed_at or '-'}</td></tr>" for p in progresses)
        sections.append(
            f"<h2>{escape(student.name)}</h2>"
            f"<p><strong>Current Chapter:</strong> {current.chapter_id if current else 'N/A'}</p>"
            f"<p><strong>Completed Chapters:</strong> {len(completed)}</p>"
            f"<table border='1'><tr><th>Chapter ID</th><th>Status</th><th>Completed At</th></tr>{rows or '<tr><td colspan=3>No chapter data</td></tr>'}</table>"
        )
    return f"<h1>Child Chapter Completion</h1>{''.join(sections)}<p><a href='/parent-dashboard'>Back to Dashboard</a></p>"


def get_teacher_credentials() -> tuple[str, str]:
    return (
        os.environ.get("TEACHER_EMAIL", "teacher@spiral.com"),
        os.environ.get("TEACHER_PASSWORD", "teacher123"),
    )


def get_school_admin_credentials() -> tuple[str, str]:
    return (
        os.environ.get("SCHOOL_ADMIN_EMAIL", "schooladmin@spiral.com"),
        os.environ.get("SCHOOL_ADMIN_PASSWORD", "schooladmin123"),
    )


@app.route("/teacher-login", methods=["GET", "POST"])
def teacher_login():
    if request.method == "GET":
        return """        <!doctype html>
        <html lang='en'>
          <head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Teacher Login</title></head>
          <body>
            <h1>Teacher Login</h1>
            <form method="post" action="/teacher-login">
              <label>Email: <input type="email" name="email" required></label><br><br>
              <label>Password: <input type="password" name="password" required></label><br><br>
              <button type="submit">Login</button>
            </form>
      </body>
        </html>
        """

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    teacher = Teacher.query.filter_by(email=email).first()
    if teacher and check_password_hash(teacher.password_hash, password):
        session["teacher_logged_in"] = True
        session["teacher_id"] = teacher.id
        return redirect(url_for("teacher_dashboard"))

    teacher_email, teacher_password = get_teacher_credentials()
    if email != teacher_email or password != teacher_password:
        return "<h2>Invalid teacher credentials</h2><p><a href='/teacher-login'>Try again</a></p>", 401

    session["teacher_logged_in"] = True
    session["teacher_id"] = 1
    return redirect(url_for("teacher_dashboard"))


@app.route("/teacher-dashboard", methods=["GET"])
def teacher_dashboard():
    if session.get("teacher_logged_in") is not True:
        return redirect(url_for("teacher_login"))
    teacher_id = session.get("teacher_id")
    if teacher_id is None:
        return redirect(url_for("teacher_login"))

    teacher_classes = (
        Class.query.filter_by(teacher_id=int(teacher_id))
        .order_by(Class.created_at.desc(), Class.id.desc())
        .all()
    )

    rows = []
    for classroom in teacher_classes:
        class_students = Student.query.filter_by(class_id=classroom.id).all()
        student_count = len(class_students)
        student_ids = [student.id for student in class_students]
        average_score = "-"
        weak_topics_summary = "-"

        if student_ids:
            latest_scores = []
            for student in class_students:
                latest_result = get_latest_student_result(student.id)
                if latest_result:
                    latest_scores.append(latest_result.score)

            if latest_scores:
                average_score = f"{round(sum(latest_scores) / len(latest_scores), 1)}%"

            weak_topic_counts = {}
            topic_progress_rows = (
                StudentTopicProgress.query.filter(
                    StudentTopicProgress.student_id.in_(student_ids)
                )
                .order_by(
                    StudentTopicProgress.last_updated.desc(),
                    StudentTopicProgress.id.desc(),
                )
                .all()
            )
            for progress in topic_progress_rows:
                if progress.latest_score < 50:
                    weak_topic_counts[progress.topic_en] = (
                        weak_topic_counts.get(progress.topic_en, 0) + 1
                    )

            if weak_topic_counts:
                top_weak_topics = sorted(
                    weak_topic_counts.items(), key=lambda item: item[1], reverse=True
                )[:3]
                weak_topics_summary = ", ".join(
                    [f"{escape(topic)} ({count})" for topic, count in top_weak_topics]
                )

        rows.append(
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'>{escape(classroom.class_name)}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{student_count}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{average_score}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{weak_topics_summary}</td>
              <td style='border:1px solid #ccc;padding:8px;'><a href='/teacher/class/{classroom.id}'>View Class</a></td>
            </tr>
            """
        )

    class_rows = "".join(rows)

    return f"""
    <!doctype html>
    <html lang='en'>
      <head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Teacher Dashboard</title></head>
      <body>
        <h1>Teacher Dashboard</h1>
        <p><a href='/teacher/create-class'>Create Class</a></p>
        <p><a href='/teacher/chapter-progress'>Chapter Progress</a></p>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Class Name</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Number of Students</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Average Score</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Weak Topics Summary</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Action</th>
            </tr>
          </thead>
          <tbody>{class_rows if class_rows else "<tr><td colspan='5' style='border:1px solid #ccc;padding:8px;'>No classes found.</td></tr>"}</tbody>
        </table>
        <p><a href='/teacher-logout'>Logout</a></p>
      </body>
    </html>
    """


@app.route("/teacher/class/<int:class_id>", methods=["GET"])
def teacher_class_details(class_id: int):
    if session.get("teacher_logged_in") is not True:
        return redirect(url_for("teacher_login"))

    teacher_id = session.get("teacher_id")
    if teacher_id is None:
        return redirect(url_for("teacher_login"))

    classroom = Class.query.filter_by(id=class_id, teacher_id=int(teacher_id)).first()
    if not classroom:
        return "<h2>Class not found</h2>", 404

    students = (
        Student.query.filter_by(class_id=classroom.id)
        .order_by(Student.name.asc(), Student.id.asc())
        .all()
    )

    labels = {
        "en": {
            "class_overview": "Class Overview",
            "weak_topics": "Weak Topics",
            "top_students": "Top Students",
            "needs_improvement": "Needs Improvement",
            "total_students": "Total students",
            "average_score": "Average score (latest results)",
            "below_50": "Number of students below 50%",
            "topic": "Topic",
            "avg_score": "Average Score",
            "student": "Student",
            "score": "Score",
        },
        "si": {
            "class_overview": "පන්තිය සාරාංශය",
            "weak_topics": "දුර්වල කොටස්",
            "top_students": "ඉහළම සිසුන්",
            "needs_improvement": "වැඩිදියුණු විය යුතු සිසුන්",
            "total_students": "මුළු සිසුන්",
            "average_score": "සාමාන්‍ය ලකුණු (අවසන් ප්‍රතිඵල)",
            "below_50": "50% ට අඩු සිසුන් ගණන",
            "topic": "මාතෘකාව",
            "avg_score": "සාමාන්‍ය ලකුණ",
            "student": "ශිෂ්‍යයා",
            "score": "ලකුණ",
        },
    }

    rows = []
    latest_scored_students = []
    topic_totals: dict[str, dict[str, float | int | str]] = {}
    for student in students:
        latest_result = get_latest_student_result(student.id)
        latest_score = f"{latest_result.score}%" if latest_result else "-"
        if latest_result:
            latest_scored_students.append({"name": student.name, "score": float(latest_result.score)})
            topic_performance = (
                StudentTopicPerformance.query.filter_by(student_result_id=latest_result.id)
                .order_by(StudentTopicPerformance.id.asc())
                .all()
            )
            for topic_item in topic_performance:
                key = topic_item.topic_en.strip().lower()
                if key not in topic_totals:
                    topic_totals[key] = {
                        "topic_en": topic_item.topic_en,
                        "topic_si": topic_item.topic_si,
                        "score_sum": 0.0,
                        "count": 0,
                    }
                topic_totals[key]["score_sum"] = float(topic_totals[key]["score_sum"]) + float(topic_item.percentage)
                topic_totals[key]["count"] = int(topic_totals[key]["count"]) + 1

        topic_progress_rows = (
            StudentTopicProgress.query.filter_by(student_id=student.id)
            .order_by(StudentTopicProgress.last_updated.desc(), StudentTopicProgress.id.desc())
            .all()
        )
        weak_topics = []
        progress_entries = []
        for progress in topic_progress_rows:
            topic_name = escape(progress.topic_en)
            progress_entries.append(
                f"{topic_name} ({progress.latest_score}%, {escape(progress.mastery_level_en)})"
            )
            if progress.latest_score < 50:
                weak_topics.append(topic_name)

        weak_topics_html = ", ".join(dict.fromkeys(weak_topics)) if weak_topics else "-"
        progress_html = "<br>".join(progress_entries) if progress_entries else "-"

        rows.append(
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'>{escape(student.name)}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{latest_score}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{weak_topics_html}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{progress_html}</td>
            </tr>
            """
        )

    student_rows = "".join(rows)
    homework_summary_rows = get_homework_summary_for_class(classroom.id)
    homework_row_list = []
    for item in homework_summary_rows:
        average_score_text = f"{item['average_score']:.1f}%" if item["average_score"] is not None else "-"
        homework_row_list.append(
            f"<tr><td style='border:1px solid #ccc;padding:8px;'>{escape(item['assignment'].title)}</td><td style='border:1px solid #ccc;padding:8px;'>{item['assignment'].due_date.strftime('%Y-%m-%d')}</td><td style='border:1px solid #ccc;padding:8px;'>{item['total_students']}</td><td style='border:1px solid #ccc;padding:8px;'>{item['submission_count']}</td><td style='border:1px solid #ccc;padding:8px;'>{average_score_text}</td><td style='border:1px solid #ccc;padding:8px;'><a href='/teacher/homework/{item['assignment'].id}'>View Details / විස්තර බලන්න</a></td></tr>"
        )
    homework_rows_html = "".join(homework_row_list)
    total_students = len(students)
    average_score = (
        sum(item["score"] for item in latest_scored_students) / len(latest_scored_students)
        if latest_scored_students
        else 0.0
    )
    below_50_count = sum(1 for item in latest_scored_students if item["score"] < 50)

    weak_topics_ranked = []
    for data in topic_totals.values():
        avg = float(data["score_sum"]) / int(data["count"])
        weak_topics_ranked.append(
            {
                "topic_en": str(data["topic_en"]),
                "topic_si": str(data["topic_si"]),
                "avg": avg,
            }
        )
    weak_topics_ranked.sort(key=lambda item: item["avg"])
    top_3_weak_topics = weak_topics_ranked[:3]

    ranked_students = sorted(latest_scored_students, key=lambda item: item["score"], reverse=True)
    top_5_students = ranked_students[:5]
    bottom_5_students = sorted(latest_scored_students, key=lambda item: item["score"])[:5]

    weak_topic_rows = "".join(
        f"<tr><td style='border:1px solid #ccc;padding:8px;'>{escape(item['topic_en'])}<br><small>{escape(item['topic_si'])}</small></td><td style='border:1px solid #ccc;padding:8px;'>{item['avg']:.1f}%</td></tr>"
        for item in top_3_weak_topics
    )
    top_student_rows = "".join(
        f"<tr><td style='border:1px solid #ccc;padding:8px;'>{escape(item['name'])}</td><td style='border:1px solid #ccc;padding:8px;'>{item['score']:.1f}%</td></tr>"
        for item in top_5_students
    )
    bottom_student_rows = "".join(
        f"<tr><td style='border:1px solid #ccc;padding:8px;'>{escape(item['name'])}</td><td style='border:1px solid #ccc;padding:8px;'>{item['score']:.1f}%</td></tr>"
        for item in bottom_5_students
    )
    class_tests = ClassTest.query.filter_by(class_id=classroom.id).order_by(ClassTest.test_date.asc(), ClassTest.id.desc()).all()
    class_test_rows = []
    for class_test in class_tests:
        submissions = ClassTestSubmission.query.filter_by(class_test_id=class_test.id).all()
        submissions_count = len(submissions)
        avg_score = f"{(sum(item.score for item in submissions) / submissions_count):.1f}%" if submissions_count else "-"
        class_test_rows.append(
            f"<tr><td style='border:1px solid #ccc;padding:8px;'>{escape(class_test.title)}</td><td style='border:1px solid #ccc;padding:8px;'>{class_test.test_date.strftime('%Y-%m-%d')}</td><td style='border:1px solid #ccc;padding:8px;'>{submissions_count}</td><td style='border:1px solid #ccc;padding:8px;'>{avg_score}</td><td style='border:1px solid #ccc;padding:8px;'><a href='/teacher/test/{class_test.id}'>ප්‍රතිඵල බලන්න / View Results</a></td></tr>"
        )
    class_tests_html = "".join(class_test_rows)

    return f"""
    <!doctype html>
    <html lang='en'>
      <head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Class Details</title></head>
      <body>
        <h1>Class: {escape(classroom.class_name)}</h1>
        <p>Grade: {display_grade(classroom.grade)}</p>
        <p><a href='/teacher/assign-students/{classroom.id}'>Assign Students</a></p>
        <p><a href='/teacher/class/{classroom.id}/assign-homework'>Assign Homework</a></p>
        <p><a href='/teacher/class/{classroom.id}/create-test'>නව පරීක්ෂාවක් සාදන්න / Create Test</a></p>
        <p><a href='/teacher/chapter-progress'>Chapter Progress</a></p>
        <h2>{labels["en"]["class_overview"]} / {labels["si"]["class_overview"]}</h2>
        <ul>
          <li><strong>{labels["en"]["total_students"]}</strong> / {labels["si"]["total_students"]}: {total_students}</li>
          <li><strong>{labels["en"]["average_score"]}</strong> / {labels["si"]["average_score"]}: {average_score:.1f}%</li>
          <li><strong>{labels["en"]["below_50"]}</strong> / {labels["si"]["below_50"]}: {below_50_count}</li>
        </ul>
        <h2>{labels["en"]["weak_topics"]} / {labels["si"]["weak_topics"]}</h2>
        <table style='border-collapse:collapse;width:100%;margin-bottom:16px;'>
          <thead><tr><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels["en"]["topic"]} / {labels["si"]["topic"]}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels["en"]["avg_score"]} / {labels["si"]["avg_score"]}</th></tr></thead>
          <tbody>{weak_topic_rows if weak_topic_rows else "<tr><td colspan='2' style='border:1px solid #ccc;padding:8px;'>No topic performance data available.</td></tr>"}</tbody>
        </table>
        <h2>{labels["en"]["top_students"]} / {labels["si"]["top_students"]}</h2>
        <table style='border-collapse:collapse;width:100%;margin-bottom:16px;'>
          <thead><tr><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels["en"]["student"]} / {labels["si"]["student"]}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels["en"]["score"]} / {labels["si"]["score"]}</th></tr></thead>
          <tbody>{top_student_rows if top_student_rows else "<tr><td colspan='2' style='border:1px solid #ccc;padding:8px;'>No results yet.</td></tr>"}</tbody>
        </table>
        <h2>{labels["en"]["needs_improvement"]} / {labels["si"]["needs_improvement"]}</h2>
        <table style='border-collapse:collapse;width:100%;margin-bottom:16px;'>
          <thead><tr><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels["en"]["student"]} / {labels["si"]["student"]}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels["en"]["score"]} / {labels["si"]["score"]}</th></tr></thead>
          <tbody>{bottom_student_rows if bottom_student_rows else "<tr><td colspan='2' style='border:1px solid #ccc;padding:8px;'>No results yet.</td></tr>"}</tbody>
        </table>
        <h2>Homework Overview / ගෙදර වැඩ සාරාංශය</h2>
        <table style='border-collapse:collapse;width:100%;margin-bottom:16px;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Title / මාතෘකාව</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Due Date / අවසන් දිනය</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Total Students / මුළු සිසුන්</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Submissions / ඉදිරිපත් කිරීම්</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Average Score / සාමාන්‍ය ලකුණ</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Action / ක්‍රියාව</th>
            </tr>
          </thead>
          <tbody>{homework_rows_html if homework_rows_html else "<tr><td colspan='6' style='border:1px solid #ccc;padding:8px;'>No homework assigned yet. / තවම ගෙදර වැඩ නියම කර නොමැත.</td></tr>"}</tbody>
        </table>
        <h2>Class Tests</h2>
        <table style='border-collapse:collapse;width:100%;margin-bottom:16px;'>
          <thead><tr><th style='border:1px solid #ccc;padding:8px;text-align:left;'>Title</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>Test Date</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>Submissions count</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>Average Score</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>Action</th></tr></thead>
          <tbody>{class_tests_html if class_tests_html else "<tr><td colspan='5' style='border:1px solid #ccc;padding:8px;'>No class tests yet.</td></tr>"}</tbody>
        </table>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Student List</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Scores</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Weak Topics</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Progress</th>
            </tr>
          </thead>
          <tbody>{student_rows if student_rows else "<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>No students in this class.</td></tr>"}</tbody>
        </table>
        <p><a href='/teacher-dashboard'>Back to Dashboard</a></p>
      </body>
    </html>
    """


@app.route("/teacher/create-class", methods=["GET", "POST"])
def teacher_create_class():
    if session.get("teacher_logged_in") is not True:
        return redirect(url_for("teacher_login"))

    if request.method == "GET":
        return f"""
        <!doctype html>
        <html lang='en'>
          <head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Create Class</title></head>
          <body>
            <h1>Create Class</h1>
            <form method="post" action="/teacher/create-class">
              <label>Class name: <input type="text" name="class_name" placeholder="Grade 6A" required></label><br><br>
              <label>Grade: <select name="grade" required>{grade_options_html()}</select></label><br><br>
              <button type="submit">Create Class</button>
            </form>
            <p><a href='/teacher-dashboard'>Back to Dashboard</a></p>
          </body>
        </html>
        """

    class_name = request.form.get("class_name", "").strip()
    grade = normalize_grade(request.form.get("grade"))
    teacher_id = session.get("teacher_id")

    if not class_name or not is_valid_grade(grade) or teacher_id is None:
        return "<h2>Invalid class data</h2><p><a href='/teacher/create-class'>Try again</a></p>", 400

    new_class = Class(class_name=class_name, grade=grade, teacher_id=int(teacher_id))
    db.session.add(new_class)
    db.session.commit()
    return redirect(url_for("teacher_dashboard"))


@app.route("/teacher/assign-students/<int:class_id>", methods=["GET", "POST"])
def teacher_assign_students(class_id: int):
    if session.get("teacher_logged_in") is not True:
        return redirect(url_for("teacher_login"))

    teacher_id = session.get("teacher_id")
    if teacher_id is None:
        return redirect(url_for("teacher_login"))

    classroom = Class.query.filter_by(id=class_id, teacher_id=int(teacher_id)).first()
    if not classroom:
        return "<h2>Class not found</h2>", 404

    if request.method == "POST":
        selected_student_ids = {
            int(student_id)
            for student_id in request.form.getlist("student_ids")
            if student_id.isdigit()
        }

        grade_students = Student.query.filter_by(grade=classroom.grade).all()
        for student in grade_students:
            if student.id in selected_student_ids:
                student.class_id = classroom.id

        db.session.commit()
        return redirect(url_for("teacher_dashboard"))

    grade_students = (
        Student.query.filter_by(grade=classroom.grade)
        .order_by(Student.name.asc(), Student.id.asc())
        .all()
    )

    student_rows = "".join(
        [
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'><input type='checkbox' name='student_ids' value='{student.id}' {'checked' if student.class_id == classroom.id else ''}></td>
              <td style='border:1px solid #ccc;padding:8px;'>{escape(student.name)}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{student.grade}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{escape(student.medium)}</td>
            </tr>
            """
            for student in grade_students
        ]
    )

    return f"""
    <!doctype html>
    <html lang='en'>
      <head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Assign Students</title></head>
      <body>
        <h1>Assign Students to {escape(classroom.class_name)}</h1>
        <p>Grade: {display_grade(classroom.grade)}</p>
        <form method='post' action='/teacher/assign-students/{classroom.id}'>
          <table style='border-collapse:collapse;width:100%;'>
            <thead>
              <tr>
                <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Select</th>
                <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Name</th>
                <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Grade</th>
                <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Medium</th>
              </tr>
            </thead>
            <tbody>{student_rows if student_rows else "<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>No students found for this grade.</td></tr>"}</tbody>
          </table>
          <br>
          <button type='submit'>Save Assignments</button>
        </form>
        <p><a href='/teacher-dashboard'>Back to Dashboard</a></p>
      </body>
    </html>
    """


@app.route("/teacher/student/<int:student_id>", methods=["GET"])
def teacher_student_details(student_id: int):
    if session.get("teacher_logged_in") is not True:
        return redirect(url_for("teacher_login"))
    student = db.session.get(Student, student_id)
    if not student:
        return "<h2>Student not found</h2>", 404

    is_sinhala = student.medium == "Sinhala"
    labels = {
        "title": "ශිෂ්‍ය විස්තර" if is_sinhala else "Student Details",
        "student_info": "ශිෂ්‍ය තොරතුරු" if is_sinhala else "Student Info",
        "name": "නම" if is_sinhala else "Name",
        "grade": "ශ්‍රේණිය" if is_sinhala else "Grade",
        "medium": "මාධ්‍යය" if is_sinhala else "Medium",
        "result_history": "ප්‍රතිඵල ඉතිහාසය" if is_sinhala else "Result History",
        "topic_performance": "මාතෘකා අනුව ක්‍රියාකාරීත්වය" if is_sinhala else "Topic-wise Performance",
        "weak_topics": "දුර්වල මාතෘකා (< 50%)" if is_sinhala else "Weak Topics (score < 50%)",
        "date": "දිනය" if is_sinhala else "Date",
        "score": "ලකුණු" if is_sinhala else "Score",
        "level": "මට්ටම" if is_sinhala else "Level",
        "correct": "නිවැරදි" if is_sinhala else "Correct",
        "topic": "මාතෘකාව" if is_sinhala else "Topic",
        "percentage": "ප්‍රතිශතය" if is_sinhala else "Percentage",
        "status": "තත්ත්වය" if is_sinhala else "Status",
        "back": "ඩෑෂ්බෝඩ් වෙත ආපසු" if is_sinhala else "Back to Dashboard",
    }

    result_history = (
        StudentResult.query.filter_by(student_id=student.id)
        .order_by(StudentResult.created_at.desc(), StudentResult.id.desc())
        .all()
    )

    history_rows = "".join(
        f"""
        <tr><td style='border:1px solid #ccc;padding:8px;'>{r.created_at.strftime('%Y-%m-%d %H:%M:%S')}</td><td style='border:1px solid #ccc;padding:8px;'>{r.score}%</td><td style='border:1px solid #ccc;padding:8px;'>{r.level}</td><td style='border:1px solid #ccc;padding:8px;'>{r.correct_answers}/{r.total_questions}</td></tr>
        """
        for r in result_history
    )

    medium_key = "si" if is_sinhala else "en"
    topic_rows_list = []
    weak_topics = []
    for result in result_history:
        topic_performance = StudentTopicPerformance.query.filter_by(student_result_id=result.id).order_by(StudentTopicPerformance.id.asc()).all()
        for topic in topic_performance:
            topic_name = getattr(topic, f"topic_{medium_key}")
            status = getattr(topic, f"status_{medium_key}")
            topic_rows_list.append(f"""
                <tr><td style='border:1px solid #ccc;padding:8px;'>{topic_name}</td><td style='border:1px solid #ccc;padding:8px;'>{topic.correct_count}/{topic.total_count}</td><td style='border:1px solid #ccc;padding:8px;'>{topic.percentage}%</td><td style='border:1px solid #ccc;padding:8px;'>{status}</td></tr>
                """)
            if topic.percentage < 50:
                weak_topics.append(topic_name)

    topic_rows = "".join(topic_rows_list)
    weak_topic_html = "<br>".join(dict.fromkeys(weak_topics)) if weak_topics else "-"
    topic_progress_rows = (
        StudentTopicProgress.query.filter_by(student_id=student.id)
        .order_by(StudentTopicProgress.last_updated.desc(), StudentTopicProgress.id.desc())
        .all()
    )
    progress_rows_html = "".join(
        f"<tr><td style='border:1px solid #ccc;padding:8px;'>{item.topic_si if is_sinhala else item.topic_en}</td>"
        f"<td style='border:1px solid #ccc;padding:8px;'>{item.latest_score}%</td>"
        f"<td style='border:1px solid #ccc;padding:8px;'>{item.mastery_level_si if is_sinhala else item.mastery_level_en}</td>"
        f"<td style='border:1px solid #ccc;padding:8px;'>{item.attempts_count}</td>"
        f"<td style='border:1px solid #ccc;padding:8px;'>{item.last_updated.strftime('%Y-%m-%d %H:%M:%S')}</td></tr>"
        for item in topic_progress_rows
    )

    return f"""
    <!doctype html>
    <html lang='{'si' if is_sinhala else 'en'}'>
      <head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{labels['title']}</title></head>
      <body>
        <h1>{labels['title']}</h1><h2>{labels['student_info']}</h2>
        <p><strong>{labels['name']}:</strong> {student.name}</p><p><strong>{labels['grade']}:</strong> {student.grade}</p><p><strong>{labels['medium']}:</strong> {student.medium}</p>
        <h2>{labels['result_history']}</h2>
        <table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['date']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['score']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['level']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['correct']}</th></tr></thead><tbody>{history_rows if history_rows else "<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>-</td></tr>"}</tbody></table>
        <h2>{labels['topic_performance']}</h2>
        <table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['topic']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['correct']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['percentage']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['status']}</th></tr></thead><tbody>{topic_rows if topic_rows else "<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>-</td></tr>"}</tbody></table>
        <h2>{labels['weak_topics']}</h2><p>{weak_topic_html}</p><p><a href='/teacher-dashboard'>{labels['back']}</a></p>
        <h2>{"මාතෘකා ප්‍රගතිය" if is_sinhala else "Student Topic Progress"}</h2>
        <table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['topic']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['score']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['status']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{"උත්සාහ ගණන" if is_sinhala else "Attempts Count"}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{"අවසන් යාවත්කාලීන" if is_sinhala else "Last Updated"}</th></tr></thead><tbody>{progress_rows_html if progress_rows_html else "<tr><td colspan='5' style='border:1px solid #ccc;padding:8px;'>-</td></tr>"}</tbody></table>
      </body></html>
    """


@app.route("/teacher/class/<int:class_id>/assign-homework", methods=["GET", "POST"])
def teacher_assign_homework(class_id: int):
    if session.get("teacher_logged_in") is not True:
        return redirect(url_for("teacher_login"))
    teacher_id = session.get("teacher_id")
    if teacher_id is None:
        return redirect(url_for("teacher_login"))
    classroom = Class.query.filter_by(id=class_id, teacher_id=int(teacher_id)).first()
    if not classroom:
        return "<h2>Class not found</h2>", 404
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        subject = (request.form.get("subject") or "Math").strip() or "Math"
        subject = (request.form.get("subject") or "Math").strip() or "Math"
        topic_en = (request.form.get("topic_en") or "").strip()
        topic_si = (request.form.get("topic_si") or "").strip()
        due_date_raw = (request.form.get("due_date") or "").strip()
        difficulty_level = int((request.form.get("difficulty_level") or "1").strip() or "1")
        try:
            due_date = datetime.strptime(due_date_raw, "%Y-%m-%d").date()
        except ValueError:
            return "<h2>Invalid due date</h2><p><a href=''>Try again</a></p>", 400
        if not title or not topic_en or not topic_si or difficulty_level < 1 or difficulty_level > 5:
            return "<h2>Invalid homework data</h2><p><a href=''>Try again</a></p>", 400
        db.session.add(
            HomeworkAssignment(
                class_id=classroom.id,
                teacher_id=int(teacher_id),
                title=title,
                grade=classroom.grade,
                subject=subject,
                topic_en=topic_en,
                topic_si=topic_si,
                difficulty_level=difficulty_level,
                due_date=due_date,
            )
        )
        db.session.commit()
        return redirect(url_for("teacher_class_details", class_id=classroom.id))
    return f"""
    <!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Assign Homework</title></head><body>
    <h1>Assign Homework - {escape(classroom.class_name)}</h1>
    <form method='post'>
      <input type='hidden' name='grade' value='{escape(classroom.grade)}'>
      <label>Title: <input type='text' name='title' required></label><br><br>
      <label>Subject: <select name='subject' required>{subject_options_html(classroom.grade, 'Math')}</select></label><br><br>
      <label>Term: <select name='term_id'><option value=''>Select term</option></select></label><br><br>
      <label>Module: <select name='module_id'><option value=''>Select module</option></select></label><br><br>
      <label>Chapter: <select name='chapter_id'><option value=''>Select chapter</option></select></label><br><br>
      <p id='syllabus-debug-message' style='color:#b45309;'></p>
      <label>Topic (English): <input type='text' name='topic_en' required></label><br><br>
      <label>Topic (Sinhala): <input type='text' name='topic_si' required></label><br><br>
      <label>Difficulty (1-5): <input type='number' min='1' max='5' name='difficulty_level' value='1' required></label><br><br>
      <label>Due date: <input type='date' name='due_date' required></label><br><br>
      <button type='submit'>Save Homework</button>
    </form>
    <p><a href='/teacher/class/{classroom.id}'>Back to Class</a></p>
    {dependent_dropdown_script(grade_selector="input[name='grade']", subject_selector="select[name='subject']")}
    </body></html>
    """


@app.route("/teacher/homework/<int:homework_id>", methods=["GET"])
def teacher_homework_details(homework_id: int):
    if session.get("teacher_logged_in") is not True:
        return redirect(url_for("teacher_login"))

    teacher_id = session.get("teacher_id")
    if teacher_id is None:
        return redirect(url_for("teacher_login"))

    homework = HomeworkAssignment.query.filter_by(id=homework_id, teacher_id=int(teacher_id)).first()
    if not homework:
        return "<h2>Homework not found</h2>", 404

    students = (
        Student.query.filter_by(class_id=homework.class_id)
        .order_by(Student.name.asc(), Student.id.asc())
        .all()
    )
    submissions = HomeworkSubmission.query.filter_by(homework_id=homework.id).all()
    submission_by_student = {item.student_id: item for item in submissions}

    total_students = len(students)
    submitted_scores = [item.score for item in submissions]
    average_score = round(sum(submitted_scores) / len(submitted_scores), 1) if submitted_scores else 0.0
    not_submitted_count = sum(1 for student in students if student.id not in submission_by_student)
    weak_students_count = sum(1 for item in submissions if item.score < 50)

    detail_rows = []
    for student in students:
        submission = submission_by_student.get(student.id)
        is_submitted = submission is not None
        score_value = f"{submission.score:.1f}%" if is_submitted else "-"
        reminder_cell = "-"
        if not is_submitted:
            status = "Not Submitted / ඉදිරිපත් කර නැත"
            reminder_url = url_for("teacher_remind_homework_student", homework_id=homework.id, student_id=student.id)
            reminder_cell = (
                f"<form method='post' action='{reminder_url}' style='margin:0;'>"
                "<button type='submit'>Send Reminder</button>"
                "</form>"
            )
        elif submission.score < 50:
            status = "Weak / දුර්වල"
        else:
            status = "Completed / සම්පූර්ණයි"

        detail_rows.append(
            f"<tr><td style='border:1px solid #ccc;padding:8px;'>{escape(student.name)}</td><td style='border:1px solid #ccc;padding:8px;'>{'Yes / ඔව්' if is_submitted else 'No / නැහැ'}</td><td style='border:1px solid #ccc;padding:8px;'>{score_value}</td><td style='border:1px solid #ccc;padding:8px;'>{status}</td><td style='border:1px solid #ccc;padding:8px;'>{reminder_cell}</td></tr>"
        )

    return f"""
    <!doctype html>
    <html lang='en'>
      <head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Homework Details</title></head>
      <body>
        <h1>Homework Details / ගෙදර වැඩ විස්තර</h1>
        <p><strong>Title / මාතෘකාව:</strong> {escape(homework.title)}</p>
        <p><strong>Due Date / අවසන් දිනය:</strong> {homework.due_date.strftime('%Y-%m-%d')}</p>
        <h2>Summary / සාරාංශය</h2>
        <ul>
          <li><strong>Average score / සාමාන්‍ය ලකුණ:</strong> {average_score:.1f}%</li>
          <li><strong>Total students / මුළු සිසුන්:</strong> {total_students}</li>
          <li><strong>Not submitted / ඉදිරිපත් නොකළ:</strong> {not_submitted_count}</li>
          <li><strong>Weak students (&lt;50%) / දුර්වල සිසුන්:</strong> {weak_students_count}</li>
        </ul>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Student Name / ශිෂ්‍ය නම</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Submitted / ඉදිරිපත් කළාද</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Score / ලකුණ</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Status / තත්ත්වය</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Reminder / මතක් කිරීම</th>
            </tr>
          </thead>
          <tbody>{''.join(detail_rows) if detail_rows else "<tr><td colspan='5' style='border:1px solid #ccc;padding:8px;'>No students found in this class.</td></tr>"}</tbody>
        </table>
        <p><a href='/teacher/class/{homework.class_id}'>Back to Class / පන්තියට ආපසු</a></p>
      </body>
    </html>
    """



@app.route("/teacher/homework/<int:homework_id>/remind/<int:student_id>", methods=["POST"])
def teacher_remind_homework_student(homework_id: int, student_id: int):
    if session.get("teacher_logged_in") is not True:
        return redirect(url_for("teacher_login"))

    teacher_id = session.get("teacher_id")
    if teacher_id is None:
        return redirect(url_for("teacher_login"))

    homework = HomeworkAssignment.query.filter_by(id=homework_id, teacher_id=int(teacher_id)).first()
    if not homework:
        return "<h2>Homework not found</h2>", 404

    student = Student.query.filter_by(id=student_id, class_id=homework.class_id).first()
    if not student:
        return "<h2>Student not found for this class</h2>", 404

    existing_submission = HomeworkSubmission.query.filter_by(homework_id=homework.id, student_id=student.id).first()
    if existing_submission:
        return redirect(url_for("teacher_homework_details", homework_id=homework.id))

    due_date_text = homework.due_date.strftime("%Y-%m-%d")
    message_en = f"Reminder: {student.name} has pending homework: {homework.title}. Due date: {due_date_text}."
    message_si = f"මතක් කිරීම: {student.name} සඳහා {homework.title} ගෙදර වැඩ තවම සම්පූර්ණ කර නොමැත. අවසන් දිනය: {due_date_text}."

    db.session.add(
        ParentNotification(
            student_id=student.id,
            parent_email=student.parent_email or student.email,
            message_en=message_en,
            message_si=message_si,
        )
    )
    db.session.commit()

    selected_message = message_si if student.medium == "Sinhala" else message_en
    parent_mobile = (student.mobile or "").strip()
    whatsapp_link_html = ""
    if parent_mobile:
        whatsapp_link = f"https://wa.me/{parent_mobile}?text={quote_plus(selected_message)}"
        whatsapp_link_html = (
            f"<p><a href='{whatsapp_link}' target='_blank' rel='noopener noreferrer'>Send via WhatsApp</a></p>"
        )

    return f"""
    <!doctype html>
    <html lang='en'>
      <head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Reminder Created</title></head>
      <body>
        <h2>Reminder created successfully.</h2>
        <p>Notification saved for {escape(student.name)}.</p>
        {whatsapp_link_html}
        <p><a href='/teacher/homework/{homework.id}'>Back to Homework Details</a></p>
      </body>
    </html>
    """


@app.route("/teacher/class/<int:class_id>/create-test", methods=["GET", "POST"])
def teacher_create_class_test(class_id: int):
    if session.get("teacher_logged_in") is not True:
        return redirect(url_for("teacher_login"))
    teacher_id = session.get("teacher_id")
    if teacher_id is None:
        return redirect(url_for("teacher_login"))
    classroom = Class.query.filter_by(id=class_id, teacher_id=int(teacher_id)).first()
    if not classroom:
        return "<h2>Class not found</h2>", 404
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        topic_en = (request.form.get("topic_en") or "").strip()
        topic_si = (request.form.get("topic_si") or topic_en).strip()
        test_date_raw = (request.form.get("test_date") or "").strip()
        difficulty_level = int((request.form.get("difficulty_level") or "1").strip() or "1")
        duration_minutes = int((request.form.get("duration_minutes") or "30").strip() or "30")
        try:
            test_date = datetime.strptime(test_date_raw, "%Y-%m-%d").date()
        except ValueError:
            return "<h2>Invalid test date</h2><p><a href=''>Try again</a></p>", 400
        if not title or not topic_en or difficulty_level < 1 or difficulty_level > 5 or duration_minutes <= 0:
            return "<h2>Invalid test data</h2><p><a href=''>Try again</a></p>", 400
        db.session.add(ClassTest(class_id=classroom.id, teacher_id=int(teacher_id), title=title, grade=classroom.grade, subject=subject, topic_en=topic_en, topic_si=topic_si, difficulty_level=difficulty_level, test_date=test_date, duration_minutes=duration_minutes))
        db.session.commit()
        return redirect(url_for("teacher_class_details", class_id=classroom.id))
    return f"""<!doctype html><html><body><h1>Create Test - {escape(classroom.class_name)}</h1><form method='post'>
    <input type='hidden' name='grade' value='{escape(classroom.grade)}'>
    <label>Title: <input type='text' name='title' required></label><br><br>
    <label>Subject: <select name='subject' required>{subject_options_html(classroom.grade, 'Math')}</select></label><br><br>
    <label>Term: <select name='term_id'><option value=''>Select term</option></select></label><br><br>
    <label>Module: <select name='module_id'><option value=''>Select module</option></select></label><br><br>
    <label>Chapter: <select name='chapter_id'><option value=''>Select chapter</option></select></label><br><br>
    <p id='syllabus-debug-message' style='color:#b45309;'></p>
    <label>Topic (English): <input type='text' name='topic_en' required></label><br><br><label>Topic (Sinhala): <input type='text' name='topic_si'></label><br><br><label>Difficulty (1-5): <input type='number' min='1' max='5' name='difficulty_level' value='1' required></label><br><br><label>Test date: <input type='date' name='test_date' required></label><br><br><label>Duration (minutes): <input type='number' min='1' name='duration_minutes' value='30' required></label><br><br><button type='submit'>Save Test</button></form><p><a href='/teacher/class/{classroom.id}'>Back to Class</a></p>{dependent_dropdown_script(grade_selector="input[name='grade']", subject_selector="select[name='subject']")}</body></html>"""

@app.route("/teacher-logout", methods=["GET"])
def teacher_logout():
    session.pop("teacher_logged_in", None)
    session.pop("teacher_id", None)
    return redirect(url_for("teacher_login"))


@app.route("/teacher/chapter-progress", methods=["GET"])
def teacher_chapter_progress():
    if session.get("teacher_logged_in") is not True:
        return redirect(url_for("teacher_login"))
    teacher_id = session.get("teacher_id")
    teacher = db.session.get(Teacher, teacher_id) if teacher_id else None
    if not teacher:
        return "<h2>Teacher not found</h2>", 404
    students = Student.query.filter_by(class_id=teacher.class_id).order_by(Student.name.asc()).all()
    rows = []
    for s in students:
        completed = StudentChapterProgress.query.filter_by(student_id=s.id, status="completed").count()
        in_progress = StudentChapterProgress.query.filter_by(student_id=s.id, status="in_progress").count()
        rows.append(f"<tr><td>{escape(s.name)}</td><td>{completed}</td><td>{in_progress}</td></tr>")
    return f"<h1>Students by Chapter Progress</h1><table border='1'><tr><th>Student</th><th>Completed Chapters</th><th>In Progress</th></tr>{''.join(rows)}</table>"


@app.route("/logout", methods=["GET"])
def logout():
    session.pop("student_id", None)
    return redirect(url_for("login"))


def get_admin_credentials() -> tuple[str, str]:
    return (
        os.environ.get("ADMIN_EMAIL", "admin@spiral.com"),
        os.environ.get("ADMIN_PASSWORD", "admin123"),
    )


def parse_ai_questions_payload(content: str) -> list[dict]:
    payload = (content or "").strip()
    if payload.startswith("```"):
        lines = payload.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        payload = "\n".join(lines).strip()

    questions = json.loads(payload)
    if not isinstance(questions, list):
        raise ValueError("AI response must be a JSON array")

    required_fields = [
        "question_en", "question_si",
        "option_a_en", "option_a_si",
        "option_b_en", "option_b_si",
        "option_c_en", "option_c_si",
        "option_d_en", "option_d_si",
        "correct_option",
        "explanation_en", "explanation_si",
    ]

    for idx, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Question #{idx} is invalid")
        for field in required_fields:
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Missing field '{field}' in question #{idx}")
        if item["correct_option"].strip().upper() not in {"A", "B", "C", "D"}:
            raise ValueError(f"Invalid correct_option in question #{idx}")

    return questions


def normalize_fraction_text(text: str) -> str:
    normalized = text or ""
    normalized = re.sub(r"\\\(\s*", "", normalized)
    normalized = re.sub(r"\s*\\\)", "", normalized)
    normalized = re.sub(r"\\frac\s*\{\s*([^{}]+?)\s*\}\s*\{\s*([^{}]+?)\s*\}", r"\1/\2", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def admin_session_required():
    if session.get("admin_logged_in") is not True:
        return redirect(url_for("admin_login"))
    return None


def school_admin_session_required():
    if session.get("school_admin_logged_in") is not True:
        return redirect(url_for("login"))
    if not session.get("school_id"):
        return "<h2>No school assigned</h2>", 400
    return None


@app.route("/school-admin/dashboard", methods=["GET"])
def school_admin_dashboard():
    auth_redirect = school_admin_session_required()
    if auth_redirect:
        return auth_redirect
    school_id = int(session["school_id"])
    selected_medium = resolve_medium(request.args.get("medium") or "English")
    is_sinhala = selected_medium == "Sinhala"

    labels = {
        "school_performance": "පාසල් කාර්ය සාධනය" if is_sinhala else "School Performance",
        "weak_topics": "දුර්වල කොටස්" if is_sinhala else "Weak Topics",
        "students_at_risk": "අවදානම් සිසුන්" if is_sinhala else "Students at Risk",
        "top_students": "ඉහළම සිසුන්" if is_sinhala else "Top Students",
        "class_performance": "පන්ති කාර්ය සාධනය" if is_sinhala else "Class Performance",
    }

    total_teachers = Teacher.query.filter_by(school_id=school_id).count()
    school_students = Student.query.filter_by(school_id=school_id).all()
    total_students = len(school_students)
    student_ids = [student.id for student in school_students]
    teacher_ids = [teacher.id for teacher in Teacher.query.filter_by(school_id=school_id).all()]
    total_classes = Class.query.filter(Class.teacher_id.in_(teacher_ids)).count() if teacher_ids else 0
    total_tests = ClassTest.query.filter(ClassTest.teacher_id.in_(teacher_ids)).count() if teacher_ids else 0

    latest_results = []
    for student in school_students:
        latest_result = (
            StudentResult.query.filter_by(student_id=student.id)
            .order_by(StudentResult.created_at.desc(), StudentResult.id.desc())
            .first()
        )
        if latest_result:
            latest_results.append({"student": student, "result": latest_result})

    average_student_score = (
        sum(item["result"].score for item in latest_results) / len(latest_results) if latest_results else 0
    )
    average_practice_score = (
        db.session.query(db.func.avg(PracticeAttempt.score))
        .filter(PracticeAttempt.student_id.in_(student_ids))
        .scalar()
        if student_ids
        else 0
    )
    average_practice_score = float(average_practice_score or 0)

    topic_totals = {}
    for item in latest_results:
        topic_rows = StudentTopicPerformance.query.filter_by(student_result_id=item["result"].id).all()
        for row in topic_rows:
            topic_key = row.topic_si if is_sinhala else row.topic_en
            if topic_key not in topic_totals:
                topic_totals[topic_key] = {"correct": 0, "total": 0}
            topic_totals[topic_key]["correct"] += row.correct_count
            topic_totals[topic_key]["total"] += row.total_count

    weak_topics = sorted(
        [
            {
                "topic": topic,
                "score": (vals["correct"] / vals["total"] * 100) if vals["total"] else 0,
            }
            for topic, vals in topic_totals.items()
        ],
        key=lambda item: item["score"],
    )[:3]

    ranked_students = sorted(
        [{"name": item["student"].name, "score": float(item["result"].score)} for item in latest_results],
        key=lambda item: item["score"],
        reverse=True,
    )
    risk_students = [item for item in sorted(ranked_students, key=lambda row: row["score"]) if item["score"] < 50][:5]
    top_students = ranked_students[:5]

    class_performance = []
    classes = Class.query.filter(Class.teacher_id.in_(teacher_ids)).order_by(Class.class_name.asc()).all() if teacher_ids else []
    for classroom in classes:
        class_students = [student for student in school_students if student.class_id == classroom.id]
        class_latest_scores = []
        for student in class_students:
            latest_result = (
                StudentResult.query.filter_by(student_id=student.id)
                .order_by(StudentResult.created_at.desc(), StudentResult.id.desc())
                .first()
            )
            if latest_result:
                class_latest_scores.append(float(latest_result.score))
        avg_score = sum(class_latest_scores) / len(class_latest_scores) if class_latest_scores else 0
        class_performance.append({"class_name": classroom.class_name, "average_score": avg_score})

    weak_topic_rows = "".join(
        f"<tr><td style='border:1px solid #ccc;padding:8px;'>{escape(item['topic'])}</td><td style='border:1px solid #ccc;padding:8px;'>{item['score']:.1f}%</td></tr>"
        for item in weak_topics
    )
    risk_student_rows = "".join(
        f"<tr><td style='border:1px solid #ccc;padding:8px;'>{escape(item['name'])}</td><td style='border:1px solid #ccc;padding:8px;'>{item['score']:.1f}%</td></tr>"
        for item in risk_students
    )
    top_student_rows = "".join(
        f"<tr><td style='border:1px solid #ccc;padding:8px;'>{escape(item['name'])}</td><td style='border:1px solid #ccc;padding:8px;'>{item['score']:.1f}%</td></tr>"
        for item in top_students
    )
    class_rows = "".join(
        f"<tr><td style='border:1px solid #ccc;padding:8px;'>{escape(item['class_name'])}</td><td style='border:1px solid #ccc;padding:8px;'>{item['average_score']:.1f}%</td></tr>"
        for item in class_performance
    )

    return f"""<!doctype html><html><body><h1>School Admin Dashboard</h1>
    <p>Total teachers: {total_teachers}</p><p>Total students: {total_students}</p>
    <p>Total classes: {total_classes}</p><p>Total tests: {total_tests}</p>
    <h2>{labels['school_performance']}</h2>
    <p>Average student score: {average_student_score:.1f}%</p>
    <p>Average practice score: {average_practice_score:.1f}%</p>
    <h2>{labels['weak_topics']}</h2>
    <table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;'>Topic</th><th style='border:1px solid #ccc;padding:8px;'>Score</th></tr></thead><tbody>{weak_topic_rows if weak_topic_rows else "<tr><td colspan='2' style='border:1px solid #ccc;padding:8px;'>No topic data found.</td></tr>"}</tbody></table>
    <h2>{labels['students_at_risk']}</h2>
    <table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;'>Name</th><th style='border:1px solid #ccc;padding:8px;'>Score</th></tr></thead><tbody>{risk_student_rows if risk_student_rows else "<tr><td colspan='2' style='border:1px solid #ccc;padding:8px;'>No risk students found.</td></tr>"}</tbody></table>
    <h2>{labels['top_students']}</h2>
    <table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;'>Name</th><th style='border:1px solid #ccc;padding:8px;'>Score</th></tr></thead><tbody>{top_student_rows if top_student_rows else "<tr><td colspan='2' style='border:1px solid #ccc;padding:8px;'>No students found.</td></tr>"}</tbody></table>
    <h2>{labels['class_performance']}</h2>
    <table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;'>Class</th><th style='border:1px solid #ccc;padding:8px;'>Average score</th></tr></thead><tbody>{class_rows if class_rows else "<tr><td colspan='2' style='border:1px solid #ccc;padding:8px;'>No classes found.</td></tr>"}</tbody></table>
    <p><a href='/school-admin/teachers'>Manage Teachers</a></p>
    <p><a href='/school-admin/students'>Manage Students</a></p>
    <p><a href='/school-admin/chapter-summary'>Chapter Completion Summary</a></p></body></html>"""


@app.route("/school-admin/teachers", methods=["GET", "POST"])
def school_admin_teachers():
    auth_redirect = school_admin_session_required()
    if auth_redirect:
        return auth_redirect
    school_id = int(session["school_id"])
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not name or not email or not password:
            return "<h2>Missing required teacher fields</h2>", 400
        db.session.add(Teacher(name=name, email=email, password_hash=generate_password_hash(password), school_id=school_id))
        db.session.commit()
        return redirect("/school-admin/teachers")
    teachers = Teacher.query.filter_by(school_id=school_id).order_by(Teacher.id.desc()).all()
    rows = "".join([f"<tr><td>{t.id}</td><td>{escape(t.name)}</td><td>{escape(t.email)}</td></tr>" for t in teachers])
    return f"""<!doctype html><html><body><h1>Manage Teachers</h1>
    <form method='post'><label>Name: <input name='name' required></label><br><br>
    <label>Email: <input type='email' name='email' required></label><br><br>
    <label>Password: <input type='password' name='password' required></label><br><br>
    <button type='submit'>Add Teacher</button></form>
    <table border='1' cellpadding='6'><tr><th>ID</th><th>Name</th><th>Email</th></tr>{rows}</table>
    <p><a href='/school-admin/dashboard'>Back</a></p></body></html>"""


@app.route("/school-admin/students", methods=["GET", "POST"])
def school_admin_students():
    auth_redirect = school_admin_session_required()
    if auth_redirect:
        return auth_redirect
    school_id = int(session["school_id"])
    students = Student.query.filter_by(school_id=school_id).order_by(Student.id.desc()).all()
    teacher_ids = [teacher.id for teacher in Teacher.query.filter_by(school_id=school_id).all()]
    classes = Class.query.filter(Class.teacher_id.in_(teacher_ids)).order_by(Class.class_name.asc()).all() if teacher_ids else []
    if request.method == "POST":
        student_id = int(request.form.get("student_id") or 0)
        class_id = int(request.form.get("class_id") or 0)
        student = Student.query.filter_by(id=student_id, school_id=school_id).first()
        if not student:
            return "<h2>Student not found in your school</h2>", 404
        student.class_id = class_id
        db.session.commit()
        return redirect("/school-admin/students")
    class_options = "".join([f"<option value='{c.id}'>{escape(c.class_name)} ({escape(display_grade(c.grade))})</option>" for c in classes])
    rows = ""
    for student in students:
        latest = StudentResult.query.filter_by(student_id=student.id).order_by(StudentResult.created_at.desc(), StudentResult.id.desc()).first()
        performance = f"{latest.score:.1f}%" if latest else "N/A"
        rows += f"<tr><td>{student.id}</td><td>{escape(student.name)}</td><td>{student.class_id or '-'}</td><td>{performance}</td></tr>"
    return f"""<!doctype html><html><body><h1>Manage Students</h1>
    <form method='post'><label>Student:
    <select name='student_id'>{''.join([f"<option value='{s.id}'>{escape(s.name)}</option>" for s in students])}</select></label>
    <label>Class: <select name='class_id'>{class_options}</select></label>
    <button type='submit'>Assign to class</button></form>
    <table border='1' cellpadding='6'><tr><th>ID</th><th>Name</th><th>Class</th><th>Performance</th></tr>{rows}</table>
    <p><a href='/school-admin/dashboard'>Back</a></p></body></html>"""


def parse_question_form_data(has_uploaded_image: bool = False, existing_image_url: str = "") -> tuple[dict, str | None]:
    grade = (request.form.get("grade") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    topic = (request.form.get("topic") or "").strip()
    term_id_raw = (request.form.get("term_id") or "").strip()
    module_id_raw = (request.form.get("module_id") or "").strip()
    chapter_id_raw = (request.form.get("chapter_id") or "").strip()
    question_text_en = (request.form.get("question_text_en") or "").strip()
    question_text_si = (request.form.get("question_text_si") or "").strip()
    question_type = (request.form.get("question_type") or "mcq").strip().lower()
    if question_type not in {"mcq", "short_answer", "box_input", "matching_pairs", "tap_select_image", "drag_drop_group_container"}:
        return {}, "Question type must be MCQ, Short Answer, Box Input, Matching Pairs, or Tap Select Image."
    option_a = (request.form.get("option_a") or "").strip()
    option_b = (request.form.get("option_b") or "").strip()
    option_c = (request.form.get("option_c") or "").strip()
    option_d = (request.form.get("option_d") or "").strip()
    correct_option = (request.form.get("correct_option") or "").strip().upper()
    correct_answer_text = (request.form.get("correct_answer_text") or "").strip()
    box_template = request.form.get("box_template") or ""
    box_answers_raw = request.form.get("box_answers") or ""
    matching_left_en = request.form.get("matching_left_en") or ""
    matching_right_en = request.form.get("matching_right_en") or ""
    matching_answers_en_raw = request.form.get("matching_answers_en") or ""
    matching_left_si = request.form.get("matching_left_si") or ""
    matching_right_si = request.form.get("matching_right_si") or ""
    matching_answers_si_raw = request.form.get("matching_answers_si") or ""
    image_url = (request.form.get("image_url") or "").strip()
    tap_areas_json_raw = request.form.get("tap_areas_json") or ""
    correct_area_id = (request.form.get("correct_area_id") or "").strip()
    drag_items_json_raw = request.form.get("drag_items_json") or ""
    drag_groups_json_raw = request.form.get("drag_groups_json") or ""
    drag_container_image_url = (request.form.get("drag_container_image_url") or "").strip()
    difficulty_level_raw = (request.form.get("difficulty_level") or "1").strip()

    common_required_values = [
        grade,
        subject,
        term_id_raw,
        module_id_raw,
        chapter_id_raw,
        question_text_en,
        question_text_si,
        difficulty_level_raw,
    ]
    if any(value == "" for value in common_required_values):
        return {}, "Please complete grade, subject, term, module, chapter, question text, and difficulty."

    required_values: list[str] = []
    if question_type == "mcq":
        required_values.extend([option_a, option_b, option_c, option_d, correct_option])
    elif question_type == "short_answer":
        required_values.append(correct_answer_text)
    elif question_type == "box_input":
        required_values.extend([box_template.strip(), box_answers_raw.strip()])
    elif question_type == "matching_pairs":
        required_values.extend([matching_left_en.strip(), matching_right_en.strip(), matching_answers_en_raw.strip(), matching_left_si.strip(), matching_right_si.strip(), matching_answers_si_raw.strip()])
    elif question_type == "drag_drop_group_container":
        required_values.extend([drag_items_json_raw.strip(), drag_groups_json_raw.strip(), drag_container_image_url.strip()])
    if any(value == "" for value in required_values):
        return {}, "All fields are required."
    if question_type == "tap_select_image":
        effective_image_url = image_url or existing_image_url
        if not effective_image_url and not has_uploaded_image:
            return {}, "Please upload an image or enter image URL."

    grade = normalize_grade(grade)
    if not is_valid_grade(grade):
        return {}, "Grade must be one of: 1-10, OL, AL."

    if question_type == "mcq" and correct_option not in {"A", "B", "C", "D"}:
        return {}, "Correct answer must be one of A, B, C, or D."
    normalized_box_answers = None
    if question_type == "box_input":
        normalized_box_answers, box_err = parse_box_answers_json(box_answers_raw)
        if box_err:
            return {}, box_err
        for key in extract_box_keys(box_template):
            if key not in normalized_box_answers:
                return {}, f"Missing answer for {key}"

    normalized_tap_areas = None
    if question_type == "tap_select_image" and (tap_areas_json_raw.strip() or correct_area_id):
        normalized_tap_areas, tap_err = parse_tap_areas_json(tap_areas_json_raw)
        if tap_err:
            return {}, tap_err
        valid_ids = {item["id"] for item in normalized_tap_areas}
        if correct_area_id and correct_area_id not in valid_ids:
            return {}, "Correct Answer Area ID must exist in Selectable Areas JSON."

    normalized_matching_answers_en = None
    normalized_matching_answers_si = None
    normalized_drag_items = None
    if question_type == "matching_pairs":
        left_en = parse_matching_items(matching_left_en)
        right_en = parse_matching_items(matching_right_en)
        left_si = parse_matching_items(matching_left_si)
        right_si = parse_matching_items(matching_right_si)
        normalized_matching_answers_en, err_en = parse_matching_answers_json(matching_answers_en_raw)
        normalized_matching_answers_si, err_si = parse_matching_answers_json(matching_answers_si_raw)
        if err_en or err_si:
            return {}, err_en or err_si
        if not left_en or not right_en or not left_si or not right_si:
            return {}, "Matching pairs lists cannot be empty."
    if question_type == "drag_drop_group_container":
        normalized_drag_items, drag_err = parse_drag_items_json(drag_items_json_raw)
        if drag_err:
            return {}, drag_err
        try:
            groups = json.loads(drag_groups_json_raw or "[]")
        except json.JSONDecodeError:
            return {}, "Drag Groups JSON must be a valid JSON array."
        if not isinstance(groups, list) or not groups:
            return {}, "Drag Groups JSON must be a non-empty JSON array."

    try:
        difficulty_level = int(difficulty_level_raw)
    except ValueError:
        return {}, "Difficulty level must be a number between 1 and 5."
    if difficulty_level not in {1, 2, 3, 4, 5}:
        return {}, "Difficulty level must be between 1 and 5."

    term_id = int(term_id_raw) if term_id_raw.isdigit() else None
    module_id = int(module_id_raw) if module_id_raw.isdigit() else None
    chapter_id = int(chapter_id_raw) if chapter_id_raw.isdigit() else None
    chapter = SyllabusChapter.query.get(chapter_id) if chapter_id else None
    chapter_en = chapter.chapter_name_en if chapter else topic
    chapter_si = chapter.chapter_name_si if chapter else topic

    return {
        "grade": grade,
        "subject": subject,
        "topic": chapter_en or topic,
        "term_id": term_id,
        "module_id": module_id,
        "chapter_id": chapter_id,
        "chapter_en": chapter_en,
        "chapter_si": chapter_si,
        "question_text_en": question_text_en,
        "question_text_si": question_text_si,
        "option_a": option_a,
        "option_b": option_b,
        "option_c": option_c,
        "option_d": option_d,
        "correct_option": correct_option,
        "question_type": question_type,
        "correct_answer_text": correct_answer_text,
        "box_template": box_template,
        "box_answers": json.dumps(normalized_box_answers, ensure_ascii=False) if normalized_box_answers is not None else "",
        "matching_left_en": json.dumps(parse_matching_items(matching_left_en), ensure_ascii=False) if question_type == "matching_pairs" else "",
        "matching_right_en": json.dumps(parse_matching_items(matching_right_en), ensure_ascii=False) if question_type == "matching_pairs" else "",
        "matching_answers_en": json.dumps(normalized_matching_answers_en, ensure_ascii=False) if normalized_matching_answers_en is not None else "",
        "matching_left_si": json.dumps(parse_matching_items(matching_left_si), ensure_ascii=False) if question_type == "matching_pairs" else "",
        "matching_right_si": json.dumps(parse_matching_items(matching_right_si), ensure_ascii=False) if question_type == "matching_pairs" else "",
        "matching_answers_si": json.dumps(normalized_matching_answers_si, ensure_ascii=False) if normalized_matching_answers_si is not None else "",
        "tap_areas_json": json.dumps(normalized_tap_areas, ensure_ascii=False) if normalized_tap_areas is not None else "",
        "correct_area_id": correct_area_id,
        "drag_items_json": json.dumps(normalized_drag_items, ensure_ascii=False) if normalized_drag_items is not None else "",
        "drag_groups_json": drag_groups_json_raw,
        "drag_container_image_url": drag_container_image_url,
        "image_url": image_url,
        "difficulty_level": difficulty_level,
    }, None


def render_question_form(action: str, data: dict, page_title: str, submit_label: str, error: str = "") -> str:
    error_html = f"<p style='color:red;'>{escape(error)}</p>" if error else ""
    difficulty_level = str(data.get("difficulty_level", "1"))
    question_type = data.get("question_type", "mcq")
    mcq_hidden = "" if question_type == "mcq" else "display:none;"
    short_hidden = "" if question_type == "short_answer" else "display:none;"
    box_hidden = "" if question_type == "box_input" else "display:none;"
    matching_hidden = "" if question_type == "matching_pairs" else "display:none;"
    tap_select_hidden = "" if question_type == "tap_select_image" else "display:none;"
    drag_group_hidden = "" if question_type == "drag_drop_group_container" else "display:none;"
    grade = (data.get("grade") or "").strip()
    subject = (data.get("subject") or "").strip()
    selected_term_id = int(data.get("term_id") or 0)
    selected_module_id = int(data.get("module_id") or 0)
    selected_chapter_id = int(data.get("chapter_id") or 0)
    terms = _syllabus_terms_for_grade_subject(grade, subject)
    modules = SyllabusModule.query.filter_by(term_id=selected_term_id).order_by(SyllabusModule.module_order.asc()).all() if selected_term_id else []
    chapters = SyllabusChapter.query.filter_by(module_id=selected_module_id, is_active=True).order_by(SyllabusChapter.chapter_order.asc()).all() if selected_module_id else []
    term_options = "<option value=''>Select term</option>" + "".join([f"<option value='{t.id}' {'selected' if t.id==selected_term_id else ''}>T{t.term_number} - {escape(t.term_name_en)}</option>" for t in terms])
    module_options = "<option value=''>Select module</option>" + "".join([f"<option value='{m.id}' {'selected' if m.id==selected_module_id else ''}>{m.module_order} - {escape(m.module_name_en)}</option>" for m in modules])
    chapter_options = "<option value=''>Select chapter</option>" + "".join([f"<option value='{c.id}' {'selected' if c.id==selected_chapter_id else ''}>{c.chapter_order} - {escape(c.chapter_name_en)}</option>" for c in chapters])
    return f"""
    <!doctype html>
    <html lang="en">
      <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{page_title}</title></head>
      <body>
        <h1>{page_title}</h1>
        {error_html}
        <form method="post" action="{action}" enctype="multipart/form-data">
          <label>Grade: <select name="grade" required>{grade_options_html(data.get('grade', ''))}</select></label><br><br>
          <label>Subject: <select name="subject" required>{subject_options_html_by_id(grade, subject, active_only=False)}</select></label><br><br>
          <label>Term: <select name="term_id" data-selected="{selected_term_id or ''}">{term_options}</select></label><br><br>
          <label>Module: <select name="module_id" data-selected="{selected_module_id or ''}">{module_options}</select></label><br><br>
          <label>Chapter: <select name="chapter_id" data-selected="{selected_chapter_id or ''}">{chapter_options}</select></label><br><br>
          <p id="syllabus-debug-message" style="color:#b45309;"></p>
          <label>Topic (legacy fallback): <input type="text" name="topic" value="{escape(data.get('topic', ''))}"></label><br><br>
          <label>Question text EN:<br><textarea name="question_text_en" rows="4" cols="80" required>{escape(data.get('question_text_en', ''))}</textarea></label><br><br>
          <label>Question text SI:<br><textarea name="question_text_si" rows="4" cols="80" required>{escape(data.get('question_text_si', ''))}</textarea></label><br><br>
          <label>Image URL (optional): <input type="text" name="image_url" value="{escape(data.get('image_url', ''))}"></label><br><br>
          <label>Question Image (optional): <input type="file" name="question_image" accept=".png,.jpg,.jpeg,.webp"></label><br><br>
          {f"<p>Current image:<br><img src='{escape(data.get('image_url', ''))}' alt='Current question image' style='max-width:500px;height:auto;border:1px solid #ddd;'></p>" if data.get("image_url") else ""}
          <label>Question Type:
            <select name="question_type" id="question_type" onchange="toggleQuestionType()" required>
              <option value="mcq" {"selected" if question_type == "mcq" else ""}>MCQ</option>
              <option value="short_answer" {"selected" if question_type == "short_answer" else ""}>Short Answer</option>
              <option value="box_input" {"selected" if question_type == "box_input" else ""}>Box Input / Fill-in-the-Boxes</option>
              <option value="matching_pairs" {"selected" if question_type == "matching_pairs" else ""}>Matching Pairs / Join the Pairs</option>
              <option value="tap_select_image" {"selected" if question_type == "tap_select_image" else ""}>Tap / Color Correct Picture</option>
              <option value="drag_drop_group_container" {"selected" if question_type == "drag_drop_group_container" else ""}>Drag Drop Group Container</option>
            </select>
          </label><br><br>
          <div id="mcq_fields" style="{mcq_hidden}">
          <label>Option A: <input type="text" name="option_a" value="{escape(data.get('option_a', ''))}"></label><br><br>
          <label>Option B: <input type="text" name="option_b" value="{escape(data.get('option_b', ''))}"></label><br><br>
          <label>Option C: <input type="text" name="option_c" value="{escape(data.get('option_c', ''))}"></label><br><br>
          <label>Option D: <input type="text" name="option_d" value="{escape(data.get('option_d', ''))}"></label><br><br>
          <label>Correct Answer (A/B/C/D): <input type="text" name="correct_option" maxlength="1" value="{escape(data.get('correct_option', ''))}"></label><br><br>
          </div>
          <div id="short_answer_fields" style="{short_hidden}">
            <label>Correct Answer (text or number): <input type="text" name="correct_answer_text" value="{escape(data.get('correct_answer_text', ''))}"></label><br><br>
          </div>

          <div id="box_input_fields" style="{box_hidden}">
            <p>Use placeholders like [box1], [box2], [box3] where students should enter answers.</p>
            <p>You can freely arrange numbers, operators, and box positions.</p>
            <label>Box Template:<br><textarea name="box_template" rows="6" cols="80">{escape(data.get('box_template', ''))}</textarea></label><br><br>
            <label>Correct Box Answers (JSON):<br><textarea name="box_answers" rows="6" cols="80">{escape(data.get('box_answers', ''))}</textarea></label><br><br>
            <pre id='box_preview' style='font-family:monospace;white-space:pre;line-height:1.25;background:#f8fafc;padding:8px;border:1px solid #ddd;'></pre>
          </div>
          <div id="matching_pairs_fields" style="{matching_hidden}">
            <h3>ENGLISH</h3><p>Enter one item per line.</p>
            <label>Left Items EN:<br><textarea name='matching_left_en' rows='5' cols='80'>{escape(data.get('matching_left_en',''))}</textarea></label><br><br>
            <label>Right Items EN:<br><textarea name='matching_right_en' rows='5' cols='80'>{escape(data.get('matching_right_en',''))}</textarea></label><br><br>
            <label>Correct Matches JSON EN:<br><textarea name='matching_answers_en' rows='6' cols='80'>{escape(data.get('matching_answers_en',''))}</textarea></label><br><br>
            <h3>SINHALA</h3><p>Enter one item per line.</p>
            <label>Left Items SI:<br><textarea name='matching_left_si' rows='5' cols='80'>{escape(data.get('matching_left_si',''))}</textarea></label><br><br>
            <label>Right Items SI:<br><textarea name='matching_right_si' rows='5' cols='80'>{escape(data.get('matching_right_si',''))}</textarea></label><br><br>
            <label>Correct Matches JSON SI:<br><textarea name='matching_answers_si' rows='6' cols='80'>{escape(data.get('matching_answers_si',''))}</textarea></label><br><br>
            <div id='matching_preview'></div>
          </div>
          <div id="tap_select_fields" style="{tap_select_hidden}">
            <p>Tap Area Editor (values are percentages on a 0-100 scale).</p>
            <div id="tap_editor_canvas" style="position:relative;display:inline-block;max-width:520px;width:100%;border:1px solid #ddd;">
              <img id="tap_editor_image" src="{escape(data.get('image_url', ''))}" alt="Tap area editor image" style="width:100%;height:auto;display:block;">
              <svg id="tap_editor_overlay" viewBox="0 0 100 100" preserveAspectRatio="none" style="position:absolute;inset:0;width:100%;height:100%;"></svg>
            </div>
            <p id="tap_editor_help">Click and drag on image to create area rectangles.</p>
            <ul id="tap_area_list"></ul>
            <label>Selectable Areas JSON:<br><textarea name="tap_areas_json" id="tap_areas_json" rows="6" cols="80">{escape(data.get('tap_areas_json',''))}</textarea></label><br><br>
            <label>Correct Area ID:
              <select name="correct_area_id" id="correct_area_id">
                <option value="">Select correct area</option>
              </select>
            </label><br><br>
          </div>
          <div id="drag_group_fields" style="{drag_group_hidden}">
            <label>Container image URL: <input type="text" name="drag_container_image_url" value="{escape(data.get('drag_container_image_url', ''))}"></label><br><br>
            <label>Drag Items JSON:<br><textarea name="drag_items_json" rows="8" cols="80">{escape(data.get('drag_items_json', ''))}</textarea></label><br><br>
            <label>Drag Groups JSON:<br><textarea name="drag_groups_json" rows="3" cols="80">{escape(data.get('drag_groups_json', ''))}</textarea></label><br><br>
          </div>
          <label>Difficulty Level:
            <select name="difficulty_level" required>
              <option value="1" {"selected" if difficulty_level == "1" else ""}>1 Easy</option>
              <option value="2" {"selected" if difficulty_level == "2" else ""}>2 Easy</option>
              <option value="3" {"selected" if difficulty_level == "3" else ""}>3 Medium</option>
              <option value="4" {"selected" if difficulty_level == "4" else ""}>4 Hard</option>
              <option value="5" {"selected" if difficulty_level == "5" else ""}>5 Hard</option>
            </select>
          </label><br><br>
          <button type="submit">{submit_label}</button>
        </form>
        <p><a href="/admin/questions">Back to Questions</a></p>
        <script>
          function toggleQuestionType() {{
            const selectedType = document.getElementById("question_type").value;
            document.getElementById("mcq_fields").style.display = selectedType === "mcq" ? "block" : "none";
            document.getElementById("short_answer_fields").style.display = selectedType === "short_answer" ? "block" : "none";
            document.getElementById("box_input_fields").style.display = selectedType === "box_input" ? "block" : "none";
            document.getElementById("matching_pairs_fields").style.display = selectedType === "matching_pairs" ? "block" : "none";
            document.getElementById("tap_select_fields").style.display = selectedType === "tap_select_image" ? "block" : "none";
            document.getElementById("drag_group_fields").style.display = selectedType === "drag_drop_group_container" ? "block" : "none";
          }}
        document.addEventListener("DOMContentLoaded", () => {{ const t=document.querySelector("textarea[name=box_template]"); const p=document.getElementById("box_preview"); const r=(raw) => (raw||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); const u=() => {{ if (p && t) {{ const v=t.value||""; p.innerHTML=v? r(v).replace(/\\[box(\\d+)\\]/gi, () => "<input type='text' class='box-input' disabled>") : "Live preview..."; }} }}; if (t) t.addEventListener("input",u); u();
          const imageInput=document.querySelector("input[name='image_url']");
          const imageFileInput=document.querySelector("input[name='question_image']");
          const imageEl=document.getElementById("tap_editor_image");
          const overlay=document.getElementById("tap_editor_overlay");
          const listEl=document.getElementById("tap_area_list");
          const jsonEl=document.getElementById("tap_areas_json");
          const correctEl=document.getElementById("correct_area_id");
          let previewObjectUrl=null;
          let areas=[]; let drawing=false; let start=null;
          const norm=(v)=>Math.max(0,Math.min(100,v));
          const parseAreas=()=>{{ try{{ const p=JSON.parse(jsonEl.value||"[]"); return Array.isArray(p)?p:[]; }}catch(e){{ return []; }} }};
          const render=()=>{{ if(!overlay) return; overlay.innerHTML=""; if(listEl) listEl.innerHTML=""; if(correctEl){{ const current=correctEl.dataset.current||correctEl.value||""; correctEl.innerHTML="<option value=''>Select correct area</option>"; }}
            areas.forEach((a,idx)=>{{ const rect=document.createElementNS("http://www.w3.org/2000/svg","rect"); rect.setAttribute("x",a.x);rect.setAttribute("y",a.y);rect.setAttribute("width",a.width);rect.setAttribute("height",a.height); rect.setAttribute("fill","rgba(59,130,246,0.2)"); rect.setAttribute("stroke","rgba(30,64,175,0.9)"); rect.setAttribute("stroke-width","0.6"); overlay.appendChild(rect);
              if(listEl){{ const li=document.createElement("li"); li.textContent=`${{a.id}} (x:${{a.x.toFixed(2)}}, y:${{a.y.toFixed(2)}}, w:${{a.width.toFixed(2)}}, h:${{a.height.toFixed(2)}})`; listEl.appendChild(li); }}
              if(correctEl){{ const o=document.createElement("option"); o.value=a.id; o.textContent=a.id; if(o.value===(correctEl.dataset.current||"")) o.selected=true; correctEl.appendChild(o); }}
            }});
            jsonEl.value=JSON.stringify(areas);
          }};
          if (correctEl) correctEl.dataset.current="{escape(data.get('correct_area_id',''))}";
          areas=parseAreas(); render();
          const setEditorImage=(src)=>{{
            if(!imageEl) return;
            imageEl.src=src||"";
          }};
          if (imageEl) imageEl.addEventListener("load",()=>{{ render(); }});
          if(imageInput&&imageEl) imageInput.addEventListener("input",()=>{{
            if (previewObjectUrl) {{
              URL.revokeObjectURL(previewObjectUrl);
              previewObjectUrl=null;
            }}
            setEditorImage(imageInput.value||"");
          }});
          if(imageFileInput&&imageEl) imageFileInput.addEventListener("change",()=>{{
            const file=imageFileInput.files && imageFileInput.files[0];
            if (!file) return;
            if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
            previewObjectUrl=URL.createObjectURL(file);
            setEditorImage(previewObjectUrl);
          }});
          const startDraw=(ev)=>{{ if(!overlay) return; drawing=true; const p=overlay.createSVGPoint(); p.x=(ev.touches?ev.touches[0].clientX:ev.clientX); p.y=(ev.touches?ev.touches[0].clientY:ev.clientY); const c=p.matrixTransform(overlay.getScreenCTM().inverse()); start={{x:norm(c.x),y:norm(c.y)}}; }};
          const endDraw=(ev)=>{{ if(!drawing||!start) return; drawing=false; const p=overlay.createSVGPoint(); p.x=(ev.changedTouches?ev.changedTouches[0].clientX:ev.clientX); p.y=(ev.changedTouches?ev.changedTouches[0].clientY:ev.clientY); const c=p.matrixTransform(overlay.getScreenCTM().inverse()); const end={{x:norm(c.x),y:norm(c.y)}}; const x=Math.min(start.x,end.x), y=Math.min(start.y,end.y), w=Math.abs(end.x-start.x), h=Math.abs(end.y-start.y); if(w<1||h<1) return; const id=`area${{areas.length+1}}`; areas.push({{id,x,y,width:w,height:h}}); render(); }};
          if(overlay){{ overlay.addEventListener("pointerdown",startDraw); overlay.addEventListener("pointerup",endDraw); overlay.addEventListener("touchstart",startDraw,{{passive:true}}); overlay.addEventListener("touchend",endDraw); }}
        }});</script>{dependent_dropdown_script()}
      </body>
    </html>
    """


@app.route("/school-admin/chapter-summary", methods=["GET"])
def school_admin_chapter_summary():
    if session.get("school_admin_logged_in") is not True:
        return redirect(url_for("login"))
    school_id = session.get("school_id")
    classes = Class.query.filter_by(school_id=school_id).order_by(Class.grade.asc(), Class.name.asc()).all()
    rows = []
    for c in classes:
        students = Student.query.filter_by(class_id=c.id).all()
        student_ids = [s.id for s in students]
        total = len(student_ids)
        completed = StudentChapterProgress.query.filter(StudentChapterProgress.student_id.in_(student_ids), StudentChapterProgress.status == "completed").count() if student_ids else 0
        rows.append(f"<tr><td>{escape(c.name)}</td><td>{display_grade(c.grade)}</td><td>{total}</td><td>{completed}</td></tr>")
    return f"<h1>Chapter Completion Summary by Class</h1><table border='1'><tr><th>Class</th><th>Grade</th><th>Students</th><th>Total Completed Chapter Records</th></tr>{''.join(rows)}</table><p><a href='/school-admin/dashboard'>Back to Dashboard</a></p>"


@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return """
        <!doctype html>
        <html lang="en">
          <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Admin Login</title></head>
          <body>
            <h1>Admin Login</h1>
            <form method="post" action="/admin-login">
              <label>Email: <input type="email" name="email" required></label><br><br>
              <label>Password: <input type="password" name="password" required></label><br><br>
              <button type="submit">Login</button>
            </form>
      </body>
        </html>
        """

    admin_email, admin_password = get_admin_credentials()
    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""

    if email != admin_email or password != admin_password:
        return "<h2>Invalid admin credentials</h2><p><a href='/admin-login'>Try again</a></p>", 401

    session["admin_logged_in"] = True
    return redirect(url_for("admin_dashboard"))


@app.route("/admin-dashboard", methods=["GET"])
def admin_dashboard():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    total_students = Student.query.count()
    total_skillscan_attempts = StudentResult.query.count()
    total_practice_attempts = PracticeAttempt.query.count()
    average_skillscan_score = db.session.query(db.func.avg(StudentResult.score)).scalar()
    average_practice_score = db.session.query(db.func.avg(PracticeAttempt.score)).scalar()
    average_skillscan_score = round(float(average_skillscan_score or 0), 2)
    average_practice_score = round(float(average_practice_score or 0), 2)

    latest_skillscan_results = (
        StudentResult.query.order_by(StudentResult.created_at.desc(), StudentResult.id.desc()).limit(10).all()
    )
    latest_practice_attempts = (
        PracticeAttempt.query.order_by(PracticeAttempt.created_at.desc(), PracticeAttempt.id.desc()).limit(10).all()
    )

    skillscan_rows = "".join(
        f"""
        <tr>
          <td style='border:1px solid #ccc;padding:8px;'>{result.created_at.strftime('%Y-%m-%d %H:%M:%S')}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{result.student_id or '-'}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{result.grade}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{result.medium}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{result.score}%</td>
          <td style='border:1px solid #ccc;padding:8px;'>{result.correct_answers}/{result.total_questions}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{result.level}</td>
        </tr>
        """
        for result in latest_skillscan_results
    )

    practice_rows = "".join(
        f"""
        <tr>
          <td style='border:1px solid #ccc;padding:8px;'>{attempt.created_at.strftime('%Y-%m-%d %H:%M:%S')}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{attempt.student_id or '-'}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{attempt.grade}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{attempt.topic_en}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{attempt.medium}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{attempt.score}%</td>
          <td style='border:1px solid #ccc;padding:8px;'>{attempt.correct_answers}/{attempt.total_questions}</td>
        </tr>
        """
        for attempt in latest_practice_attempts
    )

    return f"""
    <!doctype html>
    <html lang='en'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>Admin Dashboard</title>
      </head>
      <body>
        <h1>Admin Dashboard</h1>
        <h2>System Overview</h2>
        <p><strong>Total students:</strong> {total_students}</p>
        <p><strong>Total SkillScan attempts:</strong> {total_skillscan_attempts}</p>
        <p><strong>Total practice attempts:</strong> {total_practice_attempts}</p>
        <p><strong>Average SkillScan score:</strong> {average_skillscan_score}%</p>
        <p><strong>Average practice score:</strong> {average_practice_score}%</p>
        <h2>Quick Links</h2>
        <p><a href='/admin/students'>Manage Students</a></p>
        <p><a href='/register-form'>Register Student</a></p>
        <p><a href='/admin/questions'>Manage Questions</a></p>
        <p><a href='/admin/syllabus'>Syllabus Management</a></p><p><a href='/admin/subjects'>Subject Management</a></p>
        <p><a href='/admin/syllabus'>Chapter Content Management</a></p>
        <p><a href='/admin/lesson-builder'>Lesson Builder</a></p>
        <p><a href='/admin/classes'>Manage Classes</a></p>
        <p><a href='/admin/premium'>Premium Management</a></p>
        <p><a href='/admin/create-school-admin'>Create School Admin</a></p>
        <p><a href='/admin/schools'>Manage Schools</a></p>
        <p><a href='/admin-logout'>Logout</a></p>

        <h2>Latest Activity</h2>
        <h3>Latest 10 SkillScan Results</h3>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Date</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Student ID</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Grade</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Medium</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Score</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Correct</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Level</th>
            </tr>
          </thead>
          <tbody>
            {skillscan_rows if skillscan_rows else "<tr><td colspan='7' style='border:1px solid #ccc;padding:8px;'>No SkillScan results found.</td></tr>"}
          </tbody>
        </table>

        <h3>Latest 10 Practice Attempts</h3>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Date</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Student ID</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Grade</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Topic</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Medium</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Score</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Correct</th>
            </tr>
          </thead>
          <tbody>
            {practice_rows if practice_rows else "<tr><td colspan='7' style='border:1px solid #ccc;padding:8px;'>No practice attempts found.</td></tr>"}
          </tbody>
        </table>
      </body>
    </html>
    """


@app.route("/admin/classes", methods=["GET"])
def admin_classes():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    classes = Class.query.order_by(Class.created_at.desc(), Class.id.desc()).all()
    rows = []
    for classroom in classes:
        rows.append(
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'>{classroom.id}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{escape(classroom.class_name)}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{display_grade(classroom.grade)}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{classroom.teacher_id}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{Student.query.filter_by(class_id=classroom.id).count()}</td>
              <td style='border:1px solid #ccc;padding:8px;'><a href='/admin/class/{classroom.id}'>View / Assign</a></td>
            </tr>
            """
        )

    class_rows = "".join(rows)
    return f"""
    <h1>Manage Classes</h1>
    <p><a href='/admin-dashboard'>Back to Admin Dashboard</a></p>
    <table style='border-collapse:collapse;width:100%;'>
      <thead>
        <tr>
          <th style='border:1px solid #ccc;padding:8px;'>Class ID</th>
          <th style='border:1px solid #ccc;padding:8px;'>Class Name</th>
          <th style='border:1px solid #ccc;padding:8px;'>Grade</th>
          <th style='border:1px solid #ccc;padding:8px;'>Teacher ID</th>
          <th style='border:1px solid #ccc;padding:8px;'>Student Count</th>
          <th style='border:1px solid #ccc;padding:8px;'>Action</th>
        </tr>
      </thead>
      <tbody>{class_rows if class_rows else "<tr><td colspan='6' style='border:1px solid #ccc;padding:8px;'>No classes found.</td></tr>"}</tbody>
    </table>
    """


@app.route("/admin/class/<int:class_id>", methods=["GET"])
def admin_class_details(class_id: int):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    classroom = db.session.get(Class, class_id)
    if not classroom:
        return "<h2>Class not found</h2>", 404

    students = (
        Student.query.filter_by(class_id=classroom.id)
        .order_by(Student.name.asc(), Student.id.asc())
        .all()
    )

    student_rows = "".join(
        [
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'>{student.id}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{escape(student.name)}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{display_grade(student.grade)}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{escape(student.medium)}</td>
            </tr>
            """
            for student in students
        ]
    )

    return f"""
    <h1>Class: {escape(classroom.class_name)}</h1>
    <p><strong>Class ID:</strong> {classroom.id}</p>
    <p><strong>Grade:</strong> {display_grade(classroom.grade)}</p>
    <p><strong>Teacher ID:</strong> {classroom.teacher_id}</p>
    <p><a href='/admin/assign-students/{classroom.id}'>Assign Students</a></p>
    <table style='border-collapse:collapse;width:100%;'>
      <thead>
        <tr>
          <th style='border:1px solid #ccc;padding:8px;'>Student ID</th>
          <th style='border:1px solid #ccc;padding:8px;'>Name</th>
          <th style='border:1px solid #ccc;padding:8px;'>Grade</th>
          <th style='border:1px solid #ccc;padding:8px;'>Medium</th>
        </tr>
      </thead>
      <tbody>{student_rows if student_rows else "<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>No students assigned.</td></tr>"}</tbody>
    </table>
    <p><a href='/admin/classes'>Back to Manage Classes</a></p>
    """


@app.route("/admin/assign-students/<int:class_id>", methods=["GET", "POST"])
def admin_assign_students(class_id: int):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    classroom = db.session.get(Class, class_id)
    if not classroom:
        return "<h2>Class not found</h2>", 404

    if request.method == "POST":
        selected_student_ids = {
            int(student_id)
            for student_id in request.form.getlist("student_ids")
            if student_id.isdigit()
        }
        grade_students = Student.query.filter_by(grade=classroom.grade).all()
        for student in grade_students:
            if student.id in selected_student_ids:
                student.class_id = classroom.id
            elif student.class_id == classroom.id:
                student.class_id = None
        db.session.commit()
        return redirect(f"/admin/class/{classroom.id}")

    grade_students = (
        Student.query.filter_by(grade=classroom.grade)
        .order_by(Student.name.asc(), Student.id.asc())
        .all()
    )
    student_rows = "".join(
        [
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'><input type='checkbox' name='student_ids' value='{student.id}' {'checked' if student.class_id == classroom.id else ''}></td>
              <td style='border:1px solid #ccc;padding:8px;'>{student.id}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{escape(student.name)}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{display_grade(student.grade)}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{escape(student.medium)}</td>
            </tr>
            """
            for student in grade_students
        ]
    )

    return f"""
    <h1>Assign Students to {escape(classroom.class_name)}</h1>
    <p>Grade: {display_grade(classroom.grade)}</p>
    <form method='post' action='/admin/assign-students/{classroom.id}'>
      <table style='border-collapse:collapse;width:100%;'>
        <thead>
          <tr>
            <th style='border:1px solid #ccc;padding:8px;'>Select</th>
            <th style='border:1px solid #ccc;padding:8px;'>Student ID</th>
            <th style='border:1px solid #ccc;padding:8px;'>Name</th>
            <th style='border:1px solid #ccc;padding:8px;'>Grade</th>
            <th style='border:1px solid #ccc;padding:8px;'>Medium</th>
          </tr>
        </thead>
        <tbody>{student_rows if student_rows else "<tr><td colspan='5' style='border:1px solid #ccc;padding:8px;'>No students found for this grade.</td></tr>"}</tbody>
      </table>
      <br>
      <button type='submit'>Save Assignments</button>
    </form>
    <p><a href='/admin/class/{classroom.id}'>Back to Class Details</a></p>
    """


@app.route("/admin/premium", methods=["GET"])
def admin_premium():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    students = Student.query.order_by(Student.created_at.desc(), Student.id.desc()).all()
    student_rows = "".join(
        f"""
        <tr>
          <td style='border:1px solid #ccc;padding:8px;'>{student.id}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.name}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.grade}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.medium}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.email}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.parent_email or '-'}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.mobile}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{'Yes' if student.is_premium else 'No'}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.subscription_end_date.strftime('%Y-%m-%d') if student.subscription_end_date else '-'}</td>
          <td style='border:1px solid #ccc;padding:8px;'><a href='/admin/activate-premium/{student.id}'>Activate 30 Days</a> | <a href='/admin/deactivate-premium/{student.id}' onclick="return confirm('Deactivate premium access for this student?');">Deactivate</a></td>
        </tr>
        """
        for student in students
    )

    return f"""
    <h1>Premium Management</h1>
    <p><a href='/admin-dashboard'>Back to Admin Dashboard</a></p>
    <table style='border-collapse:collapse;width:100%;'>
      <thead>
        <tr>
          <th style='border:1px solid #ccc;padding:8px;'>ID</th>
          <th style='border:1px solid #ccc;padding:8px;'>Name</th>
          <th style='border:1px solid #ccc;padding:8px;'>Grade</th>
          <th style='border:1px solid #ccc;padding:8px;'>Medium</th>
          <th style='border:1px solid #ccc;padding:8px;'>Email</th>
          <th style='border:1px solid #ccc;padding:8px;'>Parent Email</th>
          <th style='border:1px solid #ccc;padding:8px;'>Mobile</th>
          <th style='border:1px solid #ccc;padding:8px;'>is_premium</th>
          <th style='border:1px solid #ccc;padding:8px;'>subscription_end_date</th>
          <th style='border:1px solid #ccc;padding:8px;'>Action</th>
        </tr>
      </thead>
      <tbody>{student_rows if student_rows else "<tr><td colspan='10' style='border:1px solid #ccc;padding:8px;'>No students found.</td></tr>"}</tbody>
    </table>
    """


@app.route("/admin/activate-premium/<int:student_id>", methods=["GET"])
def admin_activate_premium(student_id: int):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    student = Student.query.get_or_404(student_id)
    student.is_premium = True
    student.subscription_end_date = date.today() + timedelta(days=30)
    db.session.commit()
    return redirect("/admin/premium")




@app.route("/admin/schools", methods=["GET"])
def admin_schools():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    schools = School.query.order_by(School.created_at.desc(), School.id.desc()).all()
    rows = []
    for school in schools:
        teacher_count = Teacher.query.filter_by(school_id=school.id).count()
        student_count = Student.query.filter_by(school_id=school.id).count()
        rows.append(
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'>{escape(school.school_name)}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{school.created_at.strftime('%Y-%m-%d %H:%M:%S')}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{teacher_count}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{student_count}</td>
              <td style='border:1px solid #ccc;padding:8px;'><a href='/admin/edit-school/{school.id}'>Edit</a></td>
            </tr>
            """
        )

    school_rows = "".join(rows)
    return f"""
    <h1>Manage Schools</h1>
    <p><a href='/admin/create-school'>Create New School</a></p>
    <p><a href='/admin-dashboard'>Back to Admin Dashboard</a></p>
    <table style='border-collapse:collapse;width:100%;'>
      <thead>
        <tr>
          <th style='border:1px solid #ccc;padding:8px;'>School Name</th>
          <th style='border:1px solid #ccc;padding:8px;'>Created Date</th>
          <th style='border:1px solid #ccc;padding:8px;'>Total Teachers</th>
          <th style='border:1px solid #ccc;padding:8px;'>Total Students</th>
          <th style='border:1px solid #ccc;padding:8px;'>Action</th>
        </tr>
      </thead>
      <tbody>{school_rows if school_rows else "<tr><td colspan='5' style='border:1px solid #ccc;padding:8px;'>No schools found.</td></tr>"}</tbody>
    </table>
    """


@app.route("/admin/create-school", methods=["GET", "POST"])
def admin_create_school():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    if request.method == "POST":
        school_name = (request.form.get("school_name") or "").strip()
        if not school_name:
            return "<h2>School Name is required.</h2><p><a href='/admin/create-school'>Try again</a></p>", 400

        existing_school = School.query.filter(db.func.lower(School.school_name) == school_name.lower()).first()
        if existing_school:
            return "<h2>School name already exists.</h2><p><a href='/admin/create-school'>Try again</a></p>", 400

        db.session.add(School(school_name=school_name, created_at=datetime.utcnow()))
        db.session.commit()
        return redirect("/admin/schools")

    return """
    <h1>Create New School</h1>
    <form method='post' action='/admin/create-school'>
      <label>School Name: <input type='text' name='school_name' required></label><br><br>
      <button type='submit'>Create School</button>
    </form>
    <p><a href='/admin/schools'>Back to Manage Schools</a></p>
    """




def render_student_dashboard_shell(inner_html, active_nav="dashboard"):
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))
    student = db.session.get(Student, student_id)
    if not student:
        session.pop("student_id", None)
        return redirect(url_for("login"))
    language = "si" if student.medium == "Sinhala" else "en"
    profile_image_url = (
        getattr(student, "profile_image_url", None)
        or getattr(student, "photo_url", None)
        or getattr(student, "avatar_url", None)
        or ""
    )
    avatar_initials = "S"
    if getattr(student, "name", None):
        avatar_initials = "".join([part[0].upper() for part in student.name.split()[:2]])

    dashboard_shell_start = f"""
    <!doctype html><html lang='{'si' if language == 'si' else 'en'}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{'ශිෂ්‍ය ඩෑෂ්බෝඩ්' if language == 'si' else 'Student Dashboard'}</title><script src='https://cdn.jsdelivr.net/npm/chart.js'></script>
    <style>body{{margin:0;font-family:Inter,Arial,sans-serif;background:#edf2fa;color:#0f172a}}.app{{display:grid;grid-template-columns:252px 1fr;min-height:100vh}}.side{{background:linear-gradient(180deg,#061a4f 0%,#0f347a 55%,#123f91 100%);color:#dbeafe;padding:8px 14px 18px}}.sidebar-brand{{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:8px 10px 14px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.10);margin:0 0 12px}}.sidebar-brand img{{width:82px;height:82px;object-fit:contain;display:block;margin:0 auto 6px}}.sidebar-brand-title{{color:#ffffff;font-size:12px;font-weight:700;line-height:1.2;white-space:nowrap;text-align:center}}.sidebar-nav{{display:flex;flex-direction:column;gap:0}}.nav-link{{display:flex;align-items:center;gap:10px;min-height:32px;padding:6px 10px;margin:2px 0;border-radius:10px;color:#eaf2ff;text-decoration:none;font-size:14px;font-weight:650;background:transparent;transition:160ms ease}}.nav-link:hover,.nav-link.active{{background:rgba(59,130,246,.38);box-shadow:inset 0 0 0 1px rgba(255,255,255,.08)}}.nav-icon{{width:18px;height:18px;flex:0 0 18px;opacity:.95;color:#dbeafe}}.nav-icon svg{{width:18px;height:18px;display:block;stroke:currentColor;stroke-width:1.9;fill:none;stroke-linecap:round;stroke-linejoin:round}}.main{{padding:0 16px 8px;background:#edf2fa}}.top{{background:rgba(255,255,255,0.12);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.08);border-radius:18px;box-shadow:0 4px 14px rgba(15,23,42,.025),inset 0 1px 0 rgba(255,255,255,.06)}}.dashboard-topbar{{display:flex;align-items:center;justify-content:space-between;margin-bottom:0px;padding:0;margin-top:0px}}.dashboard-topbar-spacer{{flex:1}}.top{{width:calc(100% - 330px);max-width:600px;min-height:64px;padding:6px 18px;margin-top:-20px;display:flex;align-items:center;gap:18px}}.greeting-left{{display:flex;align-items:center;gap:14px;min-width:0}}.student-avatar{{width:58px;height:58px;border-radius:50%;object-fit:cover;border:3px solid rgba(255,255,255,0.9);box-shadow:0 8px 22px rgba(15,23,42,0.16);background:linear-gradient(135deg,#dbeafe,#eff6ff);display:flex;align-items:center;justify-content:center;color:#1e3a8a;font-weight:800;font-size:20px;overflow:hidden}}.greeting-copy h2{{margin:0;font-size:22px;line-height:1.15}}.greeting-copy small{{display:block;margin-top:3px;color:#64748b}}.change-photo-link{{display:inline-block;margin-top:3px;font-size:12px;font-weight:700;color:#2563eb;text-decoration:none;border:0;background:transparent;cursor:pointer;padding:0;width:auto;min-height:auto}}.header-actions{{display:flex;align-items:center;gap:4px;margin-left:auto;background:transparent}}.header-icon-btn,.header-action-btn{{width:36px;height:36px;border:0;border-radius:12px;background:rgba(255,255,255,0.18);color:#0f172a;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;position:relative;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,0.15);box-shadow:none}}.header-icon-btn svg,.header-action-btn svg,.student-menu-btn .menu-caret,.student-mini-profile .menu-caret{{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}}.notification-badge{{position:absolute;top:-3px;right:-3px;min-width:14px;height:14px;border-radius:999px;background:#ef4444;color:#fff;font-size:9px;font-weight:800;display:flex;align-items:center;justify-content:center;border:2px solid #fff}}.country-flag-wrap{{width:36px;height:36px;border-radius:12px;background:rgba(255,255,255,0.18);display:flex;align-items:center;justify-content:center;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,0.15);box-shadow:none}}.country-flag-img{{width:20px;height:14px;object-fit:cover;border-radius:2px;display:block}}.student-menu{{position:relative}}.student-menu-btn,.student-mini-profile{{border:0;background:rgba(255,255,255,0.18);border-radius:16px;padding:6px 10px;display:flex;align-items:center;gap:6px;cursor:pointer;color:#0f172a;min-height:34px;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,0.15);box-shadow:none}}.header-avatar{{width:24px;height:24px;border-radius:50%;object-fit:cover;background:#dbeafe;color:#1e3a8a;font-size:10px;font-weight:800;display:flex;align-items:center;justify-content:center;overflow:hidden;flex:0 0 auto}}.student-menu-copy{{text-align:left;line-height:1.15}}.student-menu-copy strong{{display:block;font-size:11px;line-height:1.1;font-weight:800;max-width:140px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.student-menu-copy small{{display:block;font-size:9px;line-height:1;color:#64748b}}.student-dropdown,.notification-dropdown{{display:none;position:absolute;right:0;top:calc(100% + 8px);width:210px;background:rgba(255,255,255,.82);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border:1px solid rgba(255,255,255,.55);border-radius:16px;box-shadow:0 14px 34px rgba(15,23,42,0.12);padding:8px;z-index:9999}}.notification-dropdown{{width:230px;right:46px}}.student-dropdown.open,.notification-dropdown.open{{display:block}}</style></head>
    <body><div class='app'><aside class='side'><div class='sidebar-brand'><img src='/static/images/SLIS LOGO.png' alt='SLIS logo'><div class='sidebar-brand-title'>Spiral Learning Intelligence System</div></div>
    <nav class='sidebar-nav'>
    <div class='nav-section-title'>{'ඉගෙනුම' if language=='si' else 'LEARN'}</div>
    <a class='nav-link{' active' if active_nav == 'dashboard' else ''}' href='/student-dashboard'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='M3 11.5 12 4l9 7.5'></path><path d='M5 10.5V20h14v-9.5'></path></svg></span><span>{'ඩෑෂ්බෝඩ්' if language=='si' else 'Dashboard'}</span></a>
    <a class='nav-link{' active' if active_nav == 'my_subjects' else ''}' href='/student/learning-path'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='M4.5 6.5h6.8v12.5H4.5z'></path><path d='M12.7 6.5H19.5V19H12.7z'></path><path d='M8 9.5h.01'></path><path d='M16 9.5h.01'></path></svg></span><span>{'මගේ විෂයයන්' if language=='si' else 'My Subjects'}</span></a>
    <a class='nav-link{' active' if active_nav == 'live_classes' else ''}' href='/student/live-classes'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='M6 5h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-5l-3.5 3v-3H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z'></path></svg></span><span>{'සජීවී පන්ති' if language=='si' else 'Live Classes'}</span></a>
    <a class='nav-link{' active' if active_nav == 'assignments' else ''}' href='/student/assignments'><span class='nav-icon'><svg viewBox='0 0 24 24'><rect x='4' y='4' width='16' height='16' rx='2'></rect><path d='M8 9h8M8 13h8M8 17h5'></path></svg></span><span>{'පැවරුම්' if language=='si' else 'Assignments'}</span></a>
    <a class='nav-link{' active' if active_nav == 'quizzes' else ''}' href='/student/quizzes'><span class='nav-icon'><svg viewBox='0 0 24 24'><circle cx='12' cy='12' r='9'></circle><path d='M9.5 9a2.5 2.5 0 1 1 4.1 2c-.7.6-1.1 1.1-1.1 2'></path><path d='M12 17h.01'></path></svg></span><span>{'ප්‍රශ්නාවලි' if language=='si' else 'Quizzes'}</span></a>
    <div class='nav-section-title'>{'සොයා බලන්න' if language=='si' else 'EXPLORE'}</div>
    <a class='nav-link{' active' if active_nav == 'study_materials' else ''}' href='/student/study-materials'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='M4 19.5c0-1.4 1.1-2.5 2.5-2.5H20'></path><path d='M6.5 17c-1.4 0-2.5-1.1-2.5-2.5V5.5C4 4.1 5.1 3 6.5 3H20v14'></path></svg></span><span>{'අධ්‍යයන ද්‍රව්‍ය' if language=='si' else 'Study Materials'}</span></a>
    <a class='nav-link{' active' if active_nav == 'ai_tutor' else ''}' href='/student/ai-tutor'><span class='nav-icon'><svg viewBox='0 0 24 24'><rect x='7' y='8' width='10' height='8' rx='2'></rect><path d='M12 4v2M8.5 13h.01M15.5 13h.01M5 10H3M21 10h-2M7 18l-1.5 2M17 18l1.5 2'></path></svg></span><span>{'AI ගුරුතුමා' if language=='si' else 'AI Tutor'}</span></a>
    <a class='nav-link{' active' if active_nav == 'progress' else ''}' href='/student/progress'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='M4 20V10'></path><path d='M10 20V4'></path><path d='M16 20v-7'></path><path d='M22 20v-4'></path></svg></span><span>{'ප්‍රගතිය' if language=='si' else 'Progress'}</span></a>
    <div class='nav-section-title'>{'සහාය' if language=='si' else 'SUPPORT'}</div>
    <a class='nav-link{' active' if active_nav == 'calendar' else ''}' href='/student/calendar'><span class='nav-icon'><svg viewBox='0 0 24 24'><rect x='3' y='5' width='18' height='16' rx='2'></rect><path d='M16 3v4M8 3v4M3 10h18'></path></svg></span><span>{'දින දර්ශනය' if language=='si' else 'Calendar'}</span></a>
    <a class='nav-link{' active' if active_nav == 'messages' else ''}' href='/student/messages'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='M4 6h16v9a2 2 0 0 1-2 2H9l-5 4V8a2 2 0 0 1 2-2z'></path></svg></span><span>{'පණිවිඩ' if language=='si' else 'Messages'}</span></a>
    <a class='nav-link{' active' if active_nav == 'achievements' else ''}' href='/student/achievements'><span class='nav-icon'><svg viewBox='0 0 24 24'><circle cx='12' cy='8' r='4'></circle><path d='m8 14-2 7 6-3 6 3-2-7'></path></svg></span><span>{'ජයග්‍රහණ' if language=='si' else 'Achievements'}</span></a>
    <a class='nav-link{' active' if active_nav == 'settings' else ''}' href='/student/settings'><span class='nav-icon'><svg viewBox='0 0 24 24'><circle cx='12' cy='12' r='3'></circle><path d='M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 0 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 0 1-4 0v-.2a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.6-1H3a2 2 0 0 1 0-4h.2a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h.1a1.7 1.7 0 0 0 1-1.6V3a2 2 0 0 1 4 0v.2a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 0 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v.1a1.7 1.7 0 0 0 1.6 1H21a2 2 0 0 1 0 4h-.2a1.7 1.7 0 0 0-1.6 1z'></path></svg></span><span>{'සැකසුම්' if language=='si' else 'Settings'}</span></a>
    </nav>
    <a class='side-footer-link' href='/logout'><span class='nav-icon'><svg viewBox='0 0 24 24'><path d='M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4'></path><path d='M16 17l5-5-5-5'></path><path d='M21 12H9'></path></svg></span><span>{'ඉවත් වන්න' if language=='si' else 'Logout'}</span></a></aside><main class='main'><div class='dashboard-topbar'><div class='dashboard-topbar-spacer'></div><div class='header-actions'><button class='header-icon-btn header-action-btn' type='button' id='headerSearchBtn' aria-label='Search'><svg viewBox='0 0 24 24'><circle cx='11' cy='11' r='7'></circle><path d='m20 20-3.5-3.5'></path></svg></button><button class='header-icon-btn header-action-btn notification-btn' type='button' id='notificationBtn' aria-label='Notifications'><svg viewBox='0 0 24 24'><path d='M15 17H5.5a1.5 1.5 0 0 1-1.2-2.4l1.1-1.4A6.7 6.7 0 0 0 6.8 9V8a5.2 5.2 0 1 1 10.4 0v1a6.7 6.7 0 0 0 1.4 4.2l1.1 1.4a1.5 1.5 0 0 1-1.2 2.4H15'></path><path d='M10 17a2 2 0 1 0 4 0'></path></svg><span class='notification-badge'>5</span></button><div class='notification-dropdown' id='notificationDropdown'><div>{'නව දැනුම්දීම් නොමැත' if language=='si' else 'No new notifications'}</div></div><button class='header-icon-btn header-action-btn' type='button' id='headerMessageBtn' aria-label='Messages'><svg viewBox='0 0 24 24'><path d='M4 6h16v9a2 2 0 0 1-2 2H9l-5 4V8a2 2 0 0 1 2-2z'></path></svg></button><div class='country-flag-wrap' aria-label='Sri Lanka'><img src='/static/images/sl-flag.png' alt='Sri Lanka' class='country-flag-img'></div><div class='student-menu'><button class='student-menu-btn student-mini-profile' type='button' id='studentMenuBtn' aria-haspopup='true' aria-expanded='false'><span class='header-avatar'>{f"<img src='{escape(profile_image_url)}' alt='Student photo' class='header-avatar'>" if profile_image_url else avatar_initials}</span><span class='student-menu-copy'><strong>{escape(student.name)}</strong><small>{f"{escape(str(student.grade))} ශ්‍රේණියේ ශිෂ්‍යයා" if language=='si' else f"Grade {escape(str(student.grade))} Student"}</small></span><svg viewBox='0 0 24 24' class='menu-caret'><path d='m6 9 6 6 6-6'></path></svg></button><div class='student-dropdown' id='studentDropdown'><a href='/student/profile'>{'මගේ පැතිකඩ' if language=='si' else 'My Profile'}</a><a href='/student/account-settings'>{'ගිණුම් සැකසුම්' if language=='si' else 'Account Settings'}</a><button type='button' id='changePhotoMenuBtn'>{'ඡායාරූපය වෙනස් කරන්න' if language=='si' else 'Change Photo'}</button><a href='/logout'>{'ඉවත් වන්න' if language=='si' else 'Logout'}</a></div></div></div></div><div class='top'><div class='greeting-left'><div class='student-avatar'>{f"<img src='{escape(profile_image_url)}' alt='Student photo' class='student-avatar'>" if profile_image_url else avatar_initials}</div><div class='greeting-copy'><h2>{'සුභ දිනක්, ' if language=='si' else 'Good Morning, '}{student.name}!</h2><small>{'ඉදිරියට යන්න. ඔබේ අනාගතය අද ගොඩනැගෙයි.' if language=='si' else 'Keep going. Your future is being built today.'}</small><button type='button' id='changePhotoBtn' class='change-photo-link' onclick='window.openStudentPhotoModal && window.openStudentPhotoModal();'>{'ඡායාරූපය වෙනස් කරන්න' if language=='si' else 'Change Photo'}</button></div></div></div>
    {f"<p style='padding:10px;border-radius:8px;background:#fff3cd;color:#7a4f00;border:1px solid #ffe69c;'>{session.pop('subscription_expired_message', None) or ''}</p>"}
    <div class='dashboard-content-inner'>"""
    dashboard_shell_end = """</div></main></div></body></html>"""
    return dashboard_shell_start + inner_html + dashboard_shell_end

@app.route("/student/learning-path", methods=["GET"])
def student_learning_path():
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))
    ensure_chapter_learning_tables()
    student = db.session.get(Student, student_id)
    is_si = student.medium == "Sinhala"
    grade = normalize_grade(student.grade)

    labels = {
        "title": "මගේ විෂයයන්" if is_si else "My Subjects",
        "subtitle": "ඔබේ පුද්ගලික ඉගෙනුම් ගමන ඉදිරියට ගෙන යන්න" if is_si else "Continue your personalized learning journey",
        "no_modules": "මෙම විෂයයට මොඩියුල නොමැත" if is_si else "No modules available for this subject",
        "manage_subjects": "විෂයයන් කළමනාකරණය" if is_si else "Manage Subjects",
        "choose_subjects": "ඔබේ විෂයයන් තෝරන්න" if is_si else "Choose Your Subjects",
        "choose_subjects_copy": "ඔබ කැමති විෂයයන් තෝරා ඔබේ ඉගෙනුම් ගමන පෞද්ගලික කරන්න." if is_si else "Select the subjects you want to study to personalize your learning path.",
        "save": "සුරකින්න" if is_si else "Save",
        "no_grade_subjects": "ඔබගේ ශ්‍රේණිය සඳහා ක්‍රියාකාරී විෂයයන් නොමැත." if is_si else "No subjects are available for your grade right now.",
    }

    subjects = get_subjects_for_grade(grade, active_only=True)
    subject_map = {s.id: s for s in subjects}
    selected_rows = StudentSelectedSubject.query.filter_by(student_id=student.id, is_active=True).order_by(
        StudentSelectedSubject.sort_order.asc(), StudentSelectedSubject.updated_at.desc(), StudentSelectedSubject.id.asc()
    ).all()
    selected_ids = [row.subject_id for row in selected_rows if row.subject_id in subject_map]
    ordered_subjects = [subject_map[sid] for sid in selected_ids]

    fallback_image = "/static/images/subjects/default-subject.jpg"
    subject_sections = []
    for idx, subject in enumerate(ordered_subjects):
        subject_name = subject.subject_name_si if is_si else subject.subject_name_en
        terms = SyllabusTerm.query.filter(
            SyllabusTerm.grade == grade,
            SyllabusTerm.subject.in_([subject.subject_code, subject.subject_name_en, subject.subject_name_si]),
        ).order_by(SyllabusTerm.term_number.asc()).all()
        term_ids = [t.id for t in terms]
        modules = (SyllabusModule.query.filter(SyllabusModule.term_id.in_(term_ids)).order_by(SyllabusModule.module_order.asc(), SyllabusModule.id.asc()).all() if term_ids else [])
        module_cards = []
        for module in modules:
            module_name = module.module_name_si if is_si else module.module_name_en
            module_image = (module.image_si_url if is_si else module.image_en_url) or fallback_image
            active_chapters = SyllabusChapter.query.filter_by(module_id=module.id, is_active=True).all()
            chapter_ids = [c.id for c in active_chapters]
            total_count = len(chapter_ids)
            completed_count = (StudentChapterProgress.query.filter(StudentChapterProgress.student_id == student.id, StudentChapterProgress.chapter_id.in_(chapter_ids), StudentChapterProgress.status == "completed").count() if chapter_ids else 0)
            progress_pct = int((completed_count / total_count) * 100) if total_count else 0
            lesson_text = (f"පාඩම් {completed_count} / {total_count}" if is_si else f"Lesson {completed_count} of {total_count}")
            module_cards.append(f"""
            <a class='module-card' href='/student/subject/{subject.id}/module/{module.id}'>
              <img src='{escape(module_image)}' alt='{escape(module_name)}' loading='lazy'>
              <div class='module-card-body'><h4>{escape(module_name)}</h4><p>{escape(lesson_text)}</p><div class='module-progress'><span style='width:{progress_pct}%;'></span></div></div>
            </a>""")
        subject_sections.append(f"""
        <section class='subject-section subject-row-section' data-subject-id='{subject.id}'><div class='subject-header'><h2>{escape(subject_name)}</h2><div class='subject-header-actions'><div class='subject-reorder-controls'><button type='button' class='subject-reorder-btn subject-move-up' data-action='up' aria-label='Move subject up' {'disabled' if idx == 0 else ''}>↑</button><button type='button' class='subject-reorder-btn subject-move-down' data-action='down' aria-label='Move subject down' {'disabled' if idx == len(ordered_subjects)-1 else ''}>↓</button></div><div class='carousel-controls'><button type='button' class='carousel-btn module-carousel-prev' data-dir='left' aria-label='Scroll left'>‹</button><button type='button' class='carousel-btn module-carousel-next' data-dir='right' aria-label='Scroll right'>›</button></div></div></div><div class='module-carousel-shell' data-subject-id='{subject.id}'><div class='module-carousel-wrap'><div class='module-carousel module-carousel-track'>{''.join(module_cards) if module_cards else f"<div class='no-modules'>{labels['no_modules']}</div>"}</div></div></div></section>""")

    manage_list_html = ''.join([
        f"<label class='subject-option'><input type='checkbox' class='subject-checkbox' value='{s.id}' {'checked' if s.id in selected_ids else ''}><img src='{escape(((s.image_si_url if is_si else s.image_en_url) or fallback_image))}' alt='{escape(s.subject_name_si if is_si else s.subject_name_en)}'><span>{escape(s.subject_name_si if is_si else s.subject_name_en)}</span></label>"
        for s in subjects
    ])

    empty_subjects_html = ""
    if not subjects:
        empty_subjects_html = f"<div class='empty-state-card'><h3>{labels['no_grade_subjects']}</h3></div>"
    elif not ordered_subjects:
        empty_subjects_html = f"<div class='premium-choose-card'><h3>{labels['choose_subjects']}</h3><p>{labels['choose_subjects_copy']}</p><button type='button' id='openManageSubjectsBtn' class='manage-subjects-btn'>{labels['manage_subjects']}</button></div>"

    content_html = f"""
    <style>.subject-page-hero{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}.subject-page-hero h1{{margin:0 0 4px}} .subject-page-hero p{{margin:0;color:#64748b}}.manage-subjects-btn{{border:none;border-radius:10px;padding:10px 16px;background:#2563eb;color:#fff;font-weight:700;cursor:pointer}}.subjects-stack{{margin-top:16px;display:flex;flex-direction:column;gap:20px}}.subject-section{{background:rgba(255,255,255,.65);border:1px solid rgba(255,255,255,.8);border-radius:20px;padding:16px 16px 18px;box-shadow:0 10px 30px rgba(15,23,42,.06)}}.subject-header{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}}.subject-header h2{{margin:0;font-size:22px}}.subject-header-actions,.subject-reorder-controls,.carousel-controls{{display:flex;align-items:center;gap:8px;pointer-events:auto;position:relative;z-index:3}}.subject-reorder-btn{{width:38px;height:38px;border:none;border-radius:10px;background:#e2e8f0;color:#0f172a;font-size:18px;line-height:1;cursor:pointer;position:relative;z-index:4}}.subject-reorder-btn:disabled{{opacity:.45;cursor:not-allowed}}.carousel-btn{{width:36px;height:36px;border:none;border-radius:999px;background:#ffffff;color:#0f172a;font-size:22px;line-height:1;cursor:pointer;box-shadow:0 4px 14px rgba(15,23,42,.12);position:relative;z-index:4}}.module-carousel-shell{{position:relative;z-index:1}}.module-carousel-wrap{{max-width:1148px;overflow:visible}}.module-carousel-track{{display:flex;gap:24px;overflow-x:auto;scroll-behavior:smooth;max-width:100%;padding:4px 2px 8px;-webkit-overflow-scrolling:touch;scrollbar-width:none}}.module-carousel-track::-webkit-scrollbar{{display:none}}.module-card{{flex:0 0 280px;width:280px;min-width:280px;max-width:280px;min-height:260px;display:flex;flex-direction:column;text-decoration:none;color:#0f172a;background:rgba(255,255,255,.92);border:1px solid rgba(226,232,240,.9);border-radius:18px;overflow:hidden;box-shadow:0 10px 25px rgba(2,6,23,.08)}}.module-card img{{width:100%;height:148px;object-fit:cover;display:block;flex:0 0 148px}}.module-card-body{{padding:12px;display:flex;flex-direction:column;flex:1}}.module-card-body h4{{margin:0 0 6px;font-size:16px}}.module-card-body p{{margin:0 0 12px;font-size:13px;color:#475569}}.module-progress{{height:6px;background:#e2e8f0;border-radius:999px;overflow:hidden}}.module-card .module-progress{{margin-top:auto}}.module-progress span{{display:block;height:100%;background:linear-gradient(90deg,#2563eb,#14b8a6)}}.no-modules{{padding:10px 0;color:#64748b}}.premium-choose-card,.empty-state-card{{margin-top:16px;padding:24px;border-radius:20px;background:linear-gradient(135deg,#0f172a,#1d4ed8);color:#fff}}.manage-modal{{position:fixed;inset:0;background:rgba(15,23,42,.55);display:none;align-items:center;justify-content:center;z-index:9999}}.manage-modal.open{{display:flex}}.manage-modal-card{{width:min(760px,92vw);max-height:84vh;overflow:auto;background:#fff;border-radius:18px;padding:18px}}.subject-options-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:12px}}.subject-option{{display:flex;align-items:center;gap:10px;border:1px solid #e2e8f0;border-radius:12px;padding:10px;cursor:pointer}}.subject-option img{{width:48px;height:48px;border-radius:8px;object-fit:cover}}</style>
    <section class='subject-page-hero'><div><h1>{labels['title']}</h1><p>{labels['subtitle']}</p></div>{"" if not subjects else f"<button type='button' id='manageSubjectsTopBtn' class='manage-subjects-btn'>{labels['manage_subjects']}</button>"}</section>
    {empty_subjects_html}
    <section class='subjects-stack'>{''.join(subject_sections)}</section>
    <div class='manage-modal' id='manageSubjectsModal'><div class='manage-modal-card'><h3>{labels['manage_subjects']}</h3><div class='subject-options-grid'>{manage_list_html or f"<div>{labels['no_grade_subjects']}</div>"}</div><div style='margin-top:14px;display:flex;justify-content:flex-end;'><button type='button' id='saveSelectedSubjectsBtn' class='manage-subjects-btn'>{labels['save']}</button></div></div></div>
    <script>
      const manageModal = document.getElementById('manageSubjectsModal');
      document.getElementById('manageSubjectsTopBtn')?.addEventListener('click', ()=>manageModal?.classList.add('open'));
      document.getElementById('openManageSubjectsBtn')?.addEventListener('click', ()=>manageModal?.classList.add('open'));
      manageModal?.addEventListener('click', (e)=>{{ if(e.target===manageModal) manageModal.classList.remove('open'); }});
      document.getElementById('saveSelectedSubjectsBtn')?.addEventListener('click', async ()=>{{
        const selectedIds = Array.from(document.querySelectorAll('.subject-checkbox:checked')).map(x=>Number(x.value)).filter(Number.isInteger);
        await fetch('/student/subjects/select', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{subject_ids:selectedIds}})}});
        window.location.reload();
      }});

      const saveSubjectOrder = async () => {{
        const subjectIds = Array.from(document.querySelectorAll('.subject-row-section')).map((section) => Number(section.dataset.subjectId)).filter(Number.isInteger);
        await fetch('/student/subject-order', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ subject_ids: subjectIds }}) }});
      }};

      const updateMoveButtons = () => {{
        const sections = Array.from(document.querySelectorAll('.subject-row-section'));
        sections.forEach((section, idx) => {{
          const upBtn = section.querySelector('.subject-move-up');
          const downBtn = section.querySelector('.subject-move-down');
          if (upBtn) upBtn.disabled = idx === 0;
          if (downBtn) downBtn.disabled = idx === sections.length - 1;
        }});
      }};

      document.querySelectorAll(".subject-row-section").forEach((section) => {{
        const track = section.querySelector(".module-carousel-track");
        const nextBtn = section.querySelector(".module-carousel-next");
        const prevBtn = section.querySelector(".module-carousel-prev");
        const moveUpBtn = section.querySelector(".subject-move-up");
        const moveDownBtn = section.querySelector(".subject-move-down");

        const getScrollAmount = () => {{
          const card = track?.querySelector(".module-card");
          const gap = 24;
          return card ? card.offsetWidth + gap : 320;
        }};

        nextBtn?.addEventListener("click", () => {{
          track?.scrollBy({{ left: getScrollAmount(), behavior: "smooth" }});
        }});

        prevBtn?.addEventListener("click", () => {{
          track?.scrollBy({{ left: -getScrollAmount(), behavior: "smooth" }});
        }});

        moveUpBtn?.addEventListener("click", async () => {{
          const prev = section.previousElementSibling;
          if (!prev) return;
          prev.parentNode?.insertBefore(section, prev);
          updateMoveButtons();
          await saveSubjectOrder();
        }});

        moveDownBtn?.addEventListener("click", async () => {{
          const next = section.nextElementSibling;
          if (!next) return;
          next.parentNode?.insertBefore(next, section);
          updateMoveButtons();
          await saveSubjectOrder();
        }});
      }});
      updateMoveButtons();

    </script>
    """
    return render_student_dashboard_shell(content_html, active_nav="my_subjects")


@app.route("/student/subject-order", methods=["POST"])
def student_subject_order():
    student_id = session.get("student_id")
    if not student_id:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    subject_ids = payload.get("subject_ids")
    if not isinstance(subject_ids, list):
        return jsonify({"ok": False, "error": "subject_ids must be a list"}), 400
    clean_ids, seen = [], set()
    for item in subject_ids:
        if isinstance(item, int) and item > 0 and item not in seen:
            clean_ids.append(item); seen.add(item)
    rows = StudentSelectedSubject.query.filter_by(student_id=student_id, is_active=True).all()
    row_map = {r.subject_id: r for r in rows}
    for idx, sid in enumerate(clean_ids):
        row = row_map.get(sid)
        if row:
            row.sort_order = idx
            row.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/student/subjects/select", methods=["POST"])
def student_subject_select():
    student_id = session.get("student_id")
    if not student_id:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    ensure_chapter_learning_tables()
    payload = request.get_json(silent=True) or {}
    subject_ids = payload.get("subject_ids") or payload.get("selected_subject_ids")
    if not isinstance(subject_ids, list):
        return jsonify({"ok": False, "error": "subject_ids must be a list"}), 400
    clean_ids, seen = [], set()
    for item in subject_ids:
        if isinstance(item, int) and item > 0 and item not in seen:
            clean_ids.append(item); seen.add(item)
    active_subject_ids = {s.id for s in get_subjects_for_grade(normalize_grade(db.session.get(Student, student_id).grade), active_only=True)}
    clean_ids = [sid for sid in clean_ids if sid in active_subject_ids]

    existing = StudentSelectedSubject.query.filter_by(student_id=student_id).all()
    by_subject = {r.subject_id: r for r in existing}
    now = datetime.utcnow()
    for idx, sid in enumerate(clean_ids):
        row = by_subject.get(sid)
        if row:
            row.is_active = True
            row.sort_order = idx
            row.updated_at = now
        else:
            db.session.add(StudentSelectedSubject(student_id=student_id, subject_id=sid, is_active=True, sort_order=idx, created_at=now, updated_at=now))
    for row in existing:
        if row.subject_id not in clean_ids and row.is_active:
            row.is_active = False
            row.updated_at = now
    db.session.commit()
    return jsonify({"ok": True, "subject_ids": clean_ids})


@app.route("/student/subject/<int:subject_id>/module/<int:module_id>", methods=["GET"])
def student_subject_module_page(subject_id: int, module_id: int):
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))
    ensure_chapter_learning_tables()
    student = db.session.get(Student, student_id)
    module = db.session.get(SyllabusModule, module_id)
    subject = db.session.get(SubjectMaster, subject_id)
    if not module or not subject:
        return redirect("/student/learning-path")
    term = db.session.get(SyllabusTerm, module.term_id)
    is_si = student.medium == "Sinhala"
    subject_name = (subject.subject_name_si if is_si else subject.subject_name_en) or subject.subject_name_en
    module_name = (module.module_name_si if is_si else module.module_name_en) or module.module_name_en
    grade_label = f"{term.grade}" if term and term.grade else f"{student.grade}"
    term_label = str(term.term_number) if term else "-"

    chapters = SyllabusChapter.query.filter_by(module_id=module.id, is_active=True).order_by(SyllabusChapter.chapter_order.asc(), SyllabusChapter.id.asc()).all()
    chapter_ids = [c.id for c in chapters]
    progress_map = {p.chapter_id: p for p in StudentChapterProgress.query.filter(StudentChapterProgress.student_id == student.id, StudentChapterProgress.chapter_id.in_(chapter_ids)).all()} if chapter_ids else {}
    completed_count = sum(1 for c in chapters if (progress_map.get(c.id) and progress_map[c.id].status == "completed"))
    total_count = len(chapters)
    progress_pct = int((completed_count / total_count) * 100) if total_count else 0

    if chapters and chapters[0].id not in progress_map:
        db.session.add(StudentChapterProgress(student_id=student.id, chapter_id=chapters[0].id, status="unlocked"))
        db.session.commit()
        progress_map[chapters[0].id] = StudentChapterProgress.query.filter_by(student_id=student.id, chapter_id=chapters[0].id).first()

    def chapter_status(ch, idx):
        row = progress_map.get(ch.id)
        if row and row.status == "completed":
            return ("completed", "සම්පූර්ණයි" if is_si else "Completed")
        if row and row.status in ("in_progress", "unlocked"):
            return ("in-progress", "ක්‍රියාත්මකයි" if is_si else "In Progress")
        if idx == 0:
            return ("not-started", "ආරම්භ නොකළ" if is_si else "Not Started")
        prev = chapters[idx - 1]
        prev_row = progress_map.get(prev.id)
        if prev_row and prev_row.status == "completed":
            return ("not-started", "ආරම්භ නොකළ" if is_si else "Not Started")
        return ("locked", "අගුළු දමා ඇත" if is_si else "Locked")

    chapter_cards = []
    weak_chapters = []
    unlocked_chapter_ids = set()
    status_counts = {"completed": 0, "in-progress": 0, "not-started": 0, "locked": 0}
    total_video_count = 0
    total_note_count = 0
    total_activity_count = 0
    total_practice_count = 0
    total_test_count = 0
    total_required_items = 0
    total_completed_items = 0
    total_estimated_minutes = int(getattr(module, "estimated_minutes", None) or 45)
    module_recommendation = get_student_next_recommendation(student.id)
    recommended_chapter_id = int(module_recommendation.get("chapter_id") or 0) if module_recommendation else 0
    chapter_mastery_map = {
        row.chapter_id: float(row.avg_mastery or 0)
        for row in db.session.query(
            StudentSkillMastery.chapter_id,
            db.func.avg(StudentSkillMastery.mastery_score).label("avg_mastery"),
        )
        .filter(StudentSkillMastery.student_id == student.id, StudentSkillMastery.chapter_id.in_(chapter_ids))
        .group_by(StudentSkillMastery.chapter_id)
        .all()
    } if chapter_ids else {}
    for idx, ch in enumerate(chapters):
        content_rows = ChapterLearningContent.query.filter_by(chapter_id=ch.id, is_active=True).all()
        ctype_counts = {"video": 0, "note": 0, "activity": 0, "practice": 0, "test": 0}
        for item in content_rows:
            key = (item.content_type or "").strip().lower()
            if key in ctype_counts:
                ctype_counts[key] += 1
            total_estimated_minutes += max(5, int(getattr(item, "estimated_minutes", None) or 0))
        total_content = len(content_rows)
        total_video_count += ctype_counts["video"]
        total_note_count += ctype_counts["note"]
        total_activity_count += ctype_counts["activity"]
        total_practice_count += ctype_counts["practice"]
        total_test_count += ctype_counts["test"]
        completed_content = StudentContentProgress.query.filter(
            StudentContentProgress.student_id == student.id,
            StudentContentProgress.content_id.in_([c.id for c in content_rows]) if content_rows else False,
            StudentContentProgress.status == "completed"
        ).count() if content_rows else 0
        total_required_items += total_content
        total_completed_items += completed_content
        chapter_pct = int((completed_content / total_content) * 100) if total_content else (100 if progress_map.get(ch.id) and progress_map[ch.id].status == "completed" else 0)
        status_key, status_text = chapter_status(ch, idx)
        status_counts[status_key] = status_counts.get(status_key, 0) + 1
        if status_key != "locked":
            unlocked_chapter_ids.add(ch.id)
        if chapter_pct < 50 and status_key != "locked":
            weak_chapters.append((ch, chapter_pct))
        ch_name = (ch.chapter_name_si if is_si else ch.chapter_name_en) or ch.chapter_name_en
        chapter_subtitle = ch.chapter_name_en if is_si else (ch.chapter_name_si or ch.chapter_name_en)
        chapter_icon = escape(getattr(ch, "chapter_icon", None) or "📘")
        chapter_estimated_minutes = int(getattr(ch, "estimated_minutes", None) or 30)
        chapter_xp_reward = int(getattr(ch, "xp_reward", None) or 10)
        total_estimated_minutes += chapter_estimated_minutes
        locked = status_key == "locked"
        first_lesson = (
            Lesson.query
            .filter_by(chapter_id=ch.id, is_active=True)
            .order_by(Lesson.lesson_order.asc())
            .first()
        )
        chapter_btn = (
            f"<button class='chapter-cta locked' type='button' aria-label='Locked'>🔒</button>"
            if locked else
            (
                f"<a class='chapter-cta' href='{url_for('student_lesson_page', lesson_id=first_lesson.id)}'>{'ඉදිරියට යන්න' if status_key == 'in-progress' else ('නැවත බලන්න' if status_key == 'completed' else ('Continue' if status_key == 'in-progress' else 'Start'))}</a>"
                if first_lesson else
                "<button class='chapter-cta' type='button' disabled>Coming Soon</button>"
            )
        )
        is_recommended = recommended_chapter_id and ch.id == recommended_chapter_id
        chapter_mastery = float(chapter_mastery_map.get(ch.id, 0))
        chapter_mastery_badge = ""
        if chapter_mastery > 70:
            chapter_mastery_badge = "<span class='chapter-health mastered'>Mastered</span>"
        elif chapter_mastery < 40 and not locked:
            chapter_mastery_badge = "<span class='chapter-health needs-practice'>Needs Practice</span>"
        chapter_cards.append(f"""
        <article class='module-chapter-card {"locked-card" if locked else ""} {"recommended-chapter" if is_recommended else ""}'>
          <div class='chapter-row'>
            <div class='chapter-leading'>
              <div class='chapter-order'>{ch.chapter_order or idx+1}</div>
              <div class='chapter-icon'>{chapter_icon}</div>
            </div>
            <div class='chapter-main'>
              <small>{'පරිච්ඡේදය' if is_si else 'Chapter'} {ch.chapter_order or idx+1} {("<span class='recommended-badge'>Recommended for You</span>" if is_recommended else "")} {chapter_mastery_badge}</small>
              <h3>{escape(ch_name)}</h3>
              <p>{escape(chapter_subtitle)}</p>
              <div class='content-metrics'><span>🎬 {ctype_counts['video']}</span><span>📝 {ctype_counts['note']}</span><span>🧩 {ctype_counts['activity']}</span><span>🎯 {ctype_counts['practice']}</span><span>❓ {ctype_counts['test']}</span><span>⏱️ {chapter_estimated_minutes}m</span><span>⭐ {chapter_xp_reward} XP</span></div>
            </div>
            <div class='chapter-progress-wrap'>
              <span class='status-pill {status_key}'>{status_text}</span>
              <div class='chapter-progress-label'>{chapter_pct}%</div>
              <div class='module-progress'><span style='width:{chapter_pct}%;'></span></div>
            </div>
            <div class='chapter-actions'>{chapter_btn}</div>
          </div>
        </article>""")

    first_available = next((c for i, c in enumerate(chapters) if chapter_status(c, i)[0] != "locked"), None)
    right_focus = "".join([f"<li>{escape((c.chapter_name_si if is_si else c.chapter_name_en) or c.chapter_name_en)} <strong>{pct}%</strong></li>" for c, pct in weak_chapters[:3]]) or f"<li>{'හොඳින් කරගෙන යනවා!' if is_si else 'Great momentum across chapters!'}</li>"
    eta_hours = max(1, int((total_estimated_minutes or (total_count * 35)) / 60))
    xp_goal = max(student.xp + 150, 150)
    xp_progress = min(100, int((student.xp / xp_goal) * 100)) if xp_goal else 0
    streak_days = max(0, int(getattr(student, "current_streak", 0) or 0))
    total_study_time = int(getattr(student, "total_study_time", 0) or 0)
    mastery_level = "Advanced" if progress_pct >= 80 else ("Growing" if progress_pct >= 50 else "Starter")
    module_number = getattr(module, "module_order", None) or module.id
    module_name_si = getattr(module, "module_name_si", "") or ""
    module_name_en = getattr(module, "module_name_en", "") or ""
    module_thumb_raw = (
        getattr(module, "image_si_url", None)
        if student.medium == "Sinhala"
        else getattr(module, "image_en_url", None)
    )
    module_thumb = escape(module_thumb_raw or "https://img.icons8.com/fluency/240/calculator.png")
    weekly_target = min(total_count, max(3, int((total_count or 3) * 0.6)))
    remaining_to_target = max(0, weekly_target - completed_count)
    completed_for_chart = status_counts.get("completed", 0)
    in_progress_for_chart = status_counts.get("in-progress", 0)
    not_started_for_chart = status_counts.get("not-started", 0) + status_counts.get("locked", 0)
    html = f"""
    <style>
    .module-hub-layout{{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:20px}}
    .module-hero,.module-chapter-card,.module-side-card,.chapter-section{{background:rgba(255,255,255,.82);border:1px solid rgba(255,255,255,.9);border-radius:22px;box-shadow:0 20px 40px rgba(15,23,42,.08);backdrop-filter:blur(14px);transition:transform .28s ease,box-shadow .28s ease}}
    .module-hero:hover,.module-chapter-card:hover,.module-side-card:hover{{transform:translateY(-4px);box-shadow:0 26px 48px rgba(37,99,235,.16)}}
    .module-hero{{padding:20px;background:linear-gradient(130deg,#eef4ff,#f3ebff 54%,#eafdf6)}}
    .hero-grid{{display:grid;grid-template-columns:170px 1fr auto;gap:16px;align-items:center}}
    .hero-media img{{width:160px;height:160px;object-fit:cover;border-radius:20px;box-shadow:0 12px 25px rgba(59,130,246,.2)}}
    .hero-title h1{{margin:0;font-size:clamp(13px,1.2vw,15px);font-weight:600;letter-spacing:.03em;color:#475569}}
    .hero-title h2{{margin:6px 0 4px;font-size:clamp(1rem,1.4vw,1.2rem);line-height:1.1;font-weight:600;color:#0f172a}}
    .hero-title p{{margin:0 0 4px;color:#1e293b;font-size:clamp(1rem,1.4vw,1.2rem);font-weight:600;line-height:1.1}}
    .hero-title em{{display:block;font-style:normal;font-size:16px;color:#64748b;font-weight:500;line-height:1.35;margin-top:4px}}
    .hero-stats{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:14px}}
    .hero-stat{{border-radius:16px;background:rgba(255,255,255,.72);padding:10px 12px;font-size:13px;color:#475569}} .hero-stat strong{{display:block;color:#0f172a;font-size:16px}}
    .module-progress{{height:10px;border-radius:999px;background:#dbeafe;overflow:hidden}} .module-progress span{{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,#2563eb,#4f46e5);transition:width .7s ease}}
    .hero-cta{{display:flex;flex-direction:column;gap:10px}} .hero-cta a{{text-decoration:none;text-align:center;padding:12px 16px;border-radius:14px;font-weight:700;transition:all .25s ease}}
    .cta-primary{{background:linear-gradient(135deg,#2563eb,#4f46e5);color:white;box-shadow:0 10px 22px rgba(37,99,235,.3)}} .cta-primary:hover{{box-shadow:0 0 0 4px rgba(59,130,246,.2),0 18px 30px rgba(37,99,235,.35)}}
    .cta-secondary{{background:white;color:#1e3a8a;border:1px solid #c7d2fe}}
    .chapter-section{{padding:18px;margin-top:14px}} .chapter-section h3{{margin:0 0 14px}}
    .module-chapter-list{{display:flex;flex-direction:column;gap:12px}}
    .module-chapter-card{{padding:14px 16px}} .chapter-row{{display:grid;grid-template-columns:auto 1fr 210px auto;gap:14px;align-items:center}}
    .chapter-leading{{display:flex;gap:10px;align-items:center}} .chapter-order{{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:#1d4ed8;color:#fff;font-weight:700}}
    .chapter-icon{{width:50px;height:50px;border-radius:14px;background:#eff6ff;display:flex;align-items:center;justify-content:center;font-size:22px}}
    .chapter-main h3{{margin:3px 0;font-size:20px}} .chapter-main p{{margin:0;color:#64748b}} .content-metrics{{margin-top:8px;display:flex;gap:9px;flex-wrap:wrap;color:#334155;font-size:13px}}
    .chapter-progress-wrap{{display:flex;flex-direction:column;gap:6px}} .chapter-progress-label{{font-weight:700;color:#1e3a8a}}
    .status-pill{{padding:6px 10px;border-radius:999px;font-size:11px;font-weight:700;justify-self:flex-start}} .status-pill.completed{{background:#dcfce7;color:#166534}} .status-pill.in-progress{{background:#dbeafe;color:#1d4ed8}} .status-pill.not-started{{background:#e2e8f0;color:#334155}} .status-pill.locked{{background:#e5e7eb;color:#6b7280}}
    .chapter-cta{{display:inline-flex;align-items:center;justify-content:center;padding:10px 14px;border-radius:12px;background:linear-gradient(135deg,#2563eb,#4f46e5);color:#fff;text-decoration:none;font-weight:700;white-space:nowrap}}
    .chapter-cta.locked{{background:#cbd5e1;color:#64748b;cursor:not-allowed;border:0}}
    .locked-card{{opacity:.62}}
    .recommended-chapter{{box-shadow:0 0 0 2px #fde047,0 16px 35px rgba(250,204,21,.35)}}
    .recommended-badge{{display:inline-flex;margin-left:8px;padding:2px 8px;border-radius:999px;background:linear-gradient(135deg,#fef08a,#facc15);font-size:10px;font-weight:800;color:#713f12}}.chapter-health{{display:inline-flex;margin-left:6px;padding:2px 8px;border-radius:999px;font-size:10px;font-weight:800}}.chapter-health.needs-practice{{background:#fef3c7;color:#92400e}}.chapter-health.mastered{{background:#dcfce7;color:#166534}}
    .module-right{{display:flex;flex-direction:column;gap:14px}} .module-side-card{{padding:16px}}
    .radial{{width:160px;height:160px;margin:4px auto 10px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(#22c55e 0 {completed_for_chart*100//max(1,total_count)}%,#3b82f6 {completed_for_chart*100//max(1,total_count)}% {(completed_for_chart+in_progress_for_chart)*100//max(1,total_count)}%,#e2e8f0 0 100%)}}
    .radial-inner{{width:118px;height:118px;border-radius:50%;display:grid;place-items:center;background:white;font-weight:800;font-size:33px;color:#1e3a8a}}
    .legend{{display:grid;gap:6px;font-size:13px;color:#475569}} .legend div{{display:flex;justify-content:space-between}}
    @media(max-width:1180px){{.hero-grid{{grid-template-columns:140px 1fr}}.hero-cta{{grid-column:1/-1;flex-direction:row}}.module-hub-layout{{grid-template-columns:1fr}}.module-right{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}}}}
    @media(max-width:860px){{.chapter-row{{grid-template-columns:1fr;gap:10px}}.module-right{{grid-template-columns:1fr}}.hero-grid{{grid-template-columns:1fr}}.hero-media img{{width:120px;height:120px}}.hero-title h2{{font-size:clamp(1.9rem,8vw,2.3rem)}}.hero-title p{{font-size:clamp(1.2rem,6vw,1.5rem)}}.hero-title em{{font-size:15px}}}}
    </style>
    <section class='module-hub-layout'>
      <div>
        <div class='module-hero'>
          <div class='hero-grid'>
            <div class='hero-media'><img src='{module_thumb}' alt='Module image'></div>
            <div class='hero-title'><h1>{escape(subject_name)} • {escape(grade_label)} {'ශ්‍රේණිය' if is_si else 'Grade'}</h1><h2>{'මොඩියුලය' if is_si else 'Module'} {module_number}</h2>{f"<p>{escape(module_name_si)}</p><em>{escape(module_name_en)}</em>" if is_si else f"<p>{escape(module_name_en)}</p><em>{escape(module_name_si)}</em>"}
              <div class='hero-stats'><div class='hero-stat'><strong>{progress_pct}%</strong>{'සම්පූර්ණ ප්‍රගතිය' if is_si else 'Overall progress'}</div><div class='hero-stat'><strong>{completed_count}/{total_count}</strong>{'අවසන් පාඩම්' if is_si else 'Completed lessons'}</div><div class='hero-stat'><strong>~{eta_hours}h</strong>{'ඇස්තමේන්තු කාලය' if is_si else 'Estimated time'}</div><div class='hero-stat'><strong>{streak_days} {'දින' if is_si else 'days'}</strong>{'නිරන්තර ඉගෙනීම' if is_si else 'Learning streak'}</div><div class='hero-stat'><strong>{mastery_level}</strong>{'දක්ෂතා මට්ටම' if is_si else 'Mastery level'}</div><div class='hero-stat'><strong>{total_completed_items}/{total_required_items or 1}</strong>{'අන්තර්ගත අවසන්' if is_si else 'Content complete'}</div></div>
              <div class='module-progress' style='margin-top:12px'><span style='width:{progress_pct}%'></span></div>
            </div>
            <div class='hero-cta'><a class='cta-primary' href='/student/chapter/{first_available.id if first_available else "#"}'>▶ {'ඉගෙනීම පටන් ගන්න' if is_si else 'Start Learning'}</a><a class='cta-secondary' href='/student/chapter/{first_available.id if first_available else "#"}'>{'ඉදිරියට යන්න' if is_si else 'Continue'}</a></div>
          </div>
        </div>
        <section class='chapter-section'><h3>📘 {'පරිච්ඡේද' if is_si else 'Chapters'}</h3><div class='module-chapter-list'>{''.join(chapter_cards) if chapter_cards else f"<div class='module-chapter-card'><p>{'මෙම මොඩියුලයට තවම අධ්‍යාය නැත.' if is_si else 'No chapters are available for this module yet.'}</p></div>"}</div></section>
      </div>
      <aside class='module-right'>
        <div class='module-side-card'><h3>{'මොඩියුල ප්‍රගතිය' if is_si else 'Progress Snapshot'}</h3><div class='radial'><div class='radial-inner'>{progress_pct}%</div></div><div class='legend'><div><span>🟢 {'සම්පූර්ණයි' if is_si else 'Completed'}</span><strong>{completed_for_chart}</strong></div><div><span>🔵 {'ක්‍රියාත්මකයි' if is_si else 'In Progress'}</span><strong>{in_progress_for_chart}</strong></div><div><span>⚪ {'ආරම්භ කර නැහැ' if is_si else 'Not Started'}</span><strong>{not_started_for_chart}</strong></div></div></div>
        <div class='module-side-card'><h3>AI {'ගුරුහිතම' if is_si else 'Tutor'}</h3><p>{'ඔබගේ දුර්වල කොටස් සඳහා අභිරුචි සහාය ලබාගන්න.' if is_si else 'Get adaptive help for weak chapters and quick concept explainers.'}</p><div style='font-size:48px'>🤖</div><a class='chapter-cta' href='/student/ai-tutor' style='margin-top:8px'>AI {'උදව් ලබා ගන්න' if is_si else 'Get Help'}</a></div>
        <div class='module-side-card'><h3>{'ත්‍යාග සහ XP' if is_si else 'Rewards & XP'}</h3><p>XP {student.xp} / {xp_goal}</p><div class='module-progress'><span style='width:{xp_progress}%;background:linear-gradient(90deg,#22c55e,#14b8a6)'></span></div><p style='margin:8px 0 0'>🔥 {streak_days} {'දින' if is_si else 'day streak'}</p></div>
        <div class='module-side-card'><h3>{'සතියේ ඉලක්කය' if is_si else 'Weekly Target'}</h3><p>{'ඉලක්කය' if is_si else 'Target'}: {weekly_target} {'පරිච්ඡේද' if is_si else 'chapters'}</p><p>{'තවත් අවශ්‍ය' if is_si else 'Remaining'}: {remaining_to_target}</p><ul>{right_focus}</ul></div>
      </aside>
    </section>
    """
    return render_student_dashboard_shell(html, active_nav="my_subjects")





@app.route("/student/live-classes", methods=["GET"])
@app.route("/student/assignments", methods=["GET"])
@app.route("/student/quizzes", methods=["GET"])
@app.route("/student/study-materials", methods=["GET"])
@app.route("/student/ai-tutor", methods=["GET"])
@app.route("/student/progress", methods=["GET"])
@app.route("/student/calendar", methods=["GET"])
@app.route("/student/messages", methods=["GET"])
@app.route("/student/achievements", methods=["GET"])
@app.route("/student/settings", methods=["GET"])
def student_shell_pages():
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))
    student = db.session.get(Student, student_id)
    route_map = {
        "/student/live-classes": ("Live Classes", "live_classes"),
        "/student/assignments": ("Assignments", "assignments"),
        "/student/quizzes": ("Quizzes", "quizzes"),
        "/student/study-materials": ("Study Materials", "study_materials"),
        "/student/ai-tutor": ("AI Tutor", "ai_tutor"),
        "/student/progress": ("Progress", "progress"),
        "/student/calendar": ("Calendar", "calendar"),
        "/student/messages": ("Messages", "messages"),
        "/student/achievements": ("Achievements", "achievements"),
        "/student/settings": ("Settings", "settings"),
    }
    page_title, active_nav = route_map.get(request.path, ("Student", "dashboard"))
    content_html = f"<div class='card' style='padding:18px;'><h2>{escape(page_title)}</h2><p>Content coming soon.</p></div>"
    return render_student_dashboard_shell(content_html, active_nav=active_nav)

@app.route("/student/chapter/<int:chapter_id>", methods=["GET", "POST"])
def student_chapter_page(chapter_id: int):
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))
    ensure_chapter_learning_tables()
    ensure_lesson_engine_tables()
    student = db.session.get(Student, student_id)
    progress = StudentChapterProgress.query.filter_by(student_id=student.id, chapter_id=chapter_id).first()
    if not progress or progress.status == "locked":
        return "<h2>Chapter is locked.</h2>", 403
    if progress.status == "unlocked":
        progress.status = "in_progress"

    if request.method == "POST":
        content_id = int(request.form.get("content_id"))
        cp = StudentContentProgress.query.filter_by(student_id=student.id, content_id=content_id).first()
        if not cp:
            cp = StudentContentProgress(student_id=student.id, content_id=content_id)
            db.session.add(cp)
        cp.status = "completed"
        cp.completed_at = datetime.utcnow()

    contents = ChapterLearningContent.query.filter_by(chapter_id=chapter_id, is_active=True).order_by(ChapterLearningContent.content_order.asc(), ChapterLearningContent.id.asc()).all()
    required_ids = [c.id for c in contents if c.is_required]
    completed_ids = {row.content_id for row in StudentContentProgress.query.filter_by(student_id=student.id, status="completed").all()}

    body_key = "si" if student.medium == "Sinhala" else "en"
    items_html = []
    for c in contents:
        body = escape(getattr(c, f"content_body_{body_key}") or "")
        title = escape(getattr(c, f"title_{body_key}") or c.title_en)
        done = "✅" if c.id in completed_ids else "⬜"
        video_html = ""
        if c.content_type == "video":
            normalized_video_url = normalize_youtube_embed_url(c.content_url)
            if normalized_video_url:
                embed_with_api = f"{normalized_video_url}{'&' if '?' in normalized_video_url else '?'}enablejsapi=1"
                video_html = f"<div id='video-wrap-{c.id}'><div id='player-{c.id}' data-embed-url='{escape(embed_with_api)}' style='max-width:800px;height:450px;'></div><button id='continue-{c.id}' style='display:none;'>Continue</button><div id='analytics-{c.id}'></div></div>"
            else:
                video_html = "<span>Invalid video URL</span>" if c.content_url else ""
        else:
            video_html = f"<a href='{escape(c.content_url)}' target='_blank'>Open</a>" if c.content_url else ""
        mark_btn = "" if c.content_type in {"practice", "test"} else f"<form method='post' style='display:inline;'><input type='hidden' name='content_id' value='{c.id}'><button type='submit'>Mark Completed</button></form>"
        items_html.append(f"<li>{done} <strong>{title}</strong> ({c.content_type}) <div>{body}</div>{video_html}{mark_btn}</li>")

    video_contents = [c for c in contents if c.content_type == 'video']
    interaction_payload = {}
    for c in video_contents:
        inters = VideoInteraction.query.filter_by(content_id=c.id).order_by(VideoInteraction.trigger_seconds.asc()).all()
        packed = []
        for i in inters:
            q = db.session.get(Question, i.question_id)
            if not q: continue
            question_type = (q.question_type or "mcq")
            if question_type == "mcq":
                correct_answer = q.correct_option
            elif question_type == "short_answer":
                correct_answer = q.correct_answer_text
            elif question_type == "box_input":
                correct_answer = q.box_answers
            elif question_type == "matching_pairs":
                correct_answer = q.matching_answers_si if student.medium == "Sinhala" else q.matching_answers_en
            elif question_type == "tap_select_image":
                correct_answer = q.correct_area_id
            else:
                correct_answer = None
            packed.append({
                "id": i.id,
                "content_id": c.id,
                "trigger_seconds": i.trigger_seconds,
                "question_id": q.id,
                "question_type": question_type,
                "required": i.required_answer,
                "pause": i.pause_video,
                "question_text": q.question_text_si if student.medium == "Sinhala" else q.question_text_en,
                "question_text_en": q.question_text_en or "",
                "question_text_si": q.question_text_si or "",
                "image_url": q.image_url or "",
                "drag_items_json": q.drag_items_json or "[]",
                "drag_container_image_url": q.drag_container_image_url or "",
                "drag_groups_json": q.drag_groups_json or "",
                "option_a_en": q.option_a_en or "",
                "option_b_en": q.option_b_en or "",
                "option_c_en": q.option_c_en or "",
                "option_d_en": q.option_d_en or "",
                "option_a_si": q.option_a_si or "",
                "option_b_si": q.option_b_si or "",
                "option_c_si": q.option_c_si or "",
                "option_d_si": q.option_d_si or "",
                "correct_option": q.correct_option or "",
                "correct_answer_text": q.correct_answer_text or "",
                "box_template": q.box_template or "",
                "box_answers": q.box_answers or "",
                "matching_left_en": q.matching_left_en or "[]",
                "matching_right_en": q.matching_right_en or "[]",
                "matching_answers_en": q.matching_answers_en or "{}",
                "matching_left_si": q.matching_left_si or "[]",
                "matching_right_si": q.matching_right_si or "[]",
                "matching_answers_si": q.matching_answers_si or "{}",
                "tap_areas_json": q.tap_areas_json or "[]",
                "correct_area_id": q.correct_area_id or "",
                "answer_data": {
                    "correct_answer": correct_answer,
                    "explanation_en": q.explanation_en,
                    "explanation_si": q.explanation_si,
                },
            })
        interaction_payload[c.id] = packed

    required_by_content = {c.id: [i.id for i in VideoInteraction.query.filter_by(content_id=c.id, required_answer=True).all()] for c in video_contents}

    if required_ids and all(cid in completed_ids for cid in required_ids):
        progress.status = "completed"
        progress.completed_at = datetime.utcnow()

    db.session.commit()
    return f"""<h1>Chapter Learning</h1>
    <script src='https://www.youtube.com/iframe_api'></script>
    <style>.quiz-overlay{{position:fixed;inset:0;background:rgba(0,0,0,.75);display:none;align-items:center;justify-content:center;z-index:9999}} .quiz-card{{background:#fff;padding:20px;border-radius:16px;max-width:760px;width:95%;max-height:90vh;overflow:auto}} .quiz-card button{{font-size:16px;padding:10px 14px;margin:6px 0}} .quiz-options label{{display:block;margin:8px 0}} .tap-select-wrap{{position:relative;display:inline-block;max-width:100%}} .tap-select-image{{max-width:100%;display:block;border:1px solid #ddd}} .tap-select-overlay{{position:absolute;inset:0;width:100%;height:100%}} .tap-area{{fill:transparent;cursor:pointer}} .tap-area.selected{{fill:rgba(34,197,94,.35);stroke:#16a34a;stroke-width:2}} .tap-popup-wrapper{{position:relative;display:inline-block;max-width:100%}} .tap-popup-wrapper img{{display:block;max-width:100%;height:auto;border:1px solid #ddd;border-radius:6px}} .tap-popup-wrapper .tap-area{{position:absolute;cursor:pointer;background:#fff;border-radius:16px;border:0 8px 20px rgba(0,0,0,.06);box-sizing:border-box}} .tap-popup-wrapper .tap-area.selected{{background:rgba(34,197,94,0.25);border:2px solid #22c55e}}</style>
    {drag_drop_group_assets()}
    <div id='quiz-overlay' class='quiz-overlay'><div class='quiz-card'><div id='quiz-body'></div></div></div>
    <ol>{''.join(items_html)}</ol><p><a href='/student/learning-path'>Back to path</a></p>
    <script>
    const interactions = {json.dumps(interaction_payload)};
    const requiredByContent = {json.dumps(required_by_content)};
    const medium = {json.dumps(student.medium)};
    const players = {{}};
    const shownInteractionIds = new Set();
    const answeredInteractionIds = new Set();
    const pollers = {{}};
    let activeInteraction = null;

    function escapeHtml(value) {{
      return String(value || '').replace(/[&<>'"]/g, function(ch) {{
        return ({{'&':'&amp;','<':'&lt;','>':'&gt;',\"'\":'&#39;','\"':'&quot;'}})[ch] || ch;
      }});
    }}

    function getInteractionQuestionText(interaction) {{
      if ((medium || '').toLowerCase() === 'sinhala' && interaction.question_text_si) return interaction.question_text_si;
      return interaction.question_text_en || interaction.question_text_si || 'Question';
    }}

    function normalizeLocalImageUrl(url) {{
      if (!url) return '';
      const v = String(url).trim();
      if (!v) return '';
      if (v.startsWith('/') || v.startsWith('http://') || v.startsWith('https://') || v.startsWith('//') || v.startsWith('data:')) return v;
      return '/' + v;
    }}

    function getInteractionOptions(interaction) {{
      const useSi = (medium || '').toLowerCase() === 'sinhala';
      const opts = [
        {{key:'A', label: useSi ? interaction.option_a_si : interaction.option_a_en}},
        {{key:'B', label: useSi ? interaction.option_b_si : interaction.option_b_en}},
        {{key:'C', label: useSi ? interaction.option_c_si : interaction.option_c_en}},
        {{key:'D', label: useSi ? interaction.option_d_si : interaction.option_d_en}},
      ];
      return opts.filter(o => o.label);
    }}

    function showInteractionModal(contentId, interaction) {{
      activeInteraction = {{contentId, interaction}};
      const overlay = document.getElementById('quiz-overlay');
      const body = document.getElementById('quiz-body');
      const qText = getInteractionQuestionText(interaction);
      const qType = (interaction.question_type || 'mcq').toLowerCase();
      const imageHtml = (qType !== 'tap_select_image' && qType !== 'drag_drop_group_container' && interaction.image_url) ? `<p><img src='${{escapeHtml(normalizeLocalImageUrl(interaction.image_url))}}' alt='Question image' style='max-width:100%;border:1px solid #ddd;border-radius:6px;'></p>` : '';
      let controlHtml = '';
      if (qType === 'mcq') {{
        const options = getInteractionOptions(interaction);
        controlHtml = `<div class='quiz-options'>${{options.map(o => `<label><input type='radio' name='interactive_answer' value='${{o.key}}'> ${{escapeHtml(o.key)}}. ${{escapeHtml(o.label)}}</label>`).join('')}}</div>`;
      }} else if (qType === 'short_answer') {{
        controlHtml = "<input id='interactive_text_answer' type='text' style='width:100%;padding:8px;' placeholder='Type your answer'>";
      }} else if (qType === 'box_input') {{
        controlHtml = `<div>${{escapeHtml(interaction.box_template || '')}}</div><input id='interactive_box_answer' type='text' style='width:100%;padding:8px;margin-top:8px;' placeholder='Enter box input answer'>`;
      }} else if (qType === 'matching_pairs') {{
        controlHtml = "<p>Match the pairs (left->right) using format: 1:A,2:B</p><input id='interactive_matching_answer' type='text' style='width:100%;padding:8px;' placeholder='1:A,2:B'>";
      }} else if (qType === 'drag_drop_group_container') {{
        const rawItems = interaction.drag_items_json || '[]';
        let items = [];
        try {{ items = JSON.parse(rawItems); }} catch(e) {{ items = []; }}
        const basket = normalizeLocalImageUrl(interaction.drag_container_image_url || '');
        if (!items.length || !basket) {{
          controlHtml = `<p style="color:#b45309;">Drag-drop assets are missing for this interactive question. Please check Drag Items JSON and Container Image URL in admin question setup.</p>`;
        }} else {{
          const safeGroupClass = (value) => String(value || '').toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '');
          controlHtml = `<div class='drag-drop-question interactive-drag-drop-question' data-question-id='interactive'><div class='drag-items-row'>${{items.map(it => {{
            const group = String(it.group || '');
            const groupClass = safeGroupClass(group);
            return `<img class="dd-item ${{groupClass ? `dd-item-${{groupClass}}` : ''}}" data-id="${{escapeHtml(String(it.id || ''))}}" data-group="${{escapeHtml(group)}}" src="${{escapeHtml(normalizeLocalImageUrl(it.image_url || ''))}}" alt="${{escapeHtml(String(it.label_si || it.label_en || it.group || 'drag item'))}}">`;
          }}).join('')}}</div><div class='dd-drop-zone'><img class='dd-basket' src='${{escapeHtml(basket)}}' alt='drop container'></div><input id='interactive_drag_answer' class='drag-answer-json' name='answer_interactive' type='hidden' value=''></div>`;
        }}
      }} else if (qType === 'tap_select_image') {{
        const question = interaction;
        console.log("tap areas", question.tap_areas_json);
        let areas = [];
        try {{
          areas = JSON.parse(question.tap_areas_json || "[]");
        }} catch (e) {{
          areas = [];
        }}
        controlHtml = `<div class='tap-popup-wrapper' id='interactive_tap_wrapper'><img src='${{escapeHtml(normalizeLocalImageUrl(interaction.image_url || ''))}}' id='interactive_tap_image' alt='Question image'><input id='interactive_tap_answer' type='hidden'></div><p id='tap_help'>Select an area to continue.</p>`;
        setTimeout(() => {{
          const wrapper = document.getElementById('interactive_tap_wrapper');
          const img = document.getElementById('interactive_tap_image');
          const hiddenAnswer = document.getElementById('interactive_tap_answer');
          const continueBtn = document.getElementById('interactive_continue');
          if (!wrapper || !img || !hiddenAnswer || !continueBtn) return;
          let selectedAreaId = '';
          wrapper.style.position = 'relative';
          wrapper.style.display = 'inline-block';
          img.style.display = 'block';
          const renderAreas = () => {{
            wrapper.querySelectorAll('.tap-area').forEach(n => n.remove());
            areas.forEach(area => {{
              const x = Number(area.x || 0);
              const y = Number(area.y || 0);
              const w = Number(area.width ?? area.w ?? 0);
              const h = Number(area.height ?? area.h ?? 0);
              const areaDiv = document.createElement('div');
              areaDiv.setAttribute('class', 'tap-area');
              areaDiv.style.position = 'absolute';
              areaDiv.style.left = `${{x}}%`;
              areaDiv.style.top = `${{y}}%`;
              areaDiv.style.width = `${{w}}%`;
              areaDiv.style.height = `${{h}}%`;
              areaDiv.style.zIndex = '10';
              areaDiv.style.cursor = 'pointer';
              areaDiv.style.pointerEvents = 'auto';
              areaDiv.style.background = 'transparent';
              const selectArea = () => {{
                selectedAreaId = String(area.id || '');
                hiddenAnswer.value = selectedAreaId;
                wrapper.querySelectorAll('.tap-area').forEach(n => n.classList.remove('selected'));
                areaDiv.classList.add('selected');
                continueBtn.disabled = false;
              }};
              areaDiv.addEventListener("pointerdown", function(e) {{
                e.preventDefault();
                selectArea();
              }});
              wrapper.appendChild(areaDiv);
            }});
          }};
          if (img.complete) renderAreas();
          else img.addEventListener('load', renderAreas, {{ once: true }});
        }}, 0);
      }}
      body.innerHTML = `<h3>Interactive Question</h3><p>${{escapeHtml(qText)}}</p>${{imageHtml}}${{controlHtml}}<button type='button' id='interactive_continue'>Continue</button>`;
      setTimeout(() => {{
        if (window.initDragGroupUI) window.initDragGroupUI(body);
      }}, 0);
      const continueBtn = document.getElementById('interactive_continue');
      if (qType === 'tap_select_image') continueBtn.disabled = true;
      continueBtn.addEventListener('click', () => {{
        let answerValue = 'SKIP';
        if (qType === 'mcq') {{
          const checked = body.querySelector(\"input[name='interactive_answer']:checked\");
          answerValue = checked ? checked.value : 'SKIP';
        }} else if (qType === 'short_answer') {{
          answerValue = (document.getElementById('interactive_text_answer') || {{value:''}}).value || '';
        }} else if (qType === 'box_input') {{
          answerValue = (document.getElementById('interactive_box_answer') || {{value:''}}).value || '';
        }} else if (qType === 'matching_pairs') {{
          answerValue = (document.getElementById('interactive_matching_answer') || {{value:''}}).value || '';
        }} else if (qType === 'drag_drop_group_container') {{
          answerValue = (document.getElementById('interactive_drag_answer') || {{value:'{{}}'}}).value || '{{}}';
        }} else if (qType === 'tap_select_image') {{
          answerValue = (document.getElementById('interactive_tap_answer') || {{value:''}}).value || '';
          if (!answerValue) return;
        }}
        handleInteractionAnswer(answerValue);
      }});
      overlay.style.display = 'flex';
    }}

    function handleInteractionAnswer(answerValue) {{
      if (!activeInteraction) return;
      const {{contentId, interaction}} = activeInteraction;
      fetch('/student/video-interaction/answer', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ interaction_id: interaction.id, answer: answerValue }})
      }}).catch(() => null);
      answeredInteractionIds.add(interaction.id);
      document.getElementById('quiz-overlay').style.display = 'none';
      activeInteraction = null;
      const player = players[contentId];
      if (player && typeof player.playVideo === 'function') player.playVideo();
    }}

    function startInteractionWatcher(contentId) {{
      if (pollers[contentId]) return;
      pollers[contentId] = setInterval(() => {{
        const player = players[contentId];
        if (!player || typeof player.getCurrentTime !== 'function') return;
        const contentInteractions = interactions[contentId] || [];
        if (!contentInteractions.length) return;
        const currentTime = player.getCurrentTime();
        for (const inter of contentInteractions) {{
          if (shownInteractionIds.has(inter.id)) continue;
          if (currentTime >= Number(inter.trigger_seconds || 0)) {{
            shownInteractionIds.add(inter.id);
            if (inter.pause !== false && typeof player.pauseVideo === 'function') player.pauseVideo();
            showInteractionModal(contentId, inter);
            break;
          }}
        }}
      }}, 500);
    }}

    function onYouTubeIframeAPIReady() {{
      Object.keys(interactions).forEach(cid => {{
        const el = document.getElementById('player-'+cid); if(!el) return;
        const embedUrl = el.dataset.embedUrl || '';
        const videoId = (embedUrl.split('/embed/')[1] || '').split(/[?&/]/)[0];
        if(!videoId) return;
        players[cid] = new YT.Player('player-'+cid, {{
          videoId: videoId,
          playerVars: {{ enablejsapi: 1 }},
          events: {{
            onReady: () => startInteractionWatcher(cid),
            onStateChange: () => {{}}
          }}
        }});
      }});
    }}
    </script>"""


@app.route("/student/video-interaction/answer", methods=["POST"])
def student_video_interaction_answer():
    student = student_session_required()
    payload = request.get_json(silent=True) or {}
    interaction_id = payload.get("interaction_id")
    answer = str(payload.get("answer") or "")
    if not interaction_id:
        return jsonify({"ok": False, "error": "Missing interaction_id"}), 400
    interaction = db.session.get(VideoInteraction, int(interaction_id))
    if not interaction:
        return jsonify({"ok": False, "error": "Interaction not found"}), 404
    question = db.session.get(Question, interaction.question_id)
    if not question:
        return jsonify({"ok": False, "error": "Question not found"}), 404
    question_type = (question.question_type or "mcq").strip().lower()
    is_correct = False
    if question_type == "mcq":
        is_correct = (answer.strip().upper() == (question.correct_option or "").strip().upper())
    elif question_type == "short_answer":
        is_correct = (answer.strip().lower() == (question.correct_answer_text or "").strip().lower())
    elif question_type == "box_input":
        is_correct = (answer.strip().lower() == (question.box_answers or "").strip().lower())
    elif question_type == "matching_pairs":
        is_correct = (answer.strip().lower() == (question.matching_answers_en or "").strip().lower() or answer.strip().lower() == (question.matching_answers_si or "").strip().lower())
    elif question_type == "tap_select_image":
        is_correct = (answer.strip() == (question.correct_area_id or "").strip())
    elif question_type == "drag_drop_group_container":
        form_like = {f"answer_{question.id}": answer}
        is_correct, _ = evaluate_drag_drop_group_container_question(question, form_like)
    attempt = StudentVideoInteractionAttempt(
        student_id=student.id,
        interaction_id=interaction.id,
        answer_text=answer,
        is_correct=bool(is_correct),
    )
    db.session.add(attempt)
    db.session.commit()
    return jsonify({"ok": True, "is_correct": bool(is_correct)})


def recalculate_student_chapter_progress(student_id: int, chapter_id: int) -> None:
    lesson_rows = Lesson.query.filter_by(chapter_id=chapter_id, is_active=True).all()
    if not lesson_rows:
        return
    lesson_ids = [row.id for row in lesson_rows]
    completed_count = StudentLessonProgress.query.filter(
        StudentLessonProgress.student_id == student_id,
        StudentLessonProgress.lesson_id.in_(lesson_ids),
        StudentLessonProgress.is_completed.is_(True),
    ).count()
    status = "completed" if completed_count >= len(lesson_ids) else ("in_progress" if completed_count > 0 else "locked")
    chapter_progress = StudentChapterProgress.query.filter_by(student_id=student_id, chapter_id=chapter_id).first()
    if not chapter_progress:
        chapter_progress = StudentChapterProgress(student_id=student_id, chapter_id=chapter_id)
        db.session.add(chapter_progress)
    chapter_progress.status = status
    chapter_progress.completed_at = datetime.utcnow() if status == "completed" else None


def find_next_lesson(lesson: Lesson) -> Lesson | None:
    same_chapter = (
        Lesson.query.filter(
            Lesson.chapter_id == lesson.chapter_id,
            Lesson.is_active.is_(True),
            Lesson.lesson_order > lesson.lesson_order,
        )
        .order_by(Lesson.lesson_order.asc(), Lesson.id.asc())
        .first()
    )
    if same_chapter:
        return same_chapter
    chapter = db.session.get(SyllabusChapter, lesson.chapter_id)
    if not chapter:
        return None
    next_chapter = (
        SyllabusChapter.query.filter(
            SyllabusChapter.module_id == chapter.module_id,
            SyllabusChapter.is_active.is_(True),
            SyllabusChapter.chapter_order > chapter.chapter_order,
        )
        .order_by(SyllabusChapter.chapter_order.asc(), SyllabusChapter.id.asc())
        .first()
    )
    if not next_chapter:
        return None
    return (
        Lesson.query.filter_by(chapter_id=next_chapter.id, is_active=True)
        .order_by(Lesson.lesson_order.asc(), Lesson.id.asc())
        .first()
    )


def update_student_skill_mastery(student_id: int, lesson_id: int, slide_id: int, is_correct: bool, activity_json) -> StudentSkillMastery | None:
    lesson = db.session.get(Lesson, lesson_id)
    if not lesson:
        return None
    chapter = db.session.get(SyllabusChapter, lesson.chapter_id)
    module = db.session.get(SyllabusModule, chapter.module_id) if chapter else None
    term = db.session.get(SyllabusTerm, module.term_id) if module else None
    student = db.session.get(Student, student_id)
    subject_id = None
    if student and term:
        subject = SubjectMaster.query.filter_by(grade=normalize_grade(student.grade), subject_name_en=term.subject).first()
        if subject:
            subject_id = subject.id
    parsed = activity_json if isinstance(activity_json, dict) else {}
    skill_code = str(parsed.get("skill_code") or "").strip() or f"chapter_{lesson.chapter_id}_lesson_{lesson.id}"
    skill_name_en = str(parsed.get("skill_name_en") or parsed.get("skill_name") or lesson.lesson_title_en or skill_code).strip()
    skill_name_si = str(parsed.get("skill_name_si") or parsed.get("skill_name") or lesson.lesson_title_si or skill_code).strip()
    now = datetime.utcnow()
    mastery = StudentSkillMastery.query.filter_by(student_id=student_id, lesson_id=lesson_id, skill_code=skill_code).first()
    if not mastery:
        mastery = StudentSkillMastery(
            student_id=student_id,
            subject_id=subject_id,
            module_id=module.id if module else None,
            chapter_id=lesson.chapter_id,
            lesson_id=lesson.id,
            skill_code=skill_code,
            skill_name_en=skill_name_en,
            skill_name_si=skill_name_si,
            mastery_score=0,
        )
        db.session.add(mastery)
    mastery.subject_id = subject_id
    mastery.module_id = module.id if module else None
    mastery.chapter_id = lesson.chapter_id
    mastery.skill_name_en = skill_name_en
    mastery.skill_name_si = skill_name_si
    mastery.total_attempts = int(mastery.total_attempts or 0) + 1
    if is_correct:
        mastery.correct_attempts = int(mastery.correct_attempts or 0) + 1
        mastery.mastery_score = min(100.0, max(0.0, float(mastery.mastery_score or 0) + 10.0))
    else:
        mastery.wrong_attempts = int(mastery.wrong_attempts or 0) + 1
        mastery.mastery_score = min(100.0, max(0.0, float(mastery.mastery_score or 0) - 5.0))
    mastery.last_answered_at = now
    mastery.status_en, mastery.status_si = mastery_status_labels(mastery.mastery_score)
    return mastery


@app.route("/student/lesson/<int:lesson_id>", methods=["GET"])
def student_lesson_page(lesson_id: int):
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))
    ensure_lesson_engine_tables()

    student = db.session.get(Student, student_id)
    lesson = Lesson.query.filter_by(id=lesson_id, is_active=True).first()
    if not lesson:
        return "<h2>Lesson not found.</h2>", 404
    chapter = db.session.get(SyllabusChapter, lesson.chapter_id)
    if not chapter:
        return "<h2>Chapter not found for lesson.</h2>", 404
    module = db.session.get(SyllabusModule, chapter.module_id)
    if not module:
        return "<h2>Module not found for chapter.</h2>", 404
    term = db.session.get(SyllabusTerm, module.term_id)

    slides = LessonSlide.query.filter_by(lesson_id=lesson.id, is_active=True).order_by(LessonSlide.slide_order.asc(), LessonSlide.id.asc()).all()
    if not slides:
        return "<h2>No slides found for this lesson.</h2>", 404

    progress = StudentLessonProgress.query.filter_by(student_id=student.id, lesson_id=lesson.id).first()
    if not progress:
        progress = StudentLessonProgress(student_id=student.id, lesson_id=lesson.id, current_slide_order=slides[0].slide_order, completion_percent=0, is_completed=False, last_opened_at=datetime.utcnow())
        db.session.add(progress)
    else:
        progress.last_opened_at = datetime.utcnow()
    db.session.commit()

    is_si = student.medium == "Sinhala"
    lesson_title = lesson.lesson_title_si if is_si else lesson.lesson_title_en
    chapter_name = chapter.chapter_name_si if is_si else chapter.chapter_name_en
    module_name = module.module_name_si if is_si else module.module_name_en
    term_name = (term.term_name_si if is_si else term.term_name_en) if term else ""
    subject_name = term.subject if term else ""
    context_label = " • ".join([x for x in [subject_name, term_name, module_name] if x])
    mastery_row = (
        StudentSkillMastery.query.filter_by(student_id=student.id, lesson_id=lesson.id)
        .order_by(StudentSkillMastery.updated_at.desc(), StudentSkillMastery.id.desc())
        .first()
    )
    mastery_en = mastery_row.status_en if mastery_row else "Weak"
    mastery_si = mastery_row.status_si if mastery_row else "දුර්වලයි"

    slide_payload = []
    for s in slides:
        activity_payload = None
        if s.activity_json:
            try:
                parsed_activity = json.loads(s.activity_json)
                if isinstance(parsed_activity, dict):
                    activity_payload = parsed_activity
            except (TypeError, ValueError, json.JSONDecodeError):
                activity_payload = None

        slide_content_type = (s.slide_type or "").strip().lower()
        image_grid_images = parse_image_grid_activity(s.activity_json)
        print(f"[student_lesson] slide.content_type={slide_content_type}")
        print(f"[student_lesson] slide.activity_json={s.activity_json or ''}")
        if slide_content_type == "image_grid" or (activity_payload or {}).get("type") == "image_grid":
            print(f"[student_lesson] parsed image_grid images count={len(image_grid_images)}")

        slide_payload.append({
            "id": s.id,
            "slide_order": s.slide_order,
            "slide_type": s.slide_type,
            "content_type": slide_content_type,
            "title": (s.title_si if is_si else s.title_en) or "",
            "content": (s.content_si if is_si else s.content_en) or "",
            "image_url": s.image_url or "",
            "video_url": s.video_url or "",
            "activity_json": s.activity_json or "",
            "activity": activity_payload,
            "image_grid_images": image_grid_images,
        })

    inner_html = f"""
    <style>.lesson-player-card{{margin-top:16px;background:#fff;border-radius:18px;padding:20px;box-shadow:0 8px 24px rgba(15,23,42,.08);position:relative}}.lesson-meta p{{margin:4px 0;color:#64748b}}.lesson-progress-line{{height:8px;background:#e2e8f0;border-radius:999px;overflow:hidden;margin:10px 0 16px}}.lesson-progress-line span{{display:block;height:100%;background:linear-gradient(90deg,#2563eb,#14b8a6)}}.slide-stage{{border:1px solid #e2e8f0;border-radius:14px;padding:18px;min-height:280px;background:#f8fafc}}.slide-pill{{display:inline-block;padding:4px 10px;border-radius:999px;background:#dbeafe;color:#1d4ed8;font-size:12px;font-weight:700;margin-bottom:10px}}.slide-content{{white-space:pre-wrap;line-height:1.7;color:#0f172a}}.slide-media{{max-width:100%;border-radius:10px;margin-top:12px}}.slide-video{{width:100%;max-width:840px;aspect-ratio:16/9;border:0;border-radius:12px;margin-top:12px}}.image-grid-gallery{{display:flex;justify-content:center;align-items:flex-start;gap:26px;margin-top:22px;flex-wrap:wrap}}.image-grid-card{{width:170px;background:transparent;border:none;box-shadow:none;padding:0;text-align:center;transition:all .25s ease}}.image-grid-card:hover{{transform:translateY(-4px)}}.image-grid-card img{{width:100%;max-height:175px;object-fit:contain;background:transparent;border-radius:0;display:block;margin:0 auto}}.image-grid-caption{{margin:10px 0 0;color:#334155;font-weight:600;text-align:center;line-height:1.35;font-size:16px}}@media (max-width:900px){{.image-grid-gallery{{gap:22px}}}}@media (max-width:640px){{.image-grid-gallery{{gap:18px}}.image-grid-card{{width:160px}}}}.lesson-dots{{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}}.lesson-dot{{width:10px;height:10px;border-radius:999px;background:#cbd5e1;border:none;cursor:pointer}}.lesson-dot.active{{background:#2563eb}}.lesson-dot.completed{{background:#22c55e}}.lesson-nav{{display:flex;justify-content:space-between;margin-top:16px;gap:10px}}.lesson-btn{{border:none;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer}}.lesson-btn.prev{{background:#e2e8f0;color:#0f172a}}.lesson-btn.next{{background:#2563eb;color:#fff}}.xp-panel{{margin-top:16px;padding:16px;border-radius:12px;background:linear-gradient(135deg,#052e16,#166534);color:#dcfce7;display:none}}.activity-wrap{{margin-top:18px;padding:18px;border-radius:16px;background:linear-gradient(180deg,#f8fbff,#f0f9ff);border:1px solid #dbeafe}}.activity-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin-top:16px}}.activity-card{{appearance:none;-webkit-appearance:none;width:100%;border:2px solid #e2e8f0;background:#ffffff;border-radius:18px;padding:18px 14px;min-height:120px;cursor:pointer;box-shadow:0 8px 20px rgba(15,23,42,.08);transition:all .2s ease;font-weight:700;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;text-align:center;color:#0f172a}}.activity-card:hover{{transform:translateY(-3px);border-color:#93c5fd}}.activity-card.selected{{border-color:#2563eb;background:#eff6ff}}.activity-card.correct{{border-color:#22c55e;background:#dcfce7}}.activity-card.wrong,.activity-card.missing{{border-color:#ef4444;background:#fee2e2}}.selected-answer{{border:2px solid #2563eb !important;background:#dbeafe !important;transform:translateY(-2px)}}.correct-answer{{border:2px solid #16a34a !important;background:#dcfce7 !important}}.wrong-answer{{border:2px solid #dc2626 !important;background:#fee2e2 !important}}.activity-card:disabled{{opacity:1;cursor:default}}.activity-thumb{{width:62px;height:62px;object-fit:cover;border-radius:14px;margin-bottom:10px;box-shadow:0 6px 16px rgba(15,23,42,.12)}}.activity-emoji{{display:block;font-size:42px;line-height:1;margin-bottom:10px}}.activity-name{{display:block;font-size:16px;line-height:1.35}}.activity-actions{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:16px}}.activity-check-btn{{margin-top:16px;border:none;border-radius:14px;background:linear-gradient(135deg,#2563eb,#4f46e5);color:white;font-weight:800;padding:12px 22px;cursor:pointer}}.activity-result{{font-weight:700}}.activity-result.success{{color:#166534}}.activity-result.fail{{color:#991b1b}}@media (max-width:640px){{.activity-grid{{grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}}.activity-card{{min-height:108px;padding:16px 12px}}.activity-name{{font-size:15px}}}}.tap-picture-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-top:18px}}.tap-picture-card{{position:relative;min-height:180px;padding:12px;border-radius:22px;border:3px solid transparent;background:rgba(255,255,255,.82);box-shadow:0 14px 32px rgba(15,23,42,.12);cursor:pointer;transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease;overflow:hidden}}.tap-picture-card:hover{{transform:translateY(-4px);box-shadow:0 18px 38px rgba(15,23,42,.16)}}.tap-picture-card img{{width:100%;height:170px;object-fit:cover;border-radius:16px;display:block}}.tap-picture-card.selected-correct{{border-color:#22c55e;background:#ecfdf5}}.tap-picture-card.selected-wrong{{border-color:#ef4444;background:#fef2f2;animation:tapShake .28s linear}}.tap-picture-check{{position:absolute;top:12px;right:12px;width:32px;height:32px;border-radius:999px;background:#22c55e;color:#fff;display:none;align-items:center;justify-content:center;font-weight:900;box-shadow:0 8px 20px rgba(34,197,94,.35)}}.tap-picture-card.selected-correct .tap-picture-check{{display:flex}}@keyframes tapShake{{0%,100%{{transform:translateX(0)}}25%{{transform:translateX(-5px)}}75%{{transform:translateX(5px)}}}}@media(max-width:900px){{.tap-picture-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:560px){{.tap-picture-grid{{grid-template-columns:1fr}}.tap-picture-card img{{height:210px}}}}.ai-helper-card{{position:fixed;right:22px;bottom:22px;background:#fff;border:1px solid #dbeafe;border-radius:14px;padding:12px;box-shadow:0 12px 30px rgba(15,23,42,.14);max-width:290px;z-index:30;display:none}}.ai-helper-close{{position:absolute;top:8px;right:10px;border:none;background:transparent;font-size:22px;font-weight:800;cursor:pointer;color:#64748b}}.ai-helper-actions{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}}.ai-helper-btn{{border:1px solid #bfdbfe;border-radius:10px;padding:8px;background:#eff6ff;color:#1e40af;font-weight:700;cursor:pointer}}.ai-helper-panel{{margin-top:8px;background:#f8fafc;border-radius:10px;padding:8px;font-size:13px}}</style>
    <section class='lesson-player-card'><div class='lesson-meta'><h1>{escape(lesson_title)}</h1><p><strong>{'Chapter' if not is_si else 'පරිච්ඡේදය'}:</strong> {escape(chapter_name)}</p><p>{escape(context_label)}</p><p><strong>{'Mastery' if not is_si else 'දක්ෂතා මට්ටම'}:</strong> <span id='masteryBadge' class='slide-pill'>{escape(mastery_si if is_si else mastery_en)}</span></p><p id='completionText'>Completion: {int(progress.completion_percent)}%</p><div class='lesson-progress-line'><span id='completionBar' style='width:{int(progress.completion_percent)}%'></span></div></div><div class='slide-stage'><div class='slide-pill' id='slideTypePill'></div><h2 id='slideTitle'></h2><div class='slide-content' id='slideContent'></div><div id='slideMediaWrap'></div></div><div class='lesson-dots' id='progressDots'></div><div id='nextLessonPanel' style='display:none;margin-top:14px;'></div><div class='lesson-nav'><button type='button' class='lesson-btn prev' id='prevSlideBtn'>Previous</button><button type='button' class='lesson-btn next' id='finishLessonBtn'>Next</button></div><div class='xp-panel' id='xpPanel'><h3 style='margin:0 0 6px;'>🎉 Lesson Completed!</h3><p style='margin:0;'>You earned <strong>{lesson.xp_reward} XP</strong>.</p></div></section><aside class='ai-helper-card' id='aiHelperCard'><button type='button' class='ai-helper-close' id='aiHelperClose'>×</button><strong>🤖 AI Study Assistant</strong><div class='ai-helper-actions'><button class='ai-helper-btn' data-ai-action='hint'>Hint</button><button class='ai-helper-btn' data-ai-action='explain'>Explain</button><button class='ai-helper-btn' data-ai-action='example'>Show Example</button><button class='ai-helper-btn' data-ai-action='video'>Watch Teacher Clip</button></div><div class='ai-helper-panel' id='aiHelperPanel'></div></aside>
    <script>
      const lessonId = {lesson.id}; const slides = {json.dumps(slide_payload)}; const isSinhala = {str(is_si).lower()}; let currentIndex = Math.max(0, slides.findIndex((s)=>s.slide_order === {int(progress.current_slide_order)})); const solvedQuizSlides = new Set(); let slideStartedAt = Date.now();
      function normalizeYouTube(url) {{ if (!url) return ""; const v = String(url).trim(); return v.includes("youtube.com/embed/") ? v : (v.includes("watch?v=") ? v.replace("watch?v=", "embed/") : v); }}
      async function saveProgress(forceComplete=false) {{ const current = slides[currentIndex]; const completion = forceComplete ? 100 : Math.round(((currentIndex + 1) / slides.length) * 100); const isCompleted = forceComplete || currentIndex >= slides.length - 1; const res = await fetch(`/student/lesson/${{lessonId}}/progress`, {{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{current_slide_order:current.slide_order,completion_percent:completion,is_completed:isCompleted}})}}); return await res.json().catch(()=>null); }}
      function parseActivityJson(rawJson) {{ if (!rawJson) return null; if (typeof rawJson === "object" && !Array.isArray(rawJson)) return rawJson; if (typeof rawJson !== "string") return null; try {{ const parsed = JSON.parse(rawJson); return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null; }} catch (err) {{ console.warn("Could not parse slide.activity_json", err); return null; }} }}
      function getImageGridImages(slide) {{
        if (Array.isArray(slide?.image_grid_images) && slide.image_grid_images.length) {{
          return slide.image_grid_images.filter(item => item && item.url);
        }}

        if (slide?.activity && Array.isArray(slide.activity.images)) {{
          return slide.activity.images.filter(item => item && item.url);
        }}

        const parsedActivity = parseActivityJson(slide?.activity_json);
        if (parsedActivity && Array.isArray(parsedActivity.images)) {{
          return parsedActivity.images.filter(item => item && item.url);
        }}

        return [];
      }}
      function escapeHtml(value) {{
        return String(value || "").replace(/[&<>'"]/g, function(ch) {{
          return ({{
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            "'": "&#39;",
            '"': "&quot;"
          }})[ch] || ch;
        }});
      }}
      function renderImageGrid(images) {{ const safeImages = Array.isArray(images) ? images.filter((item)=>item && item.url) : []; if (!safeImages.length) return ""; const cards = safeImages.map((item, idx) => {{ const caption = isSinhala ? (item.caption_si || item.caption_en || "") : (item.caption_en || item.caption_si || ""); const alt = caption || (isSinhala ? `රූපය ${{idx + 1}}` : `Image ${{idx + 1}}`); const captionHtml = caption ? `<figcaption class="image-grid-caption">${{escapeHtml(caption)}}</figcaption>` : ""; return `<figure class="image-grid-card"><img src="${{escapeHtml(item.url)}}" alt="${{escapeHtml(alt)}}" loading="lazy">${{captionHtml}}</figure>`; }}).join(""); return `<div class="image-grid-gallery" aria-label="${{isSinhala ? "රූප ගැලරිය" : "Image gallery"}}">${{cards}}</div>`; }}
      function render_activity_slide(activityData) {{ if (!activityData || typeof activityData !== "object") return ""; const activityType = String(activityData.type || activityData.activity_type || "").trim().toLowerCase(); const activityTypeMap = {{"matching_pairs":"mcq","drag_drop_group":"mcq"}}; const normalizedActivityType = activityTypeMap[activityType] || activityType; const questionTitle = isSinhala ? (activityData.question_si || activityData.question_en || "Activity") : (activityData.question_en || activityData.question_si || "Activity"); if (normalizedActivityType === "tap_correct_picture") {{ const items = Array.isArray(activityData.items) ? activityData.items : []; if (!items.length) return ""; const title = activityData.title || activityData.question_si || activityData.question_en || questionTitle; const instruction = activityData.instruction || ""; const cards = items.map((item, idx)=>{{ const alt = item.alt || item.name_si || item.name_en || item.name || `${{isSinhala ? "රූපය" : "Picture"}} ${{idx + 1}}`; if (!item.image_url) return ""; return `<button type="button" class="tap-picture-card" data-item-index="${{idx}}" data-correct="${{Boolean(item.correct)}}" aria-label="${{escapeHtml(alt)}}"><img src="${{escapeHtml(item.image_url)}}" alt="${{escapeHtml(alt)}}" loading="lazy"><span class="tap-picture-check">✓</span></button>`; }}).join(""); return `<div class="activity-wrap" data-activity-type="tap_correct_picture"><h3 class="activity-question">${{escapeHtml(title)}}</h3>${{instruction ? `<p class="slide-content">${{escapeHtml(instruction)}}</p>` : ""}}<div class="tap-picture-grid">${{cards}}</div><div class="activity-actions"><div class="activity-result" id="activityResult" style="display:none;"></div></div></div>`; }} if (normalizedActivityType === "mcq") {{ const options = Array.isArray(activityData.options) ? activityData.options : []; if (!options.length) return `<div class="activity-wrap"><h3 class="activity-question">${{questionTitle}}</h3><p>Invalid quiz configuration.</p></div>`; const optionCards = options.slice(0, 4).map((option, idx)=>{{ const label = isSinhala ? (option.text_si || option.text || option.text_en || `Option ${{idx + 1}}`) : (option.text_en || option.text || option.text_si || `Option ${{idx + 1}}`); const icon = option.emoji || option.icon || ["🅰️","🅱️","🅲","🅳"][idx] || "🧠"; return `<button type="button" class="activity-card mcq-option" data-option-index="${{idx}}" data-correct="${{String(option.correct || "").toLowerCase() === "true" || String(activityData.correct_answer || "").trim().toLowerCase() === String(option.value || option.key || option.text || option.text_en || option.text_si || "").trim().toLowerCase()}}" data-option-label="${{label.replaceAll('"', '&quot;')}}" data-option-value="${{String(option.value || option.key || option.text || option.text_en || option.text_si || "").replaceAll('"', '&quot;')}}"><span class="activity-emoji">${{icon}}</span><span class="activity-name">${{label}}</span></button>`; }}).join(""); return `<div class="activity-wrap premium-quiz" data-activity-type="mcq"><h3 class="activity-question">${{questionTitle}}</h3><div class="activity-grid">${{optionCards}}</div><p class="slide-content" id="activityExplanation" style="display:none;margin-top:12px;"></p><div class="activity-actions"><button type="button" class="activity-check-btn" id="tryAgainBtn" style="display:none;">${{isSinhala ? "නැවත උත්සාහ කරන්න" : "Try Again"}}</button><div class="activity-result" id="activityResult" style="display:none;"></div></div></div>`; }} if (normalizedActivityType === "fill_blank") {{ return `<div class="activity-wrap premium-quiz" data-activity-type="fill_blank"><h3 class="activity-question">${{questionTitle}}</h3><input type="text" class="activity-input" id="fillBlankAnswerInput" autocomplete="off" placeholder="${{isSinhala ? "ඔබේ පිළිතුර ලියන්න" : "Type your answer"}}"><p class="slide-content" id="activityExplanation" style="display:none;margin-top:12px;"></p><div class="activity-actions"><button type="button" class="activity-check-btn" id="checkFillBlankBtn">${{isSinhala ? "පිළිතුර පරීක්ෂා කරන්න" : "Check Answer"}}</button><button type="button" class="activity-check-btn" id="tryAgainBtn" style="display:none;">${{isSinhala ? "නැවත උත්සාහ කරන්න" : "Try Again"}}</button><div class="activity-result" id="activityResult" style="display:none;"></div></div></div>`; }} if (["drag_drop","matching","ordering"].includes(activityType)) return `<div class="activity-wrap"><h3 class="activity-question">${{questionTitle}}</h3><p>Activity type <strong>${{activityType}}</strong> is coming soon.</p></div>`; return ""; }}
      function enableFinishLessonButton() {{ const finishBtn = document.getElementById("finishLessonBtn"); if (finishBtn) {{ finishBtn.disabled = false; finishBtn.classList.remove("disabled"); }} }}
      function wireTapCorrectPictureInteraction(mediaWrap) {{ const cards = mediaWrap.querySelectorAll(".tap-picture-card"); const resultBox = mediaWrap.querySelector("#activityResult"); const current = slides[currentIndex]; const nextBtn = document.getElementById("finishLessonBtn"); const successMessage = current.activity?.success_message || (isSinhala ? "සුභ පැතුම්! ඔබ නිවැරදි පින්තූර තෝරා ඇත." : "Great job! You selected the correct pictures."); const wrongMessage = current.activity?.wrong_message || (isSinhala ? "නැවත උත්සාහ කරන්න." : "Try again."); function selectedCorrectNames() {{ return [...cards].filter(card => card.classList.contains("selected-correct")).map(card => card.dataset.itemIndex || ""); }} function isComplete() {{ return [...cards].every(card => card.dataset.correct !== "true" || card.classList.contains("selected-correct")); }} function showResult(ok, text) {{ if (!resultBox) return; resultBox.style.display = "inline-block"; resultBox.className = `activity-result ${{ok ? "success" : "fail"}}`; resultBox.textContent = text; }} async function completeIfReady() {{ if (!isComplete()) return; solvedQuizSlides.add(current.id); enableFinishLessonButton(); showResult(true, successMessage); await recordLessonAnswer(current.id, JSON.stringify(selectedCorrectNames()), true); }} if (nextBtn && !solvedQuizSlides.has(current.id)) nextBtn.disabled = true; cards.forEach((card) => {{ card.addEventListener("click", async () => {{ const isCorrect = card.dataset.correct === "true"; if (isCorrect) {{ card.classList.add("selected-correct"); card.disabled = true; await completeIfReady(); }} else {{ card.classList.add("selected-wrong"); showResult(false, wrongMessage); solvedQuizSlides.delete(current.id); if (nextBtn) nextBtn.disabled = true; await recordLessonAnswer(current.id, `wrong:${{card.dataset.itemIndex || ""}}`, false); maybeShowAiAssistant(true); window.setTimeout(() => {{ card.classList.remove("selected-wrong"); if (resultBox && resultBox.classList.contains("fail")) resultBox.style.display = "none"; }}, 1000); }} }}); }}); }}
            async function recordLessonAnswer(slideId, selectedAnswer, isCorrect) {{ const activity = slides[currentIndex]?.activity || null; const response = await fetch(`/student/lesson/${{lessonId}}/answer`, {{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{slide_id:slideId,selected_answer:selectedAnswer,is_correct:isCorrect,activity_json:activity,time_spent_seconds: Math.max(1, Math.round((Date.now()-slideStartedAt)/1000))}})}}); const data = await response.json().catch(()=>null); window.lastAiAssistPayload = data && data.ai_assist ? data.ai_assist : null; if (data && data.ok && data.mastery_status) {{ const badge = document.getElementById("masteryBadge"); if (badge) badge.textContent = isSinhala ? (data.mastery_status_si || data.mastery_status) : data.mastery_status; }} }}
      function normalizeEnglishAnswer(value) {{ return String(value || "").trim().toLowerCase(); }}
      function normalizeSinhalaAnswer(value) {{ return String(value || "").trim(); }}
      function wireFillBlankInteraction(mediaWrap) {{ const input = mediaWrap.querySelector("#fillBlankAnswerInput"); const checkBtn = mediaWrap.querySelector("#checkFillBlankBtn"); const tryAgainBtn = mediaWrap.querySelector("#tryAgainBtn"); const resultBox = mediaWrap.querySelector("#activityResult"); const explainEl = mediaWrap.querySelector("#activityExplanation"); const nextBtn = document.getElementById("finishLessonBtn"); const current = slides[currentIndex]; if (!input || !checkBtn) return; const acceptable = Array.isArray(current.activity?.acceptable_answers) ? current.activity.acceptable_answers : []; const primaryAnswers = [current.activity?.answer_en, current.activity?.answer_si].filter((item)=>typeof item === "string" && item.trim()); const allAnswers = [...acceptable, ...primaryAnswers].filter((item)=>typeof item === "string" && item.trim()); const normalized = new Set(allAnswers.map((item)=>isSinhala ? normalizeSinhalaAnswer(item) : normalizeEnglishAnswer(item))); const explanation = isSinhala ? (current.activity?.explanation_si || current.activity?.explanation_en || "") : (current.activity?.explanation_en || current.activity?.explanation_si || ""); const resetState = () => {{ input.classList.remove("correct", "wrong"); if (resultBox) resultBox.style.display = "none"; if (explainEl) explainEl.style.display = "none"; checkBtn.style.display = "inline-block"; if (tryAgainBtn) tryAgainBtn.style.display = "none"; if (nextBtn) nextBtn.disabled = !solvedQuizSlides.has(current.id); }}; checkBtn.addEventListener("click", async () => {{ const rawValue = String(input.value || ""); const trimmed = rawValue.trim(); const normalizedStudent = isSinhala ? normalizeSinhalaAnswer(trimmed) : normalizeEnglishAnswer(trimmed); const isCorrect = trimmed.length > 0 && normalized.has(normalizedStudent); input.classList.remove("correct", "wrong"); input.classList.add(isCorrect ? "correct" : "wrong"); if (resultBox) {{ resultBox.style.display = "inline-block"; resultBox.className = `activity-result ${{isCorrect ? "success" : "fail"}}`; resultBox.textContent = isCorrect ? (isSinhala ? "ශබාශ! නිවැරදියි 🎉" : "Great job! Correct 🎉") : (isSinhala ? "වැරදියි. නැවත උත්සාහ කරන්න." : "Not quite. Try again."); }} if (isCorrect) {{ solvedQuizSlides.add(current.id); enableFinishLessonButton(); checkBtn.style.display = "none"; if (tryAgainBtn) tryAgainBtn.style.display = "none"; if (nextBtn) nextBtn.disabled = false; if (explainEl && explanation) {{ explainEl.textContent = explanation; explainEl.style.display = "block"; }} }} else {{ solvedQuizSlides.delete(current.id); if (tryAgainBtn) tryAgainBtn.style.display = "inline-block"; if (nextBtn) nextBtn.disabled = true; }} await recordLessonAnswer(current.id, trimmed, isCorrect); maybeShowAiAssistant(!isCorrect); }}); if (tryAgainBtn) tryAgainBtn.addEventListener("click", resetState); if (nextBtn) nextBtn.disabled = !solvedQuizSlides.has(current.id); }}
      function wireMcqInteraction(mediaWrap) {{ const cards = mediaWrap.querySelectorAll(".mcq-option"); const resultBox = mediaWrap.querySelector("#activityResult"); const explainEl = mediaWrap.querySelector("#activityExplanation"); const current = slides[currentIndex]; const explanation = isSinhala ? (current.activity?.explanation_si || current.activity?.explanation_en || "") : (current.activity?.explanation_en || current.activity?.explanation_si || ""); let locked = false; cards.forEach((card) => {{ card.addEventListener("click", async () => {{ if (locked) return; cards.forEach((item)=>item.classList.remove("selected-answer","correct-answer","wrong-answer")); card.classList.add("selected-answer"); locked = true; cards.forEach((item)=>item.disabled = true); const isCorrect = card.dataset.correct === "true"; card.classList.add(isCorrect ? "correct-answer" : "wrong-answer"); cards.forEach((item)=>{{ if (item.dataset.correct === "true") item.classList.add("correct-answer"); }}); if (resultBox) {{ resultBox.style.display = "inline-block"; resultBox.className = `activity-result ${{isCorrect ? "success" : "fail"}}`; resultBox.textContent = isCorrect ? (isSinhala ? "ශබාශ! නිවැරදියි 🎉" : "Great job! Correct 🎉") : (isSinhala ? "වැරදියි." : "Not quite."); }} if (explainEl && explanation) {{ explainEl.textContent = explanation; explainEl.style.display = "block"; }} await recordLessonAnswer(current.id, card.dataset.optionValue || card.dataset.optionLabel || "", isCorrect); maybeShowAiAssistant(!isCorrect); if (isCorrect) {{ solvedQuizSlides.add(current.id); enableFinishLessonButton(); }} }}); }}); }}
      function maybeShowAiAssistant(shouldOpen=false) {{ const payload = window.lastAiAssistPayload || null; const card = document.getElementById("aiHelperCard"); if (!card || !payload) return; const panel = document.getElementById("aiHelperPanel"); if (panel && payload.message) panel.textContent = payload.message; if (shouldOpen && payload.show) card.style.display = "block"; }} function renderSlide() {{ slideStartedAt = Date.now(); window.lastAiAssistPayload = null; const current = slides[currentIndex]; document.getElementById("slideTypePill").textContent = current.slide_type.replaceAll("_", " "); document.getElementById("slideTitle").textContent = current.title || "Slide"; document.getElementById("slideContent").textContent = current.content || ""; const pct = Math.round(((currentIndex + 1) / slides.length) * 100); document.getElementById("completionText").textContent = `Completion: ${{pct}}%`; document.getElementById("completionBar").style.width = `${{pct}}%`; const mediaWrap = document.getElementById("slideMediaWrap"); mediaWrap.innerHTML = ""; const contentType = String(current.content_type || current.slide_type || current.activity?.type || "").trim().toLowerCase(); if (contentType === "intro_video" && current.video_url) {{ const iframe = document.createElement("iframe"); iframe.className = "slide-video"; iframe.src = normalizeYouTube(current.video_url); iframe.allowFullscreen = true; mediaWrap.appendChild(iframe); }} else if (contentType === "image_grid" || String(current.activity?.type || current.activity?.activity_type || "").trim().toLowerCase() === "image_grid") {{ console.log("IMAGE GRID CURRENT SLIDE:", current); const imageGridImages = getImageGridImages(current); console.log("IMAGE GRID IMAGES:", imageGridImages); const gridHtml = renderImageGrid(imageGridImages); if (gridHtml) {{ mediaWrap.insertAdjacentHTML("beforeend", gridHtml); }} else {{ mediaWrap.textContent = "No image_grid images found for this slide."; }} }} else if (current.image_url) {{ const image = document.createElement("img"); image.className = "slide-media"; image.src = current.image_url; mediaWrap.appendChild(image); }} const activityHtml = render_activity_slide(current.activity); if (activityHtml) {{ mediaWrap.insertAdjacentHTML("beforeend", activityHtml); const activityType = String(current.activity?.type || current.activity?.activity_type || "").toLowerCase(); const activityTypeMap = {{"matching_pairs":"mcq","drag_drop_group":"mcq"}}; const normalizedType = activityTypeMap[activityType] || activityType; if (normalizedType === "tap_correct_picture") wireTapCorrectPictureInteraction(mediaWrap); if (normalizedType === "mcq") wireMcqInteraction(mediaWrap); if (normalizedType === "fill_blank") wireFillBlankInteraction(mediaWrap); }} document.getElementById("progressDots").innerHTML = slides.map((s, i)=>`<button type='button' class="lesson-dot ${{i < currentIndex ? "completed" : ""}} ${{i === currentIndex ? "active" : ""}}" data-dot-index="${{i}}"></button>`).join(""); document.querySelectorAll("#progressDots .lesson-dot").forEach((dot)=>dot.addEventListener("click", ()=>{{ currentIndex = Number(dot.dataset.dotIndex || 0); renderSlide(); }})); document.getElementById("prevSlideBtn").disabled = currentIndex === 0; document.getElementById("finishLessonBtn").textContent = currentIndex === slides.length - 1 ? "Finish" : "Next"; const activityType = String(current.activity?.type || current.activity?.activity_type || "").toLowerCase(); const activityTypeMap = {{"matching_pairs":"mcq","drag_drop_group":"mcq"}}; const normalizedType2 = activityTypeMap[activityType] || activityType; const requiresCorrect = (String(current.slide_type || "").toLowerCase() === "quiz" && (normalizedType2 === "mcq" || normalizedType2 === "fill_blank")) || normalizedType2 === "tap_correct_picture"; document.getElementById("finishLessonBtn").disabled = requiresCorrect && !solvedQuizSlides.has(current.id); document.getElementById("xpPanel").style.display = currentIndex === slides.length - 1 ? "block" : "none"; }}
      document.getElementById("prevSlideBtn").addEventListener("click", ()=>{{ if (currentIndex > 0) {{ currentIndex--; renderSlide(); }} }});
      const finishBtn = document.getElementById("finishLessonBtn");
      finishBtn.addEventListener("click", async () => {{
        if (currentIndex < slides.length - 1) {{
          await saveProgress(false);
          currentIndex++;
          renderSlide();
          return;
        }}

        finishBtn.disabled = true;
        finishBtn.textContent = isSinhala ? "සම්පූර්ණ කරමින්..." : "Finishing...";

        try {{
          const res = await fetch("/student/lesson/" + lessonId + "/finish", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}}
          }});

          const data = await res.json();

          if (!res.ok || !data.success) {{
            throw new Error(data.error || "Finish failed");
          }}

          if (data.redirect_url) {{
            window.location.href = data.redirect_url;
          }}
        }} catch (err) {{
          console.error("Finish lesson failed:", err);
          alert(isSinhala ? "පාඩම අවසන් කිරීමේ දෝෂයක් ඇත." : "Could not finish lesson.");
          finishBtn.disabled = false;
          finishBtn.textContent = "Finish";
        }}
      }});
      const aiClose = document.getElementById("aiHelperClose"); const aiCard = document.getElementById("aiHelperCard"); aiClose?.addEventListener("click", () => {{ aiCard.style.display = "none"; }}); document.querySelectorAll(".ai-helper-btn").forEach((btn)=>btn.addEventListener("click", async ()=>{{ const t=btn.dataset.aiAction||"hint"; const slide = slides[currentIndex]; const res=await fetch("/student/lesson/" + lessonId + "/ai-assist",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{slide_id:slide?.id,assistance_type:t}})}}); const data=await res.json().catch(()=>null); const panel=document.getElementById("aiHelperPanel"); if(panel) panel.textContent=(data&&data.text)?data.text:"Let's keep trying together."; if (aiCard) aiCard.style.display = "block"; }}));
      renderSlide();
    </script>"""
    return render_student_dashboard_shell(inner_html, active_nav="my_subjects")


@app.route("/student/lesson/<int:lesson_id>/finish", methods=["POST"])
def student_lesson_finish(lesson_id: int):
    try:
        student = get_current_student_for_json()
        if not student:
            return jsonify({"success": False, "error": "Student session expired"}), 401
        ensure_lesson_engine_tables()
        print("FINISH LESSON:", lesson_id, "student:", student.id)
        lesson = Lesson.query.filter_by(id=lesson_id, is_active=True).first()
        if not lesson:
            return jsonify({"success": False, "error": "Lesson not found"}), 404

        progress = StudentLessonProgress.query.filter_by(student_id=student.id, lesson_id=lesson.id).first()
        if not progress:
            first_slide = (
                LessonSlide.query.filter_by(lesson_id=lesson.id, is_active=True)
                .order_by(LessonSlide.slide_order.asc(), LessonSlide.id.asc())
                .first()
            )
            progress = StudentLessonProgress(
                student_id=student.id,
                lesson_id=lesson.id,
                current_slide_order=first_slide.slide_order if first_slide else 1,
            )
            db.session.add(progress)

        progress.completion_percent = 100
        progress.is_completed = True
        progress.completed_at = datetime.utcnow()
        progress.last_opened_at = datetime.utcnow()

        recalculate_student_chapter_progress(student.id, lesson.chapter_id)

        next_lesson = (
            Lesson.query.filter(
                Lesson.chapter_id == lesson.chapter_id,
                Lesson.is_active.is_(True),
                Lesson.lesson_order > lesson.lesson_order,
            )
            .order_by(Lesson.lesson_order.asc(), Lesson.id.asc())
            .first()
        )
        if next_lesson:
            redirect_url = "/student/lesson/" + str(next_lesson.id)
        else:
            chapter = db.session.get(SyllabusChapter, lesson.chapter_id)
            module = db.session.get(SyllabusModule, chapter.module_id) if chapter else None
            term = db.session.get(SyllabusTerm, module.term_id) if module else None
            subject = SubjectMaster.query.filter_by(subject_name_en=term.subject).first() if term else None
            if module and subject:
                redirect_url = "/student/subject/" + str(subject.id) + "/module/" + str(module.id)
            else:
                redirect_url = "/student/my-subjects"

        db.session.commit()
        return jsonify({"success": True, "redirect_url": redirect_url})
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/student/lesson/<int:lesson_id>/progress", methods=["POST"])
def student_lesson_progress_update(lesson_id: int):
    student = get_current_student_for_json()
    if not student:
        return jsonify({"success": False, "error": "Student session expired"}), 401
    ensure_lesson_engine_tables()
    lesson = Lesson.query.filter_by(id=lesson_id, is_active=True).first()
    if not lesson:
        return jsonify({"success": False, "ok": False, "error": "Lesson not found"}), 404

    payload = request.get_json(silent=True) or {}
    current_slide_order = int(payload.get("current_slide_order") or 1)
    completion_percent = max(0, min(100, float(payload.get("completion_percent") or 0)))
    is_completed = bool(payload.get("is_completed"))

    progress = StudentLessonProgress.query.filter_by(student_id=student.id, lesson_id=lesson.id).first()
    if not progress:
        progress = StudentLessonProgress(student_id=student.id, lesson_id=lesson.id)
        db.session.add(progress)

    progress.current_slide_order = current_slide_order
    progress.completion_percent = completion_percent
    progress.is_completed = is_completed
    progress.last_opened_at = datetime.utcnow()
    progress.completed_at = datetime.utcnow() if is_completed else None
    if is_completed:
        progress.completion_percent = 100

    recalculate_student_chapter_progress(student.id, lesson.chapter_id)
    db.session.commit()
    next_lesson = find_next_lesson(lesson) if progress.is_completed else None
    redirect_url = None
    if progress.is_completed:
        if next_lesson:
            redirect_url = url_for("student_lesson_page", lesson_id=next_lesson.id)
        else:
            chapter = db.session.get(SyllabusChapter, lesson.chapter_id)
            module = db.session.get(SyllabusModule, chapter.module_id) if chapter else None
            term = db.session.get(SyllabusTerm, module.term_id) if module else None
            subject = SubjectMaster.query.filter_by(subject_name_en=term.subject).first() if term else None
            if module and subject:
                redirect_url = url_for("student_subject_module_page", subject_id=subject.id, module_id=module.id)
    return jsonify({"ok": True, "completion_percent": progress.completion_percent, "is_completed": progress.is_completed, "next_lesson_id": next_lesson.id if next_lesson else None, "next_lesson_title": next_lesson.lesson_title_en if next_lesson else None, "next_lesson_url": url_for("student_lesson_page", lesson_id=next_lesson.id) if next_lesson else None, "redirect_url": redirect_url, "xp_earned": int(lesson.xp_reward or 15)})


@app.route("/student/lesson/<int:lesson_id>/answer", methods=["POST"])
def student_lesson_answer_submit(lesson_id: int):
    student = get_current_student_for_json()
    if not student:
        return jsonify({"success": False, "error": "Student session expired"}), 401
    ensure_lesson_engine_tables()
    lesson = Lesson.query.filter_by(id=lesson_id, is_active=True).first()
    if not lesson:
        return jsonify({"success": False, "ok": False, "error": "Lesson not found"}), 404
    payload = request.get_json(silent=True) or {}
    slide_id = int(payload.get("slide_id") or 0)
    selected_answer = (payload.get("selected_answer") or "").strip()
    is_correct = bool(payload.get("is_correct"))
    activity_json = payload.get("activity_json")
    time_spent_seconds = max(0, int(payload.get("time_spent_seconds") or 0))
    if not slide_id or not selected_answer:
        return jsonify({"success": False, "ok": False, "error": "Invalid answer payload"}), 400
    db.session.add(StudentLessonAnswer(lesson_id=lesson.id, slide_id=slide_id, student_id=student.id, selected_answer=selected_answer, is_correct=is_correct, answered_at=datetime.utcnow()))
    mastery = update_student_skill_mastery(student.id, lesson.id, slide_id, is_correct, activity_json)
    wrong_attempts_on_slide = StudentLessonAnswer.query.filter_by(student_id=student.id, lesson_id=lesson.id, slide_id=slide_id, is_correct=False).count()
    mastery_score_now = float(mastery.mastery_score) if mastery else 100.0
    trigger_reason = None
    if wrong_attempts_on_slide >= 2:
        trigger_reason = "wrong_2_plus"
    elif time_spent_seconds >= 90:
        trigger_reason = "time_spent_too_long"
    elif mastery_score_now < 40:
        trigger_reason = "mastery_below_40"
    ai_assist_payload = {"show": False}
    if trigger_reason:
        ai_assist_payload = {
            "show": True,
            "reason": trigger_reason,
            "message": "Think about what the question is really asking. Step 1: find key words. පියවර 1: ප්‍රධාන වචන සොයන්න.",
            "recommendation": "Start easier practice now, then revision session."
        }
        db.session.add(
            StudentAiAssistanceLog(
                student_id=student.id,
                lesson_id=lesson.id,
                slide_id=slide_id,
                assistance_type="auto_prompt",
                triggered_reason=trigger_reason,
                created_at=datetime.utcnow(),
            )
        )
    if is_correct:
        student.xp = int(student.xp or 0) + 10
    recalculate_student_chapter_progress(student.id, lesson.chapter_id)
    db.session.commit()
    return jsonify({"ok": True, "mastery_status": mastery.status_en if mastery else None, "mastery_status_si": mastery.status_si if mastery else None, "ai_assist": ai_assist_payload})


@app.route("/student/lesson/<int:lesson_id>/ai-assist", methods=["POST"])
def student_lesson_ai_assist(lesson_id: int):
    student = get_current_student_for_json()
    if not student:
        return jsonify({"success": False, "error": "Student session expired"}), 401
    ensure_lesson_engine_tables()
    lesson = Lesson.query.filter_by(id=lesson_id, is_active=True).first()
    if not lesson:
        return jsonify({"success": False, "ok": False, "error": "Lesson not found"}), 404
    payload = request.get_json(silent=True) or {}
    slide_id = int(payload.get("slide_id") or 0)
    assistance_type = str(payload.get("assistance_type") or "hint").strip().lower() or "hint"
    if not slide_id:
        return jsonify({"success": False, "ok": False, "error": "Missing slide_id"}), 400
    db.session.add(StudentAiAssistanceLog(student_id=student.id, lesson_id=lesson.id, slide_id=slide_id, assistance_type=assistance_type, triggered_reason="manual_request", created_at=datetime.utcnow()))
    db.session.commit()
    content_map = {
        "hint": "Hint: Think about the important clue first. ඉඟිය: මුලින් ප්‍රධාන ඉඟිය සොයන්න.",
        "explain": "Explanation: Break the question into small parts and solve one by one. විස්තරය: ප්‍රශ්නය කොටස්වලට බෙදලා එකින් එක විසඳන්න.",
        "example": "Example: If something can roll, it is often circular. උදාහරණය: යමක් ගුරුල්ල ගත හැකි නම් ඒක බොහෝ විට වෘත්තීය.",
        "video": "Watch Teacher Explain is available when teacher_video_url is set in activity JSON.",
    }
    return jsonify({"ok": True, "text": content_map.get(assistance_type, content_map["hint"])})




def get_current_student_for_json():
    student_id = session.get("student_id")
    if not student_id:
        return None
    return db.session.get(Student, student_id)

def _parse_bool_form(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@app.route("/admin/lesson-builder", methods=["GET"])
def admin_lesson_builder():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    ensure_lesson_engine_tables()

    grade = normalize_grade((request.args.get("grade") or "").strip())
    subject_id_raw = (request.args.get("subject_id") or "").strip()
    module_id_raw = (request.args.get("module_id") or "").strip()
    chapter_id_raw = (request.args.get("chapter_id") or "").strip()
    subject_id = int(subject_id_raw) if subject_id_raw.isdigit() else None
    module_id = int(module_id_raw) if module_id_raw.isdigit() else None
    chapter_id = int(chapter_id_raw) if chapter_id_raw.isdigit() else None

    query = (
        db.session.query(Lesson, SyllabusChapter, SyllabusModule, SyllabusTerm, SubjectMaster)
        .join(SyllabusChapter, SyllabusChapter.id == Lesson.chapter_id)
        .join(SyllabusModule, SyllabusModule.id == SyllabusChapter.module_id)
        .join(SyllabusTerm, SyllabusTerm.id == SyllabusModule.term_id)
        .outerjoin(SubjectMaster, SubjectMaster.id == subject_id)
    )
    if grade:
        query = query.filter(SyllabusTerm.grade == grade)
    if module_id:
        query = query.filter(SyllabusModule.id == module_id)
    if chapter_id:
        query = query.filter(SyllabusChapter.id == chapter_id)
    lessons = query.order_by(SyllabusTerm.grade.asc(), SyllabusModule.module_order.asc(), SyllabusChapter.chapter_order.asc(), Lesson.lesson_order.asc()).all()

    subjects = get_subjects_for_grade(grade, active_only=True) if grade else []
    rows = "".join(
        f"<tr><td>{escape(term.grade)}</td><td>{escape(term.subject)}</td><td>{escape(module.module_name_en)}</td><td>{escape(chapter.chapter_name_en)}</td><td>{lesson.lesson_order}</td><td>{escape(lesson.lesson_title_en)}</td><td>{'Yes' if lesson.is_active else 'No'}</td><td><a href='/admin/lesson-builder/{lesson.id}/slides'>Manage Slides</a> | <a href='/student/lesson/{lesson.id}'>Preview Lesson</a></td></tr>"
        for lesson, chapter, module, term, _ in lessons
    )
    subject_options = "".join([f"<option value='{s.id}' {'selected' if subject_id == s.id else ''}>{escape(s.subject_name_en)}</option>" for s in subjects])
    return f"""
    <h1>Admin Lesson Builder</h1>
    <p><a href='/admin-dashboard'>Back</a> | <a href='/admin/lesson-builder/new'>Add Lesson</a></p>
    <form method='get'>
      <label>Grade <select name='grade'><option value=''>All</option>{grade_options_html(grade)}</select></label>
      <label>Subject <select name='subject_id'><option value=''>All</option>{subject_options}</select></label>
      <button type='submit'>Filter</button>
    </form>
    <table border='1' cellpadding='6'>
      <tr><th>Grade</th><th>Subject</th><th>Module</th><th>Chapter</th><th>Lesson Order</th><th>Lesson Title</th><th>Active</th><th>Actions</th></tr>
      {rows or '<tr><td colspan=8>No lessons found</td></tr>'}
    </table>
    """


@app.route("/admin/lesson-builder/new", methods=["GET", "POST"])
def admin_lesson_builder_new():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    ensure_lesson_engine_tables()
    if request.method == "POST":
        chapter_id = int((request.form.get("chapter_id") or "0"))
        lesson = Lesson(
            chapter_id=chapter_id,
            lesson_order=int((request.form.get("lesson_order") or "1")),
            lesson_title_en=(request.form.get("lesson_title_en") or "").strip() or "Untitled Lesson",
            lesson_title_si=(request.form.get("lesson_title_si") or "").strip() or "නම නොමැති පාඩම",
            lesson_type=(request.form.get("lesson_type") or "standard").strip() or "standard",
            thumbnail_url=(request.form.get("thumbnail_url") or "").strip() or None,
            estimated_minutes=int((request.form.get("estimated_minutes") or "10")),
            xp_reward=int((request.form.get("xp_reward") or "10")),
            is_active=_parse_bool_form(request.form.get("is_active"), True),
        )
        db.session.add(lesson)
        db.session.commit()
        return redirect("/admin/lesson-builder")

    active_subjects = SubjectMaster.query.filter_by(is_active=True).order_by(SubjectMaster.grade.asc(), SubjectMaster.subject_name_en.asc()).all()
    all_terms = SyllabusTerm.query.order_by(SyllabusTerm.grade.asc(), SyllabusTerm.term_number.asc(), SyllabusTerm.id.asc()).all()
    all_modules = SyllabusModule.query.order_by(SyllabusModule.term_id.asc(), SyllabusModule.module_order.asc(), SyllabusModule.id.asc()).all()
    all_chapters = SyllabusChapter.query.filter_by(is_active=True).order_by(SyllabusChapter.module_id.asc(), SyllabusChapter.chapter_order.asc(), SyllabusChapter.id.asc()).all()

    subject_options = "".join(
        f"<option value='{s.id}' data-grade='{escape(s.grade or '')}' data-keys='{escape('|'.join([k.lower() for k in [s.subject_code, s.subject_name_en, s.subject_name_si] if k]))}'>{escape(s.subject_name_en)} ({escape(s.subject_code or '-')})</option>"
        for s in active_subjects
    )
    term_options = "".join(
        f"<option value='{t.id}' data-grade='{escape(t.grade or '')}' data-subject='{escape((t.subject or '').lower())}'>Term {t.term_number} - {escape(t.term_name_en)}</option>"
        for t in all_terms
    )
    module_options = "".join(
        f"<option value='{m.id}' data-term-id='{m.term_id}'>M{m.module_order} - {escape(m.module_name_en)}</option>"
        for m in all_modules
    )
    chapter_options = "".join(
        f"<option value='{c.id}' data-module-id='{c.module_id}'>C{c.chapter_order} - {escape(c.chapter_name_en)}</option>"
        for c in all_chapters
    )

    return f"""
    <h1>Add Lesson</h1>
    <p><a href='/admin/lesson-builder'>Back to Lesson List</a></p>
    <form method='post'>
      <label>Grade <select name='grade' id='lesson-grade' required><option value=''>Select grade</option>{grade_options_html('')}</select></label><br><br>
      <label>Subject <select name='subject_id' id='lesson-subject' required><option value=''>Select subject</option>{subject_options}</select></label><br><br>
      <label>Term <select name='term_id' id='lesson-term' required><option value=''>Select term</option>{term_options}</select></label><br><br>
      <label>Module <select name='module_id' id='lesson-module' required><option value=''>Select module</option>{module_options}</select></label><br><br>
      <label>Chapter <select name='chapter_id' id='lesson-chapter' required><option value=''>Select chapter</option>{chapter_options}</select></label><br><br>
      <label>Lesson Order <input type='number' name='lesson_order' value='1' min='1' required></label><br><br>
      <label>Lesson Title (EN) <input type='text' name='lesson_title_en' required></label><br><br>
      <label>Lesson Title (SI) <input type='text' name='lesson_title_si' required></label><br><br>
      <label>Lesson Type <input type='text' name='lesson_type' value='standard' required></label><br><br>
      <label>Thumbnail URL <input type='url' name='thumbnail_url'></label><br><br>
      <label>Estimated Minutes <input type='number' name='estimated_minutes' value='10' min='1'></label><br><br>
      <label>XP Reward <input type='number' name='xp_reward' value='10' min='0'></label><br><br>
      <label><input type='checkbox' name='is_active' value='1' checked> Is Active</label><br><br>
      <button type='submit'>Save Lesson</button>
    </form>
    <script>
      (function () {{
        const gradeEl = document.getElementById('lesson-grade');
        const subjectEl = document.getElementById('lesson-subject');
        const termEl = document.getElementById('lesson-term');
        const moduleEl = document.getElementById('lesson-module');
        const chapterEl = document.getElementById('lesson-chapter');
        if (!gradeEl || !subjectEl || !termEl || !moduleEl || !chapterEl) return;

        const optionsFor = (el) => Array.from(el.querySelectorAll('option')).filter(opt => opt.value);
        const subjectOptions = optionsFor(subjectEl);
        const termOptions = optionsFor(termEl);
        const moduleOptions = optionsFor(moduleEl);
        const chapterOptions = optionsFor(chapterEl);

        const showOptions = (el, options, predicate, placeholder) => {{
          const current = el.value;
          el.innerHTML = `<option value="">${{placeholder}}</option>`;
          let keepCurrent = false;
          options.forEach(opt => {{
            if (!predicate(opt)) return;
            el.appendChild(opt);
            if (opt.value === current) keepCurrent = true;
          }});
          if (!keepCurrent) el.value = '';
        }};

        const filterSubjects = () => {{
          const grade = (gradeEl.value || '').trim();
          showOptions(subjectEl, subjectOptions, (opt) => !grade || opt.dataset.grade === grade, 'Select subject');
        }};

        const filterTerms = () => {{
          const grade = (gradeEl.value || '').trim();
          const selectedSubject = subjectEl.options[subjectEl.selectedIndex];
          const keys = ((selectedSubject?.dataset?.keys) || '').split('|').filter(Boolean);
          showOptions(termEl, termOptions, (opt) => {{
            if (grade && opt.dataset.grade !== grade) return false;
            if (keys.length === 0) return true;
            return keys.includes((opt.dataset.subject || '').toLowerCase());
          }}, 'Select term');
        }};

        const filterModules = () => {{
          const termId = termEl.value;
          showOptions(moduleEl, moduleOptions, (opt) => !termId || opt.dataset.termId === termId, 'Select module');
        }};

        const filterChapters = () => {{
          const moduleId = moduleEl.value;
          showOptions(chapterEl, chapterOptions, (opt) => !moduleId || opt.dataset.moduleId === moduleId, 'Select chapter');
        }};

        gradeEl.addEventListener('change', () => {{ filterSubjects(); filterTerms(); filterModules(); filterChapters(); }});
        subjectEl.addEventListener('change', () => {{ filterTerms(); filterModules(); filterChapters(); }});
        termEl.addEventListener('change', () => {{ filterModules(); filterChapters(); }});
        moduleEl.addEventListener('change', filterChapters);

        filterSubjects();
        filterTerms();
        filterModules();
        filterChapters();
      }})();
    </script>
    """



@app.route("/admin/lesson-content/upload-activity-image", methods=["POST"])
def admin_upload_activity_image():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return jsonify({"success": False, "error": "Admin session required."}), 401
    lesson_id_raw = (request.form.get("lesson_id") or "").strip()
    if not lesson_id_raw.isdigit():
        return jsonify({"success": False, "error": "lesson_id is required."}), 400
    lesson = db.session.get(Lesson, int(lesson_id_raw))
    if not lesson:
        return jsonify({"success": False, "error": "Lesson not found."}), 404
    slide_id = (request.form.get("slide_id") or "temp").strip() or "temp"
    public_url, path, upload_error = upload_activity_image_to_supabase(lesson.id, slide_id, request.files.get("image"))
    if upload_error:
        return jsonify({"success": False, "error": upload_error}), 400
    return jsonify({"success": True, "image_url": public_url, "path": path})

@app.route("/admin/lesson-builder/<int:lesson_id>/slides", methods=["GET"], endpoint="admin_lesson_slides")
def admin_lesson_builder_slides(lesson_id: int):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    ensure_lesson_engine_tables()
    lesson = db.session.get(Lesson, lesson_id)
    if not lesson:
        return "<h2>Lesson not found</h2>", 404
    chapter = db.session.get(SyllabusChapter, lesson.chapter_id)
    slides = LessonSlide.query.filter_by(lesson_id=lesson.id).order_by(LessonSlide.slide_order.asc(), LessonSlide.id.asc()).all()
    rows = "".join(
        f"<tr><td>{s.slide_order}</td><td>{escape(s.slide_type)}</td><td>{escape(s.title_en or '')}</td><td>{'Yes' if s.is_active else 'No'}</td><td><a href='/admin/lesson-builder/slides/{s.id}/edit'>Edit</a> | <a href='/admin/lesson-slide/{s.id}/delete' onclick=\"return confirm('Delete this slide?')\" style='color:#dc2626;font-weight:700;'>Delete</a> | <a href='/student/lesson/{lesson.id}'>Preview lesson</a></td></tr>"
        for s in slides
    )
    return f"""<h1>Lesson Slides</h1><p><strong>Lesson:</strong> {escape(lesson.lesson_title_en)}</p><p><strong>Chapter:</strong> {escape(chapter.chapter_name_en if chapter else '-')}</p><p><a href='/admin/lesson-builder'>Back</a> | <a href='/admin/lesson-builder/{lesson.id}/slides/new'>Add Slide</a></p><table border='1' cellpadding='6'><tr><th>Slide order</th><th>Slide type</th><th>Slide title</th><th>Active</th><th>Actions</th></tr>{rows or '<tr><td colspan=5>No slides</td></tr>'}</table>"""


@app.route("/admin/lesson-builder/<int:lesson_id>/slides/new", methods=["GET", "POST"])
@app.route("/admin/lesson-builder/slides/<int:slide_id>/edit", methods=["GET", "POST"])
def admin_lesson_builder_slide_form(lesson_id: int | None = None, slide_id: int | None = None):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    ensure_lesson_engine_tables()
    slide = db.session.get(LessonSlide, slide_id) if slide_id else None
    lesson = db.session.get(Lesson, slide.lesson_id if slide else lesson_id)
    if not lesson:
        return "<h2>Lesson not found</h2>", 404
    if request.method == "POST":
        obj = slide or LessonSlide(lesson_id=lesson.id)
        selected_slide_type = (request.form.get("slide_type") or "explanation").strip() or "explanation"
        obj.slide_order = int((request.form.get("slide_order") or "1"))
        obj.slide_type = selected_slide_type
        obj.title_en = (request.form.get("title_en") or "").strip() or None
        obj.title_si = (request.form.get("title_si") or "").strip() or None
        obj.content_en = (request.form.get("content_en") or "").strip() or None
        obj.content_si = (request.form.get("content_si") or "").strip() or None
        obj.video_url = (request.form.get("video_url") or "").strip() or None
        obj.audio_url = (request.form.get("audio_url") or "").strip() or None
        obj.xp_reward = int((request.form.get("xp_reward") or "10"))
        obj.is_required = _parse_bool_form(request.form.get("is_required"), True)
        obj.is_active = _parse_bool_form(request.form.get("is_active"), True)

        old_activity_json = obj.activity_json
        submitted_activity_json = (request.form.get("activity_json") or "").strip()
        upload_files = request.files.getlist("image_grid_images")
        has_image_grid_uploads = any(upload and upload.filename for upload in upload_files)
        has_image_grid_fields = any(
            key in request.form
            for key in (
                "image_grid_existing_url",
                "image_grid_existing_caption_en",
                "image_grid_existing_caption_si",
                "image_grid_remove",
                "image_grid_caption_en",
                "image_grid_caption_si",
            )
        )
        old_activity_payload = None
        if old_activity_json:
            try:
                parsed_old_activity = json.loads(old_activity_json)
                if isinstance(parsed_old_activity, dict):
                    old_activity_payload = parsed_old_activity
            except (TypeError, ValueError, json.JSONDecodeError):
                old_activity_payload = None
        submitted_activity_payload = None
        if submitted_activity_json:
            try:
                parsed_submitted_activity = json.loads(submitted_activity_json)
                if isinstance(parsed_submitted_activity, dict):
                    submitted_activity_payload = parsed_submitted_activity
            except (TypeError, ValueError, json.JSONDecodeError):
                submitted_activity_payload = None
        is_tap_correct_picture_submission = selected_slide_type == "tap_correct_picture" or (old_activity_payload or {}).get("activity_type") == "tap_correct_picture" or (old_activity_payload or {}).get("type") == "tap_correct_picture" or (submitted_activity_payload or {}).get("activity_type") == "tap_correct_picture" or (submitted_activity_payload or {}).get("type") == "tap_correct_picture"
        is_image_grid_submission = (
            selected_slide_type == "image_grid"
            or has_image_grid_uploads
            or has_image_grid_fields
            or (old_activity_payload or {}).get("type") == "image_grid"
            or (submitted_activity_payload or {}).get("type") == "image_grid"
        )

        print("CONTENT TYPE:", getattr(obj, "content_type", obj.slide_type))
        print("FILES:", request.files)
        print("OLD ACTIVITY JSON:", old_activity_json)

        if is_tap_correct_picture_submission:
            obj.image_url = None
            if not slide:
                db.session.add(obj)
                db.session.flush()

            tap_items = []
            existing_urls = request.form.getlist("tap_existing_image_url")
            existing_correct_values = set(request.form.getlist("tap_existing_correct"))
            remove_existing = set(request.form.getlist("tap_remove_existing"))
            if existing_urls:
                for idx, image_url in enumerate(existing_urls):
                    clean_url = str(image_url or "").strip()
                    if not clean_url or clean_url in remove_existing:
                        continue
                    tap_items.append({"image_url": clean_url, "correct": clean_url in existing_correct_values})
            else:
                for item in parse_tap_correct_picture_activity(old_activity_json or submitted_activity_json).get("items", []):
                    clean_url = str(item.get("image_url") or "").strip()
                    if clean_url:
                        tap_items.append({"image_url": clean_url, "correct": bool(item.get("correct"))})

            new_correct_values = set(request.form.getlist("tap_new_correct"))
            upload_files = request.files.getlist("tap_images")
            for idx, upload in enumerate(upload_files):
                if not upload or not upload.filename:
                    continue
                public_url, object_path, upload_error = upload_activity_image_to_supabase(lesson.id, obj.id or "temp", upload)
                if upload_error:
                    db.session.rollback()
                    return f"<h2>Activity image upload failed</h2><p>{escape(upload_error)}</p><p><a href='{request.path}'>Back</a></p>", 400
                if public_url:
                    tap_items.append({"image_url": public_url, "correct": str(idx) in new_correct_values})

            validation_error = validate_tap_correct_picture_items(tap_items)
            if validation_error:
                db.session.rollback()
                return f"<h2>Could not save tap-correct-picture slide</h2><p>{escape(validation_error)}</p><p><a href='{request.path}'>Back</a></p>", 400

            obj.activity_json = build_tap_correct_picture_activity_json(
                request.form.get("tap_title") or obj.title_si or obj.title_en or "",
                request.form.get("tap_instruction") or obj.content_si or obj.content_en or "",
                tap_items,
                request.form.get("tap_success_message"),
                request.form.get("tap_wrong_message"),
            )
        elif is_image_grid_submission:
            obj.image_url = None
            if not slide:
                db.session.add(obj)
                db.session.flush()

            image_items = []
            remove_existing = set(request.form.getlist("image_grid_remove"))
            existing_urls = request.form.getlist("image_grid_existing_url")
            existing_caption_en = request.form.getlist("image_grid_existing_caption_en")
            existing_caption_si = request.form.getlist("image_grid_existing_caption_si")
            if existing_urls:
                source_existing_images = [
                    {
                        "url": url,
                        "caption_en": existing_caption_en[idx] if idx < len(existing_caption_en) else "",
                        "caption_si": existing_caption_si[idx] if idx < len(existing_caption_si) else "",
                    }
                    for idx, url in enumerate(existing_urls)
                ]
            else:
                source_existing_images = parse_image_grid_activity(old_activity_json)
                if not source_existing_images:
                    source_existing_images = parse_image_grid_activity(submitted_activity_json)

            for image in source_existing_images:
                clean_url = str(image.get("url") or "").strip()
                if not clean_url or clean_url in remove_existing:
                    continue
                image_items.append({
                    "url": clean_url,
                    "caption_en": str(image.get("caption_en") or "").strip(),
                    "caption_si": str(image.get("caption_si") or "").strip(),
                })

            uploaded_urls = []
            new_caption_en = request.form.getlist("image_grid_caption_en")
            new_caption_si = request.form.getlist("image_grid_caption_si")
            for idx, upload in enumerate(upload_files):
                if not upload or not upload.filename:
                    continue
                public_url, upload_error = upload_lesson_image_to_supabase(lesson.id, obj.id or "new", upload)
                if upload_error:
                    db.session.rollback()
                    return f"<h2>Image upload failed</h2><p>{escape(upload_error)}</p><p><a href='{request.path}'>Back</a></p>", 400
                if public_url:
                    uploaded_urls.append(public_url)
                    image_items.append({
                        "url": public_url,
                        "caption_en": (new_caption_en[idx] if idx < len(new_caption_en) else "").strip(),
                        "caption_si": (new_caption_si[idx] if idx < len(new_caption_si) else "").strip(),
                    })
            new_activity_json = build_image_grid_activity_json(image_items)
            print("UPLOADED URLS:", uploaded_urls)
            print("NEW ACTIVITY JSON:", new_activity_json)
            obj.activity_json = new_activity_json
        else:
            obj.image_url = (request.form.get("image_url") or "").strip() or None
            uploaded_urls = []
            new_activity_json = submitted_activity_json or None
            print("UPLOADED URLS:", uploaded_urls)
            print("NEW ACTIVITY JSON:", new_activity_json)
            obj.activity_json = new_activity_json

        if not slide and obj not in db.session:
            db.session.add(obj)
        db.session.commit()
        print("SAVED ACTIVITY JSON:", obj.activity_json)
        return redirect(f"/admin/lesson-builder/{lesson.id}/slides")
    options = ["intro_video", "explanation", "example", "activity", "quiz", "summary", "image_grid", "tap_correct_picture"]
    selected_type = (slide.slide_type if slide else "explanation")
    type_opts = "".join([f"<option value='{x}' {'selected' if x == selected_type else ''}>{x}</option>" for x in options])
    existing_grid_images = parse_image_grid_activity(slide.activity_json if slide else None)
    existing_grid_html = "".join(
        f"""
        <div class='image-grid-admin-row'>
          <img src='{escape(item['url'])}' alt='Existing lesson grid image'>
          <input type='hidden' name='image_grid_existing_url' value='{escape(item['url'])}'>
          <label>Caption EN <input type='text' name='image_grid_existing_caption_en' value='{escape(item.get('caption_en') or '')}'></label>
          <label>Caption SI <input type='text' name='image_grid_existing_caption_si' value='{escape(item.get('caption_si') or '')}'></label>
          <label class='remove-image'><input type='checkbox' name='image_grid_remove' value='{escape(item['url'])}'> Remove</label>
        </div>
        """
        for item in existing_grid_images
    )
    tap_activity = parse_tap_correct_picture_activity(slide.activity_json if slide else None)
    tap_items = tap_activity.get("items", []) if tap_activity else []
    tap_existing_html = "".join(
        f"""
        <div class='tap-picture-admin-row'>
          <img src='{escape(item['image_url'])}' alt='Existing tap-correct-picture item'>
          <input type='hidden' name='tap_existing_image_url' value='{escape(item['image_url'])}'>
          <label class='tap-correct-label'><input type='checkbox' name='tap_existing_correct' value='{escape(item['image_url'])}' {'checked' if item.get('correct') else ''}> Correct image</label>
          <label class='remove-image'><input type='checkbox' name='tap_remove_existing' value='{escape(item['image_url'])}'> Remove</label>
        </div>
        """
        for item in tap_items
    )
    tap_title = tap_activity.get("title") or (slide.title_si if slide and slide.title_si else (slide.title_en if slide and slide.title_en else ""))
    tap_instruction = tap_activity.get("instruction") or (slide.content_si if slide and slide.content_si else (slide.content_en if slide and slide.content_en else ""))
    tap_success_message = tap_activity.get("success_message") or "සුභ පැතුම්! ඔබ නිවැරදි පින්තූර තෝරා ඇත."
    tap_wrong_message = tap_activity.get("wrong_message") or "නැවත උත්සාහ කරන්න."
    return f"""
    <h1>{'Edit Slide' if slide else 'Add Slide'}</h1>
    <p><a href='/admin/lesson-builder/{lesson.id}/slides'>Back to Slides</a></p>
    <form method='post' enctype='multipart/form-data'>
      <label>Slide Order <input type='number' name='slide_order' value='{slide.slide_order if slide else 1}' min='1' required></label><br><br>
      <label>Slide Type <select id='slideTypeSelect' name='slide_type'>{type_opts}</select></label><br><br>
      <label>Title EN <input type='text' name='title_en' value='{escape(slide.title_en) if slide and slide.title_en else ''}'></label><br><br>
      <label>Title SI <input type='text' name='title_si' value='{escape(slide.title_si) if slide and slide.title_si else ''}'></label><br><br>
      <label>Content EN <textarea name='content_en' rows='4' cols='70'>{escape(slide.content_en) if slide and slide.content_en else ''}</textarea></label><br><br>
      <label>Content SI <textarea name='content_si' rows='4' cols='70'>{escape(slide.content_si) if slide and slide.content_si else ''}</textarea></label><br><br>
      <label>Image URL <input type='url' name='image_url' value='{escape(slide.image_url) if slide and slide.image_url else ''}'></label><br><br>
      <label>Video URL <input type='url' name='video_url' value='{escape(slide.video_url) if slide and slide.video_url else ''}'></label><br><br>
      <label>Audio URL <input type='url' name='audio_url' value='{escape(slide.audio_url) if slide and slide.audio_url else ''}'></label><br><br>
      <label>Activity JSON <textarea name='activity_json' rows='4' cols='70'>{escape(slide.activity_json) if slide and slide.activity_json else ''}</textarea></label>
      <p style='max-width:760px;color:#475569;'>For <strong>image_grid</strong>, uploaded image URLs and captions are saved automatically in this JSON field as <code>{{"type":"image_grid","images":[...]}}</code>.</p>
      <fieldset style='border:1px solid #cbd5e1;border-radius:12px;padding:14px;max-width:900px;margin-bottom:18px;'>
        <legend><strong>Image Grid Gallery</strong></legend>
        <p>Use only PNG, JPG, JPEG, or WebP. Each image must be 5MB or less. Files are uploaded to Supabase Storage bucket <code>lesson-images</code>.</p>
        <style>.image-grid-admin-row{{display:grid;grid-template-columns:90px 1fr 1fr auto;gap:10px;align-items:center;margin:10px 0;padding:10px;border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc}}.image-grid-admin-row img{{width:78px;height:78px;object-fit:cover;border-radius:10px}}.image-grid-new-row{{display:grid;grid-template-columns:1.3fr 1fr 1fr;gap:10px;align-items:end;margin:10px 0}}@media(max-width:760px){{.image-grid-admin-row,.image-grid-new-row{{grid-template-columns:1fr}}}}</style>
        <h4>Existing Images</h4>
        {existing_grid_html or '<p>No images saved for this grid yet.</p>'}
        <h4>Upload New Images</h4>
        <label>Images <input id='imageGridFiles' type='file' name='image_grid_images' accept='.png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp' multiple></label>
        <div id='imageGridCaptionRows'></div>
        <script>
          (function() {{
            const input = document.getElementById('imageGridFiles');
            const rows = document.getElementById('imageGridCaptionRows');
            if (!input || !rows) return;
            input.addEventListener('change', function() {{
              rows.innerHTML = '';
              Array.from(input.files || []).forEach(function(file, index) {{
                const row = document.createElement('div');
                row.className = 'image-grid-new-row';
                row.innerHTML = `<strong>${{index + 1}}. ${{file.name}}</strong><label>Caption EN <input type='text' name='image_grid_caption_en' placeholder='English caption'></label><label>Caption SI <input type='text' name='image_grid_caption_si' placeholder='සිංහල caption'></label>`;
                rows.appendChild(row);
              }});
            }});
          }})();
        </script>
        <p style='color:#64748b;'>Tip: captions are matched to selected files in order. Choose all gallery images together to add them in one save.</p>
      </fieldset>
      <fieldset id='tapCorrectPictureBuilder' style='border:1px solid #bbf7d0;border-radius:12px;padding:14px;max-width:900px;margin-bottom:18px;background:#f0fdf4;'>
        <legend><strong>Tap Correct Picture Activity</strong></legend>
        <p>Upload PNG, JPG, JPEG, or WebP files. Each image must be 1MB or less and uploads to Supabase Storage bucket <code>lesson-images</code>.</p>
        <style>.tap-picture-admin-row,.tap-picture-new-row{{display:grid;grid-template-columns:96px 1fr auto;gap:12px;align-items:center;margin:10px 0;padding:12px;border:1px solid #dcfce7;border-radius:14px;background:#fff}}.tap-picture-admin-row img,.tap-picture-preview{{width:84px;height:84px;object-fit:cover;border-radius:14px;box-shadow:0 8px 18px rgba(15,23,42,.12)}}.tap-picture-new-row{{grid-template-columns:96px 1.4fr 1fr auto}}.tap-correct-label{{font-weight:700;color:#166534}}.tap-remove-row{{border:none;border-radius:10px;background:#fee2e2;color:#991b1b;font-weight:800;padding:8px 10px;cursor:pointer}}@media(max-width:760px){{.tap-picture-admin-row,.tap-picture-new-row{{grid-template-columns:1fr}}}}</style>
        <label>Title <input type='text' name='tap_title' value='{escape(tap_title)}' style='width:100%;max-width:720px;'></label><br><br>
        <label>Instruction <textarea name='tap_instruction' rows='3' cols='80'>{escape(tap_instruction)}</textarea></label><br><br>
        <label>Success message <input type='text' name='tap_success_message' value='{escape(tap_success_message)}' style='width:100%;max-width:720px;'></label><br><br>
        <label>Wrong message <input type='text' name='tap_wrong_message' value='{escape(tap_wrong_message)}' style='width:100%;max-width:720px;'></label>
        <h4>Existing Images</h4>
        {tap_existing_html or '<p>No tap-correct-picture images saved yet.</p>'}
        <h4>Upload New Images</h4>
        <div id='tapPictureRows'></div>
        <button type='button' id='addTapPictureRow'>Add another image</button>
        <p style='color:#166534;'>Save requires at least 2 images and at least 1 correct image.</p>
      </fieldset>
      <script>
        (function() {{
          const typeSelect = document.getElementById('slideTypeSelect');
          const builder = document.getElementById('tapCorrectPictureBuilder');
          const rows = document.getElementById('tapPictureRows');
          const addBtn = document.getElementById('addTapPictureRow');
          let rowIndex = 0;
          function escapeAttr(value) {{ return String(value || '').replace(/[&<>"']/g, function(ch) {{ return ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[ch] || ch; }}); }}
          function toggleBuilder() {{ if (builder && typeSelect) builder.style.display = typeSelect.value === 'tap_correct_picture' ? 'block' : 'none'; }}
          function addRow() {{
            if (!rows) return;
            const idx = rowIndex++;
            const row = document.createElement('div');
            row.className = 'tap-picture-new-row';
            row.innerHTML = `<img class='tap-picture-preview' alt='Preview' style='display:none;'><label>Image upload <input type='file' name='tap_images' accept='.png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp'></label><label class='tap-correct-label'><input type='checkbox' name='tap_new_correct' value='${{idx}}'> Correct image</label><button type='button' class='tap-remove-row'>Remove</button>`;
            const input = row.querySelector('input[type=file]');
            const preview = row.querySelector('.tap-picture-preview');
            input.addEventListener('change', function() {{
              const file = input.files && input.files[0];
              if (!file) {{ preview.style.display = 'none'; preview.removeAttribute('src'); return; }}
              preview.src = URL.createObjectURL(file);
              preview.style.display = 'block';
            }});
            row.querySelector('.tap-remove-row').addEventListener('click', function() {{ row.remove(); }});
            rows.appendChild(row);
          }}
          typeSelect?.addEventListener('change', toggleBuilder);
          addBtn?.addEventListener('click', addRow);
          toggleBuilder();
          if (typeSelect && typeSelect.value === 'tap_correct_picture' && rows && !rows.children.length) addRow();
        }})();
      </script>
      <label>XP Reward <input type='number' name='xp_reward' value='{slide.xp_reward if slide else 10}' min='0'></label><br><br>
      <label><input type='checkbox' name='is_required' value='1' {'checked' if (slide.is_required if slide else True) else ''}> Is Required</label><br><br>
      <label><input type='checkbox' name='is_active' value='1' {'checked' if (slide.is_active if slide else True) else ''}> Is Active</label><br><br>
      <button type='submit'>{'Update Slide' if slide else 'Save Slide'}</button>
    </form>
    """

@app.route("/admin/lesson-slide/<int:slide_id>/delete", methods=["GET"])
def admin_delete_lesson_slide(slide_id: int):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    ensure_lesson_engine_tables()
    slide = db.session.get(LessonSlide, slide_id)
    if not slide:
        return "<h2>Slide not found</h2>", 404

    lesson_id = slide.lesson_id
    db.session.delete(slide)
    db.session.commit()

    remaining_slides = LessonSlide.query.filter_by(
        lesson_id=lesson_id
    ).order_by(LessonSlide.slide_order.asc()).all()

    for index, remaining_slide in enumerate(remaining_slides, start=1):
        remaining_slide.slide_order = index

    db.session.commit()

    return redirect(url_for("admin_lesson_slides", lesson_id=lesson_id))

@app.route("/admin/edit-school/<int:school_id>", methods=["GET", "POST"])
def admin_edit_school(school_id: int):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    school = School.query.get_or_404(school_id)

    if request.method == "POST":
        school_name = (request.form.get("school_name") or "").strip()
        if not school_name:
            return f"<h2>School Name is required.</h2><p><a href='/admin/edit-school/{school.id}'>Try again</a></p>", 400

        existing_school = School.query.filter(
            db.func.lower(School.school_name) == school_name.lower(),
            School.id != school.id,
        ).first()
        if existing_school:
            return f"<h2>School name already exists.</h2><p><a href='/admin/edit-school/{school.id}'>Try again</a></p>", 400

        school.school_name = school_name
        db.session.commit()
        return redirect("/admin/schools")

    return f"""
    <h1>Edit School</h1>
    <form method='post' action='/admin/edit-school/{school.id}'>
      <label>School Name: <input type='text' name='school_name' value='{escape(school.school_name)}' required></label><br><br>
      <button type='submit'>Update School</button>
    </form>
    <p><a href='/admin/schools'>Back to Manage Schools</a></p>
    """
@app.route("/admin/create-school-admin", methods=["GET", "POST"])
def admin_create_school_admin():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    schools = School.query.order_by(School.school_name.asc(), School.id.asc()).all()
    if request.method == "POST":
        school_id_raw = (request.form.get("school_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""

        selected_school = School.query.filter_by(id=int(school_id_raw)).first() if school_id_raw.isdigit() else None
        if not selected_school or not name or not email or not password:
            return "<h2>All fields are required and school must be valid.</h2><p><a href='/admin/create-school-admin'>Try again</a></p>", 400

        existing = SchoolAdmin.query.filter_by(email=email).first()
        if existing:
            return "<h2>Email already exists.</h2><p><a href='/admin/create-school-admin'>Try again</a></p>", 400

        db.session.add(
            SchoolAdmin(
                school_id=selected_school.id,
                name=name,
                email=email,
                password_hash=generate_password_hash(password),
            )
        )
        db.session.commit()
        return redirect("/admin-dashboard")

    school_options = "".join(f"<option value='{school.id}'>{school.school_name}</option>" for school in schools)
    return f"""
    <h1>Create School Admin</h1>
    <form method='post' action='/admin/create-school-admin'>
      <label>School Name:
        <select name='school_id' required>
          <option value=''>Select School</option>
          {school_options}
        </select>
      </label><br><br>
      <label>Name: <input type='text' name='name' required></label><br><br>
      <label>Email: <input type='email' name='email' required></label><br><br>
      <label>Password: <input type='password' name='password' required></label><br><br>
      <button type='submit'>Create School Admin</button>
    </form>
    <p><a href='/admin-dashboard'>Back to Admin Dashboard</a></p>
    """


@app.route("/admin/deactivate-premium/<int:student_id>", methods=["GET"])
def admin_deactivate_premium(student_id: int):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    student = Student.query.get_or_404(student_id)
    student.is_premium = False
    student.subscription_end_date = None
    db.session.commit()
    return redirect("/admin/premium")


@app.route("/admin/students", methods=["GET"])
def admin_students():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    students_with_school = (
        db.session.query(Student, School.school_name)
        .outerjoin(School, Student.school_id == School.id)
        .order_by(Student.created_at.desc(), Student.id.desc())
        .all()
    )
    student_rows = "".join(
        f"""
        <tr>
          <td style='border:1px solid #ccc;padding:8px;'>{student.id}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.name}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.grade}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.medium}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{school_name or '-'}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.email}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.parent_email or '-'}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.mobile}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.xp}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.level}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.created_at.strftime('%Y-%m-%d %H:%M:%S')}</td>
          <td style='border:1px solid #ccc;padding:8px;'><a href='/admin/student/{student.id}'>View Details</a></td>
        </tr>
        """
        for student, school_name in students_with_school
    )
    return f"""
    <h1>Manage Students</h1>
    <p><a href='/admin-dashboard'>Back to Admin Dashboard</a></p>
    <p><a href='/register-form'>Add New Student</a></p>
    <table style='border-collapse:collapse;width:100%;'>
      <thead><tr><th style='border:1px solid #ccc;padding:8px;'>ID</th><th style='border:1px solid #ccc;padding:8px;'>Name</th><th style='border:1px solid #ccc;padding:8px;'>Grade</th><th style='border:1px solid #ccc;padding:8px;'>Medium</th><th style='border:1px solid #ccc;padding:8px;'>School</th><th style='border:1px solid #ccc;padding:8px;'>Email</th><th style='border:1px solid #ccc;padding:8px;'>Parent Email</th><th style='border:1px solid #ccc;padding:8px;'>Mobile</th><th style='border:1px solid #ccc;padding:8px;'>XP</th><th style='border:1px solid #ccc;padding:8px;'>Level</th><th style='border:1px solid #ccc;padding:8px;'>Created At</th><th style='border:1px solid #ccc;padding:8px;'>Action</th></tr></thead>
      <tbody>{student_rows if student_rows else "<tr><td colspan='12' style='border:1px solid #ccc;padding:8px;'>No students found.</td></tr>"}</tbody>
    </table>
    """


@app.route("/admin/student/<int:student_id>", methods=["GET"])
def admin_student_details(student_id: int):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    student = Student.query.get_or_404(student_id)
    skillscan_results = StudentResult.query.filter_by(student_id=student.id).order_by(StudentResult.created_at.desc(), StudentResult.id.desc()).all()
    practice_attempts = PracticeAttempt.query.filter_by(student_id=student.id).order_by(PracticeAttempt.created_at.desc(), PracticeAttempt.id.desc()).all()
    latest_result = skillscan_results[0] if skillscan_results else None
    latest_topic_performance = []
    weak_topics = []
    if latest_result:
        latest_topic_performance = StudentTopicPerformance.query.filter_by(student_result_id=latest_result.id).order_by(StudentTopicPerformance.percentage.asc()).all()
        weak_topics = [topic for topic in latest_topic_performance if topic.percentage < 50]
    skillscan_rows = "".join(f"<tr><td style='border:1px solid #ccc;padding:8px;'>{item.created_at.strftime('%Y-%m-%d %H:%M:%S')}</td><td style='border:1px solid #ccc;padding:8px;'>{item.score}%</td><td style='border:1px solid #ccc;padding:8px;'>{item.correct_answers}/{item.total_questions}</td><td style='border:1px solid #ccc;padding:8px;'>{item.level}</td></tr>" for item in skillscan_results)
    practice_rows = "".join(f"<tr><td style='border:1px solid #ccc;padding:8px;'>{item.created_at.strftime('%Y-%m-%d %H:%M:%S')}</td><td style='border:1px solid #ccc;padding:8px;'>{item.topic_en}</td><td style='border:1px solid #ccc;padding:8px;'>{item.score}%</td><td style='border:1px solid #ccc;padding:8px;'>{item.correct_answers}/{item.total_questions}</td></tr>" for item in practice_attempts)
    topic_rows = "".join(f"<tr><td style='border:1px solid #ccc;padding:8px;'>{topic.topic_en}</td><td style='border:1px solid #ccc;padding:8px;'>{topic.correct_count}/{topic.total_count}</td><td style='border:1px solid #ccc;padding:8px;'>{topic.percentage}%</td><td style='border:1px solid #ccc;padding:8px;'>{topic.status_en}</td></tr>" for topic in latest_topic_performance)
    weak_rows = "".join(f"<tr><td style='border:1px solid #ccc;padding:8px;'>{topic.topic_en}</td><td style='border:1px solid #ccc;padding:8px;'>{topic.percentage}%</td></tr>" for topic in weak_topics)
    progress_rows = StudentTopicProgress.query.filter_by(student_id=student.id).order_by(StudentTopicProgress.last_updated.desc(), StudentTopicProgress.id.desc()).all()
    progress_rows_html = "".join(
        f"<tr><td style='border:1px solid #ccc;padding:8px;'>{item.topic_en}</td><td style='border:1px solid #ccc;padding:8px;'>{item.latest_score}%</td><td style='border:1px solid #ccc;padding:8px;'>{item.mastery_level_en}</td><td style='border:1px solid #ccc;padding:8px;'>{item.attempts_count}</td><td style='border:1px solid #ccc;padding:8px;'>{item.last_updated.strftime('%Y-%m-%d %H:%M:%S')}</td></tr>"
        for item in progress_rows
    )
    return f"""
    <h1>Student Details: {student.name}</h1>
    <p><a href='/admin/students'>Back to Manage Students</a></p>
    <h2>Student Profile</h2>
    <table style='border-collapse:collapse;'><tr><td style='border:1px solid #ccc;padding:8px;'>ID</td><td style='border:1px solid #ccc;padding:8px;'>{student.id}</td></tr><tr><td style='border:1px solid #ccc;padding:8px;'>Grade</td><td style='border:1px solid #ccc;padding:8px;'>{student.grade}</td></tr><tr><td style='border:1px solid #ccc;padding:8px;'>Medium</td><td style='border:1px solid #ccc;padding:8px;'>{student.medium}</td></tr><tr><td style='border:1px solid #ccc;padding:8px;'>Email</td><td style='border:1px solid #ccc;padding:8px;'>{student.email}</td></tr><tr><td style='border:1px solid #ccc;padding:8px;'>Parent Email</td><td style='border:1px solid #ccc;padding:8px;'>{student.parent_email or '-'}</td></tr><tr><td style='border:1px solid #ccc;padding:8px;'>Mobile</td><td style='border:1px solid #ccc;padding:8px;'>{student.mobile}</td></tr><tr><td style='border:1px solid #ccc;padding:8px;'>XP / Level</td><td style='border:1px solid #ccc;padding:8px;'>{student.xp} / {student.level}</td></tr></table>
    <h2>SkillScan Result History</h2><table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;'>Date</th><th style='border:1px solid #ccc;padding:8px;'>Score</th><th style='border:1px solid #ccc;padding:8px;'>Correct</th><th style='border:1px solid #ccc;padding:8px;'>Level</th></tr></thead><tbody>{skillscan_rows if skillscan_rows else "<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>No SkillScan results found.</td></tr>"}</tbody></table>
    <h2>Practice Attempt History</h2><table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;'>Date</th><th style='border:1px solid #ccc;padding:8px;'>Topic</th><th style='border:1px solid #ccc;padding:8px;'>Score</th><th style='border:1px solid #ccc;padding:8px;'>Correct</th></tr></thead><tbody>{practice_rows if practice_rows else "<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>No practice attempts found.</td></tr>"}</tbody></table>
    <h2>Latest Topic-wise Performance</h2><table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;'>Topic</th><th style='border:1px solid #ccc;padding:8px;'>Correct</th><th style='border:1px solid #ccc;padding:8px;'>Percentage</th><th style='border:1px solid #ccc;padding:8px;'>Status</th></tr></thead><tbody>{topic_rows if topic_rows else "<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>No topic-wise data found.</td></tr>"}</tbody></table>
    <h2>Weak Topics (Percentage &lt; 50)</h2><table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;'>Topic</th><th style='border:1px solid #ccc;padding:8px;'>Percentage</th></tr></thead><tbody>{weak_rows if weak_rows else "<tr><td colspan='2' style='border:1px solid #ccc;padding:8px;'>No weak topics found.</td></tr>"}</tbody></table>
    <h2>Student Topic Progress</h2><table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;'>Topic</th><th style='border:1px solid #ccc;padding:8px;'>Latest Score</th><th style='border:1px solid #ccc;padding:8px;'>Mastery Level</th><th style='border:1px solid #ccc;padding:8px;'>Attempts Count</th><th style='border:1px solid #ccc;padding:8px;'>Last Updated</th></tr></thead><tbody>{progress_rows_html if progress_rows_html else "<tr><td colspan='5' style='border:1px solid #ccc;padding:8px;'>No topic progress found.</td></tr>"}</tbody></table>
    """




def ensure_tap_question_columns() -> None:
    db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS tap_areas_json TEXT"))
    db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS correct_area_id TEXT"))
    db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS question_type VARCHAR(20) DEFAULT 'mcq'"))
    db.session.execute(db.text("UPDATE question SET question_type = 'mcq' WHERE question_type IS NULL"))


@app.route("/update-tap-question-db", methods=["GET"])
def update_tap_question_db() -> tuple[str, int]:
    try:
        ensure_tap_question_columns()
        db.session.commit()
        return "Tap question database updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Tap question DB update failed: {exc}"}), 500
@app.route("/admin/questions", methods=["GET"])
def admin_questions():
    admin_redirect = admin_session_required()
    try:
        ensure_tap_question_columns()
        db.session.commit()
    except Exception:
        db.session.rollback()
    if admin_redirect:
        return admin_redirect

    selected_grade = (request.args.get("grade") or "").strip()
    selected_subject = (request.args.get("subject") or "").strip()
    selected_topic = (request.args.get("topic") or "").strip()

    filters = []
    if selected_grade:
        filters.append(Question.grade == selected_grade)
    if selected_subject:
        filters.append(Question.subject == selected_subject)
    if selected_topic:
        filters.append(Question.topic_en == selected_topic)

    questions_query = Question.query
    if filters:
        questions_query = questions_query.filter(*filters)
    questions = questions_query.order_by(Question.id.desc()).all()

    grades = [row[0] for row in db.session.query(Question.grade).distinct().order_by(Question.grade.asc()).all()]
    subjects = [row[0] for row in db.session.query(Question.subject).distinct().order_by(Question.subject.asc()).all()]
    topics = [row[0] for row in db.session.query(Question.topic_en).distinct().order_by(Question.topic_en.asc()).all()]

    def build_options(values: list[str], current: str) -> str:
        options = "<option value=''>All</option>"
        for value in values:
            selected = "selected" if value == current else ""
            options += f"<option value='{escape(value)}' {selected}>{escape(value)}</option>"
        return options

    rows = "".join(
        f"""
        <tr>
          <td style='border:1px solid #ccc;padding:8px;'>{q.id}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{escape(q.grade)}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{escape(q.subject)}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{escape(q.topic_en)}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{escape(q.question_text_en)}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{escape(q.question_text_si)}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{q.difficulty_level or 1}</td>
          <td style='border:1px solid #ccc;padding:8px;'><a href='/admin/edit-question/{q.id}'>Edit</a> | <a href='/admin/delete-question/{q.id}' onclick="return confirm('Delete this question?');">Delete</a></td>
        </tr>
        """
        for q in questions
    )

    return f"""
    <h1>Manage Questions</h1>
    <p><a href='/admin-dashboard'>Back to Admin Dashboard</a> | <a href='/admin/add-question'>Add New Question</a> | <a href='/admin/generate-questions'>Generate Questions (Bulk)</a> | <a href='/admin/ai-generate'>Generate Questions (AI)</a></p>
    <form method='get' action='/admin/questions'>
      <label>Grade:
        <select name='grade'>{build_options(grades, selected_grade)}</select>
      </label>
      <label>Subject:
        <select name='subject'>{build_options(subjects, selected_subject)}</select>
      </label>
      <label>Topic:
        <select name='topic'>{build_options(topics, selected_topic)}</select>
      </label>
      <button type='submit'>Filter</button>
      <a href='/admin/questions'>Reset</a>
    </form>
    <br>
    <table style='border-collapse:collapse;width:100%;'>
      <thead><tr><th style='border:1px solid #ccc;padding:8px;'>ID</th><th style='border:1px solid #ccc;padding:8px;'>Grade</th><th style='border:1px solid #ccc;padding:8px;'>Subject</th><th style='border:1px solid #ccc;padding:8px;'>Topic</th><th style='border:1px solid #ccc;padding:8px;'>Question (EN)</th><th style='border:1px solid #ccc;padding:8px;'>Question (SI)</th><th style='border:1px solid #ccc;padding:8px;'>Difficulty</th><th style='border:1px solid #ccc;padding:8px;'>Actions</th></tr></thead>
      <tbody>{rows if rows else "<tr><td colspan='8' style='border:1px solid #ccc;padding:8px;'>No questions found.</td></tr>"}</tbody>
    </table>
    """


@app.route("/admin/subjects", methods=["GET"])
def admin_subjects():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    grade = normalize_grade(request.args.get("grade"))
    query = SubjectMaster.query
    if grade:
        query = query.filter_by(grade=grade)
    subjects = query.order_by(SubjectMaster.grade.asc(), SubjectMaster.subject_name_en.asc()).all()
    rows = "".join([f"<tr><td>{display_grade(x.grade)}</td><td>{escape(x.subject_code)}</td><td>{escape(x.subject_name_en)}</td><td>{escape(x.subject_name_si)}</td><td>{'Yes' if x.is_active else 'No'}</td><td><a href='/admin/subject/edit/{x.id}'>Edit</a></td></tr>" for x in subjects])
    return f"<h1>Subject Management</h1><p><a href='/admin-dashboard'>Back</a> | <a href='/admin/subject/add'>Add Subject</a></p><form><label>Grade <select name='grade'><option value=''>All</option>{grade_options_html(grade)}</select></label><button type='submit'>Filter</button></form><table border='1' cellpadding='6'><tr><th>Grade</th><th>Subject Code</th><th>Subject Name EN</th><th>Subject Name SI</th><th>Active</th><th>Action</th></tr>{rows or '<tr><td colspan=6>No subjects found</td></tr>'}</table>"


@app.route("/admin/subject/add", methods=["GET", "POST"])
@app.route("/admin/subject/edit/<int:subject_id>", methods=["GET", "POST"])
def admin_subject_form(subject_id: int | None = None):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    def render_subject_form(subject_obj: SubjectMaster, is_edit_mode: bool = False):
        messages = "".join([
            f"<p style='color:#b42318;'>{escape(msg)}</p>" if cat == "error" else f"<p style='color:#027a48;'>{escape(msg)}</p>"
            for cat, msg in get_flashed_messages(with_categories=True)
        ])
        si_current = f"<small>Current: <a href='{escape(subject_obj.image_si_url)}' target='_blank'>View Sinhala image</a></small><br>" if is_edit_mode and subject_obj.image_si_url else ""
        en_current = f"<small>Current: <a href='{escape(subject_obj.image_en_url)}' target='_blank'>View English image</a></small><br>" if is_edit_mode and subject_obj.image_en_url else ""
        return f"<h1>{'Edit' if is_edit_mode else 'Add'} Subject</h1>{messages}<form method='POST' enctype='multipart/form-data'><label>Grade <select name='grade' required>{grade_options_html(subject_obj.grade if is_edit_mode else '')}</select></label><br><label>Subject Code <input type='text' name='subject_code' value='{escape(subject_obj.subject_code if is_edit_mode else '')}' required></label><br><label>Subject Name EN <input type='text' name='subject_name_en' value='{escape(subject_obj.subject_name_en if is_edit_mode else '')}' required></label><br><label>Subject Name SI <input type='text' name='subject_name_si' value='{escape(subject_obj.subject_name_si if is_edit_mode else '')}' required></label><br><label>Sinhala Medium Image <input type='file' name='image_si' accept='image/jpeg,image/png,image/webp'></label><br>{si_current}<label>English Medium Image <input type='file' name='image_en' accept='image/jpeg,image/png,image/webp'></label><br>{en_current}<label>Active <input type='checkbox' name='is_active' value='1' {'checked' if (subject_obj.is_active if is_edit_mode else True) else ''}></label><br><button type='submit'>Save</button></form>"

    obj = SubjectMaster.query.get(subject_id) if subject_id else SubjectMaster()
    if request.method == "POST":
        app.logger.error(
            "SUBJECT FORM DEBUG form=%s files=%s",
            dict(request.form),
            list(request.files.keys())
        )
        grade_raw = request.form.get("grade")
        subject_code = request.form.get("subject_code")
        subject_name_en = request.form.get("subject_name_en")
        subject_name_si = request.form.get("subject_name_si")
        is_active = request.form.get("is_active") == "1"

        if not grade_raw or not subject_code or not subject_name_en or not subject_name_si:
            flash("Grade, Subject Code, Subject Name EN and Subject Name SI are required.", "error")
            return render_subject_form(obj, is_edit_mode=bool(subject_id)), 400

        obj.grade = int(grade_raw)
        obj.subject_code = subject_code.strip()
        obj.subject_name_en = subject_name_en.strip()
        obj.subject_name_si = subject_name_si.strip()
        obj.is_active = is_active

        if not subject_id:
            db.session.add(obj)
            db.session.flush()

        si_file = request.files.get("image_si")
        en_file = request.files.get("image_en")
        if si_file and si_file.filename:
            si_image_bytes = si_file.read()
            si_upload_url, si_upload_error = upload_subject_image_to_supabase(obj.id, "si", si_image_bytes, si_file.mimetype or "image/webp")
            if si_upload_error:
                return f"<h3>{escape(si_upload_error)}</h3><p><a href='javascript:history.back()'>Go back</a></p>", 500
            obj.image_si_url = si_upload_url
        if en_file and en_file.filename:
            en_image_bytes = en_file.read()
            en_upload_url, en_upload_error = upload_subject_image_to_supabase(obj.id, "en", en_image_bytes, en_file.mimetype or "image/webp")
            if en_upload_error:
                return f"<h3>{escape(en_upload_error)}</h3><p><a href='javascript:history.back()'>Go back</a></p>", 500
            obj.image_en_url = en_upload_url
        db.session.commit()
        return redirect("/admin/subjects")

    return render_subject_form(obj, is_edit_mode=bool(subject_id))


@app.route("/admin/syllabus", methods=["GET"])
def admin_syllabus():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    grade = (request.args.get("grade") or "").strip()
    subject = (request.args.get("subject") or "").strip()
    term_query = SyllabusTerm.query
    if grade:
        term_query = term_query.filter_by(grade=grade)
    if subject:
        term_query = term_query.filter_by(subject=subject)
    terms = term_query.order_by(SyllabusTerm.grade.asc(), SyllabusTerm.subject.asc(), SyllabusTerm.term_number.asc()).all()
    rows = ""
    for t_item in terms:
        modules = SyllabusModule.query.filter_by(term_id=t_item.id).order_by(SyllabusModule.module_order.asc()).all()
        module_html = ""
        for m in modules:
            chapters = SyllabusChapter.query.filter_by(module_id=m.id).order_by(SyllabusChapter.chapter_order.asc()).all()
            chapter_html = "".join([
                f"<li>{escape(c.chapter_name_en)} / {escape(c.chapter_name_si)} ({'Active' if c.is_active else 'Inactive'}) - <a href='/admin/syllabus/chapter/edit/{c.id}'>Edit</a> | <a href='/admin/chapters/content/{c.id}'>Manage Learning Content</a></li>"
                for c in chapters
            ])
            module_html += f"<li>Module {m.module_order}: {escape(m.module_name_en)} - <a href='/admin/syllabus/module/edit/{m.id}'>Edit</a> | <a href='/admin/syllabus/chapter/add/{m.id}'>Add Chapter</a><ul>{chapter_html or '<li>No chapters</li>'}</ul></li>"
        rows += f"<tr><td>{escape(t_item.grade)}</td><td>{escape(t_item.subject)}</td><td>{t_item.term_number}</td><td>{escape(t_item.term_name_en)}</td><td><a href='/admin/syllabus/term/edit/{t_item.id}'>Edit</a> | <a href='/admin/syllabus/module/add/{t_item.id}'>Add Module</a><ul>{module_html or '<li>No modules</li>'}</ul></td></tr>"
    return f"<h1>Syllabus Management</h1><p><a href='/admin-dashboard'>Back</a> | <a href='/admin/syllabus/term/add'>Add Term</a> | <a href='/admin/subjects'>Manage Subjects</a></p><form method='get'><label>Grade <select name='grade'><option value=''>All</option>{grade_options_html(grade)}</select></label><label> Subject <select name='subject'>{subject_options_html(grade, subject, active_only=False)}</select></label><button type='submit'>Filter</button></form><table border='1' cellpadding='6'><tr><th>Grade</th><th>Subject</th><th>Term #</th><th>Term Name</th><th>Hierarchy</th></tr>{rows or '<tr><td colspan=5>No terms found</td></tr>'}</table>"


@app.route("/admin/add-question", methods=["GET", "POST"])
def admin_add_question():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    if request.method == "GET":
        return render_question_form("/admin/add-question", {}, "Add New Question", "Save Question")

    form_data, error = parse_question_form_data(has_uploaded_image=bool(request.files.get("question_image") and request.files.get("question_image").filename))
    if error:
        return render_question_form("/admin/add-question", request.form, "Add New Question", "Save Question", error), 400

    uploaded_image_url, upload_error = save_question_image_upload(request.files.get("question_image"))
    if upload_error:
        return render_question_form("/admin/add-question", request.form, "Add New Question", "Save Question", upload_error), 400
    if uploaded_image_url:
        form_data["image_url"] = uploaded_image_url

    question = Question(
        grade=form_data["grade"],
        subject=form_data["subject"],
        topic=form_data["topic"],
        topic_en=form_data["chapter_en"],
        topic_si=form_data["chapter_si"],
        term_id=form_data["term_id"],
        module_id=form_data["module_id"],
        chapter_id=form_data["chapter_id"],
        chapter_en=form_data["chapter_en"],
        chapter_si=form_data["chapter_si"],
        question_text_en=form_data["question_text_en"],
        question_text_si=form_data["question_text_si"],
        option_a_en=form_data["option_a"],
        option_a_si=form_data["option_a"],
        option_b_en=form_data["option_b"],
        option_b_si=form_data["option_b"],
        option_c_en=form_data["option_c"],
        option_c_si=form_data["option_c"],
        option_d_en=form_data["option_d"],
        option_d_si=form_data["option_d"],
        question_type=form_data["question_type"],
        correct_answer_text=form_data["correct_answer_text"] or None,
        box_template=form_data["box_template"] or None,
        box_answers=form_data["box_answers"] or None,
        matching_left_en=form_data["matching_left_en"] or None,
        matching_right_en=form_data["matching_right_en"] or None,
        matching_answers_en=form_data["matching_answers_en"] or None,
        matching_left_si=form_data["matching_left_si"] or None,
        matching_right_si=form_data["matching_right_si"] or None,
        matching_answers_si=form_data["matching_answers_si"] or None,
        tap_areas_json=form_data["tap_areas_json"] or None,
        correct_area_id=form_data["correct_area_id"] or None,
        drag_items_json=form_data["drag_items_json"] or None,
        drag_container_image_url=form_data["drag_container_image_url"] or None,
        drag_groups_json=form_data["drag_groups_json"] or None,
        image_url=form_data["image_url"] or None,
        correct_option=form_data["correct_option"],
        explanation_en="N/A",
        explanation_si="N/A",
        difficulty_level=form_data["difficulty_level"],
    )
    db.session.add(question)
    db.session.commit()
    return redirect("/admin/questions")


def _syllabus_bool(value: str | None) -> bool:
    return (value or "").strip().lower() not in {"0", "false", "no", "off"}


@app.route("/admin/syllabus/term/add", methods=["GET", "POST"])
@app.route("/admin/syllabus/term/edit/<int:term_id>", methods=["GET", "POST"])
def admin_syllabus_term_form(term_id: int | None = None):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    term = SyllabusTerm.query.get(term_id) if term_id else None
    if request.method == "POST":
        obj = term or SyllabusTerm()
        obj.grade = normalize_grade(request.form.get("grade"))
        obj.subject = (request.form.get("subject") or "").strip()
        obj.term_number = int(request.form.get("term_number") or 1)
        obj.term_name_en = (request.form.get("term_name_en") or "").strip()
        obj.term_name_si = (request.form.get("term_name_si") or "").strip()
        if not term:
            db.session.add(obj)
        db.session.commit()
        return redirect("/admin/syllabus")
    return f"<h1>{'Edit' if term else 'Add'} Term</h1><form method='post'><label>Grade <select name='grade' required>{grade_options_html(term.grade if term else '')}</select></label><br><label>Subject <select name='subject' required>{subject_options_html(term.grade if term else '', term.subject if term else '')}</select></label><br><label>Term Number <input type='number' name='term_number' value='{term.term_number if term else 1}' required></label><br><label>Term Name EN <input name='term_name_en' value='{escape(term.term_name_en if term else '')}' required></label><br><label>Term Name SI <input name='term_name_si' value='{escape(term.term_name_si if term else '')}' required></label><br><button type='submit'>Save</button></form>"


@app.route("/admin/syllabus/module/add/<int:term_id>", methods=["GET", "POST"])
@app.route("/admin/syllabus/module/edit/<int:module_id>", methods=["GET", "POST"])
def admin_syllabus_module_form(term_id: int | None = None, module_id: int | None = None):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    module = SyllabusModule.query.get(module_id) if module_id else None
    if request.method == "POST":
        obj = module or SyllabusModule()
        obj.term_id = int(request.form.get("term_id") or term_id or 0)
        obj.module_order = int(request.form.get("module_order") or 1)
        obj.module_name_en = (request.form.get("module_name_en") or "").strip()
        obj.module_name_si = (request.form.get("module_name_si") or "").strip()
        if not module:
            db.session.add(obj)
            db.session.flush()
        si_file = request.files.get("image_si_file")
        en_file = request.files.get("image_en_file")
        if (request.form.get("remove_image_si") or "").lower() in {"1","true","on","yes"}:
            obj.image_si_url = None
        elif si_file and si_file.filename:
            image_url, upload_error = upload_module_image_to_supabase(obj.id, "si", si_file.read(), si_file.mimetype or "image/webp")
            if upload_error:
                return f"<h3>{escape(upload_error)}</h3><p><a href='javascript:history.back()'>Go back</a></p>", 500
            obj.image_si_url = image_url
        if (request.form.get("remove_image_en") or "").lower() in {"1","true","on","yes"}:
            obj.image_en_url = None
        elif en_file and en_file.filename:
            image_url, upload_error = upload_module_image_to_supabase(obj.id, "en", en_file.read(), en_file.mimetype or "image/webp")
            if upload_error:
                return f"<h3>{escape(upload_error)}</h3><p><a href='javascript:history.back()'>Go back</a></p>", 500
            obj.image_en_url = image_url
        db.session.commit()
        return redirect("/admin/syllabus")
    si_preview = f"<div style='margin-top:8px;'><img src='{escape(module.image_si_url)}' alt='Sinhala cover' style='width:120px;height:180px;object-fit:cover;border-radius:12px;border:1px solid #dbe2ef;'><br><small><a href='{escape(module.image_si_url)}' target='_blank'>Open</a></small></div>" if module and module.image_si_url else ""
    en_preview = f"<div style='margin-top:8px;'><img src='{escape(module.image_en_url)}' alt='English cover' style='width:120px;height:180px;object-fit:cover;border-radius:12px;border:1px solid #dbe2ef;'><br><small><a href='{escape(module.image_en_url)}' target='_blank'>Open</a></small></div>" if module and module.image_en_url else ""
    return f"""<h1>{'Edit' if module else 'Add'} Module</h1><form method='post' enctype='multipart/form-data' style='max-width:860px;'><label>Term ID <input name='term_id' value='{module.term_id if module else term_id}' required></label><br><label>Module Order <input type='number' name='module_order' value='{module.module_order if module else 1}' required></label><br><label>Module Name EN <input name='module_name_en' value='{escape(module.module_name_en if module else '')}' required></label><br><label>Module Name SI <input name='module_name_si' value='{escape(module.module_name_si if module else '')}' required></label><br><div style='display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px;'><div style='border:1px solid #dbe2ef;border-radius:18px;padding:16px;background:linear-gradient(180deg,#fff,#f8fbff);'><h3 style='margin:0 0 8px;'>Sinhala Medium Cover Image</h3><label style='display:block;border:2px dashed #7aa2ff;border-radius:16px;padding:20px;text-align:center;cursor:pointer;'>Drag & Drop / Upload<input type='file' name='image_si_file' accept='image/jpeg,image/png,image/webp' style='display:block;margin:10px auto 0;'></label><small>Recommended Size: 600x900</small>{si_preview}<br><label><input type='checkbox' name='remove_image_si'> Remove image</label></div><div style='border:1px solid #dbe2ef;border-radius:18px;padding:16px;background:linear-gradient(180deg,#fff,#f8fbff);'><h3 style='margin:0 0 8px;'>English Medium Cover Image</h3><label style='display:block;border:2px dashed #7aa2ff;border-radius:16px;padding:20px;text-align:center;cursor:pointer;'>Drag & Drop / Upload<input type='file' name='image_en_file' accept='image/jpeg,image/png,image/webp' style='display:block;margin:10px auto 0;'></label><small>Recommended Size: 600x900</small>{en_preview}<br><label><input type='checkbox' name='remove_image_en'> Remove image</label></div></div><br><button type='submit'>Save</button></form>"""


@app.route("/admin/syllabus/chapter/add/<int:module_id>", methods=["GET", "POST"])
@app.route("/admin/syllabus/chapter/edit/<int:chapter_id>", methods=["GET", "POST"])
def admin_syllabus_chapter_form(module_id: int | None = None, chapter_id: int | None = None):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    chapter = SyllabusChapter.query.get(chapter_id) if chapter_id else None
    if request.method == "POST":
        obj = chapter or SyllabusChapter()
        obj.module_id = int(request.form.get("module_id") or module_id or 0)
        obj.chapter_order = int(request.form.get("chapter_order") or 1)
        obj.chapter_name_en = (request.form.get("chapter_name_en") or "").strip()
        obj.chapter_name_si = (request.form.get("chapter_name_si") or "").strip()
        obj.competency_levels = (request.form.get("competency_levels") or "").strip()
        obj.estimated_periods = int(request.form.get("estimated_periods") or 0) or None
        obj.is_active = _syllabus_bool(request.form.get("is_active"))
        if not chapter:
            db.session.add(obj)
        db.session.commit()
        return redirect("/admin/syllabus")
    return f"<h1>{'Edit' if chapter else 'Add'} Chapter</h1><form method='post'><label>Module ID <input name='module_id' value='{chapter.module_id if chapter else module_id}' required></label><br><label>Chapter Order <input type='number' name='chapter_order' value='{chapter.chapter_order if chapter else 1}' required></label><br><label>Chapter Name EN <input name='chapter_name_en' value='{escape(chapter.chapter_name_en if chapter else '')}' required></label><br><label>Chapter Name SI <input name='chapter_name_si' value='{escape(chapter.chapter_name_si if chapter else '')}' required></label><br><label>Competency Levels <input name='competency_levels' value='{escape(chapter.competency_levels if chapter else '')}'></label><br><label>Estimated Periods <input type='number' name='estimated_periods' value='{chapter.estimated_periods if chapter and chapter.estimated_periods else ''}'></label><br><label>Is Active <input type='checkbox' name='is_active' {'checked' if (chapter.is_active if chapter else True) else ''}></label><br><button type='submit'>Save</button></form>"


@app.route("/admin/edit-question/<int:question_id>", methods=["GET", "POST"])
def admin_edit_question(question_id: int):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    question = Question.query.get_or_404(question_id)

    if request.method == "GET":
        return render_question_form(
            f"/admin/edit-question/{question_id}",
            {
                "grade": question.grade,
                "subject": question.subject,
                "topic": question.topic_en,
                "term_id": question.term_id or "",
                "module_id": question.module_id or "",
                "chapter_id": question.chapter_id or "",
                "question_text_en": question.question_text_en,
                "question_text_si": question.question_text_si,
                "option_a": question.option_a_en,
                "option_b": question.option_b_en,
                "option_c": question.option_c_en,
                "option_d": question.option_d_en,
                "correct_option": question.correct_option,
                "question_type": question.question_type or "mcq",
                "correct_answer_text": question.correct_answer_text or "",
                "box_template": question.box_template or "",
                "box_answers": question.box_answers or "",
                "matching_left_en": "\n".join(json.loads(question.matching_left_en or "[]")),
                "matching_right_en": "\n".join(json.loads(question.matching_right_en or "[]")),
                "matching_answers_en": question.matching_answers_en or "",
                "matching_left_si": "\n".join(json.loads(question.matching_left_si or "[]")),
                "matching_right_si": "\n".join(json.loads(question.matching_right_si or "[]")),
                "matching_answers_si": question.matching_answers_si or "",
                "tap_areas_json": question.tap_areas_json or "",
                "correct_area_id": question.correct_area_id or "",
                "drag_items_json": question.drag_items_json or "",
                "drag_container_image_url": question.drag_container_image_url or "",
                "drag_groups_json": question.drag_groups_json or "",
                "image_url": question.image_url or "",
                "difficulty_level": question.difficulty_level or 1,
            },
            "Edit Question",
            "Update Question",
        )

    form_data, error = parse_question_form_data(
        has_uploaded_image=bool(request.files.get("question_image") and request.files.get("question_image").filename),
        existing_image_url=question.image_url or "",
    )
    if error:
        return render_question_form(f"/admin/edit-question/{question_id}", request.form, "Edit Question", "Update Question", error), 400

    uploaded_image_url, upload_error = save_question_image_upload(request.files.get("question_image"))
    if upload_error:
        return render_question_form(f"/admin/edit-question/{question_id}", request.form, "Edit Question", "Update Question", upload_error), 400

    question.grade = form_data["grade"]
    question.subject = form_data["subject"]
    question.topic = form_data["topic"]
    question.topic_en = form_data["chapter_en"]
    question.topic_si = form_data["chapter_si"]
    question.term_id = form_data["term_id"]
    question.module_id = form_data["module_id"]
    question.chapter_id = form_data["chapter_id"]
    question.chapter_en = form_data["chapter_en"]
    question.chapter_si = form_data["chapter_si"]
    question.question_text_en = form_data["question_text_en"]
    question.question_text_si = form_data["question_text_si"]
    question.option_a_en = form_data["option_a"]
    question.option_a_si = form_data["option_a"]
    question.option_b_en = form_data["option_b"]
    question.option_b_si = form_data["option_b"]
    question.option_c_en = form_data["option_c"]
    question.option_c_si = form_data["option_c"]
    question.option_d_en = form_data["option_d"]
    question.option_d_si = form_data["option_d"]
    question.question_type = form_data["question_type"]
    question.correct_answer_text = form_data["correct_answer_text"] or None
    question.box_template = form_data["box_template"] or None
    question.box_answers = form_data["box_answers"] or None
    question.matching_left_en = form_data["matching_left_en"] or None
    question.matching_right_en = form_data["matching_right_en"] or None
    question.matching_answers_en = form_data["matching_answers_en"] or None
    question.matching_left_si = form_data["matching_left_si"] or None
    question.matching_right_si = form_data["matching_right_si"] or None
    question.matching_answers_si = form_data["matching_answers_si"] or None
    question.tap_areas_json = form_data["tap_areas_json"] or None
    question.correct_area_id = form_data["correct_area_id"] or None
    question.drag_items_json = form_data["drag_items_json"] or None
    question.drag_container_image_url = form_data["drag_container_image_url"] or None
    question.drag_groups_json = form_data["drag_groups_json"] or None
    question.image_url = uploaded_image_url or form_data["image_url"] or question.image_url
    question.correct_option = form_data["correct_option"]
    question.difficulty_level = form_data["difficulty_level"]
    db.session.commit()
    return redirect("/admin/questions")


@app.route("/admin/delete-question/<int:question_id>", methods=["GET"])
def admin_delete_question(question_id: int):
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    question = Question.query.get_or_404(question_id)
    db.session.delete(question)
    db.session.commit()
    return redirect("/admin/questions")


@app.route("/api/subjects", methods=["GET"])
def api_subjects():
    grade = request.args.get("grade")
    medium = resolve_medium(request.args.get("medium"))
    subjects = get_subjects_for_grade(grade, active_only=True)
    return jsonify(
        {
            "subjects": [
                {
                    "id": item.id,
                    "grade": item.grade,
                    "subject_code": item.subject_code,
                    "subject_name_en": item.subject_name_en,
                    "image_url": (
                        (item.image_si_url or "").strip()
                        if medium == "Sinhala"
                        else (item.image_en_url or "").strip()
                    ) or "/static/images/subjects/default-subject.jpg",
                }
                for item in subjects
            ]
        }
    )


@app.route("/api/syllabus/terms", methods=["GET"])
def api_syllabus_terms():
    grade = request.args.get("grade")
    subject_id = request.args.get("subject")
    app.logger.error("ADD QUESTION DEBUG grade=%s subject_id=%s", grade, subject_id)
    terms = _syllabus_terms_for_grade_subject(grade, subject_id)
    app.logger.error("TERMS FOUND=%s", len(terms))
    return jsonify({"terms": [{"id": t.id, "label": f"T{t.term_number} - {t.term_name_en}"} for t in terms]})


@app.route("/api/syllabus/modules", methods=["GET"])
def api_syllabus_modules():
    term_id_raw = (request.args.get("term_id") or "").strip()
    term_id = int(term_id_raw) if term_id_raw.isdigit() else 0
    modules = SyllabusModule.query.filter_by(term_id=term_id).order_by(SyllabusModule.module_order.asc()).all() if term_id else []
    return jsonify({"modules": [{"id": m.id, "label": f"{m.module_order} - {m.module_name_en}"} for m in modules]})


@app.route("/api/syllabus/chapters", methods=["GET"])
def api_syllabus_chapters():
    module_id_raw = (request.args.get("module_id") or "").strip()
    module_id = int(module_id_raw) if module_id_raw.isdigit() else 0
    chapters = (
        SyllabusChapter.query.filter_by(module_id=module_id, is_active=True).order_by(SyllabusChapter.chapter_order.asc()).all()
        if module_id
        else []
    )
    return jsonify({"chapters": [{"id": c.id, "label": f"{c.chapter_order} - {c.chapter_name_en}"} for c in chapters]})


@app.route("/admin/generate-questions", methods=["GET", "POST"])
def admin_generate_questions():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    if request.method == "GET":
        return f"""
        <h2>Generate Questions (Bulk)</h2>
        <form method='post' action='/admin/generate-questions'>
          <label>Grade: <select name='grade' required>{grade_options_html('6')}</select></label><br><br>
          <label>Subject: <select name='subject' required>{subject_options_html('6', 'Math')}</select></label><br><br>
          <label>Term: <select name='term_id'><option value=''>Select term</option></select></label><br><br>
          <label>Module: <select name='module_id'><option value=''>Select module</option></select></label><br><br>
          <label>Chapter: <select name='chapter_id'><option value=''>Select chapter</option></select></label><br><br>
          <p id='syllabus-debug-message' style='color:#b45309;'></p>
          <label>Topic: <input type='text' name='topic' value='Fractions' required></label><br><br>
          <label>Number of questions: <input type='number' name='question_count' min='1' max='200' value='10' required></label><br><br>
          <label>Difficulty level (1–5): <input type='number' name='difficulty_level' min='1' max='5' value='1' required></label><br><br>
          <button type='submit'>Generate</button>
        </form>
        <p><a href='/admin/questions'>Back to Questions</a></p>
        {dependent_dropdown_script()}
        """

    grade = (request.form.get("grade") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    topic = (request.form.get("topic") or "").strip()
    try:
        question_count = int((request.form.get("question_count") or "0").strip())
    except ValueError:
        return "<h3>Number of questions must be a valid number.</h3>", 400

    try:
        difficulty_level = int((request.form.get("difficulty_level") or "0").strip())
    except ValueError:
        return "<h3>Difficulty level must be between 1 and 5.</h3>", 400

    if not grade or not subject or not topic:
        return "<h3>Grade, Subject, and Topic are required.</h3>", 400
    if question_count < 1 or question_count > 200:
        return "<h3>Number of questions must be between 1 and 200.</h3>", 400
    if difficulty_level < 1 or difficulty_level > 5:
        return "<h3>Difficulty level must be between 1 and 5.</h3>", 400

    for _ in range(question_count):
        db.session.add(Question(**build_generated_question(grade, subject, topic, difficulty_level)))

    db.session.commit()
    return jsonify(
        {
            "success": True,
            "message": f"{question_count} questions generated successfully",
            "created_count": question_count,
        }
    ), 201


@app.route("/admin/ai-generate", methods=["GET", "POST"])
def admin_ai_generate_questions():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    if request.method == "GET":
        return f"""
        <h2>AI Question Generator</h2>
        <form method='post' action='/admin/ai-generate'>
          <label>Grade: <select name='grade' required>{grade_options_html('6')}</select></label><br><br>
          <label>Subject: <select name='subject' required>{subject_options_html('6', 'Math')}</select></label><br><br>
          <label>Term: <select name='term_id'><option value=''>Select term</option></select></label><br><br>
          <label>Module: <select name='module_id'><option value=''>Select module</option></select></label><br><br>
          <label>Chapter: <select name='chapter_id'><option value=''>Select chapter</option></select></label><br><br>
          <p id='syllabus-debug-message' style='color:#b45309;'></p>
          <label>Topic: <input type='text' name='topic' value='Fractions' required></label><br><br>
          <label>Number of questions: <input type='number' name='question_count' min='1' max='100' value='10' required></label><br><br>
          <label>Difficulty level (1–5): <input type='number' name='difficulty_level' min='1' max='5' value='1' required></label><br><br>
          <button type='submit'>Generate with AI</button>
        </form>
        <p><a href='/admin/questions'>Back to Questions</a></p>
        {dependent_dropdown_script()}
        """

    grade = (request.form.get("grade") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    topic = (request.form.get("topic") or "").strip()
    try:
        question_count = int((request.form.get("question_count") or "0").strip())
    except ValueError:
        return "<h3>Number of questions must be a valid number.</h3>", 400

    try:
        difficulty_level = int((request.form.get("difficulty_level") or "0").strip())
    except ValueError:
        return "<h3>Difficulty level must be between 1 and 5.</h3>", 400

    if not grade or not subject or not topic:
        return "<h3>Grade, Subject, and Topic are required.</h3>", 400
    if question_count < 1 or question_count > 100:
        return "<h3>Number of questions must be between 1 and 100.</h3>", 400
    if difficulty_level < 1 or difficulty_level > 5:
        return "<h3>Difficulty level must be between 1 and 5.</h3>", 400

    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return jsonify({"success": False, "message": "OpenAI API key is not configured"}), 500

    prompt = (
        f"Generate {question_count} multiple choice questions for Grade {grade} {subject} on topic {topic}.\n"
        f"Difficulty level: {difficulty_level}.\n\n"
        "Return JSON format:\n"
        "[\n"
        "{\n"
        "question_en: \"...\",\n"
        "question_si: \"...\",\n"
        "option_a_en: \"...\",\n"
        "option_a_si: \"...\",\n"
        "option_b_en: \"...\",\n"
        "option_b_si: \"...\",\n"
        "option_c_en: \"...\",\n"
        "option_c_si: \"...\",\n"
        "option_d_en: \"...\",\n"
        "option_d_si: \"...\",\n"
        "correct_option: \"A/B/C/D\",\n"
        "explanation_en: \"...\",\n"
        "explanation_si: \"...\"\n"
        "}\n"
        "]\n\n"
        "Ensure:\n"
        "- Correct answers are accurate\n"
        "- Sinhala is simple and correct\n"
        "- Questions are unique\n"
        "- No repetition\n"
        "- Use plain fractions only (examples: 3/4, 8/9, 1/6)\n"
        "- Do not use \\( \\), \\frac{}, or any LaTeX formatting\n"
    )

    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
            input=prompt,
            temperature=0.2,
        )
        questions = parse_ai_questions_payload(response.output_text)
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Failed to generate AI questions safely: {exc}"}), 502

    if len(questions) != question_count:
        return jsonify({"success": False, "message": f"AI returned {len(questions)} questions, expected {question_count}"}), 400

    for item in questions:
        question_en = normalize_fraction_text(item["question_en"])
        question_si = normalize_fraction_text(item["question_si"])
        option_a_en = normalize_fraction_text(item["option_a_en"])
        option_a_si = normalize_fraction_text(item["option_a_si"])
        option_b_en = normalize_fraction_text(item["option_b_en"])
        option_b_si = normalize_fraction_text(item["option_b_si"])
        option_c_en = normalize_fraction_text(item["option_c_en"])
        option_c_si = normalize_fraction_text(item["option_c_si"])
        option_d_en = normalize_fraction_text(item["option_d_en"])
        option_d_si = normalize_fraction_text(item["option_d_si"])
        explanation_en = normalize_fraction_text(item["explanation_en"])
        explanation_si = normalize_fraction_text(item["explanation_si"])
        db.session.add(
            Question(
                grade=grade,
                subject=subject,
                topic=topic,
                topic_en=topic,
                topic_si=topic,
                question_text_en=question_en,
                question_text_si=question_si,
                option_a_en=option_a_en,
                option_a_si=option_a_si,
                option_b_en=option_b_en,
                option_b_si=option_b_si,
                option_c_en=option_c_en,
                option_c_si=option_c_si,
                option_d_en=option_d_en,
                option_d_si=option_d_si,
                correct_option=item["correct_option"].strip().upper(),
                explanation_en=explanation_en,
                explanation_si=explanation_si,
                difficulty_level=difficulty_level,
            )
        )

    db.session.commit()
    return jsonify({"success": True, "message": f"{question_count} AI questions created successfully"}), 201


@app.route("/admin-logout", methods=["GET"])
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


@app.route("/update-syllabus-db", methods=["GET"])
def update_syllabus_db() -> tuple[str, int]:
    try:
        SyllabusTerm.__table__.create(bind=db.engine, checkfirst=True)
        SyllabusModule.__table__.create(bind=db.engine, checkfirst=True)
        SyllabusChapter.__table__.create(bind=db.engine, checkfirst=True)
        for col_def in [
            "ALTER TABLE question ADD COLUMN IF NOT EXISTS term_id INTEGER",
            "ALTER TABLE question ADD COLUMN IF NOT EXISTS module_id INTEGER",
            "ALTER TABLE question ADD COLUMN IF NOT EXISTS chapter_id INTEGER",
            "ALTER TABLE question ADD COLUMN IF NOT EXISTS chapter_en VARCHAR(150)",
            "ALTER TABLE question ADD COLUMN IF NOT EXISTS chapter_si VARCHAR(150)",
            "ALTER TABLE homework_assignment ADD COLUMN IF NOT EXISTS term_id INTEGER",
            "ALTER TABLE homework_assignment ADD COLUMN IF NOT EXISTS module_id INTEGER",
            "ALTER TABLE homework_assignment ADD COLUMN IF NOT EXISTS chapter_id INTEGER",
            "ALTER TABLE class_test ADD COLUMN IF NOT EXISTS term_id INTEGER",
            "ALTER TABLE class_test ADD COLUMN IF NOT EXISTS module_id INTEGER",
            "ALTER TABLE class_test ADD COLUMN IF NOT EXISTS chapter_id INTEGER",
            "ALTER TABLE syllabus_module ADD COLUMN IF NOT EXISTS image_si_url TEXT",
            "ALTER TABLE syllabus_module ADD COLUMN IF NOT EXISTS image_en_url TEXT",
        ]:
            db.session.execute(db.text(col_def))
        db.session.commit()
        return "Syllabus database updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return f"Syllabus DB update failed: {exc}", 500


@app.route("/update-subject-master-db", methods=["GET"])
def update_subject_master_db() -> tuple:
    try:
        SubjectMaster.__table__.create(bind=db.engine, checkfirst=True)
        db.session.execute(db.text("ALTER TABLE subject_master ADD COLUMN IF NOT EXISTS image_si_url TEXT"))
        db.session.execute(db.text("ALTER TABLE subject_master ADD COLUMN IF NOT EXISTS image_en_url TEXT"))
        db.session.commit()
        return jsonify({"success": True, "message": "Subject master database updated successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Subject master DB update failed: {exc}"}), 500


@app.route("/seed-basic-subjects", methods=["GET"])
def seed_basic_subjects() -> tuple:
    try:
        SubjectMaster.__table__.create(bind=db.engine, checkfirst=True)
        existing = SubjectMaster.query.filter_by(grade="6", subject_code="MATH").first()
        if not existing:
            db.session.add(SubjectMaster(grade="6", subject_code="MATH", subject_name_en="Math", subject_name_si="ගණිතය", is_active=True))
            db.session.commit()
            return jsonify({"success": True, "message": "Seeded Grade 6 Math subject"}), 200
        return jsonify({"success": True, "message": "Grade 6 Math subject already exists"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Seed failed: {exc}"}), 500


@app.route("/update-login-db", methods=["GET"])
def update_login_db() -> tuple:
    try:
        db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)"))
        ensure_student_username_schema()
        db.session.commit()
        return jsonify({"success": True, "message": "Login database updated successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Login DB update failed: {exc}"}), 500




@app.route("/update-parent-link-db", methods=["GET"])
def update_parent_link_db() -> tuple:
    try:
        db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS parent_email VARCHAR(120)"))
        db.session.commit()
        return jsonify({"success": True, "message": "Parent link database updated successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Parent link DB update failed: {exc}"}), 500



@app.route("/update-class-db", methods=["GET"])
def update_class_db() -> tuple:
    try:
        Class.__table__.create(bind=db.engine, checkfirst=True)
        inspector = db.inspect(db.engine)
        student_columns = {col["name"] for col in inspector.get_columns("student")}
        if "class_id" not in student_columns:
            db.session.execute(db.text("ALTER TABLE student ADD COLUMN class_id INTEGER"))
        db.session.commit()
        return jsonify({"success": True, "message": "Class database updated successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Class DB update failed: {exc}"}), 500


@app.route("/update-school-db", methods=["GET"])
def update_school_db() -> tuple[str, int]:
    try:
        db.create_all()
        School.__table__.create(bind=db.engine, checkfirst=True)
        SchoolAdmin.__table__.create(bind=db.engine, checkfirst=True)
        Teacher.__table__.create(bind=db.engine, checkfirst=True)
        db.session.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS school_admin (
                    id SERIAL PRIMARY KEY,
                    school_id INTEGER NOT NULL,
                    name VARCHAR(120) NOT NULL,
                    email VARCHAR(120) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        inspector = db.inspect(db.engine)
        student_columns = {col["name"] for col in inspector.get_columns("student")}
        if "school_id" not in student_columns:
            db.session.execute(db.text("ALTER TABLE student ADD COLUMN school_id INTEGER"))
        teacher_columns = {col["name"] for col in inspector.get_columns("teacher")}
        if "school_id" not in teacher_columns:
            db.session.execute(db.text("ALTER TABLE teacher ADD COLUMN school_id INTEGER"))
        first_school = School.query.order_by(School.id.asc()).first()
        if not first_school:
            first_school = School(school_name="Default School")
            db.session.add(first_school)
            db.session.flush()
        db.session.execute(db.text("UPDATE student SET school_id = :school_id WHERE school_id IS NULL"), {"school_id": first_school.id})
        db.session.execute(db.text("UPDATE teacher SET school_id = :school_id WHERE school_id IS NULL"), {"school_id": first_school.id})
        db.session.commit()
        return "School database updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"School DB update failed: {exc}"}), 500


@app.route("/update-gamification-db", methods=["GET"])
def update_gamification_db() -> tuple[str, int]:
    try:
        ensure_gamification_columns()
        return "Gamification columns updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Gamification DB update failed: {exc}"}), 500



@app.route("/update-streak-db", methods=["GET"])
def update_streak_db() -> tuple[str, int]:
    try:
        ensure_streak_columns()
        return "Streak columns updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Streak DB update failed: {exc}"}), 500


@app.route("/update-subscription-db", methods=["GET"])
def update_subscription_db() -> tuple[str, int]:
    try:
        ensure_subscription_columns()
        return "Subscription columns updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Subscription DB update failed: {exc}"}), 500


@app.route("/update-db", methods=["GET"])
def update_db() -> tuple:
    try:
        db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS medium VARCHAR(20)"))
        ensure_gamification_columns()
        db.session.execute(db.text("UPDATE student SET medium = 'English' WHERE medium IS NULL"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS topic_en VARCHAR(150)"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS topic_si VARCHAR(150)"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS difficulty_level INTEGER DEFAULT 1"))
        db.session.execute(db.text("UPDATE question SET topic_en = topic WHERE topic_en IS NULL"))
        db.session.execute(db.text("UPDATE question SET topic_si = topic WHERE topic_si IS NULL"))
        db.session.execute(db.text("UPDATE question SET difficulty_level = 1 WHERE difficulty_level IS NULL"))
        db.session.commit()
        return jsonify({"success": True, "message": "Database updated successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Database update failed: {exc}"}), 500





@app.route("/update-difficulty-db", methods=["GET"])
def update_difficulty_db() -> tuple[str, int]:
    try:
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS difficulty_level INTEGER"))
        db.session.execute(db.text("UPDATE question SET difficulty_level = 1 WHERE difficulty_level IS NULL"))
        db.session.commit()
        return "Difficulty column updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Difficulty column update failed: {exc}"}), 500


@app.route("/update-question-difficulty-db", methods=["GET"])
def update_question_difficulty_db() -> tuple[str, int]:
    try:
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS difficulty_level INTEGER DEFAULT 1"))
        db.session.execute(db.text("UPDATE question SET difficulty_level = 1 WHERE difficulty_level IS NULL"))
        db.session.commit()
        return "Question difficulty column updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Question difficulty DB update failed: {exc}"}), 500
@app.route("/update-question-topics-db", methods=["GET"])
def update_question_topics_db() -> tuple:
    try:
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS topic_en VARCHAR(150)"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS topic_si VARCHAR(150)"))
        db.session.execute(db.text("UPDATE question SET topic_en = topic WHERE topic_en IS NULL OR topic_en = ''"))

        topic_mapping = {
            "Fractions": "භාග",
            "Decimals": "දශම",
            "Perimeter": "පරිමිතිය",
            "Factors": "සාධක",
            "Percentages": "ප්‍රතිශත",
        }
        for topic_en, topic_si in topic_mapping.items():
            db.session.execute(
                db.text(
                    "UPDATE question "
                    "SET topic_si = :topic_si "
                    "WHERE topic = :topic_en AND (topic_si IS NULL OR topic_si = '')"
                ),
                {"topic_en": topic_en, "topic_si": topic_si},
            )

        db.session.execute(db.text("UPDATE question SET topic_si = topic WHERE topic_si IS NULL OR topic_si = ''"))
        db.session.commit()
        return "Question topic columns updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Question topic column update failed: {exc}"}), 500


@app.route("/update-question-format-db", methods=["GET"])
def update_question_format_db() -> tuple[str, int]:
    try:
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS question_type VARCHAR(20) DEFAULT 'mcq'"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS correct_answer_text TEXT"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS image_url TEXT"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS tap_areas_json TEXT"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS correct_area_id VARCHAR(100)"))
        db.session.execute(db.text("UPDATE question SET question_type = 'mcq' WHERE question_type IS NULL"))
        db.session.commit()
        return "Question format database updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Question format DB update failed: {exc}"}), 500


@app.route("/update-box-question-db", methods=["GET"])
def update_box_question_db() -> tuple[str, int]:
    try:
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS question_type VARCHAR(20) DEFAULT 'mcq'"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS box_template TEXT"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS box_answers TEXT"))
        db.session.execute(db.text("UPDATE question SET question_type = 'mcq' WHERE question_type IS NULL"))
        db.session.commit()
        return "Box question database updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Box question DB update failed: {exc}"}), 500



@app.route("/update-matching-pairs-db", methods=["GET"])
def update_matching_pairs_db() -> tuple[str, int]:
    try:
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS question_type VARCHAR(20) DEFAULT 'mcq'"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS matching_left_en TEXT"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS matching_right_en TEXT"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS matching_answers_en TEXT"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS matching_left_si TEXT"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS matching_right_si TEXT"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS matching_answers_si TEXT"))
        db.session.execute(db.text("UPDATE question SET question_type = 'mcq' WHERE question_type IS NULL"))
        db.session.commit()
        return "Matching pairs database updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Matching pairs DB update failed: {exc}"}), 500


@app.route("/update-drag-drop-db", methods=["GET"])
def update_drag_drop_db() -> tuple[str, int]:
    try:
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS question_type VARCHAR(50) DEFAULT 'mcq'"))
        db.session.execute(db.text("ALTER TABLE question ALTER COLUMN question_type TYPE VARCHAR(50)"))
        db.session.execute(db.text("UPDATE question SET question_type = 'mcq' WHERE question_type IS NULL"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS drag_items_json TEXT"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS drag_container_image_url TEXT"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS drag_groups_json TEXT"))
        db.session.commit()
        return "Drag drop database updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Drag drop DB update failed: {exc}"}), 500

@app.route("/update-results-db", methods=["GET"])
def update_results_db() -> tuple:
    try:
        db.create_all()
        db.session.commit()
        return jsonify({"success": True, "message": "Result tables ensured successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Results DB update failed: {exc}"}), 500




@app.route("/update-video-interaction-db", methods=["GET"])
def update_video_interaction_db():
    try:
        db.create_all()
        db.session.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS video_interaction (
                    id SERIAL PRIMARY KEY,
                    content_id INTEGER NOT NULL REFERENCES chapter_learning_content(id),
                    question_id INTEGER NOT NULL REFERENCES question(id),
                    trigger_seconds INTEGER NOT NULL,
                    pause_video BOOLEAN NOT NULL DEFAULT TRUE,
                    required_answer BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        db.session.commit()
        return "Video interaction database updated successfully", 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Video interaction DB update failed: {exc}"}), 500



@app.route("/update-lesson-engine-db", methods=["GET"])
def update_lesson_engine_db():
    try:
        ensure_lesson_engine_tables()
        return jsonify({"success": True, "message": "Lesson engine tables ensured successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Lesson engine DB update failed: {exc}"}), 500

@app.route("/update-chapter-learning-db", methods=["GET"])
def update_chapter_learning_db():
    try:
        ensure_chapter_learning_tables()
        return jsonify({"success": True, "message": "Chapter learning tables ensured successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Chapter learning DB update failed: {exc}"}), 500


@app.route("/questions", methods=["POST"])
def create_question():
    data = request.get_json(silent=True) or {}
    required_fields = [
        "grade",
        "subject",
        "topic",
        "topic_en",
        "topic_si",
        "question_text_en",
        "question_text_si",
        "option_a_en",
        "option_a_si",
        "option_b_en",
        "option_b_si",
        "option_c_en",
        "option_c_si",
        "option_d_en",
        "option_d_si",
        "correct_option",
        "explanation_en",
        "explanation_si",
    ]
    missing_fields = [field for field in required_fields if not str(data.get(field, "")).strip()]
    if missing_fields:
        return jsonify({"success": False, "message": f"Missing required fields: {', '.join(missing_fields)}"}), 400

    correct_option = data["correct_option"].strip().upper()
    if correct_option not in {"A", "B", "C", "D"}:
        return jsonify({"success": False, "message": "correct_option must be one of A, B, C, D"}), 400

    payload = {field: data[field].strip() for field in required_fields if field != "correct_option"}
    difficulty_level = int(data.get("difficulty_level", 1))
    if difficulty_level < 1 or difficulty_level > 5:
        return jsonify({"success": False, "message": "difficulty_level must be between 1 and 5"}), 400
    if not payload.get("topic"):
        payload["topic"] = payload.get("topic_en") or payload.get("topic_si")
    question = Question(**payload, correct_option=correct_option, difficulty_level=difficulty_level)
    db.session.add(question)
    db.session.commit()

    return jsonify({"success": True, "question_id": question.id}), 201


@app.route("/create-questions", methods=["GET"])
def create_questions() -> tuple:
    sample_questions = [
        {
            "grade": "6",
            "subject": "Math",
            "topic": "Fractions",
            "topic_en": "Fractions",
            "topic_si": "භාග",
            "question_text_en": "What is 1/2 + 1/4?",
            "question_text_si": "1/2 + 1/4 කීයද?",
            "option_a_en": "1/2",
            "option_a_si": "1/2",
            "option_b_en": "3/4",
            "option_b_si": "3/4",
            "option_c_en": "2/6",
            "option_c_si": "2/6",
            "option_d_en": "1",
            "option_d_si": "1",
            "correct_option": "B",
            "explanation_en": "Convert to a common denominator: 1/2 = 2/4, then 2/4 + 1/4 = 3/4.",
            "explanation_si": "එකම හරණයට මාරු කරන්න: 1/2 = 2/4, එවිට 2/4 + 1/4 = 3/4.",
        },
        {
            "grade": "6",
            "subject": "Math",
            "topic": "Decimals",
            "topic_en": "Decimals",
            "topic_si": "දශම",
            "question_text_en": "What is 3.5 + 2.4?",
            "question_text_si": "3.5 + 2.4 කීයද?",
            "option_a_en": "5.9",
            "option_a_si": "5.9",
            "option_b_en": "6.0",
            "option_b_si": "6.0",
            "option_c_en": "5.7",
            "option_c_si": "5.7",
            "option_d_en": "6.9",
            "option_d_si": "6.9",
            "correct_option": "A",
            "explanation_en": "Add the decimals by place value: 3.5 + 2.4 = 5.9.",
            "explanation_si": "ස්ථාන අගය අනුව දශම එකතු කළാൽ: 3.5 + 2.4 = 5.9.",
        },
        {
            "grade": "6",
            "subject": "Math",
            "topic": "Perimeter",
            "topic_en": "Perimeter",
            "topic_si": "පරිමිතිය",
            "question_text_en": "A rectangle has length 8 cm and width 3 cm. What is its perimeter?",
            "question_text_si": "දිග 8 cm සහ පළල 3 cm වන සෘජුකෝණාස්‍රයක පරිමාව කීයද?",
            "option_a_en": "11 cm",
            "option_a_si": "11 cm",
            "option_b_en": "16 cm",
            "option_b_si": "16 cm",
            "option_c_en": "22 cm",
            "option_c_si": "22 cm",
            "option_d_en": "24 cm",
            "option_d_si": "24 cm",
            "correct_option": "C",
            "explanation_en": "Perimeter of a rectangle is 2(l+w) = 2(8+3) = 22 cm.",
            "explanation_si": "සෘජුකෝණාස්‍රයක පරිමාව 2(දිග+පළල) = 2(8+3) = 22 cm.",
        },
        {
            "grade": "6",
            "subject": "Math",
            "topic": "Factors",
            "topic_en": "Factors",
            "topic_si": "ගුණක",
            "question_text_en": "Which number is a factor of 24?",
            "question_text_si": "24 හි ගුණකයක් වන්නේ කුමක්ද?",
            "option_a_en": "5",
            "option_a_si": "5",
            "option_b_en": "7",
            "option_b_si": "7",
            "option_c_en": "9",
            "option_c_si": "9",
            "option_d_en": "6",
            "option_d_si": "6",
            "correct_option": "D",
            "explanation_en": "24 ÷ 6 = 4 with no remainder, so 6 is a factor.",
            "explanation_si": "24 ÷ 6 = 4 ඉතිරියක් නැති නිසා 6 ගුණකයකි.",
        },
        {
            "grade": "6",
            "subject": "Math",
            "topic": "Percentages",
            "topic_en": "Percentages",
            "topic_si": "ප්‍රතිශත",
            "question_text_en": "What is 10% of 150?",
            "question_text_si": "150 හි 10% කීයද?",
            "option_a_en": "10",
            "option_a_si": "10",
            "option_b_en": "12",
            "option_b_si": "12",
            "option_c_en": "15",
            "option_c_si": "15",
            "option_d_en": "20",
            "option_d_si": "20",
            "correct_option": "C",
            "explanation_en": "10% means one-tenth: 150 ÷ 10 = 15.",
            "explanation_si": "10% යනු දහයෙන් එකක්: 150 ÷ 10 = 15.",
        },
    ]

    created_ids = []
    for data in sample_questions:
        question = Question(**data)
        db.session.add(question)
        db.session.flush()
        created_ids.append(question.id)

    db.session.commit()
    return jsonify({"success": True, "created_count": len(created_ids), "question_ids": created_ids}), 201


@app.route("/test", methods=["GET"])
def test_page() -> str:
    db.create_all()
    student_id = session.get("student_id")
    student = db.session.get(Student, student_id) if student_id else None
    if student:
        selected_grade = normalize_grade(student.grade)
        selected_medium = resolve_medium(student.medium)
        selected_subject = "Math"
    else:
        selected_grade = normalize_grade(request.args.get("grade") or "6")
        if not is_valid_grade(selected_grade):
            selected_grade = "6"
        selected_medium = resolve_medium(request.args.get("medium"))
        selected_subject = (request.args.get("subject") or "Math").strip() or "Math"

    questions = (
        Question.query.filter_by(grade=selected_grade, subject=selected_subject)
        .order_by(Question.id.asc())
        .all()
    )
    medium_key = "en" if selected_medium == "English" else "si"
    streak_message = ""

    question_blocks = []
    for q in questions:
        question_text = getattr(q, f"question_text_{medium_key}")
        image_html = f"<img src='{escape(normalize_local_image_url(q.image_url))}' alt='Question image' class='question-image'>" if q.image_url else ""
        if is_matching_pairs_question(q):
            answer_html = render_matching_pairs_inputs(q, medium_key)
        elif is_box_input_question(q):
            answer_html = render_box_template_with_inputs(q, 'qbox')
        elif is_tap_select_image_question(q):
            answer_html = render_tap_select_image_input(q)
        elif is_drag_drop_group_container_question(q):
            answer_html = render_drag_drop_group_container_input(q, medium_key)
        elif is_short_answer_question(q):
            answer_html = f"<input type='text' name='q_{q.id}' placeholder='Type your answer'>"
        else:
            option_a = getattr(q, f"option_a_{medium_key}")
            option_b = getattr(q, f"option_b_{medium_key}")
            option_c = getattr(q, f"option_c_{medium_key}")
            option_d = getattr(q, f"option_d_{medium_key}")
            answer_html = f"<label><input type='radio' name='q_{q.id}' value='A'> A. {option_a}</label><br><label><input type='radio' name='q_{q.id}' value='B'> B. {option_b}</label><br><label><input type='radio' name='q_{q.id}' value='C'> C. {option_c}</label><br><label><input type='radio' name='q_{q.id}' value='D'> D. {option_d}</label>"
        question_blocks.append(f"<div style='margin:16px 0;padding:12px;border:1px solid #ddd;'><p><strong>Q{q.id}.</strong> {question_text}</p>{'' if (is_tap_select_image_question(q) or is_drag_drop_group_container_question(q)) else image_html}{answer_html}</div>")

    show_language_controls = student is None
    language_controls_html = f"""
        <form method='get' action='/test' style='margin-bottom:20px;'>
          <label>{t(selected_medium, 'language')}:
            <select name='medium'>
              <option value='English' {'selected' if selected_medium == 'English' else ''}>English</option>
              <option value='Sinhala' {'selected' if selected_medium == 'Sinhala' else ''}>Sinhala</option>
            </select>
          </label>
          <button type='submit'>{t(selected_medium, 'change_language')}</button>
        </form>
    """ if show_language_controls else ""

    return f"""
    <!doctype html>
    <html lang='{'si' if selected_medium == 'Sinhala' else 'en'}'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>{t(selected_medium, 'test_title').format(grade=selected_grade, subject=selected_subject)}</title>
        <style>
          .box-layout {{font-family:monospace;white-space:pre;line-height:1.4;}}
          .box-input {{width:14px;height:14px;min-width:14px;padding:1px;text-align:center;font-size:12px;line-height:12px;border:1.5px solid #000;border-radius:2px;display:inline-block;vertical-align:middle;margin:0 1px;font-family:monospace;box-sizing:border-box;}}
          .question-image {{
            max-width: 250px;
            width: 100%;
            height: auto;
            display: block;
            margin: 10px 0;
            border: 1px solid #ddd;
            border-radius: 6px;
          }}
          @media (max-width: 768px) {{
            .box-layout {{font-family:monospace;white-space:pre;line-height:1.4;}}
          .box-input {{width:14px;height:14px;min-width:14px;padding:1px;text-align:center;font-size:12px;line-height:12px;border:1.5px solid #000;border-radius:2px;display:inline-block;vertical-align:middle;margin:0 1px;font-family:monospace;box-sizing:border-box;}}
          .question-image {{
              max-width: 180px;
            }}
          }}
        </style>
        {tap_select_common_assets()}
        {drag_drop_group_assets()}
      </head>
      <body>
        <h1>{t(selected_medium, 'test_title').format(grade=selected_grade, subject=selected_subject)}</h1>
        {language_controls_html}
        <form method='post' action='/submit-test'>
          <input type='hidden' name='grade' value='{selected_grade}'>
          <input type='hidden' name='subject' value='{escape(selected_subject)}'>
          <input type='hidden' name='medium' value='{selected_medium}'>
          <p>{t(selected_medium, 'selected_language')}: <strong>{selected_medium}</strong></p>
          {''.join(question_blocks) if question_blocks else f"<p>{t(selected_medium, 'no_questions').format(grade=selected_grade)}</p>"}
          <button type='submit'>{t(selected_medium, 'submit')}</button>
        </form>
      </body>
    </html>
    """

def update_student_xp_and_level(student_id: int | None, earned_xp: int) -> tuple[int, int]:
    if not student_id:
        return 0, 1

    student = db.session.get(Student, student_id)
    if not student:
        return 0, 1

    student.xp = (student.xp or 0) + earned_xp
    student.level = (student.xp // 100) + 1
    return student.xp, student.level





@app.route("/submit-test", methods=["POST"])
def submit_test() -> str:
    db.create_all()
    student_id = session.get("student_id")
    student = db.session.get(Student, student_id) if student_id else None
    if student:
        selected_grade = normalize_grade(student.grade)
        selected_medium = resolve_medium(student.medium)
        selected_subject = "Math"
    else:
        selected_grade = normalize_grade(request.form.get("grade") or request.args.get("grade") or "6")
        if not is_valid_grade(selected_grade):
            selected_grade = "6"
        selected_medium = resolve_medium(request.form.get("medium") or request.args.get("medium"))
        selected_subject = (request.form.get("subject") or request.args.get("subject") or "Math").strip() or "Math"
    streak_message = ""

    medium_key = "en" if selected_medium == "English" else "si"
    questions = (
        Question.query.filter_by(grade=selected_grade, subject=selected_subject)
        .order_by(Question.id.asc())
        .all()
    )

    total_questions = len(questions)
    correct_answers = 0
    wrong_answer_rows = []
    option_label_key = {"A": "option_a", "B": "option_b", "C": "option_c", "D": "option_d"}
    topic_stats = {}

    for q in questions:
        topic_name = q.topic_en if selected_medium == "English" else q.topic_si
        if not topic_name:
            topic_name = q.topic
        topic_stats.setdefault(
            topic_name,
            {
                "total": 0,
                "correct": 0,
                "topic_en": q.topic_en or q.topic,
                "topic_si": q.topic_si or q.topic,
            },
        )
        topic_stats[topic_name]["total"] += 1
        if is_matching_pairs_question(q):
            is_correct, student_pair_answers, correct_pair_answers = evaluate_matching_pairs_question(q, request.form, medium_key)
            student_answer = json.dumps(student_pair_answers, ensure_ascii=False)
            correct_answer = json.dumps(correct_pair_answers, ensure_ascii=False)
        elif is_box_input_question(q):
            is_correct, student_box_answers, correct_box_answers = evaluate_box_question(q, request.form)
            student_answer = json.dumps(student_box_answers, ensure_ascii=False)
            correct_answer = json.dumps(correct_box_answers, ensure_ascii=False)
        elif is_tap_select_image_question(q):
            is_correct, student_answer, correct_answer = evaluate_tap_select_question(q, request.form)
        elif is_drag_drop_group_container_question(q):
            is_correct, student_answer = evaluate_drag_drop_group_container_question(q, request.form)
            correct_answer = 'Grouped in basket'
        elif is_short_answer_question(q):
            student_answer = request.form.get(f"q_{q.id}", "").strip()
            correct_answer = (q.correct_answer_text or "").strip()
            is_correct = bool(correct_answer) and student_answer.casefold() == correct_answer.casefold()
        else:
            student_answer = request.form.get(f"q_{q.id}", "").strip().upper()
            correct_answer = q.correct_option.strip().upper()
            is_correct = student_answer == correct_answer

        if is_correct:
            correct_answers += 1
            topic_stats[topic_name]["correct"] += 1
        db.session.add(
            StudentQuestionAttempt(
                student_id=session.get("student_id"),
                question_id=q.id,
                source_type="SkillScan",
                is_correct=is_correct,
            )
        )
        if is_correct:
            continue

        question_text = getattr(q, f"question_text_{medium_key}")
        explanation_text = getattr(q, f"explanation_{medium_key}")

        if is_matching_pairs_question(q):
            student_map = json.loads(student_answer or "{}")
            correct_map = json.loads(correct_answer or "{}")
            parts = [f"<div><strong>{escape(k)}</strong><br>Your Answer: {escape(student_map.get(k) or t(selected_medium, 'not_answered'))}<br>Correct Answer: {escape(v)}</div>" for k, v in correct_map.items()]
            student_answer_text = "".join(parts) or t(selected_medium, "not_answered")
            correct_answer_text = "-"
        elif is_box_input_question(q):
            student_map = json.loads(student_answer or '{}')
            correct_map = json.loads(correct_answer or '{}')
            parts = []
            for k, v in correct_map.items():
                sval = student_map.get(k, '')
                ok = sval.strip().casefold() == str(v).strip().casefold()
                color = '#16a34a' if ok else '#dc2626'
                parts.append(f"<div><strong>{k}</strong>: <span style='color:{color}'>{escape(sval or '-')}</span> / {escape(str(v))}</div>")
            student_answer_text = ''.join(parts) or t(selected_medium, 'not_answered')
            correct_answer_text = ''.join([f"<div><strong>{k}</strong>: {escape(str(v))}</div>" for k,v in correct_map.items()]) or t(selected_medium, 'not_answered')
        elif is_tap_select_image_question(q):
            student_answer_text = render_tap_select_review(q, student_answer, correct_answer)
            correct_answer_text = f"Selected: {escape(student_answer or '-')} | Correct: {escape(correct_answer or '-')}"
        elif is_drag_drop_group_container_question(q):
            student_answer_text = escape(student_answer or t(selected_medium, 'not_answered'))
            correct_answer_text = 'All items inside basket and grouped close' if selected_medium == 'English' else 'සියලු දේ basket තුළ හා එකම කණ්ඩායම් ලඟින්'
        elif is_short_answer_question(q):
            student_answer_text = student_answer or t(selected_medium, "not_answered")
            correct_answer_text = correct_answer or t(selected_medium, "not_answered")
        elif student_answer in option_label_key:
            student_answer_text = getattr(q, f"{option_label_key[student_answer]}_{medium_key}")
            correct_answer_text = getattr(q, f"{option_label_key[correct_answer]}_{medium_key}")
        else:
            student_answer_text = t(selected_medium, "not_answered")
            correct_answer_text = getattr(q, f"{option_label_key[correct_answer]}_{medium_key}")
        wrong_answer_rows.append(
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'>{question_text}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{student_answer_text}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{correct_answer_text}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{explanation_text}</td>
            </tr>
            """
        )

    percentage_score = round((correct_answers / total_questions) * 100, 2) if total_questions else 0

    if percentage_score <= 20:
        level_name = "Foundation Weak"
    elif percentage_score <= 40:
        level_name = "Basic Learner"
    elif percentage_score <= 60:
        level_name = "Developing Learner"
    elif percentage_score <= 80:
        level_name = "Strong Learner"
    else:
        level_name = "Advanced Learner"

    topic_rows = []
    recommendations = []
    recommendation_practice_links = []
    for topic_name, stats in topic_stats.items():
        topic_total = stats["total"]
        topic_correct = stats["correct"]
        topic_percentage = round((topic_correct / topic_total) * 100, 2) if topic_total else 0
        status_en, status_si = classify_topic(topic_percentage)
        classification_label = status_en if selected_medium == "English" else status_si

        if status_en == "Weak":
            if selected_medium == "Sinhala":
                recommendations.append(f"{stats['topic_si']} සඳහා තවත් ප්‍රශ්න අභ්‍යාස කරන්න")
                recommendations.append(f"{stats['topic_si']} හි මූලික සංකල්ප නැවත බලන්න")
            else:
                recommendations.append(f"Practice more questions on {stats['topic_en']}")
                recommendations.append(f"Revise basic concepts of {stats['topic_en']}")
        elif status_en == "Improving":
            if selected_medium == "Sinhala":
                recommendations.append(f"{stats['topic_si']} සඳහා අතරමැදි මට්ටමේ ප්‍රශ්න කරන්න")
            else:
                recommendations.append(f"Do intermediate level questions on {stats['topic_en']}")
        else:
            if selected_medium == "Sinhala":
                recommendations.append(f"ඔබ {stats['topic_si']} තුළ ශක්තිමත්ය")
            else:
                recommendations.append(f"You are strong in {stats['topic_en']}")


        if status_en in {"Weak", "Improving"}:
            topic_en_encoded = quote_plus(stats["topic_en"])
            medium_encoded = quote_plus(selected_medium)
            practice_href = f"/practice?grade=6&subject=Math&topic={topic_en_encoded}&medium={medium_encoded}"
            button_text = (
                f"{stats['topic_si']} පුහුණුව ආරම්භ කරන්න"
                if selected_medium == "Sinhala"
                else f"Start {stats['topic_en']} Practice"
            )
            recommendation_practice_links.append(
                f"<p><a href='{practice_href}' style='display:inline-block;padding:8px 12px;"
                "background:#2563eb;color:#fff;text-decoration:none;border-radius:6px;'>"
                f"{button_text}</a></p>"
            )
        topic_rows.append(
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'>{topic_name}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{topic_total}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{topic_correct}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{topic_percentage}%</td>
              <td style='border:1px solid #ccc;padding:8px;'>{classification_label}</td>
            </tr>
            """
        )

    previous_result = (
        StudentResult.query.filter_by(student_id=student_id)
        .order_by(StudentResult.created_at.desc())
        .first()
    )
    if previous_result:
        improved = percentage_score > previous_result.score
    else:
        improved = False

    earned_xp = correct_answers * 15
    student_xp, _ = update_student_xp_and_level(student_id, earned_xp)
    streak_feedback = get_streak_feedback(student_id)
    if student_id:
        if streak_feedback.get("increased"):
            streak_message = (
                f"ඔබේ අඛණ්ඩ දින {streak_feedback.get('current')} දක්වා වැඩිවුණා"
                if selected_medium == "Sinhala"
                else f"Your streak increased to {streak_feedback.get('current')} days"
            )
        elif streak_feedback.get("restarted"):
            streak_message = "ඔබේ අඛණ්ඩ දින නැවත ආරම්භ විය" if selected_medium == "Sinhala" else "Your streak restarted"

    student_result = StudentResult(
        student_id=student_id,
        grade=selected_grade,
        subject=selected_subject,
        medium=resolve_medium(student.medium) if student else selected_medium,
        score=percentage_score,
        level=level_name,
        total_questions=total_questions,
        correct_answers=correct_answers,
    )
    db.session.add(student_result)
    db.session.flush()

    for topic_name, stats in topic_stats.items():
        topic_total = stats["total"]
        topic_correct = stats["correct"]
        topic_percentage = round((topic_correct / topic_total) * 100, 2) if topic_total else 0
        status_en, status_si = classify_topic(topic_percentage)
        db.session.add(
            StudentTopicPerformance(
                student_result_id=student_result.id,
                topic_en=stats["topic_en"],
                topic_si=stats["topic_si"],
                correct_count=topic_correct,
                total_count=topic_total,
                percentage=topic_percentage,
                status_en=status_en,
                status_si=status_si,
            )
        )
        upsert_student_topic_progress(
            student_id=student_id,
            grade=selected_grade,
            subject=selected_subject,
            topic_en=stats["topic_en"],
            topic_si=stats["topic_si"],
            score=topic_percentage,
        )
    primary_topic = max(topic_stats.values(), key=lambda item: item["total"], default=None)
    create_parent_notification(
        student_id=student_id,
        topic_en=(primary_topic or {}).get("topic_en", "Math"),
        topic_si=(primary_topic or {}).get("topic_si", "ගණිතය"),
        score=percentage_score,
        improved=improved,
        streak_increased=bool(streak_feedback.get("increased")),
    )
    db.session.commit()

    topic_analysis_html = f"""
    <h2>{t(selected_medium, 'topic_analysis')}</h2>
    <table style='border-collapse:collapse;width:100%;'>
      <thead>
        <tr>
          <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'topic')}</th>
          <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'total_questions')}</th>
          <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'correct_answers')}</th>
          <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'percentage_score')}</th>
          <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'classification')}</th>
        </tr>
      </thead>
      <tbody>
        {''.join(topic_rows)}
      </tbody>
    </table>
    """

    recommendations_html = f"""
    <h2>{t(selected_medium, 'recommended_next_steps')}</h2>
    <ul>
      {''.join(f"<li>{item}</li>" for item in recommendations)}
    </ul>
    {''.join(recommendation_practice_links)}
    """

    if wrong_answer_rows:
        wrong_answers_html = f"""
        <h2>{t(selected_medium, 'wrong_answers')}</h2>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'question')}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'student_answer')}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'correct_answer')}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'explanation')}</th>
            </tr>
          </thead>
          <tbody>
            {''.join(wrong_answer_rows)}
          </tbody>
        </table>
        """
    else:
        wrong_answers_html = f"<p>{t(selected_medium, 'excellent_no_wrong')}</p>"

    return f"""
    <!doctype html>
    <html lang='{'si' if selected_medium == 'Sinhala' else 'en'}'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>SkillScan Result</title>
      </head>
      <body>
        <h1>{t(selected_medium, 'result_title')}</h1>
        <p><strong>{t(selected_medium, 'total_questions')}:</strong> {total_questions}</p>
        <p><strong>{t(selected_medium, 'correct_answers')}:</strong> {correct_answers}</p>
        <p><strong>{t(selected_medium, 'percentage_score')}:</strong> {percentage_score}%</p>
        <p><strong>{t(selected_medium, 'level')}:</strong> {level_name}</p>
        <p><strong>{t(selected_medium, 'xp')} ({t(selected_medium, 'xp_sinhala')}):</strong> +{earned_xp} | Total: {student_xp}</p>
        {f"<p><strong>{streak_message}</strong></p>" if streak_message else ""}
        {topic_analysis_html}
        {recommendations_html}
        {wrong_answers_html}
        <p><a href='/retest-weak?medium={selected_medium}'>{t(selected_medium, 'retest_weak_topics')}</a></p>
        <p><a href='/test?medium={selected_medium}'>{t(selected_medium, 'try_again')}</a></p>
      </body>
    </html>
    """


@app.route("/retest-weak", methods=["GET", "POST"])
def retest_weak() -> str:
    db.create_all()
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))

    student = db.session.get(Student, student_id)
    if not student:
        session.pop("student_id", None)
        return redirect(url_for("login"))


    selected_medium = resolve_medium(student.medium)
    expired_now = expire_subscription_if_needed(student)
    expired_message = get_subscription_expired_message(selected_medium) if expired_now else ""
    if not has_active_premium(student) and get_daily_retest_count(student_id) >= 3:
        return redirect(url_for("upgrade_page", medium=selected_medium, limit_type="retest"))
    medium_key = "en" if selected_medium == "English" else "si"
    latest_result = get_latest_student_result(student_id)

    if not latest_result:
        return f"<p>{t(selected_medium, 'no_weak_topics_retest')}</p><p><a href='/student-dashboard'>{t(selected_medium, 'back_to_dashboard')}</a></p>"

    weak_topics = (
        StudentTopicPerformance.query.filter_by(student_result_id=latest_result.id)
        .filter(StudentTopicPerformance.percentage < 50)
        .all()
    )
    if not weak_topics:
        return f"<p>{t(selected_medium, 'no_weak_topics_retest')}</p><p><a href='/student-dashboard'>{t(selected_medium, 'back_to_dashboard')}</a></p>"

    weak_topic_names = [topic.topic_en for topic in weak_topics]
    last_score = min(topic.percentage for topic in weak_topics)
    if last_score < 50:
        target_difficulties = [1, 2]
    elif last_score < 80:
        target_difficulties = [3]
    else:
        target_difficulties = [4, 5]

    base_query = Question.query.filter(
        Question.grade == latest_result.grade,
        Question.subject == latest_result.subject,
        Question.topic_en.in_(weak_topic_names),
    )
    effective_difficulty = db.func.coalesce(Question.difficulty_level, 1)
    questions = base_query.filter(effective_difficulty.in_(target_difficulties)).order_by(Question.id.asc()).all()
    if not questions:
        questions = base_query.order_by(Question.id.asc()).all()

    attempted_ids = [
        row[0]
        for row in db.session.query(StudentQuestionAttempt.question_id)
        .join(Question, Question.id == StudentQuestionAttempt.question_id)
        .filter(
            StudentQuestionAttempt.student_id == student_id,
            Question.grade == latest_result.grade,
            Question.subject == latest_result.subject,
            Question.topic_en.in_(weak_topic_names),
        )
        .distinct()
        .all()
    ]
    unattempted_questions = [q for q in questions if q.id not in attempted_ids]
    if unattempted_questions:
        questions = unattempted_questions + [q for q in questions if q.id in attempted_ids]

    mini_count = min(10, max(5, len(questions)))
    questions = questions[:mini_count]

    if request.method == "POST":
        total_questions = len(questions)
        correct_answers = 0
        topic_stats = {}
        for q in questions:
            topic_stats.setdefault(q.topic_en, {"topic_en": q.topic_en, "topic_si": q.topic_si, "total": 0, "correct": 0})
            topic_stats[q.topic_en]["total"] += 1
            if is_matching_pairs_question(q):
                is_correct, _, _ = evaluate_matching_pairs_question(q, request.form, medium_key)
            elif is_box_input_question(q):
                is_correct, _, _ = evaluate_box_question(q, request.form)
            elif is_short_answer_question(q):
                student_answer = request.form.get(f"q_{q.id}", "").strip()
                is_correct = bool((q.correct_answer_text or '').strip()) and student_answer.casefold() == (q.correct_answer_text or '').strip().casefold()
            else:
                student_answer = request.form.get(f"q_{q.id}", "").strip().upper()
                is_correct = student_answer == q.correct_option.strip().upper()
            if is_correct:
                correct_answers += 1
                topic_stats[q.topic_en]["correct"] += 1
            db.session.add(StudentQuestionAttempt(student_id=student_id, question_id=q.id, source_type="RetestWeak", is_correct=is_correct))

        percentage_score = round((correct_answers / total_questions) * 100, 2) if total_questions else 0
        level_name = "Retest Weak Topics"
        earned_xp = correct_answers * 12
        update_student_xp_and_level(student_id, earned_xp)
        streak_feedback = get_streak_feedback(student_id)
        student_result = StudentResult(
            student_id=student_id,
            grade=latest_result.grade,
            subject=latest_result.subject,
            medium=selected_medium,
            score=percentage_score,
            level=level_name,
            total_questions=total_questions,
            correct_answers=correct_answers,
        )
        db.session.add(student_result)
        db.session.flush()
        for stats in topic_stats.values():
            topic_percentage = round((stats["correct"] / stats["total"]) * 100, 2) if stats["total"] else 0
            status_en, status_si = classify_topic(topic_percentage)
            db.session.add(StudentTopicPerformance(
                student_result_id=student_result.id,
                topic_en=stats["topic_en"],
                topic_si=stats["topic_si"],
                correct_count=stats["correct"],
                total_count=stats["total"],
                percentage=topic_percentage,
                status_en=status_en,
                status_si=status_si,
            ))
            upsert_student_topic_progress(
                student_id=student_id,
                grade=latest_result.grade,
                subject=latest_result.subject,
                topic_en=stats["topic_en"],
                topic_si=stats["topic_si"],
                score=topic_percentage,
            )
        primary_topic = max(topic_stats.values(), key=lambda item: item["total"], default=None)
        create_parent_notification(
            student_id=student_id,
            topic_en=(primary_topic or {}).get("topic_en", "Math"),
            topic_si=(primary_topic or {}).get("topic_si", "ගණිතය"),
            score=percentage_score,
            improved=percentage_score > last_score,
            streak_increased=bool(streak_feedback.get("increased")),
        )
        db.session.commit()
        streak_status = "increased" if streak_feedback["increased"] else "restarted" if streak_feedback["restarted"] else ""
        return redirect(
            url_for(
                "view_result",
                result_id=student_result.id,
                medium=selected_medium,
                streak_status=streak_status,
                streak_current=streak_feedback["current"],
            )
        )

    question_blocks = []
    for q in questions:
        if is_matching_pairs_question(q):
            answer_html = render_matching_pairs_inputs(q, medium_key)
        elif is_box_input_question(q):
            answer_html = render_box_template_with_inputs(q, 'qbox')
        elif is_tap_select_image_question(q):
            answer_html = render_tap_select_image_input(q)
        elif is_drag_drop_group_container_question(q):
            answer_html = render_drag_drop_group_container_input(q, medium_key)
        elif is_short_answer_question(q):
            answer_html = f"<input type='text' name='q_{q.id}' placeholder='Type your answer'>"
        else:
            answer_html = f"<label><input type='radio' name='q_{q.id}' value='A'> A. {getattr(q, f'option_a_{medium_key}')}</label><br><label><input type='radio' name='q_{q.id}' value='B'> B. {getattr(q, f'option_b_{medium_key}')}</label><br><label><input type='radio' name='q_{q.id}' value='C'> C. {getattr(q, f'option_c_{medium_key}')}</label><br><label><input type='radio' name='q_{q.id}' value='D'> D. {getattr(q, f'option_d_{medium_key}')}</label>"
        question_blocks.append(f"<div style='margin:16px 0;padding:12px;border:1px solid #ddd;'><p><strong>Q{q.id}.</strong> {getattr(q, f'question_text_{medium_key}')}</p>{answer_html}</div>")
    return f"""
    <!doctype html>
    <html lang='{'si' if selected_medium == 'Sinhala' else 'en'}'>
      <head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{t(selected_medium, 'retest_weak_topics')}</title></head>
      <body>
        <h1>{t(selected_medium, 'retest_weak_topics')}</h1>
        <p><strong>{t(selected_medium, 'total_questions')}:</strong> {len(questions)}</p>
        <form method='post' action='/retest-weak'>
          {''.join(question_blocks)}
          <button type='submit'>{t(selected_medium, 'submit')}</button>
        </form>
        <p><a href='/student-dashboard?medium={selected_medium}'>{t(selected_medium, 'back_to_dashboard')}</a></p>
      </body>
    </html>
    """


@app.route("/upgrade", methods=["GET"])
def upgrade_page() -> str:
    selected_medium = resolve_medium(request.args.get("medium"))
    back = "ඩෑෂ්බෝඩ් වෙත ආපසු" if selected_medium == "Sinhala" else "Back to Dashboard"
    whatsapp_link = "https://wa.me/94703755777?text=I%20want%20to%20upgrade%20to%20premium"

    return f"""
    <!doctype html>
    <html lang='{'si' if selected_medium == 'Sinhala' else 'en'}'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>Upgrade</title>
        <style>
          body {{
            margin: 0;
            font-family: Arial, sans-serif;
            background: #f5f7fb;
            color: #102036;
          }}
          .wrap {{
            max-width: 720px;
            margin: 32px auto;
            padding: 0 16px;
          }}
          .card {{
            background: #fff;
            border-radius: 14px;
            box-shadow: 0 8px 24px rgba(16, 32, 54, 0.08);
            padding: 28px;
          }}
          h1 {{ margin: 0 0 10px; font-size: 2rem; line-height: 1.2; }}
          .si-title {{ margin: 0 0 22px; font-size: 1.2rem; color: #1a3f8b; }}
          h2 {{ margin: 22px 0 10px; font-size: 1.1rem; color: #1a3f8b; }}
          ul {{ margin: 0; padding-left: 20px; }}
          li {{ margin: 8px 0; }}
          .price {{ font-size: 1.5rem; font-weight: 700; color: #0f7b43; margin: 4px 0 0; }}
          .cta-row {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 22px; }}
          .btn {{
            display: inline-block;
            text-decoration: none;
            padding: 12px 18px;
            border-radius: 10px;
            font-weight: 700;
          }}
          .btn-primary {{ background: #25d366; color: #fff; }}
          .btn-secondary {{ background: #e7ecf7; color: #102036; }}
        </style>
        {tap_select_common_assets()}
        {drag_drop_group_assets()}
      </head>
      <body>
        <main class='wrap'>
          <section class='card'>
            <h1>Improve Your Child’s Marks in 30 Days</h1>
            <p class='si-title'>ඔබගේ දරුවාගේ ලකුණු දින 30 ක් තුළ වැඩි කරගන්න</p>

            <h2>Benefits</h2>
            <ul>
              <li>Personalized learning</li>
              <li>Weak topic fixing</li>
              <li>Daily progress tracking</li>
              <li>Parent notifications</li>
            </ul>

            <h2>Pricing</h2>
            <p class='price'>Only LKR 350 per month</p>

            <div class='cta-row'>
              <a class='btn btn-primary' href='{whatsapp_link}' target='_blank' rel='noopener noreferrer'>Contact on WhatsApp</a>
              <a class='btn btn-primary' href='{whatsapp_link}' target='_blank' rel='noopener noreferrer'>WhatsApp මගින් සම්බන්ධ වන්න</a>
              <a class='btn btn-secondary' href='/student-dashboard?medium={selected_medium}'>{back}</a>
            </div>
          </section>
        </main>
      </body>
    </html>
    """




@app.route("/leaderboard", methods=["GET"])
@app.route("/leaderboard/grade/<grade>", methods=["GET"])
def leaderboard(grade: str | None = None) -> str:
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))

    current_student = db.session.get(Student, student_id)
    if not current_student:
        session.pop("student_id", None)
        return redirect(url_for("login"))

    is_si = current_student.medium == "Sinhala"
    labels = {
        "title": "ප්‍රමුඛ ලැයිස්තුව" if is_si else "Leaderboard",
        "rank": "ස්ථානය" if is_si else "Rank",
        "name": "නම" if is_si else "Student name",
        "grade": "ශ්‍රේණිය" if is_si else "Grade",
        "xp": "ලකුණු" if is_si else "XP",
        "level": "මට්ටම" if is_si else "Level",
        "current_streak": "වත්මන් අඛණ්ඩ දින" if is_si else "Current streak",
        "back": "ඩෑෂ්බෝඩ් වෙත ආපසු" if is_si else "Back to Dashboard",
    }

    selected_grade = normalize_grade(grade)
    if selected_grade and selected_grade not in VALID_GRADES:
        selected_grade = ""

    tabs = []
    for g in VALID_GRADES:
        if g == selected_grade:
            tabs.append(f"<strong>{display_grade(g, 'Sinhala' if is_si else 'English')}</strong>")
        else:
            tabs.append(f"<a href='/leaderboard/grade/{g}'>{display_grade(g, 'Sinhala' if is_si else 'English')}</a>")

    query = Student.query
    if selected_grade:
        query = query.filter(Student.grade == selected_grade)

    students = query.order_by(Student.level.desc(), Student.xp.desc(), Student.current_streak.desc(), Student.id.asc()).limit(50).all()
    rows = "".join(
        f"<tr><td style='border:1px solid #ccc;padding:8px;'>{idx}</td><td style='border:1px solid #ccc;padding:8px;'>{s.name}</td><td style='border:1px solid #ccc;padding:8px;'>{display_grade(s.grade, 'Sinhala' if is_si else 'English')}</td><td style='border:1px solid #ccc;padding:8px;'>{s.xp or 0}</td><td style='border:1px solid #ccc;padding:8px;'>{s.level or 1}</td><td style='border:1px solid #ccc;padding:8px;'>{s.current_streak or 0}</td></tr>"
        for idx, s in enumerate(students, start=1)
    )

    return f"""
    <!doctype html><html lang='{'si' if is_si else 'en'}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{labels['title']}</title></head><body>
    <h1>{labels['title']}</h1>
    <p><a href='/leaderboard'>{'All Grades' if not is_si else 'සියලු ශ්‍රේණි'}</a> | {' | '.join(tabs)}</p>
    <table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['rank']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['name']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['grade']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['xp']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['level']}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{labels['current_streak']}</th></tr></thead><tbody>{rows}</tbody></table>
    <p><a href='/student-dashboard'>{labels['back']}</a></p>
    </body></html>
    """


@app.route("/result/<int:result_id>", methods=["GET"])
def view_result(result_id: int) -> str:
    student_result = db.session.get(StudentResult, result_id)
    if not student_result:
        return redirect(url_for("student_dashboard"))
    selected_medium = resolve_medium(request.args.get("medium") or student_result.medium)
    streak_status = (request.args.get("streak_status") or "").strip()
    streak_current = (request.args.get("streak_current") or "").strip()
    streak_message = ""
    if streak_status == "increased":
        streak_message = (
            f"ඔබේ අඛණ්ඩ දින {streak_current} දක්වා වැඩිවුණා"
            if selected_medium == "Sinhala"
            else f"Your streak increased to {streak_current} days"
        )
    elif streak_status == "restarted":
        streak_message = "ඔබේ අඛණ්ඩ දින නැවත ආරම්භ විය" if selected_medium == "Sinhala" else "Your streak restarted"
    topics = StudentTopicPerformance.query.filter_by(student_result_id=student_result.id).order_by(StudentTopicPerformance.id.asc()).all()
    topic_rows = "".join(
        f"<tr><td style='border:1px solid #ccc;padding:8px;'>{topic.topic_si if selected_medium == 'Sinhala' else topic.topic_en}</td>"
        f"<td style='border:1px solid #ccc;padding:8px;'>{topic.correct_count}/{topic.total_count}</td>"
        f"<td style='border:1px solid #ccc;padding:8px;'>{topic.percentage}%</td></tr>"
        for topic in topics
    )
    return f"""
    <!doctype html>
    <html lang='{'si' if selected_medium == 'Sinhala' else 'en'}'>
      <head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{t(selected_medium, 'result_title')}</title></head>
      <body>
        <h1>{t(selected_medium, 'result_title')}</h1>
        <p><strong>{t(selected_medium, 'total_questions')}:</strong> {student_result.total_questions}</p>
        <p><strong>{t(selected_medium, 'correct_answers')}:</strong> {student_result.correct_answers}</p>
        <p><strong>{t(selected_medium, 'percentage_score')}:</strong> {student_result.score}%</p>
        {f"<p><strong>{streak_message}</strong></p>" if streak_message else ""}
        <table style='border-collapse:collapse;width:100%;'><thead><tr><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'topic')}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'correct_answers')}</th><th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'percentage_score')}</th></tr></thead><tbody>{topic_rows}</tbody></table>
        <p><a href='/student-dashboard'>{t(selected_medium, 'back_to_dashboard')}</a></p>
      </body>
    </html>
    """


@app.route("/practice", methods=["GET"])
def practice_page() -> str:
    db.create_all()
    grade = (request.args.get("grade") or "").strip()
    subject = (request.args.get("subject") or "").strip()
    topic = (request.args.get("topic") or "").strip()
    student_id = session.get("student_id")
    student = db.session.get(Student, student_id) if student_id else None
    selected_medium = resolve_medium(student.medium) if student else resolve_medium(request.args.get("medium"))

    medium_key = "en" if selected_medium == "English" else "si"
    expired_now = expire_subscription_if_needed(student)
    expired_message = get_subscription_expired_message(selected_medium) if expired_now else ""
    if student_id and not has_active_premium(student) and get_daily_practice_count(student_id) >= 5:
        return redirect(url_for("upgrade_page", medium=selected_medium, limit_type="practice"))
    last_attempt = None
    if student_id:
        last_attempt = (
            PracticeAttempt.query.filter_by(student_id=student_id, grade=grade, subject=subject, topic_en=topic)
            .order_by(PracticeAttempt.created_at.desc(), PracticeAttempt.id.desc())
            .first()
        )

    if not last_attempt or last_attempt.score < 50:
        target_difficulties = [1, 2]
    elif last_attempt.score < 80:
        target_difficulties = [3]
    else:
        target_difficulties = [4, 5]

    base_query = Question.query.filter_by(grade=grade, subject=subject, topic=topic)
    effective_difficulty = db.func.coalesce(Question.difficulty_level, 1)
    questions = (
        base_query.filter(effective_difficulty.in_(target_difficulties))
        .order_by(Question.id.asc())
        .all()
    )
    if not questions:
        questions = base_query.order_by(Question.id.asc()).all()
    selected_question_difficulty = (
        ", ".join(str(level) for level in target_difficulties) if questions and any((q.difficulty_level or 1) in target_difficulties for q in questions) else "1-5"
    )
    if student_id and questions:
        attempted_ids = [
            row[0]
            for row in db.session.query(StudentQuestionAttempt.question_id)
            .join(Question, Question.id == StudentQuestionAttempt.question_id)
            .filter(
                StudentQuestionAttempt.student_id == student_id,
                Question.grade == grade,
                Question.subject == subject,
                Question.topic == topic,
            )
            .distinct()
            .all()
        ]
        unattempted_questions = [q for q in questions if q.id not in attempted_ids]
        if len(unattempted_questions) >= len(questions):
            questions = unattempted_questions
        elif unattempted_questions:
            questions = unattempted_questions + [q for q in questions if q.id in attempted_ids]

    question_blocks = []
    for q in questions:
        question_text = getattr(q, f"question_text_{medium_key}")
        image_html = f"<img src='{escape(normalize_local_image_url(q.image_url))}' alt='Question image' class='question-image'>" if q.image_url else ""
        if is_matching_pairs_question(q):
            answer_html = render_matching_pairs_inputs(q, medium_key)
        elif is_box_input_question(q):
            answer_html = render_box_template_with_inputs(q, 'qbox')
        elif is_tap_select_image_question(q):
            answer_html = render_tap_select_image_input(q)
        elif is_drag_drop_group_container_question(q):
            answer_html = render_drag_drop_group_container_input(q, medium_key)
        elif is_short_answer_question(q):
            answer_html = f"<input type='text' name='q_{q.id}' placeholder='Type your answer'>"
        else:
            option_a = getattr(q, f"option_a_{medium_key}")
            option_b = getattr(q, f"option_b_{medium_key}")
            option_c = getattr(q, f"option_c_{medium_key}")
            option_d = getattr(q, f"option_d_{medium_key}")
            answer_html = f"<label><input type='radio' name='q_{q.id}' value='A'> A. {option_a}</label><br><label><input type='radio' name='q_{q.id}' value='B'> B. {option_b}</label><br><label><input type='radio' name='q_{q.id}' value='C'> C. {option_c}</label><br><label><input type='radio' name='q_{q.id}' value='D'> D. {option_d}</label>"
        question_blocks.append(f"<div style='margin:16px 0;padding:12px;border:1px solid #ddd;'><p><strong>Q{q.id}.</strong> {question_text}</p>{'' if (is_tap_select_image_question(q) or is_drag_drop_group_container_question(q)) else image_html}{answer_html}</div>")

    show_language_controls = student is None
    language_controls_html = f"""
        <form method='get' action='/practice' style='margin-bottom:20px;'>
          <input type='hidden' name='grade' value='{grade}'>
          <input type='hidden' name='subject' value='{subject}'>
          <input type='hidden' name='topic' value='{topic}'>
          <label>{t(selected_medium, 'language')}:
            <select name='medium'>
              <option value='English' {'selected' if selected_medium == 'English' else ''}>English</option>
              <option value='Sinhala' {'selected' if selected_medium == 'Sinhala' else ''}>Sinhala</option>
            </select>
          </label>
          <button type='submit'>{t(selected_medium, 'change_language')}</button>
        </form>
    """ if show_language_controls else ""
    display_topic = topic or "-"

    return f"""
    <!doctype html>
    <html lang='{'si' if selected_medium == 'Sinhala' else 'en'}'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>{t(selected_medium, 'practice_title')}</title>
        <style>
          .box-layout {{font-family:monospace;white-space:pre;line-height:1.4;}}
          .box-input {{width:14px;height:14px;min-width:14px;padding:1px;text-align:center;font-size:12px;line-height:12px;border:1.5px solid #000;border-radius:2px;display:inline-block;vertical-align:middle;margin:0 1px;font-family:monospace;box-sizing:border-box;}}
          .question-image {{
            max-width: 250px;
            width: 100%;
            height: auto;
            display: block;
            margin: 10px 0;
            border: 1px solid #ddd;
            border-radius: 6px;
          }}
          @media (max-width: 768px) {{
            .box-layout {{font-family:monospace;white-space:pre;line-height:1.4;}}
          .box-input {{width:14px;height:14px;min-width:14px;padding:1px;text-align:center;font-size:12px;line-height:12px;border:1.5px solid #000;border-radius:2px;display:inline-block;vertical-align:middle;margin:0 1px;font-family:monospace;box-sizing:border-box;}}
          .question-image {{
              max-width: 180px;
            }}
          }}
        </style>
        {tap_select_common_assets()}
        {drag_drop_group_assets()}
      </head>
      <body>
        <h1>{t(selected_medium, 'practice_title')}</h1>
        <p><strong>{t(selected_medium, 'topic_name')}:</strong> {display_topic}</p>
        <p><strong>{t(selected_medium, 'difficulty_label')}:</strong> {selected_question_difficulty}</p>
        {language_controls_html}
        <form method='post' action='/submit-practice'>
          <input type='hidden' name='grade' value='{grade}'>
          <input type='hidden' name='subject' value='{subject}'>
          <input type='hidden' name='topic' value='{topic}'>
          {''.join(question_blocks) if question_blocks else f"<p>{t(selected_medium, 'no_questions')}</p>"}
          <button type='submit'>{t(selected_medium, 'submit')}</button>
        </form>
        <p><a href='/student-dashboard'>{t(selected_medium, 'back_to_dashboard')}</a></p>
      </body>
    </html>
    """


@app.route("/student/recommended-practice", methods=["GET", "POST"])
def student_recommended_practice() -> str:
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))
    student = db.session.get(Student, student_id)
    if not student:
        return redirect(url_for("login"))
    selected_medium = resolve_medium(student.medium)
    medium_key = "en" if selected_medium == "English" else "si"
    dynamic_questions = get_dynamic_practice_questions(student.id, limit=10)
    if request.method == "POST":
        grouped: dict[str, dict[str, object]] = {}
        for q in dynamic_questions:
            selected_answer = (request.form.get(f"q_{q.id}") or "").strip()
            is_correct = selected_answer.upper() == (q.correct_option or "").upper()
            db.session.add(StudentQuestionAttempt(student_id=student.id, question_id=q.id, source_type="recommended_practice", is_correct=is_correct))
            topic_key = q.topic_en or q.topic
            bucket = grouped.setdefault(topic_key, {"question": q, "total": 0, "correct": 0})
            bucket["total"] = int(bucket["total"]) + 1
            if is_correct:
                bucket["correct"] = int(bucket["correct"]) + 1
        for bucket in grouped.values():
            q = bucket["question"]
            total = int(bucket["total"])
            correct = int(bucket["correct"])
            score = (correct / total) * 100 if total else 0
            upsert_student_topic_progress(student.id, q.grade, q.subject, q.topic_en, q.topic_si, score)
            mastery = StudentSkillMastery.query.filter_by(student_id=student.id, chapter_id=q.chapter_id or 0, lesson_id=q.chapter_id or 0, skill_code=q.topic_en).first()
            if not mastery:
                mastery = StudentSkillMastery(student_id=student.id, subject_id=None, module_id=q.module_id, chapter_id=q.chapter_id or 0, lesson_id=q.chapter_id or 0, skill_code=q.topic_en, skill_name_en=q.topic_en, skill_name_si=q.topic_si, mastery_score=0)
                db.session.add(mastery)
            mastery.total_attempts = (mastery.total_attempts or 0) + total
            mastery.correct_attempts = (mastery.correct_attempts or 0) + correct
            mastery.wrong_attempts = (mastery.wrong_attempts or 0) + (total - correct)
            mastery.mastery_score = round((mastery.correct_attempts / max(mastery.total_attempts, 1)) * 100, 2)
            mastery.status_en, mastery.status_si = classify_mastery(mastery.mastery_score)
            mastery.last_answered_at = datetime.utcnow()
        db.session.commit()
        return redirect("/student/recommended-practice")
    blocks = []
    for q in dynamic_questions:
        q_text = getattr(q, f"question_text_{medium_key}")
        option_a = getattr(q, f"option_a_{medium_key}")
        option_b = getattr(q, f"option_b_{medium_key}")
        option_c = getattr(q, f"option_c_{medium_key}")
        option_d = getattr(q, f"option_d_{medium_key}")
        blocks.append(f"<div style='border:1px solid #ddd;padding:10px;margin:10px 0'><p><strong>{escape(q.topic_si if selected_medium=='Sinhala' else q.topic_en)}</strong> • L{q.difficulty_level or 1}</p><p>{escape(q_text)}</p><label><input type='radio' name='q_{q.id}' value='A'> {escape(option_a)}</label><br><label><input type='radio' name='q_{q.id}' value='B'> {escape(option_b)}</label><br><label><input type='radio' name='q_{q.id}' value='C'> {escape(option_c)}</label><br><label><input type='radio' name='q_{q.id}' value='D'> {escape(option_d)}</label></div>")
    return f"<!doctype html><html><body><h1>{'Recommended Practice' if selected_medium=='English' else 'නිර්දේශිත පුහුණුව'}</h1><form method='post'>{''.join(blocks) if blocks else '<p>No dynamic questions available yet.</p>'}<button type='submit'>Submit Practice</button></form><p><a href='/student-dashboard'>Back to Dashboard</a></p></body></html>"


@app.route("/submit-practice", methods=["POST"])
def submit_practice() -> str:
    db.create_all()
    student_id = session.get("student_id")
    student = db.session.get(Student, student_id) if student_id else None
    selected_medium = resolve_medium(student.medium) if student else resolve_medium(request.form.get("medium") or request.args.get("medium"))
    grade = (request.form.get("grade") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    topic = (request.form.get("topic") or "").strip()
    medium_key = "en" if selected_medium == "English" else "si"
    streak_message = ""

    questions = (
        Question.query.filter_by(grade=grade, subject=subject, topic=topic)
        .order_by(Question.id.asc())
        .all()
    )

    total_questions = len(questions)
    correct_answers = 0
    option_label_key = {"A": "option_a", "B": "option_b", "C": "option_c", "D": "option_d"}
    answer_rows = []
    student_id = session.get("student_id")

    for q in questions:
        if is_matching_pairs_question(q):
            is_correct, student_pair_answers, correct_pair_answers = evaluate_matching_pairs_question(q, request.form, medium_key)
            student_answer = json.dumps(student_pair_answers, ensure_ascii=False)
            correct_answer = json.dumps(correct_pair_answers, ensure_ascii=False)
        elif is_box_input_question(q):
            is_correct, student_box_answers, correct_box_answers = evaluate_box_question(q, request.form)
            student_answer = json.dumps(student_box_answers, ensure_ascii=False)
            correct_answer = json.dumps(correct_box_answers, ensure_ascii=False)
        elif is_tap_select_image_question(q):
            is_correct, student_answer, correct_answer = evaluate_tap_select_question(q, request.form)
        elif is_drag_drop_group_container_question(q):
            is_correct, student_answer = evaluate_drag_drop_group_container_question(q, request.form)
            correct_answer = 'Grouped in basket'
        elif is_short_answer_question(q):
            student_answer = request.form.get(f"q_{q.id}", "").strip()
            correct_answer = (q.correct_answer_text or "").strip()
            is_correct = bool(correct_answer) and student_answer.casefold() == correct_answer.casefold()
        else:
            student_answer = request.form.get(f"q_{q.id}", "").strip().upper()
            correct_answer = q.correct_option.strip().upper()
            is_correct = student_answer == correct_answer
        if is_correct:
            correct_answers += 1
        db.session.add(
            StudentQuestionAttempt(
                student_id=student_id,
                question_id=q.id,
                source_type="Practice",
                is_correct=is_correct,
            )
        )

        question_text = getattr(q, f"question_text_{medium_key}")
        explanation_text = getattr(q, f"explanation_{medium_key}")
        if is_matching_pairs_question(q):
            student_map = json.loads(student_answer or "{}")
            correct_map = json.loads(correct_answer or "{}")
            parts = [f"<div><strong>{escape(k)}</strong><br>Your Answer: {escape(student_map.get(k) or t(selected_medium, 'not_answered'))}<br>Correct Answer: {escape(v)}</div>" for k, v in correct_map.items()]
            student_answer_text = "".join(parts) or t(selected_medium, "not_answered")
            correct_answer_text = "-"
        elif is_box_input_question(q):
            student_map = json.loads(student_answer or '{}')
            correct_map = json.loads(correct_answer or '{}')
            parts = []
            for k, v in correct_map.items():
                sval = student_map.get(k, '')
                ok = sval.strip().casefold() == str(v).strip().casefold()
                color = '#16a34a' if ok else '#dc2626'
                parts.append(f"<div><strong>{k}</strong>: <span style='color:{color}'>{escape(sval or '-')}</span> / {escape(str(v))}</div>")
            student_answer_text = ''.join(parts) or t(selected_medium, 'not_answered')
            correct_answer_text = ''.join([f"<div><strong>{k}</strong>: {escape(str(v))}</div>" for k,v in correct_map.items()]) or t(selected_medium, 'not_answered')
        elif is_tap_select_image_question(q):
            student_answer_text = render_tap_select_review(q, student_answer, correct_answer)
            correct_answer_text = f"Selected: {escape(student_answer or '-')} | Correct: {escape(correct_answer or '-')}"
        elif is_drag_drop_group_container_question(q):
            student_answer_text = escape(student_answer or t(selected_medium, 'not_answered'))
            correct_answer_text = 'All items inside basket and grouped close' if selected_medium == 'English' else 'සියලු දේ basket තුළ හා එකම කණ්ඩායම් ලඟින්'
        elif is_short_answer_question(q):
            student_answer_text = student_answer or t(selected_medium, "not_answered")
            correct_answer_text = correct_answer or t(selected_medium, "not_answered")
        elif student_answer in option_label_key:
            student_answer_text = getattr(q, f"{option_label_key[student_answer]}_{medium_key}")
            correct_answer_text = getattr(q, f"{option_label_key[correct_answer]}_{medium_key}")
        else:
            student_answer_text = t(selected_medium, "not_answered")
            correct_answer_text = getattr(q, f"{option_label_key[correct_answer]}_{medium_key}")

        answer_rows.append(
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'>{question_text}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{student_answer_text}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{correct_answer_text}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{explanation_text}</td>
            </tr>
            """
        )

    score = round((correct_answers / total_questions) * 100, 2) if total_questions else 0

    topic_question = Question.query.filter_by(grade=grade, subject=subject, topic=topic).first()
    topic_en = topic_question.topic_en if topic_question else topic
    topic_si = topic_question.topic_si if topic_question else topic

    previous_attempt = None
    if student_id:
        previous_attempt = (
            PracticeAttempt.query.filter_by(student_id=student_id, grade=grade, subject=subject, topic_en=topic_en)
            .order_by(PracticeAttempt.created_at.desc(), PracticeAttempt.id.desc())
            .first()
        )

    earned_xp = correct_answers * 10
    student_xp, _ = update_student_xp_and_level(student_id, earned_xp)
    streak_feedback = get_streak_feedback(student_id)
    if student_id:
        if streak_feedback.get("increased"):
            streak_message = (
                f"ඔබේ අඛණ්ඩ දින {streak_feedback.get('current')} දක්වා වැඩිවුණා"
                if selected_medium == "Sinhala"
                else f"Your streak increased to {streak_feedback.get('current')} days"
            )
        elif streak_feedback.get("restarted"):
            streak_message = "ඔබේ අඛණ්ඩ දින නැවත ආරම්භ විය" if selected_medium == "Sinhala" else "Your streak restarted"

    practice_attempt = PracticeAttempt(
        student_id=student_id,
        grade=grade,
        subject=subject,
        topic_en=topic_en,
        topic_si=topic_si,
        medium=resolve_medium(student.medium) if student else selected_medium,
        score=score,
        total_questions=total_questions,
        correct_answers=correct_answers,
    )
    db.session.add(practice_attempt)
    upsert_student_topic_progress(
        student_id=student_id,
        grade=grade,
        subject=subject,
        topic_en=topic_en,
        topic_si=topic_si,
        score=score,
    )
    create_parent_notification(
        student_id=student_id,
        topic_en=topic_en or topic or "Math",
        topic_si=topic_si or topic or "ගණිතය",
        score=score,
        improved=bool(previous_attempt and score > previous_attempt.score),
        streak_increased=bool(streak_feedback.get("increased")),
    )
    db.session.commit()
    return f"""
    <!doctype html>
    <html lang='{'si' if selected_medium == 'Sinhala' else 'en'}'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>{t(selected_medium, 'practice_title')}</title>
      </head>
      <body>
        <h1>{t(selected_medium, 'practice_title')}</h1>
        <p><strong>{t(selected_medium, 'topic_name')}:</strong> {topic or '-'}</p>
        <p><strong>{t(selected_medium, 'practice_score')}:</strong> {score}%</p>
        <p><strong>{t(selected_medium, 'correct_answers')}:</strong> {correct_answers}/{total_questions}</p>
        <p><strong>{t(selected_medium, 'xp')} ({t(selected_medium, 'xp_sinhala')}):</strong> +{earned_xp} | Total: {student_xp}</p>
        {f"<p><strong>{streak_message}</strong></p>" if streak_message else ""}
        <h2>{t(selected_medium, 'wrong_answers')}</h2>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'question')}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'student_answer')}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'correct_answer')}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{t(selected_medium, 'explanation')}</th>
            </tr>
          </thead>
          <tbody>
            {''.join(answer_rows) if answer_rows else f"<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>{t(selected_medium, 'no_questions')}</td></tr>"}
          </tbody>
        </table>
        <p><a href='/practice?grade={grade}&subject={subject}&topic={topic}&medium={selected_medium}'>{t(selected_medium, 'try_again')}</a></p>
        <p><a href='/student-dashboard'>{t(selected_medium, 'back_to_dashboard')}</a></p>
      </body>
    </html>
    """


@app.route("/update-practice-db", methods=["GET"])
def update_practice_db() -> tuple:
    try:
        db.create_all()
        db.session.commit()
        return jsonify({"success": True, "message": "Practice tables ensured successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Practice DB update failed: {exc}"}), 500


@app.route("/update-topic-progress-db", methods=["GET"])
def update_topic_progress_db() -> tuple:
    try:
        db.create_all()
        db.session.commit()
        return jsonify({"success": True, "message": "Student topic progress table ensured successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Topic progress DB update failed: {exc}"}), 500


@app.route("/student/homework", methods=["GET"])
def student_homework_list():
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))
    student = db.session.get(Student, student_id)
    if not student:
        return redirect(url_for("login"))
    if not student.class_id:
        return "<h2>No class assigned</h2><p><a href='/student-dashboard'>Back</a></p>"
    items = HomeworkAssignment.query.filter_by(class_id=student.class_id).order_by(HomeworkAssignment.due_date.asc(), HomeworkAssignment.id.desc()).all()
    rows = "".join(
        f"<tr><td style='border:1px solid #ccc;padding:8px;'>{escape(h.title)}</td><td style='border:1px solid #ccc;padding:8px;'>{escape(h.topic_si if student.medium=='Sinhala' else h.topic_en)}</td><td style='border:1px solid #ccc;padding:8px;'>{h.difficulty_level}</td><td style='border:1px solid #ccc;padding:8px;'>{h.due_date.strftime('%Y-%m-%d')}</td><td style='border:1px solid #ccc;padding:8px;'><a href='/student/homework/{h.id}'>Open</a></td></tr>"
        for h in items
    )
    empty_row = "<tr><td colspan='5' style='border:1px solid #ccc;padding:8px;'>No homework assigned.</td></tr>"
    title = "මගේ ගෙදර වැඩ" if student.medium == "Sinhala" else "My Homework"
    return f"<!doctype html><html><body><h1>{title}</h1><table style='border-collapse:collapse;width:100%'><thead><tr><th style='border:1px solid #ccc;padding:8px;'>Title</th><th style='border:1px solid #ccc;padding:8px;'>Topic</th><th style='border:1px solid #ccc;padding:8px;'>Difficulty</th><th style='border:1px solid #ccc;padding:8px;'>Due Date</th><th style='border:1px solid #ccc;padding:8px;'>Action</th></tr></thead><tbody>{rows if rows else empty_row}</tbody></table><p><a href='/student-dashboard'>Back to Dashboard</a></p></body></html>"


@app.route("/student/homework/<int:homework_id>", methods=["GET"])
def student_homework_detail(homework_id: int):
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))
    student = db.session.get(Student, student_id)
    homework = db.session.get(HomeworkAssignment, homework_id)
    if not student or not homework or student.class_id != homework.class_id:
        return "<h2>Homework not found</h2>", 404
    questions = get_questions_for_homework(homework.grade, homework.subject, homework.topic_en, homework.topic_si, homework.difficulty_level)
    medium_key = "si" if student.medium == "Sinhala" else "en"
    q_html_parts = []
    for q in questions:
        image_html = f"<img src='{escape(normalize_local_image_url(q.image_url))}' alt='Question image' class='question-image'>" if q.image_url else ""
        if is_matching_pairs_question(q):
            answer_html = render_matching_pairs_inputs(q, medium_key)
        elif is_box_input_question(q):
            answer_html = render_box_template_with_inputs(q, 'qbox')
        elif is_tap_select_image_question(q):
            answer_html = render_tap_select_image_input(q)
        elif is_drag_drop_group_container_question(q):
            answer_html = render_drag_drop_group_container_input(q, medium_key)
        elif is_short_answer_question(q):
            answer_html = f"<input type='text' name='q_{q.id}' placeholder='Type your answer'>"
        else:
            answer_html = f"<label><input type='radio' name='q_{q.id}' value='A'> A. {escape(getattr(q, f'option_a_{medium_key}'))}</label><br><label><input type='radio' name='q_{q.id}' value='B'> B. {escape(getattr(q, f'option_b_{medium_key}'))}</label><br><label><input type='radio' name='q_{q.id}' value='C'> C. {escape(getattr(q, f'option_c_{medium_key}'))}</label><br><label><input type='radio' name='q_{q.id}' value='D'> D. {escape(getattr(q, f'option_d_{medium_key}'))}</label>"
        q_html_parts.append(f"<div style='margin:16px 0;padding:12px;border:1px solid #ddd;'><p><strong>Q{q.id}.</strong> {escape(getattr(q, f'question_text_{medium_key}'))}</p>{'' if (is_tap_select_image_question(q) or is_drag_drop_group_container_question(q)) else image_html}{answer_html}</div>")
    q_html = "".join(q_html_parts)
    return f"""<!doctype html>
<html>
  <head>
    <meta name='viewport' content='width=device-width, initial-scale=1'>
    <style>
      .box-layout {{font-family:monospace;white-space:pre;line-height:1.4;}}
          .box-input {{width:14px;height:14px;min-width:14px;padding:1px;text-align:center;font-size:12px;line-height:12px;border:1.5px solid #000;border-radius:2px;display:inline-block;vertical-align:middle;margin:0 1px;font-family:monospace;box-sizing:border-box;}}
          .question-image {{
        max-width: 250px;
        width: 100%;
        height: auto;
        display: block;
        margin: 10px 0;
        border: 1px solid #ddd;
        border-radius: 6px;
      }}
      @media (max-width: 768px) {{
        .box-layout {{font-family:monospace;white-space:pre;line-height:1.4;}}
          .box-input {{width:14px;height:14px;min-width:14px;padding:1px;text-align:center;font-size:12px;line-height:12px;border:1.5px solid #000;border-radius:2px;display:inline-block;vertical-align:middle;margin:0 1px;font-family:monospace;box-sizing:border-box;}}
          .question-image {{
          max-width: 180px;
        }}
      }}
    </style>
    {tap_select_common_assets()}
    {drag_drop_group_assets()}
  </head>
  <body><h1>{escape(homework.title)}</h1><p>Due: {homework.due_date.strftime('%Y-%m-%d')}</p><form method='post' action='/student/homework/{homework.id}/submit'>{q_html if q_html else '<p>No matching questions found.</p>'}<button type='submit'>Submit</button></form><p><a href='/student/homework'>Back</a></p></body>
</html>"""


@app.route("/student/homework/<int:homework_id>/submit", methods=["POST"])
def student_homework_submit(homework_id: int):
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))
    student = db.session.get(Student, student_id)
    homework = db.session.get(HomeworkAssignment, homework_id)
    if not student or not homework or student.class_id != homework.class_id:
        return "<h2>Homework not found</h2>", 404
    questions = get_questions_for_homework(homework.grade, homework.subject, homework.topic_en, homework.topic_si, homework.difficulty_level)
    correct_answers = 0
    medium_key = "si" if student.medium == "Sinhala" else "en"
    for q in questions:
        if is_matching_pairs_question(q):
            is_correct, _, _ = evaluate_matching_pairs_question(q, request.form, medium_key)
        elif is_box_input_question(q):
            is_correct, _, _ = evaluate_box_question(q, request.form)
        elif is_tap_select_image_question(q):
            is_correct, _, _ = evaluate_tap_select_question(q, request.form)
        elif is_drag_drop_group_container_question(q):
            is_correct, _ = evaluate_drag_drop_group_container_question(q, request.form)
        elif is_short_answer_question(q):
            is_correct = (request.form.get(f"q_{q.id}") or '').strip().casefold() == (q.correct_answer_text or '').strip().casefold()
        else:
            is_correct = (request.form.get(f"q_{q.id}") or "").strip().upper() == (q.correct_option or "").strip().upper()
        if is_correct:
            correct_answers += 1
    total_questions = len(questions)
    score = round((correct_answers / total_questions) * 100, 2) if total_questions else 0
    db.session.add(HomeworkSubmission(homework_id=homework.id, student_id=student.id, score=score, total_questions=total_questions, correct_answers=correct_answers))
    db.session.commit()
    return f"<h1>Homework Submitted</h1><p>Score: {score}% ({correct_answers}/{total_questions})</p><p><a href='/student/homework'>Back to My Homework</a></p>"



@app.route("/student/tests", methods=["GET"])
def student_tests_list():
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))
    student = db.session.get(Student, student_id)
    if not student or not student.class_id:
        return "<h2>No class assigned</h2><p><a href='/student-dashboard'>Back</a></p>"
    tests = ClassTest.query.filter_by(class_id=student.class_id).order_by(ClassTest.test_date.asc(), ClassTest.id.desc()).all()
    today = date.today()
    is_si = student.medium == "Sinhala"
    rows = []
    for item in tests:
        is_upcoming = item.test_date >= today
        status = "Upcoming" if is_upcoming else "Completed"
        action = (
            f"<a href='/student/test/{item.id}'>{'පරීක්ෂාව ආරම්භ කරන්න' if is_si else 'Start Test'}</a>"
            if is_upcoming
            else f"<a href='/student/test/{item.id}/result'>{'ප්‍රතිඵල බලන්න' if is_si else 'View Result'}</a>"
        )
        rows.append(f"<tr><td style='border:1px solid #ccc;padding:8px;'>{escape(item.title)}</td><td style='border:1px solid #ccc;padding:8px;'>{item.test_date.strftime('%Y-%m-%d')}</td><td style='border:1px solid #ccc;padding:8px;'>{status}</td><td style='border:1px solid #ccc;padding:8px;'>{action}</td></tr>")
    rows_html = "".join(rows) if rows else "<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>No tests found.</td></tr>"
    return f"<!doctype html><html><body><h1>{'මගේ පරීක්ෂා' if is_si else 'My Tests'}</h1><table style='border-collapse:collapse;width:100%'><thead><tr><th style='border:1px solid #ccc;padding:8px;'>Title</th><th style='border:1px solid #ccc;padding:8px;'>Date</th><th style='border:1px solid #ccc;padding:8px;'>Status</th><th style='border:1px solid #ccc;padding:8px;'>Action</th></tr></thead><tbody>{rows_html}</tbody></table><p><a href='/student-dashboard'>Back</a></p></body></html>"

@app.route("/student/test/<int:test_id>", methods=["GET", "POST"])
def student_take_test(test_id: int):
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))
    student = db.session.get(Student, student_id)
    test = db.session.get(ClassTest, test_id)
    if not student or not test or student.class_id != test.class_id:
        return "<h2>Test not found</h2>", 404
    questions = get_questions_for_homework(test.grade, test.subject, test.topic_en, test.topic_si, test.difficulty_level)
    if request.method == "POST":
        medium_key = "si" if student.medium == "Sinhala" else "en"
        def _is_correct(q):
            if is_matching_pairs_question(q):
                ok, _, _ = evaluate_matching_pairs_question(q, request.form, medium_key)
                return ok
            if is_box_input_question(q):
                ok, _, _ = evaluate_box_question(q, request.form)
                return ok
            if is_tap_select_image_question(q):
                ok, _, _ = evaluate_tap_select_question(q, request.form)
                return ok
            if is_drag_drop_group_container_question(q):
                ok, _ = evaluate_drag_drop_group_container_question(q, request.form)
                return ok
            if is_short_answer_question(q):
                return (request.form.get(f"q_{q.id}") or '').strip().casefold() == (q.correct_answer_text or '').strip().casefold()
            return (request.form.get(f"q_{q.id}") or "").strip().upper() == (q.correct_option or "").strip().upper()
        correct_answers = sum(1 for q in questions if _is_correct(q))
        total_questions = len(questions)
        score = round((correct_answers / total_questions) * 100, 2) if total_questions else 0
        existing = ClassTestSubmission.query.filter_by(class_test_id=test.id, student_id=student.id).first()
        if existing:
            db.session.delete(existing)
            db.session.flush()
        db.session.add(ClassTestSubmission(class_test_id=test.id, student_id=student.id, score=score, total_questions=total_questions, correct_answers=correct_answers))
        db.session.commit()
        return redirect(url_for("student_test_result_summary", test_id=test.id))
    medium_key = "si" if student.medium == "Sinhala" else "en"
    q_html_parts = []
    for q in questions:
        image_html = f"<img src='{escape(normalize_local_image_url(q.image_url))}' alt='Question image' class='question-image'>" if q.image_url else ""
        if is_matching_pairs_question(q):
            answer_html = render_matching_pairs_inputs(q, medium_key)
        elif is_box_input_question(q):
            answer_html = render_box_template_with_inputs(q, 'qbox')
        elif is_tap_select_image_question(q):
            answer_html = render_tap_select_image_input(q)
        elif is_drag_drop_group_container_question(q):
            answer_html = render_drag_drop_group_container_input(q, medium_key)
        elif is_short_answer_question(q):
            answer_html = f"<input type='text' name='q_{q.id}' placeholder='Type your answer'>"
        else:
            answer_html = f"<label><input type='radio' name='q_{q.id}' value='A'> A. {escape(getattr(q, f'option_a_{medium_key}'))}</label><br><label><input type='radio' name='q_{q.id}' value='B'> B. {escape(getattr(q, f'option_b_{medium_key}'))}</label><br><label><input type='radio' name='q_{q.id}' value='C'> C. {escape(getattr(q, f'option_c_{medium_key}'))}</label><br><label><input type='radio' name='q_{q.id}' value='D'> D. {escape(getattr(q, f'option_d_{medium_key}'))}</label>"
        q_html_parts.append(f"<div style='margin:16px 0;padding:12px;border:1px solid #ddd;'><p><strong>Q{q.id}.</strong> {escape(getattr(q, f'question_text_{medium_key}'))}</p>{'' if (is_tap_select_image_question(q) or is_drag_drop_group_container_question(q)) else image_html}{answer_html}</div>")
    q_html = "".join(q_html_parts)
    timer_html = f"<p><strong>{'කාලය' if student.medium == 'Sinhala' else 'Timer'}:</strong> {test.duration_minutes} {'මිනිත්තු' if student.medium == 'Sinhala' else 'minutes'}</p>" if test.duration_minutes else ""
    return f"""<!doctype html>
<html>
  <head>
    <meta name='viewport' content='width=device-width, initial-scale=1'>
    <style>
      .box-layout {{font-family:monospace;white-space:pre;line-height:1.4;}}
          .box-input {{width:14px;height:14px;min-width:14px;padding:1px;text-align:center;font-size:12px;line-height:12px;border:1.5px solid #000;border-radius:2px;display:inline-block;vertical-align:middle;margin:0 1px;font-family:monospace;box-sizing:border-box;}}
          .question-image {{
        max-width: 250px;
        width: 100%;
        height: auto;
        display: block;
        margin: 10px 0;
        border: 1px solid #ddd;
        border-radius: 6px;
      }}
      @media (max-width: 768px) {{
        .box-layout {{font-family:monospace;white-space:pre;line-height:1.4;}}
          .box-input {{width:14px;height:14px;min-width:14px;padding:1px;text-align:center;font-size:12px;line-height:12px;border:1.5px solid #000;border-radius:2px;display:inline-block;vertical-align:middle;margin:0 1px;font-family:monospace;box-sizing:border-box;}}
          .question-image {{
          max-width: 180px;
        }}
      }}
    </style>
    {tap_select_common_assets()}
    {drag_drop_group_assets()}
  </head>
  <body><h1>{escape(test.title)}</h1><p>Date: {test.test_date.strftime('%Y-%m-%d')}</p>{timer_html}<form method='post'>{q_html if q_html else '<p>No matching questions found.</p>'}<button type='submit' style='padding:10px 16px;font-weight:bold;'>{'යවන්න' if student.medium=='Sinhala' else 'Submit Test'}</button></form><p><a href='/student/tests'>Back</a></p></body>
</html>"""

@app.route("/student/test/<int:test_id>/result", methods=["GET"])
def student_test_result_summary(test_id: int):
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))
    test = db.session.get(ClassTest, test_id)
    if not test:
        return "<h2>Test not found</h2>", 404
    submission = ClassTestSubmission.query.filter_by(class_test_id=test_id, student_id=student_id).first()
    if not submission:
        return "<h2>Result not available</h2><p><a href='/student/tests'>Back to tests</a></p>", 404
    return f"<!doctype html><html><body><h1>{escape(test.title)} - Result</h1><p>Score: {submission.score}% ({submission.correct_answers}/{submission.total_questions})</p><p><a href='/student/tests'>Back to Tests</a></p></body></html>"

@app.route("/teacher/test/<int:test_id>", methods=["GET"])
def teacher_test_results(test_id: int):
    if session.get("teacher_logged_in") is not True:
        return redirect(url_for("teacher_login"))
    teacher_id = session.get("teacher_id")
    test = ClassTest.query.filter_by(id=test_id, teacher_id=int(teacher_id or 0)).first()
    if not test:
        return "<h2>Test not found</h2>", 404
    students = Student.query.filter_by(class_id=test.class_id).order_by(Student.name.asc()).all()
    submissions = ClassTestSubmission.query.filter_by(class_test_id=test.id).all()
    by_student = {s.student_id: s for s in submissions}
    avg = round(sum(s.score for s in submissions) / len(submissions), 2) if submissions else 0
    sorted_students = sorted(students, key=lambda st: by_student.get(st.id).score if st.id in by_student else -1, reverse=True)
    rows = "".join(f"<tr><td style='border:1px solid #ccc;padding:8px;'>{escape(st.name)}</td><td style='border:1px solid #ccc;padding:8px;'>{('-' if st.id not in by_student else str(by_student[st.id].score)+'%')}</td><td style='border:1px solid #ccc;padding:8px;'>{('Not Submitted' if st.id not in by_student else 'Submitted')}</td></tr>" for st in sorted_students)
    return f"<!doctype html><html><body><h1>Test Title: {escape(test.title)}</h1><p>Average Score: {avg}%</p><p>Total Students: {len(students)}</p><p>Submitted Count: {len(submissions)}</p><table style='border-collapse:collapse;width:100%'><thead><tr><th style='border:1px solid #ccc;padding:8px;'>Student Name</th><th style='border:1px solid #ccc;padding:8px;'>Score</th><th style='border:1px solid #ccc;padding:8px;'>Status</th></tr></thead><tbody>{rows}</tbody></table><p><a href='/teacher/class/{test.class_id}'>Back to Class</a></p></body></html>"

@app.route("/update-class-test-db", methods=["GET"])
def update_class_test_db() -> tuple:
    try:
        db.create_all()
        db.session.commit()
        return jsonify({"success": True, "message": "Class test tables ensured successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Class test DB update failed: {exc}"}), 500

@app.route("/update-question-attempt-db", methods=["GET"])
def update_question_attempt_db() -> tuple:
    try:
        db.create_all()
        db.session.commit()
        return jsonify({"success": True, "message": "Student question attempt table ensured successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Question attempt DB update failed: {exc}"}), 500


@app.route("/update-homework-db", methods=["GET"])
def update_homework_db() -> tuple:
    try:
        db.create_all()
        db.session.commit()
        return jsonify({"success": True, "message": "Homework tables ensured successfully without deleting existing data"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Homework DB update failed: {exc}"}), 500


@app.route("/update-family-registration-db", methods=["GET"])
def update_family_registration_db() -> tuple:
    try:
        ensure_student_username_schema()
        ensure_family_registration_schema()
        return jsonify({"success": True, "message": "Family registration database updated successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Family registration DB update failed: {exc}"}), 500


@app.route("/student/revision-session", methods=["GET"])
def student_revision_session():
    if "student_id" not in session:
        return redirect(url_for("student_login"))
    ensure_revision_queue_tables()
    student_id = int(session["student_id"])
    generate_student_revision_queue(student_id)
    queued = StudentRevisionQueue.query.filter_by(student_id=student_id, is_completed=False).filter(StudentRevisionQueue.due_date <= date.today()).order_by(StudentRevisionQueue.priority_score.desc(), StudentRevisionQueue.due_date.asc()).all()
    if not queued:
        return "<h2>No revision items due right now.</h2><p><a href='/student-dashboard'>Back to Dashboard</a></p>"
    rows = []
    for item in queued[:20]:
        mastery = StudentSkillMastery.query.filter_by(student_id=student_id, skill_code=item.skill_code).first()
        score = float(mastery.mastery_score or 0) if mastery else 0.0
        is_correct = score >= 70
        interval_days, success_count = _next_revision_interval(item.successful_revisions, is_correct)
        if is_correct:
            item.successful_revisions = success_count
            item.interval_days = interval_days
            item.due_date = date.today() + timedelta(days=interval_days)
            item.priority_score = max(0.0, float(item.priority_score or 0) - 10)
            if item.priority_score <= 0:
                item.is_completed = True
        else:
            item.successful_revisions = 0
            item.interval_days = interval_days
            item.due_date = date.today() + timedelta(days=1)
            item.priority_score = min(100.0, float(item.priority_score or 0) + 20)
        if mastery:
            mastery.status_en, mastery.status_si = mastery_status_labels(float(mastery.mastery_score or 0))
        rows.append(f"<tr><td>{escape(item.skill_code)}</td><td>{escape(item.revision_reason)}</td><td>{int(item.priority_score)}</td><td>{item.due_date}</td></tr>")
    db.session.commit()
    return f"<!doctype html><html><body><h1>Revision Session</h1><p>Weakest skills prioritized first.</p><table border='1' cellpadding='6'><tr><th>Skill</th><th>Reason</th><th>Priority</th><th>Next Due</th></tr>{''.join(rows)}</table><p><a href='/student-dashboard'>Back to Dashboard</a></p></body></html>"


with app.app_context():
    run_startup_migrations()


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        run_startup_migrations()
        normalize_existing_grade_data()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
