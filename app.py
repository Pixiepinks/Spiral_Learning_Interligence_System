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
    grade = db.Column(db.String(20), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    topic = db.Column(db.String(150), nullable=False)
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
        "grade",
        "subject",
        "topic",
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


@app.route("/create-questions", methods=["GET"])
def create_questions() -> tuple:
    sample_questions = [
        {
            "grade": "6",
            "subject": "Math",
            "topic": "Fractions",
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
    selected_medium = request.args.get("medium", "English")
    if selected_medium not in SUPPORTED_MEDIA:
        selected_medium = "English"

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
    <html lang='en'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>Test Page</title>
      </head>
      <body>
        <h1>SkillScan Test - Grade 6 Math</h1>
        <form method='get' action='/test' style='margin-bottom:20px;'>
          <label>Language:
            <select name='medium'>
              <option value='English' {english_selected}>English</option>
              <option value='Sinhala' {sinhala_selected}>Sinhala</option>
            </select>
          </label>
          <button type='submit'>Change Language</button>
        </form>
        <form method='post' action='/submit-test'>
          <input type='hidden' name='medium' value='{selected_medium}'>
          <p>Selected language: <strong>{selected_medium}</strong></p>
          {''.join(question_blocks) if question_blocks else '<p>No Grade 6 Math questions available.</p>'}
          <button type='submit'>Submit</button>
        </form>
      </body>
    </html>
    """


@app.route("/submit-test", methods=["POST"])
def submit_test() -> str:
    submitted_answers = {k: v for k, v in request.form.items() if k.startswith("q_")}
    return (
        "<h2>Test submitted successfully.</h2>"
        f"<p>Received {len(submitted_answers)} answer(s).</p>"
        "<p><a href='/test'>Back to test</a></p>"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
