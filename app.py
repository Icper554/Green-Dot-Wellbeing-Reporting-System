from flask import Flask, render_template, request, redirect, url_for, session, flash
from datetime import datetime
import os
import uuid
import secrets
import smtplib
from email.message import EmailMessage
from urllib.parse import urlparse
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash
import mysql.connector
from functools import wraps

app = Flask(__name__)

# Clears cookie session from previous run 
@app.before_request
def clear_stale_sessions_after_restart():
    if session.get("user_id") and session.get("app_run_token") != APP_RUN_TOKEN:
        session.clear()

# Session secret key used to protect session data
app.config["SECRET_KEY"] = "GREENDOTS_SECRET_KEY"

# Crete new token every new session
APP_RUN_TOKEN = secrets.token_hex(16)

# Evidence Upload to Folder in Directory
UPLOAD_FOLDER = os.path.join("static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Max File Size
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

# Allowed File Types
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

# checks if file has allowed file type 
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# Email account used for SMTP service
EMAIL_ADDRESS = "greendotprojecttesting@gmail.com"
EMAIL_PASSWORD = "bfyzlmgkweitrehj"

# MySQL database connection
DB_CONFIG = {
    "host": "localhost",
    "user": "greendot_user",
    "password": "greendot123",
    "database": "greendot_db",
}
# connect to MySQL
def get_db():
    return mysql.connector.connect(**DB_CONFIG)

# Generates a Unique Reference ID
def format_report_code(row_id: int) -> str:
    return f"GD-2026-{row_id:06d}"

# Checks user in user table when log in
def get_user_by_username(username: str):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
    user = cursor.fetchone()
    cursor.close()
    db.close()
    return user


# Validates that a local path is safe to prevent unsafe redirection
def safe_local_path(next_url: str) -> str:
    if not next_url:
        return ""
    parsed = urlparse(next_url)

    if parsed.scheme == "" and parsed.netloc == "" and next_url.startswith("/"):
        return next_url
    return ""

# Admin logIn Validation
def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):

        # If token doesn't match current run, forces re-login
        if session.get("user_id") and session.get("app_run_token") != APP_RUN_TOKEN:
            session.clear()
            return redirect(url_for("login", next=request.path))

        # Only admins can access admin routes
        if session.get("role") != "admin":
            session.clear()
            return redirect(url_for("login", next=request.path))

        return view_func(*args, **kwargs)
    return wrapper



# LoinIn Routes
@app.route("/login", methods=["GET", "POST"])
def login():
    next_url = safe_local_path(request.args.get("next", ""))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = get_user_by_username(username)

        #Checks if user and password is correct
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["app_run_token"] = APP_RUN_TOKEN

            # Redirect based on role
            if user["role"] == "admin":
                return redirect(next_url or url_for("admin_dashboard"))
            return redirect(next_url or url_for("home"))

        flash("Invalid username or password.", "error")
        return redirect(url_for("login", next=next_url))

    return render_template("login.html", active_page="", next_url=next_url)


# Logout Route
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))



# Redirection Routes Web Pages
@app.route("/")
def home():
    return render_template("index.html", active_page="home")
@app.route("/report")
def report_form():
    return render_template("report.html", active_page="report")
@app.route("/resources")
def resources():
    return render_template("resources.html", active_page="resources")
@app.route("/info")
def info():
    return render_template("info.html", active_page="info")
@app.route("/confirm/<report_code>")
def confirm(report_code):
    anonymous = session.pop("last_report_anonymous", 0)
    return render_template(
        "confirm.html",
        report_code=report_code,
        anonymous=anonymous,
        active_page=""
    )
@app.route("/report-hub")
def report_hub():
    return render_template("report_hub.html", active_page="report")



