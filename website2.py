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

# Sheet names used by the app
USER_SHEET = "Users"
DOCTOR_SHEET = "Doctors"
ASSIGN_SHEET = "Assignments"
DATA_SHEET = "Data"          # <-- you told me the main data sheet is named "Data"
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
GEMINI_API_KEY = st.secrets["gemini"]["api_key"]
client_genai = genai.Client(api_key=GEMINI_API_KEY)

@st.cache_resource
def get_sheet_client():
    return client.open_by_url(SPREADSHEET_URL)

sheet_file = get_sheet_client()
sheet = sheet_file.worksheet("Data")
# -------------------------------
# Ensure sheets exist and have headers
# -------------------------------
def ensure_sheet_exists(title, headers):
    try:
        try:
            _ = sheet_file.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            ws = sheet_file.add_worksheet(title=title, rows=2000, cols=max(10, len(headers)))
            ws.append_row(headers)
    except Exception as e:
        st.error(f"Unable to ensure sheet {title}: {e}")

ensure_sheet_exists(USER_SHEET, ["Username", "Password", "Role"])
ensure_sheet_exists(DOCTOR_SHEET, ["Username", "Password", "Role", "FullName", "Specialty", "Hospital", "Bio"])
ensure_sheet_exists(ASSIGN_SHEET, ["Patient", "Doctor"])
ensure_sheet_exists(DATA_SHEET, ["Timestamp","Username","IN","MT","RI","PT","TH","IN_Force","MT_Force","RI_Force","PT_Force","TH_Force","Pain","Fatigue","Notes"])
ensure_sheet_exists(AUDIT_SHEET, ["Timestamp", "Manager", "Action", "Details"])

# -------------------------------
# Helpers: read/write sheets, clear+update
# -------------------------------
@st.cache_data(ttl=120)
def load_sheet(sheet_name):
    try:
        ws = sheet_file.worksheet(sheet_name)
        data = ws.get_all_records()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        return df
    except Exception as e:
        st.error(f"Unable to load sheet {sheet_name}: {e}")
        return pd.DataFrame()

def append_row(sheet_name, row):
    try:
        ws = sheet_file.worksheet(sheet_name)
        ws.append_row(row)
        try: st.cache_data.clear()
        except Exception: pass
        return True
    except Exception as e:
        st.error(f"Failed to append to {sheet_name}: {e}")
        return False

def clear_and_update_sheet(sheet_name, records):
    """
    records: list of dicts (keys = header names) OR pandas DataFrame
    This will clear the sheet and write header + rows.
    """
    try:
        ws = sheet_file.worksheet(sheet_name)
        ws.clear()
        if records is None or len(records) == 0:
            # leave just header (if we can infer header from existing sheet definition)
            return True
        if isinstance(records, pd.DataFrame):
            df = records.copy()
        else:
            df = pd.DataFrame(records)
        # Ensure header order consistent
        header = list(df.columns)
        ws.append_row(header)
        values = df.fillna("").values.tolist()
        if values:
            ws.append_rows(values)
        try: st.cache_data.clear()
        except Exception: pass
        return True
    except Exception as e:
        st.error(f"Failed to clear/update sheet {sheet_name}: {e}")
        return False

# -------------------------------
# Load convenience wrappers
# -------------------------------
def load_users():
    df = load_sheet(USER_SHEET)
    # normalize columns
    if df.empty:
        return pd.DataFrame(columns=["Username","Password","Role"])
    df.columns = [c.strip() for c in df.columns]
    if "Role" in df.columns:
        df["Role"] = df["Role"].astype(str).str.strip().str.lower()
    else:
        df["Role"] = ""
    if "Username" in df.columns:
        df["Username"] = df["Username"].astype(str).str.strip()
    return df

