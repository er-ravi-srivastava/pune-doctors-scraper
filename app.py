import os, time, requests, pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional
import streamlit as st

# ---------- Setup ----------
st.set_page_config(page_title="Pune Doctors Scraper (Places API)", layout="wide")

# Prefer Streamlit Secrets (cloud), fallback to env (local)
API_KEY = st.secrets.get("GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    st.error("Missing GOOGLE_API_KEY. On Streamlit Cloud, add it in **Settings → Secrets**.\n"
             "Locally, create a `.env` or environment variable with GOOGLE_API_KEY.")
    st.stop()

SESSION = requests.Session()
BASE_HEADERS = {"Content-Type": "application/json", "X-Goog-Api-Key": API_KEY}

TEXT_URL   = "https://places.googleapis.com/v1/places:searchText"
DETAIL_URL = "https://places.googleapis.com/v1/places/{place_id}"

TEXT_FIELDS   = "places.id,places.displayName,places.formattedAddress,places.types,places.rating,places.userRatingCount"
DETAIL_FIELDS = "id,displayName,formattedAddress,types,websiteUri,nationalPhoneNumber,internationalPhoneNumber,rating,userRatingCount"

# ---------- HTTP helpers ----------
def _retry(fn, *args, **kwargs):
    for i in range(3):
        try:
            return fn(*args, **kwargs)
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            if code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (i + 1))
                continue
            raise
    raise RuntimeError("Request failed after retries")

def text_search(query: str, page_token: Optional[str] = None, page_size: int = 20) -> Dict[str, Any]:
    page_size = max(1, min(page_size, 50))  # API cap
    payload: Dict[str, Any] = {"textQuery": query, "pageSize": page_size}
    if page_token:
        payload["pageToken"] = page_token
    r = SESSION.post(
        TEXT_URL,
        headers={**BASE_HEADERS, "X-Goog-FieldMask": TEXT_FIELDS},
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

def place_details(place_id: str, include_reviews: bool=False) -> Dict[str, Any]:
    fields = DETAIL_FIELDS + (",reviews" if include_reviews else "")
    r = SESSION.get(
        DETAIL_URL.format(place_id=place_id),
        headers={**BASE_HEADERS, "X-Goog-FieldMask": fields},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

def summarize_reviews(reviews: List[Dict[str, Any]]) -> str:
    if not reviews:
        return "—"
    snippets = []
    for rv in reviews[:5]:
        t = (rv.get("text", {}) or {}).get("text", "")
        if t:
            snippets.append(t.strip().replace("\n", " ")[:140])
    return " | ".join(snippets) if snippets else "—"

# ---------- UI ----------
st.title("Search Doctors and Clinics in Pune")

default_areas = ["Aundh, Pune", "Baner, Pune", "Wakad, Pune"]
areas = st.multiselect("Areas", default_areas, default=default_areas)

specialties = st.multiselect(
    "Specialties",
    ["cardiologist","dermatologist","neurologist","oncologist","general surgeon",
     "orthopedic","neurosurgeon","pediatrician","gynecologist","psychiatrist"],
    default=["dermatologist","cardiologist","pediatrician"],
)

max_results_per_query = st.slider("Number of results per search", 5, 50, 15)
include_reviews = st.checkbox("Show sample reviews", value=False)
threads = st.slider("Search speed (higher = faster, lower = safer)", 1, 10, 6)

if st.button("Find Doctors"):
    progress = st.progress(0)
    status = st.empty()
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()

    total_queries = max(len(areas) * len(specialties), 1)
    q_done = 0

    for area in areas:
        for sp in specialties:
            query = f"{sp} in {area}"
            status.write(f"Searching: **{query}**")

            try:
                data = _retry(text_search, query, page_token=None, page_size=max_results_per_query)
            except Exception as e:
                st.warning(f"TextSearch failed for '{query}': {e}")
                q_done += 1
                progress.progress(min(int(q_done / total_queries * 100), 100))
                continue

            places = (data.get("places") or [])[:max_results_per_query]

            with ThreadPoolExecutor(max_workers=threads) as ex:
                futures = {}
                for p in places:
                    pid = p.get("id")
                    if not pid or pid in seen:
                        continue
                    seen.add(pid)
                    futures[ex.submit(_retry, place_details, pid, include_reviews)] = p

                done = 0
                for fut in as_completed(futures):
                    p = futures[fut]
                    try:
                        det = fut.result()
                    except Exception as e:
                        st.info(f"Details failed for {p.get('id')}: {e}")
                        continue

                    name = (det.get("displayName", {}) or {}).get("text", "")
                    addr = det.get("formattedAddress", "")
                    phone = det.get("internationalPhoneNumber") or det.get("nationalPhoneNumber") or ""
                    website = det.get("websiteUri", "") or ""
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
                        "Locality searched": area,
                    })
                    done += 1
                    status.write(f"Fetched details {done}/{len(places)} for **{query}**")

            q_done += 1
            progress.progress(min(int(q_done / total_queries * 100), 100))

    if not rows:
        st.error("No rows fetched. Check your API key, quotas, or try a broader query.")
    else:
        df = pd.DataFrame(rows)
        st.success(f"Done. {len(df)} rows.")
        st.dataframe(df.head(20), use_container_width=True)
        out = "pune_doctors_streamlit.xlsx"
        df.to_excel(out, index=False)
        with open(out, "rb") as f:
            st.download_button(
                "Download Excel",
                f,
                file_name=out,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
