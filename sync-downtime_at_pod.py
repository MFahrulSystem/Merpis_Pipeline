import os
import requests
from supabase import create_client

MERPIS_API_TOKEN = os.getenv("MERPIS_API_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

TABLE_NAME = "downtime_at_pod"
BATCH_SIZE = 500

url = "https://merpis.ptmerahputih.com/api/downtime/4?year=2026&month=&customer=&tugboat=&barge="

if not MERPIS_API_TOKEN:
    raise Exception("MERPIS_API_TOKEN belum tersedia")

if not SUPABASE_URL:
    raise Exception("SUPABASE_URL belum tersedia")

if not SUPABASE_KEY:
    raise Exception("SUPABASE_KEY belum tersedia")

headers = {
    "Accept": "application/json",
    "Authorization": f"Bearer {MERPIS_API_TOKEN}"
}

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
        "actual_arrived_pod": row.get("arrivepod"),

        "standard_disc_complete": row.get("standarddischargecomplete"),
        "disc_complete": row.get("dischargecomplete"),
        "downtime_disc": row.get("downtimedischarge"),
        "days_disc": row.get("daysdischarge"),
        "department_disc": row.get("departmentdischarge"),
        "category_disc": row.get("categorydischarge"),
        "notes_disc": row.get("notesdischarge"),

        "standard_faw_to_finish": row.get("standardfawsailingtofinish"),
        "faw_to_finish": row.get("fawsailingtofinish"),
        "downtime_faw_to_finish": row.get("downtimefawsailingtofinish"),
        "days_faw_to_finish": row.get("daysfawsailingtofinish"),
        "department_faw_to_finish": row.get("departmentfawsailingtofinish"),
        "category_faw_to_finish": row.get("categoryfawsailingtofinish"),
        "notes_faw_to_finish": row.get("notesfawsailingtofinish")
    })

print("Total Transform Rows:", len(rows))

if len(rows) > 0:
    print("Contoh hasil transform:")
    print(rows[0])

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

for start in range(0, len(rows), BATCH_SIZE):
    end = start + BATCH_SIZE
    batch = rows[start:end]

    print(f"Upsert Batch {start} - {min(end, len(rows))}")

    result = supabase.table(TABLE_NAME).upsert(
        batch,
        on_conflict="id"
    ).execute()

    print("Batch berhasil:", len(batch))

print("Selesai upload downtime_at_pod ke Supabase")