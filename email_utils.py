import os

import resend

resend.api_key = os.getenv("RESEND_API_KEY")


def send_welcome_email(student_name: str, email: str, grade: str, medium: str) -> None:
    if not resend.api_key or not email:
        return

    resend.Emails.send(
        {
            "from": "SLIS <support@slis-e.com>",
            "to": [email],
            "subject": "Welcome to SLIS – Your Learning Journey Starts Here 🚀",
            "html": f"""
            <div style="font-family:Arial,sans-serif;padding:20px;">
                <h2>Welcome to SLIS 🚀</h2>
                <p>Dear {student_name},</p>
                <p>Your SLIS account has been successfully created.</p>
                <ul>
                    <li>Grade: {grade}</li>
                    <li>Medium: {medium}</li>
                </ul>
                <p>
                    Start learning now:
                    <a href="https://slis-e.com/login">Login to SLIS</a>
                </p>
                <p>The Future of Education Starts Here.</p>
                <hr>
                <small>SLIS – Sri Lanka's Leading Learning Intelligence System</small>
            </div>
            """,
        }
    )
