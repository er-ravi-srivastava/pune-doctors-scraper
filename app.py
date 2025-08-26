# app.py
# ---------------------------------------------
# Search Doctors and Clinics in Pune (Streamlit)
# Google Places API (v1)
# ---------------------------------------------
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

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
    """
    Load Google API key in this order:
    1) .streamlit/secrets.toml  -> [api].google_api_key
    2) Environment variable     -> GOOGLE_API_KEY (optionally from .env)
    """
    try:
        section = st.secrets.get("api", {})
        if section and "google_api_key" in section:
            return section["google_api_key"]
    except Exception:
        pass

    try:
        from dotenv import load_dotenv  # optional dependency
        load_dotenv()
    except Exception:
        pass

    return os.getenv("GOOGLE_API_KEY")


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

# ---- explicit headers + request helpers (fix for 400s)
def _headers(field_mask: str) -> dict:
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": field_mask,  # required by Places API (New)
    }

def _post_json(url: str, headers: dict, payload: dict, timeout: int = 30) -> dict:
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if r.status_code >= 400:
        # show Google's detailed error in the UI
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
    """Exponential backoff."""
    time.sleep(1.25 * (attempt + 1))

def retry_request(fn, *args, **kwargs):
    """Retry wrapper for transient HTTP errors."""
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

def text_search(query: str, page_token: Optional[str] = None, page_size: int = 20) -> Dict[str, Any]:
    page_size = max(1, min(page_size, 50))  # API cap
    payload: Dict[str, Any] = {"textQuery": query, "pageSize": page_size}
    if page_token:
        payload["pageToken"] = page_token
    return _post_json(
        TEXT_URL,
        headers=_headers(TEXT_FIELDS),
        payload=payload,
    )

def place_details(place_id: str, include_reviews: bool = False) -> Dict[str, Any]:
    fields = DETAIL_FIELDS_BASE + (",reviews" if include_reviews else "")
    return _get_json(
        DETAIL_URL.format(place_id=place_id),
        headers=_headers(fields),
    )

def summarize_reviews(reviews: List[Dict[str, Any]]) -> str:
    if not reviews:
        return "—"
    snippets: List[str] = []
    for rv in reviews[:5]:
        t = (rv.get("text") or {}).get("text", "")
        if t:
            t = t.strip().replace("\n", " ")
            snippets.append(t[:140])
    return " | ".join(snippets) if snippets else "—"

# =========================
# UI controls
# =========================
default_areas = ["Aundh, Pune", "Baner, Pune", "Wakad, Pune"]
areas = st.multiselect("Areas", options=default_areas, default=default_areas)

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

max_results_per_query = st.slider("Number of results per search", min_value=5, max_value=50, value=15)
include_reviews = st.checkbox("Show sample reviews", value=False)
threads = st.slider("Search speed (higher = faster, lower = safer)", min_value=1, max_value=10, value=6)

# =========================
# Run search
# =========================
if st.button("Find Doctors"):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    progress = st.progress(0)
    status = st.empty()

    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()

    total_queries = max(len(areas) * len(specialties), 1)
    completed_queries = 0

    for area in areas:
        for sp in specialties:
            query = f"{sp} in {area}"
            status.info(f"Searching: **{query}**")

            # ---- Text Search
            try:
                data = retry_request(text_search, query, page_token=None, page_size=max_results_per_query)
            except Exception as e:
                st.warning(f"Text search failed for '{query}': {e}")
                completed_queries += 1
                progress.progress(min(int(completed_queries / total_queries * 100), 100))
                continue

            places = (data.get("places") or [])[:max_results_per_query]

            # ---- Details in parallel
            with ThreadPoolExecutor(max_workers=threads) as ex:
                futures = {}
                for p in places:
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
                    except Exception as e:
                        st.info(f"Details failed for {p.get('id')}: {e}")
                        continue

                    name = (det.get("displayName") or {}).get("text", "")
                    addr = det.get("formattedAddress", "")
                    phone = det.get("internationalPhoneNumber") or det.get("nationalPhoneNumber") or ""
                    website = det.get("websiteUri", "") or ""
                    rating = det.get("rating")
                    count = det.get("userRatingCount")
                    summary = summarize_reviews(det.get("reviews", [])) if include_reviews else "—"

                    rows.append(
                        {
                            "Complete address": addr,
                            "Doctor name": name,
                            "Specialty": sp.title(),
                            "Clinic/Hospital": name,
                            "Contact number": phone,
                            "Website": website,
                            "Ratings": rating,
                            "Reviews": count,
                            "Summary ofPros and Cons recommendation": summary,
                            "Place ID": det.get("id"),
                        }
                    )

                    fetched += 1
                    status.write(f"Fetched details {fetched}/{len(places)} for **{query}**")

            completed_queries += 1
            progress.progress(min(int(completed_queries / total_queries * 100), 100))

    # ---- Output
    if not rows:
        st.error("No results fetched. Check API key, enabled APIs/billing, quotas, or try broader queries.")
    else:
        df = pd.DataFrame(rows)
        st.success(f"Done. {len(df)} rows.")
        st.dataframe(df, use_container_width=True)

        out_path = "pune_doctors_streamlit.xlsx"
        df.to_excel(out_path, index=False)
        with open(out_path, "rb") as f:
            st.download_button(
                label="Download Excel",
                data=f,
                file_name=out_path,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
