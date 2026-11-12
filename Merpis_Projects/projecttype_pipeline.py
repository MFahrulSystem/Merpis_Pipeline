import os
import sys
import time
import pandas as pd
import requests
from supabase import create_client

API_URL = "https://merpis.ptmerahputih.com/api/projecttype"
TABLE = "project_types"
RETRIES = 3

MERPIS_TOKEN = os.environ.get("MERPIS_TOKEN_PROJECTTYPE") or os.environ["MERPIS_TOKEN_PROJECTS"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]  # service_role key

COLS = ["id", "name", "code", "active", "created_at", "updated_at"]
TS_COLS = ["created_at", "updated_at"]


def fetch() -> pd.DataFrame:
    headers = {"Accept": "application/json", "Authorization": f"Bearer {MERPIS_TOKEN}"}
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(API_URL, headers=headers, timeout=(15, 120))
            r.raise_for_status()
            data = r.json()
            break
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            print(f"Percobaan {attempt}/{RETRIES} gagal: {type(e).__name__}")
            time.sleep(10 * attempt)
    else:
        raise RuntimeError(f"API tidak merespons setelah {RETRIES} percobaan: {last_err}")
    return pd.json_normalize(data["data"]["projecttype"])


def clean(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d.columns = [c.strip().lower() for c in d.columns]
    for c in COLS:
        if c not in d.columns:
            d[c] = None
    d = d[COLS].copy()

    d["id"] = pd.to_numeric(d["id"], errors="coerce").astype("Int64")
    d["active"] = pd.to_numeric(d["active"], errors="coerce").astype("Int64")
    for c in ["name", "code"]:
        d[c] = d[c].apply(lambda x: x.strip() if isinstance(x, str) else x).astype("object")
    for c in TS_COLS:   # UTC -> WIB, tanpa timezone
        s = pd.to_datetime(d[c], errors="coerce", utc=True)
        d[c] = s.dt.tz_convert("Asia/Jakarta").dt.tz_localize(None).dt.strftime("%Y-%m-%d %H:%M:%S")

    d = d.dropna(subset=["id"]).drop_duplicates(subset="id", keep="first")
    return d.sort_values("id").reset_index(drop=True)


def to_records(d: pd.DataFrame) -> list:
    d = d.astype(object).where(d.notna(), None)
    records = d.to_dict(orient="records")
    for r in records:
        for k in ("id", "active"):
            if r[k] is not None:
                r[k] = int(r[k])
    return records


def main() -> None:
    raw = fetch()
    print("Raw:", raw.shape)
    d = clean(raw)
    print("Clean:", d.shape)
    if d.empty:
        raise RuntimeError("Data kosong, upsert dibatalkan")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    sb.table(TABLE).upsert(to_records(d), on_conflict="id").execute()
    print(f"Upsert {len(d)} baris ke {TABLE}")
    print("Selesai.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e)
        sys.exit(1)