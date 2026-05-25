import os
import sys
import requests
import pandas as pd
import numpy as np
from supabase import create_client, Client


# =====================================================
# LOAD ENV FROM GITHUB SECRETS / SYSTEM ENV
# =====================================================

from dotenv import load_dotenv

# Load local env file
load_dotenv("environment.env")

MERPIS_API_URL = os.environ["MERPIS_API_URL"]
MERPIS_API_TOKEN = os.environ["MERPIS_API_TOKEN"]

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

TABLE_NAME = "monitoring_project"


# =====================================================
# VALIDASI ENV
# =====================================================

if not MERPIS_API_TOKEN:
    raise ValueError("MERPIS_API_TOKEN belum ditemukan.")

if not SUPABASE_URL:
    raise ValueError("SUPABASE_URL belum ditemukan.")

if not SUPABASE_KEY:
    raise ValueError("SUPABASE_KEY belum ditemukan.")


# =====================================================
# FETCH DATA API
# =====================================================

def fetch_merpis_data():
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {MERPIS_API_TOKEN}"
    }

    print("Mengambil data dari API Merpis...")

    response = requests.get(
        MERPIS_API_URL,
        headers=headers,
        timeout=120
    )

    if response.status_code != 200:
        raise Exception(
            f"Gagal ambil data API. "
            f"Status: {response.status_code}. "
            f"Response: {response.text}"
        )

    raw_data = response.json()

    if "data" not in raw_data:
        raise ValueError("Response API tidak memiliki key 'data'.")

    print("Data API berhasil diambil.")

    return raw_data


# =====================================================
# FLATTEN DATA
# =====================================================

def flatten_data(raw_data):
    raw_monitoring_list = raw_data["data"]
    df_items = pd.DataFrame(raw_monitoring_list)

    if "monitoring" not in df_items.columns:
        raise ValueError("Kolom 'monitoring' tidak ditemukan dari API.")

    df_flat = pd.json_normalize(df_items["monitoring"])

    print("Data berhasil di-flatten.")
    print(f"Total baris awal: {df_flat.shape[0]:,}")
    print(f"Total kolom awal: {df_flat.shape[1]:,}")

    return df_flat


# =====================================================
# BUILD MATRIX FINAL
# =====================================================

