import os
from datetime import datetime

from flask import Flask, jsonify, redirect, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

SUPPORTED_MEDIA = {"English", "Sinhala"}

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
    mobile = db.Column(db.String(20), unique=True, nullable=False)
    medium = db.Column(db.String(20), nullable=False, default="English")
    password_hash = db.Column(db.String(255), nullable=True)
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
            <input type="text" name="grade" required>
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

    required_fields = ["name", "grade", "email", "mobile", "medium", "password"]
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

    email = data["email"].strip()
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
        grade=data["grade"].strip(),
        medium=medium,
        email=email,
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
    student = Student.query.filter_by(email=email).first()
    if not student or not student.password_hash or not check_password_hash(student.password_hash, password):
        return "<h2>Invalid email or password</h2><p><a href='/login'>Try again</a></p>", 401

    session["student_id"] = student.id
    return redirect(url_for("student_dashboard"))


@app.route("/student-dashboard", methods=["GET"])
def student_dashboard():
    student_id = session.get("student_id")
    if not student_id:
        return redirect(url_for("login"))

    student = db.session.get(Student, student_id)
    if not student:
        session.pop("student_id", None)
        return redirect(url_for("login"))

    result_history = (
        StudentResult.query.filter_by(student_id=student.id)
        .order_by(StudentResult.created_at.desc(), StudentResult.id.desc())
        .all()
    )
    latest_result = result_history[0] if result_history else None
    ui_text = {
        "en": {
            "dashboard": "Student Dashboard",
            "name": "Name",
            "grade": "Grade",
            "medium": "Medium",
            "latest_result": "Latest SkillScan Result",
            "date": "Date",
            "score": "Score",
            "level": "Level",
            "correct_answers": "Correct Answers",
            "result_history": "Result History",
            "topic_performance": "Topic-wise Performance (Latest Result)",
            "take_test": "Take SkillScan Test",
            "logout": "Logout",
        },
        "si": {
            "dashboard": "ශිෂ්‍ය ඩෑෂ්බෝඩ්",
            "name": "නම",
            "grade": "ශ්‍රේණිය",
            "medium": "මාධ්‍යය",
            "latest_result": "අවසන් SkillScan ප්‍රතිඵලය",
            "date": "දිනය",
            "score": "ලකුණු",
            "level": "මට්ටම",
            "correct_answers": "නිවැරදි පිළිතුරු",
            "result_history": "ප්‍රතිඵල ඉතිහාසය",
            "topic_performance": "මාතෘකා අනුව ක්‍රියාකාරීත්වය",
            "take_test": "SkillScan පරීක්ෂණය ආරම්භ කරන්න",
            "logout": "ඉවත් වන්න",
        },
    }
    language = "si" if student.medium == "Sinhala" else "en"
    text = ui_text[language]
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
      </head>
      <body>
        <h1>{text["dashboard"]}</h1>
        <p><strong>{text["name"]}:</strong> {student.name}</p>
        <p><strong>{text["grade"]}:</strong> {student.grade}</p>
        <p><strong>{text["medium"]}:</strong> {student.medium}</p>

        {latest_html}

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

        <p>
          <a href='/test'>{text["take_test"]}</a>
          &nbsp;|&nbsp;
          <a href='/logout'>{text["logout"]}</a>
        </p>
      </body>
    </html>
    """


@app.route("/logout", methods=["GET"])
def logout():
    session.pop("student_id", None)
    return redirect(url_for("login"))


def get_admin_credentials() -> tuple[str, str]:
    return (
        os.environ.get("ADMIN_EMAIL", "admin@spiral.com"),
        os.environ.get("ADMIN_PASSWORD", "admin123"),
    )


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
    if session.get("admin_logged_in") is not True:
        return redirect(url_for("admin_login"))

    total_students = Student.query.count()
    total_attempts = StudentResult.query.count()
    average_score = db.session.query(db.func.avg(StudentResult.score)).scalar()
    average_score = round(float(average_score or 0), 2)

    latest_results = (
        StudentResult.query.order_by(StudentResult.created_at.desc(), StudentResult.id.desc()).limit(10).all()
    )
    students = Student.query.order_by(Student.created_at.desc(), Student.id.desc()).all()

    result_rows = "".join(
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
        for result in latest_results
    )

    student_rows = "".join(
        f"""
        <tr>
          <td style='border:1px solid #ccc;padding:8px;'>{student.name}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.grade}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.medium}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.email}</td>
          <td style='border:1px solid #ccc;padding:8px;'>{student.mobile}</td>
        </tr>
        """
        for student in students
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
        <p><strong>Total students:</strong> {total_students}</p>
        <p><strong>Total SkillScan attempts:</strong> {total_attempts}</p>
        <p><strong>Average score:</strong> {average_score}%</p>
        <p><a href='/admin-logout'>Logout</a></p>

        <h2>Latest 10 Test Results</h2>
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
            {result_rows if result_rows else "<tr><td colspan='7' style='border:1px solid #ccc;padding:8px;'>No test results found.</td></tr>"}
          </tbody>
        </table>

        <h2>Student List</h2>
        <table style='border-collapse:collapse;width:100%;'>
          <thead>
            <tr>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Name</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Grade</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Medium</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Email</th>
              <th style='border:1px solid #ccc;padding:8px;text-align:left;'>Mobile</th>
            </tr>
          </thead>
          <tbody>
            {student_rows if student_rows else "<tr><td colspan='5' style='border:1px solid #ccc;padding:8px;'>No students found.</td></tr>"}
          </tbody>
        </table>
      </body>
    </html>
    """


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


