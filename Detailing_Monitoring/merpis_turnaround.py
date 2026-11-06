"""
Merpis API (year=all) -> 1 baris per project (T1 ringkas + T2 semua aktivitas)
-> upsert ke Supabase.  Default DRY_RUN=true (tidak menulis apa pun).
"""
import os, re, json, time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

try:
    from dotenv import load_dotenv
    load_dotenv("environment.env", override=True)
except ImportError:
    pass

# ======================= KONFIGURASI =======================
MERPIS_URL   = os.environ["MERPIS_API_URL"]
MERPIS_TOKEN = os.environ["MERPIS_API_TOKEN"]          # sudah berisi "Bearer ..."
SB_URL       = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY       = os.environ["SUPABASE_KEY"]
TABLE        = os.environ.get("TURNAROUND_TABLE", "monitoring_turnaround_v2")
KEY          = "project_code"
BATCH        = 200
MIN_RATIO    = 0.90                                    # guard jumlah baris
DRY_RUN      = os.environ.get("DRY_RUN", "true").lower() not in ("0", "false", "no")

DETAIL = ["activitydetail", "speed", "rpm", "eta", "remainingdistance", "cargoremarks"]

PROJ_COLS = [
    "id", "code", "remarks", "owner", "started_at", "expected_at",
    "closed_at", "closed_by", "tugboatel.name", "bargeel.name",
    "bargeel.feet", "tugboatel.feet", "customerel.shortname",
    "customerel.fullname", "cargoel.name", "polel.name", "polel.shortname",
    "podel.name", "podel.shortname", "podpreviousel.name", "spal", "si",
    "sidate", "price", "prorata", "bbm", "demmurage", "ondemmurage",
    "sapb1", "projectremarks", "polagen", "podagen",
    "polstartdemmurage", "polenddemmurage",
    "podstartdemmurage", "podenddemmurage", "downtimeamount",
]

HDR = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
       "Content-Type": "application/json"}


# ======================= 1. FETCH =======================
def fetch_records():
    r = requests.get(MERPIS_URL, timeout=180,
                     headers={"Accept": "application/json",
                              "Authorization": MERPIS_TOKEN})
    r.raise_for_status()
    recs = r.json()["data"]["monitoring"]
    n_proj = len({x["project"] for x in recs})
    print(f"Merpis  : {len(recs)} event, {n_proj} project")
    if "year=all" not in MERPIS_URL:
        raise SystemExit("ABORT: MERPIS_API_URL harus memakai year=all")
    return recs


