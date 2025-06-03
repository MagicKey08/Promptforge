from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_file
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
from flask_mail import Mail, Message
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from pymongo import MongoClient
from bson.objectid import ObjectId
from itsdangerous import URLSafeTimedSerializer
from dotenv import load_dotenv
import os, stripe, paypalrestsdk, requests
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from functools import wraps
from xhtml2pdf import pisa
from io import BytesIO

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

# === Init ===
bcrypt = Bcrypt(app)
csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, default_limits=["200/day", "50/hour"])
limiter.init_app(app)
serializer = URLSafeTimedSerializer(app.secret_key)

# === Uploads ===
app.config['UPLOAD_FOLDER'] = os.path.join("static", "downloads")
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# === MongoDB ===
client = MongoClient(os.getenv("MONGODB_URI"))
db = client["promptshop"]
users = db["users"]
products = db["products"]
purchases = db["purchases"]
coupons = db["coupons"]  # Rabattcodes

# === Mail ===
app.config.update(
    MAIL_SERVER="smtp.gmail.com",
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USERNAME=os.getenv("MAIL_USER"),
    MAIL_PASSWORD=os.getenv("MAIL_PASS"),
    MAIL_DEFAULT_SENDER=os.getenv("MAIL_USER")
)
mail = Mail(app)

# === Zahlungsanbieter ===
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLIC_KEY = os.getenv("STRIPE_PUBLIC_KEY")

paypalrestsdk.configure({
    "mode": "sandbox",
    "client_id": os.getenv("PAYPAL_CLIENT_ID"),
    "client_secret": os.getenv("PAYPAL_CLIENT_SECRET")
})

# === Login & Benutzerklasse ===
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data["_id"])
        self.email = user_data["email"]
        self.role = user_data.get("role", "user")
        self.newsletter = user_data.get("newsletter", False)

@login_manager.user_loader
def load_user(user_id):
    data = users.find_one({"_id": ObjectId(user_id)})
    return User(data) if data else None

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated

def send_confirmation_email(to_email):
    token = serializer.dumps(to_email, salt="email-confirm")
    url = url_for('verify_email', token=token, _external=True)
    msg = Message("✅ Bitte bestätige deine E-Mail", recipients=[to_email])
    msg.html = render_template('confirm_email.html', confirm_url=url)
    mail.send(msg)

def send_invoice_email(user_email, product, price, timestamp):
    pdf_html = render_template("emails/invoice.html", product=product, price=price / 100, timestamp=timestamp)
    pdf_file = BytesIO()
    pisa.CreatePDF(pdf_html, dest=pdf_file)
    pdf_file.seek(0)

    msg = Message("🧾 Deine Rechnung – PromptForge", recipients=[user_email])
    msg.body = "Vielen Dank für deinen Kauf. Die Rechnung findest du im Anhang."
    msg.attach("rechnung.pdf", "application/pdf", pdf_file.read())
    mail.send(msg)

def verify_recaptcha(response_token):
    res = requests.post("https://www.google.com/recaptcha/api/siteverify", data={
        "secret": os.getenv("RECAPTCHA_SECRET_KEY"),
        "response": response_token
    }).json()
    return res.get("success", False)


# === Startseite ===
@app.route('/')
def index():
    return render_template("index.html", products=list(products.find()), stripe_key=STRIPE_PUBLIC_KEY)

# === Authentifizierung ===
@app.route('/signup', methods=["GET", "POST"])
@limiter.limit("5/minute")
def signup():
    if request.method == "POST":
        if not verify_recaptcha(request.form.get("g-recaptcha-response")):
            flash("Bitte reCAPTCHA bestätigen.", "error")
            return render_template("signup.html")

        email = request.form["email"].strip().lower()
        if users.find_one({"email": email}):
            flash("E-Mail existiert bereits.", "error")
            return render_template("signup.html")

        user = {
            "email": email,
            "username": request.form["username"],
            "password": bcrypt.generate_password_hash(request.form["password"]).decode("utf-8"),
            "verified": False,
            "role": "admin" if email == os.getenv("ADMIN_EMAIL") else "user",
            "newsletter": False
        }
        users.insert_one(user)
        send_confirmation_email(email)
        flash("E-Mail-Bestätigung wurde gesendet.", "warning")
        return render_template("confirm_email.html")
    return render_template("signup.html")

