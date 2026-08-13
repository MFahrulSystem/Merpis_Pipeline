import os
import requests
import pandas as pd
from supabase import create_client

MERPIS_API_TOKEN = os.getenv("MERPIS_API_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

TABLE_NAME = "downtime_sailing_pod"
BATCH_SIZE = 500

url = "https://merpis.ptmerahputih.com/api/downtime/3?year=&month=&customer=&tugboat=&barge="

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
        "eta_arrived_pod": row.get("etaarrivepod"),
        "actual_arrived_pod": row.get("actualarrivepod"),
        "downtime": row.get("downtime"),
        "downtime_days": downtime_days,
        "department": row.get("department"),
        "category": row.get("category"),
        "notes": row.get("notes")
    })

print("Total Transform Rows:", len(rows))

# =========================
# CLEAN-THEN-INSERT TO SUPABASE
# =========================
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===== TAHAP 0: VALIDASI DATA BARU SEBELUM MELAKUKAN PERUBAHAN APAPUN =====
# Kalau Merpis error / balas data kosong / datanya anjlok drastis,
# JANGAN sampai proses lanjut menghapus data lama di Supabase.
MIN_EXPECTED_ROWS = 1          # minimal harus ada data, jangan nol
DROP_THRESHOLD_RATIO = 0.5     # kalau data baru < 50% dari data lama, dianggap mencurigakan

if len(rows) < MIN_EXPECTED_ROWS:
    raise Exception(
        "Data dari Merpis kosong (0 baris). Kemungkinan Merpis sedang error. "
        "Proses dihentikan, data di Supabase TIDAK diubah."
    )

# Ambil jumlah data lama di Supabase sebagai pembanding & backup
old_data_result = supabase.table(TABLE_NAME).select("*").execute()
old_rows = old_data_result.data
old_count = len(old_rows)

print("Total data lama di Supabase:", old_count)
print("Total data baru dari Merpis:", len(rows))

if old_count > 0 and len(rows) < old_count * DROP_THRESHOLD_RATIO:
    raise Exception(
        f"Data baru dari Merpis ({len(rows)} baris) turun drastis dibanding "
        f"data lama di Supabase ({old_count} baris). Kemungkinan Merpis error "
        f"atau mengembalikan data tidak lengkap. Proses dihentikan, "
        f"data di Supabase TIDAK diubah."
    )

# ===== TAHAP 1: BERSIHKAN SEMUA DATA LAMA (CLEAN SHEET) =====
# Supaya data yang sudah dihapus di sumber (Merpis) ikut terhapus di Supabase,
# bukan cuma insert/update seperti upsert.
print(f"Menghapus seluruh data lama di tabel {TABLE_NAME} ...")

try:
    supabase.table(TABLE_NAME).delete().gte("id", 0).execute()
    print("Data lama berhasil dihapus.")
except Exception as e:
    raise Exception(f"Gagal menghapus data lama, proses dihentikan: {e}")

# ===== TAHAP 2: INSERT DATA BARU PER BATCH =====
try:
    for start in range(0, len(rows), BATCH_SIZE):
        end = start + BATCH_SIZE
        batch = rows[start:end]

        print(f"Insert Batch {start} - {min(end, len(rows))}")

        supabase.table(TABLE_NAME).insert(batch).execute()

        print("Batch berhasil:", len(batch))

    print("Selesai upload downtime_sailing_pod ke Supabase")

except Exception as e:
    # ===== ROLLBACK: kalau insert gagal di tengah jalan, kembalikan data lama =====
    print(f"Insert gagal di tengah proses: {e}")
    print("Mencoba rollback dengan mengembalikan data lama...")

    try:
        if old_count > 0:
            for start in range(0, len(old_rows), BATCH_SIZE):
                end = start + BATCH_SIZE
                batch = old_rows[start:end]
                supabase.table(TABLE_NAME).insert(batch).execute()
            print("Rollback berhasil, data lama dikembalikan.")
        else:
            print("Tidak ada data lama untuk di-rollback (tabel sebelumnya memang kosong).")
    except Exception as rollback_error:
        print(f"ROLLBACK GAGAL: {rollback_error}")
        print("PERHATIAN: tabel Supabase kemungkinan dalam kondisi tidak lengkap. Cek manual!")

    raise Exception(f"Proses upload gagal, lihat log di atas. Error asli: {e}")