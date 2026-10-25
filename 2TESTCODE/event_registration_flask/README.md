# CampusEvents — Flask starter

A browser-based Python/Flask starter for the Event Registration System described by your group.

## 1. Open the project
Extract the ZIP and open the `event_registration_flask` folder in VS Code.

## 2. Install packages (Windows PowerShell)
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```
If activation is blocked, run:
```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 3. Start the app
For the easiest first run, leave `DATABASE_URL` unset. The app uses SQLite and creates `instance/events.db` automatically.

Set a demo admin account in the same PowerShell terminal:
```powershell
$env:SECRET_KEY="replace-this-with-a-long-random-secret"
$env:ADMIN_EMAIL="admin@example.com"
$env:ADMIN_PASSWORD="ChangeThisAdminPassword123!"
python app.py
```
Open the local address printed in the terminal, usually `http://127.0.0.1:5000`.

The app seeds two sample events and creates the first admin account on first run.
Sign in using the email/password you set above. Then use **Manage accounts** to create professor and student accounts.

## 4. Use MySQL
Install/start MySQL Server and create a database:
```sql
CREATE DATABASE event_registration CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```
Then set the database URL before starting the app:
```powershell
$env:DATABASE_URL="mysql+pymysql://event_admin:YOUR_PASSWORD@localhost/event_registration"
python app.py
```
Create a MySQL user with only the permissions your project needs; avoid using root. URL-encode special characters in the password if needed.

## Included
- Landing page and separate role-based login paths for Admin, Professor, and Student
- Admin event create/edit/delete, status display, archive, expense breakdown, account creation
- Student event browsing/search, registration, printable registration receipt, waiting-list entry
- Professor roster limited to assigned sections and CSV export
- Password hashing, activity logs, basic warning logs
- Queue, dictionary/hash lookup, custom merge sort, and binary search in `services/dsa.py`
- SQLAlchemy persistence using SQLite by default or MySQL with `DATABASE_URL`

## Important limitations to finish before final submission
This is a starter implementation, not a production-ready application. The waiting list is recorded in FIFO order in the database, but automatic promotion of the next student after a cancellation is not implemented yet. A full cancellation workflow should dequeue/promote the first eligible student in a transaction. Attendance actions exist as routes but need a dedicated roster/UI for organizers. The dashboard refreshes status on page load rather than using push-based live updates. Warning logs are basic (failed logins and duplicate attempts) and cannot reliably detect every third-party program or script on a user's device. Add CSRF protection, stronger deployment settings, and test every role before real deployment.


## Password reset requests

Students and professors can use **Forgot your password? Request a reset** on their login page. The form asks for the account email, student number or professor/staff ID, and an optional reason. Requests appear in the Admin dashboard, where the administrator can approve or reject them.

For an approved request, the administrator sets a temporary password of at least 12 characters. The application stores only its password hash, never the readable password. Share the temporary password only through a verified, private channel and ask the account owner to change it after signing in. The administrator should manually verify professor/staff IDs because this project does not currently store a professor ID field.

The feature creates a new `password_reset_request` table automatically when the app starts. Existing users and their password hashes are preserved.

## Additional functions in the updated build

- Admins can edit and delete eligible student/professor accounts. Student accounts with registration or waiting-list records cannot be deleted, preserving their event records.
- Admins can edit and delete expense records.
- Admins can view an event's registration roster, search by student name/number or receipt code, and export the roster to CSV.
- Admins and professors can use the attendance desk to search registrations and record time-in/time-out. Professors are restricted to students in their assigned sections.
- Students confirm their name, student number, year level, section, and program on the event registration form.
- Students can cancel an un-attended registration. When a slot opens, the next waiting-list entry is promoted using the custom FIFO `WaitingQueue`.
- Signed-in users can change their password. New passwords must be at least 12 characters.
- CSRF protection is enabled for POST forms using Flask-WTF.

## Security and deployment notes

- Install dependencies with `pip install -r requirements.txt`.
- Set a unique, long `SECRET_KEY` environment variable before deployment. The built-in fallback is only for local development and must not be used for a public deployment.
- Set `FLASK_DEBUG=0` for deployment. Use a production WSGI server rather than Flask's development server.
- Set `COOKIE_SECURE=1` only when serving the app over HTTPS; this enables secure session cookies.
- The app currently uses `db.create_all()` for first-run table creation. For future schema changes on an existing database, use a migration tool such as Flask-Migrate rather than relying on `create_all()` to alter existing tables.

## DSA unit tests

After installing the project dependencies, run the built-in DSA tests from the project directory:

```bash
python -m unittest discover -s tests
```

These tests cover merge sort, exact-title binary search, the registration hash index, and FIFO queue behavior. They do not replace end-to-end tests of the Flask routes and database workflow.
