# JobLens

JobLens is a website that helps you keep track of your job search. It reads the job emails in your Gmail and sorts them for you: applications, rejections, interviews, tests, offers and more. You see everything in one simple dashboard.

Built by Kiran Chowdary Paritala.

---

## Why I built this

When you apply to many jobs, the replies get lost between promotions and newsletters. You forget which company replied, who rejected you, and which test is due. I built JobLens to sort all of that in one place, so I do not have to search my inbox again and again.

---

## What it does

1. You sign in with Google, or with an email and password.
2. You pick a day on a calendar.
3. JobLens reads the mails from that day and sorts each one into a group.
4. You see the result on one page, and the numbers on your dashboard.

**The groups**

- Applications
- Rejections
- Interviews
- Assessments (tests, questionnaires)
- Offers
- Follow-ups you sent
- Recruiter outreach
- General (promotions and everything else)

For each job mail it shows the company, the job title, a short summary and a deadline, when the mail gives one.

**Sender check.** Fake job mails are common, so every job mail gets a small label:

- *Direct from company*: the mail came from the company's own address.
- *Via recruiting platform or agency*: the mail came from a hiring system or a staffing company, such as Workday, Greenhouse or Ashby.
- *Could not verify sender*: the mail failed Gmail's own checks or looks odd. Be careful with these.

**Dashboard.** It shows counts for today, the last 7 days, the last 30 days and the total, plus a short weekly summary.

---

## How it sorts the mails

1. **Key phrases first.** A list of common phrases, such as "thank you for applying" or "we will not be moving forward", decides most mails. These phrases are in one file, so anyone can read and change them.
2. **AI as a backup.** If no phrase matches, a small AI model (through Groq) makes the decision. It receives only the sender, the subject, a short preview and a few matching sentences. It never receives the full mail.
3. **Details.** The company, job title and deadline are then taken from the mail.

The AI can make mistakes. Please check important mails yourself.

---

## Privacy and safety

- JobLens asks Google for **read-only** access to Gmail. It cannot send, delete or change your mail.
- It saves only the main details of each job mail (category, company, job title, dates, a short summary and preview), not the full mail.
- You can delete your account from inside the site. This removes your saved data.
- Passwords are stored as hashes, never as plain text.
- A password must start with a capital letter, be at least 7 characters long, and have one special character.
- After 5 wrong passwords, the account is locked. The user gets a reset link by email. The link works once and expires after 30 minutes.

The site also has a Privacy page and a Terms page.

---

## Built with

- Python and Flask
- SQLite for the database on a local computer
- Flask-Login for sign-in, Authlib for Google sign-in
- Gmail API for reading mail
- Groq for the AI backup step
- Bootstrap CSS for the look

---

## Project folders

```
jobLens/
  run.py                 starts the site
  requirements.txt       the Python packages
  .env.example           the settings you need (copy to .env)
  app/
    __init__.py          sets up the site
    models.py            the database tables
    routes/              the web pages and their logic
      auth.py              sign up, login, password reset
      google_auth.py       sign in with Google
      emails.py            calendar, scanning, day results
      main.py              home page, dashboard, privacy, terms
    agents/              the sorting steps
      phrases.py           the list of key phrases
      rules.py             matches mails against the phrases
      classifier.py        the AI backup and the Groq connection
      extractor.py         finds company, job title, deadline and sender check
      summarizer.py        writes the weekly summary
    services/
      gmail_service.py     reads Gmail and sends reset emails
    templates/           the HTML pages
    static/              images
```

---

## Run it on your computer

You need Python 3 (I tested on 3.13 and 3.14) and a Google account.

**1. Get the code**

```
git clone https://github.com/KiranParitala95/joblens.git
cd joblens
```

**2. Install the packages**

```
python -m venv venv
source venv/Scripts/activate
pip install -r requirements.txt
```

On Mac or Linux, use `source venv/bin/activate` for the second line.

**3. Set up Google sign-in**

1. Open the Google Cloud Console and create a project.
2. Turn on the **Gmail API**.
3. Create an **OAuth client** (type: Web application).
4. Add this redirect address: `http://127.0.0.1:5000/auth/google/callback`
5. While the app is in testing mode, add your own Gmail address as a **test user**.

**4. Add your settings**

Copy `.env.example` to `.env` and fill in the values:

| Setting | What it is |
|---|---|
| `SECRET_KEY` | A long random text that signs logins. Make one with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` | From your OAuth client |
| `GROQ_API_KEYS` | One or more Groq keys on one line, separated by commas |
| `MAIL_USERNAME` and `MAIL_APP_PASSWORD` | A Gmail address and its app password, used to send reset emails |

Never share your `.env` file and never upload it to GitHub. It is already in `.gitignore`.

**5. Start the site**

```
python run.py
```

Open `http://127.0.0.1:5000/` in your browser.

---

## Things to know

- The free Groq plan has a limit on how much text it reads each minute. Using several keys from different accounts spreads the load. A busy day with 60 or more mails can still take a minute or two.
- JobLens can only sort mails that arrive in Gmail. If a company sends no confirmation mail, nothing shows up for that application.
- While the Google app is in testing mode, only people added as test users can sign in, and Google asks them to connect Gmail again about every 7 days.
- The site runs on a local computer for now. I plan to put it online on AWS.

---

## Contact

Kiran Chowdary Paritala, Data Engineer

- LinkedIn: https://www.linkedin.com/in/kiran-chowdary95
- GitHub: https://github.com/KiranParitala95
- Website: https://www.kiranparitala.dev
- Email: paritalakiran123@gmail.com