@app.route('/login', methods=["GET", "POST"])
@limiter.limit("10/minute")
def login():
    if request.method == "POST":
        if not verify_recaptcha(request.form.get("g-recaptcha-response")):
            flash("Bitte reCAPTCHA bestätigen.", "error")
            return render_template("login.html")

        user = users.find_one({"email": request.form["email"]})
        if not user:
            flash("E-Mail nicht gefunden.", "error")
            return render_template("login.html")
        if not user.get("verified"):
            return render_template("unverified.html", user_email=user["email"])

        if bcrypt.check_password_hash(user["password"], request.form["password"]):
            login_user(User(user), remember=True)
            # Wiederherstellen des gespeicherten Warenkorbs
            if "cart" in user:
                session["cart"] = user["cart"]
            return redirect(url_for("index"))
        flash("Falsches Passwort.", "error")
    return render_template("login.html")

@app.route('/logout')
@login_required
def logout():
    # Warenkorb speichern
    if "cart" in session:
        users.update_one({"_id": ObjectId(current_user.id)}, {"$set": {"cart": session["cart"]}})
    session.pop("cart", None)
    logout_user()
    return redirect(url_for("index"))

@app.route('/verify')
def verify_email():
    try:
        email = serializer.loads(request.args.get("token"), salt="email-confirm", max_age=3600)
    except:
        return "Link ungültig oder abgelaufen."
    users.update_one({"email": email}, {"$set": {"verified": True}})
    flash("E-Mail bestätigt!", "success")
    return redirect(url_for("login"))

@app.route('/resend-verification', methods=["POST"])
def resend_verification():
    user = users.find_one({"email": request.form["email"]})
    if user and not user.get("verified"):
        send_confirmation_email(user["email"])
        flash("Bestätigungs-E-Mail erneut gesendet.", "success")
        return render_template("resend_success.html")
    return "Benutzer nicht gefunden oder bereits bestätigt."

# === Einstellungen ===
@app.route("/settings")
@login_required
def settings():
    return render_template("settings.html")

@app.route("/update-newsletter", methods=["POST"])
@login_required
def update_newsletter():
    users.update_one({"_id": ObjectId(current_user.id)}, {"$set": {"newsletter": "newsletter" in request.form}})
    flash("Newsletter aktualisiert.", "success")
    return redirect(url_for("settings"))

@app.route("/change-password", methods=["POST"])
@login_required
def change_password():
    data = request.form
    user = users.find_one({"_id": ObjectId(current_user.id)})
    if not bcrypt.check_password_hash(user["password"], data["current_password"]):
        flash("Aktuelles Passwort falsch.", "error")
    elif data["new_password"] != data["confirm_password"]:
        flash("Neue Passwörter stimmen nicht überein.", "error")
    else:
        hashed = bcrypt.generate_password_hash(data["new_password"]).decode("utf-8")
        users.update_one({"_id": ObjectId(current_user.id)}, {"$set": {"password": hashed}})
        flash("Passwort geändert.", "success")
    return redirect(url_for("settings"))

@app.route("/update-theme", methods=["POST"])
@login_required
def update_theme():
    theme = request.form.get("theme", "system")
    session["theme"] = theme if theme in ["light", "dark", "system"] else "system"
    flash("Theme gespeichert.", "success")
    return redirect(url_for("settings"))

@app.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    users.delete_one({"_id": ObjectId(current_user.id)})
    logout_user()
    flash("Account gelöscht.", "success")
    return redirect(url_for("index"))

# === Shop & Bestellungen ===
@app.route("/add-to-cart/<product_id>")
def add_to_cart(product_id):
    session.setdefault("cart", []).append(product_id)
    session.modified = True
    return redirect(url_for("index"))