def load_doctors():
    try:
        ws = sheet_file.worksheet(DOCTOR_SHEET)
        data = ws.get_all_records(expected_headers=[
            "Username", "Password", "Role", "FullName", "Specialty", "Hospital", "Bio"
        ])
        if not data:
            return pd.DataFrame(columns=["Username", "Password", "Role", "FullName", "Specialty", "Hospital", "Bio"])
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"Unable to load sheet {DOCTOR_SHEET}: {e}")
        return pd.DataFrame(columns=["Username", "Password", "Role", "FullName", "Specialty", "Hospital", "Bio"])


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
        return pd.DataFrame(columns=["Timestamp","Username","IN","MT","RI","PT","TH","IN_Force","MT_Force","RI_Force","PT_Force","TH_Force","Pain","Fatigue","Notes"])
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
        # remove any existing entry for patient, then append new
        df_assign = df_assign[df_assign["Patient"].str.lower() != str(patient).strip().lower()]
        df_new = pd.concat([df_assign, pd.DataFrame([{"Patient": patient, "Doctor": doctor}])], ignore_index=True)
        clear_and_update_sheet(ASSIGN_SHEET, df_new)
        if manager_user:
            log_audit(manager_user, "Assign Doctor", f"{patient} -> {doctor}")
        st.success(f"Assigned {patient} → {doctor}")
        try: st.cache_data.clear()
        except Exception: pass
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
        try: st.cache_data.clear()
        except Exception: pass
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
def save_user(username, password, role="patient"):
    try:
        ws = sheet_file.worksheet(USER_SHEET)
        ws.append_row([username, password, role])
        try: st.cache_data.clear()
        except Exception: pass
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
    df_doctors = load_doctors()

    matched = pd.DataFrame()
    if "Username" in df_users.columns:
        matched = df_users[df_users["Username"].str.strip().str.lower() == str(username).strip().lower()]
    if matched.empty:
        # check doctors sheet in case credentials stored there
        matched = df_doctors[df_doctors["Username"].astype(str).str.strip().str.lower() == str(username).strip().lower()]

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
    save_user(username, password, role="patient")
    st.success("Registration successful. Please log in.")
    try: st.cache_data.clear()
    except Exception: pass
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
            in_force, mt_force, ri_force, pt_force, th_force,
            pain, fatigue, notes
        ]
        ok = append_row(DATA_SHEET, row)
        if ok:
            st.success("✅ Data saved successfully!")
        else:
            st.error("❌ Unable to save data")

    st.markdown("---")
    st.subheader("👨‍⚕️ Assigned Doctor")
    doctor_username = get_doctor_for_patient(st.session_state.username)
    if doctor_username:
        df_doctors = load_doctors()
        doc_row = df_doctors[df_doctors["Username"].astype(str).str.lower() == str(doctor_username).strip().lower()]
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
    # coerce numeric where possible
    numeric_cols = ["IN","MT","RI","PT","TH","IN_Force","MT_Force","RI_Force","PT_Force","TH_Force","Pain","Fatigue"]
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

    # charts
    flex_cols = [c for c in ["IN","MT","RI","PT","TH"] if c in df_patients.columns]
    force_cols = [c for c in ["IN_Force","MT_Force","RI_Force","PT_Force","TH_Force"] if c in df_patients.columns]
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
        st.error(f"Failed to load data: {e}")
        return
    if df.empty:
        st.info("No data yet.")
        return

    st.markdown("### 🧾 Raw Patient Data")
    name = st.text_input("Search patient (username)")
    df_filtered = df[df["Username"].str.contains(name, case=False, na=False)] if name else df
    st.dataframe(df_filtered, use_container_width=True, height=300)

    st.markdown("---")
    st.info("Gemini AI integration enabled — you can select rows and send to AI (this UI keeps your original prompts).")

    # Example simple use: send a small CSV excerpt to Gemini and show a response
    if st.button("Send I sample to Gemini (example)"):
        sample = df_filtered.head(10).to_csv(index=False)
        prompt = f"Please summarize this patient data concisely:\n\n{sample}"
        with st.spinner("Querying Gemini..."):
            try:
                response = client_genai.models.generate_content(model="gemini-2.5-flash", contents=prompt)
                st.subheader("Gemini response")
                st.write(response.text)
            except Exception as e:
                st.error(f"Gemini request failed: {e}")

