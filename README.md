# NumberCheck AI

Understand who might be calling from an unknown number. Validates the number,
detects country/region, optionally enriches it via Twilio Lookup, and uses
Google Gemini to explain the results in plain language.

**What it will not do:** identify a private person by name from a number alone,
scrape private databases, or label a number a scam without evidence.

Each result is tagged: **Local analysis**, **Third-party data**, or **AI-generated**.

## Features
- Validation and normalization (`phonenumbers`)
- Country, region, time zones
- Carrier, line type, caller name only from a configured provider
- Evidence-based risk label (Low Risk / Potentially Suspicious / Reported Spam / Not assessed)
- Gemini plain-language explanation that is told not to invent facts
- Optional in-session history with a clear button
- Responsive dark navy UI

## Project structure
```
numbercheck-ai/
├── app.py
├── requirements.txt
├── README.md
├── .gitignore
└── .streamlit/
    ├── secrets.toml.example
    └── config.toml
```

## Run locally
```bash
cd numbercheck-ai
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then edit it
streamlit run app.py
```
Open http://localhost:8501. Environment variables (`GEMINI_API_KEY`, etc.) also work.

## Configure Google Gemini
1. Go to https://aistudio.google.com/apikey and create an API key.
2. Put it in `.streamlit/secrets.toml` as `GEMINI_API_KEY = "..."`.
3. Optional: set `GEMINI_MODEL` (default `gemini-2.5-flash`).

Without a key, the app still works and shows a notice instead of the explanation.

## Configure optional phone lookup (Twilio Lookup v2)
1. Create a Twilio account and copy the Account SID and Auth Token from the console.
2. Add `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` to your secrets.
3. Optional: `TWILIO_ENABLE_CALLER_NAME = true` (paid add-on, US numbers only).

Lookups are billed per request by Twilio; check current pricing.
Without credentials, only local information is shown.

Notes on risk: the built-in signal is Twilio's SMS-pumping fraud score. It is
**not** a spam-report database, so the app never shows "Reported Spam" by itself.

## Push to GitHub
```bash
cd numbercheck-ai
git init
git add .
git status                      # confirm secrets.toml is NOT listed
git commit -m "Initial commit: NumberCheck AI"
git branch -M main
# Create an empty repo on github.com first, then:
git remote add origin https://github.com/<your-username>/numbercheck-ai.git
git push -u origin main
```
If a key was ever committed, revoke it immediately and create a new one.

## Deploy to Streamlit Community Cloud
1. Sign in at https://share.streamlit.io with GitHub and authorize access to your repo.
2. Click **Create app** → **Deploy a public app from GitHub**.
3. Choose the repository, branch `main`, and main file path `app.py`.
4. Open **Advanced settings** → **Secrets** and paste:
   ```toml
   GEMINI_API_KEY = "your-key"
   # TWILIO_ACCOUNT_SID = "..."
   # TWILIO_AUTH_TOKEN = "..."
   ```
5. (Optional) Pick a Python version (3.11 or 3.12 recommended) and click **Deploy**.
6. To change secrets later: app menu → **Settings** → **Secrets**.

## Troubleshooting
| Problem | Fix |
|---|---|
| `ModuleNotFoundError` on deploy | Confirm the package is in `requirements.txt` at the repo root, then reboot the app. |
| "Gemini is not configured" | Add `GEMINI_API_KEY` to Secrets (Cloud) or `secrets.toml` (local); reboot. |
| "AI explanation is temporarily unavailable" | Key invalid, quota exceeded, or model name wrong. Check the key and `GEMINI_MODEL` in AI Studio. |
| Twilio "rejected the credentials" | Re-copy SID and Auth Token; no extra spaces or quotes. |
| "No data for this number" | The provider could not resolve it; this is normal for some numbers. |
| App not found / wrong file | Main file path must be exactly `app.py`. |
| Secrets not loading locally | File must be `.streamlit/secrets.toml` and you must run `streamlit run` from the project root. |
| Dependency conflicts | Loosen version pins or choose another Python version in Advanced settings. |
| App asleep | Free apps hibernate after inactivity; click "Wake up". |

## Extending: spam-report sources
Add a licensed reputation API in `assess_risk()` in `app.py`. Return
`"Reported Spam"` only when the source gives actual report counts, and keep the
source name in the evidence text.

## Privacy
- No numbers are written to disk or a database. History lives in the Streamlit
  session only, is off by default, and can be cleared with one click.
- Number data is sent to Twilio (if configured) and Gemini (as lookup facts) to produce results.
- Secrets live only in Streamlit secrets or environment variables.

## Disclaimer
Results may be incomplete or inaccurate. Caller ID can be spoofed and numbers
can be ported or reassigned. Use responsibly and comply with applicable laws.
