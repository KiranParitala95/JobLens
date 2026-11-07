from datetime import datetime, timedelta

from authlib.integrations.base_client.errors import MismatchingStateError
from flask import Blueprint, flash, redirect, url_for
from flask_login import current_user, login_user

from app import db, oauth
from app.models import User

google_auth_bp = Blueprint("google_auth", __name__, url_prefix="/auth/google")


@google_auth_bp.route("/login")
def login():
    redirect_uri = url_for("google_auth.callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@google_auth_bp.route("/reconnect")
def reconnect():
    """Used only when the saved Google connection can't be reused (no
    refresh token, or it was revoked). Forces Google to show consent once
    so it issues a fresh refresh token. Normal logins never come here, so
    returning users aren't asked for permission again."""
    redirect_uri = url_for("google_auth.callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri, prompt="consent")


@google_auth_bp.route("/callback")
def callback():
    try:
        token = oauth.google.authorize_access_token()
    except MismatchingStateError:
        # The sign-in cookie didn't match Google's reply - typically an old
        # or duplicated sign-in tab, or switching between localhost and
        # 127.0.0.1 mid-login. Send the user back to try again instead of
        # showing a crash page.
        flash("Your sign-in expired. Please try again.")
        return redirect(url_for("auth.login"))
    user_info = token.get("userinfo")

    google_id = user_info["sub"]
    email = user_info["email"]
    username = email.split("@")[0]

    if current_user.is_authenticated:
        # Already signed in (e.g. made an account with email + password and
        # is now connecting Gmail): attach Google to THIS account instead of
        # creating a second one. It has to be the same Gmail they signed up with.
        if email.lower() != (current_user.email or "").lower():
            flash(
                "Please connect the same Gmail you signed up with "
                f"({current_user.email})."
            )
            return redirect(url_for("emails.calendar_view"))
        owner = User.query.filter_by(google_id=google_id).first()
        if owner is not None and owner.id != current_user.id:
            flash("That Google account is already linked to another JobLens account.")
            return redirect(url_for("emails.calendar_view"))
        user = current_user
        user.google_id = google_id
    else:
        user = User.query.filter_by(google_id=google_id).first()

        if user is None:
            user = User.by_email(email)
            if user is None:
                user = User(username=username, email=email, google_id=google_id)
                db.session.add(user)
            else:
                user.google_id = google_id

    # Real name from the Google profile (kept up to date on every sign-in).
    given = (user_info.get("given_name") or "").strip()[:60]
    family = (user_info.get("family_name") or "").strip()[:60]
    if given:
        user.first_name = given
        user.last_name = family or None
    elif not user.first_name and user_info.get("name"):
        user.first_name = user_info["name"].strip()[:60]

    user.google_access_token = token.get("access_token")
    if token.get("refresh_token"):
        user.google_refresh_token = token.get("refresh_token")
    expires_in = token.get("expires_in", 3600)
    user.token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)

    db.session.commit()

    login_user(user)

    return redirect(url_for("emails.calendar_view"))
