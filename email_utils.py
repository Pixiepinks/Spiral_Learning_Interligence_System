import os

import resend

resend.api_key = os.getenv("RESEND_API_KEY")


def send_welcome_email(
    student_name: str,
    recipients: list[str],
    grade: str,
    medium: str,
    username: str,
    plain_password: str,
) -> None:
    if not resend.api_key or not recipients:
        return

    resend.Emails.send(
        {
            "from": "SLIS <support@slis-e.com>",
            "to": recipients,
            "subject": "Welcome to SLIS – Your Learning Journey Starts Here 🚀",
            "html": f"""
            <div style="margin:0;background:#f4f8ff;padding:24px 12px;font-family:'Segoe UI',Arial,sans-serif;color:#0f172a;">
              <div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:20px;overflow:hidden;box-shadow:0 14px 38px rgba(30,64,175,.16);">
                <div style="padding:28px;background:linear-gradient(135deg,#1d4ed8,#2563eb,#38bdf8);color:#fff;text-align:center;">
                  <img src="https://slis-e.com/static/images/SLIS%20LOGO.png" alt="SLIS Logo" style="height:48px;max-width:100%;object-fit:contain;margin-bottom:12px;">
                  <h1 style="margin:0;font-size:24px;">Welcome to SLIS 🚀</h1>
                  <p style="margin:10px 0 0;opacity:.96;">Sri Lanka's Most Advanced AI Learning Platform</p>
                </div>
                <div style="padding:28px;">
                  <p style="margin:0 0 12px;">Dear Parent / Student,</p>
                  <p style="margin:0 0 14px;">Your student account has been successfully created.</p>
                  <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:14px;padding:16px 18px;">
                    <p style="margin:0 0 8px;"><strong>Student:</strong> {student_name}</p>
                    <p style="margin:0 0 8px;"><strong>Grade:</strong> {grade}</p>
                    <p style="margin:0 0 8px;"><strong>Medium:</strong> {medium}</p>
                    <p style="margin:0 0 8px;"><strong>Username:</strong> {username}</p>
                    <p style="margin:0;"><strong>Temporary Password:</strong> {plain_password}</p>
                  </div>
                  <p style="margin:16px 0 8px;">You can now access AI-powered lessons, interactive activities, smart progress tracking, bilingual learning, and a gamified education experience.</p>
                  <p style="margin:0 0 18px;">For security, please change your password after first login.</p>
                  <a href="https://slis-e.com/login" style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;padding:12px 20px;border-radius:999px;font-weight:600;">Start Learning Now</a>
                  <p style="margin:18px 0 0;color:#334155;">Need help? Contact us at <a href="mailto:support@slis-e.com">support@slis-e.com</a>.</p>
                </div>
              </div>
            </div>
            """,
        }
    )
