# app.py
# ---------------------------------------------
# Search Doctors and Clinics in Pune (Streamlit)
# Google Places API (v1) with robust pagination (up to 500 target)
# ---------------------------------------------
from __future__ import annotations
from crawler import crawl_doctor_site
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import streamlit as st

# =========================
# Streamlit page config
# =========================
st.set_page_config(page_title="Search Doctors and Clinics in Pune", layout="wide")
st.title("Search Doctors and Clinics in Pune")

# =========================
# HTTP session with pooling & retries (faster)
# =========================
SESSION = requests.Session()
_retries = Retry(
    total=3,
    backoff_factor=0.6,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"],
    raise_on_status=False,
)
adapter = HTTPAdapter(max_retries=_retries, pool_connections=256, pool_maxsize=256)
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)

DEFAULT_TIMEOUT = 8

# =========================
# API key loading
# =========================
def load_api_key() -> Optional[str]:
    try:
        section = st.secrets.get("api", {})
        if section and "google_api_key" in section:
            return section["google_api_key"].strip()
    except Exception:
        pass
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    k = os.getenv("GOOGLE_API_KEY")
    return k.strip() if k else None

API_KEY = load_api_key()
if not API_KEY:
    st.error(
        "Missing **GOOGLE API key**.\n\n"
        "Add it in `.streamlit/secrets.toml` as:\n"
        "```\n[api]\ngoogle_api_key = \"YOUR_KEY\"\n```\n"
        "Or set an environment variable `GOOGLE_API_KEY` (you can use a `.env` file)."
    )
    st.stop()

# =========================
# HTTP / endpoints / fields
# =========================
TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
DETAIL_URL = "https://places.googleapis.com/v1/places/{place_id}"

TEXT_FIELDS = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.types",
        "places.rating",
        "places.userRatingCount",
    ]
)

DETAIL_FIELDS_BASE = ",".join(
    [
        "id",
        "displayName",
        "formattedAddress",
        "types",
        "websiteUri",
        "nationalPhoneNumber",
        "internationalPhoneNumber",
        "rating",
        "userRatingCount",
    ]
)

def _headers(field_mask: str) -> dict:
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": field_mask,
    }

