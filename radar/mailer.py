"""Envío del informe por Gmail SMTP (usa una contraseña de aplicación, no tu contraseña normal)."""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send(user: str, app_password: str, to: str, subject: str, html: str, cc: list[str] | None = None):
    cc = cc or []
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Radar Inmobiliario <{user}>"
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.attach(MIMEText("Tu cliente de correo no muestra HTML. Abre el panel web del radar.", "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, app_password)
        s.sendmail(user, [to, *cc], msg.as_string())
