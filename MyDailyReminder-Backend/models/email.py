"""One SMTP session per batch, with explicit and conservative outcomes."""
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import SMTP_SERVER, SMTP_PORT, EMAIL_ADDRESS, EMAIL_PASSWORD


@dataclass(frozen=True)
class DeliveryResult:
    status: str
    code: str = ""


class BoundedSMTP(smtplib.SMTP):
    def __init__(self, budget):
        self.budget = budget
        self.submitting = False
        super().__init__(timeout=budget.io_timeout())

    def _set_timeout(self):
        self.timeout = self.budget.io_timeout()
        if self.sock:
            self.sock.settimeout(self.timeout)

    def connect(self, host='localhost', port=0, source_address=None):
        # SMTP() is constructed without a host so connection errors are handled
        # by MailSession. STARTTLS still needs the hostname for SNI and certificate
        # verification; smtplib.connect() does not update it automatically.
        self._host = host
        self._set_timeout()
        return super().connect(host, port, source_address)

    def send(self, value):
        self._set_timeout()
        return super().send(value)

    def getreply(self):
        self._set_timeout()
        return super().getreply()

    def data(self, message):
        # A disconnect anywhere in DATA is treated conservatively as uncertain.
        self.submitting = True
        return super().data(message)


class MailSession:
    def __init__(self, budget):
        self.server = BoundedSMTP(budget)

    def __enter__(self):
        try:
            self.server.connect(SMTP_SERVER, SMTP_PORT)
            self.server.starttls(context=ssl.create_default_context())
            self.server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            return self
        except Exception:
            self.server.close()
            raise

    def __exit__(self, *args):
        # QUIT is unnecessary and would consume the result-persistence reserve.
        self.server.close()

    def send(self, to_email, subject, body, message_id):
        message = MIMEMultipart()
        message["From"] = f"MyDailyReminder <{EMAIL_ADDRESS}>"
        message["To"] = to_email
        message["Subject"] = subject
        message["Message-ID"] = message_id
        message.attach(MIMEText(body, "html", "utf-8"))
        self.server.submitting = False
        try:
            self.server.sendmail(EMAIL_ADDRESS, [to_email], message.as_string())
            return DeliveryResult("sent")
        except smtplib.SMTPRecipientsRefused as exc:
            code = next(iter(exc.recipients.values()))[0]
        except smtplib.SMTPResponseException as exc:
            code = exc.smtp_code
        except (OSError, smtplib.SMTPException):
            return DeliveryResult(
                "uncertain" if self.server.submitting else "retryable_failure",
                "connection_interrupted",
            )
        return DeliveryResult(
            "retryable_failure" if 400 <= code < 500 else "permanent_failure",
            str(code),
        )
