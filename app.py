import json
import os
import random
import re
from datetime import date, datetime, timedelta
from fractions import Fraction
from html import escape
from urllib.parse import quote_plus

from flask import Flask, jsonify, redirect, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from openai import OpenAI
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

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
        "no_questions": "No Grade 6 Math questions available.",
        "test_title": "SkillScan Test - Grade 6 Math",
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
        "no_questions": "6 ශ්‍රේණියේ ගණිත ප්‍රශ්න නොමැත.",
        "test_title": "SkillScan පරීක්ෂණය - 6 ශ්‍රේණිය ගණිතය",
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


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    grade = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    parent_email = db.Column(db.String(120), nullable=True)
    mobile = db.Column(db.String(20), unique=True, nullable=False)
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


class Class(db.Model):
    __tablename__ = "class"

    id = db.Column(db.Integer, primary_key=True)
    class_name = db.Column(db.String(120), nullable=False)
    grade = db.Column(db.String(20), nullable=False)
    teacher_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    grade = db.Column(db.String(20), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    topic = db.Column(db.String(150), nullable=False)
    topic_en = db.Column(db.String(150), nullable=False)
    topic_si = db.Column(db.String(150), nullable=False)
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


class ParentNotification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False)
    parent_email = db.Column(db.String(120), nullable=False)
    message_en = db.Column(db.Text, nullable=False)
    message_si = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)




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


@app.route("/")
def home() -> str:
    return "Spiral Learning System Running"


@app.route("/create-db")
def create_db() -> str:
    db.create_all()
    return "Database tables created successfully"


