"""NumberCheck AI - understand who might be calling from an unknown number.

Design rules:
* Local data (phonenumbers library) is shown as "Local analysis".
* Carrier / line type / caller name come ONLY from a configured provider
  (Twilio Lookup v2) and are labelled "Third-party".
* Gemini only explains the facts it is given and is labelled "AI-generated".
* The app never claims to know a private person's identity.
"""

from __future__ import annotations

import html
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

import phonenumbers
import requests
import streamlit as st
from phonenumbers import NumberParseException, PhoneNumberType, geocoder
from phonenumbers import timezone as pn_timezone

try:  # optional, only used for nicer country names
    import pycountry
except ImportError:  # pragma: no cover
    pycountry = None

# --------------------------------------------------------------------------- #
# Page setup (must be the first Streamlit call)
# --------------------------------------------------------------------------- #
st.set_page_config(
    page_title="NumberCheck AI",
    page_icon="📞",
    layout="centered",
    initial_sidebar_state="collapsed",
)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
REQUEST_TIMEOUT = 10  # seconds
COOLDOWN_SECONDS = 2.0
HISTORY_LIMIT = 10

CSS = """
<style>
:root {
  --navy: #0a1628; --navy-2: #12233f; --card: #14284a; --border: #24427a;
  --blue: #3b82f6; --blue-2: #60a5fa; --text: #e8eefc; --muted: #a9b8d6;
}
.stApp { background: var(--navy); color: var(--text); }
.block-container { padding-top: 1.5rem; max-width: 820px; }
h1, h2, h3, p, label, span, div { color: var(--text); }
.brand { display: flex; align-items: center; gap: .75rem; margin-bottom: .25rem; }
.brand-logo { width: 44px; height: 44px; border-radius: 12px;
  background: linear-gradient(135deg, #2563eb, #60a5fa);
  display: flex; align-items: center; justify-content: center; font-size: 1.5rem; }
.brand-name { font-size: 1.7rem; font-weight: 700; letter-spacing: .2px; }
.brand-name span { color: var(--blue-2); }
.tagline { color: var(--muted); margin-bottom: 1.2rem; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 16px;
  padding: 1rem 1.1rem; margin-bottom: .9rem; min-height: 112px; }
.card .label { color: var(--muted); font-size: .8rem; text-transform: uppercase;
  letter-spacing: .6px; margin-bottom: .25rem; }
.card .value { font-size: 1.15rem; font-weight: 600; word-break: break-word; }
.card .note { color: var(--muted); font-size: .82rem; margin-top: .35rem; }
.badge { display: inline-block; font-size: .68rem; padding: .1rem .5rem; border-radius: 999px;
  margin-top: .45rem; border: 1px solid var(--border); color: var(--blue-2); }
.badge.local { color: #86efac; border-color: #166534; }
.badge.third { color: #fcd34d; border-color: #854d0e; }
.badge.none { color: var(--muted); }
.risk-low { color: #86efac; } .risk-sus { color: #fcd34d; } .risk-rep { color: #fca5a5; }
.ai-box { background: var(--navy-2); border: 1px solid var(--border);
  border-left: 4px solid var(--blue); border-radius: 12px; padding: 1rem 1.1rem; }
.disclaimer { color: var(--muted); font-size: .82rem; border-top: 1px solid var(--border);
  margin-top: 1.5rem; padding-top: .8rem; }
div[data-testid="stFormSubmitButton"] button {
  background: var(--blue); color: white; border: 0; border-radius: 12px;
  font-weight: 700; padding: .65rem 1rem; width: 100%; }
div[data-testid="stFormSubmitButton"] button:hover { background: #2563eb; color: white; }
div[data-testid="stForm"] { background: var(--navy-2); border: 1px solid var(--border);
  border-radius: 16px; }
</style>
"""

LOCAL_TYPE_LABELS = {
    PhoneNumberType.FIXED_LINE: "Landline (fixed line)",
    PhoneNumberType.MOBILE: "Mobile",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "Mobile or landline (cannot tell)",
    PhoneNumberType.TOLL_FREE: "Toll-free",
    PhoneNumberType.PREMIUM_RATE: "Premium rate",
    PhoneNumberType.SHARED_COST: "Shared cost",
    PhoneNumberType.VOIP: "VoIP",
    PhoneNumberType.PERSONAL_NUMBER: "Personal number",
    PhoneNumberType.PAGER: "Pager",
    PhoneNumberType.UAN: "Universal access number",
    PhoneNumberType.VOICEMAIL: "Voicemail",
    PhoneNumberType.UNKNOWN: "Unknown",
}