# ======================= 2. BANGUN TABEL TURNAROUND =======================
def build_turnaround(records):
    ev_rows, proj = [], {}
    for r in records:
        p = r.get("projectel") or {}
        a = r.get("activitiesel") or {}
        ev = {k: v for k, v in r.items() if k not in ("projectel", "activitiesel")}
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
    res = (g.agg(Start=("started_at", "first"),
                 Last=("started_at", "last"),
                 Count=("id", "size"))
             .join(g[DETAIL].last())
             .reset_index())

    order = (ev.drop_duplicates("act").sort_values("activities")["act"].tolist())
    parts = []
    for act in order:
        s = res[res["act"] == act].set_index("project")
        b = pd.DataFrame(index=s.index)
        b[f"{act} | Start"] = s["Start"]
        if s["Count"].max() > 1:
            b[f"{act} | Last"] = s["Last"]
            b[f"{act} | Count"] = s["Count"]
        for c in DETAIL:
            if s[c].notna().any():
                b[f"{act} | {c}"] = s[c]
        parts.append(b)
    wide = pd.concat(parts, axis=1)

    # ---- info project ----
    cols = [c for c in PROJ_COLS if c in df_proj.columns]
    info = (df_proj[cols]
            .rename(columns={"id": "project_id", "code": "Project Code"}).copy())

    # ---- T1 = trip sebelumnya (lintas tahun), hanya 2 kolom ----
    pat = info["Project Code"].str.extract(r"^(.*)-(\d{4})-(\d+)$")
    info["_prefix"] = pat[0]
    info["_year"] = pat[1].astype(float)
    info["_trip"] = pat[2].astype(float)
    info = info.sort_values(["_prefix", "_year", "_trip"]).reset_index(drop=True)
    info["T1 Project Code"] = info.groupby("_prefix")["Project Code"].shift(1)
    info["_prev_id"] = info.groupby("_prefix")["project_id"].shift(1)

    t1_src = {
        "T1 FAW Sailing To Finish": "POD | FAW Sailing To Finish | Start",
        "T1 Actual Arrive Finish":  "Finish | Actual Arrive Finish | Start",
    }
    for new, src in t1_src.items():
        info[new] = info["_prev_id"].map(wide[src]) if src in wide.columns else pd.NaT

    df = (info.drop(columns=["_prefix", "_year", "_trip", "_prev_id"])
              .merge(wide.add_prefix("T2 | "), left_on="project_id",
                     right_index=True, how="left")
              .reset_index(drop=True))

    # ---- durasi (hari) ----
    def days(a, b):
        if a not in df.columns or b not in df.columns:
            return pd.Series(np.nan, index=df.index)
        return (df[b] - df[a]).dt.total_seconds() / 86400

    POL_ARR = "T2 | POL | Actual Arrive POL | Start"
    POL_LS  = "T2 | POL | Loading Start | Start"
    POL_LC  = "T2 | POL | Loading Complete | Start"
    POL_FAW = "T2 | POL | FAW Sailing To POD | Start"
    POD_ARR = "T2 | POD | Actual Arrive POD | Start"
    POD_DS  = "T2 | POD | Discharge Start | Start"
    POD_DC  = "T2 | POD | Discharge Complete | Start"
    POD_FAW = "T2 | POD | FAW Sailing To Finish | Start"
    FIN_ARR = "T2 | Finish | Actual Arrive Finish | Start"

    df["Voyage V.2 (hari)"] = days("T1 FAW Sailing To Finish", POD_FAW)
    df["Jeda Finish T1 ke Arrive POL (hari)"] = days("T1 Actual Arrive Finish", POL_ARR)
    df["POL Loading (hari)"] = days(POL_LS, POL_LC)
    df["POL Total Port (hari)"] = days(POL_ARR, POL_FAW)
    df["Sailing POL ke POD (hari)"] = days(POL_FAW, POD_ARR)
    df["POD Discharge (hari)"] = days(POD_DS, POD_DC)
    df["POD Total Port (hari)"] = days(POD_ARR, POD_FAW)
    df["Sailing POD ke Finish (hari)"] = days(POD_FAW, FIN_ARR)
    df["Total Voyage Days"] = (df["Sailing POL ke POD (hari)"]
                               + df["Sailing POD ke Finish (hari)"])

    # ---- flag Data Check (durasi negatif) ----
    labels = {
        "Jeda Finish T1 ke Arrive POL (hari)": "Jeda T1-POL negatif",
        "POL Loading (hari)": "POL Loading negatif",
        "Sailing POL ke POD (hari)": "Sailing POL-POD negatif",
        "Sailing POD ke Finish (hari)": "Sailing POD-Finish negatif",
        "Total Voyage Days": "Total Voyage negatif",
    }
    chk = pd.DataFrame({lab: np.where(df[c] < 0, lab, "") for c, lab in labels.items()})
    df["Data Check"] = (chk.apply(lambda r: "; ".join(x for x in r if x), axis=1)
                           .replace("", None))

    print(f"Turnaround: {df.shape[0]} project x {df.shape[1]} kolom | "
          f"tanpa T1: {df['T1 Project Code'].isna().sum()} | "
          f"flag Data Check: {df['Data Check'].notna().sum()}")
    return df


# ======================= 3. RAPIKAN UNTUK SUPABASE =======================
def snake(name):
    s = name.replace("T2 | ", "t2_").replace("T1 ", "t1_")
    s = s.replace("|", " ").replace("/", " ").replace(".", "_")
    s = re.sub(r"[^0-9a-zA-Z_ ]+", " ", s)
    s = re.sub(r"\s+", "_", s.strip()).lower()
    return re.sub(r"_+", "_", s)

def to_num(s):
    out = pd.to_numeric(s, errors="coerce")
    lost = int((s.notna() & out.isna()).sum())
    if lost:
        print(f"  ! {lost} nilai non-angka di '{s.name}' jadi null")
    return out

def to_dt(s):
    return pd.to_datetime(s, errors="coerce", utc=True).dt.tz_localize(None)

def finalize(df_turn):
    df = df_turn.copy()
    new = [snake(c) for c in df.columns]
    dup = pd.Series(new)[pd.Series(new).duplicated(keep=False)].tolist()
    assert not dup, f"Nama kolom kembar: {dup}"
    assert max(len(c.encode()) for c in new) <= 63, "Ada nama kolom > 63 karakter"
    df.columns = new

    for c in ["started_at", "expected_at", "closed_at", "sidate"]:
        if c in df.columns:
            df[c] = to_dt(df[c])
    for c in [c for c in df.columns if c.endswith("_eta")]:
        df[c] = to_dt(df[c])

    num = ["price", "demmurage", "bbm", "bargeel_feet", "tugboatel_feet",
           "ondemmurage", "downtimeamount", "polstartdemmurage",
           "polenddemmurage", "podstartdemmurage", "podenddemmurage"]
    for c in df.columns:
        if c in num or c.endswith(("_speed", "_rpm", "_remainingdistance")):
            df[c] = to_num(df[c])
    for c in [c for c in df.columns if c.endswith("_count")]:
        df[c] = to_num(df[c]).astype("Int64")

    dups = df[df[KEY].duplicated(keep=False)][KEY].unique().tolist()
    assert not dups, f"project_code duplikat: {dups[:10]}"
    assert df[KEY].notna().all(), "Ada project_code kosong"

    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%dT%H:%M:%S")
    out = out.astype(object).where(out.notna(), None)
    return df, out.to_dict(orient="records")


