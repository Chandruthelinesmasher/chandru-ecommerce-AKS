import os
import signal
import sys
import logging
import json
import time
from functools import wraps
from threading import Event

from flask import Flask, jsonify, request, abort, render_template
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from redis import Redis, RedisError
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import HTTPException

# --------- Configuration ----------
DB_DEFAULT = "sqlite:///./product.db"
DATABASE_URL = os.environ.get("DATABASE_URL", DB_DEFAULT)
REDIS_URL = os.environ.get("REDIS_URL", None)
SECRET_KEY = os.environ.get("SECRET_KEY", "changeme")
APP_VERSION = os.environ.get("APP_VERSION", "v1.0.0")

db = SQLAlchemy()
redis_client = None
shutdown_event = Event()

# Prometheus metrics
HTTP_REQUESTS = Counter("http_requests_total", "Total HTTP requests", ["method", "endpoint", "http_status"])
HTTP_LATENCY = Histogram("http_request_latency_seconds", "HTTP request latency", ["endpoint"])


# --------- Logging ----------
def configure_logging():
    class JsonFormatter(logging.Formatter):
        def format(self, record):
            log = {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "module": record.module,
                "line": record.lineno,
            }
            if record.exc_info:
                log["exc_info"] = self.formatException(record.exc_info)
            return json.dumps(log)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = []
    root.addHandler(handler)


# --------- Metrics Decorator ----------
def record_metrics(endpoint):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                response = func(*args, **kwargs)
                status = getattr(response, "status_code", 200)
                return response
            except HTTPException as he:
                status = he.code or 500
                raise
            except Exception:
                status = 500
                raise
            finally:
                duration = time.time() - start
                HTTP_LATENCY.labels(endpoint=endpoint).observe(duration)
                HTTP_REQUESTS.labels(method=request.method, endpoint=endpoint, http_status=str(status)).inc()
        return wrapper
    return decorator


# --------- Models ----------
def init_models(app):
    db.init_app(app)

    class Product(db.Model):
        __tablename__ = "products"
        id = db.Column(db.Integer, primary_key=True)
        title = db.Column(db.String(255), nullable=False)
        description = db.Column(db.Text)
        price = db.Column(db.Float, nullable=False)
        currency = db.Column(db.String(8), default="USD")
        stock = db.Column(db.Integer, default=0)
        created_at = db.Column(db.DateTime, server_default=text("CURRENT_TIMESTAMP"))

        def to_dict(self):
            return {
                "id": self.id,
                "title": self.title,
                "description": self.description,
                "price": self.price,
                "currency": self.currency,
                "stock": self.stock
            }

    with app.app_context():
        db.create_all()

    return Product


# --------- ✅ FLASK FACTORY ----------
def create_app(config=None):
    configure_logging()

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, "templates"),
        static_folder=os.path.join(BASE_DIR, "static")
    )

    app.wsgi_app = ProxyFix(app.wsgi_app)

    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = SECRET_KEY
    app.config["APP_VERSION"] = APP_VERSION

    if config:
        app.config.update(config)

    Product = init_models(app)

    global redis_client
    if REDIS_URL:
        try:
            redis_client = Redis.from_url(REDIS_URL, decode_responses=True)
            redis_client.ping()
            app.logger.info("Connected to Redis")
        except RedisError:
            redis_client = None
    else:
        app.logger.info("Redis not configured; using in-memory cache")

    # ---------- Errors ----------
    @app.errorhandler(Exception)
    def handle_exception(e):
        app.logger.exception("Unhandled exception")
        code = 500
        if isinstance(e, HTTPException):
            code = e.code
        return jsonify({"error": str(e)}), code

    # ---------- Health ----------
    @app.route("/health")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify({"status": "ok"}), 200
        except Exception:
            return jsonify({"status": "degraded"}), 503

    @app.route("/ready")
    def ready():
        return jsonify({"ready": True}), 200

    @app.route("/metrics")
    def metrics():
        return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

    # ---------- ✅ FIXED WEB INDEX WITH PAGINATION ----------
    @app.route("/", methods=["GET"])
    @record_metrics("index")
    def index():
        page = int(request.args.get("page", 1))
        per_page = 12
        q = request.args.get("q", "").strip()

        query = Product.query
        if q:
            like_q = f"%{q}%"
            query = query.filter(
                db.or_(Product.title.ilike(like_q), Product.description.ilike(like_q))
            )

        pag = query.order_by(Product.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

        items = pag.items

        return render_template(
            "index.html",
            products=[p.to_dict() for p in items],
            page=page,
            pages=pag.pages,
            q=q,
            version=APP_VERSION,
        )

    # ---------- REST API ----------
    @app.route("/api/products")
    def api_products():
        return jsonify([p.to_dict() for p in Product.query.all()])

    # ---------- Shutdown ----------
    def handle_sigterm(signum, frame):
        app.logger.info("Shutdown signal received")
        shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    return app


# ✅ LOCAL DEV ONLY
if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
