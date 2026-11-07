from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from app import db, login_manager


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)

    # Real name, shown in greetings. Filled from the sign-up form, or from
    # the Google profile for Google sign-in. Empty for older accounts.
    first_name = db.Column(db.String(60), nullable=True)
    last_name = db.Column(db.String(60), nullable=True)

    # Optional now - a user who signs up purely through Google never sets
    # one of these, since Google itself is what verifies their identity.
    password_hash = db.Column(db.String(255), nullable=True)

    # Google account linkage - google_id is what lets a returning Google
    # user be recognized on their next login, independent of email in
    # case it ever changes. Tokens are what let jobLens actually read
    # Gmail on the user's behalf later, without asking them to log in
    # to Google every single time.
    google_id = db.Column(db.String(120), unique=True, nullable=True)
    google_access_token = db.Column(db.Text, nullable=True)
    google_refresh_token = db.Column(db.Text, nullable=True)
    token_expiry = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # IANA timezone name (e.g. "America/New_York", "Asia/Kolkata"),
    # detected automatically from the browser on first page load and
    # kept updated if it ever changes (e.g. the user travels). Used to
    # determine what "today" and "this calendar day" mean for THIS
    # specific person, rather than assuming everyone is in one fixed
    # timezone. Nullable because it's unknown for the brief moment
    # between account creation and the browser reporting it back.
    timezone = db.Column(db.String(50), nullable=True)

    # Login protection. After MAX_FAILED_LOGINS wrong passwords in a row
    # the account is locked, and only a password reset unlocks it.
    failed_logins = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    locked = db.Column(db.Boolean, nullable=False, default=False, server_default="0")

    # Password reset. Only a hash of the emailed token is stored, so a
    # copy of the database cannot be used to reset anyone's password.
    reset_token_hash = db.Column(db.String(64), nullable=True)
    reset_token_expires = db.Column(db.DateTime, nullable=True)
    reset_requested_at = db.Column(db.DateTime, nullable=True)

    @classmethod
    def by_email(cls, email):
        """Find an account by email, ignoring upper/lower case."""
        email = (email or "").strip().lower()
        if not email:
            return None
        return cls.query.filter(func.lower(cls.email) == email).first()

    @property
    def display_name(self):
        """Name for greetings: "First Last" when known, otherwise the old
        name made from the email (digits removed, capitalised)."""
        full = " ".join(
            part for part in (self.first_name, self.last_name) if part
        ).strip()
        if full:
            return full
        return (self.username.rstrip("0123456789") or self.username).capitalize()

    def set_password(self, plain_password):
        self.password_hash = generate_password_hash(plain_password)

    def check_password(self, plain_password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, plain_password)


class Email(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Gmail's own unique ID for this message - lets a repeated sync check
    # "have I already saved this one?" before reprocessing it, so clicking
    # a date twice doesn't create duplicate rows or double-count stats.
    gmail_message_id = db.Column(db.String(50), unique=True, nullable=True)

    # Filled in by Agent 1 (Classifier)
    category = db.Column(db.String(30), nullable=False)

    # Filled in by Agent 2 (Extractor) - nullable because a "general" or
    # promotional email won't have a company/role/deadline to extract
    company = db.Column(db.String(120))
    role = db.Column(db.String(120))
    deadline = db.Column(db.Date)
    urgency = db.Column(db.String(20))

    # Where the email came from (e.g. a company or platform name) and a
    # one-line description - used for the General and Recruiter outreach
    # lists, where there is no company/role to show.
    sender = db.Column(db.String(200))
    summary = db.Column(db.String(300))

    # Sender check, only filled for offers, interviews and assessments:
    # "direct", "platform" or "unverified", plus a short reason.
    sender_check = db.Column(db.String(20))
    sender_note = db.Column(db.String(200))

    # True when the email was in the user's Trash (deleted) when it was
    # scanned. Gmail keeps trashed mail for 30 days.
    from_trash = db.Column(db.Boolean, default=False)

    # True when the email was found in the user's Spam folder, so the app
    # can tell them where it came from.
    from_spam = db.Column(db.Boolean, default=False)

    raw_snippet = db.Column(db.Text, nullable=False)
    date_received = db.Column(db.Date, nullable=False)
    processed_at = db.Column(db.DateTime, default=datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