def build_matrix_final(df_flat):
    print("Membentuk matrix final...")

    required_cols = [
        "project",
        "activities",
        "activitydetail",
        "started_at",
        "eta",
        "projectel.code",
        "projectel.customerel.fullname",
        "projectel.spal",
        "projectel.si",
        "projectel.price",
        "projectel.prorata",
        "projectel.tugboatel.name",
        "projectel.bargeel.name",
        "projectel.bargeel.feet",
        "projectel.podpreviousel.name",
        "projectel.polel.name",
        "projectel.podel.name",
        "projectel.started_at"
    ]

    missing_cols = [c for c in required_cols if c not in df_flat.columns]

    if missing_cols:
        raise ValueError(f"Kolom berikut tidak ditemukan: {missing_cols}")

    df = df_flat.copy()

    df["project"] = df["project"].astype("string")
    df["activities"] = df["activities"].astype("string")

    for col in ["started_at", "eta", "projectel.started_at"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    profile_cols = [
        "project",
        "projectel.code",
        "projectel.customerel.fullname",
        "projectel.spal",
        "projectel.si",
        "projectel.price",
        "projectel.prorata",
        "projectel.tugboatel.name",
        "projectel.bargeel.name",
        "projectel.bargeel.feet",
        "projectel.podpreviousel.name",
        "projectel.polel.name",
        "projectel.podel.name",
        "projectel.started_at"
    ]

    df_project_profile = (
        df[profile_cols]
        .sort_values(by=["project", "projectel.started_at"])
        .drop_duplicates(subset=["project"], keep="first")
        .reset_index(drop=True)
    )

    df_project_profile["Grup_Kapal_Murni"] = (
        df_project_profile["projectel.code"]
        .astype("string")
        .apply(lambda x: x.split("-")[0] if pd.notna(x) and "-" in x else x)
    )

    df_project_profile = df_project_profile.sort_values(
        by=["Grup_Kapal_Murni", "projectel.started_at", "project"]
    ).reset_index(drop=True)

    mapping_activities = {
        "1": "Actual Arrive POL",
        "2": "Berthing POL",
        "3": "Loading Start",
        "5": "Loading Complete",
        "6": "Cash Off Jetty POL",
        "8": "SKAB / LHV",
        "9": "Document On Board POL",
        "10": "FAW Sailing To POD",
        "12": "Cleaning Start POL",
        "13": "Cleaning Target POL",
        "15": "Cleaning Complete POL",
        "31": "Actual Arrive POD",
        "32": "Berthing POD",
        "33": "Discharge Start",
        "35": "Discharge Complete",
        "36": "Cash Off Jetty POD",
        "37": "Document On Board POD",
        "38": "FAW Sailing To Finish",
        "61": "Actual Arrive Finish",
        "40": "Cleaning Start Finish",
        "41": "Cleaning Target Finish",
        "43": "Cleaning Complete Finish"
    }

    df_activities = df[df["activities"].isin(mapping_activities.keys())].copy()
    df_activities["activity_name"] = df_activities["activities"].map(mapping_activities)

    df_activities_pivoted = (
        df_activities
        .sort_values(by=["project", "started_at"])
        .pivot_table(
            index="project",
            columns="activity_name",
            values="started_at",
            aggfunc="first"
        )
        .reset_index()
    )

    df_activities_pivoted.columns.name = None

    df_eta_pod = (
        df[df["activities"] == "10"][["project", "eta"]]
        .sort_values(by=["project", "eta"])
        .drop_duplicates(subset=["project"], keep="first")
        .rename(columns={"eta": "ETA POD"})
    )

    df_project_profile = pd.merge(
        df_project_profile,
        df_eta_pod,
        on="project",
        how="left"
    )

    df_eta_pol = (
        df[df["activities"] == "38"][["project", "eta"]]
        .sort_values(by=["project", "eta"])
        .drop_duplicates(subset=["project"], keep="first")
        .rename(columns={"eta": "ETA_POL_Mentah"})
    )

    df_project_profile = pd.merge(
        df_project_profile,
        df_eta_pol,
        on="project",
        how="left"
    )

    df_project_profile["ETA POL"] = (
        df_project_profile
        .groupby("Grup_Kapal_Murni")["ETA_POL_Mentah"]
        .shift(1)
    )

    df_loading = (
        df[df["activities"] == "5"][["project", "activitydetail"]]
        .sort_values(by=["project"])
        .drop_duplicates(subset=["project"], keep="first")
        .rename(columns={
            "activitydetail": "Total Cargo Loading"
        })
    )

    df_discharge = (
        df[df["activities"] == "35"][["project", "activitydetail"]]
        .sort_values(by=["project"])
        .drop_duplicates(subset=["project"], keep="first")
        .rename(columns={
            "activitydetail": "Total Cargo Discharge"
        })
    )
    
    # =====================================================
    # MERGE TOTAL CARGO
    # =====================================================

    df_project_profile = pd.merge(
        df_project_profile,
        df_loading,
        on="project",
        how="left"
    )

    df_project_profile = pd.merge(
        df_project_profile,
        df_discharge,
        on="project",
        how="left"
    )

    # =====================================================
    # MERGE MATRIX FINAL
    # =====================================================

    df_matrix_final = pd.merge(
        df_project_profile,
        df_activities_pivoted,
        on="project",
        how="left"
    )

    rename_columns = {
        "projectel.code": "Project Code",
        "projectel.customerel.fullname": "Nama Customer",
        "projectel.spal": "SPAL",
        "projectel.si": "SI",
        "projectel.price": "Price/ MT",
        "projectel.prorata": "Prorata",
        "projectel.tugboatel.name": "Tugboat",
        "projectel.bargeel.name": "Barge",
        "projectel.bargeel.feet": "Feet",
        "projectel.podpreviousel.name": "POD Previous",
        "projectel.polel.name": "Port of Loading",
        "projectel.podel.name": "Port of Discharge"
    }

    df_matrix_final = df_matrix_final.rename(columns=rename_columns)

    df_matrix_final = df_matrix_final.drop(
        columns=[
            "project",
            "Grup_Kapal_Murni",
            "projectel.started_at",
            "ETA_POL_Mentah"
        ],
        errors="ignore"
    )

    kolom_final_urut = [
        "Project Code",
        "Nama Customer",
        "SPAL",
        "SI",
        "Price/ MT",
        "Prorata",
        "Tugboat",
        "Barge",
        "Feet",
        "POD Previous",
        "Port of Loading",
        "Port of Discharge",
        "ETA POL",
        "Actual Arrive POL",
        "Berthing POL",
        "Loading Start",
        "Loading Complete",
        "Total Cargo Loading",
        "Cash Off Jetty POL",
        "SKAB / LHV",
        "Document On Board POL",
        "FAW Sailing To POD",
        "Cleaning Start POL",
        "Cleaning Target POL",
        "Cleaning Complete POL",
        "ETA POD",
        "Actual Arrive POD",
        "Berthing POD",
        "Discharge Start",
        "Discharge Complete",
        "Total Cargo Discharge",
        "Cash Off Jetty POD",
        "Document On Board POD",
        "FAW Sailing To Finish",
        "Actual Arrive Finish",
        "Cleaning Start Finish",
        "Cleaning Target Finish",
        "Cleaning Complete Finish",
        "Revenue",
        "Total Voyage Days"
    ]

    kolom_tersedia = [
        c for c in kolom_final_urut
        if c in df_matrix_final.columns
    ]

    df_matrix_final = df_matrix_final[kolom_tersedia]

    if "ETA POL" in df_matrix_final.columns:
        df_matrix_final = df_matrix_final.sort_values(
            by="ETA POL",
            ascending=True,
            na_position="last"
        )

    df_matrix_final = df_matrix_final.reset_index(drop=True)

    print("Matrix final berhasil dibuat.")
    print(f"Jumlah row: {len(df_matrix_final):,}")
    print(f"Jumlah kolom: {len(df_matrix_final.columns):,}")

    return df_matrix_final


# =====================================================
# CLEAN DATA FOR SUPABASE
# =====================================================

def clean_for_supabase(df_matrix_final):
    print("Melakukan cleansing data...")

    df_upload = df_matrix_final.copy()

    keyword_tanggal = [
        "ETA", "Arrive", "Start", "Complete", "Berthing",
        "Jetty", "LHV", "Board", "Sailing", "Target"
    ]

    kolom_tanggal = [
        col for col in df_upload.columns
        if any(k in col for k in keyword_tanggal)
    ]

    for col in kolom_tanggal:
        df_upload[col] = pd.to_datetime(df_upload[col], errors="coerce")
        df_upload.loc[df_upload[col].dt.year <= 1970, col] = pd.NaT

    if "Prorata" in df_upload.columns:
        df_upload["Prorata"] = pd.to_timedelta(
            df_upload["Prorata"],
            errors="coerce"
        )
        df_upload["Prorata"] = df_upload["Prorata"].dt.total_seconds() / 3600

    kolom_upper = ["Project Code", "Tugboat", "Barge", "Nama Customer"]

    for col in kolom_upper:
        if col in df_upload.columns:
            df_upload[col] = (
                df_upload[col]
                .astype("string")
                .str.strip()
                .str.upper()
            )

    kolom_port = ["POD Previous", "Port of Loading", "Port of Discharge"]

    for col in kolom_port:
        if col in df_upload.columns:
            df_upload[col] = (
                df_upload[col]
                .astype("string")
                .str.strip()
                .str.title()
            )

    numeric_cols = [
        "Price/ MT",
        "Feet",
        "Total Cargo Loading",
        "Total Cargo Discharge"
    ]

    for col in numeric_cols:
        if col in df_upload.columns:
            df_upload[col] = (
                df_upload[col]
                .astype("string")
                .str.replace(r"[^0-9.]", "", regex=True)
            )
            df_upload[col] = pd.to_numeric(df_upload[col], errors="coerce")

    # =====================================================
    # CALCULATED COLUMNS
    # =====================================================

    if "Price/ MT" in df_upload.columns and "Total Cargo Loading" in df_upload.columns:
        df_upload["Revenue"] = (
            df_upload["Price/ MT"] * df_upload["Total Cargo Loading"]
        )

    if "FAW Sailing To Finish" in df_upload.columns and "Actual Arrive POL" in df_upload.columns:
        finish_dt = pd.to_datetime(
            df_upload["FAW Sailing To Finish"],
            errors="coerce"
        )

        arrive_pol_dt = pd.to_datetime(
            df_upload["Actual Arrive POL"],
            errors="coerce"
        )

        df_upload["Total Voyage Days"] = (
            finish_dt - arrive_pol_dt
        ).dt.total_seconds() / 86400

    for col in kolom_tanggal:
        if col in df_upload.columns:
            df_upload[col] = df_upload[col].apply(
                lambda x: x.isoformat() if pd.notnull(x) else None
            )

    # Hapus duplicate berdasarkan Project Code
    if "Project Code" in df_upload.columns:
        before_rows = len(df_upload)
        df_upload = df_upload.drop_duplicates(
            subset=["Project Code"],
            keep="last"
        )
        after_rows = len(df_upload)
        print(f"Duplicate Project Code terhapus: {before_rows - after_rows:,}")

    # Bersihkan NaN, Inf, -Inf agar aman untuk JSON Supabase
    df_upload = df_upload.replace([np.inf, -np.inf], None)
    df_upload = df_upload.astype(object).where(pd.notnull(df_upload), None)

    if "ETA POL" in df_upload.columns:
        df_upload = df_upload.sort_values(
            by="ETA POL",
            na_position="last"
        )

    df_upload = df_upload.reset_index(drop=True)

    print("Cleansing selesai.")
    print(f"Total data siap upload: {len(df_upload):,}")

    return df_upload


def upload_to_supabase(df_upload):
    print("Menghubungkan ke Supabase...")

    supabase: Client = create_client(
        SUPABASE_URL,
        SUPABASE_KEY
    )

    print("Supabase connected.")

    # Bersihkan NaN / Inf / -Inf final sebelum jadi JSON
    df_upload = df_upload.replace([np.inf, -np.inf], np.nan)
    df_upload = df_upload.astype(object).where(pd.notnull(df_upload), None)

    records = df_upload.to_dict(orient="records")

    for row in records:
        for key, value in row.items():
            if pd.isna(value) if not isinstance(value, (list, dict)) else False:
                row[key] = None

    if not records:
        print("Tidak ada data untuk diupload.")
        return

    print(f"Total upsert: {len(records):,} rows")

    batch_size = 500
    total_uploaded = 0

    print("Mulai upsert batch...")

    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]

        supabase.table(TABLE_NAME) \
            .upsert(batch, on_conflict="Project Code") \
            .execute()

        total_uploaded += len(batch)

        print(
            f"Batch {(i // batch_size) + 1} "
            f"berhasil upsert {len(batch):,} rows"
        )

    print("===================================")
    print("UPSERT SELESAI")
    print("===================================")
    print(f"Total Upserted: {total_uploaded:,} rows")
    print(f"Table: {TABLE_NAME}")
    print("===================================")

# =====================================================
# MAIN PIPELINE
# =====================================================

def main():
    raw_data = fetch_merpis_data()
    df_flat = flatten_data(raw_data)
    df_matrix_final = build_matrix_final(df_flat)
    df_upload = clean_for_supabase(df_matrix_final)
    upload_to_supabase(df_upload)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("===================================")
        print("PIPELINE GAGAL")
        print("===================================")
        print(str(e))
        sys.exit(1)