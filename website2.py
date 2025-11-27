import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from google import genai
import io

# -------------------------------
# 🌐 Page Config
# -------------------------------
st.set_page_config(
    page_title="Patient Monitoring System",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# -------------------------------
# 🌐 Google Sheets Setup (uses st.secrets for Streamlit Cloud)
# -------------------------------
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1oUyY7W9scIdWd6K5nmyyzZ9qshsKSzQdmAsGybMecsU/edit?usp=sharing"

# Sheet names used by the app (from your message)
USER_SHEET = "Users"
DOCTOR_SHEET = "Doctors"
ASSIGN_SHEET = "Assignments"
DATA_SHEET = "Data"
PROFILES_SHEET = "Profiles"
AUDIT_SHEET = "AuditLog"

scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Credentials: prefer st.secrets["gcp_service_account"], fallback to local file
try:
    credentials = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
except Exception:
    credentials = Credentials.from_service_account_file("service_account.json", scopes=scope)

client = gspread.authorize(credentials)

# Gemini (Gemini API key stored in st.secrets["gemini"]["api_key"])
try:
    GEMINI_API_KEY = st.secrets["gemini"]["api_key"]
    client_genai = genai.Client(api_key=GEMINI_API_KEY)
except Exception:
    client_genai = None

# -------------------------------
# CACHED ACCESS HELPERS (to prevent 429 errors)
# -------------------------------

@st.cache_resource
def get_sheet_client():
    # open_by_url is used to avoid lookup by name
    return client.open_by_url(SPREADSHEET_URL)

@st.cache_resource
def get_worksheet(name: str):
    sh = get_sheet_client()
    return sh.worksheet(name)

@st.cache_data(ttl=120)
def load_sheet(sheet_name: str) -> pd.DataFrame:
    """Return a DataFrame for the given sheet name (cached)."""
    try:
        ws = get_worksheet(sheet_name)
        data = ws.get_all_records()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        # normalize column names (strip)
        df.columns = [c.strip() for c in df.columns]
        return df
    except Exception as e:
        st.error(f"Unable to load sheet {sheet_name}: {e}")
        return pd.DataFrame()

def clear_read_cache():
    try:
        st.cache_data.clear()
    except Exception:
        pass

# -------------------------------
# Ensure sheets exist and have headers (low-frequency; OK to call once)
# -------------------------------
def ensure_sheet_exists(title, headers):
    try:
        sh = get_sheet_client()
        try:
            _ = sh.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=title, rows=2000, cols=max(10, len(headers)))
            ws.append_row(headers)
    except Exception as e:
        st.error(f"Unable to ensure sheet {title}: {e}")

ensure_sheet_exists(USER_SHEET, ["Username", "Password", "Role", "Name", "Age", "Gender", "Condition"])
ensure_sheet_exists(DOCTOR_SHEET, ["Username", "Password", "Role", "FullName", "Specialty", "Hospital", "Bio"])
ensure_sheet_exists(ASSIGN_SHEET, ["Patient", "Doctor"])
ensure_sheet_exists(DATA_SHEET, ["Timestamp","Username","IN","MT","RI","PT","TH","TH_Force","IN_Force","MT_Force","RI_Force","PT_Force","Pain_Scale","Fatigue_Scale"])
ensure_sheet_exists(PROFILES_SHEET, ["Username", "Name", "Age", "Condition", "Specialization", "Role"])
ensure_sheet_exists(AUDIT_SHEET, ["Timestamp", "Manager", "Action", "Details"])

# -------------------------------
# Basic read/write helpers (writes will clear cache afterward)
# -------------------------------
def append_row(sheet_name: str, row: list):
    try:
        ws = get_worksheet(sheet_name)
        ws.append_row(row)
        clear_read_cache()
        return True
    except Exception as e:
        st.error(f"Failed to append to {sheet_name}: {e}")
        return False

def append_rows(sheet_name: str, rows: list):
    try:
        ws = get_worksheet(sheet_name)
        ws.append_rows(rows)
        clear_read_cache()
        return True
    except Exception as e:
        try:
            for r in rows:
                ws.append_row(r)
            clear_read_cache()
            return True
        except Exception as e2:
            st.error(f"Failed to append multiple rows to {sheet_name}: {e2}")
            return False

def clear_and_update_sheet(sheet_name: str, records):
    """
    records: list of dicts (keys = header names) OR pandas DataFrame
    This will clear the sheet and write header + rows.
    """
    try:
        ws = get_worksheet(sheet_name)
        ws.clear()
        if records is None or len(records) == 0:
            clear_read_cache()
            return True
        if isinstance(records, pd.DataFrame):
            df = records.copy()
        else:
            df = pd.DataFrame(records)
        header = list(df.columns)
        ws.append_row(header)
        values = df.fillna("").values.tolist()
        if values:
            ws.append_rows(values)
        clear_read_cache()
        return True
    except Exception as e:
        st.error(f"Failed to clear/update sheet {sheet_name}: {e}")
        return False

# -------------------------------
# Convenience loaders that use cached load_sheet(...)
# -------------------------------
def load_users():
    df = load_sheet(USER_SHEET)
    if df.empty:
        return pd.DataFrame(columns=["Username","Password","Role","Name","Age","Gender","Condition"])
    df.columns = [c.strip() for c in df.columns]
    if "Role" in df.columns:
        df["Role"] = df["Role"].astype(str).str.strip().str.lower()
    else:
        df["Role"] = ""
    if "Username" in df.columns:
        df["Username"] = df["Username"].astype(str).str.strip()
    return df