@app.route("/remove-from-cart/<int:index>", methods=["POST"])
@login_required
def remove_from_cart(index):
    if "cart" in session and 0 <= index < len(session["cart"]):
        session["cart"].pop(index)
        session.modified = True
    return redirect(url_for("cart"))

@app.route("/cart/increase/<product_id>", methods=["POST"])
@login_required
def increase_quantity(product_id):
    session.setdefault("cart", []).append(product_id)
    session.modified = True
    return redirect(url_for("cart"))

@app.route("/cart/decrease/<product_id>", methods=["POST"])
@login_required
def decrease_quantity(product_id):
    if "cart" in session and product_id in session["cart"]:
        session["cart"].remove(product_id)
        session.modified = True
    return redirect(url_for("cart"))

@app.route("/cart/remove-all/<product_id>", methods=["POST"])
@login_required
def remove_all_from_cart(product_id):
    session["cart"] = [pid for pid in session.get("cart", []) if pid != product_id]
    session.modified = True
    return redirect(url_for("cart"))


@app.route("/cart")
@login_required
def cart():
    cart_raw = session.get("cart", [])
    cart_data = {}
    total = 0

    for pid in cart_raw:
        if pid not in cart_data:
            product = products.find_one({"_id": ObjectId(pid)})
            if product:
                cart_data[pid] = {"product": product, "quantity": 1}
                total += product["price"]
        else:
            cart_data[pid]["quantity"] += 1
            total += cart_data[pid]["product"]["price"]

    cart_items = list(cart_data.values())
    return render_template("cart.html", cart_items=cart_items, total=total / 100)

@app.route("/checkout-cart", methods=["POST"])
@login_required
def checkout_cart():
    cart_ids = session.get("cart", [])
    if not cart_ids:
        flash("Dein Warenkorb ist leer.", "error")
        return redirect(url_for("cart"))

    # Rabatt prüfen
    coupon_code = request.form.get("coupon", "").strip().upper()
    discount = 0
    if coupon_code:
        coupon = coupons.find_one({"code": coupon_code})
        if coupon:
            discount = coupon.get("discount", 0)
        else:
            flash("Ungültiger Rabattcode.", "error")
            return redirect(url_for("cart"))

    # Produkte & Preis berechnen
    line_items = []
    total = 0
    counted = {}
    for pid in cart_ids:
        counted[pid] = counted.get(pid, 0) + 1
    for pid, qty in counted.items():
        product = products.find_one({"_id": ObjectId(pid)})
        if product:
            unit_price = product["price"]
            if discount:
                unit_price = int(unit_price * (100 - discount) / 100)
            total += unit_price * qty
            line_items.append({
                "price_data": {
                    "currency": "eur",
                    "product_data": {"name": product["title"]},
                    "unit_amount": unit_price
                },
                "quantity": qty
            })

    # Zahlungsmethode auswerten
    method = request.form.get("payment_method")
    if method == "paypal":
        flash("PayPal für mehrere Produkte ist bald verfügbar.", "warning")
        return redirect(url_for("cart"))

    # Stripe Checkout starten
    session_obj = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=line_items,
        mode="payment",
        success_url=url_for("success", _external=True),
        cancel_url=url_for("cart", _external=True)
    )
    return redirect(session_obj.url, code=303)

@app.route("/validate-coupon", methods=["POST"])
def validate_coupon():
    code = request.json.get("code", "").strip().upper()
    product_id = request.json.get("product_id")
    product = products.find_one({"_id": ObjectId(product_id)})

    if not product:
        return {"valid": False}

    discount = 0
    coupon = coupons.find_one({"code": code})
    if coupon:
        discount = coupon.get("discount", 0)

    final_price = int(product["price"] * (100 - discount) / 100)
    return {"valid": True, "discount": discount, "final_price": final_price}