def patient_profile():
    st.title("👤 My Profile (Patient)")
    username = st.session_state.username
    df_users = load_users()
    df_doctors = load_doctors()
    user_row = df_users[df_users["Username"].astype(str).str.lower() == username.lower()]
    if not user_row.empty:
        user = user_row.iloc[0]
        st.markdown(f"**Username:** {user.get('Username','')}")
        st.markdown(f"**Role:** {user.get('Role','')}")
    else:
        st.markdown(f"**Username:** {username}")

    st.markdown("---")
    st.subheader("👨‍⚕️ Assigned Doctor")
    doc_username = get_doctor_for_patient(username)
    if doc_username:
        doc_row = df_doctors[df_doctors["Username"].astype(str).str.lower() == str(doc_username).strip().lower()]
        if not doc_row.empty:
            doc = doc_row.iloc[0]
            st.markdown(f"**Name:** {doc.get('FullName','N/A')}")
            st.markdown(f"**Specialty:** {doc.get('Specialty','N/A')}")
            st.markdown(f"**Hospital:** {doc.get('Hospital','N/A')}")
            st.markdown(f"**Bio:** {doc.get('Bio','N/A')}")
        else:
            st.warning("Doctor record not found.")
    else:
        st.info("No doctor assigned yet.")

def doctor_profile():
    st.title("👨‍⚕️ My Profile (Doctor)")
    username = st.session_state.username
    df_doctors = load_doctors()

    doc_row = df_doctors[df_doctors["Username"].astype(str).str.lower() == username.lower()]
    if not doc_row.empty:
        doc = doc_row.iloc[0]
        st.markdown(f"**Full Name:** {doc.get('FullName','N/A')}")
        st.markdown(f"**Specialty:** {doc.get('Specialty','N/A')}")
        st.markdown(f"**Hospital:** {doc.get('Hospital','N/A')}")
        st.markdown(f"**Bio:** {doc.get('Bio','N/A')}")
    else:
        st.warning("Doctor record not found in Doctors sheet.")

    st.markdown("---")
    st.subheader("👥 Assigned Patients")
    patients = get_patients_for_doctor(username)
    if patients:
        for p in patients:
            st.write(f"• {p}")
    else:
        st.info("No patients assigned yet.")

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
        # coerce numeric
        numeric_cols = ["IN","MT","RI","PT","TH","IN_Force","MT_Force","RI_Force","PT_Force","TH_Force","Pain","Fatigue"]
        for c in numeric_cols:
            if c in df_all.columns:
                df_all[c] = pd.to_numeric(df_all[c], errors="coerce")
        if "Timestamp" in df_all.columns:
            df_all["Timestamp"] = pd.to_datetime(df_all["Timestamp"], errors="coerce")

        existing_flex = [c for c in ["IN","MT","RI","PT","TH"] if c in df_all.columns]
        if existing_flex and "Timestamp" in df_all.columns:
            fig = px.line(df_all.sort_values("Timestamp"), x="Timestamp", y=existing_flex, title="Average Flex Trends (Global)")
            st.plotly_chart(fig, use_container_width=True)

        existing_force = [c for c in ["IN_Force","MT_Force","RI_Force","PT_Force","TH_Force"] if c in df_all.columns]
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
    doctor_filter = st.selectbox("Select Doctor (or leave blank)", [""] + df_doctors["Username"].tolist())
    if doctor_filter:
        patients = get_patients_for_doctor(doctor_filter)
        if not patients:
            st.info("This doctor has no assigned patients.")
        else:
            df_doc = df_all[df_all["Username"].isin(patients)].copy()
            st.markdown(f"**Doctor:** {doctor_filter} — **Patients:** {', '.join(patients)}")
            st.dataframe(df_doc, use_container_width=True, height=250)
            # charts similar to doctor's view
            flex_cols = [c for c in ["IN","MT","RI","PT","TH"] if c in df_doc.columns]
            force_cols = [c for c in ["IN_Force","MT_Force","RI_Force","PT_Force","TH_Force"] if c in df_doc.columns]
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
        all_patients = df_users[df_users["Role"] == "patient"]["Username"].tolist() if not df_users.empty else []
        patient_choice = st.selectbox("Select Patient", [""] + all_patients, key="manager_patient_select")
    with colB:
        all_doctors = df_doctors["Username"].tolist() if not df_doctors.empty else []
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
                ws_doc = sheet_file.worksheet(DOCTOR_SHEET)
                ws_doc.append_row([new_doc_user, new_doc_pass, "doctor", new_doc_full, new_doc_spec, new_doc_hosp, new_doc_bio])
                ws_users = sheet_file.worksheet(USER_SHEET)
                ws_users.append_row([new_doc_user, new_doc_pass, "doctor"])
                try: st.cache_data.clear()
                except Exception: pass
                log_audit(st.session_state.username, "Create Doctor", f"{new_doc_user}")
                st.success("Doctor created.")
            except Exception as e:
                st.error(f"Failed to create doctor: {e}")

    with mg_col2:
        st.markdown("**Edit / Delete existing doctor**")
        doc_select = st.selectbox("Select doctor to edit/delete", [""] + df_doctors["Username"].tolist(), key="edit_doc_select")
        if doc_select:
            doc_row = df_doctors[df_doctors["Username"] == doc_select].iloc[0]
            e_full = st.text_input("Full Name", value=doc_row.get("FullName",""), key="edit_full")
            e_spec = st.text_input("Specialty", value=doc_row.get("Specialty",""), key="edit_spec")
            e_hosp = st.text_input("Hospital", value=doc_row.get("Hospital",""), key="edit_hosp")
            e_bio = st.text_area("Bio", value=doc_row.get("Bio",""), key="edit_bio")
            if st.button("💾 Save Doctor Profile"):
                try:
                    ws = sheet_file.worksheet(DOCTOR_SHEET)
                    all_rows = ws.get_all_records()
                    df_tmp = pd.DataFrame(all_rows)
                    df_tmp.loc[df_tmp["Username"] == doc_select, "FullName"] = e_full
                    df_tmp.loc[df_tmp["Username"] == doc_select, "Specialty"] = e_spec
                    df_tmp.loc[df_tmp["Username"] == doc_select, "Hospital"] = e_hosp
                    df_tmp.loc[df_tmp["Username"] == doc_select, "Bio"] = e_bio
                    ws.clear()
                    ws.append_row(["Username","Password","Role","FullName","Specialty","Hospital","Bio"])
                    for _, r in df_tmp.iterrows():
                        ws.append_row([r.get("Username",""), r.get("Password",""), r.get("Role",""), r.get("FullName",""), r.get("Specialty",""), r.get("Hospital",""), r.get("Bio","")])
                    try: st.cache_data.clear()
                    except Exception: pass
                    log_audit(st.session_state.username, "Edit Doctor", f"{doc_select}")
                    st.success("Saved.")
                except Exception as e:
                    st.error(f"Failed to save doctor profile: {e}")

            # --- Safe delete flow with confirmation modal-style UI (no native modal in Streamlit) ---
            st.markdown("### 🗑️ Delete selected doctor")
            if st.button("🗑 Delete Doctor (show confirmation)"):
                # Show confirmation details and require explicit confirm
                st.warning(f"⚠️ You are about to delete doctor **{doc_row.get('FullName','')}** ({doc_select}). This will:")
                st.write("- Remove doctor from Doctors sheet")
                st.write("- Remove doctor from Users sheet")
                st.write("- Unassign any patients assigned to this doctor")
                st.write("**Doctor details:**")
                st.write(f"• Full name: {doc_row.get('FullName','')}")
                st.write(f"• Specialty: {doc_row.get('Specialty','')}")
                st.write(f"• Hospital: {doc_row.get('Hospital','')}")
                # explicit confirm
                if st.button("✅ Confirm Delete Doctor"):
                    try:
                        # remove from Doctors sheet
                        ws_doc = sheet_file.worksheet(DOCTOR_SHEET)
                        df_doc = pd.DataFrame(ws_doc.get_all_records())
                        df_doc = df_doc[df_doc["Username"].astype(str).str.lower() != doc_select.lower()]
                        ws_doc.clear()
                        ws_doc.append_row(["Username","Password","Role","FullName","Specialty","Hospital","Bio"])
                        for _, r in df_doc.iterrows():
                            ws_doc.append_row([r.get("Username",""), r.get("Password",""), r.get("Role",""), r.get("FullName",""), r.get("Specialty",""), r.get("Hospital",""), r.get("Bio","")])

                        # remove from Users
                        ws_users = sheet_file.worksheet(USER_SHEET)
                        df_users_tmp = pd.DataFrame(ws_users.get_all_records())
                        df_users_tmp = df_users_tmp[df_users_tmp["Username"].astype(str).str.lower() != doc_select.lower()]
                        ws_users.clear()
                        ws_users.append_row(["Username","Password","Role"])
                        for _, r in df_users_tmp.iterrows():
                            ws_users.append_row([r.get("Username",""), r.get("Password",""), r.get("Role","")])

                        # unassign patients who had this doctor
                        df_assign = load_assignments()
                        df_assign = df_assign[df_assign["Doctor"].astype(str).str.lower() != doc_select.lower()]
                        ws_assign = sheet_file.worksheet(ASSIGN_SHEET)
                        ws_assign.clear()
                        ws_assign.append_row(["Patient","Doctor"])
                        for _, r in df_assign.iterrows():
                            ws_assign.append_row([r.get("Patient",""), r.get("Doctor","")])

                        try: st.cache_data.clear()
                        except Exception: pass
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
        df_audit = pd.DataFrame(sheet_file.worksheet(AUDIT_SHEET).get_all_records())
        if df_audit.empty:
            st.info("Audit log is empty.")
        else:
            st.dataframe(df_audit.sort_values("Timestamp", ascending=False).head(200), use_container_width=True)
            csv_a = df_audit.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Export Audit Log", data=csv_a, file_name="audit_log.csv", mime="text/csv")
    except Exception as e:
        st.error(f"Failed to load audit log: {e}")
