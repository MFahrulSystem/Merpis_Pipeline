import os
import requests
import pandas as pd
from supabase import create_client

MERPIS_API_TOKEN = os.getenv("MERPIS_API_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

TABLE_NAME = "downtime_at_pol"
BATCH_SIZE = 500

url = "https://merpis.ptmerahputih.com/api/downtime/2?year=&month=&customer=&tugboat=&barge="

headers = {
    "Accept": "application/json",
    "Authorization": f"Bearer {MERPIS_API_TOKEN}"
}

if not MERPIS_API_TOKEN:
    raise Exception("MERPIS_API_TOKEN belum tersedia")

if not SUPABASE_URL:
    raise Exception("SUPABASE_URL belum tersedia")

if not SUPABASE_KEY:
    raise Exception("SUPABASE_KEY belum tersedia")

response = requests.get(url, headers=headers, timeout=300)

print("Status Code:", response.status_code)

if response.status_code != 200:
    print(response.text)
    raise Exception("Gagal mengambil data API")

json_data = response.json()
data = json_data["data"]["data"]

print("Total Raw Data:", len(data))

rows = []

for row in data:
    projectel = row.get("projectel") or {}
    tugboat = projectel.get("tugboatel") or {}
    barge = projectel.get("bargeel") or {}
    customer = projectel.get("customerel") or {}

    rows.append({
        "id": row.get("id"),
        "year": row.get("year"),
        "month": row.get("month"),

        "code": projectel.get("code"),
        "tugboat": tugboat.get("name"),
        "barge": barge.get("name"),
        "customer": customer.get("fullname"),

        "prorata": row.get("prorata"),
        "actual_arrived_pol": row.get("arrivepol"),

        "standard_skab_lhv": row.get("standardskablhv"),
        "skab_lhv": row.get("skablhv"),
        "downtime_skab_lhv": row.get("downtimeskablhv"),
        "days_skab_lhv": row.get("dayskablhv"),
        "department_skab_lhv": row.get("departmentskablhv"),
        "category_skab_lhv": row.get("categoryskablhv"),
        "notes_skab_lhv": row.get("notesskablhv"),

        "standard_faw_to_pod": row.get("standardfawsailingtopod"),
        "faw_to_pod": row.get("fawsailingtopod"),
        "downtime_faw_to_pod": row.get("downtimefawsailingtopod"),
        "days_faw_to_pod": row.get("daysfawsailingtopod"),
        "department_faw_to_pod": row.get("departmentfawsailingtopod"),
        "category_faw_to_pod": row.get("categoryfawsailingtopod"),
        "notes_faw_to_pod": row.get("notesfawsailingtopod")
    })

print("Total Transform Rows:", len(rows))

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

print("Selesai upload downtime_at_pol ke Supabase")