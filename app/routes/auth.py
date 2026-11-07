import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import db
from app.models import Email, User
from app.services.gmail_service import send_reset_email

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# After this many wrong passwords in a row the account is locked until the
# user resets the password.
MAX_FAILED_LOGINS = 5
# How long an emailed reset link works, and the shortest gap between two
# reset emails for the same account.
RESET_LINK_MINUTES = 30
RESET_EMAIL_GAP_MINUTES = 2

# Longest first or last name accepted on the sign-up form.
MAX_NAME_LENGTH = 50

# Limits that match the database columns (email 120, username 80), so a
# very long address gives a clear message instead of a database error.
MAX_EMAIL_LENGTH = 120
MAX_EMAIL_NAME_PART = 64

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9]+$")
GMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@gmail\.com$")
# Starts with an uppercase letter, at least 7 characters total, and
# contains at least one special character somewhere in the rest of it.
PASSWORD_PATTERN = re.compile(r'^[A-Z](?=.*[!@#$%^&*(),.?":{}|<>]).{6,}$')


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        first_name = " ".join(request.form.get("first_name", "").split())
        last_name = " ".join(request.form.get("last_name", "").split())

        # Server-side checks are the real enforcement - the matching
        # HTML pattern attributes in the template are just for instant
        # feedback and can always be bypassed, so nothing here can be
        # skipped even if the form's client-side validation passes.
        if not request.form.get("agree"):
            flash("Please agree to the Terms of Service and Privacy Policy.")
            return redirect(url_for("auth.signup"))

        if not first_name or not last_name:
            flash("Please enter your first and last name.")
            return redirect(url_for("auth.signup"))

        if len(first_name) > MAX_NAME_LENGTH or len(last_name) > MAX_NAME_LENGTH:
            flash(f"Names can be at most {MAX_NAME_LENGTH} characters.")
            return redirect(url_for("auth.signup"))

        if (
            not GMAIL_PATTERN.match(email)
            or len(email) > MAX_EMAIL_LENGTH
            or len(email.split("@")[0]) > MAX_EMAIL_NAME_PART
        ):
            flash("Email must be a valid gmail.com address.")
            return redirect(url_for("auth.signup"))

        if not PASSWORD_PATTERN.match(password):
            flash(
                "Password must start with a capital letter, be at least "
                "7 characters, and include a special character."
            )
            return redirect(url_for("auth.signup"))

        if User.by_email(email):
            flash("An account with that email already exists.")
            return redirect(url_for("auth.signup"))

        # No username field on the form - the name shown in the app comes
        # from the part of the email before the @, same as Google sign-in.
        user = User(
            username=email.split("@")[0], email=email,
            first_name=first_name, last_name=last_name,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        return redirect(url_for("emails.calendar_view"))

    return render_template("auth/signup.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = User.by_email(email)

        # A locked account is refused even with the right password - only a
        # password reset unlocks it.
        if user is not None and user.locked:
            session["account_locked"] = True
            return redirect(url_for("auth.login"))

        if user is None or not user.check_password(password):
            # Only accounts that have a JobLens password can be locked.
            if user is not None and user.password_hash:
                user.failed_logins = (user.failed_logins or 0) + 1
                if user.failed_logins >= MAX_FAILED_LOGINS:
                    user.locked = True
                    db.session.commit()
                    session["account_locked"] = True
                    return redirect(url_for("auth.login"))
                db.session.commit()
            flash("Invalid email or password.")
            return redirect(url_for("auth.login"))

        # A sign-in that works makes any emailed reset link pointless, so
        # it is cancelled (otherwise a forgotten old link stays usable).
        user.failed_logins = 0
        user.reset_token_hash = None
        user.reset_token_expires = None
        db.session.commit()
        session.pop("account_locked", None)

        login_user(user)
        return redirect(url_for("emails.calendar_view"))

    # The locked notice is not a short popup: it stays on this page until
    # the password is reset (or a sign-in succeeds).
    return render_template("auth/login.html", locked=session.get("account_locked", False))


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    # A Google-only account never set a JobLens password, so there is
    # nothing to change.
    if not current_user.password_hash:
        flash("Your account signs in with Google, so there is no JobLens password to change.")
        return redirect(url_for("emails.calendar_view"))

    if request.method == "POST":
        current_password = request.form["current_password"]
        new_password = request.form["new_password"]
        confirm_password = request.form["confirm_password"]

        if not current_user.check_password(current_password):
            flash("Your current password is incorrect.")
            return redirect(url_for("auth.change_password"))

        if new_password != confirm_password:
            flash("The new passwords do not match.")
            return redirect(url_for("auth.change_password"))

        if not PASSWORD_PATTERN.match(new_password):
            flash(
                "Password must start with a capital letter, be at least "
                "7 characters, and include a special character."
            )
            return redirect(url_for("auth.change_password"))

        if current_user.check_password(new_password):
            flash("Please choose a new password. It cannot be the same as your current one.")
            return redirect(url_for("auth.change_password"))

        current_user.set_password(new_password)
        current_user.reset_token_hash = None
        current_user.reset_token_expires = None
        db.session.commit()
        flash("Your password was updated.", "success")
        return redirect(url_for("emails.calendar_view"))

    return render_template("auth/change_password.html")


@auth_bp.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    """Permanently delete this user and everything saved about them. The
    page asks for confirmation first; this only ever runs on a POST."""
    user_id = current_user.id
    logout_user()

    Email.query.filter_by(user_id=user_id).delete()
    User.query.filter_by(id=user_id).delete()
    db.session.commit()

    flash("Your account and all saved data were deleted.", "success")
    return redirect(url_for("main.landing"))


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Asks for the account email and mails a one-time reset link to that
    same address. The answer is identical whether or not the email is
    registered, so the page cannot be used to find who has an account."""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.by_email(email)
        now = datetime.utcnow()

        recently = (
            user is not None
            and user.reset_requested_at is not None
            and now - user.reset_requested_at < timedelta(minutes=RESET_EMAIL_GAP_MINUTES)
        )

        # Google-only accounts have no JobLens password to reset.
        if user is not None and user.password_hash and not recently:
            token = secrets.token_urlsafe(32)
            user.reset_token_hash = _hash_token(token)
            user.reset_token_expires = now + timedelta(minutes=RESET_LINK_MINUTES)
            user.reset_requested_at = now
            db.session.commit()

            base = os.getenv("APP_BASE_URL", "").rstrip("/")
            path = url_for("auth.reset_password", token=token)
            link = f"{base}{path}" if base else url_for("auth.reset_password", token=token, _external=True)
            send_reset_email(user.email, user.display_name, link, RESET_LINK_MINUTES)

        flash("If that email has a JobLens account, a reset link has been sent to it.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = User.query.filter_by(reset_token_hash=_hash_token(token)).first()
    if (
        user is None
        or user.reset_token_expires is None
        or user.reset_token_expires < datetime.utcnow()
    ):
        flash("This reset link is invalid or has expired. Please ask for a new one.")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if new_password != confirm_password:
            flash("The passwords do not match.")
            return redirect(url_for("auth.reset_password", token=token))

        if not PASSWORD_PATTERN.match(new_password):
            flash(
                "Password must start with a capital letter, be at least "
                "7 characters, and include a special character."
            )
            return redirect(url_for("auth.reset_password", token=token))

        # The new password must really be new.
        if user.password_hash and user.check_password(new_password):
            flash("Please choose a new password. It cannot be the same as your old one.")
            return redirect(url_for("auth.reset_password", token=token))

        user.set_password(new_password)
        user.failed_logins = 0
        user.locked = False
        user.reset_token_hash = None
        user.reset_token_expires = None
        db.session.commit()
        session.pop("account_locked", None)

        flash("Your password was reset. You can sign in now.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token)
