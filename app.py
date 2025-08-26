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
import streamlit as st

# =========================
# Streamlit page config
# =========================
st.set_page_config(page_title="Search Doctors and Clinics in Pune", layout="wide")
st.title("Search Doctors and Clinics in Pune")

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

def _post_json(url: str, headers: dict, payload: dict, timeout: int = 30) -> dict:
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if r.status_code >= 400:
        try:
            st.warning(r.json())
        except Exception:
            st.warning(r.text)
        r.raise_for_status()
    return r.json()

def _get_json(url: str, headers: dict, timeout: int = 30) -> dict:
    r = requests.get(url, headers=headers, timeout=timeout)
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
    time.sleep(1.25 * (attempt + 1))

def retry_request(fn, *args, **kwargs):
    tries = kwargs.pop("tries", 4)
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

# ---- simple area centers for locationBias (meters)
AREA_CENTERS: dict[str, Tuple[float, float]] = {
    "Aundh, Pune": (18.5606, 73.8077),
    "Baner, Pune": (18.5590, 73.7806),
    "Wakad, Pune": (18.5976, 73.7707),
}
AREA_RADIUS_M = 6000  # 6 km circle

def text_search_page(
    query: str,
    page_token: Optional[str] = None,
    page_size: int = 20,
    center: Optional[Tuple[float, float]] = None,
    radius_m: int = AREA_RADIUS_M,
) -> Dict[str, Any]:
    """
    One Text Search request.
    - page_size max is 20 (per Google Places Text Search)
    - include locationBias (circle) if center provided
    """
    page_size = max(1, min(page_size, 20))  # cap at 20
    payload: Dict[str, Any] = {"textQuery": query, "pageSize": page_size}
    if page_token:
        payload["pageToken"] = page_token
    if center:
        lat, lng = center
        payload["locationBias"] = {
            "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_m}
        }
    return _post_json(TEXT_URL, headers=_headers(TEXT_FIELDS), payload=payload)

def paginate_text_search(
    query: str,
    total_needed: int,
    center: Optional[Tuple[float, float]] = None,
) -> List[Dict[str, Any]]:
    """
    Keep calling searchText while:
    - we have a nextPageToken (becomes valid after ~2s)
    - we still need more results
    """
    results: List[Dict[str, Any]] = []
    token: Optional[str] = None
    while len(results) < total_needed:
        remaining = total_needed - len(results)
        page_size = min(20, remaining)
        data = retry_request(
            text_search_page, query, page_token=token, page_size=page_size, center=center
        )
        page_places = data.get("places") or []
        results.extend(page_places)
        token = data.get("nextPageToken")
        if not token:
            break
        # nextPageToken usually needs a short delay to become valid
        time.sleep(2.1)
    return results[:total_needed]

def place_details(place_id: str, include_reviews: bool = False) -> Dict[str, Any]:
    fields = DETAIL_FIELDS_BASE + (",reviews" if include_reviews else "")
    return _get_json(DETAIL_URL.format(place_id=place_id), headers=_headers(fields))

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
_CLINIC_WORDS = ("clinic", "hospital", "medical", "centre", "center", "diagnostic", "labs", "skin", "laser", "hair")

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
    max_total_results = st.slider("Max results (area total)" , 10, 100, 10, step=5)
    include_reviews = st.checkbox("Include review snippets", value=False)
    threads = st.slider("Parallel requests", 1, 10, 6)
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

        # Text Search with robust pagination (20 per page + 2s token delay)
        try:
            place_summaries = paginate_text_search(query, total_needed=per_specialty_budget, center=center)
            print(f"\n=== RAW TEXT_SEARCH JSON for {query} ===")
            print(place_summaries)
        except Exception as e:
            st.warning(f"Text search failed for '{query}': {e}")
            continue

        with ThreadPoolExecutor(max_workers=threads) as ex:
            futures = {}
            for p in place_summaries:
                pid = p.get("id")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                futures[ex.submit(retry_request, place_details, pid, include_reviews, tries=4)] = p

            fetched = 0
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    det = fut.result()
                    print(f"\n=== RAW PLACE_DETAILS JSON for {p.get('id')} ===")
                    print(det)
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
                summary = summarize_reviews(det.get("reviews", [])) if include_reviews else "N/A"
                recommendation = make_recommendation(rating, count)

                contact_email, years_exp = "N/A", "N/A"

                # if website != "N/A":
                #     try:
                #         info = crawl_doctor_site(website)
                #         if info.get("email"):
                #             contact_email = info["email"]
                #         if info.get("experience"):
                #             years_exp = info["experience"]
                #     except Exception as e:
                #         st.info(f"Failed to crawl {website}: {e}")

                if website != "N/A":
                    crawl_future = ex.submit(crawl_doctor_site, website)
                    try:
                        info = crawl_future.result(timeout=8)  # timeout so one site doesn’t hang forever
                        contact_email = info.get("email", "N/A")
                        years_exp = info.get("years_of_experience", "N/A")
                    except Exception as e:
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
                        "Website": website,
                        "Ratings": rating if rating is not None else "N/A",
                        "Reviews": count if count is not None else "N/A",
                        "Summary of Pros and Cons (reviews)": summary,
                        "Recommendation": recommendation,
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
        st.dataframe(df, use_container_width=True)

        out_path = f"{area.split(',')[0].lower()}_doctors_streamlit.xlsx".replace(" ", "_")
        df.to_excel(out_path, index=False)
        with open(out_path, "rb") as f:
            st.download_button(
                label="Download Excel",
                data=f,
                file_name=out_path,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