@app.route("/register-form", methods=["GET"])
def register_form() -> str:
    selected_medium = resolve_medium(request.args.get("medium"))
    english_selected = "selected" if selected_medium == "English" else ""
    sinhala_selected = "selected" if selected_medium == "Sinhala" else ""

    return f"""
    <!doctype html>
    <html lang="{'si' if selected_medium == 'Sinhala' else 'en'}">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{t(selected_medium, "student_registration")}</title>
      </head>
      <body>
        <h1>{t(selected_medium, "student_registration")}</h1>
        <form method="post" action="/register">
          <label>
            {t(selected_medium, "name")}:
            <input type="text" name="name" required>
          </label>
          <br><br>
          <label>
            {t(selected_medium, "grade")}:
            <select name="grade" required>{grade_options_html()}</select>
          </label>
          <br><br>
          <label>
            {t(selected_medium, "medium")}:
            <select name="medium" required>
              <option value="English" {english_selected}>English</option>
              <option value="Sinhala" {sinhala_selected}>Sinhala</option>
            </select>
          </label>
          <br><br>
          <label>
            {t(selected_medium, "email")}:
            <input type="email" name="email" required>
          </label>
          <br><br>
          <label>
            Parent Email:
            <input type="email" name="parent_email" required>
          </label>
          <br><br>
          <label>
            {t(selected_medium, "mobile")}:
            <input type="text" name="mobile" required>
          </label>
          <br><br>
          <label>
            Password:
            <input type="password" name="password" required>
          </label>
          <br><br>
          <button type="submit">{t(selected_medium, "register")}</button>
        </form>
      </body>
    </html>
    """


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

    required_fields = ["name", "grade", "email", "parent_email", "mobile", "medium", "password"]
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

    email = data["email"].strip()
    parent_email = data["parent_email"].strip()
    mobile = data["mobile"].strip()

    if Student.query.filter_by(email=email).first():
        if is_form_submission:
            return "<h2>Error: Email already exists</h2><p><a href='/register-form'>Back</a></p>", 409
        return jsonify({"success": False, "message": "Email already exists"}), 409

    if Student.query.filter_by(mobile=mobile).first():
        if is_form_submission:
            return "<h2>Error: Mobile already exists</h2><p><a href='/register-form'>Back</a></p>", 409
        return jsonify({"success": False, "message": "Mobile already exists"}), 409

    student = Student(
        name=data["name"].strip(),
        grade=grade,
        medium=medium,
        email=email,
        parent_email=parent_email,
        mobile=mobile,
        password_hash=generate_password_hash(data["password"]),
    )

    db.session.add(student)
    db.session.commit()

    if is_form_submission:
        return (
            "<h2>Success: Student registered successfully</h2>"
            "<p><a href='/register-form'>Register another student</a></p>",
            201,
        )

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
                    "parent_email": student.parent_email,
                    "mobile": student.mobile,
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


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return """
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Student Login</title>
          </head>
          <body>
            <h1>Student Login</h1>
            <form method="post" action="/login">
              <label>Email: <input type="email" name="email" required></label><br><br>
              <label>Password: <input type="password" name="password" required></label><br><br>
              <button type="submit">Login</button>
            </form>
          </body>
        </html>
        """

    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""

    try:
        ensure_gamification_columns()
        ensure_streak_columns()
        ensure_subscription_columns()
    except Exception:
        db.session.rollback()

    student = Student.query.filter_by(email=email).first()
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
        previous_result = (
            StudentResult.query.filter_by(student_id=student_id, grade="6", subject="Math")
            .order_by(StudentResult.created_at.desc(), StudentResult.id.desc())
            .first()
        )
    if not student_id:
        return redirect(url_for("login"))

    student = db.session.get(Student, student_id)
    if not student:
        session.pop("student_id", None)
        return redirect(url_for("login"))

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
            "latest_result": "Latest SkillScan Result",
            "date": "Date",
            "score": "Score",
            "level": "Level",
            "xp": "XP",
            "xp_sinhala": "ලකුණු",
            "progress_to_next_level": "Progress to next level",
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
            "current_streak": "Current streak",
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
            "latest_result": "අවසන් SkillScan ප්‍රතිඵලය",
            "date": "දිනය",
            "score": "ලකුණු",
            "level": "මට්ටම",
            "xp": "XP",
            "xp_sinhala": "ලකුණු",
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
            "current_streak": "වත්මන් අඛණ්ඩ දින",
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

    practice_rows = "".join(
        f"""
        <tr>
          <td style='border:1px solid #ccc;padding:8px;'>{getattr(attempt, f'topic_{practice_medium_key}')}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{attempt.score}%</td>
          <td style='border:1px solid #ccc;padding:8px;'>{attempt.correct_answers}/{attempt.total_questions}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{attempt.created_at.strftime('%Y-%m-%d %H:%M:%S')}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{improvement_by_attempt.get(attempt.id, '-')}</td>
        </tr>
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
    <!doctype html>
    <html lang='{'si' if language == 'si' else 'en'}'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>{text["dashboard"]}</title>
        <script src='https://cdn.jsdelivr.net/npm/chart.js'></script>
      </head>
      <body>
        <h1>{text["dashboard"]}</h1>
        {f"<p style='padding:10px;border-radius:8px;background:#fff3cd;color:#7a4f00;border:1px solid #ffe69c;'>{expired_message}</p>" if expired_message else ""}
        <p><strong>{text["name"]}:</strong> {student.name}</p>
        <p><strong>{text["grade"]}:</strong> {student.grade}</p>
        <p><strong>{text["medium"]}:</strong> {student.medium}</p>
        <p><strong>{text['xp']} ({text['xp_sinhala']}):</strong> {student.xp}</p>
        <p><strong>{text['level']}:</strong> {student.level}</p>
        <p><strong>🔥 {text['current_streak']}:</strong> {student.current_streak or 0}</p>
        <p><strong>🏆 {text['longest_streak']}:</strong> {student.longest_streak or 0}</p>
        <p><strong>{text['goal_completed_today'] if completed_activity_today else text['complete_one_activity_today']}</strong></p>
        <p><strong>{text['progress_to_next_level']}:</strong> {student.xp % 100}%</p>

        {latest_html}

        <h2>{text["progress_overview"]}</h2>
        <div style='max-width:900px; margin-bottom:20px;'>
          <canvas id='progressOverviewChart' height='120'></canvas>
        </div>

        <h2>{text["topic_trend"]}</h2>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{text["topic"] if "topic" in text else "Topic"}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{text["last_score"]}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{text["previous_score"]}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{text["trend"]}</th>
            </tr>
          </thead>
          <tbody>
            {''.join(topic_trend_rows) if topic_trend_rows else "<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>No topic trend data available.</td></tr>"}
          </tbody>
        </table>

        <h2>{text["result_history"]}</h2>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{text["date"]}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{text["score"]}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{text["level"]}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{text["correct_answers"]}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{text["medium"]}</th>
            </tr>
          </thead>
          <tbody>
            {history_rows if history_rows else "<tr><td colspan='5' style='border:1px solid #ccc;padding:8px;'>No results found.</td></tr>"}
          </tbody>
        </table>

        <h2>{text["topic_performance"]}</h2>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Topic</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Correct/Total</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Percentage</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Status</th>
            </tr>
          </thead>
          <tbody>
            {topic_rows if topic_rows else "<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>No topic performance available.</td></tr>"}
          </tbody>
        </table>

        <h2>{text["latest_practice_attempts"]}</h2>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Topic</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{text["score"]}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{text["correct_answers"]}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{text["date"]}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{text["improvement"]}</th>
            </tr>
          </thead>
          <tbody>
            {practice_rows if practice_rows else "<tr><td colspan='5' style='border:1px solid #ccc;padding:8px;'>No practice attempts found.</td></tr>"}
          </tbody>
        </table>
        <h2>{"මගේ ඊළඟ පියවර" if language == "si" else "My Next Steps"}</h2>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{"මාතෘකාව" if language == "si" else "Topic"}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{"දැනට මට්ටම" if language == "si" else "Current Level"}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{"නිර්දේශිත ක්‍රියාව" if language == "si" else "Recommended Action"}</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>{"සබැඳිය" if language == "si" else "Link"}</th>
            </tr>
          </thead>
          <tbody>
            {rec_rows if rec_rows else "<tr><td colspan='4' style='border:1px solid #ccc;padding:8px;'>No recommendations found.</td></tr>"}
          </tbody>
        </table>

        <p>
          <a href='/learning-path'>{text["my_learning_path"]}</a>
          &nbsp;|&nbsp;
          <a href='/test'>{text["take_test"]}</a>
          &nbsp;|&nbsp;
          <a href='/leaderboard'>{text['leaderboard']}</a>
          &nbsp;|&nbsp;
          <a href='/logout'>{text["logout"]}</a>
        </p>
        <script>
          const progressCtx = document.getElementById('progressOverviewChart');
          if (progressCtx) {{
            const practiceData = {json.dumps(chart_practice_points)};
            const datasets = [
              {{
                label: 'SkillScan',
                data: {json.dumps(chart_result_scores)},
                borderColor: '#2563eb',
                backgroundColor: 'rgba(37,99,235,0.2)',
                tension: 0.25,
                fill: false
              }}
            ];

            if (practiceData.length) {{
              datasets.push({{
                label: 'Practice',
                data: practiceData,
                parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
                borderColor: '#16a34a',
                backgroundColor: 'rgba(22,163,74,0.2)',
                tension: 0.25,
                fill: false
              }});
            }}

            new Chart(progressCtx, {{
              type: 'line',
              data: {{
                labels: {json.dumps(chart_labels)},
                datasets: datasets
              }},
              options: {{
                responsive: true,
                scales: {{
                  x: {{ title: {{ display: true, text: '{text["date"]}' }} }},
                  y: {{ title: {{ display: true, text: '{text["score"]}' }}, min: 0, max: 100 }}
                }}
              }}
            }});
          }}
        </script>
      </body>
    </html>
    """




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
        whatsapp_button_text = "WhatsApp මගින් යවන්න" if student_medium == "Sinhala" else "Send via WhatsApp"
        parent_mobile = (student.mobile or "").strip() if student else ""
        whatsapp_link = f"https://wa.me/{parent_mobile}?text={quote_plus(message)}"
        whatsapp_button_html = (
            f"<a href='{whatsapp_link}' target='_blank' rel='noopener noreferrer'>{escape(whatsapp_button_text)}</a>"
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


def get_teacher_credentials() -> tuple[str, str]:
    return (
        os.environ.get("TEACHER_EMAIL", "teacher@spiral.com"),
        os.environ.get("TEACHER_PASSWORD", "teacher123"),
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

    return f"""
    <!doctype html>
    <html lang='en'>
      <head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Class Details</title></head>
      <body>
        <h1>Class: {escape(classroom.class_name)}</h1>
        <p>Grade: {display_grade(classroom.grade)}</p>
        <p><a href='/teacher/assign-students/{classroom.id}'>Assign Students</a></p>
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

@app.route("/teacher-logout", methods=["GET"])
def teacher_logout():
    session.pop("teacher_logged_in", None)
    session.pop("teacher_id", None)
    return redirect(url_for("teacher_login"))


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


def parse_question_form_data() -> tuple[dict, str | None]:
    grade = (request.form.get("grade") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    topic = (request.form.get("topic") or "").strip()
    question_text_en = (request.form.get("question_text_en") or "").strip()
    question_text_si = (request.form.get("question_text_si") or "").strip()
    option_a = (request.form.get("option_a") or "").strip()
    option_b = (request.form.get("option_b") or "").strip()
    option_c = (request.form.get("option_c") or "").strip()
    option_d = (request.form.get("option_d") or "").strip()
    correct_option = (request.form.get("correct_option") or "").strip().upper()
    difficulty_level_raw = (request.form.get("difficulty_level") or "1").strip()

    required_values = [
        grade,
        subject,
        topic,
        question_text_en,
        question_text_si,
        option_a,
        option_b,
        option_c,
        option_d,
        correct_option,
    ]
    if any(value == "" for value in required_values):
        return {}, "All fields are required."

    grade = normalize_grade(grade)
    if not is_valid_grade(grade):
        return {}, "Grade must be one of: 1-10, OL, AL."

    if correct_option not in {"A", "B", "C", "D"}:
        return {}, "Correct answer must be one of A, B, C, or D."
    try:
        difficulty_level = int(difficulty_level_raw)
    except ValueError:
        return {}, "Difficulty level must be a number between 1 and 5."
    if difficulty_level not in {1, 2, 3, 4, 5}:
        return {}, "Difficulty level must be between 1 and 5."

    return {
        "grade": grade,
        "subject": subject,
        "topic": topic,
        "question_text_en": question_text_en,
        "question_text_si": question_text_si,
        "option_a": option_a,
        "option_b": option_b,
        "option_c": option_c,
        "option_d": option_d,
        "correct_option": correct_option,
        "difficulty_level": difficulty_level,
    }, None


def render_question_form(action: str, data: dict, page_title: str, submit_label: str, error: str = "") -> str:
    error_html = f"<p style='color:red;'>{escape(error)}</p>" if error else ""
    difficulty_level = str(data.get("difficulty_level", "1"))
    return f"""
    <!doctype html>
    <html lang="en">
      <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{page_title}</title></head>
      <body>
        <h1>{page_title}</h1>
        {error_html}
        <form method="post" action="{action}">
          <label>Grade: <select name="grade" required>{grade_options_html(data.get('grade', ''))}</select></label><br><br>
          <label>Subject: <input type="text" name="subject" value="{escape(data.get('subject', ''))}" required></label><br><br>
          <label>Topic: <input type="text" name="topic" value="{escape(data.get('topic', ''))}" required></label><br><br>
          <label>Question text EN:<br><textarea name="question_text_en" rows="4" cols="80" required>{escape(data.get('question_text_en', ''))}</textarea></label><br><br>
          <label>Question text SI:<br><textarea name="question_text_si" rows="4" cols="80" required>{escape(data.get('question_text_si', ''))}</textarea></label><br><br>
          <label>Option A: <input type="text" name="option_a" value="{escape(data.get('option_a', ''))}" required></label><br><br>
          <label>Option B: <input type="text" name="option_b" value="{escape(data.get('option_b', ''))}" required></label><br><br>
          <label>Option C: <input type="text" name="option_c" value="{escape(data.get('option_c', ''))}" required></label><br><br>
          <label>Option D: <input type="text" name="option_d" value="{escape(data.get('option_d', ''))}" required></label><br><br>
          <label>Correct Answer (A/B/C/D): <input type="text" name="correct_option" maxlength="1" value="{escape(data.get('correct_option', ''))}" required></label><br><br>
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
      </body>
    </html>
    """


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
        <p><a href='/admin/questions'>Manage Questions</a></p>
        <p><a href='/admin/classes'>Manage Classes</a></p>
        <p><a href='/admin/premium'>Premium Management</a></p>
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
          <td style='border:1px solid #ccc;padding:8px;'>{student.xp}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.level}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.created_at.strftime('%Y-%m-%d %H:%M:%S')}</td>
          <td style='border:1px solid #ccc;padding:8px;'><a href='/admin/student/{student.id}'>View Details</a></td>
        </tr>
        """
        for student in students
    )
    return f"""
    <h1>Manage Students</h1>
    <p><a href='/admin-dashboard'>Back to Admin Dashboard</a></p>
    <table style='border-collapse:collapse;width:100%;'>
      <thead><tr><th style='border:1px solid #ccc;padding:8px;'>ID</th><th style='border:1px solid #ccc;padding:8px;'>Name</th><th style='border:1px solid #ccc;padding:8px;'>Grade</th><th style='border:1px solid #ccc;padding:8px;'>Medium</th><th style='border:1px solid #ccc;padding:8px;'>Email</th><th style='border:1px solid #ccc;padding:8px;'>Parent Email</th><th style='border:1px solid #ccc;padding:8px;'>Mobile</th><th style='border:1px solid #ccc;padding:8px;'>XP</th><th style='border:1px solid #ccc;padding:8px;'>Level</th><th style='border:1px solid #ccc;padding:8px;'>Created At</th><th style='border:1px solid #ccc;padding:8px;'>Action</th></tr></thead>
      <tbody>{student_rows if student_rows else "<tr><td colspan='11' style='border:1px solid #ccc;padding:8px;'>No students found.</td></tr>"}</tbody>
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


@app.route("/admin/questions", methods=["GET"])
def admin_questions():
    admin_redirect = admin_session_required()
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


@app.route("/admin/add-question", methods=["GET", "POST"])
def admin_add_question():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect
    if request.method == "GET":
        return render_question_form("/admin/add-question", {}, "Add New Question", "Save Question")

    form_data, error = parse_question_form_data()
    if error:
        return render_question_form("/admin/add-question", request.form, "Add New Question", "Save Question", error), 400

    question = Question(
        grade=form_data["grade"],
        subject=form_data["subject"],
        topic=form_data["topic"],
        topic_en=form_data["topic"],
        topic_si=form_data["topic"],
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
        correct_option=form_data["correct_option"],
        explanation_en="N/A",
        explanation_si="N/A",
        difficulty_level=form_data["difficulty_level"],
    )
    db.session.add(question)
    db.session.commit()
    return redirect("/admin/questions")


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
                "question_text_en": question.question_text_en,
                "question_text_si": question.question_text_si,
                "option_a": question.option_a_en,
                "option_b": question.option_b_en,
                "option_c": question.option_c_en,
                "option_d": question.option_d_en,
                "correct_option": question.correct_option,
                "difficulty_level": question.difficulty_level or 1,
            },
            "Edit Question",
            "Update Question",
        )

    form_data, error = parse_question_form_data()
    if error:
        return render_question_form(f"/admin/edit-question/{question_id}", request.form, "Edit Question", "Update Question", error), 400

    question.grade = form_data["grade"]
    question.subject = form_data["subject"]
    question.topic = form_data["topic"]
    question.topic_en = form_data["topic"]
    question.topic_si = form_data["topic"]
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


@app.route("/admin/generate-questions", methods=["GET", "POST"])
def admin_generate_questions():
    admin_redirect = admin_session_required()
    if admin_redirect:
        return admin_redirect

    if request.method == "GET":
        return """
        <h2>Generate Questions (Bulk)</h2>
        <form method='post' action='/admin/generate-questions'>
          <label>Grade: <input type='text' name='grade' value='6' required></label><br><br>
          <label>Subject: <input type='text' name='subject' value='Math' required></label><br><br>
          <label>Topic: <input type='text' name='topic' value='Fractions' required></label><br><br>
          <label>Number of questions: <input type='number' name='question_count' min='1' max='200' value='10' required></label><br><br>
          <label>Difficulty level (1–5): <input type='number' name='difficulty_level' min='1' max='5' value='1' required></label><br><br>
          <button type='submit'>Generate</button>
        </form>
        <p><a href='/admin/questions'>Back to Questions</a></p>
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
        return """
        <h2>AI Question Generator</h2>
        <form method='post' action='/admin/ai-generate'>
          <label>Grade: <input type='text' name='grade' value='6' required></label><br><br>
          <label>Subject: <input type='text' name='subject' value='Math' required></label><br><br>
          <label>Topic: <input type='text' name='topic' value='Fractions' required></label><br><br>
          <label>Number of questions: <input type='number' name='question_count' min='1' max='100' value='10' required></label><br><br>
          <label>Difficulty level (1–5): <input type='number' name='difficulty_level' min='1' max='5' value='1' required></label><br><br>
          <button type='submit'>Generate with AI</button>
        </form>
        <p><a href='/admin/questions'>Back to Questions</a></p>
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


@app.route("/update-login-db", methods=["GET"])
def update_login_db() -> tuple:
    try:
        db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)"))
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


@app.route("/update-results-db", methods=["GET"])
def update_results_db() -> tuple:
    try:
        db.create_all()
        db.session.commit()
        return jsonify({"success": True, "message": "Result tables ensured successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Results DB update failed: {exc}"}), 500


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
    selected_medium = resolve_medium(request.args.get("medium"))

    questions = (
        Question.query.filter_by(grade="6", subject="Math")
        .order_by(Question.id.asc())
        .all()
    )
    medium_key = "en" if selected_medium == "English" else "si"
    streak_message = ""

    question_blocks = []
    for q in questions:
        question_text = getattr(q, f"question_text_{medium_key}")
        option_a = getattr(q, f"option_a_{medium_key}")
        option_b = getattr(q, f"option_b_{medium_key}")
        option_c = getattr(q, f"option_c_{medium_key}")
        option_d = getattr(q, f"option_d_{medium_key}")

        question_blocks.append(
            f"""
            <div style='margin:16px 0;padding:12px;border:1px solid #ddd;'>
              <p><strong>Q{q.id}.</strong> {question_text}</p>
              <label><input type='radio' name='q_{q.id}' value='A'> A. {option_a}</label><br>
              <label><input type='radio' name='q_{q.id}' value='B'> B. {option_b}</label><br>
              <label><input type='radio' name='q_{q.id}' value='C'> C. {option_c}</label><br>
              <label><input type='radio' name='q_{q.id}' value='D'> D. {option_d}</label>
            </div>
            """
        )

    english_selected = "selected" if selected_medium == "English" else ""
    sinhala_selected = "selected" if selected_medium == "Sinhala" else ""

    return f"""
    <!doctype html>
    <html lang='{'si' if selected_medium == 'Sinhala' else 'en'}'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>Test Page</title>
      </head>
      <body>
        <h1>{t(selected_medium, 'test_title')}</h1>
        <form method='get' action='/test' style='margin-bottom:20px;'>
          <label>{t(selected_medium, 'language')}:
            <select name='medium'>
              <option value='English' {english_selected}>English</option>
              <option value='Sinhala' {sinhala_selected}>Sinhala</option>
            </select>
          </label>
          <button type='submit'>{t(selected_medium, 'change_language')}</button>
        </form>
        <form method='post' action='/submit-test'>
          <input type='hidden' name='medium' value='{selected_medium}'>
          <p>{t(selected_medium, 'selected_language')}: <strong>{selected_medium}</strong></p>
          {''.join(question_blocks) if question_blocks else f"<p>{t(selected_medium, 'no_questions')}</p>"}
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
    selected_medium = resolve_medium(request.form.get("medium") or request.args.get("medium"))
    streak_message = ""

    medium_key = "en" if selected_medium == "English" else "si"
    questions = (
        Question.query.filter_by(grade="6", subject="Math")
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
        student_answer = request.form.get(f"q_{q.id}", "").strip().upper()
        correct_answer = q.correct_option.strip().upper()

        if student_answer == correct_answer:
            correct_answers += 1
            topic_stats[topic_name]["correct"] += 1
        db.session.add(
            StudentQuestionAttempt(
                student_id=session.get("student_id"),
                question_id=q.id,
                source_type="SkillScan",
                is_correct=(student_answer == correct_answer),
            )
        )
        if student_answer == correct_answer:
            continue

        question_text = getattr(q, f"question_text_{medium_key}")
        explanation_text = getattr(q, f"explanation_{medium_key}")

        if student_answer in option_label_key:
            student_answer_text = getattr(q, f"{option_label_key[student_answer]}_{medium_key}")
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

    student_id = session.get("student_id")
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
        grade="6",
        subject="Math",
        medium=selected_medium,
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
            grade="6",
            subject="Math",
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


    selected_medium = resolve_medium(request.values.get("medium") or student.medium)
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
        question_blocks.append(
            f"""
            <div style='margin:16px 0;padding:12px;border:1px solid #ddd;'>
              <p><strong>Q{q.id}.</strong> {getattr(q, f"question_text_{medium_key}")}</p>
              <label><input type='radio' name='q_{q.id}' value='A'> A. {getattr(q, f"option_a_{medium_key}")}</label><br>
              <label><input type='radio' name='q_{q.id}' value='B'> B. {getattr(q, f"option_b_{medium_key}")}</label><br>
              <label><input type='radio' name='q_{q.id}' value='C'> C. {getattr(q, f"option_c_{medium_key}")}</label><br>
              <label><input type='radio' name='q_{q.id}' value='D'> D. {getattr(q, f"option_d_{medium_key}")}</label>
            </div>
            """
        )
    return f"""
    <!doctype html>
    <html lang='{'si' if selected_medium == 'Sinhala' else 'en'}'>
      <head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{t(selected_medium, 'retest_weak_topics')}</title></head>
      <body>
        <h1>{t(selected_medium, 'retest_weak_topics')}</h1>
        <p><strong>{t(selected_medium, 'total_questions')}:</strong> {len(questions)}</p>
        <form method='post' action='/retest-weak'>
          <input type='hidden' name='medium' value='{selected_medium}'>
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
          .price {{ font-size: 1.5rem; font-weight: 700; color: #0f7b43; margin: 6px 0 0; }}
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
    selected_medium = resolve_medium(request.args.get("medium"))

    medium_key = "en" if selected_medium == "English" else "si"
    student_id = session.get("student_id")
    student = db.session.get(Student, student_id) if student_id else None
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
        option_a = getattr(q, f"option_a_{medium_key}")
        option_b = getattr(q, f"option_b_{medium_key}")
        option_c = getattr(q, f"option_c_{medium_key}")
        option_d = getattr(q, f"option_d_{medium_key}")
        question_blocks.append(
            f"""
            <div style='margin:16px 0;padding:12px;border:1px solid #ddd;'>
              <p><strong>Q{q.id}.</strong> {question_text}</p>
              <label><input type='radio' name='q_{q.id}' value='A'> A. {option_a}</label><br>
              <label><input type='radio' name='q_{q.id}' value='B'> B. {option_b}</label><br>
              <label><input type='radio' name='q_{q.id}' value='C'> C. {option_c}</label><br>
              <label><input type='radio' name='q_{q.id}' value='D'> D. {option_d}</label>
            </div>
            """
        )

    english_selected = "selected" if selected_medium == "English" else ""
    sinhala_selected = "selected" if selected_medium == "Sinhala" else ""
    display_topic = topic or "-"

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
        <p><strong>{t(selected_medium, 'topic_name')}:</strong> {display_topic}</p>
        <p><strong>{t(selected_medium, 'difficulty_label')}:</strong> {selected_question_difficulty}</p>
        <form method='get' action='/practice' style='margin-bottom:20px;'>
          <input type='hidden' name='grade' value='{grade}'>
          <input type='hidden' name='subject' value='{subject}'>
          <input type='hidden' name='topic' value='{topic}'>
          <label>{t(selected_medium, 'language')}:
            <select name='medium'>
              <option value='English' {english_selected}>English</option>
              <option value='Sinhala' {sinhala_selected}>Sinhala</option>
            </select>
          </label>
          <button type='submit'>{t(selected_medium, 'change_language')}</button>
        </form>
        <form method='post' action='/submit-practice'>
          <input type='hidden' name='grade' value='{grade}'>
          <input type='hidden' name='subject' value='{subject}'>
          <input type='hidden' name='topic' value='{topic}'>
          <input type='hidden' name='medium' value='{selected_medium}'>
          {''.join(question_blocks) if question_blocks else f"<p>{t(selected_medium, 'no_questions')}</p>"}
          <button type='submit'>{t(selected_medium, 'submit')}</button>
        </form>
        <p><a href='/student-dashboard'>{t(selected_medium, 'back_to_dashboard')}</a></p>
      </body>
    </html>
    """


@app.route("/submit-practice", methods=["POST"])
def submit_practice() -> str:
    db.create_all()
    selected_medium = resolve_medium(request.form.get("medium") or request.args.get("medium"))
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
        student_answer = request.form.get(f"q_{q.id}", "").strip().upper()
        correct_answer = q.correct_option.strip().upper()
        if student_answer == correct_answer:
            correct_answers += 1
        db.session.add(
            StudentQuestionAttempt(
                student_id=student_id,
                question_id=q.id,
                source_type="Practice",
                is_correct=(student_answer == correct_answer),
            )
        )

        question_text = getattr(q, f"question_text_{medium_key}")
        explanation_text = getattr(q, f"explanation_{medium_key}")
        student_answer_text = (
            getattr(q, f"{option_label_key[student_answer]}_{medium_key}")
            if student_answer in option_label_key
            else t(selected_medium, "not_answered")
        )
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
        medium=selected_medium,
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


@app.route("/update-question-attempt-db", methods=["GET"])
def update_question_attempt_db() -> tuple:
    try:
        db.create_all()
        db.session.commit()
        return jsonify({"success": True, "message": "Student question attempt table ensured successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Question attempt DB update failed: {exc}"}), 500


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        normalize_existing_grade_data()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