def load_doctors():
    df = load_sheet(DOCTOR_SHEET)
    if df.empty:
        return pd.DataFrame(columns=["Username", "Password", "Role", "FullName", "Specialty", "Hospital", "Bio"])
    df.columns = [c.strip() for c in df.columns]
    return df

def load_assignments():
    df = load_sheet(ASSIGN_SHEET)
    if df.empty:
        return pd.DataFrame(columns=["Patient","Doctor"])
    df.columns = [c.strip() for c in df.columns]
    df["Patient"] = df["Patient"].astype(str).str.strip()
    df["Doctor"] = df["Doctor"].astype(str).str.strip()
    return df

def load_data():
    df = load_sheet(DATA_SHEET)
    if df.empty:
        return pd.DataFrame(columns=["Timestamp","Username","IN","MT","RI","PT","TH","TH_Force","IN_Force","MT_Force","RI_Force","PT_Force","Pain_Scale","Fatigue_Scale"])
    df.columns = [c.strip() for c in df.columns]

    # Convert the sheet's Pain_Scale / Fatigue_Scale to internal Pain / Fatigue fields
    if "Pain_Scale" in df.columns and "Pain" not in df.columns:
        df["Pain"] = df["Pain_Scale"]
    if "Fatigue_Scale" in df.columns and "Fatigue" not in df.columns:
        df["Fatigue"] = df["Fatigue_Scale"]

    return df

def load_profiles():
    df = load_sheet(PROFILES_SHEET)
    if df.empty:
        return pd.DataFrame(columns=["Username", "Name", "Age", "Condition", "Specialization", "Role"])
    df.columns = [c.strip() for c in df.columns]
    return df

def load_audit():
    df = load_sheet(AUDIT_SHEET)
    if df.empty:
        return pd.DataFrame(columns=["Timestamp","Manager","Action","Details"])
    df.columns = [c.strip() for c in df.columns]
    return df

# -------------------------------
# Audit logging
# -------------------------------
def log_audit(manager, action, details=""):
    try:
        append_row(AUDIT_SHEET, [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), manager, action, details])
    except Exception as e:
        st.error(f"Failed to write audit log: {e}")

# -------------------------------
# Assignment functions
# -------------------------------
def assign_doctor(patient, doctor, manager_user=None):
    try:
        df_assign = load_assignments()
        df_assign = df_assign[df_assign["Patient"].str.lower() != str(patient).strip().lower()]
        df_new = pd.concat([df_assign, pd.DataFrame([{"Patient": patient, "Doctor": doctor}])], ignore_index=True)
        clear_and_update_sheet(ASSIGN_SHEET, df_new)
        if manager_user:
            log_audit(manager_user, "Assign Doctor", f"{patient} -> {doctor}")
        st.success(f"Assigned {patient} → {doctor}")
        return True
    except Exception as e:
        st.error(f"Failed to assign doctor: {e}")
        return False

def remove_assignment(patient, manager_user=None):
    try:
        df_assign = load_assignments()
        if df_assign.empty or patient not in df_assign["Patient"].tolist():
            st.info("No assignment found for that patient.")
            return False
        df_new = df_assign[df_assign["Patient"].str.lower() != str(patient).strip().lower()]
        clear_and_update_sheet(ASSIGN_SHEET, df_new)
        if manager_user:
            log_audit(manager_user, "Remove Assignment", f"{patient}")
        st.success(f"Removed assignment for {patient}")
        return True
    except Exception as e:
        st.error(f"Failed to remove assignment: {e}")
        return False

def get_doctor_for_patient(patient):
    df = load_assignments()
    if df.empty: return None
    matches = df[df["Patient"].astype(str).str.lower() == str(patient).strip().lower()]
    if not matches.empty:
        return matches["Doctor"].iloc[0]
    return None

def get_patients_for_doctor(doctor):
    df = load_assignments()
    if df.empty: return []
    matches = df[df["Doctor"].astype(str).str.lower() == str(doctor).strip().lower()]
    return matches["Patient"].tolist() if not matches.empty else []

# -------------------------------
# User management
# -------------------------------
def save_user(username, password, role="patient", name="", age="", gender="", condition=""):
    try:
        ws = get_worksheet(USER_SHEET)
        ws.append_row([username, password, role, name, age, gender, condition])
        clear_read_cache()
        return True
    except Exception as e:
        st.error(f"Error saving user: {e}")
        return False

# -------------------------------
# Session + Auth init
# -------------------------------
if "page" not in st.session_state:
    st.session_state.page = "login"
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "role" not in st.session_state:
    st.session_state.role = None
if "username" not in st.session_state:
    st.session_state.username = None

def login_action():
    username = st.session_state.get("login_user", "")
    password = st.session_state.get("login_pass", "")
    df_users = load_users()

    matched = pd.DataFrame()
    if "Username" in df_users.columns:
        matched = df_users[df_users["Username"].astype(str).str.strip().str.lower() == str(username).strip().lower()]

    if matched.empty:
        st.error("❌ Username not found")
        return

    stored_pwd = str(matched.iloc[0].get("Password","")).strip()
    if stored_pwd == str(password).strip():
        st.session_state.logged_in = True
        st.session_state.username = str(matched.iloc[0].get("Username","")).strip()
        st.session_state.role = str(matched.iloc[0].get("Role","patient")).strip().lower()
        st.session_state.page = "main"
    else:
        st.error("❌ Username or Password is incorrect")

def logout_action():
    for key in ["logged_in","role","username","page"]:
        st.session_state.pop(key, None)
    st.session_state.page = "login"

