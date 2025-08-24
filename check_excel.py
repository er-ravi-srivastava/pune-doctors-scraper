# check_excel.py
import pandas as pd, sys

EXPECTED_COLS = [
    "Complete address","Doctors name","Specialty","Clinic/Hospital","Years of experience",
    "Contact number","Contact email","Ratings","Reviews",
    "Summary of Pros and Cons (Summary of reviews), and recommendation"
]

def main(path):
    df = pd.read_excel(path)
    missing = [c for c in EXPECTED_COLS if c not in df.columns]
    extra = [c for c in df.columns if c not in EXPECTED_COLS]
    print("Rows:", len(df))
    print("Missing columns:", missing or "None")
    print("Extra columns:", extra or "None")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python check_excel.py <excel_path>")
    else:
        main(sys.argv[1])