# -------------------------------
# 🧩 Extra Page per role
# -------------------------------
def extra_page():
    st.markdown("<h1 style='text-align:center;'>AI KPI Analytics</h1>", unsafe_allow_html=True)
    try:
        df = pd.DataFrame(sheet.get_all_records())
    except Exception as e:
        st.error(f"❌ Failed to load data: {e}")
        return
    if df.empty:
        st.info("No data yet. Please add patient data first.")
    else:
        # -------------------------------
        # 🧩 Raw Data Section
        # -------------------------------
        st.markdown("### 🧾 Raw Patient Data (for filtering and review)")
        name = st.text_input("🔍 Search Patient Name")
        df_filtered = df[df["Username"].str.contains(name, case=False, na=False)] if name else df

        st.dataframe(df_filtered, use_container_width=True, height=300)

        st.markdown("---")

        # -------------------------------
        # 🔄 Preprocessing to Astronaut KPI Schema
        # -------------------------------
        st.markdown("### 🧠 Preprocessed Astronaut KPI Schema")

        df_a = df_filtered.copy()
        df_a['Timestamp'] = pd.to_datetime(df_a['Timestamp'])
        df_a = df_a.sort_values('Timestamp')
        df_a['Week'] = ["W" + str(i+1) for i in range(len(df_a))]
        df_a['Phase'] = "P1"
        df_a['Adherence (%)'] = 100

        # Convert Force columns to numeric
        force_cols = ["TH_Force", "IN_Force", "MT_Force", "RI_Force", "PT_Force"]
        for col in force_cols:
            df_a[col] = pd.to_numeric(df_a[col], errors='coerce')

        # Create calculated column for average Grip Force
        df_a["Hand: Avg Grip Force"] = df_a[force_cols].mean(axis=1).round(2)

        # Set N/A values for metrics not yet available
        df_a["Hand: VR Error Rate (%)"] = "N/A"
        df_a["Chest: Avg COM-BOS Angle (°)"] = "N/A"
        df_a["Balance: Alarm Triggers/Min"] = "N/A"
        df_a["Locomotion: Max Angle Spike (°)"] = "N/A"
        df_a["P4: Time to Stability (sec)"] = "N/A"

        # Map fatigue/pain
        df_a["Fatigue Avg (1–10)"] = df_a["Fatigue_Scale"]
        df_a["Pain Avg (0–10)"] = df_a["Pain_Scale"]

        # Final schema columns
        final_cols = [
            "Week", "Phase", "Adherence (%)",
            "Hand: Avg Grip Force", "Hand: VR Error Rate (%)",
            "Chest: Avg COM-BOS Angle (°)", "Balance: Alarm Triggers/Min",
            "Locomotion: Max Angle Spike (°)", "P4: Time to Stability (sec)",
            "Fatigue Avg (1–10)", "Pain Avg (0–10)"
        ]

        # Display editable table
        st.markdown("#### ✏️ Editable Preprocessed Table")
        edited = st.data_editor(df_a[final_cols], use_container_width=True, num_rows="dynamic")

        # Store edited DataFrame
        df_a = edited.copy()

        # Convert columns that should be numeric
        numeric_cols = [
            "Adherence (%)", "Hand: Avg Grip Force",
            "Chest: Avg COM-BOS Angle (°)", "Balance: Alarm Triggers/Min",
            "Locomotion: Max Angle Spike (°)", "P4: Time to Stability (sec)",
            "Fatigue Avg (1–10)", "Pain Avg (0–10)"
        ]
        for col in numeric_cols:
            df_a[col] = pd.to_numeric(df_a[col], errors='coerce')

        # Display summary after preprocessing
        st.subheader("📊 Processed Schema Preview")
        st.dataframe(df_a, use_container_width=True, height=300)
        message = st.text_input("📜 Message")
        if st.button("📩 Send To AI"):
            with st.spinner("AI Analyzing..."):

                prompt1= f"""
You are a Clinical Rehabilitation Analytics System designed for Astronaut Hand-Body Integration training. 
Your role is to analyze weekly KPI data and produce structured reports that mimic the formatting, tone, and clinical reasoning 
of the standardized documentation below.

Input will be CSV records containing:
Week
Phase (P1,P2,P3,P4)
Adherence (%)
Hand: Avg Grip Force
Hand: VR Error Rate (%)
Chest: Avg COM-BOS Angle (°)
Balance: Alarm Triggers/Min
Locomotion: Max Angle Spike (°)
Phase 4 Only: Time to Stability (sec)
Fatigue Avg (1–10)
Pain Avg (0–10)

------------------------------------------------------------
DATA AVAILABILITY RULES
If the CSV input is incomplete or missing some metrics (for example: missing COM-BOS Angle, Alarm Triggers/Min, VR Error Rate, or Time to Stability):
1. Do NOT reject the input. Always proceed with analysis.
2. Mark missing metrics as “N/A”.
3. Infer trends and highlight performance using available data only.
   - Use Grip Force as a proxy for Hand strength and control trends.
   - Use Pain and Fatigue as physiological indicators for endurance or regression.
   - If COM-BOS or Alarm data are absent, assume stability metrics are under observation but unmeasured this session.
4. Adapt your interpretation logically. If a metric is missing, base the clinical reasoning on the remaining indicators.
5. Maintain all standard output sections (B, C, and D) even when data are partial or incomplete.

------------------------------------------------------------
REHAB PROGRAM LOGIC (REFERENCE)
Phase 1 focus (Weeks 1–4): Soft to Medium Grip, Static Balance tolerance >3°, Hand VR Error Rate target <3%, Avg COM-BOS <2.2°, Alarm Triggers/min <1/5 min
Phase 2 focus (Weeks 5–8): Strong Grip Force, Dynamic Balance tolerance >1.5°, Turning control (90°/180°), Alarm Response <0.5s, COM-BOS <1.0°
Phase 3 focus (Weeks 9–12): Hard Grip + Cognitive load, Tightest tolerance >0.7°, Alarm Triggers/session <3, COM-BOS <0.5° under stress
Phase 4 focus (Weeks 13–16): Impact Loading, Post-landing stability, Time to Stability (TTS) <0.5s

------------------------------------------------------------
METRIC THRESHOLDS (ALERT MODEL)
Balance: Alarm Triggers/Min — Green <0.2 (P2), <0.05 (P3/P4); Yellow 0.2–0.5 / 0.05–0.1; Red >0.5 / >0.1  
Chest: Avg COM-BOS Angle — Green <1.0° (P2/P3), <0.5° (P4); Yellow 1.0–2.0° / 0.5–1.0°; Red >2.0° / >1.0°  
Locomotion: Max Angle Spike — Green <1.5° (P2), <1.0° (P3/P4); Yellow 1.5–2.5° / 1.0–1.5°; Red >2.5° / >1.5°  
Hand: VR Error Rate — Green <3% (P1/P2), <0.5% (P3/P4); Yellow 3–6% / 0.5–1.0%; Red >6% / >1.0%

------------------------------------------------------------
YOUR TASK
Using the CSV data provided, produce the following structured sections clearly labeled:

SECTION B. Weekly AI Summary & Recommendations (for Clinician Review)
Columns:
Week | Trend Highlights (KPIs) | Red Flags (N if none) | Root-Cause Hypotheses | Recommendations for Next Phase | Progression Decision (Progress, Maintain, Regress)
Rules:
- Use short, clinical highlight sentences.
- Mention % improvement where possible.
- Mention COM-BOS and Alarm behavior only if data exist.
- Mark missing metrics as N/A but keep consistent structure.
- Mention Grip Force, Fatigue, and Pain trends in all cases.

SECTION C. KPI Thresholds & Triggers (Auto-Flags)
For each week:
- Identify metrics in Yellow or Red zones (only from available metrics).
- Produce 1–2 Auto-Actions referencing threshold logic.

SECTION D. Free-Text Weekly Notes (Communication Log)
Astronaut/Patient Note: first-person subjective report (1–2 sentences)
AI Note: integrated analysis paragraph linking available metrics such as Grip Force, Pain, Fatigue, and any stability metric present.

Style: Use compact, clinical writing in report tone. 
An approriate response (example) should be something like this:
Response to Inquiry: 'What should I do next week to lower the pain?'
As an AI analytics system, I cannot provide direct medical advice or treatment recommendations. These decisions must be made in consultation with your clinical team. However, based on the analytical report:

Your most recent reported pain average was 6.0. This indicates an increase from the previous week's average. Elevated pain can be correlated with increased fatigue or specific training activities. The system has flagged this as an 'Elevated Pain Alert'. It is crucial to review recent training load and specific activities that may be contributing.
To address pain, it is recommended to discuss with your clinician the following data-driven considerations: adjusting the intensity or volume of training sessions, particularly for activities that may be correlated with increased pain; incorporating targeted recovery strategies; and ensuring adequate rest and nutrition. A detailed clinical assessment may identify specific biomechanical factors or exercises contributing to discomfort. Please consult directly with your medical doctor or rehabilitation specialist for personalized guidance and to determine the most appropriate course of action for pain managemen

Analyze the data given, then answer the message below accordingly and professional. Do not reveal in any way you are gemeni, just that you are an AI bot here to help. 
Always answer professionally and clear, do not be vague. If you do not know, say so and tell them to discuss with their doctor. Do the same if you are not sure.
Always respond and produce an answer, even if data is incomplete.
If you are not "trained" to produce an answer, give appropriate suggestions
DO NOT PRODUCE ANY SECTION. JUST PROVIDE AN ANSWER TO THE MESSAGE BELOW
{message} 

                """

                summary = df_a.to_csv(index=False)
                response = client_genai.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt1
                )

                st.subheader("🧠 AI Q&A")
                # st.write(response.text)
                st.markdown(response.text, unsafe_allow_html=True)

        # -------------------------------
        # 🤖 Run AI KPI Analysis
        # -------------------------------
        if st.button("🚀 Run AI KPI Analysis"):
            with st.spinner("Running AI analysis..."):

                summary = df_a.to_csv(index=False)

    #             prompt = f"""
    # You are a Clinical Rehabilitation Analytics System designed for Astronaut Hand-Body Integration training.
    # Always accept incomplete data. If metrics are 'N/A', infer trends from Grip Force, Pain, and Fatigue.
    # Never reject input.

    # Input CSV:
    # {summary}

    # Return:
    # Section B: Weekly AI Summary & Recommendations
    # Section C: KPI Thresholds & Triggers
    # Section D: Free-Text Weekly Notes
    # """
                prompt = f"""
You are a Clinical Rehabilitation Analytics System designed for Astronaut Hand-Body Integration training. 
Your role is to analyze weekly KPI data and produce structured reports that mimic the formatting, tone, and clinical reasoning 
of the standardized documentation below.

Input will be CSV records containing:
Week
Phase (P1,P2,P3,P4)
Adherence (%)
Hand: Avg Grip Force
Hand: VR Error Rate (%)
Chest: Avg COM-BOS Angle (°)
Balance: Alarm Triggers/Min
Locomotion: Max Angle Spike (°)
Phase 4 Only: Time to Stability (sec)
Fatigue Avg (1–10)
Pain Avg (0–10)

------------------------------------------------------------
DATA AVAILABILITY RULES
If the CSV input is incomplete or missing some metrics (for example: missing COM-BOS Angle, Alarm Triggers/Min, VR Error Rate, or Time to Stability):
1. Do NOT reject the input. Always proceed with analysis.
2. Mark missing metrics as “N/A”.
3. Infer trends and highlight performance using available data only.
   - Use Grip Force as a proxy for Hand strength and control trends.
   - Use Pain and Fatigue as physiological indicators for endurance or regression.
   - If COM-BOS or Alarm data are absent, assume stability metrics are under observation but unmeasured this session.
4. Adapt your interpretation logically. If a metric is missing, base the clinical reasoning on the remaining indicators.
5. Maintain all standard output sections (B, C, and D) even when data are partial or incomplete.

------------------------------------------------------------
REHAB PROGRAM LOGIC (REFERENCE)
Phase 1 focus (Weeks 1–4): Soft to Medium Grip, Static Balance tolerance >3°, Hand VR Error Rate target <3%, Avg COM-BOS <2.2°, Alarm Triggers/min <1/5 min
Phase 2 focus (Weeks 5–8): Strong Grip Force, Dynamic Balance tolerance >1.5°, Turning control (90°/180°), Alarm Response <0.5s, COM-BOS <1.0°
Phase 3 focus (Weeks 9–12): Hard Grip + Cognitive load, Tightest tolerance >0.7°, Alarm Triggers/session <3, COM-BOS <0.5° under stress
Phase 4 focus (Weeks 13–16): Impact Loading, Post-landing stability, Time to Stability (TTS) <0.5s

------------------------------------------------------------
METRIC THRESHOLDS (ALERT MODEL)
Balance: Alarm Triggers/Min — Green <0.2 (P2), <0.05 (P3/P4); Yellow 0.2–0.5 / 0.05–0.1; Red >0.5 / >0.1  
Chest: Avg COM-BOS Angle — Green <1.0° (P2/P3), <0.5° (P4); Yellow 1.0–2.0° / 0.5–1.0°; Red >2.0° / >1.0°  
Locomotion: Max Angle Spike — Green <1.5° (P2), <1.0° (P3/P4); Yellow 1.5–2.5° / 1.0–1.5°; Red >2.5° / >1.5°  
Hand: VR Error Rate — Green <3% (P1/P2), <0.5% (P3/P4); Yellow 3–6% / 0.5–1.0%; Red >6% / >1.0%

------------------------------------------------------------
YOUR TASK
Using the CSV data provided, produce the following structured sections clearly labeled:

SECTION B. Weekly AI Summary & Recommendations (for Clinician Review)
Columns:
Week | Trend Highlights (KPIs) | Red Flags (N if none) | Root-Cause Hypotheses | Recommendations for Next Phase | Progression Decision (Progress, Maintain, Regress)
Rules:
- Use short, clinical highlight sentences.
- Mention % improvement where possible.
- Mention COM-BOS and Alarm behavior only if data exist.
- Mark missing metrics as N/A but keep consistent structure.
- Mention Grip Force, Fatigue, and Pain trends in all cases.

SECTION C. KPI Thresholds & Triggers (Auto-Flags)
For each week:
- Identify metrics in Yellow or Red zones (only from available metrics).
- Produce 1–2 Auto-Actions referencing threshold logic.

SECTION D. Free-Text Weekly Notes (Communication Log)
Astronaut/Patient Note: first-person subjective report (1–2 sentences)
AI Note: integrated analysis paragraph linking available metrics such as Grip Force, Pain, Fatigue, and any stability metric present.

Style: Use compact, clinical writing in report tone. 
Do NOT reject incomplete data. Always produce Sections B, C, and D.

------------------------------------------------------------
INPUT CSV DATA (below this line):
{summary}
"""
                response = client_genai.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt
                )

                st.subheader("🧠 AI KPI Summary Output")
                # st.write(response.text)
                st.markdown(response.text, unsafe_allow_html=True)
# -------------------------------
# Sidebar + Routing
# -------------------------------
# Login / Register UI when not logged in
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
    doctor_profile()
elif st.session_state.page == "patient_profile":
    patient_profile()
elif st.session_state.page == "manager":
    if str(st.session_state.role).lower() == "manager":
        manager_dashboard()
    else:
        st.error("Access denied — manager role required.")
