# app.py
from __future__ import annotations
from crawler import crawl_doctor_site
import os, re, time, math, json, pathlib
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import streamlit as st

# =============== Page setup ===============
st.set_page_config(page_title="Search Doctors and Clinics in Pune", layout="wide")
st.title("Search Doctors and Clinics in Pune")

# =============== HTTP session ===============
SESSION = requests.Session()
_retries = Retry(total=3, backoff_factor=0.6,
                 status_forcelist=[429, 500, 502, 503, 504],
                 allowed_methods=["GET", "POST"], raise_on_status=False)
adapter = HTTPAdapter(max_retries=_retries, pool_connections=64, pool_maxsize=64)
SESSION.mount("https://", adapter); SESSION.mount("http://", adapter)
DEFAULT_TIMEOUT = 8

# =============== API key ===============
def load_api_key() -> Optional[str]:
    try:
        sec = st.secrets.get("api", {})
        if sec and "google_api_key" in sec: return sec["google_api_key"].strip()
    except Exception: pass
    try:
        from dotenv import load_dotenv; load_dotenv()
    except Exception: pass
    k = os.getenv("GOOGLE_API_KEY")
    return k.strip() if k else None

API_KEY = load_api_key()
if not API_KEY:
    st.error("Missing *GOOGLE_API_KEY*. Put it in `.streamlit/secrets.toml` or env.")
    st.stop()

# =============== Endpoints & fields ===============
TEXT_URL   = "https://places.googleapis.com/v1/places:searchText"
DETAIL_URL = "https://places.googleapis.com/v1/places/{place_id}"

TEXT_FIELDS = ",".join([
    "places.id","places.displayName","places.formattedAddress","places.types",
    "places.rating","places.userRatingCount","places.websiteUri",
    "places.nationalPhoneNumber","places.internationalPhoneNumber",
])
DETAIL_FIELDS_FAST = ",".join([
    "id","displayName","formattedAddress","types","websiteUri",
    "nationalPhoneNumber","internationalPhoneNumber","rating","userRatingCount",
])
DETAIL_FIELDS_FULL = DETAIL_FIELDS_FAST + ",reviews"

def _headers(mask: str) -> dict:
    return {"Content-Type":"application/json","X-Goog-Api-Key":API_KEY,"X-Goog-FieldMask":mask}