def register_action():
    username = st.session_state.get("reg_user","").strip()
    password = st.session_state.get("reg_pass","")
    confirm = st.session_state.get("reg_confirm","")
    name = st.session_state.get("reg_name","")
    age = st.session_state.get("reg_age","")
    gender = st.session_state.get("reg_gender","")
    condition = st.session_state.get("reg_condition","")
    if not username or not password:
        st.warning("Please enter username and password")
        return
    if password != confirm:
        st.warning("Passwords do not match")
        return
    df = load_users()
    if username.lower() in df["Username"].astype(str).str.lower().tolist():
        st.error("This username already exists")
        return
    # create user in Users and profile in Profiles
    if save_user(username, password, role="patient", name=name, age=age, gender=gender, condition=condition):
        # add to profiles sheet
        try:
            ws_profiles = get_worksheet(PROFILES_SHEET)
            ws_profiles.append_row([username, name, age, condition, "", "patient"])
            clear_read_cache()
        except Exception as e:
            st.error(f"Failed to create profile row: {e}")
        st.success("Registration successful. Please log in.")
        st.session_state.page = "login"

# -------------------------------
# Pages
# -------------------------------
def patient_page():
    st.title("🧑‍⚕️ Patient Data Entry")
    st.markdown(f"👤 Patient Name: **{st.session_state.username}**")

    in_flex = st.number_input("IN Flex (degrees)", 0, 180, 0, key="in_flex")
    mt_flex = st.number_input("MT Flex (degrees)", 0, 180, 0, key="mt_flex")
    ri_flex = st.number_input("RI Flex (degrees)", 0, 180, 0, key="ri_flex")
    pt_flex = st.number_input("PT Flex (degrees)", 0, 180, 0, key="pt_flex")
    th_flex = st.number_input("TH Flex (degrees)", 0, 180, 0, key="th_flex")

    st.markdown("### 💪 Force Values for Each Part")
    in_force = st.number_input("IN Force", 0.0, 1000.0, 0.0, key="in_force")
    mt_force = st.number_input("MT Force", 0.0, 1000.0, 0.0, key="mt_force")
    ri_force = st.number_input("RI Force", 0.0, 1000.0, 0.0, key="ri_force")
    pt_force = st.number_input("PT Force", 0.0, 1000.0, 0.0, key="pt_force")
    th_force = st.number_input("TH Force", 0.0, 1000.0, 0.0, key="th_force")

    pain = st.slider("Pain Scale", 0, 10, 0, key="pain")
    fatigue = st.slider("Fatigue Scale", 0, 10, 0, key="fatigue")
    notes = st.text_area("Notes (optional)")

    if st.button("💾 Save Data"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [
            timestamp,
            st.session_state.username,
            in_flex, mt_flex, ri_flex, pt_flex, th_flex,
            th_force, in_force, mt_force, ri_force, pt_force,
            pain, fatigue
        ]
        # match Data sheet order: Timestamp | Username | IN | MT | RI | PT | TH | TH_Force | IN_Force | MT_Force | RI_Force | PT_Force | Pain_Scale | Fatigue_Scale
        ok = append_row(DATA_SHEET, row)
        if ok:
            st.success("✅ Data saved successfully!")
        else:
            st.error("❌ Unable to save data")

    st.markdown("---")
    st.subheader("👨‍⚕️ Assigned Doctor")
    doctor_username = get_doctor_for_patient(st.session_state.username)
    if doctor_username:
        doctors = load_doctors()
        doc_row = doctors[doctors["Username"].astype(str).str.lower() == str(doctor_username).strip().lower()]
        if not doc_row.empty:
            doc = doc_row.iloc[0]
            st.markdown(f"**Name:** {doc.get('FullName','N/A')}")
            st.markdown(f"**Specialty:** {doc.get('Specialty','N/A')}")
            st.markdown(f"**Hospital:** {doc.get('Hospital','N/A')}")
            st.markdown(f"**Bio:** {doc.get('Bio','N/A')}")
        else:
            st.warning("Doctor record not found in Doctors sheet.")
    else:
        st.info("No doctor assigned yet. Ask your clinic to assign a doctor.")

def my_data_page():
    st.title("📊 My Data")
    try:
        df = load_data()
    except Exception as e:
        st.error(f"Failed to load data: {e}")
        return

    if df.empty:
        st.warning("No data in system yet")
        return

    if "Username" not in df.columns:
        st.error("Username column not found in data sheet")
        return

    my_username = st.session_state.username.strip().lower()
    df_user = df[df["Username"].astype(str).str.lower() == my_username]
    if df_user.empty:
        st.info("No data has been entered yet")
        return

    st.success(f"Found {len(df_user)} records")
    st.dataframe(df_user, use_container_width=True)

def doctor_page_view():
    st.title("👨‍⚕️ Doctor Dashboard")
    try:
        df = load_data()
    except Exception as e:
        st.error(f"Failed to load data: {e}")
        return
    if df.empty:
        st.warning("No patient data in system yet")
        return

    my_patients = get_patients_for_doctor(st.session_state.username)
    if not my_patients:
        st.info("You have no patients assigned yet.")
        return

    df_patients = df[df["Username"].astype(str).isin(my_patients)].copy()
    numeric_cols = ["IN","MT","RI","PT","TH","TH_Force","IN_Force","MT_Force","RI_Force","PT_Force","Pain","Fatigue"]
    for c in numeric_cols:
        if c in df_patients.columns:
            df_patients[c] = pd.to_numeric(df_patients[c], errors="coerce")

    if "Timestamp" in df_patients.columns:
        df_patients["Timestamp"] = pd.to_datetime(df_patients["Timestamp"], errors="coerce")
    col1, col2, col3, col4 = st.columns([2,2,3,3])
    col1.metric("Number of Patients", len(my_patients))
    col2.metric("Total Records", len(df_patients))
    existing_flex = [c for c in ["IN","MT","RI","PT","TH"] if c in df_patients.columns]
    if existing_flex:
        col3.metric("Average Flex", round(df_patients[existing_flex].mean().mean(),2))
    else:
        col3.metric("Average Flex", "-")
    if "Timestamp" in df_patients.columns:
        latest_time = df_patients["Timestamp"].dropna().max()
        col4.metric("Latest Record", latest_time.strftime("%Y-%m-%d %H:%M:%S") if pd.notna(latest_time) else "-")
    else:
        col4.metric("Latest Record", "-")

    st.dataframe(df_patients, use_container_width=True)

    flex_cols = [c for c in ["IN","MT","RI","PT","TH"] if c in df_patients.columns]
    force_cols = [c for c in ["TH_Force","IN_Force","MT_Force","RI_Force","PT_Force"] if c in df_patients.columns]
    if flex_cols and "Timestamp" in df_patients.columns:
        fig_flex = px.line(df_patients.sort_values("Timestamp"), x="Timestamp", y=flex_cols, title="Flex Trends")
        st.plotly_chart(fig_flex, use_container_width=True)
    if force_cols:
        fig_force = px.bar(df_patients.groupby("Username")[force_cols].mean().reset_index(), x="Username", y=force_cols, barmode="group", title="Avg Force per Patient")
        st.plotly_chart(fig_force, use_container_width=True)
    if "Pain" in df_patients.columns and "Fatigue" in df_patients.columns:
        fig_pf = px.scatter(df_patients, x="Pain", y="Fatigue", color="Username", title="Pain vs Fatigue")
        st.plotly_chart(fig_pf, use_container_width=True)

def extra_page():
    st.markdown("<h1 style='text-align:center;'>AI KPI Analytics</h1>", unsafe_allow_html=True)
    try:
        df = load_data()
    except Exception as e:
        st.error(f"❌ Failed to load data: {e}")
        return
    if df.empty:
        st.info("No data yet. Please add patient data first.")
    else:
        st.markdown("### 🧾 Raw Patient Data (for filtering and review)")
        name = st.text_input("🔍 Search Patient Name")
        df_filtered = df[df["Username"].str.contains(name, case=False, na=False)] if name else df

        st.dataframe(df_filtered, use_container_width=True, height=300)
        st.markdown("---")

        st.markdown("### 🧠 Preprocessed Astronaut KPI Schema")
        df_a = df_filtered.copy()
        if "Timestamp" in df_a.columns:
            df_a['Timestamp'] = pd.to_datetime(df_a['Timestamp'], errors='coerce')
        else:
            df_a['Timestamp'] = pd.NaT

        df_a = df_a.sort_values('Timestamp')
        df_a['Week'] = ["W" + str(i+1) for i in range(len(df_a))]
        df_a['Phase'] = "P1"
        df_a['Adherence (%)'] = 100

        force_cols = ["TH_Force", "IN_Force", "MT_Force", "RI_Force", "PT_Force"]
        for col in force_cols:
            if col in df_a.columns:
                df_a[col] = pd.to_numeric(df_a[col], errors='coerce')
            else:
                df_a[col] = pd.NA

        df_a["Hand: Avg Grip Force"] = df_a[force_cols].mean(axis=1).round(2)
        df_a["Hand: VR Error Rate (%)"] = "N/A"
        df_a["Chest: Avg COM-BOS Angle (°)"] = "N/A"
        df_a["Balance: Alarm Triggers/Min"] = "N/A"
        df_a["Locomotion: Max Angle Spike (°)"] = "N/A"
        df_a["P4: Time to Stability (sec)"] = "N/A"

        if "Fatigue" in df_a.columns:
            df_a["Fatigue Avg (1–10)"] = df_a["Fatigue"]
        elif "Fatigue_Scale" in df_a.columns:
            df_a["Fatigue Avg (1–10)"] = df_a["Fatigue_Scale"]
        else:
            df_a["Fatigue Avg (1–10)"] = pd.NA

        if "Pain" in df_a.columns:
            df_a["Pain Avg (0–10)"] = df_a["Pain"]
        elif "Pain_Scale" in df_a.columns:
            df_a["Pain Avg (0–10)"] = df_a["Pain_Scale"]
        else:
            df_a["Pain Avg (0–10)"] = pd.NA

        final_cols = [
            "Week", "Phase", "Adherence (%)",
            "Hand: Avg Grip Force", "Hand: VR Error Rate (%)",
            "Chest: Avg COM-BOS Angle (°)", "Balance: Alarm Triggers/Min",
            "Locomotion: Max Angle Spike (°)", "P4: Time to Stability (sec)",
            "Fatigue Avg (1–10)", "Pain Avg (0–10)"
        ]

        for col in final_cols:
            if col not in df_a.columns:
                df_a[col] = pd.NA

        st.markdown("#### ✏️ Editable Preprocessed Table")
        edited = st.data_editor(df_a[final_cols], use_container_width=True, num_rows="dynamic")
        df_a = edited.copy()

        numeric_cols = [
            "Adherence (%)", "Hand: Avg Grip Force",
            "Chest: Avg COM-BOS Angle (°)", "Balance: Alarm Triggers/Min",
            "Locomotion: Max Angle Spike (°)", "P4: Time to Stability (sec)",
            "Fatigue Avg (1–10)", "Pain Avg (0–10)"
        ]
        for col in numeric_cols:
            df_a[col] = pd.to_numeric(df_a[col], errors='coerce')

        st.subheader("📊 Processed Schema Preview")
        st.dataframe(df_a, use_container_width=True, height=300)
        message = st.text_input("📜 Message")
        if st.button("📩 Send To AI"):
            with st.spinner("AI Analyzing..."):
                if client_genai is None:
                    st.error("AI client not configured (missing gemini API key in st.secrets).")
                else:
                    prompt1 = f"""
You are a Clinical Rehabilitation Analytics System designed for Astronaut Hand-Body Integration training.
INPUT CSV:
{df_a.to_csv(index=False)}
"""
                    try:
                        response = client_genai.models.generate_content(model="gemini-2.5-flash", contents=prompt1)
                        st.subheader("🧠 AI Q&A")
                        st.markdown(response.text, unsafe_allow_html=True)
                    except Exception as e:
                        st.error(f"AI request failed: {e}")

        if st.button("🚀 Run AI KPI Analysis"):
            with st.spinner("Running AI analysis..."):
                if client_genai is None:
                    st.error("AI client not configured (missing gemini API key in st.secrets).")
                else:
                    prompt = f"""
You are a Clinical Rehabilitation Analytics System designed for Astronaut Hand-Body Integration training.
INPUT CSV DATA (below this line):
{df_a.to_csv(index=False)}
"""
                    try:
                        response = client_genai.models.generate_content(model="gemini-2.5-flash", contents=prompt)
                        st.subheader("🧠 AI KPI Summary Output")
                        st.markdown(response.text, unsafe_allow_html=True)
                    except Exception as e:
                        st.error(f"AI request failed: {e}")

# -------------------------------
# Doctor profile and Patient profile (hybrid mode: doctors -> Doctors sheet, patients -> Profiles sheet)
# -------------------------------
def doctor_profile():
    st.title("👨‍⚕️ Doctor Profile")
    username = st.session_state.username
    doctors = load_doctors()
    if doctors.empty:
        st.error("Doctors sheet is empty or could not be loaded.")
        return

    doc_row = doctors[doctors["Username"].astype(str).str.lower() == str(username).strip().lower()]
    if doc_row.empty:
        st.error("Doctor profile not found.")
        return
    doc = doc_row.iloc[0]

    st.subheader("📋 Basic Information")
    st.markdown(f"**Full Name:** {doc.get('FullName','N/A')}")
    st.markdown(f"**Username:** {doc.get('Username','N/A')}")
    st.markdown(f"**Specialty:** {doc.get('Specialty','N/A')}")
    st.markdown(f"**Hospital:** {doc.get('Hospital','N/A')}")
    st.markdown(f"**Bio:** {doc.get('Bio','N/A')}")
    # show role from Users sheet as source of truth for login role
    users = load_users()
    user_row = users[users["Username"].astype(str).str.lower() == str(username).strip().lower()]
    if not user_row.empty:
        st.markdown(f"**Role:** {user_row.iloc[0].get('Role','N/A')}")

    st.markdown("---")
    st.subheader("👥 Assigned Patients")
    assigned_usernames = get_patients_for_doctor(username)
    if not assigned_usernames:
        st.info("No assigned patients.")
        return

    profiles_df = load_profiles()
    if profiles_df.empty:
        st.info("Profiles sheet empty; assigned patient usernames exist but profiles cannot be loaded.")
        return

    assigned_profiles = profiles_df[profiles_df["Username"].astype(str).str.lower().isin([p.strip().lower() for p in assigned_usernames])]
    if assigned_profiles.empty:
        st.info("Assigned patients not found in Profiles sheet.")
        return

    display_cols = [c for c in ["Username", "Name", "Age", "Condition"] if c in assigned_profiles.columns]
    st.dataframe(assigned_profiles[display_cols].reset_index(drop=True), use_container_width=True)

def patient_profile():
    st.title("👤 Patient Profile")
    username = st.session_state.username
    profiles = load_profiles()
    if profiles.empty:
        st.error("Profiles sheet is empty or could not be loaded.")
        return

    pat_row = profiles[profiles["Username"].astype(str).str.lower() == str(username).strip().lower()]
    if pat_row.empty:
        st.error("Patient profile not found.")
        return
    pat = pat_row.iloc[0]

    st.subheader("📋 Basic Information")
    st.markdown(f"**Name:** {pat.get('Name','N/A')}")
    st.markdown(f"**Username:** {pat.get('Username','N/A')}")
    st.markdown(f"**Age:** {pat.get('Age','N/A')}")
    st.markdown(f"**Condition:** {pat.get('Condition','N/A')}")
    # role from profiles or users
    if "Role" in pat.index:
        st.markdown(f"**Role:** {pat.get('Role','N/A')}")
    else:
        users = load_users()
        u = users[users["Username"].astype(str).str.lower() == str(username).strip().lower()]
        if not u.empty:
            st.markdown(f"**Role:** {u.iloc[0].get('Role','N/A')}")

    st.markdown("---")
    st.subheader("👨‍⚕️ Assigned Doctor")
    doctor_username = get_doctor_for_patient(username)
    if doctor_username:
        doctors = load_doctors()
        doc_row = doctors[doctors["Username"].astype(str).str.lower() == str(doctor_username).strip().lower()]
        if not doc_row.empty:
            doc = doc_row.iloc[0]
            st.markdown(f"**Name:** {doc.get('FullName','N/A')}")
            st.markdown(f"**Specialty:** {doc.get('Specialty','N/A')}")
        else:
            # fallback to profiles (if doctor was stored there)
            profiles_df = load_profiles()
            doc_row2 = profiles_df[profiles_df["Username"].astype(str).str.lower() == str(doctor_username).strip().lower()] if not profiles_df.empty else pd.DataFrame()
            if not doc_row2.empty:
                d = doc_row2.iloc[0]
                st.markdown(f"**Name:** {d.get('Name','N/A')}")
                st.markdown(f"**Specialization:** {d.get('Specialization','N/A')}")
            else:
                st.warning("Assigned doctor not found in Doctors or Profiles sheet.")
    else:
        st.info("No doctor assigned.")

    st.markdown("---")
    st.subheader("📝 Recent Data Logs")
    try:
        df = load_data()
    except Exception as e:
        st.error(f"Failed to load data: {e}")
        return

    if df.empty:
        st.info("No data recorded yet.")
        return

    if "Username" not in df.columns:
        st.error("Data sheet does not contain 'Username' column.")
        return

    logs = df[df["Username"].astype(str).str.lower() == str(username).strip().lower()].copy()
    if logs.empty:
        st.info("No rehab data submitted yet.")
        return

    if "Timestamp" in logs.columns:
        logs["Timestamp"] = pd.to_datetime(logs["Timestamp"], errors="coerce")
        logs = logs.sort_values("Timestamp", ascending=False)

    st.dataframe(logs.head(20), use_container_width=True)

# -------------------------------
# Manager Dashboard (full)
# -------------------------------
def manager_dashboard():
    st.title("🧑‍💼 Manager Dashboard")

    df_all = load_data()
    df_doctors = load_doctors()
    df_users = load_users()
    df_assign = load_assignments()

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Patients", df_users[df_users["Role"] == "patient"]["Username"].nunique() if not df_users.empty else 0)
    col2.metric("Total Doctors", df_doctors["Username"].nunique() if not df_doctors.empty else 0)
    col3.metric("Total Assignments", len(df_assign) if not df_assign.empty else 0)

    st.markdown("---")
    st.subheader("Global Patient Analytics (All Patients)")
    if not df_all.empty:
        numeric_cols = ["IN","MT","RI","PT","TH","TH_Force","IN_Force","MT_Force","RI_Force","PT_Force","Pain","Fatigue"]
        for c in numeric_cols:
            if c in df_all.columns:
                df_all[c] = pd.to_numeric(df_all[c], errors="coerce")
        if "Timestamp" in df_all.columns:
            df_all["Timestamp"] = pd.to_datetime(df_all["Timestamp"], errors="coerce")

        existing_flex = [c for c in ["IN","MT","RI","PT","TH"] if c in df_all.columns]
        if existing_flex and "Timestamp" in df_all.columns:
            fig = px.line(df_all.sort_values("Timestamp"), x="Timestamp", y=existing_flex, title="Average Flex Trends (Global)")
            st.plotly_chart(fig, use_container_width=True)

        existing_force = [c for c in ["TH_Force","IN_Force","MT_Force","RI_Force","PT_Force"] if c in df_all.columns]
        if existing_force:
            fig2 = px.bar(df_all.groupby("Username")[existing_force].mean().reset_index(), x="Username", y=existing_force, title="Avg Force per Patient (Global)", barmode="group")
            st.plotly_chart(fig2, use_container_width=True)

        if "Pain" in df_all.columns and "Fatigue" in df_all.columns:
            fig3 = px.scatter(df_all, x="Pain", y="Fatigue", color="Username", title="Pain vs Fatigue (Global)")
            st.plotly_chart(fig3, use_container_width=True)
    else:
        st.info("No patient records yet for analytics.")

    st.markdown("---")
    st.subheader("Per-Doctor Analytics")
    doctor_filter = st.selectbox("Select Doctor (or leave blank)", [""] + df_doctors["Username"].tolist() if not df_doctors.empty else [""])
    if doctor_filter:
        patients = get_patients_for_doctor(doctor_filter)
        if not patients:
            st.info("This doctor has no assigned patients.")
        else:
            df_doc = df_all[df_all["Username"].isin(patients)].copy()
            st.markdown(f"**Doctor:** {doctor_filter} — **Patients:** {', '.join(patients)}")
            st.dataframe(df_doc, use_container_width=True, height=250)
            flex_cols = [c for c in ["IN","MT","RI","PT","TH"] if c in df_doc.columns]
            force_cols = [c for c in ["TH_Force","IN_Force","MT_Force","RI_Force","PT_Force"] if c in df_doc.columns]
            if "Timestamp" in df_doc.columns and flex_cols:
                figf = px.line(df_doc.sort_values("Timestamp"), x="Timestamp", y=flex_cols, title=f"Flex Trend - {doctor_filter}")
                st.plotly_chart(figf, use_container_width=True)
            if force_cols:
                figF = px.bar(df_doc.groupby("Username")[force_cols].mean().reset_index(), x="Username", y=force_cols, barmode="group", title=f"Avg Force - {doctor_filter}")
                st.plotly_chart(figF, use_container_width=True)
            if "Pain" in df_doc.columns and "Fatigue" in df_doc.columns:
                figpf = px.scatter(df_doc, x="Pain", y="Fatigue", color="Username", title=f"Pain vs Fatigue - {doctor_filter}")
                st.plotly_chart(figpf, use_container_width=True)

    st.markdown("---")
    st.subheader("Manage Assignments")
    colA, colB = st.columns(2)
    with colA:
        all_patients = load_profiles()[load_profiles()["Role"] == "patient"]["Username"].tolist() if not load_profiles().empty else []
        patient_choice = st.selectbox("Select Patient", [""] + all_patients, key="manager_patient_select")
    with colB:
        all_doctors = load_doctors()["Username"].tolist() if not load_doctors().empty else []
        doctor_choice = st.selectbox("Select Doctor", [""] + all_doctors, key="manager_doctor_select")

    assign_btn = st.button("✅ Assign / Reassign", key="manager_assign")
    if assign_btn:
        if patient_choice and doctor_choice:
            assign_doctor(patient_choice, doctor_choice, manager_user=st.session_state.username)
        else:
            st.warning("Please select both a patient and a doctor.")

    remove_btn = st.button("🗑 Remove Assignment", key="manager_remove")
    if remove_btn:
        if patient_choice:
            remove_assignment(patient_choice, manager_user=st.session_state.username)
        else:
            st.warning("Select a patient to remove assignment for.")

    st.markdown("---")
    st.subheader("Manage Doctor Accounts & Profiles")
    mg_col1, mg_col2 = st.columns([2,3])
    with mg_col1:
        st.markdown("**Create new doctor**")
        new_doc_user = st.text_input("Doctor Username", key="new_doc_user")
        new_doc_pass = st.text_input("Doctor Password", key="new_doc_pass")
        new_doc_full = st.text_input("Full Name", key="new_doc_full")
        new_doc_spec = st.text_input("Specialty", key="new_doc_spec")
        new_doc_hosp = st.text_input("Hospital", key="new_doc_hosp")
        new_doc_bio = st.text_area("Bio", key="new_doc_bio")
        if st.button("➕ Create Doctor"):
            try:
                ws_doc = get_worksheet(DOCTOR_SHEET)
                ws_doc.append_row([new_doc_user, new_doc_pass, "doctor", new_doc_full, new_doc_spec, new_doc_hosp, new_doc_bio])
                ws_profiles = get_worksheet(PROFILES_SHEET)
                ws_profiles.append_row([new_doc_user, new_doc_full, "", "", new_doc_spec, "doctor"])
                ws_users = get_worksheet(USER_SHEET)
                ws_users.append_row([new_doc_user, new_doc_pass, "doctor", new_doc_full, "", "", ""])
                clear_read_cache()
                log_audit(st.session_state.username, "Create Doctor", f"{new_doc_user}")
                st.success("Doctor created.")
            except Exception as e:
                st.error(f"Failed to create doctor: {e}")

    with mg_col2:
        st.markdown("**Edit / Delete existing doctor**")
        doc_options = load_doctors()["Username"].tolist() if not load_doctors().empty else [""]
        doc_select = st.selectbox("Select doctor to edit/delete", [""] + doc_options, key="edit_doc_select")
        if doc_select:
            profiles_df = load_profiles()
            doctors_df = load_doctors()
            doc_row = doctors_df[doctors_df["Username"] == doc_select].iloc[0] if not doctors_df.empty else {}
            e_full = st.text_input("Full Name", value=doc_row.get("FullName",""), key="edit_full")
            e_spec = st.text_input("Specialty", value=doc_row.get("Specialty",""), key="edit_spec")
            e_hosp = st.text_input("Hospital", value=doc_row.get("Hospital",""), key="edit_hosp")
            e_bio = st.text_area("Bio", value=doc_row.get("Bio",""), key="edit_bio")
            if st.button("💾 Save Doctor Profile"):
                try:
                    # Update Doctors sheet
                    df_tmp = doctors_df.copy()
                    if not df_tmp.empty:
                        df_tmp.loc[df_tmp["Username"] == doc_select, "FullName"] = e_full
                        df_tmp.loc[df_tmp["Username"] == doc_select, "Specialty"] = e_spec
                        df_tmp.loc[df_tmp["Username"] == doc_select, "Hospital"] = e_hosp
                        df_tmp.loc[df_tmp["Username"] == doc_select, "Bio"] = e_bio
                        clear_and_update_sheet(DOCTOR_SHEET, df_tmp)
                    # Update Profiles sheet (specialization and name)
                    if not profiles_df.empty:
                        profiles_df.loc[profiles_df["Username"] == doc_select, "Name"] = e_full
                        profiles_df.loc[profiles_df["Username"] == doc_select, "Specialization"] = e_spec
                        clear_and_update_sheet(PROFILES_SHEET, profiles_df)
                    clear_read_cache()
                    log_audit(st.session_state.username, "Edit Doctor", f"{doc_select}")
                    st.success("Saved.")
                except Exception as e:
                    st.error(f"Failed to save doctor profile: {e}")

            st.markdown("### 🗑️ Delete selected doctor")
            if st.button("🗑 Delete Doctor (show confirmation)"):
                st.warning(f"⚠️ You are about to delete doctor **{doc_row.get('FullName','')}** ({doc_select}). This will:")
                st.write("- Remove doctor from Doctors sheet")
                st.write("- Remove doctor from Users sheet")
                st.write("- Unassign any patients assigned to this doctor")
                if st.button("✅ Confirm Delete Doctor"):
                    try:
                        df_profiles_tmp = load_profiles()
                        df_profiles_tmp = df_profiles_tmp[df_profiles_tmp["Username"].astype(str).str.lower() != doc_select.lower()]
                        clear_and_update_sheet(PROFILES_SHEET, df_profiles_tmp)

                        df_doctors_tmp = load_doctors()
                        df_doctors_tmp = df_doctors_tmp[df_doctors_tmp["Username"].astype(str).str.lower() != doc_select.lower()]
                        clear_and_update_sheet(DOCTOR_SHEET, df_doctors_tmp)

                        df_users_tmp = load_users()
                        df_users_tmp = df_users_tmp[df_users_tmp["Username"].astype(str).str.lower() != doc_select.lower()]
                        clear_and_update_sheet(USER_SHEET, df_users_tmp)

                        df_assign = load_assignments()
                        df_assign = df_assign[df_assign["Doctor"].astype(str).str.lower() != doc_select.lower()]
                        clear_and_update_sheet(ASSIGN_SHEET, df_assign)

                        clear_read_cache()
                        log_audit(st.session_state.username, "Delete Doctor", f"{doc_select}")
                        st.success("Doctor deleted and affected assignments removed.")
                    except Exception as e:
                        st.error(f"Failed to delete doctor: {e}")

    st.markdown("---")
    st.subheader("Export & Reports")
    if not df_all.empty:
        csv = df_all.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Export All Patient Data (CSV)", data=csv, file_name="patient_data.csv", mime="text/csv")
    if not df_assign.empty:
        csv2 = df_assign.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Export Assignments (CSV)", data=csv2, file_name="assignments.csv", mime="text/csv")

    st.markdown("---")
    st.subheader("Audit Log (manager actions)")
    try:
        df_audit = load_audit()
        if df_audit.empty:
            st.info("Audit log is empty.")
        else:
            st.dataframe(df_audit.sort_values("Timestamp", ascending=False).head(200), use_container_width=True)
            csv_a = df_audit.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Export Audit Log", data=csv_a, file_name="audit_log.csv", mime="text/csv")
    except Exception as e:
        st.error(f"Failed to load audit log: {e}")

# -------------------------------
# Sidebar + Routing
# -------------------------------
if not st.session_state.logged_in:
    if st.session_state.page == "login":
        st.markdown("<h1 style='text-align:center;'>🔐 Login</h1>", unsafe_allow_html=True)
        st.text_input("👤 Username", key="login_user")
        st.text_input("🔑 Password", type="password", key="login_pass")
        col1,col2 = st.columns(2)
        with col1:
            st.button("Login", use_container_width=True, on_click=login_action)
        with col2:
            st.button("Register", use_container_width=True, on_click=lambda: st.session_state.update({"page":"register"}))
    elif st.session_state.page == "register":
        st.markdown("<h1 style='text-align:center;'>🆕 Register</h1>", unsafe_allow_html=True)
        st.text_input("👤 Username", key="reg_user")
        st.text_input("🔑 Password", type="password", key="reg_pass")
        st.text_input("🔁 Confirm Password", type="password", key="reg_confirm")
        st.text_input("📛 Full name", key="reg_name")
        st.text_input("🎂 Age", key="reg_age")
        st.text_input("⚧ Gender", key="reg_gender")
        st.text_input("🩺 Condition", key="reg_condition")
        col1,col2 = st.columns(2)
        with col1:
            st.button("Sign Up", use_container_width=True, on_click=register_action)
        with col2:
            st.button("Back to Login", use_container_width=True, on_click=lambda: st.session_state.update({"page":"login"}))
    st.stop()
else:
    with st.sidebar:
        st.markdown(f"👋 Welcome, **{st.session_state.username}**")
        role = str(st.session_state.role).lower() if st.session_state.role else "patient"

        if role == "doctor":
            st.button("👨‍⚕️ Doctor Dashboard", use_container_width=True, on_click=lambda: st.session_state.update({"page":"main"}))
            st.button("👨‍⚕️ My Profile", use_container_width=True, on_click=lambda: st.session_state.update({"page":"doctor_profile"}))
            st.button("📄 AI KPI Analytics", use_container_width=True, on_click=lambda: st.session_state.update({"page":"extra"}))
        elif role == "patient":
            st.button("🧑‍⚕️ Patient Data Entry", use_container_width=True, on_click=lambda: st.session_state.update({"page":"main"}))
            st.button("📊 View My Data", use_container_width=True, on_click=lambda: st.session_state.update({"page":"mydata"}))
            st.button("👤 My Profile", use_container_width=True, on_click=lambda: st.session_state.update({"page":"patient_profile"}))
            st.button("📄 AI KPI Analytics", use_container_width=True, on_click=lambda: st.session_state.update({"page":"extra"}))
        elif role == "manager":
            st.button("🧑‍💼 Manager Dashboard", use_container_width=True, on_click=lambda: st.session_state.update({"page":"manager"}))
            st.button("📄 AI KPI Analytics", use_container_width=True, on_click=lambda: st.session_state.update({"page":"extra"}))

        st.button("🚪 Logout", use_container_width=True, on_click=logout_action)

# Final routing
if st.session_state.page == "main":
    if st.session_state.role == "doctor":
        doctor_page_view()
    elif st.session_state.role == "patient":
        patient_page()
    elif st.session_state.role == "manager":
        manager_dashboard()
    else:
        st.info("Unknown role. Please contact admin.")
elif st.session_state.page == "extra":
    extra_page()
elif st.session_state.page == "mydata":
    my_data_page()
elif st.session_state.page == "doctor_profile":
    try:
        doctor_profile()
    except Exception as e:
        st.error(f"Doctor profile failed: {e}")
elif st.session_state.page == "patient_profile":
    try:
        patient_profile()
    except Exception as e:
        st.error(f"Patient profile failed: {e}")
elif st.session_state.page == "manager":
    if str(st.session_state.role).lower() == "manager":
        manager_dashboard()
    else:
        st.error("Access denied — manager role required.")
