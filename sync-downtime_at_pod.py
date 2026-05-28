import os
import requests
from supabase import create_client

MERPIS_API_TOKEN = os.getenv("MERPIS_API_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

TABLE_NAME = "downtime_at_pod"
BATCH_SIZE = 500

url = "https://merpis.ptmerahputih.com/api/downtime/4?year=2026&month=&customer=&tugboat=&barge="

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


def get_value(row, *keys):
    for key in keys:
        value = row.get(key)
        if value not in [None, ""]:
            return value
    return None


response = requests.get(url, headers=headers, timeout=300)

print("Status Code:", response.status_code)

if response.status_code != 200:
    print(response.text)
    raise Exception("Gagal mengambil data API")

json_data = response.json()
data = json_data["data"]["data"]

print("Total Raw Data:", len(data))

# Debug untuk cek nama field asli dari API
if len(data) > 0:
    print("Contoh key dari API:")
    print(data[0].keys())

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
        "actual_arrived_pod": get_value(row, "arrivepod", "arrive_pod", "actual_arrived_pod"),

        "standard_disc_complete": get_value(row, "standarddisccomplete", "standard_disc_complete", "std_disch_complete"),
        "disc_complete": get_value(row, "disccomplete", "disc_complete", "disch_complete"),
        "downtime_disc": get_value(row, "downtimedisc", "downtime_disc", "downtime_disch"),
        "days_disc": get_value(row, "daysdisc", "days_disc"),
        "department_disc": get_value(row, "departmentdisc", "department_disc", "department"),
        "category_disc": get_value(row, "categorydisc", "category_disc", "category"),
        "notes_disc": get_value(row, "notesdisc", "notes_disc", "notes"),

        "standard_faw_to_finish": get_value(row, "standardfawtofinish", "standard_faw_to_finish", "std_faw_to_finish"),
        "faw_to_finish": get_value(row, "fawtofinish", "faw_to_finish"),
        "downtime_faw_to_finish": get_value(row, "downtimefawtofinish", "downtime_faw_to_finish"),
        "days_faw_to_finish": get_value(row, "daysfawtofinish", "days_faw_to_finish"),
        "department_faw_to_finish": get_value(row, "departmentfawtofinish", "department_faw_to_finish"),
        "category_faw_to_finish": get_value(row, "categoryfawtofinish", "category_faw_to_finish"),
        "notes_faw_to_finish": get_value(row, "notesfawtofinish", "notes_faw_to_finish")
    })

print("Total Transform Rows:", len(rows))

# Debug isi hasil transform pertama
if len(rows) > 0:
    print("Contoh hasil transform:")
    print(rows[0])

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

print("Selesai upload downtime_at_pod ke Supabase")