@app.route("/checkout/<product_id>", methods=["GET", "POST"])
@login_required
def checkout(product_id):
    product = products.find_one({"_id": ObjectId(product_id)})
    if not product:
        abort(404)

    if request.method == "POST":
        coupon_code = request.form.get("coupon", "").strip().upper()
        discount = 0
        if coupon_code:
            coupon = db["coupons"].find_one({"code": coupon_code})
            if coupon:
                discount = coupon.get("discount", 0)
            else:
                flash("Ungültiger Rabattcode.", "error")
                return render_template("checkout.html", product_name=product["title"])

        price = product["price"]
        if discount > 0:
            price = int(price * (100 - discount) / 100)

        if request.form.get("payment_method") == "paypal":
            return redirect(url_for("paypal_checkout", product_id=product_id))

        session_obj = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "eur",
                    "product_data": {"name": product["title"]},
                    "unit_amount": price
                },
                "quantity": 1
            }],
            mode="payment",
            success_url=url_for("success", file=product["file"], _external=True),
            cancel_url=url_for("cart", _external=True)
        )
        return redirect(session_obj.url, code=303)

    return render_template("checkout.html", product_name=product["title"])

@app.route("/paypal-checkout/<product_id>")
@login_required
def paypal_checkout(product_id):
    product = products.find_one({"_id": ObjectId(product_id)})
    if not product: abort(404)
    payment = paypalrestsdk.Payment({
        "intent": "sale",
        "payer": {"payment_method": "paypal"},
        "redirect_urls": {
            "return_url": url_for("paypal_execute", product_id=product_id, _external=True),
            "cancel_url": url_for("cart", _external=True)
        },
        "transactions": [{
            "item_list": {
                "items": [{
                    "name": product["title"],
                    "sku": str(product["_id"]),
                    "price": f"{product['price'] / 100:.2f}",
                    "currency": "EUR",
                    "quantity": 1
                }]
            },
            "amount": {
                "total": f"{product['price'] / 100:.2f}",
                "currency": "EUR"
            },
            "description": f"Kauf von {product['title']}"
        }]
    })
    if payment.create():
        session["paypal_payment_id"] = payment.id
        for link in payment.links:
            if link.method == "REDIRECT":
                return redirect(link.href)
    flash("PayPal-Zahlung fehlgeschlagen.", "error")
    return redirect(url_for("cart"))

@app.before_request
def track_affiliate():
    ref = request.args.get("ref")
    if ref:
        session["ref"] = ref



@app.route("/paypal-execute/<product_id>")
@login_required
def paypal_execute(product_id):
    payment = paypalrestsdk.Payment.find(session.get("paypal_payment_id"))
    if not payment.execute({"payer_id": request.args.get("PayerID")}):
        flash("PayPal-Zahlung fehlgeschlagen.", "error")
        return redirect(url_for("cart"))

    product = products.find_one({"_id": ObjectId(product_id)})
    purchases.insert_one({
        "user_id": current_user.id,
        "email": current_user.email,
        "file": product["file"],
        "timestamp": datetime.utcnow(),
        "downloaded": False,
        "expires_at": datetime.utcnow() + timedelta(days=7)
    })
    send_invoice_email(current_user.email, product, product["price"], datetime.utcnow())
    flash("Zahlung erfolgreich!", "success")
    return redirect(url_for("success", file=product["file"]))

@app.route("/success")
@login_required
def success():
    cart = session.get("cart", [])
    if not cart:
        return render_template("success.html", file=None)

    added = []
    for pid in cart:
        product = products.find_one({"_id": ObjectId(pid)})
        if not product:
            continue

        purchases.insert_one({
            "user_id": current_user.id,
            "email": current_user.email,
            "file": product["file"],
            "timestamp": datetime.utcnow(),
            "downloaded": False,
            "expires_at": datetime.utcnow() + timedelta(days=7)
        })
        send_invoice_email(current_user.email, product, product["price"], datetime.utcnow())
        added.append(product["file"])

    # cart leeren
    session["cart"] = []
    flash("🧾 Zahlung erfolgreich! Dateien verfügbar unter Bestellungen.", "success")
    return render_template("success.html", file=added[0] if added else None)

@app.route("/orders")
@login_required
def orders():
    history = list(purchases.find({"user_id": current_user.id}))
    now = datetime.utcnow()

    for o in history:
        expired = o.get("expires_at") and now > o["expires_at"]
        if expired:
            o["status"] = "Abgelaufen"
            o["downloadable"] = False
        elif o.get("downloaded"):
            o["status"] = "Bereits heruntergeladen"
            o["downloadable"] = False
        else:
            o["status"] = "Bereit"
            o["downloadable"] = True
    return render_template("orders.html", orders=history)

