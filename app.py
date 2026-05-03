import os
from datetime import datetime

from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    grade = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    mobile = db.Column(db.String(20), unique=True, nullable=False)
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

    required_fields = ["name", "grade", "email", "mobile"]
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
                        "email": student.email,
                        "mobile": student.mobile,
                    }
                    for student in students
                ],
            }
        ),
        200,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
