import os, re, csv, uuid
from io import StringIO
from datetime import datetime, date, time
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, abort, Response
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import UniqueConstraint
from services.dsa import merge_sort, binary_search_title, RegistrationHashIndex, WaitingQueue

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me-before-submission")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///events.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["WTF_CSRF_TIME_LIMIT"] = 3600
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SESSION_COOKIE_SECURE"] = os.getenv("COOKIE_SECURE", "0") == "1"
csrf = CSRFProtect(app)
db = SQLAlchemy(app)
login = LoginManager(app)
login.login_view = "landing"

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    student_number = db.Column(db.String(40), unique=True)
    year_level = db.Column(db.String(30))
    section = db.Column(db.String(2), index=True)
    program = db.Column(db.String(20))
    handled_sections = db.Column(db.String(300))
    def set_password(self, raw): self.password_hash = generate_password_hash(raw)
    def check_password(self, raw): return check_password_hash(self.password_hash, raw)

class PasswordResetRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(160), nullable=False, index=True)
    account_identifier = db.Column(db.String(80), nullable=False)
    reason = db.Column(db.String(500))
    status = db.Column(db.String(20), default="pending", nullable=False)
    admin_note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime)

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, default="")
    event_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    venue = db.Column(db.String(160), nullable=False)
    program = db.Column(db.String(40), nullable=False, default="BSCS")
    capacity = db.Column(db.Integer, nullable=False, default=100)
    lunch_start = db.Column(db.Time)
    lunch_end = db.Column(db.Time)
    archived = db.Column(db.Boolean, default=False, nullable=False)
    featured = db.Column(db.Boolean, default=False, nullable=False)
    image_filename = db.Column(db.String(255))
    @property
    def confirmed_count(self): return Registration.query.filter_by(event_id=self.id, status="registered").count()
    @property
    def waiting_count(self): return WaitingList.query.filter_by(event_id=self.id).count()
    @property
    def total_expenses(self): return sum(x.amount for x in self.expenses)
    @property
    def status(self):
        if self.archived: return "Archived"
        today, now = date.today(), datetime.now().time()
        if today < self.event_date or (today == self.event_date and now < self.start_time): return "Not started yet"
        if today > self.event_date or (today == self.event_date and now >= self.end_time): return "Ended"
        if self.lunch_start and self.lunch_end and self.lunch_start <= now < self.lunch_end: return "Lunch Break"
        return "Ongoing"

