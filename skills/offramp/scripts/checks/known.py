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


#: Platform couplings: URLs that stop working, or keep pointing at the platform, once
#: the application leaves it. Each rule was confirmed against the recall corpus --
#: RFC-0002 forbids adding one from memory of how a platform worked at some point.
#: (rule id, URL regex, severity, title, what breaks, what to do)
PLATFORM_COUPLINGS = (
    ("emergent-preview-url",
     r"https?://[a-z0-9-]+\.preview(?:\.static)?\.emergentagent\.com",
     "medium", "The app calls its own Emergent preview deployment",
     "A URL on `*.preview.emergentagent.com` is written into the app -- often as the "
     "backend URL the frontend is built with. It keeps pointing at the platform after a "
     "move, and stops answering when the preview is shut down.",
     "Read the URL from the environment, and set it to the new deployment's address."),
    ("emergent-managed-auth",
     r"https?://(?:auth|demobackend)\.emergentagent\.com",
     "medium", "Sign-in runs through Emergent's managed login",
     "The login flow redirects to, or validates sessions against, Emergent's own auth "
     "service. Users cannot sign in once the app no longer runs on Emergent.",
     "Replace it with an auth provider you control before moving, and plan how existing "
     "users will sign in afterwards."),
    ("emergent-integrations-proxy",
     r"https?://integrations\.emergentagent\.com",
     "medium", "Calls go through Emergent's integrations proxy",
     "Storage or model calls are sent to Emergent's integrations proxy with a platform "
     "key, rather than to the provider directly.",
     "Call the provider directly with your own account and key, from server-side code."),
    ("emergent-editor-scripts",
     r"https?://assets\.emergent\.sh/(?:scripts|npm)/",
     "low", "The platform's editor scripts load in production",
     "`index.html` or package.json pulls Emergent's editor or visual-edit scripts from "
     "the platform's CDN. Visitors download them, and a build that installs from that "
     "URL fails if it moves.",
     "Remove the script tags and the dependency once you no longer edit in the platform."),
    ("emergent-hosted-assets",
     r"https?://(?:static\.prod-images\.emergentagent\.com|"
     r"[a-z0-9-]+\.emergentagent\.com/job_[^/\s]+/artifacts)",
     "low", "Images are served from Emergent's storage",
     "Images and files are linked from the platform's storage, which you do not control.",
     "Copy the files into your own storage or the repository, and update the links."),
    ("lovable-ai-gateway",
     r"https?://ai[.-]gateway\.lovable\.dev",
     "medium", "AI calls go through Lovable's AI gateway",
     "Model calls are sent to Lovable's AI gateway with a Lovable key. They stop working "
     "outside Lovable, and usage is billed through the platform.",
     "Call the model provider directly with your own key, from a server-side function."),
    ("lovable-connector-gateway",
     r"https?://connector-gateway\.lovable\.dev",
     "medium", "Integrations go through Lovable's connector gateway",
     "Email, messaging or other integrations are called through Lovable's connector "
     "gateway rather than the provider's own API.",
     "Call each provider's API directly with your own credentials."),
    ("lovable-editor-script",
     r"https?://cdn\.gpteng\.co/",
     "low", "Lovable's editor script loads in production",
     "`index.html` loads `gptengineer.js` from Lovable's CDN. Every visitor downloads a "
     "third-party script that the app does not need to run.",
     "Remove the script tag from `index.html` once you no longer edit in Lovable."),
    ("lovable-app-url",
     r"https?://[a-z0-9-]+\.(?:lovable\.app|lovableproject\.com|sandbox\.lovable\.dev)",
     "medium", "A Lovable deployment URL is written into the app",
     "Redirects, links or allowed origins point at a `*.lovable.app` address, which keeps "
     "pointing at the platform after a move.",
     "Read the site URL from the environment, and set it to your own domain."),
    ("lovable-default-social-image",
     r"https?://lovable\.dev/opengraph-image",
     "low", "Link previews use Lovable's default image",
     "The page's social preview image is Lovable's default, so shared links show "
     "Lovable's branding rather than yours.",
     "Replace the `og:image` and `twitter:image` tags with your own image."),
)
