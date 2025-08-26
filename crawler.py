import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime

def crawl_doctor_site(url: str):
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    def fetch_page(link: str):
        try:
            resp = session.get(link, timeout=8)
            if resp.status_code == 200:
                return resp.text
        except requests.exceptions.RequestException as e:
            # controlled logging instead of noisy prints
            print(f"[warn] Failed to fetch {link}: {e}")
        return None

    def extract_email_and_exp(text: str):
        email_pattern = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
        exp_pattern = (
            r"(\d{1,2}\+?)\s*(?:years?|yrs?)\s*(?:of\s+)?experience"
            r"|over\s+(\d{1,2})\s*(?:years?|yrs?)"
        )

        email_match = re.search(email_pattern, text, re.I)
        exp_match = re.search(exp_pattern, text, re.I)

        email = email_match.group(0) if email_match else None
        years = None
        if exp_match:
            years = exp_match.group(1) or exp_match.group(2)
            if years:
                years = years.replace("+", "")
                try:
                    years = int(years)
                except ValueError:
                    years = None
        return email, years

    # --- 1. Home page ---
    homepage = fetch_page(url)
    if not homepage:
        return {"email": None, "years_of_experience": None}

    soup = BeautifulSoup(homepage, "html.parser")
    email, years = extract_email_and_exp(soup.get_text(" ", strip=True))

    # --- 2. Candidate subpages ---
    candidate_pages = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(word in href for word in ["about", "contact", "team", "profile", "doctor"]):
            full_url = urljoin(url, a["href"])  # safer than string concat
            candidate_pages.add(full_url)

    # --- 3. Crawl subpages until info is found ---
    for link in candidate_pages:
        if email and years:
            break
        html = fetch_page(link)
        if not html:
            continue
        sub_soup = BeautifulSoup(html, "html.parser")
        new_email, new_years = extract_email_and_exp(sub_soup.get_text(" ", strip=True))
        if not email and new_email:
            email = new_email
        if not years and new_years:
            years = new_years

    return {"email": email, "years_of_experience": years}


if __name__ == "__main__":
    url = "https://www.neoskinhair.com/"  # replace with a real doctor/clinic website
    result = crawl_doctor_site(url)
    print("Scraped info:", result)



