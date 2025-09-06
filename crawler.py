import re
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime
from typing import Optional, Tuple, Dict, List

# Patterns for email and obfuscated email forms
EMAIL_PAT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", re.I)

OBFUSC_EMAIL_PAT = re.compile(
    r"""
    ([A-Za-z0-9._%+\-]+)      # local
    \s*(?:\[?at\]?|\(at\)|@)\s*
    ([A-Za-z0-9.\-]+)
    \s*(?:\[?dot\]?|\(dot\)|\.)\s*
    ([A-Za-z]{2,})
    """,
    re.I | re.X,
)

EXP_PAT = re.compile(
    r"""
    (?:
        (?P<num1>\d{1,2}\+?)\s*(?:years?|yrs?)\s*(?:of\s+)?experience|
        over\s+(?P<num2>\d{1,2})\s*(?:years?|yrs?)|
        since\s+(?P<year1>19\d{2}|20\d{2})|
        practicing\s+since\s+(?P<year2>19\d{2}|20\d{2})
    )
    """,
    re.I | re.X,
)


def _norm_obfuscated(m: re.Match) -> str:
    return f"{m.group(1)}@{m.group(2)}.{m.group(3)}"


def _infer_years_from_year(year: int) -> Optional[int]:
    now = datetime.now().year
    if 1970 <= year <= now:
        return max(0, now - year)
    return None


def extract_email_and_exp(text: str) -> Tuple[Optional[str], Optional[int]]:
    email = None
    years = None

    if not text:
        return None, None

    # direct email
    m = EMAIL_PAT.search(text)
    if m:
        email = m.group(0)

    # obfuscated email like name [at] domain [dot] com
    if not email:
        ob = OBFUSC_EMAIL_PAT.search(text)
        if ob:
            try:
                email = _norm_obfuscated(ob)
            except Exception:
                email = None

    # years of experience patterns
    em = EXP_PAT.search(text)
    if em:
        if em.group("num1"):
            try:
                years = int(em.group("num1").replace("+", ""))
            except Exception:
                pass
        elif em.group("num2"):
            try:
                years = int(em.group("num2"))
            except Exception:
                pass
        elif em.group("year1"):
            years = _infer_years_from_year(int(em.group("year1")))
        elif em.group("year2"):
            years = _infer_years_from_year(int(em.group("year2")))

    return email, years


def fetch_html(url: str, timeout: int = 8) -> Optional[str]:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; Bot/0.1)"})
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code == 200 and r.text:
            return r.text
    except requests.RequestException:
        return None
    return None


def crawl_doctor_site(url: str) -> Dict[str, Optional[object]]:
    """
    Crawl given homepage URL and a small set of candidate pages to extract:
      - email (if available)
      - years_of_experience (best-effort)
    Returns dict {"email": str|None, "years_of_experience": int|None}
    """
    homepage = fetch_html(url)
    if not homepage:
        return {"email": None, "years_of_experience": None}

    soup = BeautifulSoup(homepage, "html.parser")

    # 1) mailto links on homepage
    email_mailto = None
    for a in soup.select('a[href^="mailto:"]'):
        addr = a.get("href", "")[7:]
        if EMAIL_PAT.fullmatch(addr):
            email_mailto = addr
            break

    # 2) JSON-LD with "email"
    jsonld_emails: List[str] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except Exception:
            continue
        objs = data if isinstance(data, list) else [data]
        for obj in objs:
            if isinstance(obj, dict):
                em = obj.get("email")
                if isinstance(em, str):
                    jsonld_emails.append(em.strip())

    # 3) visible text on homepage
    text_home = soup.get_text(" ", strip=True)
    email_text, years_text = extract_email_and_exp(text_home)

    email = email_mailto or (jsonld_emails[0] if jsonld_emails else None) or email_text
    years = years_text

    # candidate subpages to inspect (shallow)
    candidates: set = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(w in href for w in ["contact", "about", "team", "doctor", "doctors", "providers", "staff", "meet"]):
            candidates.add(urljoin(url, a["href"]))

    # crawl up to 8 candidate pages
    for link in list(candidates)[:8]:
        if email and years:
            break
        html = fetch_html(link)
        if not html:
            continue
        s2 = BeautifulSoup(html, "html.parser")

        # mailto on subpage
        if not email:
            for a in s2.select('a[href^="mailto:"]'):
                addr = a.get("href", "")[7:]
                if EMAIL_PAT.fullmatch(addr):
                    email = addr
                    break

        # json-ld on subpage
        if not email:
            for tag in s2.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(tag.string or "{}")
                except Exception:
                    continue
                objs = data if isinstance(data, list) else [data]
                for obj in objs:
                    if isinstance(obj, dict) and isinstance(obj.get("email"), str):
                        email = obj["email"].strip()
                        break
                if email:
                    break

        # extract from visible text
        if not (email and years):
            t2 = s2.get_text(" ", strip=True)
            em2, y2 = extract_email_and_exp(t2)
            if not email and em2:
                email = em2
            if years is None and y2 is not None:
                years = y2

    return {"email": email, "years_of_experience": years}


if __name__ == "__main__":
    # local quick test (change URL as desired)
    url = "https://www.neoskinhair.com/"
    print(crawl_doctor_site(url))