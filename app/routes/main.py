from datetime import datetime, timedelta

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.agents.summarizer import generate_summary
from app.models import Email
from app.services.gmail_service import get_zone, is_valid_zone


def _user_today(user):
    """What day is it RIGHT NOW for this specific person, in their own
    timezone - not the server's, not a fixed assumption. Falls back to
    UTC only in the brief window between signup and the browser
    reporting its timezone back.
    """
    return datetime.now(get_zone(user.timezone)).date()

main_bp = Blueprint("main", __name__)

# Five categories get their own dashboard section. general is shown so
# the user can see how much of their inbox is noise vs. real activity,
# but - like offer_received - it's deliberately left out of the
# response rate calculation below, since it was never an application.
DASHBOARD_CATEGORIES = {
    "applications": "job_applied",
    "rejections": "rejection",
    "interviews_scheduled": "interview_scheduled",
    "assessments": "assessment_invite",
    "offers": "offer_received",
    "follow_ups": "follow_up_sent",
    "recruiter_outreach": "recruiter_outreach",
    "general": "general",
}


def _count(category, since=None):
    """Count this user's emails in one category, optionally only those
    received on or after a given date."""
    query = Email.query.filter_by(user_id=current_user.id, category=category)
    if since:
        query = query.filter(Email.date_received >= since)
    return query.count()


def _build_dashboard_stats():
    """Query the Email table for this user and build the section->window
    counts the dashboard cards need, plus the flat stats dict Agent 3
    expects for its written recap.

    Date windows are calendar-day based (not hour-precise) since
    Email.date_received is stored as a Date, not a DateTime - "last 24
    hours" is treated as "today," which matches the granularity the data
    actually supports without pretending to more precision than it has.
    """
    today = _user_today(current_user)
    last_7d = today - timedelta(days=7)
    last_30d = today - timedelta(days=30)

    sections = {}
    for section_name, category in DASHBOARD_CATEGORIES.items():
        sections[section_name] = {
            "last_24h": _count(category, today),
            "last_7d": _count(category, last_7d),
            "last_30d": _count(category, last_30d),
            "total": _count(category),
        }

    # Dashboard shows interviews and assessments/screenings as one row.
    # The separate sections above stay in place because the recap and
    # response rate still use them individually.
    sections["interviews_assessments"] = {
        key: sections["interviews_scheduled"][key] + sections["assessments"][key]
        for key in ("last_24h", "last_7d", "last_30d", "total")
    }

    total_applications = sections["applications"]["total"]
    total_responses = (
        sections["rejections"]["total"]
        + sections["interviews_scheduled"]["total"]
        + sections["assessments"]["total"]
        + _count("offer_received")
    )
    response_rate = (
        f"{round(100 * total_responses / total_applications)}%"
        if total_applications > 0
        else "0%"
    )

    return sections, response_rate


@main_bp.route("/api/set-timezone", methods=["POST"])
@login_required
def set_timezone():
    """Receives the timezone the browser detected via JavaScript
    (Intl.DateTimeFormat) and saves it to this user's account. Called
    automatically from base.html on every page load - only writes to
    the database when the value actually changes, so this is a no-op
    most of the time, not a write on every single page view.
    """
    tz_name = request.get_json(silent=True, force=True) or {}
    tz_name = tz_name.get("timezone")

    # Only a real time zone name is saved. Anything else is ignored, so a
    # bad value can never break the calendar or the dashboard.
    if not is_valid_zone(tz_name):
        return jsonify({"status": "ignored"}), 400

    if tz_name != current_user.timezone:
        current_user.timezone = tz_name
        db.session.commit()

    return jsonify({"status": "ok"})


@main_bp.route("/privacy")
def privacy():
    return render_template("legal/privacy.html")


@main_bp.route("/terms")
def terms():
    return render_template("legal/terms.html")


@main_bp.route("/")
def landing():
    """The actual front door of the site. A logged-in user visiting the
    bare address skips straight to their dashboard - this page is only
    for someone who hasn't signed in yet, giving them a clear choice
    (sign in, create an account, or continue with Google) instead of
    being dropped straight into a login form with no context.
    """
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    return render_template("pages/landing.html")


@main_bp.route("/home")
@login_required
def home():
    sections, response_rate = _build_dashboard_stats()

    # Agent 3's recap uses the 7-day window numbers - gives a weekly
    # flavor to the written summary alongside the all-window card grid.
    weekly_stats = {
        "applications": sections["applications"]["last_7d"],
        "rejections": sections["rejections"]["last_7d"],
        "interviews": sections["interviews_scheduled"]["last_7d"],
        "assessments": sections["assessments"]["last_7d"],
        "response_rate": response_rate,
    }
    summary = generate_summary(weekly_stats, period="week")

    return render_template(
        "pages/dashboard.html",
        username=current_user.username,
        sections=sections,
        response_rate=response_rate,
        summary=summary,
    )
