import os
from datetime import datetime

from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

SUPPORTED_MEDIA = {"English", "Sinhala"}


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    grade = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    mobile = db.Column(db.String(20), unique=True, nullable=False)
    medium = db.Column(db.String(20), nullable=False, default="English")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
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


@app.route("/")
def home() -> str:
    return "Spiral Learning System Running"


@app.route("/create-db")
def create_db() -> str:
    db.create_all()
    return "Database tables created successfully"


@app.route("/register-form", methods=["GET"])
def register_form() -> str:
    return """
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Student Registration</title>
      </head>
      <body>
        <h1>Student Registration</h1>
        <form method="post" action="/register">
          <label>
            Name:
            <input type="text" name="name" required>
          </label>
          <br><br>
          <label>
            Grade:
            <input type="text" name="grade" required>
          </label>
          <br><br>
          <label>
            Medium:
            <select name="medium" required>
              <option value="English">English</option>
              <option value="Sinhala">Sinhala</option>
            </select>
          </label>
          <br><br>
          <label>
            Email:
            <input type="email" name="email" required>
          </label>
          <br><br>
          <label>
            Mobile:
            <input type="text" name="mobile" required>
          </label>
          <br><br>
          <button type="submit">Register</button>
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

    required_fields = ["name", "grade", "email", "mobile", "medium"]
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


@app.route("/update-db", methods=["GET"])
def update_db() -> tuple:
    try:
        db.session.execute(db.text("ALTER TABLE student ADD COLUMN IF NOT EXISTS medium VARCHAR(20)"))
        db.session.execute(db.text("UPDATE student SET medium = 'English' WHERE medium IS NULL"))
        db.session.commit()
        return jsonify({"success": True, "message": "Database updated successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Database update failed: {exc}"}), 500



@app.route("/questions", methods=["POST"])
def create_question():
    data = request.get_json(silent=True) or {}
    required_fields = [
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

    question = Question(**{field: data[field].strip() for field in required_fields if field != "correct_option"}, correct_option=correct_option)
    db.session.add(question)
    db.session.commit()

    return jsonify({"success": True, "question_id": question.id}), 201


@app.route("/test", methods=["GET"])
def test_page() -> str:
    student_id = request.args.get("student_id", type=int)
    selected_medium = request.args.get("medium")
    student = Student.query.get(student_id) if student_id else None

    if selected_medium not in SUPPORTED_MEDIA:
        selected_medium = student.medium if student else "English"

    questions = Question.query.order_by(Question.id.asc()).all()
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

    student_options = ["<option value=''>Select student (optional)</option>"]
    for s in Student.query.order_by(Student.name.asc()).all():
        selected = "selected" if student_id == s.id else ""
        student_options.append(f"<option value='{s.id}' {selected}>{s.name} ({s.medium})</option>")

    english_selected = "selected" if selected_medium == "English" else ""
    sinhala_selected = "selected" if selected_medium == "Sinhala" else ""

    return f"""
    <!doctype html>
    <html lang='en'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>Test Page</title>
      </head>
      <body>
        <h1>Test Page</h1>
        <form method='get' action='/test' style='margin-bottom:20px;'>
          <label>Student:
            <select name='student_id'>{''.join(student_options)}</select>
          </label>
          <label style='margin-left:10px;'>Medium:
            <select name='medium'>
              <option value='English' {english_selected}>English</option>
              <option value='Sinhala' {sinhala_selected}>Sinhala</option>
            </select>
          </label>
          <button type='submit'>Load Questions</button>
        </form>
        <p>Selected medium: <strong>{selected_medium}</strong></p>
        {''.join(question_blocks) if question_blocks else '<p>No questions available.</p>'}
      </body>
    </html>
    """


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
