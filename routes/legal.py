"""
Legal Pages - Public Routes (Apple App Review Compliant)

Exposes public, read-only HTML pages for:
- Privacy Policy
- Terms of Use (EULA)

These routes:
- Require NO authentication
- Require NO database access
- Return static HTML content
- Are Apple App Store Review compliant

Endpoints:
- GET /privacy
- GET /terms
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legal"])


# ============================================================
# PRIVACY POLICY
# ============================================================

PRIVACY_POLICY_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Privacy Policy – AeroLogix AI</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            line-height: 1.6;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            color: #333;
        }
        h1 {
            color: #1a1a1a;
            border-bottom: 2px solid #007AFF;
            padding-bottom: 10px;
        }
        p {
            margin-bottom: 16px;
        }
        .contact {
            margin-top: 30px;
            padding: 15px;
            background: #f5f5f7;
            border-radius: 8px;
        }
        .updated {
            color: #666;
            font-size: 14px;
            margin-top: 30px;
        }
    </style>
</head>
<body>
    <h1>Privacy Policy – AeroLogix AI</h1>
    
    <p>AeroLogix AI is an aviation maintenance assistance application designed for aircraft owners.</p>
    
    <p>The application collects only information explicitly provided by the user, including aircraft details and maintenance records uploaded for analysis.</p>
    
    <p>Data is used solely to provide maintenance tracking, document analysis, and informational insights within the application.</p>
    
    <p>AeroLogix AI does not sell, rent, or share personal data with third parties.</p>
    
    <p>Uploaded documents may be processed using OCR services for text extraction. Extracted data is stored only for the user's aircraft records.</p>
    
    <p>Reasonable technical measures are implemented to protect user data.</p>
    
    <div class="contact">
        <p>For questions regarding this policy, please contact: <a href="mailto:support@aerologix.ai">support@aerologix.ai</a></p>
    </div>
    
    <p class="updated">Last updated: January 2026</p>
</body>
</html>
"""


# ============================================================
# TERMS OF USE
# ============================================================

TERMS_OF_USE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Terms of Use – AeroLogix AI</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            line-height: 1.6;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            color: #333;
        }
        h1 {
            color: #1a1a1a;
            border-bottom: 2px solid #007AFF;
            padding-bottom: 10px;
        }
        p {
            margin-bottom: 16px;
        }
        .eula-link {
            margin-top: 20px;
            padding: 15px;
            background: #f5f5f7;
            border-radius: 8px;
        }
        .eula-link a {
            color: #007AFF;
            text-decoration: none;
        }
        .eula-link a:hover {
            text-decoration: underline;
        }
        .updated {
            color: #666;
            font-size: 14px;
            margin-top: 30px;
        }
    </style>
</head>
<body>
    <h1>Terms of Use – AeroLogix AI</h1>
    
    <p>AeroLogix AI provides informational tools for aircraft maintenance tracking and document organization.</p>
    
    <p>The application does not make airworthiness determinations and does not replace certified maintenance professionals.</p>
    
    <p>Use of the application is subject to the Apple Standard End User License Agreement (EULA), available at the link below.</p>
    
    <div class="eula-link">
        <p><strong>Apple Standard EULA:</strong><br>
        <a href="https://www.apple.com/legal/internet-services/itunes/dev/stdeula/" target="_blank" rel="noopener">
            https://www.apple.com/legal/internet-services/itunes/dev/stdeula/
        </a></p>
    </div>
    
    <p class="updated">Last updated: January 2026</p>
</body>
</html>
"""


# ============================================================
# ENDPOINTS
# ============================================================

@router.get(
    "/privacy",
    response_class=HTMLResponse,
    summary="Privacy Policy",
    description="Public privacy policy page for Apple App Store compliance.",
    include_in_schema=True
)
async def privacy_policy():
    """
    Returns the Privacy Policy as an HTML page.
    
    - No authentication required
    - No database access
    - Apple App Review compliant
    """
    return HTMLResponse(content=PRIVACY_POLICY_HTML, status_code=200)


@router.get(
    "/terms",
    response_class=HTMLResponse,
    summary="Terms of Use",
    description="Public terms of use page with Apple Standard EULA reference.",
    include_in_schema=True
)
async def terms_of_use():
    """
    Returns the Terms of Use as an HTML page.
    
    - No authentication required
    - No database access
    - References Apple Standard EULA
    - Apple App Review compliant
    """
    return HTMLResponse(content=TERMS_OF_USE_HTML, status_code=200)
