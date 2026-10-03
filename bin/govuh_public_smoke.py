#!/usr/bin/env python3
"""Read-only, TLS-verifying public GOV.UH Forms acceptance checks.

No form is completed, no data is submitted, and no authenticated route is used.
Use the existing Forms e2e suite for controlled authenticated/submission testing.
"""
from __future__ import annotations
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

BASE = os.environ.get("GOVUH_BASE_URL", "https://www.gov.uhrblx.com").rstrip("/")
SITES = {
    "canonical-homepage": "/",
    "contact-service": "/contact/",
    "ministerial-application-start": os.environ.get("GOVUH_EOI_PATH", "/government/ministerial-expression-of-interest"),
    "ministerial-form": os.environ.get("GOVUH_EOI_FORM_PATH", "/form/1"),
    "published-forms-journey": os.environ.get("GOVUH_LIVE_FORM_PATH", "/form/2/get-help-finding-a-job-or-opportunity/NESopp01"),
}

class VisibleContent(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ignore = 0
        self.title = ""
        self.title_level = 0
        self.text = []
        self.image_alts = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "template"):
            self.ignore += 1
        if tag == "title":
            self.title_level += 1
        if tag == "img":
            self.image_alts.append(a.get("alt") or "")
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "template"):
            self.ignore = max(self.ignore - 1, 0)
        if tag == "title":
            self.title_level = max(self.title_level - 1, 0)

    def handle_data(self, data):
        if not self.ignore:
            self.text.append(data)
            if self.title_level:
                self.title += data

class SameOriginRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        origin = urllib.parse.urlsplit(BASE)
        destination = urllib.parse.urlsplit(newurl)
        if (destination.scheme, destination.netloc) != (origin.scheme, origin.netloc):
            raise ValueError(f"Cross-origin redirect prevented: {destination.scheme}://{destination.netloc}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def probe(name, path):
    result = {"check": name, "path": path, "pass": False, "errors": []}
    origin = urllib.parse.urlsplit(BASE)
    if origin.scheme != "https" or not origin.hostname or not path.startswith("/") or path.startswith("//"):
        result["errors"].append("Invalid HTTPS base URL or path")
        return result
    url = urllib.parse.urljoin(BASE + "/", path.lstrip("/"))
    if (urllib.parse.urlsplit(url).scheme, urllib.parse.urlsplit(url).netloc) != (origin.scheme, origin.netloc):
        result["errors"].append("Initial request is outside the approved GOV.UH origin")
        return result
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()), SameOriginRedirects())
    req = urllib.request.Request(url, headers={"User-Agent": "GOVUH-read-only-Forms-acceptance/1.0"}, method="GET")
    try:
        with opener.open(req, timeout=12) as response:
            result["http_status"] = response.status
            result["final_url"] = response.url
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                result["errors"].append("Document exceeds bounded inspection size")
                return result
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        result["errors"].append(f"HTTP {exc.code}; expected a live public page")
        return result
    except Exception as exc:
        result["errors"].append(str(exc))
        return result
    if response.status != 200:
        result["errors"].append(f"HTTP {response.status}, expected 200")
    parser = VisibleContent()
    parser.feed(body.decode("utf-8", errors="replace"))
    result["title"] = parser.title.strip()
    if name in ("ministerial-form", "published-forms-journey"):
        readable = " ".join(parser.text + parser.image_alts)
        if re.search(r"GOV\.UK\s+Forms|GOV\.UK\s*[-–]\s*GOV\.UK", readable, re.I):
            result["errors"].append("UK Forms identity displayed in public content")
        if not re.search(r"GOV\.UH|United Hampshire", readable, re.I):
            result["errors"].append("UH public identity absent from Forms page")
        if any(urllib.parse.urlsplit(link).hostname in ("gov.uk", "www.gov.uk", "signin.account.gov.uk") for link in parser.links):
            result["errors"].append("UK-government outbound destination in public Forms page")
    result["pass"] = not result["errors"]
    return result

if __name__ == "__main__":
    checks = [probe(name, path) for name, path in SITES.items()]
    report = {"mode": "read-only", "base_url": BASE, "pass": all(c["pass"] for c in checks), "checks": checks}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    sys.exit(0 if report["pass"] else 1)
