import os
import sys
import numpy as np
import pandas as pd
import requests
from supabase import create_client

API_URL = "https://merpis.ptmerahputih.com/api/fuelbudget"
TABLE = "fuelbudget"
BATCH = 500

MERPIS_TOKEN = os.environ["MERPIS_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]  # service_role key

BIGINT_COLS = [
    "project", "shiftingmaster", "auxiliarymaster", "sailingtopolmaster",
    "sailingtopodmaster", "tugboat", "barge", "bargeel_id", "budgetsyncel_id",
    "projectel_id", "projectel_barge", "projectel_tugboat", "projectel_customer",
    "projectel_cargo", "projectel_project", "projectel_active", "projectel_podprevious",
    "projectel_pol", "projectel_pod", "projectel_ondemmurage", "projectel_bargeel_id",
    "projectel_cargoel_id", "projectel_customerel_id", "projectel_podel_id",
    "projectel_podpreviousel_id", "projectel_polel_id", "projectel_tugboatel_id",
    "tugboatel_id",
]

NUMERIC_COLS = [
    "volume", "shifting", "auxiliary", "sailingtopol", "sailingtopod", "etc", "sailtopol",
    "sailtopod", "prevdischarge", "loading", "discharge", "shiftinglph", "auxiliarylph",
    "sailtopollph", "sailtopodlph", "others", "bargeel_feet", "budgetsyncel_budget",
    "budgetsyncel_consumption", "budgetsyncel_overliter", "budgetsyncel_overpercentage",
    "budgetsyncel_overamount", "budgetsyncel_fuelremaining", "projectel_price",
    "projectel_demmurage", "projectel_polstartdemmurage", "projectel_polenddemmurage",
    "projectel_podstartdemmurage", "projectel_podenddemmurage",
    "projectel_bargeel_feet", "projectel_tugboatel_hp",
    "projectel_tugboatel_speedempty", "projectel_tugboatel_speedload", "tugboatel_hp",
    "tugboatel_speedempty", "tugboatel_speedload",
]

TS_COLS = [
    "created_at", "updated_at", "bargeel_created_at", "bargeel_updated_at",
    "budgetsyncel_created_at", "projectel_started_at", "projectel_expected_at",
    "projectel_created_at", "projectel_updated_at", "projectel_closed_at",
    "projectel_bargeel_created_at", "projectel_bargeel_updated_at",
    "projectel_cargoel_created_at", "projectel_cargoel_updated_at",
    "projectel_customerel_created_at", "projectel_podel_created_at",
    "projectel_podel_updated_at", "projectel_podpreviousel_created_at",
    "projectel_podpreviousel_updated_at", "projectel_polel_created_at",
    "projectel_polel_updated_at", "projectel_tugboatel_updated_at",
    "tugboatel_created_at", "tugboatel_updated_at",
]

DATE_COLS = [
    "bargeel_builtyear", "projectel_bargeel_builtyear", "projectel_tugboatel_builtyear",
    "tugboatel_builtyear",
]

TEXT_COLS = [
    "created_by", "otherstext", "bargeel_name", "bargeel_owner", "bargeel_code",
    "bargeel_sapb1", "budgetsyncel_status", "projectel_code", "projectel_remarks",
    "projectel_prorata", "projectel_spal", "projectel_si", "projectel_created_by",
    "projectel_closed_by", "projectel_owner", "projectel_sapb1", "projectel_bargeel_name",
    "projectel_bargeel_owner", "projectel_bargeel_code", "projectel_bargeel_sapb1",
    "projectel_cargoel_name", "projectel_customerel_shortname",
    "projectel_customerel_fullname", "projectel_customerel_phone1",
    "projectel_customerel_pic1", "projectel_podel_name", "projectel_podel_shortname",
    "projectel_podpreviousel_name", "projectel_podpreviousel_shortname",
    "projectel_polel_name", "projectel_polel_shortname", "projectel_tugboatel_name",
    "projectel_tugboatel_code", "tugboatel_name", "tugboatel_owner", "tugboatel_code",
    "tugboatel_sapb1",
]

ALL_COLS = ["id"] + BIGINT_COLS + NUMERIC_COLS + TS_COLS + DATE_COLS + TEXT_COLS


def fetch() -> pd.DataFrame:
    headers = {"Accept": "application/json", "Authorization": f"Bearer {MERPIS_TOKEN}"}
    r = requests.get(API_URL, headers=headers, timeout=120)
    r.raise_for_status()
    data = r.json()
    records = data["data"] if isinstance(data, dict) and "data" in data else data
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
    for c in NUMERIC_COLS:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    for c in TS_COLS:   # UTC -> WIB, tanpa timezone
        s = pd.to_datetime(d[c], errors="coerce", utc=True)
        d[c] = s.dt.tz_convert("Asia/Jakarta").dt.tz_localize(None).dt.strftime("%Y-%m-%d %H:%M:%S")
    for c in DATE_COLS:
        d[c] = pd.to_datetime(d[c], errors="coerce").dt.strftime("%Y-%m-%d")
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
        for k in NUMERIC_COLS:
            if r[k] is not None:
                r[k] = float(r[k])
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
    upsert(to_records(d))
    print("Selesai.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e)
        sys.exit(1)