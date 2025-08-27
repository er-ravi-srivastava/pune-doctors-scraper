🏥 Pune Doctors Scraper

A Python-based tool to collect publicly available information about doctors and clinics in Pune using the Google Places API.
The results can be explored through a Streamlit web app or exported into an Excel file for further use.

👉 Live demo: doctor-finder-pune.streamlit.app


🎯 Objective

The goal of this project is to make it easy to:

Search doctors/clinics in Pune by area and specialty.

Get structured contact and profile details (address, phone, rating, reviews, etc.).

Export results into a clean Excel file for offline reference.

This can help patients, healthcare analysts, or businesses looking to explore healthcare options in Pune.


✨ Features

✅ Search Pune doctors by area (e.g., Aundh, Baner, Wakad).
✅ Filter by specialty (cardiologist, pediatrician, dermatologist, etc.).
✅ Collect extra info such as ratings, reviews, phone numbers, and websites.
✅ Export clean data to Excel (.xlsx).
✅ Two ways to use:

Command Line (CLI) → scraper.py

Web App (Streamlit) → app.py


⚙️ Installation

Clone the repository and install dependencies:
git clone https://github.com/er-ravi-srivastava/pune-doctors-scraper.git
cd pune-doctors-scraper
pip install -r requirements.txt


🚀 How to Run
1️⃣ Run the Streamlit Web App
streamlit run app.py
This launches a local web interface where you can:

Choose an area
Select specialties
Fetch and download doctor data
