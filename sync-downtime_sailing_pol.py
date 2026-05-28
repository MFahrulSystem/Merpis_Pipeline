import os
import requests
import pandas as pd
from supabase import create_client

# =========================
# CONFIG
# =========================
MERPIS_API_TOKEN = os.getenv("MERPIS_API_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

TABLE_NAME = "downtime_sailing_pol"
BATCH_SIZE = 500

url = "https://merpis.ptmerahputih.com/api/downtime/1?year=&month=&customer=&tugboat=&barge="

headers = {
    "Accept": "application/json",
    "Authorization": f"Bearer {MERPIS_API_TOKEN}"
}

# =========================
# VALIDASI ENV
# =========================
if not MERPIS_API_TOKEN:
    raise Exception("MERPIS_API_TOKEN belum tersedia di GitHub Secrets")

if not SUPABASE_URL:
    raise Exception("SUPABASE_URL belum tersedia di GitHub Secrets")

if not SUPABASE_KEY:
    raise Exception("SUPABASE_KEY belum tersedia di GitHub Secrets")

# =========================
# REQUEST API
# =========================
response = requests.get(
    url,
    headers=headers,
    timeout=300
)

print("Status Code:", response.status_code)

if response.status_code != 200:
    print(response.text)
    raise Exception("Gagal mengambil data API Merpis")

json_data = response.json()
data = json_data["data"]["data"]

print("Total Raw Data:", len(data))

# =========================
# TRANSFORM DATA
# =========================
rows = []

for row in data:
    projectel = row.get("projectel") or {}

    tugboat = projectel.get("tugboatel") or {}
    barge = projectel.get("bargeel") or {}
    customer = projectel.get("customerel") or {}

    downtime_days = None

    if row.get("downtime"):
        td = pd.to_timedelta(row.get("downtime"), errors="coerce")

        if not pd.isna(td):
            downtime_days = round(td.total_seconds() / 86400, 6)

    rows.append({
        "id": row.get("id"),
        "year": row.get("year"),
        "month": row.get("month"),
        "code": projectel.get("code"),
        "tugboat": tugboat.get("name"),
        "barge": barge.get("name"),
        "customer": customer.get("fullname"),
        "eta_arrived_pol": row.get("etaarrivepol"),
        "actual_arrived_pol": row.get("actualarrivepol"),
        "downtime": row.get("downtime"),
        "downtime_days": downtime_days,
        "department": row.get("department"),
        "category": row.get("category"),
        "notes": row.get("notes")
    })

print("Total Transform Rows:", len(rows))

# =========================
# UPSERT TO SUPABASE
# =========================
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

for start in range(0, len(rows), BATCH_SIZE):
    end = start + BATCH_SIZE
    batch = rows[start:end]

    print(f"Upsert Batch {start} - {min(end, len(rows))}")

    supabase.table(TABLE_NAME).upsert(
        batch,
        on_conflict="id"
    ).execute()

    print("Batch berhasil:", len(batch))

print("Selesai upload downtime_sailing_pol ke Supabase")