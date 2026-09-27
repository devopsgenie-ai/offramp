"""Data the checks consult, kept in one place so it can be reviewed as data.

RFC-0002: "Platform domains and platform-coupled dependencies are data. They live in one
file, and each entry is confirmed against the recall corpus before a check depends on it."
The same discipline applies to the list of keys that are public by design: every entry
here is a false positive someone would otherwise be told to panic about.
"""

from __future__ import annotations

import re

#: Build-time variables whose value is meant to be shipped to the browser. Matched on the
#: variable name with the bundler prefix removed. Each is a client identifier or a key
#: whose safety comes from server-side rules (Supabase row-level security, Firebase
#: security rules, a referrer restriction), not from secrecy.
PUBLIC_BY_DESIGN = re.compile(
    r"(ANON_KEY|PUBLISHABLE_KEY|PUBLIC_KEY|FIREBASE_API_KEY|FIREBASE_.*|"
    r"GOOGLE_MAPS_API_KEY|MAPS_API_KEY|MAPBOX_.*TOKEN|KAKAO_.*KEY|RECAPTCHA_SITE_KEY|SITE_KEY|"
    r"POSTHOG_.*KEY|SENTRY_DSN|CLIENT_ID|MEASUREMENT_ID|APP_ID)$"
)

#: A build-time variable name that says it holds a secret. Narrower than the runtime
#: detector's SECRET_NAME_RE on purpose: in a bundle, a false alarm teaches the reader to
#: ignore the report.
SECRET_NAME = re.compile(r"(SECRET|PRIVATE|PASSWORD|PASSWD|SERVICE_ROLE|TOKEN|API_?KEY)")

#: Literal secret-key formats that are never meant for a browser. Matched on a token;
#: the token itself is never recorded.
SECRET_LITERAL_PREFIXES = (
    "sk_live_",        # Stripe secret key
    "rk_live_",        # Stripe restricted key
    "sb_secret_",      # Supabase secret API key
    "sk-ant-",         # Anthropic API key
    "sk-proj-",        # OpenAI project key
)