# Tracking Report Status Route
@app.route("/track", methods=["GET", "POST"])
def track():
    report = None
    report_code = ""
    updates = []

    # when users submits a reference ID
    if request.method == "POST":
        report_code = request.form.get("report_code", "").strip()

        db = get_db()
        cursor = db.cursor(dictionary=True)

        # Get report with matching Unique Referce ID
        cursor.execute(
            "SELECT report_code, status, created_at FROM reports WHERE report_code=%s",
            (report_code,)
        )
        report = cursor.fetchone()

        # Get the ststus of the report
        display_map = {
            "Sent": "Sent",
            "Received": "Received",
            "In Progress": "In Progress",
            "Resolved": "Resolved",
            "Closed": "Closed"
        }

        # Displays the report
        # if no reprot found
        if not report:
            flash("Report not found. Please check the reference ID.", "error")

        # if report exists
        else:
            #Display report status
            report["display_status"] = display_map.get(report["status"], report["status"])

            # get all posted message (by admin) for the report
            cursor.execute(
                "SELECT message, created_at FROM report_updates WHERE report_code=%s ORDER BY created_at ASC",
                (report_code,)
            )
            updates = cursor.fetchall()

        cursor.close()
        db.close()


    return render_template(
        "track.html",
        report=report,
        report_code=report_code,
        updates=updates
    )



# Submit Report Route
@app.route("/submit-report", methods=["POST"])
def submit_report():
    # cheks if optional anonymous selected or not
    anonymous = 1 if request.form.get("anonymous") == "on" else 0

    report_for = request.form.get("report_for", "").strip()

    reporter_name = request.form.get("reporter_name", "").strip()
    reporter_email = request.form.get("reporter_email", "").strip()
    reporter_phone = request.form.get("reporter_phone", "").strip()

    connection = request.form.get("connection", "").strip()
    connection_other = request.form.get("connection_other", "").strip()

    subject_connection = request.form.get("subject_connection", "").strip()
    subject_connection_other = request.form.get("subject_connection_other", "").strip()

    incident_date = request.form.get("incident_date", "").strip()  # required
    incident_time = request.form.get("incident_time", "").strip()  # optional

    location = request.form.get("location", "").strip()
    location_other = request.form.get("location_other", "").strip()

    category = request.form.get("category", "").strip()
    category_other = request.form.get("category_other", "").strip()

    priority = request.form.get("priority", "").strip()
    description = request.form.get("description", "").strip()


    # If report is anonymous
    if anonymous == 1:
        # Do not store contact info
        reporter_name = ""
        reporter_email = ""
        reporter_phone = ""

    # if report is no anonymous
    else:
        # Makes sure required fields are filled
        if not reporter_email:
            flash("Email is required if you are not submitting anonymously.", "error")
            return render_template("report.html", active_page="report")

    if report_for not in {"Myself", "Behalf of someone"}:
        flash("Please choose who the report is for.", "error")
        return render_template("report.html", active_page="report")

    if not all([incident_date, location, category, priority]) or not description:
        flash("Please fill in all required fields.", "error")
        return render_template("report.html", active_page="report")

    if not connection:
        flash("Please select your connection to the university.", "error")
        return render_template("report.html", active_page="report")

    if connection == "Other" and not connection_other:
        flash("Please specify your connection.", "error")
        return render_template("report.html", active_page="report")

    if report_for == "Behalf of someone":
        if not subject_connection:
            flash("Please select their connection to the university.", "error")
            return render_template("report.html", active_page="report")
        if subject_connection == "Other" and not subject_connection_other:
            flash("Please specify their connection.", "error")
            return render_template("report.html", active_page="report")
    else:
        subject_connection = ""
        subject_connection_other = ""


    # Insert Report into database
    # creates a temporary code
    temp_code = f"TEMP-{uuid.uuid4().hex[:12]}"
    evidence_path = ""

    db = get_db()
    cursor = db.cursor()

    # SQL Query to insert report into reports table
    sql = """
    INSERT INTO reports
    (report_code, anonymous, report_for,
     reporter_name, reporter_email, reporter_phone,
     connection, connection_other,
     subject_connection, subject_connection_other,
     incident_date, incident_time,
     location, location_other,
     category, category_other,
     priority, description, evidence_path,
     status, created_at)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    # Executes the query with the data submitted in the form
    cursor.execute(
        sql,
        (
            temp_code,
            anonymous,
            report_for,

            reporter_name,
            reporter_email,
            reporter_phone,

            connection,
            connection_other,
            subject_connection,
            subject_connection_other,
            incident_date,
            incident_time or None,
            location,
            location_other,
            category,
            category_other,
            priority,
            description,
            evidence_path,
            "Sent",
            datetime.now(),
        ),
    )
    db.commit()

    # Generates the new Unique reference ID
    row_id = cursor.lastrowid
    report_code = format_report_code(row_id)

    # Update report code even if no file is uploaded
    cursor.execute(
        "UPDATE reports SET report_code=%s WHERE id=%s",
        (report_code, row_id),
    )
    db.commit()

    # Evidence Upload
    file = request.files.get("evidence")
    if file and file.filename:

        # checks the file type
        if not allowed_file(file.filename):
            flash("Invalid file type. Allowed: png, jpg, jpeg, gif", "error")
            cursor.close()
            db.close()
            return render_template("report.html", active_page="report")

        # change file name before saving
        safe_name = secure_filename(file.filename)
        filename = f"{report_code}_{safe_name}"
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(file_path)
        evidence_path = f"uploads/{filename}"

        # updates the report's evidence file path
        cursor.execute(
            "UPDATE reports SET evidence_path=%s WHERE id=%s",
            (evidence_path, row_id),
        )
        db.commit()



    # Send confirmation email only for non-anonymous reports
    if anonymous == 0 and reporter_email:

        subject = f"Green Dot Report Confirmation – {report_code}"

        body = f"""Hello {reporter_name},