@app.route("/product-info/<product_id>")
def product_info(product_id):
    product = products.find_one({"_id": ObjectId(product_id)})
    if not product:
        return {}, 404
    return {
        "title": product["title"],
        "description": product["description"],
        "price": product["price"],
        "preview_image": product.get("image", "default.jpg")
    }


@app.route("/download/<filename>")
@login_required
def download(filename):
    purchase = purchases.find_one({
        "user_id": current_user.id,
        "file": filename
    })

    if not purchase:
        abort(403)

    # ⏱ Ablauf prüfen
    if datetime.utcnow() > purchase.get("expires_at", datetime.utcnow()):
        flash("⏱ Dieser Download-Link ist abgelaufen.", "error")
        return redirect(url_for("orders"))

    # 🔁 Nur einmaliger Download
    if purchase.get("downloaded"):
        flash("⚠️ Du hast diese Datei bereits heruntergeladen.", "error")
        return redirect(url_for("orders"))

    # ✅ Download freigeben und Status speichern
    purchases.update_one({"_id": purchase["_id"]}, {"$set": {"downloaded": True}})
    return send_file(os.path.join(app.config["UPLOAD_FOLDER"], filename), as_attachment=True)

# === Adminbereich ===
@app.route("/admin", methods=["GET", "POST"])
@login_required
@admin_required
def admin():
    if request.method == "POST":
        title = request.form["title"]
        price = int(float(request.form["price"]) * 100)
        description = request.form["description"]
        file = request.files["file"]
        image = request.files.get("preview_image")

        if file:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        else:
            flash("PDF-Datei fehlt.", "error")
            return redirect(url_for("admin"))

        image_filename = None
        if image and image.filename:
            image_filename = secure_filename(image.filename)
            image_path = os.path.join("static", "preview_images")
            os.makedirs(image_path, exist_ok=True)
            image.save(os.path.join(image_path, image_filename))

        products.insert_one({
            "title": title,
            "price": price,
            "description": description,
            "file": filename,
            "image": image_filename
        })
        flash("Produkt erfolgreich hochgeladen.", "success")
        return redirect(url_for("admin"))

    return render_template("admin.html", products=list(products.find()), orders=list(purchases.find()))

@app.route("/edit-product/<product_id>", methods=["GET", "POST"])
@login_required
@admin_required
def edit_product(product_id):
    product = products.find_one({"_id": ObjectId(product_id)})
    if request.method == "POST":
        products.update_one({"_id": ObjectId(product_id)}, {
            "$set": {
                "title": request.form["title"],
                "price": int(float(request.form["price"]) * 100),
                "description": request.form["description"]
            }
        })
        flash("Produkt aktualisiert.", "success")
        return redirect(url_for("admin"))
    return render_template("edit_product.html", product=product)

@app.route("/delete-product/<product_id>", methods=["POST"])
@login_required
@admin_required
def delete_product(product_id):
    products.delete_one({"_id": ObjectId(product_id)})
    flash("Produkt gelöscht.", "success")
    return redirect(url_for("admin"))

@app.route("/create-coupon", methods=["POST"])
@login_required
@admin_required
def create_coupon():
    code = request.form.get("code", "").strip().upper()
    discount = int(request.form.get("discount", 0))
    if not code or discount <= 0 or discount > 100:
        flash("Ungültiger Rabattcode.", "error")
        return redirect(url_for("admin"))

    coupons.update_one({"code": code}, {"$set": {"discount": discount}}, upsert=True)
    flash(f"🎉 Rabattcode '{code}' mit {discount}% gespeichert!", "success")
    return redirect(url_for("admin"))

# === Fehlerbehandlung ===
@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403

@app.context_processor
def inject_globals():
    return dict(
        products=products,
        products_collection=products,  # ← das brauchst du!
        ObjectId=ObjectId,
        selected_theme=session.get("theme", "system")
    )

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)