def _post_json(url: str, headers: dict, payload: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    r = SESSION.post(url, headers=headers, json=payload, timeout=timeout)
    if r.status_code >= 400:
        try:
            st.warning(r.json())
        except Exception:
            st.warning(r.text)
        r.raise_for_status()
    return r.json()

def _get_json(url: str, headers: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    r = SESSION.get(url, headers=headers, timeout=timeout)
    if r.status_code >= 400:
        try:
            st.warning(r.json())
        except Exception:
            st.warning(r.text)
        r.raise_for_status()
    return r.json()

# =========================
# Helpers
# =========================
def backoff_sleep(attempt: int) -> None:
    time.sleep(0.9 * (attempt + 1))

def retry_request(fn, *args, **kwargs):
    tries = kwargs.pop("tries", 3)
    for attempt in range(tries):
        try:
            return fn(*args, **kwargs)
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            if code in {429, 500, 502, 503, 504} and attempt < tries - 1:
                backoff_sleep(attempt)
                continue
            raise
        except requests.RequestException:
            if attempt < tries - 1:
                backoff_sleep(attempt)
                continue
            raise
    raise RuntimeError("Request failed after retries")

# ---- area centers
AREA_CENTERS: dict[str, Tuple[float, float]] = {
    "Aundh, Pune": (18.5606, 73.8077),
    "Baner, Pune": (18.5590, 73.7806),
    "Wakad, Pune": (18.5976, 73.7707),
}
AREA_RADIUS_M = 6000

def text_search_page(query: str, page_token: Optional[str] = None,
                     page_size: int = 20, center: Optional[Tuple[float, float]] = None,
                     radius_m: int = AREA_RADIUS_M) -> Dict[str, Any]:
    page_size = max(1, min(page_size, 20))
    payload: Dict[str, Any] = {"textQuery": query, "pageSize": page_size}
    if page_token:
        payload["pageToken"] = page_token
    if center:
        lat, lng = center
        payload["locationBias"] = {
            "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_m}
        }
    return _post_json(TEXT_URL, headers=_headers(TEXT_FIELDS), payload=payload)

def paginate_text_search(query: str, total_needed: int,
                         center: Optional[Tuple[float, float]] = None) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    token: Optional[Tuple[str, int]] = None
    while len(results) < total_needed:
        remaining = total_needed - len(results)
        page_size = min(20, remaining)
        data = retry_request(
            text_search_page, query, page_token=(token[0] if token else None),
            page_size=page_size, center=center
        )
        page_places = data.get("places") or []
        results.extend(page_places)
        nxt = data.get("nextPageToken")
        if not nxt:
            break
        time.sleep(2.0)
        token = (nxt, page_size)
    return results[:total_needed]

# ---- Cache heavy calls for speed
@st.cache_data(ttl=7200, show_spinner=False)
def cached_place_details(place_id: str, include_reviews: bool = True) -> Dict[str, Any]:
    fields = DETAIL_FIELDS_BASE + ",reviews"
    return _get_json(DETAIL_URL.format(place_id=place_id), headers=_headers(fields))

@st.cache_data(ttl=7200, show_spinner=False)
def cached_crawl_site(url: str) -> Dict[str, Any]:
    try:
        return crawl_doctor_site(url) or {}
    except Exception:
        return {}

def summarize_reviews(reviews: List[Dict[str, Any]]) -> str:
    if not reviews:
        return "N/A"
    snippets: List[str] = []
    for rv in reviews[:5]:
        t = (rv.get("text") or {}).get("text", "")
        if t:
            t = t.strip().replace("\n", " ")
            snippets.append(t[:140])
    return " | ".join(snippets) if snippets else "N/A"

# ---- name parsing
_DOCTOR_PAT = re.compile(r"\bDr\.?\s*[A-Z][A-Za-z.\- ]{1,60}", flags=re.UNICODE)
_CLINIC_WORDS = ("clinic", "hospital", "medical", "centre", "center",
                 "diagnostic", "labs", "skin", "laser", "hair")

def split_doctor_and_clinic(place_name: str) -> Tuple[str, str]:
    if not place_name:
        return "N/A", "N/A"
    name = place_name.strip()
    low = name.lower()
    m = _DOCTOR_PAT.search(name)
    if m:
        doc = m.group(0).strip(" -|,")
        rest = (name[:m.start()] + name[m.end():]).strip(" -|,")
        clinic = rest if (rest and any(w in rest.lower() for w in _CLINIC_WORDS)) else "N/A"
        return doc, clinic
    if any(w in low for w in _CLINIC_WORDS):
        return "N/A", name
    return "N/A", name

def make_recommendation(rating: Optional[float], count: Optional[int]) -> str:
    if rating is None or count is None or count == 0:
        return "Insufficient data"
    if rating >= 4.5 and count >= 50:
        return "Highly recommended"
    if rating >= 4.0 and count >= 10:
        return "Recommended"
    return "Consider with caution"

# =========================
# Sidebar controls
# =========================
with st.sidebar:
    st.header("Filters")
    area = st.selectbox("Area", ["Aundh, Pune", "Baner, Pune", "Wakad, Pune"], index=0)
    specialties = st.multiselect(
        "Specialties",
        [
            "cardiologist",
            "dermatologist",
            "neurologist",
            "oncologist",
            "general surgeon",
            "orthopedic",
            "neurosurgeon",
            "pediatrician",
            "gynecologist",
            "psychiatrist",
        ],
        default=["dermatologist", "cardiologist", "pediatrician"],
    )
    max_total_results = st.slider("Max results (area total)", 10, 100, 10, step=5)
    threads = st.slider("Parallel requests", 1, 20, 12)
    run = st.button("Find Doctors")

# =========================
# Run search
# =========================
if run:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    progress = st.progress(0)
    status = st.empty()

    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()

    center = AREA_CENTERS.get(area)
    per_specialty_budget = max_total_results // max(1, len(specialties))

    for idx, sp in enumerate(specialties, start=1):
        query = f"{sp} in {area}"
        status.info(f"Searching: **{query}** (target {per_specialty_budget})")

        try:
            place_summaries = paginate_text_search(query, total_needed=per_specialty_budget, center=center)
        except Exception as e:
            st.warning(f"Text search failed for '{query}': {e}")
            continue

        max_workers = max(1, min(threads, 32))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {}
            for p in place_summaries:
                pid = p.get("id")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                futures[ex.submit(retry_request, cached_place_details, pid, True, tries=3)] = p

            fetched = 0
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    det = fut.result()
                except Exception as e:
                    st.info(f"Details failed for {p.get('id')}: {e}")
                    continue

                place_name = (det.get("displayName") or {}).get("text", "") or ""
                doc_name, clinic_name = split_doctor_and_clinic(place_name)

                addr = det.get("formattedAddress", "") or "N/A"
                phone = det.get("internationalPhoneNumber") or det.get("nationalPhoneNumber") or "N/A"
                website = det.get("websiteUri", "") or "N/A"
                rating = det.get("rating")
                count = det.get("userRatingCount")
                summary = summarize_reviews(det.get("reviews", [])) or "N/A"
                recommendation = make_recommendation(rating, count)

                combined_summary = (summary if summary and summary != "N/A" else "")
                if recommendation:
                    combined_summary = (combined_summary + "\n\nRecommendation: " + recommendation).strip()
                if not combined_summary:
                    combined_summary = "N/A"

                contact_email, years_exp = "N/A", "N/A"

                if website != "N/A":
                    crawl_future = ex.submit(cached_crawl_site, website)
                    try:
                        info = crawl_future.result(timeout=4)
                        contact_email = info.get("email", "N/A") or "N/A"
                        years_exp = info.get("years_of_experience", "N/A") or "N/A"
                    except Exception:
                        pass

                rows.append(
                    {
                        "Complete address": addr,
                        "Doctor name": doc_name if doc_name else "N/A",
                        "Specialty": sp.title(),
                        "Clinic/Hospital": clinic_name if clinic_name else "N/A",
                        "Years of experience": years_exp if years_exp else "N/A",
                        "Contact number": phone,
                        "Contact email": contact_email if contact_email else "N/A",
                        "Ratings": rating if rating is not None else "N/A",
                        "Reviews": count if count is not None else "N/A",
                        "Summary of Pros and Cons and Recommendation": combined_summary,
                    }
                )

                fetched += 1
                status.write(f"Fetched details {fetched}/{len(place_summaries)} for **{query}**")

        progress.progress(int(idx / max(1, len(specialties)) * 100))

    if not rows:
        st.error("No results fetched. Try a broader query or smaller radius.")
    else:
        df = pd.DataFrame(rows)
        st.success(f"Done. {len(df)} rows for {area}.")
        st.dataframe(df, use_container_width=True, hide_index=True)

        out_path = f"{area.split(',')[0].lower()}_doctors_streamlit.xlsx".replace(" ", "_")
        df.to_excel(out_path, index=False)
        with open(out_path, "rb") as f:
            st.download_button(
                label="Download Excel",
                data=f,
                file_name=out_path,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