Your report has been submitted successfully to the Green Dot Wellbeing Reporting System.

Reference ID: {report_code}

Please keep this reference ID safe, as you may need it to track your report.

If there are any updates regarding your report, the wellbeing team may contact you using the details you provided.
"""
        # Try and except block to prevent system failure
        # due to SMTP server failure
        try:
            send_email(reporter_email, subject, body)
        except Exception as e:
            print("Submission email failed:", e)

    cursor.close()
    db.close()

    # Temporarily save anonymous value for confirmation page
    session["last_report_anonymous"] = anonymous

    # Redirect user to confirmation Page
    return redirect(url_for("confirm", report_code=report_code))



# Admin Dashbaord Route
@app.route("/admin")
@admin_required
def admin_dashboard():
    
    # Get Filter value from the URL
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    priority = request.args.get("priority", "").strip()
    category = request.args.get("category", "").strip()

    # connects to database
    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Builds the Query to get reports based on filter
    query = "SELECT * FROM reports WHERE 1=1"
    params = []

    if search:
        query += " AND report_code LIKE %s"
        params.append(f"%{search}%")

    if status:
        query += " AND status = %s"
        params.append(status)

    if priority:
        query += " AND priority = %s"
        params.append(priority)

    if category:
        query += " AND category = %s"
        params.append(category)

    # shows latest report first
    query += " ORDER BY created_at DESC"

    # executes the query
    cursor.execute(query, params)
    reports = cursor.fetchall()

    # displays report analytics on status of reports
    cursor.execute("SELECT COUNT(*) AS total FROM reports")
    total_reports = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) AS sent FROM reports WHERE status='Sent'")
    sent_reports = cursor.fetchone()["sent"]

    cursor.execute("SELECT COUNT(*) AS received FROM reports WHERE status='Received'")
    received_reports = cursor.fetchone()["received"]

    cursor.execute("SELECT COUNT(*) AS in_progress FROM reports WHERE status='In Progress'")
    in_progress_reports = cursor.fetchone()["in_progress"]

    cursor.execute("SELECT COUNT(*) AS resolved FROM reports WHERE status='Resolved'")
    resolved_reports = cursor.fetchone()["resolved"]

    cursor.execute("SELECT COUNT(*) AS closed FROM reports WHERE status='Closed'")
    closed_reports = cursor.fetchone()["closed"]

    cursor.close()
    db.close()

    return render_template(
        "admin.html",
        reports=reports,
        search=search,
        status_filter=status,
        priority_filter=priority,
        category_filter=category,
        total_reports=total_reports,
        sent_reports=sent_reports,
        received_reports=received_reports,
        in_progress_reports=in_progress_reports,
        resolved_reports=resolved_reports,
        closed_reports=closed_reports,
    )



# View Report (via Admin Dashabord) Route
@app.route("/admin/view/<report_code>")
@admin_required
def view_report(report_code):
    next_url = safe_local_path(request.args.get("next", ""))

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # get report details form report table
    cursor.execute("SELECT * FROM reports WHERE report_code=%s", (report_code,))
    report = cursor.fetchone()

    # If report status is sent
    # Automatically changes report status to received
    if report and report["status"] == "Sent":
        update_cursor = db.cursor()
        update_cursor.execute(
            "UPDATE reports SET status=%s WHERE report_code=%s",
            ("Received", report_code)
        )
        db.commit()
        update_cursor.close()

        # re-load page to show updated report status
        cursor.execute("SELECT * FROM reports WHERE report_code=%s", (report_code,))
        report = cursor.fetchone()

    # Loads all posted message by admin for the report.
    cursor.execute(
        "SELECT message, created_by, created_at FROM report_updates WHERE report_code=%s ORDER BY created_at ASC",
        (report_code,)
    )
    updates = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template("view_report.html", report=report, updates=updates, next_url=next_url)



# Route/Function for update report stauts
@app.route("/admin/update-status/<report_code>", methods=["POST"])
@admin_required
def update_status(report_code):
    new_status = request.form.get("status")
    next_url = safe_local_path(request.form.get("next", ""))

    db = get_db()
    cursor = db.cursor()

    # updates the report status in the database
    cursor.execute(
        "UPDATE reports SET status=%s WHERE report_code=%s",
        (new_status, report_code),
    )
    db.commit()
    cursor.close()
    db.close()

    # Redirects to previous or admin dashboard after update
    return redirect(next_url or url_for("admin_dashboard"))



# Admin Post Update on Report
@app.route("/admin/add-update/<report_code>", methods=["POST"])
@admin_required
def add_update(report_code):
    message = request.form.get("message", "").strip()
    next_url = safe_local_path(request.form.get("next", ""))

    # If message box is left empty
    if not message:
        return redirect(next_url or url_for("view_report", report_code=report_code))

    db = get_db()
    cursor = db.cursor()

    # else save the new posted message into the report_updates table
    cursor.execute(
        """
        INSERT INTO report_updates (report_code, message, created_by, created_at)
        VALUES (%s, %s, %s, %s)
        """,
        (report_code, message, session.get("username"), datetime.now())
    )
    db.commit()

    # Sends email notification if report is non-anonymous
    cursor.execute(
        "SELECT anonymous, reporter_email, reporter_name FROM reports WHERE report_code=%s",
        (report_code,)
    )
    report_data = cursor.fetchone()

    if report_data and report_data[0] == 0 and report_data[1]:
        reporter_name = report_data[2] if report_data[2] else "User"
        subject = f"Update on Your Green Dot Report – {report_code}"
        body = f"""Hello {reporter_name},

New information has been added to your report.

Reference ID: {report_code}

Please use your reference ID to check the latest update on your report via the report tracking page.
"""
        try:
            send_email(report_data[1], subject, body)
        except Exception as e:
            print("Update email failed:", e)

    cursor.close()
    db.close()

    return redirect(next_url or url_for("view_report", report_code=report_code))



# Send notification to email
def send_email(to_email: str, subject: str, body: str):
    msg = EmailMessage()
    msg["subject"] = subject
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:
        smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        smtp.send_message(msg)



if __name__ == "__main__":
    app.run(debug=True)