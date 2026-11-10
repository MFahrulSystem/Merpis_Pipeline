import os
import sys
import re
import json
import time
from datetime import datetime, timezone
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

# Override Revenue manual (dipakai clean_for_supabase untuk tabel lama)
MANUAL_REVENUE = {
    "BG.MP31-2026-014": 180000000,
    "BG.MP31-2026-016": 300000000,
    "BG.MP31-2026-017": 180000000,
    "BG.MP31-2026-018": 240000000
}


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

    # Aman untuk secret yang berisi "Bearer xxx" maupun hanya "xxx"
    token = MERPIS_API_TOKEN.strip()

    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"

    headers = {
        "Accept": "application/json",
        "Authorization": token
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
    print(f"Total record API: {len(raw_data['data']):,}")

    return raw_data


# =====================================================
# FLATTEN DATA
# =====================================================

def flatten_data(raw_data):
    raw_monitoring_list = raw_data["data"]

    if not raw_monitoring_list:
        raise ValueError(
            "API mengembalikan data kosong. "
            "Proses dihentikan agar tabel Supabase tidak terhapus."
        )

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
        raise ValueError(
            f"Kolom berikut tidak ditemukan: {missing_cols}"
        )

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
        .drop_duplicates(
            subset=["project"],
            keep="first"
        )
        .reset_index(drop=True)
    )

    df_project_profile["Grup_Kapal_Murni"] = (
        df_project_profile["projectel.code"]
        .astype("string")
        .apply(
            lambda x:
                x.split("-")[0]
                if pd.notna(x) and "-" in x
                else x
        )
    )

    df_project_profile = df_project_profile.sort_values(
        by=[
            "Grup_Kapal_Murni",
            "projectel.started_at",
            "project"
        ]
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

    df_activities = df[
        df["activities"].isin(mapping_activities.keys())
    ].copy()

    df_activities["activity_name"] = (
        df_activities["activities"]
        .map(mapping_activities)
    )

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

    # =====================================================
    # ETA POD
    # =====================================================

    df_eta_pod = (
        df[df["activities"] == "10"][
            ["project", "eta"]
        ]
        .sort_values(by=["project", "eta"])
        .drop_duplicates(
            subset=["project"],
            keep="first"
        )
        .rename(columns={
            "eta": "ETA POD"
        })
    )

    df_project_profile = pd.merge(
        df_project_profile,
        df_eta_pod,
        on="project",
        how="left"
    )

    # =====================================================
    # ETA POL
    # =====================================================

    df_eta_pol = (
        df[df["activities"] == "38"][
            ["project", "eta"]
        ]
        .sort_values(by=["project", "eta"])
        .drop_duplicates(
            subset=["project"],
            keep="first"
        )
        .rename(columns={
            "eta": "ETA_POL_Mentah"
        })
    )

    df_project_profile = pd.merge(
        df_project_profile,
        df_eta_pol,
        on="project",
        how="left"
    )

    # ETA POL = ETA (FAW Sailing To Finish) milik TRIP SEBELUMNYA.
    # Trip sebelumnya ditentukan dari nomor di Project Code
    # (prefix, tahun, nomor trip), BUKAN dari tanggal mulai project.
    # Kalau trip sebelumnya ada tapi ETA-nya kosong, hasilnya tetap kosong.
    kode_eta = (
        df_project_profile["projectel.code"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    pola_eta = kode_eta.str.extract(r"^(.*)-(\d{4})-(\d+)$")

    df_urut_eta = pd.DataFrame({
        "project": df_project_profile["project"],
        "prefix": pola_eta[0],
        "tahun": pd.to_numeric(pola_eta[1], errors="coerce"),
        "trip": pd.to_numeric(pola_eta[2], errors="coerce"),
        "eta_mentah": df_project_profile["ETA_POL_Mentah"]
    }).dropna(subset=["prefix", "tahun", "trip"])

    # ETA hanya dianggap ada kalau tanggal FAW Sailing To Finish trip itu
    # juga ada (valid, tahun > 1970). Kalau tanggal FAW kosong, ETA-nya
    # dikosongkan juga (kapal baru / baru selesai docking).
    if "FAW Sailing To Finish" in df_activities_pivoted.columns:

        faw_per_project = pd.to_datetime(
            df_activities_pivoted
            .set_index("project")["FAW Sailing To Finish"],
            errors="coerce"
        )

        faw_valid = df_urut_eta["project"].map(faw_per_project)
        faw_valid = faw_valid.notna() & (faw_valid.dt.year > 1970)

    else:

        faw_valid = pd.Series(False, index=df_urut_eta.index)

    df_urut_eta["eta_mentah"] = df_urut_eta["eta_mentah"].where(faw_valid)

    df_urut_eta = df_urut_eta.sort_values(
        ["prefix", "tahun", "trip"]
    )

    # Trip sebelumnya harus BERURUTAN: nomor trip - 1 di tahun yang sama.
    # Kalau nomor trip sebelumnya tidak ada di data (project tanpa event
    # aktivitas, kapal baru, atau baru selesai docking), ETA POL dikosongkan,
    # tidak melompat ke trip yang lebih lama.
    # Trip pertama di tahun baru mengikuti trip terakhir tahun lalu.
    grup_eta = df_urut_eta.groupby("prefix")

    eta_sebelumnya = grup_eta["eta_mentah"].shift(1)
    tahun_sebelumnya = grup_eta["tahun"].shift(1)
    trip_sebelumnya = grup_eta["trip"].shift(1)

    berurutan = (
        (
            (df_urut_eta["tahun"] == tahun_sebelumnya)
            & (df_urut_eta["trip"] == trip_sebelumnya + 1)
        )
        |
        (
            (df_urut_eta["tahun"] == tahun_sebelumnya + 1)
            & (df_urut_eta["trip"] == 1)
        )
    )

    df_urut_eta["ETA POL"] = eta_sebelumnya.where(berurutan)

    df_project_profile = pd.merge(
        df_project_profile,
        df_urut_eta[["project", "ETA POL"]],
        on="project",
        how="left"
    )

    # =====================================================
    # TOTAL CARGO LOADING
    # =====================================================

    df_loading = (
        df[df["activities"] == "5"][
            ["project", "activitydetail"]
        ]
        .sort_values(by=["project"])
        .drop_duplicates(
            subset=["project"],
            keep="first"
        )
        .rename(columns={
            "activitydetail": "Total Cargo Loading"
        })
    )

    # =====================================================
    # TOTAL CARGO DISCHARGE
    # =====================================================

    df_discharge = (
        df[df["activities"] == "35"][
            ["project", "activitydetail"]
        ]
        .sort_values(by=["project"])
        .drop_duplicates(
            subset=["project"],
            keep="first"
        )
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

    df_matrix_final = df_matrix_final.rename(
        columns=rename_columns
    )

    # =====================================================
    # VOYAGE V.2
    # Sumber: FAW Sailing To Finish
    # Rumus : FAW Sailing To Finish (trip ini)
    #         - FAW Sailing To Finish (trip sebelumnya, grup sama)
    # (harus SETELAH rename, karena butuh kolom "Project Code")
    # =====================================================

    if (
        "FAW Sailing To Finish" in df_matrix_final.columns
        and
        "Project Code" in df_matrix_final.columns
    ):

        kode = (
            df_matrix_final["Project Code"]
            .astype("string")
            .str.strip()
            .str.upper()
        )

        # BG.AB01-2026-002 -> grup: BG.AB01-2026 | trip: 2
        kode_split = kode.str.extract(r"^(.*)-(\d+)$")

        faw_finish = pd.to_datetime(
            df_matrix_final["FAW Sailing To Finish"],
            errors="coerce"
        )

        faw_finish = faw_finish.where(
            faw_finish.dt.year > 1970
        )

        df_trip = pd.DataFrame({
            "grup": kode_split[0],
            "trip": pd.to_numeric(
                kode_split[1],
                errors="coerce"
            ),
            "faw": faw_finish
        }).sort_values(["grup", "trip"])

        prev_faw = (
            df_trip
            .groupby("grup")["faw"]
            .shift(1)
        )

        df_matrix_final["Voyage V.2"] = (
            (df_trip["faw"] - prev_faw)
            .dt.total_seconds() / 86400
        )

    else:
        print(
            "PERINGATAN: Voyage V.2 dilewati, kolom "
            "'FAW Sailing To Finish' atau 'Project Code' "
            "tidak ditemukan."
        )

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
        "Total Voyage Days",
        "Voyage V.2"
    ]

    kolom_tersedia = [
        c
        for c in kolom_final_urut
        if c in df_matrix_final.columns
    ]

    df_matrix_final = df_matrix_final[
        kolom_tersedia
    ]

    if "ETA POL" in df_matrix_final.columns:
        df_matrix_final = df_matrix_final.sort_values(
            by="ETA POL",
            ascending=True,
            na_position="last"
        )

    df_matrix_final = df_matrix_final.reset_index(
        drop=True
    )

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
        "ETA",
        "Arrive",
        "Start",
        "Complete",
        "Berthing",
        "Jetty",
        "LHV",
        "Board",
        "Sailing",
        "Target"
    ]

    kolom_tanggal = [
        col
        for col in df_upload.columns
        if any(k in col for k in keyword_tanggal)
    ]

    for col in kolom_tanggal:
        df_upload[col] = pd.to_datetime(
            df_upload[col],
            errors="coerce"
        )

        df_upload.loc[
            df_upload[col].dt.year <= 1970,
            col
        ] = pd.NaT

    # =====================================================
    # PRORATA
    # =====================================================

    if "Prorata" in df_upload.columns:

        df_upload["Prorata"] = pd.to_timedelta(
            df_upload["Prorata"],
            errors="coerce"
        )

        df_upload["Prorata"] = (
            df_upload["Prorata"]
            .dt.total_seconds()
            / 3600
        )

    # =====================================================
    # UPPERCASE
    # =====================================================

    kolom_upper = [
        "Project Code",
        "Tugboat",
        "Barge",
        "Nama Customer"
    ]

    for col in kolom_upper:

        if col in df_upload.columns:

            df_upload[col] = (
                df_upload[col]
                .astype("string")
                .str.strip()
                .str.upper()
            )

    # =====================================================
    # TITLE CASE PORT
    # =====================================================

    kolom_port = [
        "POD Previous",
        "Port of Loading",
        "Port of Discharge"
    ]

    for col in kolom_port:

        if col in df_upload.columns:

            df_upload[col] = (
                df_upload[col]
                .astype("string")
                .str.strip()
                .str.title()
            )

    # =====================================================
    # NUMERIC
    # =====================================================

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
                .str.replace(
                    r"[^0-9.]",
                    "",
                    regex=True
                )
            )

            df_upload[col] = pd.to_numeric(
                df_upload[col],
                errors="coerce"
            )

    # =====================================================
    # REMOVE DUPLICATE PROJECT CODE
    # (dipindahkan ke sini, SEBELUM kalkulasi Revenue,
    #  supaya override manual di bawah tidak ketiban/kehapus
    #  oleh proses dedup)
    # =====================================================

    if "Project Code" in df_upload.columns:

        before_rows = len(df_upload)

        df_upload = df_upload.drop_duplicates(
            subset=["Project Code"],
            keep="last"
        )

        after_rows = len(df_upload)

        print(
            f"Duplicate Project Code terhapus: "
            f"{before_rows - after_rows:,}"
        )

    # =====================================================
    # CALCULATED COLUMNS
    # =====================================================

    if (
        "Price/ MT" in df_upload.columns
        and
        "Total Cargo Loading" in df_upload.columns
    ):

        # Hitung Revenue normal untuk semua project
        df_upload["Revenue"] = (
            df_upload["Price/ MT"]
            *
            df_upload["Total Cargo Loading"]
        )

        # Override manual: dict MANUAL_REVENUE (didefinisikan di bagian konfigurasi atas)
        project_code = df_upload["Project Code"].astype(str).str.strip()

        for code, value in MANUAL_REVENUE.items():
            df_upload.loc[project_code == code, "Revenue"] = value

    if (
        "FAW Sailing To Finish" in df_upload.columns
        and
        "Actual Arrive POL" in df_upload.columns
    ):

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

    # =====================================================
    # FORMAT DATE TO ISO
    # =====================================================

    for col in kolom_tanggal:

        if col in df_upload.columns:

            df_upload[col] = df_upload[col].apply(
                lambda x:
                    x.isoformat()
                    if pd.notnull(x)
                    else None
            )

    # =====================================================
    # CLEAN NaN / INF
    # =====================================================

    df_upload = df_upload.replace(
        [np.inf, -np.inf],
        None
    )

    df_upload = df_upload.astype(object).where(
        pd.notnull(df_upload),
        None
    )

    # =====================================================
    # SORT
    # =====================================================

    if "ETA POL" in df_upload.columns:

        df_upload = df_upload.sort_values(
            by="ETA POL",
            na_position="last"
        )

    df_upload = df_upload.reset_index(
        drop=True
    )

    print("Cleansing selesai.")
    print(
        f"Total data siap upload: "
        f"{len(df_upload):,}"
    )

    return df_upload


# =====================================================
# FULL REFRESH SUPABASE
# =====================================================

def upload_to_supabase(df_upload):

    print("Menghubungkan ke Supabase...")

    supabase: Client = create_client(
        SUPABASE_URL,
        SUPABASE_KEY
    )

    print("Supabase connected.")

    # =====================================================
    # VALIDASI DATA
    # =====================================================

    if df_upload is None or df_upload.empty:

        raise ValueError(
            "Data hasil processing kosong. "
            "Proses DELETE dibatalkan."
        )

    if "Project Code" not in df_upload.columns:

        raise ValueError(
            "Kolom 'Project Code' tidak ditemukan. "
            "Proses DELETE dibatalkan."
        )

    # Bersihkan NaN / Inf final
    df_upload = df_upload.replace(
        [np.inf, -np.inf],
        np.nan
    )

    df_upload = df_upload.astype(object).where(
        pd.notnull(df_upload),
        None
    )

    records = df_upload.to_dict(
        orient="records"
    )

    # =====================================================
    # VALIDASI RECORD
    # =====================================================

    if not records:

        raise ValueError(
            "Tidak ada record untuk diupload. "
            "Proses DELETE dibatalkan."
        )

    print(
        f"Total data dari API yang siap "
        f"disimpan: {len(records):,} rows"
    )

    # =====================================================
    # CEK PROJECT CODE
    # =====================================================

    project_codes = [
        row.get("Project Code")
        for row in records
        if row.get("Project Code")
    ]

    if not project_codes:

        raise ValueError(
            "Tidak ada Project Code valid. "
            "Proses DELETE dibatalkan."
        )

    print(
        f"Project Code valid: "
        f"{len(project_codes):,}"
    )

    # =====================================================
    # DELETE DATA LAMA
    # =====================================================

    print("===================================")
    print("FULL REFRESH SUPABASE")
    print("===================================")

    print(
        f"Menghapus seluruh data lama "
        f"dari table: {TABLE_NAME}"
    )

    try:

        # Filter neq digunakan agar Supabase
        # menjalankan DELETE terhadap seluruh row
        supabase.table(TABLE_NAME) \
            .delete() \
            .neq("Project Code", "") \
            .execute()

    except Exception as e:

        raise Exception(
            "Gagal menghapus data lama dari "
            f"Supabase: {str(e)}"
        )

    print("Data lama berhasil dihapus.")

    # =====================================================
    # INSERT DATA BARU
    # =====================================================

    batch_size = 500
    total_uploaded = 0
    total_batches = (
        (len(records) + batch_size - 1)
        // batch_size
    )

    print(
        f"Mulai insert data baru "
        f"dalam {total_batches} batch..."
    )

    try:

        for i in range(
            0,
            len(records),
            batch_size
        ):

            batch = records[
                i:i + batch_size
            ]

            supabase.table(TABLE_NAME) \
                .insert(batch) \
                .execute()

            total_uploaded += len(batch)

            print(
                f"Batch {(i // batch_size) + 1}/"
                f"{total_batches} "
                f"berhasil insert "
                f"{len(batch):,} rows"
            )

    except Exception as e:

        print("===================================")
        print("INSERT GAGAL")
        print("===================================")
        print(str(e))

        raise Exception(
            "Proses insert ke Supabase gagal. "
            "Table mungkin hanya terisi sebagian. "
            f"Detail: {str(e)}"
        )

    # =====================================================
    # RESULT
    # =====================================================

    print("===================================")
    print("FULL REFRESH SELESAI")
    print("===================================")
    print(f"Table          : {TABLE_NAME}")
    print(f"Data dari API  : {len(records):,} rows")
    print(f"Data inserted  : {total_uploaded:,} rows")
    print("===================================")

# ===============================================================
# TABEL TURNAROUND (TRIP SAAT INI DIHUBUNGKAN KE TRIP SEBELUMNYA)
# ===============================================================

TABLE_TURNAROUND = "monitoring_turnaround"

KOLOM_IDENTITAS = [
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
    "Port of Discharge"
]

KOLOM_T1 = [
    "FAW Sailing To Finish",
    "Actual Arrive Finish",
    "Cleaning Start Finish",
    "Cleaning Target Finish",
    "Cleaning Complete Finish"
]

KOLOM_T2 = [
    "ETA POL",
    "Actual Arrive POL",
    "Berthing POL",
    "Loading Start",
    "Loading Complete",
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
    "Cash Off Jetty POD",
    "Document On Board POD",
    "FAW Sailing To Finish",
    "Actual Arrive Finish",
    "Cleaning Start Finish",
    "Cleaning Target Finish",
    "Cleaning Complete Finish",
    "Total Cargo Loading",
    "Total Cargo Discharge",
    "Revenue",
    "Total Voyage Days"
]


def build_turnaround(df_upload):

    print("Membentuk tabel turnaround (Trip 1 + Trip 2)...")

    df = df_upload.copy()

    # Buang baris tanpa Project Code (primary key tidak boleh kosong)
    df = df[df["Project Code"].notna()].copy()

    # BG.AB01-2026-002 -> grup: BG.AB01-2026 | trip: 2
    kode = (
        df["Project Code"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    kode_split = kode.str.extract(r"^(.*)-(\d+)$")

    # Kode yang tidak cocok pola dianggap trip 1 di grupnya sendiri
    df["_grup"] = kode_split[0].fillna(kode)
    df["_trip"] = pd.to_numeric(
        kode_split[1],
        errors="coerce"
    ).fillna(1)

    df = df.sort_values(
        ["_grup", "_trip"]
    ).reset_index(drop=True)

    # =====================================================
    # TOTAL VOYAGE DAYS (rumus turnaround)
    # (Actual Arrive POD - FAW Sailing To POD)
    # + (Actual Arrive Finish - FAW Sailing To Finish)
    # Kosong kalau salah satu dari 4 tanggalnya belum ada
    # =====================================================

    def _dt(col):
        if col in df.columns:
            return pd.to_datetime(df[col], errors="coerce")
        return pd.Series(pd.NaT, index=df.index)

    leg_pod = (
        _dt("Actual Arrive POD") - _dt("FAW Sailing To POD")
    ).dt.total_seconds() / 86400

    leg_finish = (
        _dt("Actual Arrive Finish") - _dt("FAW Sailing To Finish")
    ).dt.total_seconds() / 86400

    df["Total Voyage Days"] = leg_pod + leg_finish

    grp = df.groupby("_grup")

    out = pd.DataFrame(index=df.index)

    # Identitas
    for c in KOLOM_IDENTITAS:
        out[c] = df[c] if c in df.columns else None

    # Trip 1 = baris sebelumnya di grup yang sama
    out["T1 Project Code"] = grp["Project Code"].shift(1)

    for c in KOLOM_T1:
        out[f"T1 {c}"] = (
            grp[c].shift(1)
            if c in df.columns
            else None
        )

    # Trip 2 = baris itu sendiri
    for c in KOLOM_T2:
        out[f"T2 {c}"] = (
            df[c]
            if c in df.columns
            else None
        )

    kolom_urut = (
        KOLOM_IDENTITAS
        + ["T1 Project Code"]
        + [f"T1 {c}" for c in KOLOM_T1]
        + [f"T2 {c}" for c in KOLOM_T2]
    )

    out = out[kolom_urut]

    out = out.replace([np.inf, -np.inf], np.nan)

    out = out.astype(object).where(
        pd.notnull(out),
        None
    )

    print(
        f"Turnaround siap: {len(out):,} baris | "
        f"T1 terisi: {out['T1 Project Code'].notna().sum():,} | "
        f"Total Voyage Days terisi: "
        f"{out['T2 Total Voyage Days'].notna().sum():,}"
    )

    return out


def upload_turnaround(df_turnaround):

    if df_turnaround is None or df_turnaround.empty:
        raise ValueError(
            "Data turnaround kosong. Upload dibatalkan."
        )

    supabase: Client = create_client(
        SUPABASE_URL,
        SUPABASE_KEY
    )

    records = df_turnaround.to_dict(
        orient="records"
    )

    batch_size = 500
    total_batches = (
        (len(records) + batch_size - 1)
        // batch_size
    )

    print(
        f"Upsert ke table {TABLE_TURNAROUND}: "
        f"{len(records):,} rows dalam {total_batches} batch..."
    )

    for i in range(0, len(records), batch_size):

        batch = records[i:i + batch_size]

        supabase.table(TABLE_TURNAROUND) \
            .upsert(batch) \
            .execute()

        print(
            f"Batch {(i // batch_size) + 1}/"
            f"{total_batches} "
            f"berhasil upsert {len(batch):,} rows"
        )

    print("Upload turnaround selesai.")


# ===============================================================
# TURNAROUND V2
# 1 baris per project: info project + T1 (3 kolom) + T2 (aktivitas pilihan)
# Layout kolom akhir diatur oleh V2_KOLOM + V2_RENAME (lihat di bawah)
# Tabel: monitoring_turnaround_v2 -> upsert + hapus baris yatim
# (tabel monitoring_turnaround lama tidak disentuh)
# ===============================================================

V2_TABLE = os.environ.get("V2_TABLE", "monitoring_turnaround_v2")
V2_KEY = "project_code"
V2_BATCH = 200
V2_MIN_RATIO = 0.90     # guard: jumlah baru minimal 90% dari isi tabel
V2_DRY_RUN = os.environ.get("V2_DRY_RUN", "false").lower() in ("1", "true", "yes")

V2_DETAIL = [
    "activitydetail", "speed", "rpm", "eta",
    "remainingdistance", "cargoremarks"
]

V2_PROJ_COLS = [
    "id", "code", "remarks", "owner", "started_at", "expected_at",
    "closed_at", "closed_by", "tugboatel.name", "bargeel.name",
    "bargeel.feet", "tugboatel.feet", "customerel.shortname",
    "customerel.fullname", "cargoel.name", "polel.name", "polel.shortname",
    "podel.name", "podel.shortname", "podpreviousel.name", "spal", "si",
    "sidate", "price", "prorata", "bbm", "demmurage", "ondemmurage",
    "sapb1", "projectremarks", "polagen", "podagen",
    "polstartdemmurage", "polenddemmurage",
    "podstartdemmurage", "podenddemmurage", "downtimeamount"
]

V2_BASE = f"{SUPABASE_URL.rstrip('/')}/rest/v1/{V2_TABLE}"
V2_HDR = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}


def v2_build(records):

    ev_rows, proj = [], {}

    for r in records:
        p = r.get("projectel") or {}
        a = r.get("activitiesel") or {}

        ev = {
            k: v for k, v in r.items()
            if k not in ("projectel", "activitiesel")
        }
        ev["activity_port"] = a.get("port")
        ev["activity_name"] = a.get("name")
        ev_rows.append(ev)

        if p:
            proj[p["id"]] = p

    df_ev = pd.DataFrame(ev_rows)
    df_proj = pd.json_normalize(list(proj.values()), sep=".")
    df_ev["started_at"] = pd.to_datetime(df_ev["started_at"], errors="coerce")

    # ---- pivot aktivitas per project ----
    ev = df_ev.dropna(subset=["activity_name"]).copy()
    ev["act"] = ev["activity_port"] + " | " + ev["activity_name"]
    ev = ev.sort_values(["project", "started_at", "id"])

    g = ev.groupby(["project", "act"])
    res = (
        g.agg(
            Start=("started_at", "first"),
            Last=("started_at", "last"),
            Count=("id", "size")
        )
        .join(g[V2_DETAIL].last())
        .reset_index()
    )

    order = (
        ev.drop_duplicates("act")
        .sort_values("activities")["act"]
        .tolist()
    )

    parts = []
    for act in order:
        s = res[res["act"] == act].set_index("project")
        b = pd.DataFrame(index=s.index)
        b[f"{act} | Start"] = s["Start"]

        if s["Count"].max() > 1:
            b[f"{act} | Last"] = s["Last"]
            b[f"{act} | Count"] = s["Count"]

        for c in V2_DETAIL:
            if s[c].notna().any():
                b[f"{act} | {c}"] = s[c]

        parts.append(b)

    wide = pd.concat(parts, axis=1)

    # ---- rata-rata speed & rpm dari semua event Sailing ----
    for act in ("POL | Sailing To POD", "POD | Sailing To Finish"):
        sub = ev[ev["act"] == act]
        avg = (
            sub.assign(
                speed=pd.to_numeric(sub["speed"], errors="coerce"),
                rpm=pd.to_numeric(sub["rpm"], errors="coerce")
            )
            .groupby("project")[["speed", "rpm"]]
            .mean()
        )
        wide[f"{act} | speed_avg"] = avg["speed"].reindex(wide.index)
        wide[f"{act} | rpm_avg"] = avg["rpm"].reindex(wide.index)

    # ---- info project ----
    cols = [c for c in V2_PROJ_COLS if c in df_proj.columns]
    info = (
        df_proj[cols]
        .rename(columns={"id": "project_id", "code": "Project Code"})
        .copy()
    )

    # ---- T1 = trip sebelumnya (lintas tahun), hanya 2 kolom ----
    pat = info["Project Code"].str.extract(r"^(.*)-(\d{4})-(\d+)$")
    info["_prefix"] = pat[0]
    info["_year"] = pat[1].astype(float)
    info["_trip"] = pat[2].astype(float)

    info = info.sort_values(
        ["_prefix", "_year", "_trip"]
    ).reset_index(drop=True)

    info["T1 Project Code"] = info.groupby("_prefix")["Project Code"].shift(1)
    info["_prev_id"] = info.groupby("_prefix")["project_id"].shift(1)

    t1_src = {
        "T1 FAW Sailing To Finish": "POD | FAW Sailing To Finish | Start",
        "T1 Actual Arrive Finish": "Finish | Actual Arrive Finish | Start"
    }

    for new, src in t1_src.items():
        info[new] = (
            info["_prev_id"].map(wide[src])
            if src in wide.columns
            else pd.NaT
        )

    df = (
        info.drop(columns=["_prefix", "_year", "_trip", "_prev_id"])
        .merge(
            wide.add_prefix("T2 | "),
            left_on="project_id",
            right_index=True,
            how="left"
        )
        .reset_index(drop=True)
    )

    # ---- durasi (hari): hanya untuk flag Data Check, tidak disimpan ----
    def days(a, b):
        if a not in df.columns or b not in df.columns:
            return pd.Series(np.nan, index=df.index)
        return (df[b] - df[a]).dt.total_seconds() / 86400

    POL_ARR = "T2 | POL | Actual Arrive POL | Start"
    POL_LS = "T2 | POL | Loading Start | Start"
    POL_LC = "T2 | POL | Loading Complete | Start"
    POL_FAW = "T2 | POL | FAW Sailing To POD | Start"
    POD_ARR = "T2 | POD | Actual Arrive POD | Start"
    POD_DS = "T2 | POD | Discharge Start | Start"
    POD_DC = "T2 | POD | Discharge Complete | Start"
    POD_FAW = "T2 | POD | FAW Sailing To Finish | Start"
    FIN_ARR = "T2 | Finish | Actual Arrive Finish | Start"

    df["Voyage V.2 (hari)"] = days("T1 FAW Sailing To Finish", POD_FAW)
    df["Jeda Finish T1 ke Arrive POL (hari)"] = days(
        "T1 Actual Arrive Finish", POL_ARR
    )
    df["POL Loading (hari)"] = days(POL_LS, POL_LC)
    df["POL Total Port (hari)"] = days(POL_ARR, POL_FAW)
    df["Sailing POL ke POD (hari)"] = days(POL_FAW, POD_ARR)
    df["POD Discharge (hari)"] = days(POD_DS, POD_DC)
    df["POD Total Port (hari)"] = days(POD_ARR, POD_FAW)
    df["Sailing POD ke Finish (hari)"] = days(POD_FAW, FIN_ARR)
    df["Total Voyage Days"] = (
        df["Sailing POL ke POD (hari)"]
        + df["Sailing POD ke Finish (hari)"]
    )

    # ---- flag Data Check (durasi negatif) ----
    labels = {
        "Jeda Finish T1 ke Arrive POL (hari)": "Jeda T1-POL negatif",
        "POL Loading (hari)": "POL Loading negatif",
        "Sailing POL ke POD (hari)": "Sailing POL-POD negatif",
        "Sailing POD ke Finish (hari)": "Sailing POD-Finish negatif",
        "Total Voyage Days": "Total Voyage negatif"
    }

    chk = pd.DataFrame({
        lab: np.where(df[c] < 0, lab, "")
        for c, lab in labels.items()
    })
    flag = chk.apply(lambda r: "; ".join(x for x in r if x), axis=1)
    df["Data Check"] = flag.where(flag != "", None)

    print(
        f"Turnaround V2: {df.shape[0]:,} project x {df.shape[1]:,} kolom | "
        f"tanpa T1: {df['T1 Project Code'].isna().sum():,} | "
        f"flag Data Check: {df['Data Check'].notna().sum():,}"
    )

    return df


def v2_snake(name):
    s = name.replace("T2 | ", "t2_").replace("T1 ", "t1_")
    s = s.replace("|", " ").replace("/", " ").replace(".", "_")
    s = re.sub(r"[^0-9a-zA-Z_ ]+", " ", s)
    s = re.sub(r"\s+", "_", s.strip()).lower()
    return re.sub(r"_+", "_", s)


def v2_to_num(s):
    out = pd.to_numeric(s, errors="coerce")
    lost = int((s.notna() & out.isna()).sum())
    if lost:
        print(f"  ! {lost} nilai non-angka di '{s.name}' jadi null")
    return out


def v2_to_dt(s):
    # pandas >= 2 menebak format dari elemen pertama; "mixed" mencegah
    # elemen berformat lain diam-diam jadi NaT
    if int(pd.__version__.split(".")[0]) >= 2:
        out = pd.to_datetime(s, errors="coerce", utc=True, format="mixed")
    else:
        out = pd.to_datetime(s, errors="coerce", utc=True)
    return out.dt.tz_localize(None)


# ---------------------------------------------------------------
# LAYOUT KOLOM AKHIR TABEL V2 (urutan = urutan kolom di tabel)
# V2_KOLOM  : nama kolom sumber (hasil v2_snake) yang dipertahankan
# V2_RENAME : nama sumber -> nama akhir di Supabase
# Kolom yang tidak ada di V2_KOLOM otomatis dibuang.
# synced_at ditambahkan otomatis di kolom paling akhir.
# ---------------------------------------------------------------

V2_KOLOM = [
    "project_id",
    "project_code",
    "remarks",
    "owner",
    "started_at",
    "expected_at",
    "closed_at",
    "closed_by",
    "tugboatel_name",
    "bargeel_name",
    "bargeel_feet",
    "tugboatel_feet",
    "customerel_shortname",
    "customerel_fullname",
    "cargoel_name",
    "polel_name",
    "polel_shortname",
    "podel_name",
    "podel_shortname",
    "podpreviousel_name",
    "spal",
    "si",
    "sidate",
    "price",
    "prorata",
    "bbm",
    "demmurage",
    "ondemmurage",
    "sapb1",
    "projectremarks",
    "polagen",
    "podagen",
    "polstartdemmurage",
    "polenddemmurage",
    "podstartdemmurage",
    "podenddemmurage",
    "downtimeamount",
    "t1_project_code",
    "t1_faw_sailing_to_finish",
    "t1_actual_arrive_finish",
    "t2_pol_actual_arrive_pol_start",
    "t2_pol_actual_arrive_pol_activitydetail",
    "t2_pol_berthing_start",
    "t2_pol_berthing_last",
    "t2_pol_loading_start_start",
    "t2_pol_loading_progress_start",
    "t2_pol_loading_progress_last",
    "t2_pol_loading_complete_start",
    "t2_pol_loading_complete_activitydetail",
    "t2_pol_loading_complete_cargoremarks",
    "t2_pol_cash_off_jetty_start",
    "t2_pol_wait_document_start",
    "t2_pol_wait_document_last",
    "t2_pol_skab_lhv_start",
    "t2_pol_skab_lhv_activitydetail",
    "t2_pol_document_on_board_start",
    "t2_pol_document_on_board_activitydetail",
    "t2_pol_faw_sailing_to_pod_start",
    "t2_pol_faw_sailing_to_pod_eta",
    "t2_pol_faw_sailing_to_pod_remainingdistance",
    "t2_pol_sailing_to_pod_start",
    "t2_pol_sailing_to_pod_last",
    "t2_pol_sailing_to_pod_speed_avg",
    "t2_pol_sailing_to_pod_rpm_avg",
    "t2_pol_cleaning_start_start",
    "t2_pol_cleaning_start_activitydetail",
    "t2_pol_cleaning_target_start",
    "t2_pol_cleaning_progress_start",
    "t2_pol_cleaning_progress_last",
    "t2_pol_cleaning_complete_start",
    "t2_pol_standby_start",
    "t2_pol_standby_last",
    "t2_pol_bunkering_start",
    "t2_pol_bunkering_last",
    "t2_pol_bunkering_activitydetail",
    "t2_pol_rain_start",
    "t2_pol_rain_stop_start",
    "t2_pol_maintenance_start",
    "t2_pol_maintenance_activitydetail",
    "t2_pol_maintenance_done_start",
    "t2_pol_berlindung_start",
    "t2_pol_berlindung_last",
    "t2_pol_connect_rampdoor_start",
    "t2_pod_actual_arrive_pod_start",
    "t2_pod_berthing_start",
    "t2_pod_berthing_last",
    "t2_pod_discharge_start_start",
    "t2_pod_discharge_progress_start",
    "t2_pod_discharge_progress_last",
    "t2_pod_discharge_complete_start",
    "t2_pod_discharge_complete_activitydetail",
    "t2_pod_discharge_complete_cargoremarks",
    "t2_pod_cash_off_jetty_start",
    "t2_pod_document_on_board_start",
    "t2_pod_faw_sailing_to_finish_start",
    "t2_pod_faw_sailing_to_finish_activitydetail",
    "t2_pod_faw_sailing_to_finish_eta",
    "t2_pod_faw_sailing_to_finish_remainingdistance",
    "t2_pod_sailing_to_finish_start",
    "t2_pod_sailing_to_finish_last",
    "t2_pod_sailing_to_finish_speed_avg",
    "t2_pod_sailing_to_finish_rpm_avg",
    "t2_pod_cleaning_start_start",
    "t2_pod_cleaning_complete_start",
    "t2_pod_standby_start",
    "t2_pod_standby_last",
    "t2_pod_waiting_for_si_start",
    "t2_pod_bunkering_start",
    "t2_pod_bunkering_last",
    "t2_pod_rain_start",
    "t2_pod_rain_stop_start",
    "t2_pod_maintenance_start",
    "t2_pod_maintenance_done_start",
    "t2_pod_connect_rampdoor_start",
    "t2_pod_wait_document_start",
    "t2_pod_wait_document_last",
    "t2_pod_waiting_surveyor_ds_start",
    "t2_finish_actual_arrive_finish_start",
    "data_check"
]

V2_RENAME = {
    "t2_pol_actual_arrive_pol_start": "t2_pol_actual_arrive_pol",
    "t2_pol_loading_start_start": "t2_pol_loading_start",
    "t2_pol_loading_complete_start": "t2_pol_loading_complete",
    "t2_pol_cash_off_jetty_start": "t2_pol_cash_off_jetty",
    "t2_pol_skab_lhv_start": "t2_pol_skab_lhv",
    "t2_pol_document_on_board_start": "t2_pol_document_on_board",
    "t2_pol_faw_sailing_to_pod_start": "t2_pol_faw_sailing_to_pod",
    "t2_pol_faw_sailing_to_pod_remainingdistance": "t2_pol_faw_sailing_to_pod_jarak",
    "t2_pol_cleaning_start_start": "t2_pol_cleaning_start",
    "t2_pol_cleaning_target_start": "t2_pol_cleaning_target",
    "t2_pol_cleaning_complete_start": "t2_pol_cleaning_complete",
    "t2_pol_rain_start": "t2_pol_rain",
    "t2_pol_rain_stop_start": "t2_pol_rain_stop",
    "t2_pol_maintenance_start": "t2_pol_maintenance",
    "t2_pol_maintenance_done_start": "t2_pol_maintenance_done",
    "t2_pol_connect_rampdoor_start": "t2_pol_connect_rampdoor",
    "t2_pod_actual_arrive_pod_start": "t2_pod_actual_arrive_pod",
    "t2_pod_discharge_start_start": "t2_pod_discharge_start",
    "t2_pod_discharge_complete_start": "t2_pod_discharge_complete",
    "t2_pod_cash_off_jetty_start": "t2_pod_cash_off_jetty",
    "t2_pod_document_on_board_start": "t2_pod_document_on_board",
    "t2_pod_faw_sailing_to_finish_start": "t2_pod_faw_sailing_to_finish",
    "t2_pod_faw_sailing_to_finish_remainingdistance": "t2_pod_faw_sailing_to_finish_jarak",
    "t2_pod_cleaning_start_start": "t2_pod_cleaning_start",
    "t2_pod_cleaning_complete_start": "t2_pod_cleaning_complete",
    "t2_pod_waiting_for_si_start": "t2_pod_waiting_for_si",
    "t2_pod_rain_start": "t2_pod_rain",
    "t2_pod_rain_stop_start": "t2_pod_rain_stop",
    "t2_pod_maintenance_start": "t2_pod_maintenance",
    "t2_pod_maintenance_done_start": "t2_pod_maintenance_done",
    "t2_pod_connect_rampdoor_start": "t2_pod_connect_rampdoor",
    "t2_pod_waiting_surveyor_ds_start": "t2_pod_waiting_surveyor_ds",
    "t2_finish_actual_arrive_finish_start": "t2_finish_actual_arrive_finish"
}


def v2_apply_layout(df):
    """Pilih, urutkan, dan ganti nama kolom sesuai V2_KOLOM / V2_RENAME."""

    data, missing = {}, []

    for s in V2_KOLOM:
        d = V2_RENAME.get(s, s)
        if s in df.columns:
            data[d] = df[s]
        else:
            data[d] = pd.Series(np.nan, index=df.index)
            missing.append(s)

    if missing:
        print(f"  ! {len(missing)} kolom sumber tidak ada di data "
              f"(diisi kosong): {missing}")

    out = pd.DataFrame(data, index=df.index)

    if len(set(out.columns)) != len(out.columns):
        raise ValueError("Nama kolom akhir V2 kembar.")
    if max(len(c.encode()) for c in out.columns) > 63:
        raise ValueError("Ada nama kolom akhir V2 > 63 karakter.")

    return out


def v2_finalize(df_turn):

    df = df_turn.copy()

    new_cols = [v2_snake(c) for c in df.columns]
    dup = pd.Series(new_cols)[pd.Series(new_cols).duplicated(keep=False)]
    if len(dup):
        raise ValueError(f"Nama kolom kembar: {dup.tolist()}")
    if max(len(c.encode()) for c in new_cols) > 63:
        raise ValueError("Ada nama kolom > 63 karakter")
    df.columns = new_cols

    for c in ["started_at", "expected_at", "closed_at", "sidate"]:
        if c in df.columns:
            df[c] = v2_to_dt(df[c])

    for c in [c for c in df.columns if c.endswith("_eta")]:
        df[c] = v2_to_dt(df[c])

    num = [
        "price", "demmurage", "bbm", "bargeel_feet", "tugboatel_feet",
        "ondemmurage", "downtimeamount", "polstartdemmurage",
        "polenddemmurage", "podstartdemmurage", "podenddemmurage"
    ]

    for c in df.columns:
        if c in num or c.endswith(("_speed", "_rpm", "_remainingdistance")):
            df[c] = v2_to_num(df[c])

    for c in [c for c in df.columns if c.endswith("_count")]:
        df[c] = v2_to_num(df[c]).astype("Int64")

    df = v2_apply_layout(df)

    if df[V2_KEY].isna().any():
        raise ValueError("Ada project_code kosong")

    dups = df[df[V2_KEY].duplicated(keep=False)][V2_KEY].unique().tolist()
    if dups:
        raise ValueError(f"project_code duplikat: {dups[:10]}")

    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%dT%H:%M:%S")

    out = out.astype(object).where(out.notna(), None)

    return df, out.to_dict(orient="records")


def v2_sql_type(s):
    if pd.api.types.is_datetime64_any_dtype(s):
        return "timestamp"
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_integer_dtype(s):
        return "bigint"
    if pd.api.types.is_float_dtype(s):
        return "double precision"
    return "text"


def v2_req(method, url, retries=3, **kw):

    extra = kw.pop("headers", {})
    err = ""

    for i in range(retries):
        try:
            r = requests.request(
                method,
                url,
                headers={**V2_HDR, **extra},
                timeout=120,
                **kw
            )
            if r.status_code < 300:
                return r
            if r.status_code < 500 and r.status_code != 429:
                raise RuntimeError(f"{r.status_code}: {r.text[:500]}")
            err = f"{r.status_code}: {r.text[:200]}"
        except requests.RequestException as e:
            err = str(e)

        time.sleep(2 ** i)

    raise RuntimeError(f"Gagal setelah {retries}x: {err}")


def v2_existing_keys():

    keys, offset, page = [], 0, 1000

    while True:
        rows = v2_req(
            "GET",
            V2_BASE,
            params={
                "select": V2_KEY,
                "order": V2_KEY,
                "limit": page,
                "offset": offset
            }
        ).json()

        keys += [x[V2_KEY] for x in rows]

        if len(rows) < page:
            return keys

        offset += page


def v2_check_schema(df):
    """Cocokkan kolom df dengan tabel. Kalau beda: cetak SQL lalu berhenti."""

    try:
        r = requests.get(
            f"{SUPABASE_URL.rstrip('/')}/rest/v1/",
            timeout=60,
            headers={**V2_HDR, "Accept": "application/openapi+json"}
        )
        r.raise_for_status()
        props = (
            r.json()
            .get("definitions", {})
            .get(V2_TABLE, {})
            .get("properties")
        )
    except Exception as e:
        print("  ! Cek skema dilewati:", e)
        return

    cols = list(df.columns)

    if not props:
        lines = [
            f'  "{c}" '
            f'{"text primary key" if c == V2_KEY else v2_sql_type(df[c])}'
            for c in cols
        ]
        lines.append('  "synced_at" timestamptz')

        print(f"\nTabel {V2_TABLE} belum ada. Jalankan di SQL Editor:\n")
        print(f"create table {V2_TABLE} (\n" + ",\n".join(lines) + "\n);")
        print("notify pgrst, 'reload schema';")
        raise RuntimeError("Tabel V2 belum dibuat")

    missing = [c for c in cols if c not in props]

    if missing:
        print(f"\n{len(missing)} kolom belum ada di tabel. "
              f"Jalankan di SQL Editor:\n")
        for c in missing:
            print(
                f'alter table {V2_TABLE} add column if not exists '
                f'"{c}" {v2_sql_type(df[c])};'
            )
        print("notify pgrst, 'reload schema';")
        raise RuntimeError("Kolom tabel V2 belum lengkap")

    print("Skema V2 cocok.")


def v2_sync(df, rows):

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [{**r, "synced_at": now} for r in rows]
    new_keys = [r[V2_KEY] for r in rows]

    v2_check_schema(df)

    old_keys = v2_existing_keys()
    print(f"Supabase V2: {len(old_keys):,} baris | baru: {len(rows):,}")

    if not rows:
        raise RuntimeError("Payload V2 kosong")

    if old_keys and len(rows) < V2_MIN_RATIO * len(old_keys):
        raise RuntimeError(
            f"Jumlah baris V2 turun drastis "
            f"({len(rows)} < {V2_MIN_RATIO:.0%} x {len(old_keys)}). "
            f"Cek MERPIS_API_URL (harus year=all)."
        )

    orphans = sorted(set(old_keys) - set(new_keys))
    print(f"V2 upsert: {len(rows):,} | dihapus: {len(orphans):,} {orphans[:10]}")

    if V2_DRY_RUN:
        print("V2_DRY_RUN aktif -> tidak ada yang ditulis.")
        return

    def dumps(o):
        return json.dumps(
            o,
            default=lambda x: x.item() if hasattr(x, "item") else str(x)
        )

    for i in range(0, len(rows), V2_BATCH):
        v2_req(
            "POST",
            V2_BASE,
            params={"on_conflict": V2_KEY},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            data=dumps(rows[i:i + V2_BATCH])
        )
        print(f"  V2 upsert {min(i + V2_BATCH, len(rows)):,}/{len(rows):,}")

    for i in range(0, len(orphans), 100):
        in_list = ",".join(
            '"' + c.replace('"', "") + '"'
            for c in orphans[i:i + 100]
        )
        v2_req(
            "DELETE",
            V2_BASE,
            params={V2_KEY: f"in.({in_list})"},
            headers={"Prefer": "return=minimal"}
        )

    print(f"  V2 hapus baris yatim: {len(orphans):,}")

    final = v2_existing_keys()
    print(f"V2 selesai. Baris di tabel: {len(final):,} (target {len(rows):,})")

    if set(final) != set(new_keys):
        raise RuntimeError("Isi tabel V2 tidak sama dengan payload")


def run_turnaround_v2(raw_data):

    print("===================================")
    print(f"TURNAROUND V2 -> {V2_TABLE}")
    print("===================================")

    data = raw_data["data"]

    if not isinstance(data, dict) or not data.get("monitoring"):
        raise ValueError("Struktur API tidak sesuai: data.monitoring kosong.")

    records = data["monitoring"]

    n_proj = len({r["project"] for r in records})
    print(f"V2 sumber: {len(records):,} event, {n_proj:,} project")

    df_turn = v2_build(records)
    df, rows = v2_finalize(df_turn)
    v2_sync(df, rows)

    return df_turn

# =====================================================
# MAIN PIPELINE
# =====================================================

def main():

    print("===================================")
    print("MERPIS MONITORING DATA PIPELINE")
    print("===================================")

    # 1. FETCH API
    raw_data = fetch_merpis_data()

    # 2. FLATTEN
    df_flat = flatten_data(raw_data)

    # 3. BUILD MATRIX
    df_matrix_final = build_matrix_final(
        df_flat
    )

    # 4. CLEAN
    df_upload = clean_for_supabase(
        df_matrix_final
    )

    # Cek hasil Voyage V.2 di log
    if "Voyage V.2" in df_upload.columns:
        print(
            "Voyage V.2 terisi:",
            df_upload["Voyage V.2"].notna().sum(),
            "dari",
            len(df_upload)
        )
    else:
        print("PERINGATAN: kolom Voyage V.2 tidak ada di data upload.")

    # 5. FULL REFRESH SUPABASE
    upload_to_supabase(
        df_upload
    )

    # 6. TABEL TURNAROUND (Trip 1 + Trip 2)
    df_turnaround = build_turnaround(df_upload)
    upload_turnaround(df_turnaround)

    # 7. TURNAROUND V2 (1 baris per project, kolom sesuai V2_KOLOM)
    run_turnaround_v2(raw_data)

    print("===================================")
    print("PIPELINE BERHASIL")
    print("===================================")


# =====================================================
# EXECUTION
# =====================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print("===================================")
        print("PIPELINE GAGAL")
        print("===================================")
        print(str(e))
        print("===================================")

        sys.exit(1)