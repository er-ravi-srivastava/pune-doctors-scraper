import os, time, requests, pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
import streamlit as st

# ----------------- Config -----------------
TEXT_URL   = "https://places.googleapis.com/v1/places:searchText"
DETAIL_URL = "https://places.googleapis.com/v1/places/{place_id}"

TEXT_FIELDS_BASE = "places.id,places.displayName,places.formattedAddress,places.types,places.rating,places.userRatingCount"
DETAIL_FIELDS_BASE = "id,displayName,formattedAddress,types,websiteUri,nationalPhoneNumber,internationalPhoneNumber,rating,userRatingCount"

# ----------------- API setup -----------------
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    st.stop()

session = requests.Session()
BASE_HEADERS = {"Content-Type":"application/json", "X-Goog-Api-Key": API_KEY}

def text_search(query: str, page_token: Optional[str] = None, page_size: int = 20) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"textQuery": query, "pageSize": page_size}
    if page_token:
        payload["pageToken"] = page_token
    r = session.post(
        TEXT_URL,
        headers={**BASE_HEADERS, "X-Goog-FieldMask": TEXT_FIELDS_BASE},
        json=payload,
        timeout=30
    )
    r.raise_for_status()
    return r.json()

def place_details(place_id: str, include_reviews: bool=False) -> Dict[str, Any]:
    details_fields = DETAIL_FIELDS_BASE + (",reviews" if include_reviews else "")
    r = session.get(
        DETAIL_URL.format(place_id=place_id),
        headers={**BASE_HEADERS, "X-Goog-FieldMask": details_fields},
        timeout=30
    )
    r.raise_for_status()
    return r.json()

def summarize_reviews(reviews: List[Dict[str, Any]]) -> str:
    if not reviews: return "—"
    texts = []
    for rv in reviews[:5]:
        t = (rv.get("text", {}) or {}).get("text", "")
        if t: texts.append(t.strip().replace("\n"," ")[:140])
    return " | ".join(texts) if texts else "—"

# ----------------- UI -----------------
st.set_page_config(page_title="Pune Doctors Scraper (Places API)", layout="wide")
st.title("Pune Doctors Scraper (Google Places)")

default_areas = ["Aundh, Pune", "Baner, Pune", "Wakad, Pune"]
areas = st.multiselect("Areas", default_areas, default=default_areas)
specialties = st.multiselect(
    "Specialties",
    ["cardiologist","dermatologist","neurologist","oncologist","general surgeon",
     "orthopedic","neurosurgeon","pediatrician","gynecologist","psychiatrist"],
    default=["dermatologist","cardiologist","pediatrician"]
)
max_results_per_query = st.slider("Max places per query", 5, 50, 15)
include_reviews = st.checkbox("Include short review snippets (slower)", value=False)
threads = st.slider("Parallel detail lookups", 1, 10, 6)
run_button = st.button("Run scraper")

if run_button:
    progress = st.progress(0)
    status = st.empty()
    rows = []
    seen = set()

    total_queries = max(len(areas)*len(specialties), 1)
    q_done = 0

    for area in areas:
        for sp in specialties:
            query = f"{sp} in {area}"
            status.write(f"Searching: **{query}**")
            try:
                data = text_search(query, page_token=None, page_size=max_results_per_query)
            except requests.HTTPError as e:
                st.warning(f"TextSearch failed for '{query}': {e.response.text}")
                q_done += 1
                progress.progress(min(int(q_done/total_queries*100), 100))
                continue

            places = data.get("places", []) or []
            # Cap to user-selected max
            places = places[:max_results_per_query]

            # Concurrent details lookups
            with ThreadPoolExecutor(max_workers=threads) as ex:
                futures = {}
                for p in places:
                    pid = p.get("id")
                    if not pid or pid in seen: continue
                    seen.add(pid)
                    futures[ex.submit(place_details, pid, include_reviews)] = p

                done = 0
                for fut in as_completed(futures):
                    p = futures[fut]
                    try:
                        det = fut.result()
                    except requests.HTTPError as e:
                        st.info(f"Details failed for {p.get('id')}: {e.response.text}")
                        continue

                    name = (det.get("displayName", {}) or {}).get("text","")
                    addr = det.get("formattedAddress","")
                    phone = det.get("internationalPhoneNumber") or det.get("nationalPhoneNumber") or ""
                    website = det.get("websiteUri","") or ""
                    rating = det.get("rating")
                    count  = det.get("userRatingCount")
                    summary = summarize_reviews(det.get("reviews", [])) if include_reviews else "—"

                    rows.append({
                        "Doctor/Clinic name": name,
                        "Specialty (from query)": sp.title(),
                        "Clinic/Hospital": name,
                        "Complete address": addr,
                        "Contact number": phone,
                        "Website": website,
                        "Ratings": rating,
                        "Reviews count": count,
                        "Review snippets": summary,
                        "Place ID": det.get("id"),
                        "Locality searched": area
                    })
                    done += 1
                    status.write(f"Fetched details {done}/{len(places)} for **{query}**")

            q_done += 1
            progress.progress(min(int(q_done/total_queries*100), 100))

    if not rows:
        st.error("No rows fetched. Check your API key, quotas, or try a broader query.")
    else:
        df = pd.DataFrame(rows)
        st.success(f"Done. {len(df)} rows.")
        st.dataframe(df.head(20))
        out = "pune_doctors_streamlit.xlsx"
        df.to_excel(out, index=False)
        with open(out, "rb") as f:
            st.download_button("Download Excel", f, file_name=out, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
