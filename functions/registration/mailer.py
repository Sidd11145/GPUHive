from email.message import EmailMessage
import smtplib
import ssl


class SmtpMailer:
    def __init__(self, host, port, username, password, sender):
        if not all((host, username, password, sender)):
            raise ValueError("SMTP configuration is incomplete")
        self.host, self.port = host, int(port)
        self.username, self.password, self.sender = username, password, sender

    def send_token(self, recipient, token, expires_at):
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = recipient
        message["Subject"] = "Your GridGPU verification code"
        message.set_content(
            "Verify your email to submit a GridGPU device for approval.\n\n"
            f"Enter this 6-digit code into the GridGPU installer:\n\n{token}\n\n"
            f"The code expires at {expires_at.isoformat()}. It registers one device.\n"
            "After submission, the owner must approve your device before it can participate.\n"
        )
        context = ssl.create_default_context()
        if self.port == 465:
            connection = smtplib.SMTP_SSL(self.host, self.port, timeout=20, context=context)
        else:
            connection = smtplib.SMTP(self.host, self.port, timeout=20)
        with connection as smtp:
            if self.port != 465:
                smtp.starttls(context=context)
            smtp.login(self.username, self.password)
            smtp.send_message(message)
