import os
import sys
import time
import numpy as np
import pandas as pd
import requests
from supabase import create_client

API_URL = "https://merpis.ptmerahputih.com/api/port"
TABLE = "ports"
BATCH = 500
READ_TIMEOUT = 120
RETRIES = 3

MERPIS_TOKEN = os.environ.get("MERPIS_TOKEN_PORT") or os.environ["MERPIS_TOKEN_PROJECTS"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]  # service_role key

BIGINT_COLS = ["active"]
TS_COLS = ["created_at", "updated_at"]
TEXT_COLS = ["name", "shortname"]

ALL_COLS = ["id"] + BIGINT_COLS + TS_COLS + TEXT_COLS


def find_records(obj, path="root"):
    """Cari list of dict di level mana pun, kembalikan yang paling besar."""
    found = []
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        found.append((path, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            found += find_records(v, f"{path}.{k}")
    return found


def fetch() -> pd.DataFrame:
    headers = {"Accept": "application/json", "Authorization": f"Bearer {MERPIS_TOKEN}"}
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            t0 = time.time()
            r = requests.get(API_URL, headers=headers, timeout=(15, READ_TIMEOUT))
            r.raise_for_status()
            data = r.json()
            print(f"Fetch OK (percobaan {attempt}, {time.time() - t0:.0f}s, {len(r.content) / 1e6:.2f} MB)")
            break
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            print(f"Percobaan {attempt}/{RETRIES} gagal: {type(e).__name__}")
            time.sleep(10 * attempt)
    else:
        raise RuntimeError(f"API tidak merespons setelah {RETRIES} percobaan: {last_err}")

    candidates = find_records(data)
    if not candidates:
        raise RuntimeError("Tidak ada list data di respons API")
    path, records = max(candidates, key=lambda x: len(x[1]))
    print("Sumber data:", path, "|", len(records), "item")
    return pd.json_normalize(records)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d.columns = [c.strip().lower().replace(".", "_").replace(" ", "_") for c in d.columns]

    # string: trim, kosong/None/null -> NaN
    for c in d.select_dtypes(include=["object", "string"]).columns:
        d[c] = d[c].apply(lambda x: x.strip() if isinstance(x, str) else x)
        d[c] = d[c].replace({"": np.nan, "None": np.nan, "none": np.nan,
                             "null": np.nan, "NaN": np.nan})

    # pastikan semua kolom ada, buang yang tidak ada di tabel
    for c in ALL_COLS:
        if c not in d.columns:
            d[c] = np.nan
    d = d[ALL_COLS].copy()

    d["id"] = pd.to_numeric(d["id"], errors="coerce").astype("Int64")
    for c in BIGINT_COLS:
        d[c] = pd.to_numeric(d[c], errors="coerce").astype("Int64")
    for c in TS_COLS:        # UTC -> WIB, tanpa timezone
        s = pd.to_datetime(d[c], errors="coerce", utc=True)
        d[c] = s.dt.tz_convert("Asia/Jakarta").dt.tz_localize(None).dt.strftime("%Y-%m-%d %H:%M:%S")
    for c in TEXT_COLS:
        d[c] = d[c].astype("object")

    d = d.dropna(subset=["id"]).drop_duplicates(subset="id", keep="first")
    return d.sort_values("id", ascending=False).reset_index(drop=True)


def to_records(d: pd.DataFrame) -> list:
    d = d.astype(object).where(d.notna(), None)
    records = d.to_dict(orient="records")
    int_cols = ["id"] + BIGINT_COLS
    for r in records:
        for k in int_cols:
            if r[k] is not None:
                r[k] = int(r[k])
    return records


def upsert(records: list) -> None:
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    for i in range(0, len(records), BATCH):
        sb.table(TABLE).upsert(records[i:i + BATCH], on_conflict="id").execute()
        print(f"Upsert {min(i + BATCH, len(records))}/{len(records)}")


def main() -> None:
    raw = fetch()
    print("Raw:", raw.shape, "| id max:", raw["id"].max())
    d = clean(raw)
    print("Clean:", d.shape)
    if d.empty:
        raise RuntimeError("Data kosong, upsert dibatalkan")
    upsert(to_records(d))
    print("Selesai.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e)
        sys.exit(1)