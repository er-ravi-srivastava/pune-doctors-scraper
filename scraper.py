import os
import time
import math
import requests
import pandas as pd
from typing import Dict, Any, List, Tuple
from dotenv import load_dotenv

# ------------------------
# Configuration
# ------------------------
SPECIALTIES = [
    "cardiologist", "dermatologist", "neurologist", "oncologist",
    "general surgeon", "orthopedic", "neurosurgeon",
    "pediatrician", "gynecologist", "psychiatrist"
]

AREAS = ["Aundh, Pune", "Baner, Pune", "Wakad, Pune"]

TEXT_URL   = "https://places.googleapis.com/v1/places:searchText"
DETAIL_URL = "https://places.googleapis.com/v1/places/{place_id}"

TEXT_FIELDS   = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.types,places.rating,places.userRatingCount"
)
DETAIL_FIELDS = (
    "id,displayName,formattedAddress,types,primaryType,websiteUri,"
    "nationalPhoneNumber,internationalPhoneNumber,rating,userRatingCount,reviews"
)

# ------------------------
# Init
# ------------------------
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise SystemExit("❌ Missing GOOGLE_API_KEY in .env")

BASE_HEADERS = {
    "Content-Type": "application/json",
    "X-Goog-Api-Key": API_KEY,
}

# ------------------------
# Helpers
# ------------------------
def text_search(query: str, page_token: str | None = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"textQuery": query}
    if page_token:
        payload["pageToken"] = page_token
    r = requests.post(
        TEXT_URL,
        headers={**BASE_HEADERS, "X-Goog-FieldMask": TEXT_FIELDS},
        json=payload,
        timeout=30
    )
    r.raise_for_status()
    return r.json()

def place_details(place_id: str) -> Dict[str, Any]:
    r = requests.get(
        DETAIL_URL.format(place_id=place_id),
        headers={**BASE_HEADERS, "X-Goog-FieldMask": DETAIL_FIELDS},
        timeout=30
    )
    r.raise_for_status()
    return r.json()

def summarize_reviews(reviews: List[Dict[str, Any]]) -> Tuple[str, str]:
    if not reviews:
        return "—", "No strong signal"
    pros, cons = [], []
    for rv in reviews[:5]:
        txt = (rv.get("text", {}) or {}).get("text", "")[:240].lower()
        if any(k in txt for k in ["good","great","excellent","friendly","clean","helpful","caring"]):
            pros.append(txt[:80])
        if any(k in txt for k in ["rude","wait","delay","expensive","crowd","poor","bad","unprofessional"]):
            cons.append(txt[:80])
    pros_s = "; ".join(pros[:3]) or "—"
    cons_s = "; ".join(cons[:3]) or "—"
    rec = "Recommended" if len(pros) >= len(cons) else "Mixed"
    return f"Pros: {pros_s}\nCons: {cons_s}", rec

def safe_get(d: Dict[str, Any], *path, default=None):
    cur = d
    for p in path:
        cur = cur.get(p, {}) if isinstance(cur, dict) else {}
    return cur if cur else default

# ------------------------
# Main runner
# ------------------------
def run(output_path: str = "pune_doctors.xlsx") -> tuple[str, int]:
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for area in AREAS:
        for sp in SPECIALTIES:
            query = f"{sp} in {area}"
            try:
                data = text_search(query)
            except requests.HTTPError as e:
                print(f"[WARN] TextSearch error for '{query}': {e.response.text}")
                continue

            places = data.get("places", [])
            next_token = data.get("nextPageToken")

            while True:
                for p in places:
                    pid = p.get("id")
                    if not pid or pid in seen:
                        continue
                    seen.add(pid)

                    # Details call (required to get phone, website, reviews)
                    try:
                        det = place_details(pid)
                    except requests.HTTPError as e:
                        print(f"[WARN] Details error {pid}: {e.response.text}")
                        continue

                    name = safe_get(det, "displayName", "text")
                    addr = det.get("formattedAddress", "")
                    phone = det.get("internationalPhoneNumber") or det.get("nationalPhoneNumber") or ""
                    website = det.get("websiteUri", "")
                    rating = det.get("rating", None)
                    count  = det.get("userRatingCount", None)
                    reviews = det.get("reviews", [])
                    summary, recommend = summarize_reviews(reviews)

                    rows.append({
                        "Doctor/Clinic name": name,
                        "Specialty (from query)": sp.title(),
                        "Clinic/Hospital": name,
                        "Complete address": addr,
                        "Years of experience": "",     # Not available via Places
                        "Contact number": phone,
                        "Contact email": "",           # Not available via Places
                        "Ratings": rating,
                        "Reviews count": count,
                        "Pros/Cons summary": summary,
                        "Recommendation": recommend,
                        "Website": website,
                        "Place ID": pid,
                        "Locality searched": area
                    })

                if not next_token:
                    break
                # brief pause before using nextPageToken (Google recommends a small delay)
                time.sleep(2)
                try:
                    data = text_search(query, page_token=next_token)
                except requests.HTTPError as e:
                    print(f"[WARN] Pagination error for '{query}': {e.response.text}")
                    break
                places = data.get("places", [])
                next_token = data.get("nextPageToken")

    df = pd.DataFrame(rows)
    # keep all rows (no de-dupe across specialties/areas beyond place id)
    # if you want unique places only:
    # df.drop_duplicates(subset=["Place ID"], inplace=True)

    df.to_excel(output_path, index=False)
    return output_path, len(df)

if __name__ == "__main__":
    path, n = run()
    print(f"✅ Saved {n} rows to {path}")
