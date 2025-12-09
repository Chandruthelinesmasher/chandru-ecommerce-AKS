"""
Chandru E-Commerce — product service (Flask)
Production-ready example:
- Factory pattern (create_app)
- SQLAlchemy models (sqlite default, but use DATABASE_URL env var)
- Redis-backed cart (optional, enabled if REDIS_URL provided)
- Prometheus metrics endpoint (/metrics)
- Health (/health) and readiness (/ready) endpoints
- Structured logging (JSON-ish)
- Graceful shutdown handlers
- Server-side rendered pages + REST APIs
- Pagination, search, filtering
"""

import os
import signal
import sys
import logging
import json
import time
from functools import wraps
from threading import Event

from flask import (
    Flask, jsonify, request, abort, render_template, url_for, redirect
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import OperationalError
from sqlalchemy import text
from redis import Redis, RedisError
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import HTTPException

# --------- Configuration & Globals ----------
DB_DEFAULT = "sqlite:///./product.db"
DATABASE_URL = os.environ.get("DATABASE_URL", DB_DEFAULT)
REDIS_URL = os.environ.get("REDIS_URL", None)
SECRET_KEY = os.environ.get("SECRET_KEY", "changeme")
APP_VERSION = os.environ.get("APP_VERSION", "v1.0.0")
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "10"))

db = SQLAlchemy()
redis_client = None  # set in create_app if REDIS_URL provided

# Prometheus metrics
HTTP_REQUESTS = Counter("http_requests_total", "Total HTTP requests", ["method", "endpoint", "http_status"])
HTTP_LATENCY = Histogram("http_request_latency_seconds", "HTTP request latency", ["endpoint"])

shutdown_event = Event()


# --------- Helper functions ----------
def configure_logging():
    """Configure structured logging for production"""
    # Simple JSON-ish log formatter
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
    root.handlers = []  # remove default handlers
    root.addHandler(handler)


def record_metrics(endpoint):
    """Decorator to record Prometheus metrics for endpoints"""
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
    global db
    db.init_app(app)

    class Product(db.Model):
        __tablename__ = "products"
        id = db.Column(db.Integer, primary_key=True)
        title = db.Column(db.String(255), nullable=False)
        description = db.Column(db.Text, nullable=True)
        price = db.Column(db.Float, nullable=False)
        currency = db.Column(db.String(8), default="USD")
        stock = db.Column(db.Integer, default=0)
        sku = db.Column(db.String(64), unique=True, nullable=True)
        created_at = db.Column(db.DateTime, server_default=text("CURRENT_TIMESTAMP"))

        def to_dict(self):
            return {
                "id": self.id,
                "title": self.title,
                "description": self.description,
                "price": self.price,
                "currency": self.currency,
                "stock": self.stock,
                "sku": self.sku
            }

    # attach to db instance for external import if needed
    app.Product = Product

    with app.app_context():
        db.create_all()

    return Product


