import os
import sys
import time
import numpy as np
import pandas as pd
import requests
from supabase import create_client

API_URL = "https://merpis.ptmerahputih.com/api/fuelbudget"
TABLE_BUDGET = "fuelbudget"
TABLE_DAILY = "fuelbudget_dailies"
BATCH = 500
READ_TIMEOUT = 300
RETRIES = 3

MERPIS_TOKEN = (os.environ.get("MERPIS_TOKEN_FUEL") or os.environ.get("MERPIS_TOKEN")
                or os.environ["MERPIS_TOKEN_PROJECTS"])
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]  # service_role key

# ---------- Tabel 1: fuelbudget (1 baris per budget) ----------
B_BIGINT = ["project", "active", "shiftingmaster", "auxiliarymaster", "sailingtopolmaster",
            "sailingtopodmaster", "tugboat", "barge"]
B_NUMERIC = ["volume", "shifting", "auxiliary", "sailingtopol", "sailingtopod", "etc",
             "sailtopol", "sailtopod", "prevdischarge", "loading", "discharge",
             "shiftinglph", "auxiliarylph", "sailtopollph", "sailtopodlph", "others",
             "budgetsyncel_budget", "budgetsyncel_consumption", "budgetsyncel_overliter",
             "budgetsyncel_overpercentage", "budgetsyncel_overamount",
             "budgetsyncel_fuelremaining"]
B_TS = ["created_at", "updated_at"]
B_TEXT = ["created_by", "otherstext", "budgetsyncel_status"]
B_COLS = ["id"] + B_BIGINT + B_NUMERIC + B_TS + B_TEXT

# ---------- Tabel 2: fuelbudget_dailies (dailiesel, 1 baris per catatan harian) ----------
D_BIGINT = ["budget", "lastactivity", "active"]
D_NUMERIC = ["volume", "price"]
D_TS = ["created_at", "updated_at", "date", "approval_at"]
D_TEXT = ["rob", "remarks", "created_by", "destination", "approval_status", "approval_by",
          "approval_remarks", "trfreference", "code"]
D_COLS = ["id"] + D_BIGINT + D_NUMERIC + D_TS + D_TEXT


def extract_records(body):
    cur = body
    for _ in range(3):
        if isinstance(cur, list):
            return cur
        if isinstance(cur, dict) and "data" in cur:
            cur = cur["data"]
        else:
            break
    raise RuntimeError("Tidak menemukan list data di respons API")


def fetch() -> list:
    headers = {"Accept": "application/json", "Authorization": f"Bearer {MERPIS_TOKEN}"}
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            t0 = time.time()
            r = requests.get(API_URL, headers=headers, timeout=(15, READ_TIMEOUT))
            r.raise_for_status()
            body = r.json()
            print(f"Fetch OK (percobaan {attempt}, {time.time() - t0:.0f}s, {len(r.content) / 1e6:.2f} MB)")
            break
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            print(f"Percobaan {attempt}/{RETRIES} gagal: {type(e).__name__}")
            time.sleep(10 * attempt)
    else:
        raise RuntimeError(f"API tidak merespons setelah {RETRIES} percobaan: {last_err}")

    if isinstance(body, dict) and any(k in body for k in ("links", "meta", "next_page_url")):
        print("PERINGATAN: respons berisi paginasi, cek apakah semua halaman sudah terambil")
    return extract_records(body)


def split_records(records: list):
    """Pisahkan budget (tanpa nested projectel/tugboatel/bargeel) dan dailies."""
    budgets, dailies = [], []
    for rec in records:
        row = {k: v for k, v in rec.items()
               if k not in ("projectel", "tugboatel", "bargeel", "dailiesel", "budgetsyncel")}
        sync = rec.get("budgetsyncel") or {}
        for k in ("budget", "consumption", "overliter", "overpercentage",
                  "overamount", "status", "fuelremaining"):
            row[f"budgetsyncel_{k}"] = sync.get(k)
        budgets.append(row)
        for dly in rec.get("dailiesel") or []:
            item = dict(dly)
            item.setdefault("budget", rec.get("id"))
            dailies.append(item)
    return pd.DataFrame(budgets), pd.DataFrame(dailies)


def to_wib(s: pd.Series) -> pd.Series:
    """Nilai berakhiran Z (UTC) -> WIB. Nilai tanpa zona waktu dianggap sudah WIB dan tidak diubah."""
    s = s.astype("object")
    is_z = s.notna() & s.astype(str).str.endswith("Z")
    z = pd.to_datetime(s.where(is_z), errors="coerce", utc=True).dt.tz_convert("Asia/Jakarta").dt.tz_localize(None)
    n = pd.to_datetime(s.where(~is_z), errors="coerce")
    return z.fillna(n).dt.strftime("%Y-%m-%d %H:%M:%S")


def clean(df: pd.DataFrame, cols, bigint, numeric, ts, text) -> pd.DataFrame:
    d = df.copy()
    d.columns = [c.strip().lower().replace(".", "_").replace(" ", "_") for c in d.columns]

    for c in d.select_dtypes(include=["object", "string"]).columns:
        d[c] = d[c].apply(lambda x: x.strip() if isinstance(x, str) else x)
        d[c] = d[c].replace({"": np.nan, "None": np.nan, "none": np.nan,
                             "null": np.nan, "NaN": np.nan})

    for c in cols:
        if c not in d.columns:
            d[c] = np.nan
    d = d[cols].copy()

    d["id"] = pd.to_numeric(d["id"], errors="coerce").astype("Int64")
    for c in bigint:
        d[c] = pd.to_numeric(d[c], errors="coerce").astype("Int64")
    for c in numeric:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    for c in ts:
        d[c] = to_wib(d[c])
    for c in text:
        d[c] = d[c].astype("object")

    d = d.dropna(subset=["id"]).drop_duplicates(subset="id", keep="first")
    return d.sort_values("id", ascending=False).reset_index(drop=True)


def to_records(d: pd.DataFrame, bigint, numeric) -> list:
    d = d.astype(object).where(d.notna(), None)
    records = d.to_dict(orient="records")
    for r in records:
        for k in ["id"] + bigint:
            if r[k] is not None:
                r[k] = int(r[k])
        for k in numeric:
            if r[k] is not None:
                r[k] = float(r[k])
    return records


def upsert(sb, table: str, records: list) -> None:
    for i in range(0, len(records), BATCH):
        sb.table(table).upsert(records[i:i + BATCH], on_conflict="id").execute()
        print(f"{table}: upsert {min(i + BATCH, len(records))}/{len(records)}")


def main() -> None:
    records = fetch()
    df_b, df_d = split_records(records)
    print("Raw budget:", df_b.shape, "| raw dailies:", df_d.shape)

    d_b = clean(df_b, B_COLS, B_BIGINT, B_NUMERIC, B_TS, B_TEXT)
    d_d = clean(df_d, D_COLS, D_BIGINT, D_NUMERIC, D_TS, D_TEXT)
    print("Clean budget:", d_b.shape, "| clean dailies:", d_d.shape)
    if d_b.empty:
        raise RuntimeError("Data fuelbudget kosong, upsert dibatalkan")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    upsert(sb, TABLE_BUDGET, to_records(d_b, B_BIGINT, B_NUMERIC))
    if not d_d.empty:
        upsert(sb, TABLE_DAILY, to_records(d_d, D_BIGINT, D_NUMERIC))
    print("Selesai.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e)
        sys.exit(1)