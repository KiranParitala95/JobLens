import os

from authlib.integrations.flask_client import OAuth
from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

# Created without an app attached yet, so models.py and routes can
# import `db` without triggering a circular import with create_app().
db = SQLAlchemy()
login_manager = LoginManager()
oauth = OAuth()


def create_app():
    app = Flask(__name__)

    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key-change-this")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", "sqlite:///joblens.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # SQLite's default is to fail INSTANTLY on any lock contention, even a
    # brief one - this made "database is locked" errors far more common
    # than they need to be during normal use (a write from one request
    # landing a moment before another). This tells SQLite to wait up to
    # 15 seconds and retry before actually giving up. Only applies to
    # SQLite - this option doesn't carry over once DATABASE_URL points
    # at Postgres in production, so no cleanup needed at that point.
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "connect_args": {"timeout": 15}
        }

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid email profile https://www.googleapis.com/auth/gmail.readonly",
        },
        # access_type=offline requests a refresh token on first grant. We
        # deliberately do NOT force prompt=consent here - that flag was
        # only needed once, to fix an earlier bug where no refresh token
        # had been issued yet. Forcing it permanently makes Google re-show
        # the full consent screen on every single login, which is exactly
        # the repeated-permission annoyance a returning user shouldn't see.
        # Once a refresh token is already saved, Google recognizes a
        # returning, already-consented user and skips straight through.
        authorize_params={"access_type": "offline"},
    )

    # Imported here, not at module level, to avoid the circular import
    # noted above - by this point `db` is already attached to `app`.
    from app.routes.auth import auth_bp
    from app.routes.emails import emails_bp
    from app.routes.google_auth import google_auth_bp
    from app.routes.main import main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(emails_bp)
    app.register_blueprint(google_auth_bp)
    app.register_blueprint(main_bp)

    with app.app_context():
        db.create_all()
        _add_missing_columns()

    return app


def _add_missing_columns():
    """db.create_all() creates new tables but never adds a new column to a
    table that already exists. This adds any model column that the saved
    database is missing (ALTER TABLE ... ADD COLUMN), so the existing data
    is kept and nobody has to run a manual migration."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    for table in db.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            col_type = column.type.compile(dialect=db.engine.dialect)
            sql = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'
            if column.server_default is not None:
                sql += f" DEFAULT {column.server_default.arg}"
            if not column.nullable and column.server_default is not None:
                sql += " NOT NULL"
            with db.engine.begin() as conn:
                conn.execute(text(sql))