# --------- Factory ----------
def create_app(config: dict = None):
    configure_logging()
    app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "..", "templates"))
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # if behind proxy/load balancer

    # config from env + overrides
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", DATABASE_URL)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", SECRET_KEY)
    app.config["APP_VERSION"] = APP_VERSION
    if config:
        app.config.update(config)

    # init DB and models
    Product = init_models(app)

    # init redis if available
    global redis_client
    if REDIS_URL:
        try:
            redis_client = Redis.from_url(REDIS_URL, decode_responses=True)
            # quick ping
            redis_client.ping()
            app.logger.info("Connected to Redis")
        except RedisError as e:
            app.logger.error(f"Redis connection failed: {e}")
            redis_client = None
    else:
        app.logger.info("Redis not configured; using in-memory session cart")

    # ---------- Error handlers ----------
    @app.errorhandler(Exception)
    def handle_exception(e):
        app.logger.exception("Unhandled exception")
        code = 500
        if isinstance(e, HTTPException):
            code = e.code
        return jsonify({"error": str(e)}), code

    # ---------- Health & Metrics ----------
    @app.route("/health", methods=["GET"])
    @record_metrics("health")
    def health():
        # check DB connectivity quickly
        try:
            # run a simple query
            with app.app_context():
                db.session.execute("SELECT 1")
            db_ok = True
        except Exception as e:
            app.logger.error("DB healthcheck failed: %s", e)
            db_ok = False

        # check redis
        redis_ok = True
        if REDIS_URL and redis_client:
            try:
                redis_client.ping()
            except RedisError:
                redis_ok = False

        status = {"status": "ok" if db_ok and redis_ok else "degraded", "version": app.config.get("APP_VERSION")}
        return jsonify(status), (200 if db_ok else 503)

    @app.route("/ready", methods=["GET"])
    @record_metrics("ready")
    def ready():
        # Basic readiness probe, akin to health but faster
        return jsonify({"ready": True, "version": app.config.get("APP_VERSION")})

    @app.route("/metrics")
    def metrics():
        return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

    # ---------- Web pages ----------
    @app.route("/", methods=["GET"])
    @record_metrics("index")
    def index():
        # server-side rendered product catalog with pagination + search
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 12))
        q = request.args.get("q", "").strip()

        query = Product.query
        if q:
            like_q = f"%{q}%"
            query = query.filter(db.or_(Product.title.ilike(like_q), Product.description.ilike(like_q)))

        pag = query.order_by(Product.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        items = [p.to_dict() for p in pag.items]

        return render_template("index.html", products=items, page=page, pages=pag.pages, q=q, version=app.config.get("APP_VERSION"))

    @app.route("/product/<int:product_id>", methods=["GET"])
    @record_metrics("product_page")
    def product_page(product_id):
        p = Product.query.get_or_404(product_id)
        return render_template("product.html", product=p.to_dict(), version=app.config.get("APP_VERSION"))

    # ---------- REST API ----------
    def json_request_or_400(schema=None):
        if not request.is_json:
            abort(400, "JSON body required")
        data = request.get_json()
        return data

    @app.route("/api/products", methods=["GET"])
    @record_metrics("api_products_list")
    def api_products():
        # supported params: page, per_page, q, min_price, max_price
        page = int(request.args.get("page", 1))
        per_page = min(int(request.args.get("per_page", 25)), 100)
        q = request.args.get("q", "").strip()
        min_price = request.args.get("min_price", None)
        max_price = request.args.get("max_price", None)

        query = Product.query
        if q:
            like_q = f"%{q}%"
            query = query.filter(db.or_(Product.title.ilike(like_q), Product.description.ilike(like_q)))
        if min_price:
            query = query.filter(Product.price >= float(min_price))
        if max_price:
            query = query.filter(Product.price <= float(max_price))

        pag = query.order_by(Product.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        return jsonify({
            "items": [p.to_dict() for p in pag.items],
            "total": pag.total,
            "page": pag.page,
            "pages": pag.pages
        })

    @app.route("/api/products/<int:product_id>", methods=["GET"])
    @record_metrics("api_product_detail")
    def api_product_detail(product_id):
        p = Product.query.get_or_404(product_id)
        return jsonify(p.to_dict())

    # --------- Cart handling (Redis or in-memory) ----------
    def _cart_key(user_id):
        return f"cart:{user_id}"

    def get_cart_data(user_id):
        if redis_client:
            try:
                raw = redis_client.get(_cart_key(user_id))
                return json.loads(raw) if raw else {}
            except RedisError:
                app.logger.exception("Redis read error")
                return {}
        # fallback to in-memory per-process store (not recommended for prod)
        # attach simple dict to app for demo purposes
        if not hasattr(app, "_memory_carts"):
            app._memory_carts = {}
        return app._memory_carts.get(user_id, {})

    def set_cart_data(user_id, data):
        if redis_client:
            try:
                redis_client.set(_cart_key(user_id), json.dumps(data), ex=60 * 60 * 24)  # 24h TTL
                return True
            except RedisError:
                app.logger.exception("Redis write error")
                return False
        if not hasattr(app, "_memory_carts"):
            app._memory_carts = {}
        app._memory_carts[user_id] = data
        return True

    @app.route("/api/cart/<string:user_id>", methods=["GET"])
    @record_metrics("api_cart_get")
    def api_cart_get(user_id):
        cart = get_cart_data(user_id)
        return jsonify(cart)

    @app.route("/api/cart/<string:user_id>", methods=["POST"])
    @record_metrics("api_cart_add")
    def api_cart_add(user_id):
        payload = json_request_or_400()
        product_id = payload.get("product_id")
        qty = int(payload.get("quantity", 1))
        if not product_id:
            abort(400, "product_id required")

        p = Product.query.get(product_id)
        if not p:
            abort(404, "product not found")

        cart = get_cart_data(user_id)
        entry = cart.get(str(product_id), {"quantity": 0})
        entry["quantity"] = entry.get("quantity", 0) + qty
        entry["title"] = p.title
        entry["price"] = p.price
        cart[str(product_id)] = entry
        set_cart_data(user_id, cart)
        return jsonify(cart), 201

    @app.route("/api/cart/<string:user_id>", methods=["PUT"])
    @record_metrics("api_cart_update")
    def api_cart_update(user_id):
        payload = json_request_or_400()
        product_id = str(payload.get("product_id"))
        qty = int(payload.get("quantity", 0))
        cart = get_cart_data(user_id)
        if product_id not in cart:
            abort(404, "item not in cart")
        if qty <= 0:
            cart.pop(product_id, None)
        else:
            cart[product_id]["quantity"] = qty
        set_cart_data(user_id, cart)
        return jsonify(cart)

    @app.route("/api/cart/<string:user_id>", methods=["DELETE"])
    @record_metrics("api_cart_delete")
    def api_cart_delete(user_id):
        payload = json_request_or_400()
        product_id = str(payload.get("product_id"))
        cart = get_cart_data(user_id)
        if product_id in cart:
            cart.pop(product_id, None)
            set_cart_data(user_id, cart)
        return jsonify(cart)

    # ---------- Checkout / Order (simple demo)
    @app.route("/api/checkout/<string:user_id>", methods=["POST"])
    @record_metrics("api_checkout")
    def api_checkout(user_id):
        payload = json_request_or_400()
        # For demo: accept `payment_method` and `customer` info
        payment_method = payload.get("payment_method", "card")
        customer = payload.get("customer", {})
        cart = get_cart_data(user_id)
        if not cart:
            abort(400, "cart empty")
        # In production: call payment gateway, reserve stock, create order in DB
        # Here we create a fake order and clear the cart
        total = sum(item["price"] * item["quantity"] for item in cart.values())
        order = {
            "order_id": int(time.time()),
            "user_id": user_id,
            "items": cart,
            "total": total,
            "payment_method": payment_method,
            "customer": customer,
            "status": "confirmed",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        # clear cart
        set_cart_data(user_id, {})
        app.logger.info("Order created: %s", order["order_id"])
        return jsonify(order), 201

    # ---------- Admin / management endpoints (seed data) ----------
    @app.route("/admin/seed", methods=["POST"])
    def admin_seed():
        # create example products. In prod protect this endpoint
        data = request.get_json() or {}
        n = int(data.get("n", 6))
        seeds = [
            {"title": "Phone Model X", "description": "Flagship phone", "price": 699.99, "stock": 50},
            {"title": "Wireless Headphones", "description": "Noise cancelling", "price": 199.99, "stock": 120},
            {"title": "Mechanical Keyboard", "description": "Tactile switches", "price": 89.99, "stock": 200},
            {"title": "4K Monitor", "description": "Ultra HD display", "price": 299.99, "stock": 30},
            {"title": "USB-C Hub", "description": "6-in-1 hub", "price": 39.99, "stock": 300},
            {"title": "External SSD", "description": "1TB portable", "price": 129.99, "stock": 80},
        ]
        added = []
        for s in seeds[:n]:
            p = Product(title=s["title"], description=s["description"], price=s["price"], stock=s["stock"])
            db.session.add(p)
            added.append(s["title"])
        db.session.commit()
        return jsonify({"seeded": added}), 201

    # ---------- graceful shutdown ----------
    def handle_sigterm(signum, frame):
        app.logger.info("Received signal %s - initiating graceful shutdown", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    # ---------- after request to add headers ----------
    @app.after_request
    def after_request(response):
        response.headers["X-App-Version"] = app.config.get("APP_VERSION")
        return response

    return app


# Run locally for development
if __name__ == "__main__":
    configure_logging()
    app = create_app()
    # For local dev only. Use Gunicorn in production.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 80)), debug=False)