# ======================= 4. SUPABASE =======================
BASE = f"{SB_URL}/rest/v1/{TABLE}"

def sql_type(s):
    if pd.api.types.is_datetime64_any_dtype(s): return "timestamp"
    if pd.api.types.is_bool_dtype(s):           return "boolean"
    if pd.api.types.is_integer_dtype(s):        return "bigint"
    if pd.api.types.is_float_dtype(s):          return "double precision"
    return "text"

def req(method, url, retries=3, **kw):
    extra = kw.pop("headers", {})
    err = ""
    for i in range(retries):
        try:
            r = requests.request(method, url, headers={**HDR, **extra},
                                 timeout=120, **kw)
            if r.status_code < 300:
                return r
            if r.status_code < 500 and r.status_code != 429:
                raise RuntimeError(f"{r.status_code}: {r.text[:500]}")
            err = f"{r.status_code}: {r.text[:200]}"
        except requests.RequestException as e:
            err = str(e)
        time.sleep(2 ** i)
    raise RuntimeError(f"Gagal setelah {retries}x: {err}")

def existing_keys():
    keys, offset, page = [], 0, 1000
    while True:
        rows = req("GET", BASE, params={"select": KEY, "order": KEY,
                                        "limit": page, "offset": offset}).json()
        keys += [x[KEY] for x in rows]
        if len(rows) < page:
            return keys
        offset += page

def check_schema(df):
    """Bandingkan kolom df dengan tabel. Cetak DDL/ALTER lalu stop kalau tidak cocok."""
    try:
        r = requests.get(f"{SB_URL}/rest/v1/", timeout=60,
                         headers={**HDR, "Accept": "application/openapi+json"})
        r.raise_for_status()
        props = r.json().get("definitions", {}).get(TABLE, {}).get("properties")
    except Exception as e:
        print("  ! Cek skema dilewati:", e)
        return
    cols = [c for c in df.columns]
    if not props:
        lines = [f'  "{c}" {"text primary key" if c == KEY else sql_type(df[c])}' for c in cols]
        lines.append('  "synced_at" timestamptz')
        print(f"\nTabel {TABLE} belum ada. Jalankan di SQL Editor:\n")
        print(f"create table {TABLE} (\n" + ",\n".join(lines) + "\n);")
        print("notify pgrst, 'reload schema';")
        raise SystemExit("ABORT: buat tabel dulu")
    missing = [c for c in cols if c not in props]
    if missing:
        print(f"\n{len(missing)} kolom belum ada di tabel. Jalankan di SQL Editor:\n")
        for c in missing:
            print(f'alter table {TABLE} add column if not exists "{c}" {sql_type(df[c])};')
        print("notify pgrst, 'reload schema';")
        raise SystemExit("ABORT: tambah kolom dulu")
    print("Skema   : cocok")

def sync(df, rows):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [{**r, "synced_at": now} for r in rows]
    new_keys = [r[KEY] for r in rows]

    check_schema(df)

    old_keys = existing_keys()
    print(f"Supabase: {len(old_keys)} baris | baru: {len(rows)}")
    if not rows:
        raise SystemExit("ABORT: payload kosong")
    if old_keys and len(rows) < MIN_RATIO * len(old_keys):
        raise SystemExit(f"ABORT: jumlah baru turun drastis ({len(rows)} < "
                         f"{MIN_RATIO:.0%} x {len(old_keys)})")

    orphans = sorted(set(old_keys) - set(new_keys))
    print(f"Upsert  : {len(rows)} | dihapus: {len(orphans)} {orphans[:10]}")

    if DRY_RUN:
        print("\nDRY_RUN=true -> tidak ada yang ditulis.")
        return

    dumps = lambda o: json.dumps(o, default=lambda x: x.item() if hasattr(x, "item") else str(x))
    for i in range(0, len(rows), BATCH):
        req("POST", BASE, params={"on_conflict": KEY},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            data=dumps(rows[i:i + BATCH]))
        print(f"  upsert {min(i + BATCH, len(rows))}/{len(rows)}")

    for i in range(0, len(orphans), 100):
        in_list = ",".join('"' + c.replace('"', "") + '"' for c in orphans[i:i + 100])
        req("DELETE", BASE, params={KEY: f"in.({in_list})"},
            headers={"Prefer": "return=minimal"})
    print(f"  hapus yatim: {len(orphans)}")

    final = existing_keys()
    print(f"\nSelesai. Baris di tabel: {len(final)} (target {len(rows)})")
    assert set(final) == set(new_keys), "Isi tabel tidak sama dengan payload"


# ======================= MAIN =======================
def main():
    records = fetch_records()
    df_turn = build_turnaround(records)
    df, rows = finalize(df_turn)
    sync(df, rows)
    return df_turn          # untuk dicek di Jupyter

if __name__ == "__main__":
    df_turn = main()