def _post_json(url: str, headers: dict, payload: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    r = SESSION.post(url, headers=headers, json=payload, timeout=timeout)
    if r.status_code >= 400:
        try: st.warning(r.json())
        except Exception: st.warning(r.text)
        r.raise_for_status()
    return r.json()

def _get_json(url: str, headers: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    r = SESSION.get(url, headers=headers, timeout=timeout)
    if r.status_code >= 400:
        try: st.warning(r.json())
        except Exception: st.warning(r.text)
        r.raise_for_status()
    return r.json()

# =============== Helpers ===============
def backoff_sleep(attempt: int) -> None: time.sleep(0.9 * (attempt + 1))

def retry_request(fn, *args, **kwargs):
    tries = kwargs.pop("tries", 3)
    for attempt in range(tries):
        try: return fn(*args, **kwargs)
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            if code in {429,500,502,503,504} and attempt < tries-1:
                backoff_sleep(attempt); continue
            raise
        except requests.RequestException:
            if attempt < tries-1: backoff_sleep(attempt); continue
            raise
    raise RuntimeError("Request failed after retries")

AREA_CENTERS: dict[str, Tuple[float,float]] = {
    "Aundh, Pune": (18.5606, 73.8077),
    "Baner, Pune": (18.5590, 73.7806),
    "Wakad, Pune": (18.5976, 73.7707),
}

def build_grid(center: Tuple[float,float], radius_m=2900, step_m=1800, size=4) -> List[Tuple[float,float]]:
    lat0,lng0 = center
    m_per_deg_lat = 111_320
    m_per_deg_lng = 111_320 * math.cos(math.radians(lat0))
    dlat, dlng = step_m/m_per_deg_lat, step_m/m_per_deg_lng
    offs = [i - (size-1)/2 for i in range(size)]
    pts = [(lat0+oy*dlat, lng0+ox*dlng) for oy in offs for ox in offs]
    if center not in pts: pts.append(center)
    return pts

def text_search_page(query: str, page_token: Optional[str]=None, page_size: int=20,
                     center: Optional[Tuple[float,float]]=None, radius_m: int=2900) -> Dict[str,Any]:
    page_size = max(1, min(page_size, 20))
    payload: Dict[str,Any] = {"textQuery": query, "pageSize": page_size}
    if page_token: payload["pageToken"] = page_token
    if center:
        lat,lng = center
        payload["locationBias"] = {"circle":{"center":{"latitude":lat,"longitude":lng},"radius":radius_m}}
    return _post_json(TEXT_URL, headers=_headers(TEXT_FIELDS), payload=payload)

def paginate_text_search(query: str, total_needed: int,
                         center: Optional[Tuple[float,float]]=None, radius_m: int = 2900) -> List[Dict[str,Any]]:
    results: List[Dict[str,Any]] = []; token=None; pages=0
    while len(results) < total_needed and pages < 25:
        remaining = total_needed - len(results)
        data = retry_request(text_search_page, query, page_token=token,
                             page_size=min(20,remaining), center=center, radius_m=radius_m)
        results.extend(data.get("places") or []); pages += 1
        token = data.get("nextPageToken")
        if not token: break
        time.sleep(1.5)
    return results[:total_needed]

@st.cache_data(ttl=7200, show_spinner=False)
def cached_place_details(place_id: str, want_reviews: bool) -> Dict[str,Any]:
    fields = DETAIL_FIELDS_FULL if want_reviews else DETAIL_FIELDS_FAST
    return _get_json(DETAIL_URL.format(place_id=place_id), headers=_headers(fields))

@st.cache_data(ttl=7200, show_spinner=False)
def cached_crawl_site(url: str) -> Dict[str,Any]:
    try: return crawl_doctor_site(url) or {}
    except Exception: return {}

def summarize_reviews(reviews: List[Dict[str,Any]]) -> str:
    if not reviews: return "N/A"
    snippets = []
    for rv in reviews[:5]:
        t = (rv.get("text") or {}).get("text","")
        if t: snippets.append(t.strip().replace("\n"," ")[:140])
    return " | ".join(snippets) if snippets else "N/A"

_DOCTOR_PAT = re.compile(r"\bDr\.?\s*[A-Z][A-Za-z.\- ]{1,60}", re.UNICODE)
_CLINIC_WORDS = ("clinic","hospital","medical","centre","center","diagnostic","labs","skin","laser","hair","institute","speciality")

def split_doctor_and_clinic(place_name: str) -> Tuple[str,str]:
    if not place_name: return "N/A","N/A"
    name = place_name.strip(); low = name.lower()
    m = _DOCTOR_PAT.search(name)
    if m:
        doc = m.group(0).strip(" -|,"); rest = (name[:m.start()] + name[m.end():]).strip(" -|,")
        clinic = rest if (rest and any(w in rest.lower() for w in _CLINIC_WORDS)) else "N/A"
        return doc, clinic
    if any(w in low for w in _CLINIC_WORDS): return "N/A",name
    return "N/A",name

def make_recommendation(rating: Optional[float], count: Optional[int]) -> str:
    if rating is None or count is None or count == 0: return "Insufficient data"
    if rating >= 4.5 and count >= 50: return "Highly recommended"
    if rating >= 4.0 and count >= 10: return "Recommended"
    return "Consider with caution"

def _get_display_name(det: Dict[str,Any], p: Dict[str,Any]) -> str:
    dn = det.get("displayName")
    if isinstance(dn, dict): return dn.get("text","") or ""
    if isinstance(dn, str): return dn
    return (p.get("displayName") or {}).get("text","") if isinstance(p.get("displayName"), dict) else p.get("displayName") or ""

# =============== UI (assignment-specific) ===============
with st.sidebar:
    st.header("Filters")
    area = st.selectbox("Area", list(AREA_CENTERS.keys()), index=0)

    # EXACT specialties from the PDF (case + parentheses preserved)
    specialties = st.multiselect(
        "Specialties (assignment)",
        [
            "Cardiology (heart)",
            "Dermatology (skin)",
            "Neurology (brain and nervous system)",
            "Oncology (cancer)",
            "General surgery",
            "Orthopaedics",
            "Neurosurgery",
            "Paediatrics (child health)",
            "Obstetrics/gynaecology (women's health)",
            "Psychiatry (mental health)",
        ],
        default=["Dermatology (skin)", "Paediatrics (child health)", "Cardiology (heart)"],
    )

    target_total = st.slider("Target results per area", 50, 600, 200, step=25)

    preset = st.radio("Speed preset", ["Turbo (max)", "Balanced", "Careful"], index=1)
    run = st.button("Find Doctors")

if preset == "Turbo (max)":
    fast_mode = True
    details_threads, crawl_threads = 32, 12
    grid_size, grid_radius, grid_step = 4, 2900, 1800
elif preset == "Balanced":
    fast_mode = True
    details_threads, crawl_threads = 20, 8
    grid_size, grid_radius, grid_step = 3, 3200, 2200
else:
    fast_mode = False
    details_threads, crawl_threads = 12, 6
    grid_size, grid_radius, grid_step = 3, 3500, 2500

# =============== Run search ===============
if run:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    progress = st.progress(0); status = st.empty()
    rows: List[Dict[str,Any]] = []; seen: set[str] = set()

    center = AREA_CENTERS.get(area)
    grid_points = build_grid(center, radius_m=grid_radius, step_m=grid_step, size=grid_size) if center else [None]

    def phrases(sp: str) -> List[str]:
        # map the exact specialty strings to search phrases - keep simple, include short forms
        mapping = {
            "Cardiology (heart)": ["cardiology","cardiologist","heart clinic"],
            "Dermatology (skin)": ["dermatology","dermatologist","skin clinic","cosmetology"],
            "Neurology (brain and nervous system)": ["neurology","neurologist","neuro clinic"],
            "Oncology (cancer)": ["oncology","oncologist","cancer centre"],
            "General surgery": ["general surgery","general surgeon","surgery"],
            "Orthopaedics": ["orthopaedics","orthopaedic","bone clinic","joint replacement"],
            "Neurosurgery": ["neurosurgery","neurosurgeon"],
            "Paediatrics (child health)": ["paediatrics","paediatrician","child specialist"],
            "Obstetrics/gynaecology (women's health)": ["obstetrics","gynaecology","obgyn","obstetrician"],
            "Psychiatry (mental health)": ["psychiatry","psychiatrist"],
        }
        keys = mapping.get(sp, [sp])
        phrases = []
        for k in keys:
            phrases.extend([f"{k} in {area}", f"{k} doctor {area}", f"{k} clinic {area}", f"{k} hospital {area}"])
        return phrases

    combos: List[Tuple[str,str,Optional[Tuple[float,float]]]] = []
    for sp in specialties:
        for phr in phrases(sp):
            for gp in grid_points:
                combos.append((sp, phr, gp))

    fetched_places: List[Dict[str,Any]] = []
    combo_idx = 0

    cache_file = pathlib.Path(f".cache_{area.split(',')[0].lower()}.json")
    try:
        cached_ids = set(json.loads(cache_file.read_text()))
    except Exception:
        cached_ids = set()

    while len(fetched_places) < target_total and combo_idx < len(combos):
        sp, phr, gp = combos[combo_idx]; combo_idx += 1
        status.info(f"Searching: *{phr}* @ {gp or 'no-bias'} — {len(fetched_places)}/{target_total}")
        try:
            batch = paginate_text_search(phr, total_needed=40, center=gp, radius_m=grid_radius)
        except Exception as e:
            st.warning(f"Text search failed for '{phr}': {e}"); continue

        for p in batch:
            pid = p.get("id")
            if pid and pid not in seen and pid not in cached_ids:
                seen.add(pid); fetched_places.append(p)
                if len(fetched_places) >= target_total: break

        progress.progress(min(95, int(len(fetched_places)/max(1,target_total)*100)))

    if fetched_places:
        cached_ids.update([p["id"] for p in fetched_places if p.get("id")])
        try: cache_file.write_text(json.dumps(sorted(cached_ids)))
        except Exception: pass

    if not fetched_places:
        st.error("No results fetched. Try Balanced preset or add more specialties.")
    else:
        # details
        with ThreadPoolExecutor(max_workers=details_threads) as ex:
            futures = {ex.submit(retry_request, cached_place_details, p["id"], not fast_mode, tries=3): p
                       for p in fetched_places if p.get("id")}
            enriched = []; processed = 0
            for fut in as_completed(futures):
                p = futures[fut]
                try: det = fut.result()
                except Exception as e:
                    st.info(f"Details failed for {p.get('id')}: {e}"); continue
                website = det.get("websiteUri") or p.get("websiteUri") or "N/A"
                enriched.append((p, det, website))
                processed += 1
                status.write(f"Fetched details {processed}/{len(fetched_places)}")

        # crawls
        def crawl_or_empty(url):
            if url and url != "N/A": return cached_crawl_site(url) or {}
            return {}
        with ThreadPoolExecutor(max_workers=12) as ex2:
            crawl_map = {ex2.submit(crawl_or_empty, web): (p, det, web) for (p, det, web) in enriched}
            for fut in as_completed(crawl_map):
                p, det, website = crawl_map[fut]
                extra = {}
                try: extra = fut.result()
                except Exception: pass

                place_name = _get_display_name(det, p)
                doc_name, clinic_name = split_doctor_and_clinic(place_name)
                addr = det.get("formattedAddress","") or p.get("formattedAddress") or "N/A"
                phone = det.get("internationalPhoneNumber") or det.get("nationalPhoneNumber") \
                        or p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber") or "N/A"
                rating = det.get("rating"); count = det.get("userRatingCount")
                summary = "N/A" if fast_mode else (summarize_reviews(det.get("reviews",[])) or "N/A")
                recommendation = make_recommendation(rating, count)
                combined_summary = (summary if summary and summary!="N/A" else "")
                if recommendation: combined_summary = (combined_summary + "\n\nRecommendation: " + recommendation).strip()
                if not combined_summary: combined_summary = "N/A"

                contact_email = extra.get("email") or "N/A"
                y = extra.get("years_of_experience"); years_exp = str(y) if isinstance(y,int) else (y or "N/A")
                sp_guess = (p.get("types") or ["N/A"])[0]

                # *** EXACT column names from PDF ***
                rows.append({
                    "Complete address": addr,
                    "Doctors name": doc_name if doc_name else "N/A",
                    "Specialty": sp_guess.title(),
                    "Clinic/Hospital": clinic_name if clinic_name else "N/A",
                    "Years of experience": years_exp,
                    "Contact number": phone,
                    "Contact email": contact_email,
                    "Ratings": rating if rating is not None else "N/A",
                    "Reviews": count if count is not None else "N/A",
                    "Summary of Pros and Cons (Summary of reviews), and recommendation": combined_summary,
                })

        progress.progress(100)

        if not rows:
            st.error("No rows built. Try again.")
        else:
            df = pd.DataFrame(rows)
            # enforce exact column order from PDF
            expected_cols = [
                "Complete address",
                "Doctors name",
                "Specialty",
                "Clinic/Hospital",
                "Years of experience",
                "Contact number",
                "Contact email",
                "Ratings",
                "Reviews",
                "Summary of Pros and Cons (Summary of reviews), and recommendation",
            ]
            df = df.reindex(columns=expected_cols)
            st.success(f"Done. {len(df)} rows for {area}.")
            st.dataframe(df, use_container_width=True, hide_index=True)
            out_path = f"{area.split(',')[0].lower()}_doctors_assignment.xlsx".replace(" ", "")
            df.to_excel(out_path, index=False)
            with open(out_path, "rb") as f:
                st.download_button("Download Excel", f, file_name=out_path,
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