class Registration(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    receipt_code = db.Column(db.String(50), unique=True, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    status = db.Column(db.String(20), default="registered", nullable=False)
    time_in = db.Column(db.DateTime)
    time_out = db.Column(db.DateTime)
    student = db.relationship("User", backref="registrations")
    event = db.relationship("Event", backref=db.backref("registrations", lazy=True))
    __table_args__ = (UniqueConstraint("student_id", "event_id", name="uq_student_event"),)

class WaitingList(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    student = db.relationship("User")
    __table_args__ = (UniqueConstraint("student_id", "event_id", name="uq_waiting_student_event"),)

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    label = db.Column(db.String(160), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    notes = db.Column(db.String(400))
    event = db.relationship("Event", backref=db.backref("expenses", lazy=True))

class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor = db.Column(db.String(160), default="anonymous")
    action = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text)
    level = db.Column(db.String(20), default="INFO")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

@login.user_loader
def load_user(uid): return db.session.get(User, int(uid))

def log(action, details="", level="INFO"):
    db.session.add(ActivityLog(actor=current_user.email if current_user.is_authenticated else "anonymous",
                               action=action, details=details, level=level))
    db.session.commit()

def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            if current_user.role not in roles: abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco

def parse_date(s): return datetime.strptime(s, "%Y-%m-%d").date()
def parse_time(s): return datetime.strptime(s, "%H:%M").time()

@app.route("/")
def landing():
    featured_event = Event.query.filter_by(featured=True, archived=False).first()
    return render_template("landing.html", featured_event=featured_event)

@app.route("/login/<role>", methods=["GET", "POST"])
def sign_in(role):
    if role not in ("admin", "professor", "student"): abort(404)
    if request.method == "POST":
        email, password = request.form["email"].strip().lower(), request.form["password"]
        user = User.query.filter_by(email=email, role=role).first()
        if user and user.check_password(password):
            login_user(user); log("LOGIN_SUCCESS", role)
            return redirect(url_for({"admin":"admin_home","professor":"professor_home","student":"student_home"}[role]))
        log("LOGIN_FAILED", f"{role} login failed for {email}", "WARNING")
        flash("Invalid email or password.", "error")
    return render_template("login.html", role=role)

@app.route("/forgot-password/<role>", methods=["GET", "POST"])
def forgot_password(role):
    if role not in ("student", "professor"):
        abort(404)
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        identifier = request.form.get("account_identifier", "").strip()
        reason = request.form.get("reason", "").strip()
        if not email or not identifier:
            flash("Enter your email and student/professor ID.", "error")
        elif len(email) > 160 or len(identifier) > 80 or len(reason) > 500:
            flash("One or more fields are too long.", "error")
        else:
            existing = PasswordResetRequest.query.filter_by(
                role=role, email=email, status="pending"
            ).first()
            if existing:
                flash("A request for this email is already pending review.", "error")
            else:
                reset_request = PasswordResetRequest(
                    role=role, email=email, account_identifier=identifier,
                    reason=reason or None
                )
                db.session.add(reset_request)
                db.session.commit()
                log("PASSWORD_RESET_REQUESTED", f"{role} reset request for {email}")
                flash("Your request was submitted. Please wait for the administrator to review it.", "success")
                return redirect(url_for("sign_in", role=role))
    return render_template("forgot_password.html", role=role)


@app.route("/admin/password-resets/<int:request_id>/review", methods=["POST"])
@role_required("admin")
def review_password_reset(request_id):
    reset_request = db.session.get(PasswordResetRequest, request_id) or abort(404)
    action = request.form.get("action")
    note = request.form.get("admin_note", "").strip()[:500]
    if reset_request.status != "pending":
        flash("This request has already been reviewed.", "error")
        return redirect(url_for("admin_home"))
    user = User.query.filter_by(email=reset_request.email, role=reset_request.role).first()
    if action == "reject":
        reset_request.status = "rejected"
        reset_request.admin_note = note or "Request rejected by administrator."
        reset_request.reviewed_at = datetime.utcnow()
        db.session.commit()
        log("PASSWORD_RESET_REJECTED", f"Request #{reset_request.id} for {reset_request.email}", "WARNING")
        flash("Password reset request rejected.", "success")
    elif action == "approve":
        if not user:
            flash("No account matches that email and role. Verify the request before approving it.", "error")
            return redirect(url_for("admin_home"))
        if reset_request.role == "student" and user.student_number != reset_request.account_identifier:
            flash("The student number does not match this account. Verify the request before approving it.", "error")
            return redirect(url_for("admin_home"))
        reset_request.status = "approved"
        reset_request.admin_note = note or "Identity checked by administrator."
        reset_request.reviewed_at = datetime.utcnow()
        db.session.commit()
        log("PASSWORD_RESET_APPROVED", f"Request #{reset_request.id} for {reset_request.email}")
        flash("Request approved. Use the temporary-password action below to complete the reset.", "success")
    else:
        flash("Invalid review action.", "error")
    return redirect(url_for("admin_home"))


@app.route("/admin/password-resets/<int:request_id>/complete", methods=["POST"])
@role_required("admin")
def complete_password_reset(request_id):
    reset_request = db.session.get(PasswordResetRequest, request_id) or abort(404)
    if reset_request.status != "approved":
        flash("Only approved requests can be completed.", "error")
        return redirect(url_for("admin_home"))
    user = User.query.filter_by(email=reset_request.email, role=reset_request.role).first()
    if not user:
        flash("The account could not be found.", "error")
        return redirect(url_for("admin_home"))
    temporary_password = request.form.get("temporary_password", "").strip()
    if len(temporary_password) < 12:
        flash("Set a temporary password with at least 12 characters.", "error")
        return redirect(url_for("admin_home"))
    user.set_password(temporary_password)
    reset_request.status = "completed"
    reset_request.admin_note = (request.form.get("admin_note", "").strip() or "Password reset completed.")[:500]
    reset_request.reviewed_at = datetime.utcnow()
    db.session.commit()
    log("PASSWORD_RESET_COMPLETED", f"Request #{reset_request.id} for {reset_request.email}")
    flash("Password reset completed. Share the temporary password with the account owner through a verified, private channel and ask them to change it after signing in.", "success")
    return redirect(url_for("admin_home"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not current_user.check_password(current_password):
            flash("Your current password is incorrect.", "error")
        elif len(new_password) < 12:
            flash("Use a new password with at least 12 characters.", "error")
        elif new_password != confirm_password:
            flash("The new passwords do not match.", "error")
        elif current_user.check_password(new_password):
            flash("Choose a password different from your current password.", "error")
        else:
            current_user.set_password(new_password)
            db.session.commit()
            log("PASSWORD_CHANGED", "User changed their own password")
            flash("Password changed successfully.", "success")
            return redirect(url_for({"admin": "admin_home", "professor": "professor_home", "student": "student_home"}[current_user.role]))
    return render_template("change_password.html")


@app.route("/logout")
@login_required
def sign_out():
    log("LOGOUT", "User logged out")
    logout_user()
    return redirect(url_for("landing"))

@app.route("/admin")
@role_required("admin")
def admin_home():
    events = Event.query.filter_by(archived=False).order_by(Event.event_date).all()
    past = Event.query.filter_by(archived=True).order_by(Event.event_date.desc()).all()
    logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(30).all()
    reset_requests = PasswordResetRequest.query.order_by(
        PasswordResetRequest.created_at.desc()
    ).limit(50).all()
    alerts = ActivityLog.query.filter(ActivityLog.level.in_(["WARNING","ERROR"])).order_by(ActivityLog.created_at.desc()).limit(20).all()
    return render_template("admin.html", events=events, past=past, logs=logs, alerts=alerts,
        reset_requests=reset_requests,
        student_count=User.query.filter_by(role="student").count(),
        registration_count=Registration.query.filter_by(status="registered").count())

@app.route("/admin/accounts", methods=["GET","POST"])
@role_required("admin")
def accounts():
    if request.method == "POST":
        role = request.form.get("role")
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        section = request.form.get("section","").strip().upper()
        program = request.form.get("program","").strip().upper()
        student_number = request.form.get("student_number","").strip()
        year_level = request.form.get("year_level","")
        handled = request.form.get("handled_sections","").strip().upper()
        if role not in ("student","professor") or not name or not email or len(password)<8:
            flash("Provide valid details; passwords must have at least 8 characters.","error")
        elif User.query.filter_by(email=email).first():
            flash("That email already exists.","error"); log("DUPLICATE_ACCOUNT",email,"WARNING")
        elif role=="student" and (not student_number or not re.fullmatch(r"[1-4][A-Z]",section) or program not in ("BSCS","BSIT") or User.query.filter_by(student_number=student_number).first()):
            flash("Student requires a unique student number, section like 2B, and program BSCS/BSIT.","error")
        else:
            u=User(name=name,email=email,role=role,section=section if role=="student" else None,
                   program=program if role=="student" else None,student_number=student_number if role=="student" else None,
                   year_level=year_level if role=="student" else None,handled_sections=handled if role=="professor" else None)
            u.set_password(password); db.session.add(u); db.session.commit(); log("ACCOUNT_CREATED",f"{role}: {email}")
            flash("Account created.","success"); return redirect(url_for("accounts"))
    users=User.query.filter(User.role!="admin").order_by(User.role,User.name).all()
    return render_template("accounts.html",users=users)

@app.route("/admin/accounts/<int:uid>/edit", methods=["GET", "POST"])
@role_required("admin")
def account_edit(uid):
    user = db.session.get(User, uid) or abort(404)
    if user.role == "admin": abort(403)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        if not name or not email or len(name) > 120 or len(email) > 160:
            flash("Enter a valid name and email address.", "error")
        elif User.query.filter(User.email == email, User.id != user.id).first():
            flash("That email is already used by another account.", "error")
        else:
            user.name, user.email = name, email
            if user.role == "student":
                student_number = request.form.get("student_number", "").strip()
                section = request.form.get("section", "").strip().upper()
                program = request.form.get("program", "").strip().upper()
                year_level = request.form.get("year_level", "").strip()
                duplicate = User.query.filter(User.student_number == student_number, User.id != user.id).first()
                if not student_number or duplicate or year_level not in ("First Year", "Second Year", "Third Year", "Fourth Year") or not re.fullmatch(r"[1-4][A-Z]", section) or program not in ("BSCS", "BSIT"):
                    db.session.rollback()
                    flash("Student details must include a unique student number, a section like 2B, and BSCS or BSIT.", "error")
                    return render_template("account_form.html", user=user)
                user.student_number, user.section, user.program, user.year_level = student_number, section, program, year_level
            else:
                user.handled_sections = request.form.get("handled_sections", "").strip().upper()
            new_password = request.form.get("password", "")
            if new_password and len(new_password) < 12:
                db.session.rollback()
                flash("If changing the password, use at least 12 characters.", "error")
                return render_template("account_form.html", user=user)
            if new_password: user.set_password(new_password)
            db.session.commit()
            log("ACCOUNT_EDITED", f"{user.role}: {user.email}")
            flash("Account updated.", "success")
            return redirect(url_for("accounts"))
    return render_template("account_form.html", user=user)


@app.route("/admin/accounts/<int:uid>/delete", methods=["POST"])
@role_required("admin")
def account_delete(uid):
    user = db.session.get(User, uid) or abort(404)
    if user.role == "admin": abort(403)
    has_registrations = Registration.query.filter_by(student_id=user.id).first() is not None if user.role == "student" else False
    has_waitlist = WaitingList.query.filter_by(student_id=user.id).first() is not None if user.role == "student" else False
    if has_registrations or has_waitlist:
        flash("This student has registration or waiting-list records. Keep the account to preserve event records.", "error")
    else:
        email, role = user.email, user.role
        db.session.delete(user); db.session.commit()
        log("ACCOUNT_DELETED", f"{role}: {email}", "WARNING")
        flash("Account deleted.", "success")
    return redirect(url_for("accounts"))


@app.route("/admin/events/new", methods=["GET","POST"])
@role_required("admin")
def event_new():
    if request.method=="POST":
        try:
            e=Event(title=request.form["title"].strip(),description=request.form.get("description","").strip(),
                event_date=parse_date(request.form["event_date"]),start_time=parse_time(request.form["start_time"]),
                end_time=parse_time(request.form["end_time"]),venue=request.form["venue"].strip(),
                program=request.form["program"],capacity=int(request.form["capacity"]),
                lunch_start=parse_time(request.form["lunch_start"]) if request.form.get("lunch_start") else None,
                lunch_end=parse_time(request.form["lunch_end"]) if request.form.get("lunch_end") else None)
            if not e.title or not e.venue or e.capacity<1 or e.end_time<=e.start_time: raise ValueError()
            image = request.files.get("event_image")
            if image and image.filename:
                ext = os.path.splitext(secure_filename(image.filename))[1].lower()
                if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                    raise ValueError("image")
                os.makedirs(os.path.join(app.static_folder, "uploads"), exist_ok=True)
                e.image_filename = uuid.uuid4().hex + ext
                image.save(os.path.join(app.static_folder, "uploads", e.image_filename))
            if request.form.get("featured") == "1":
                Event.query.update({Event.featured: False})
                e.featured = True
            db.session.add(e); db.session.commit(); log("EVENT_CREATED",e.title); flash("Event created.","success")
            return redirect(url_for("admin_home"))
        except (ValueError,KeyError):
            db.session.rollback(); flash("Check all event fields and times.","error")
    return render_template("event_form.html",event=None)

@app.route("/admin/events/<int:eid>/edit", methods=["GET","POST"])
@role_required("admin")
def event_edit(eid):
    e=db.session.get(Event,eid) or abort(404)
    if request.method=="POST":
        try:
            e.title=request.form["title"].strip(); e.description=request.form.get("description","").strip()
            e.event_date=parse_date(request.form["event_date"]); e.start_time=parse_time(request.form["start_time"])
            e.end_time=parse_time(request.form["end_time"]); e.venue=request.form["venue"].strip()
            e.program=request.form["program"]; e.capacity=int(request.form["capacity"])
            e.lunch_start=parse_time(request.form["lunch_start"]) if request.form.get("lunch_start") else None
            e.lunch_end=parse_time(request.form["lunch_end"]) if request.form.get("lunch_end") else None
            if not e.title or not e.venue or e.capacity<max(1,e.confirmed_count) or e.end_time<=e.start_time: raise ValueError()
            image = request.files.get("event_image")
            if image and image.filename:
                ext = os.path.splitext(secure_filename(image.filename))[1].lower()
                if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                    raise ValueError("image")
                os.makedirs(os.path.join(app.static_folder, "uploads"), exist_ok=True)
                e.image_filename = uuid.uuid4().hex + ext
                image.save(os.path.join(app.static_folder, "uploads", e.image_filename))
            if request.form.get("featured") == "1":
                Event.query.filter(Event.id != e.id).update({Event.featured: False})
                e.featured = True
            elif request.form.get("featured") == "0":
                e.featured = False
            db.session.commit(); log("EVENT_EDITED",e.title); flash("Event updated.","success")
            return redirect(url_for("admin_home"))
        except (ValueError,KeyError):
            db.session.rollback(); flash("Invalid values; capacity must not be below confirmed registrations.","error")
    return render_template("event_form.html",event=e)

@app.route("/admin/events/<int:eid>/feature", methods=["POST"])
@role_required("admin")
def event_feature(eid):
    event = db.session.get(Event, eid) or abort(404)
    if event.archived:
        flash("Archived events cannot be featured.", "error")
    else:
        Event.query.update({Event.featured: False})
        event.featured = True
        db.session.commit()
        log("EVENT_FEATURED", event.title)
        flash("Main-page featured event updated.", "success")
    return redirect(url_for("admin_home"))

@app.route("/admin/events/<int:eid>/unfeature", methods=["POST"])
@role_required("admin")
def event_unfeature(eid):
    event = db.session.get(Event, eid) or abort(404)
    event.featured = False
    db.session.commit()
    log("EVENT_UNFEATURED", event.title)
    flash("Featured event removed from the main page.", "success")
    return redirect(url_for("admin_home"))

@app.route("/admin/events/<int:eid>/delete",methods=["POST"])
@role_required("admin")
def event_delete(eid):
    e=db.session.get(Event,eid) or abort(404)
    if (Registration.query.filter_by(event_id=e.id).count() or
            WaitingList.query.filter_by(event_id=e.id).count() or
            Expense.query.filter_by(event_id=e.id).count()):
        flash("Events with registrations, waiting-list entries, or expenses cannot be deleted; archive them instead.", "error")
    else:
        title=e.title; db.session.delete(e); db.session.commit(); log("EVENT_DELETED",title,"WARNING"); flash("Event deleted.","success")
    return redirect(url_for("admin_home"))

@app.route("/admin/events/<int:eid>/archive",methods=["POST"])
@role_required("admin")
def event_archive(eid):
    e=db.session.get(Event,eid) or abort(404)
    if e.status!="Ended": flash("Only ended events can be archived.","error")
    else: e.archived=True; db.session.commit(); log("EVENT_ARCHIVED",e.title); flash("Event archived.","success")
    return redirect(url_for("admin_home"))

@app.route("/admin/events/<int:eid>/expenses",methods=["GET","POST"])
@role_required("admin")
def expenses(eid):
    e=db.session.get(Event,eid) or abort(404)
    if request.method=="POST":
        try:
            label=request.form["label"].strip(); amount=float(request.form["amount"])
            if not label or amount<0: raise ValueError()
            db.session.add(Expense(event_id=e.id,label=label,amount=amount,notes=request.form.get("notes","")))
            db.session.commit(); log("EXPENSE_ADDED",f"{e.title}: {label} ₱{amount:.2f}"); flash("Expense added.","success")
            return redirect(url_for("expenses",eid=e.id))
        except ValueError: db.session.rollback(); flash("Enter a label and a non-negative amount.","error")
    return render_template("expenses.html",event=e)

@app.route("/admin/expenses/<int:xid>/edit", methods=["GET", "POST"])
@role_required("admin")
def expense_edit(xid):
    expense = db.session.get(Expense, xid) or abort(404)
    if request.method == "POST":
        label = request.form.get("label", "").strip()
        notes = request.form.get("notes", "").strip()
        try:
            amount = float(request.form.get("amount", ""))
            if not label or len(label) > 160 or len(notes) > 400 or amount < 0 or amount != amount or amount == float("inf"):
                raise ValueError()
            expense.label, expense.amount, expense.notes = label, amount, notes
            db.session.commit()
            log("EXPENSE_EDITED", f"{expense.event.title}: {label} ₱{amount:.2f}")
            flash("Expense updated.", "success")
            return redirect(url_for("expenses", eid=expense.event_id))
        except ValueError:
            db.session.rollback()
            flash("Enter a valid label and a non-negative amount.", "error")
    return render_template("expense_form.html", expense=expense)


@app.route("/admin/expenses/<int:xid>/delete", methods=["POST"])
@role_required("admin")
def expense_delete(xid):
    expense = db.session.get(Expense, xid) or abort(404)
    eid, label = expense.event_id, expense.label
    db.session.delete(expense); db.session.commit()
    log("EXPENSE_DELETED", f"Event #{eid}: {label}", "WARNING")
    flash("Expense deleted.", "success")
    return redirect(url_for("expenses", eid=eid))

@app.route("/admin/events/<int:eid>/registrations")
@role_required("admin")
def event_registrations(eid):
    event = db.session.get(Event, eid) or abort(404)
    term = request.args.get("q", "").strip().casefold()
    records = Registration.query.filter_by(event_id=eid, status="registered").all()
    records = merge_sort(records, key=lambda r: (r.student.name.casefold(), r.student.student_number or ""))
    if term:
        records = [r for r in records if term in r.student.name.casefold() or term in (r.student.student_number or "").casefold() or term in r.receipt_code.casefold()]
    return render_template("registrations.html", event=event, registrations=records, term=term)


@app.route("/admin/events/<int:eid>/registrations.csv")
@role_required("admin")
def event_registrations_csv(eid):
    event = db.session.get(Event, eid) or abort(404)
    records = Registration.query.filter_by(event_id=eid, status="registered").all()
    records = merge_sort(records, key=lambda r: (r.student.name.casefold(), r.student.student_number or ""))
    out = StringIO(); writer = csv.writer(out)
    writer.writerow(["Receipt Code", "Student Number", "Student Name", "Email", "Year Level", "Section", "Program", "Registered At", "Time In", "Time Out"])
    for r in records:
        writer.writerow([r.receipt_code, r.student.student_number, r.student.name, r.student.email, r.student.year_level, r.student.section, r.student.program, r.created_at.strftime("%Y-%m-%d %H:%M"), r.time_in.strftime("%Y-%m-%d %H:%M") if r.time_in else "", r.time_out.strftime("%Y-%m-%d %H:%M") if r.time_out else ""])
    log("EVENT_REGISTRATIONS_EXPORTED", f"{event.title}: {len(records)} records")
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=event-{eid}-registrations.csv"})


@app.route("/attendance")
@role_required("admin", "professor")
def attendance_dashboard():
    term = request.args.get("q", "").strip().casefold()
    event_id = request.args.get("event_id", type=int)
    query = Registration.query.filter_by(status="registered")
    if event_id: query = query.filter_by(event_id=event_id)
    records = query.all()
    if current_user.role == "professor":
        sections = [x.strip() for x in (current_user.handled_sections or "").split(",") if x.strip()]
        records = [r for r in records if r.student.section in sections]
    visible_event_ids = {r.event_id for r in records}
    if term:
        records = [r for r in records if term in r.receipt_code.casefold() or term in r.student.name.casefold() or term in (r.student.student_number or "").casefold()]
    records = merge_sort(records, key=lambda r: (r.event.event_date, r.event.title.casefold(), r.student.name.casefold()))
    events = Event.query.filter_by(archived=False).order_by(Event.event_date).all()
    if current_user.role == "professor":
        events = [e for e in events if e.id in visible_event_ids]
    return render_template("attendance.html", registrations=records, events=events, term=term, selected_event=event_id)

@app.route("/professor")
@role_required("professor")
def professor_home():
    sections=[x.strip() for x in (current_user.handled_sections or "").split(",") if x.strip()]
    students=User.query.filter_by(role="student").filter(User.section.in_(sections)).order_by(User.section,User.name).all() if sections else []
    return render_template("professor.html",students=students,sections=sections)

@app.route("/professor/students.csv")
@role_required("professor")
def professor_csv():
    sections=[x.strip() for x in (current_user.handled_sections or "").split(",") if x.strip()]
    students=User.query.filter_by(role="student").filter(User.section.in_(sections)).order_by(User.section,User.name).all() if sections else []
    out=StringIO(); writer=csv.writer(out)
    writer.writerow(["Student Number","Name","Email","Year Level","Section","Program"])
    for s in students: writer.writerow([s.student_number,s.name,s.email,s.year_level,s.section,s.program])
    log("CSV_EXPORTED",f"{len(students)} students")
    return Response(out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=students.csv"})

@app.route("/student")
@role_required("student")
def student_home():
    events=Event.query.filter_by(archived=False).all()
    term=request.args.get("q","").strip()
    events=merge_sort(events,key=lambda e:e.title.casefold())
    if term:
        exact=binary_search_title(events,term)
        events=[e for e in events if (e.title.casefold()==term.casefold())] if exact else [e for e in events if term.casefold() in e.title.casefold()]
    return render_template("student.html",events=events,term=term)

@app.route("/student/events/<int:eid>")
@role_required("student")
def event_detail(eid):
    e=db.session.get(Event,eid) or abort(404)
    registration=Registration.query.filter_by(student_id=current_user.id,event_id=e.id,status="registered").first()
    waiting=WaitingList.query.filter_by(student_id=current_user.id,event_id=e.id).first()
    return render_template("event_detail.html",event=e,registration=registration,waiting=waiting)

@app.route("/student/events/<int:eid>/register", methods=["POST"])
@role_required("student")
def register(eid):
    e = db.session.get(Event, eid) or abort(404)
    full_name = request.form.get("full_name", "").strip()
    student_number = request.form.get("student_number", "").strip()
    year_level = request.form.get("year_level", "").strip()
    section = request.form.get("section", "").strip().upper()
    program = request.form.get("program", "").strip().upper()
    duplicate_number = User.query.filter(User.student_number == student_number, User.id != current_user.id).first() if student_number else None
    if (not full_name or len(full_name) > 120 or not student_number or len(student_number) > 40 or duplicate_number
            or year_level not in ("First Year", "Second Year", "Third Year", "Fourth Year")
            or not re.fullmatch(r"[1-4][A-Z]", section) or program not in ("BSCS", "BSIT")):
        flash("Check your registration details. Student number must be unique and section must look like 2B.", "error")
        return redirect(url_for("event_detail", eid=e.id))
    waiting = WaitingList.query.filter_by(student_id=current_user.id, event_id=e.id).first()
    all_regs = Registration.query.filter_by(event_id=e.id).all()
    if RegistrationHashIndex(all_regs).contains(current_user.id, e.id) or waiting:
        log("DUPLICATE_REGISTRATION", f"user={current_user.id}, event={e.id}", "WARNING")
        flash("You are already registered or waitlisted.", "error")
    elif e.archived or e.status == "Ended":
        flash("Registration is closed.", "error")
    else:
        current_user.name = full_name
        current_user.student_number = student_number
        current_user.year_level = year_level
        current_user.section = section
        current_user.program = program
        if e.confirmed_count >= e.capacity:
            db.session.add(WaitingList(student_id=current_user.id, event_id=e.id))
            db.session.commit()
            log("WAITLIST_JOINED", f"user={current_user.id}, event={e.id}")
            flash("Event full; you joined the FIFO waiting list.", "success")
        else:
            code = f"EVT-{e.id}-{current_user.id}-{datetime.now().strftime('%Y%m%d%H%M%S%f')[-8:]}"
            r = Registration(receipt_code=code, student_id=current_user.id, event_id=e.id)
            db.session.add(r)
            db.session.commit()
            log("STUDENT_REGISTERED", code)
            return redirect(url_for("receipt", rid=r.id))
    return redirect(url_for("event_detail", eid=e.id))


@app.route("/student/waitlist/<int:wid>/cancel", methods=["POST"])
@role_required("student")
def cancel_waitlist(wid):
    item = db.session.get(WaitingList, wid) or abort(404)
    if item.student_id != current_user.id: abort(403)
    event_id = item.event_id
    db.session.delete(item)
    db.session.commit()
    log("WAITLIST_LEFT", f"user={current_user.id}, event={event_id}")
    flash("You have left the event waiting list.", "success")
    return redirect(url_for("event_detail", eid=event_id))

@app.route("/student/registrations/<int:rid>/cancel", methods=["POST"])
@role_required("student")
def cancel_registration(rid):
    registration = db.session.get(Registration, rid) or abort(404)
    if registration.student_id != current_user.id: abort(403)
    if registration.time_in or registration.time_out:
        flash("A registration with recorded attendance cannot be cancelled.", "error")
        return redirect(url_for("event_detail", eid=registration.event_id))
    event = registration.event
    if event.archived or event.status == "Ended":
        flash("Registration cannot be cancelled after the event has ended.", "error")
        return redirect(url_for("event_detail", eid=event.id))
    code = registration.receipt_code
    db.session.delete(registration)
    db.session.flush()
    # Use the custom FIFO queue to select the earliest waiting student.
    waiting_rows = WaitingList.query.filter_by(event_id=event.id).order_by(WaitingList.created_at, WaitingList.id).all()
    queue = WaitingQueue()
    for row in waiting_rows:
        queue.enqueue(row.id)
    next_waiting_id = queue.dequeue()
    if next_waiting_id is not None:
        next_waiting = db.session.get(WaitingList, next_waiting_id)
        if next_waiting:
            promoted_student_id = next_waiting.student_id
            promoted_code = f"EVT-{event.id}-{promoted_student_id}-{datetime.now().strftime('%Y%m%d%H%M%S%f')[-8:]}"
            promoted = Registration(receipt_code=promoted_code, student_id=promoted_student_id, event_id=event.id)
            db.session.add(promoted)
            db.session.delete(next_waiting)
            db.session.commit()
            log("WAITLIST_PROMOTED", f"{promoted_code}; promoted after cancellation of {code}")
            flash("Registration cancelled. The next student on the waiting list has been promoted.", "success")
        else:
            db.session.commit()
            flash("Registration cancelled.", "success")
    else:
        db.session.commit()
        flash("Registration cancelled.", "success")
    log("REGISTRATION_CANCELLED", code, "WARNING")
    return redirect(url_for("student_home"))


@app.route("/student/receipt/<int:rid>")
@role_required("student")
def receipt(rid):
    r=db.session.get(Registration,rid) or abort(404)
    if r.student_id!=current_user.id: abort(403)
    return render_template("receipt.html",registration=r)

@app.route("/attendance/<int:rid>/<action>",methods=["POST"])
@role_required("admin","professor")
def attendance(rid,action):
    r=db.session.get(Registration,rid) or abort(404)
    if current_user.role == "professor":
        sections = [x.strip() for x in (current_user.handled_sections or "").split(",") if x.strip()]
        if r.student.section not in sections: abort(403)
    if action=="time-in" and not r.time_in:
        r.time_in=datetime.now(); db.session.commit(); log("ATTENDANCE_TIME_IN",str(rid)); flash("Time-in recorded.","success")
    elif action=="time-out" and r.time_in and not r.time_out:
        r.time_out=datetime.now(); db.session.commit(); log("ATTENDANCE_TIME_OUT",str(rid)); flash("Time-out recorded.","success")
    else: flash("Invalid or duplicate attendance action.","error")
    return redirect(request.referrer or url_for("admin_home"))

@app.errorhandler(403)
def forbidden(_): return render_template("error.html",code=403,message="You do not have permission to view this page."),403
@app.errorhandler(404)
def not_found(_): return render_template("error.html",code=404,message="The requested page or record was not found."),404

with app.app_context():
    db.create_all()
    # Lightweight SQLite migration for databases created before featured-event support.
    from sqlalchemy import inspect
    if "event" in inspect(db.engine).get_table_names():
        columns = {column["name"] for column in inspect(db.engine).get_columns("event")}
        with db.engine.begin() as connection:
            if "featured" not in columns:
                connection.exec_driver_sql("ALTER TABLE event ADD COLUMN featured BOOLEAN NOT NULL DEFAULT 0")
            if "image_filename" not in columns:
                connection.exec_driver_sql("ALTER TABLE event ADD COLUMN image_filename VARCHAR(255)")
    admin_email=os.getenv("ADMIN_EMAIL","admin@example.com").lower()
    admin_password=os.getenv("ADMIN_PASSWORD","ChangeThisAdminPassword123!")
    if not User.query.filter_by(email=admin_email).first():
        u=User(name="System Administrator",email=admin_email,role="admin")
        u.set_password(admin_password); db.session.add(u); db.session.commit()
    if Event.query.count()==0:
        today=date.today()
        db.session.add_all([
            Event(title="CCS Acquaintance Party",description="Meet students and faculty from the College of Computer Studies.",
                  event_date=today,start_time=time(8),end_time=time(17),venue="Campus Gymnasium",program="BSCS",capacity=100,lunch_start=time(12),lunch_end=time(13)),
            Event(title="Programming Workshop",description="A hands-on introductory programming workshop.",
                  event_date=today,start_time=time(9),end_time=time(15),venue="Computer Laboratory",program="BSIT",capacity=40,lunch_start=time(12),lunch_end=time(13))
        ]); db.session.commit()

if __name__=="__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "0") == "1")