@app.route("/update-db", methods=["GET"])
def update_db() -> tuple:
    try:
        db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS medium VARCHAR(20)"))
        db.session.execute(db.text("UPDATE student SET medium = 'English' WHERE medium IS NULL"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS topic_en VARCHAR(150)"))
        db.session.execute(db.text("ALTER TABLE question ADD COLUMN IF NOT EXISTS topic_si VARCHAR(150)"))
        db.session.execute(db.text("UPDATE question SET topic_en = topic WHERE topic_en IS NULL"))
        db.session.execute(db.text("UPDATE question SET topic_si = topic WHERE topic_si IS NULL"))
        db.session.commit()
        return jsonify({"success": True, "message": "Database updated successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Database update failed: {exc}"}), 500



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
    if not payload.get("topic"):
        payload["topic"] = payload.get("topic_en") or payload.get("topic_si")
    question = Question(**payload, correct_option=correct_option)
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


@app.route("/submit-test", methods=["POST"])
def submit_test() -> str:
    db.create_all()
    selected_medium = resolve_medium(request.form.get("medium") or request.args.get("medium"))

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

    def classify_topic(score: float) -> str:
        if score <= 40:
            return "Weak" if selected_medium == "English" else "දුර්වල"
        if score <= 70:
            return "Needs Improvement" if selected_medium == "English" else "වැඩිදියුණු කළ යුතුය"
        return "Strong" if selected_medium == "English" else "ශක්තිමත්"

    topic_rows = []
    for topic_name, stats in topic_stats.items():
        topic_total = stats["total"]
        topic_correct = stats["correct"]
        topic_percentage = round((topic_correct / topic_total) * 100, 2) if topic_total else 0
        status_en = classify_topic(topic_percentage)
        status_si = "දුර්වල" if topic_percentage <= 40 else "වැඩිදියුණු කළ යුතුය" if topic_percentage <= 70 else "ශක්තිමත්"

        topic_rows.append(
            f"""
            <tr>
              <td style='border:1px solid #ccc;padding:8px;'>{topic_name}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{topic_total}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{topic_correct}</td>
              <td style='border:1px solid #ccc;padding:8px;'>{topic_percentage}%</td>
              <td style='border:1px solid #ccc;padding:8px;'>{status_en}</td>
            </tr>
            """
        )

    student_result = StudentResult(
        student_id=session.get('student_id'),
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
        status_en = "Weak" if topic_percentage <= 40 else "Needs Improvement" if topic_percentage <= 70 else "Strong"
        status_si = "දුර්වල" if topic_percentage <= 40 else "වැඩිදියුණු කළ යුතුය" if topic_percentage <= 70 else "ශක්තිමත්"
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
        {topic_analysis_html}
        {wrong_answers_html}
        <p><a href='/test?medium={selected_medium}'>{t(selected_medium, 'try_again')}</a></p>
      </body>
    </html>
    """


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