TWILIO_TYPE_LABELS = {
    "mobile": "Mobile",
    "landline": "Landline",
    "fixedVoip": "VoIP (fixed)",
    "nonFixedVoip": "VoIP (non-fixed)",
    "personal": "Personal number",
    "tollFree": "Toll-free",
    "premium": "Premium rate",
    "sharedCost": "Shared cost",
    "uan": "Universal access number",
    "voicemail": "Voicemail",
    "pager": "Pager",
    "unknown": "Unknown",
}


# --------------------------------------------------------------------------- #
# Configuration helpers
# --------------------------------------------------------------------------- #
def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read from Streamlit secrets first, then environment variables."""
    try:
        value = st.secrets[name]
        if value not in (None, ""):
            return str(value)
    except Exception:  # missing secrets file / key
        pass
    return os.environ.get(name, default) or default


def get_bool_secret(name: str) -> bool:
    return str(get_secret(name, "false")).strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# Data containers
# --------------------------------------------------------------------------- #
@dataclass
class LocalResult:
    raw_input: str
    valid: bool = False
    possible: bool = False
    error: Optional[str] = None
    e164: Optional[str] = None
    international: Optional[str] = None
    national: Optional[str] = None
    region_code: Optional[str] = None
    country_name: Optional[str] = None
    location: Optional[str] = None
    timezones: list[str] = field(default_factory=list)
    numbering_type: Optional[str] = None


@dataclass
class ProviderResult:
    configured: bool = False
    ok: bool = False
    provider: str = "Twilio Lookup"
    error: Optional[str] = None
    carrier: Optional[str] = None
    line_type: Optional[str] = None
    caller_name: Optional[str] = None
    caller_type: Optional[str] = None
    risk_score: Optional[int] = None


@dataclass
class SpamAssessment:
    level: str  # "Low Risk" | "Potentially Suspicious" | "Reported Spam" | "Not assessed"
    evidence: list[str]
    source: str


# --------------------------------------------------------------------------- #
# Country helpers
# --------------------------------------------------------------------------- #
def flag_emoji(region: str) -> str:
    if len(region) != 2 or not region.isalpha():
        return "🌐"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in region.upper())


def country_name(region: str) -> str:
    if pycountry is not None:
        match = pycountry.countries.get(alpha_2=region)
        if match is not None:
            return getattr(match, "common_name", None) or match.name
    return region


@st.cache_data(show_spinner=False)
def build_country_options() -> dict[str, Optional[str]]:
    """Map display label -> region code (None means 'auto-detect from +')."""
    items = []
    for region in phonenumbers.SUPPORTED_REGIONS:
        code = phonenumbers.country_code_for_region(region)
        label = f"{flag_emoji(region)} {country_name(region)} (+{code})"
        items.append((country_name(region), label, region))
    items.sort(key=lambda x: x[0])
    options: dict[str, Optional[str]] = {"🌐 Auto-detect (number starts with +)": None}
    for _, label, region in items:
        options[label] = region
    return options


# --------------------------------------------------------------------------- #
# Step 1-2: local validation and details
# --------------------------------------------------------------------------- #
def analyze_locally(raw: str, region: Optional[str]) -> LocalResult:
    result = LocalResult(raw_input=raw)
    cleaned = raw.strip()
    if not cleaned:
        result.error = "Please enter a phone number."
        return result
    if len(cleaned) > 40:
        result.error = "That input is too long to be a phone number."
        return result

    default_region = None if cleaned.startswith("+") else region
    if default_region is None and not cleaned.startswith("+"):
        result.error = (
            "Select a country, or start the number with + and the country code "
            "(for example +44 20 7946 0958)."
        )
        return result

    try:
        parsed = phonenumbers.parse(cleaned, default_region)
    except NumberParseException as exc:
        messages = {
            NumberParseException.INVALID_COUNTRY_CODE: "The country code is not valid.",
            NumberParseException.NOT_A_NUMBER: "That does not look like a phone number.",
            NumberParseException.TOO_SHORT_AFTER_IDD: "The number is too short.",
            NumberParseException.TOO_SHORT_NSN: "The number is too short.",
            NumberParseException.TOO_LONG: "The number is too long.",
        }
        result.error = messages.get(exc.error_type, "The number could not be parsed.")
        return result

    result.possible = phonenumbers.is_possible_number(parsed)
    result.valid = phonenumbers.is_valid_number(parsed)
    result.e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    result.international = phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
    )
    result.national = phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.NATIONAL
    )

    if not result.valid:
        reason = phonenumbers.is_possible_number_with_reason(parsed)
        reasons = {
            phonenumbers.ValidationResult.TOO_SHORT: "The number is too short for this country.",
            phonenumbers.ValidationResult.TOO_LONG: "The number is too long for this country.",
            phonenumbers.ValidationResult.INVALID_LENGTH: "The number has an invalid length.",
            phonenumbers.ValidationResult.INVALID_COUNTRY_CODE: "The country code is invalid.",
        }
        result.error = reasons.get(
            reason, "The number does not match a valid numbering pattern for that country."
        )
        return result

    result.region_code = phonenumbers.region_code_for_number(parsed)
    if result.region_code:
        result.country_name = country_name(result.region_code)
    result.location = geocoder.description_for_number(parsed, "en") or None
    result.timezones = [
        tz for tz in pn_timezone.time_zones_for_number(parsed) if tz != "Etc/Unknown"
    ]
    result.numbering_type = LOCAL_TYPE_LABELS.get(
        phonenumbers.number_type(parsed), "Unknown"
    )
    return result


# --------------------------------------------------------------------------- #
# Step 3: optional provider lookup (Twilio Lookup v2)
# --------------------------------------------------------------------------- #
def lookup_provider(e164: str) -> ProviderResult:
    sid = get_secret("TWILIO_ACCOUNT_SID")
    token = get_secret("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        return ProviderResult(configured=False)

    fields = ["line_type_intelligence", "sms_pumping_risk"]
    if get_bool_secret("TWILIO_ENABLE_CALLER_NAME"):
        fields.append("caller_name")  # paid add-on, US numbers only

    url = f"https://lookups.twilio.com/v2/PhoneNumbers/{quote(e164, safe='')}"
    out = ProviderResult(configured=True)
    try:
        resp = requests.get(
            url,
            params={"Fields": ",".join(fields)},
            auth=(sid, token),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.Timeout:
        out.error = "The lookup provider timed out. Please try again."
        return out
    except requests.RequestException:
        out.error = "Could not reach the lookup provider."
        return out

    if resp.status_code == 401:
        out.error = "Lookup provider rejected the credentials. Check your Twilio SID and token."
        return out
    if resp.status_code == 404:
        out.error = "The lookup provider has no data for this number."
        return out
    if resp.status_code == 429:
        out.error = "Lookup provider rate limit reached. Try again later."
        return out
    if resp.status_code >= 400:
        out.error = f"Lookup provider returned an error (HTTP {resp.status_code})."
        return out

    try:
        data: dict[str, Any] = resp.json()
    except ValueError:
        out.error = "Lookup provider returned an unreadable response."
        return out

    out.ok = True
    lti = data.get("line_type_intelligence") or {}
    if lti and not lti.get("error_code"):
        out.carrier = lti.get("carrier_name") or None
        raw_type = lti.get("type")
        if raw_type:
            out.line_type = TWILIO_TYPE_LABELS.get(raw_type, raw_type)

    cn = data.get("caller_name") or {}
    if cn and not cn.get("error_code"):
        out.caller_name = cn.get("caller_name") or None
        out.caller_type = cn.get("caller_type") or None

    spr = data.get("sms_pumping_risk") or {}
    score = spr.get("sms_pumping_risk_score")
    if isinstance(score, int) and not spr.get("error_code"):
        out.risk_score = score
    return out


# --------------------------------------------------------------------------- #
# Step 4: spam / risk assessment (evidence-based only)
# --------------------------------------------------------------------------- #
def assess_risk(local: LocalResult, provider: ProviderResult) -> SpamAssessment:
    """Never labels a number as spam without supporting evidence.

    "Reported Spam" requires an actual spam-report source. None is built in;
    add one by returning report counts here (see README, 'Extending').
    """
    if not (provider.configured and provider.ok and provider.risk_score is not None):
        return SpamAssessment(
            level="Not assessed",
            evidence=["No reputation or spam-report source is configured."],
            source="none",
        )

    score = provider.risk_score
    if score >= 75:
        return SpamAssessment(
            "Potentially Suspicious",
            [f"Twilio fraud-risk score is elevated ({score}/100).",
             "This is a fraud-risk signal, not a confirmed spam report."],
            "third-party",
        )
    return SpamAssessment(
        "Low Risk",
        [f"Twilio fraud-risk score is {score}/100 (no elevated signal).",
         "This is not a check against spam-report databases."],
        "third-party",
    )


# --------------------------------------------------------------------------- #
# Step 5: Gemini explanation
# --------------------------------------------------------------------------- #
SYSTEM_INSTRUCTION = (
    "You explain phone number lookup results to everyday users in plain, calm language. "
    "Use ONLY the facts provided in the JSON. Never invent a caller name, business, carrier, "
    "location, or spam history. If a field is missing or null, say it is unavailable. "
    "Never claim to know who owns the number. Never call a number a scam unless the facts "
    "include an actual spam report. Clearly separate what is verified from the numbering "
    "plan, what came from a third-party provider, and what is unknown. Keep it under 150 "
    "words and end with one practical safety tip."
)


def explain_with_gemini(facts: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Return (text, error)."""
    api_key = get_secret("GEMINI_API_KEY")
    if not api_key:
        return None, "Gemini is not configured (add GEMINI_API_KEY to your secrets)."
    try:
        import json

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=get_secret("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
            contents="Explain these lookup results:\n" + json.dumps(facts, indent=2),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.2,
                max_output_tokens=600,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            return None, "Gemini returned an empty response."
        return text, None
    except ImportError:
        return None, "The google-genai package is not installed."
    except Exception:  # never surface raw errors (could contain request details)
        return None, "The AI explanation is temporarily unavailable."


def build_facts(local: LocalResult, provider: ProviderResult, risk: SpamAssessment) -> dict[str, Any]:
    return {
        "number_format_valid": local.valid,
        "country": local.country_name,
        "region_or_area_from_numbering_plan": local.location,
        "timezones": local.timezones,
        "numbering_plan_type_not_portability_aware": local.numbering_type,
        "provider_configured": provider.configured,
        "provider_lookup_succeeded": provider.ok,
        "provider_carrier": provider.carrier,
        "provider_line_type": provider.line_type,
        "provider_caller_name": provider.caller_name,
        "risk_level": risk.level,
        "risk_evidence": risk.evidence,
    }


# --------------------------------------------------------------------------- #
# UI helpers
# --------------------------------------------------------------------------- #
def esc(value: Optional[str]) -> str:
    return html.escape(value) if value else ""


def card(label: str, value: Optional[str], source: str, note: str = "", value_class: str = "") -> str:
    badges = {
        "local": ('<span class="badge local">Local analysis</span>'),
        "third": ('<span class="badge third">Third-party data</span>'),
        "none": ('<span class="badge none">Unavailable</span>'),
    }
    shown = esc(value) if value else "Not available"
    note_html = f'<div class="note">{esc(note)}</div>' if note else ""
    return (
        f'<div class="card"><div class="label">{esc(label)}</div>'
        f'<div class="value {value_class}">{shown}</div>{note_html}'
        f'{badges[source if value else "none"]}</div>'
    )


def render_header() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="brand"><div class="brand-logo">📞</div>'
        '<div class="brand-name">NumberCheck <span>AI</span></div></div>'
        '<div class="tagline">Understand an unknown number: format, country, and '
        "what can be reliably known about it.</div>",
        unsafe_allow_html=True,
    )


def render_results(res: dict[str, Any]) -> None:
    local: LocalResult = res["local"]
    provider: ProviderResult = res["provider"]
    risk: SpamAssessment = res["risk"]

    if local.error:
        st.error(local.error)
        return

    st.success(f"✅ Valid number format: {local.international}")

    # Number type: prefer provider data, fall back to the numbering plan.
    if provider.ok and provider.line_type:
        type_val, type_src, type_note = provider.line_type, "third", "From lookup provider."
    else:
        type_val, type_src = local.numbering_type, "local"
        type_note = "Based on the numbering plan; ported numbers may differ."

    country_val = local.country_name
    country_note = local.location or ""
    if local.timezones:
        country_note = (country_note + " · " if country_note else "") + ", ".join(local.timezones[:2])

    risk_class = {
        "Low Risk": "risk-low",
        "Potentially Suspicious": "risk-sus",
        "Reported Spam": "risk-rep",
    }.get(risk.level, "")
    risk_src = "third" if risk.source == "third-party" else "none"

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
