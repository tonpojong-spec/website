import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from google import genai

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
# 🌐 Google Sheets Setup
# -------------------------------
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1oUyY7W9scIdWd6K5nmyyzZ9qshsKSzQdmAsGybMecsU/edit?usp=sharing"
USER_SHEET = "Users"

scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

try:
    credentials = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scope)
except Exception:
    credentials = Credentials.from_service_account_file("service_account.json", scopes=scope)

client = gspread.authorize(credentials)

GEMINI_API_KEY = st.secrets["gemini"]["api_key"]
client_genai = genai.Client(api_key=GEMINI_API_KEY)

@st.cache_resource
def get_sheet_client():
    return client.open_by_url(SPREADSHEET_URL)

sheet_file = get_sheet_client()
sheet = sheet_file.worksheet("Sheet1")

# -------------------------------
# 🔒 Users Handling
# -------------------------------
@st.cache_data(ttl=120)
def load_users():
    try:
        user_sheet = sheet_file.worksheet(USER_SHEET)
        data = user_sheet.get_all_records()
        if not data:
            return pd.DataFrame(columns=["Username", "Password", "Role"])
        df = pd.DataFrame(data)
        col_map = {}
        lower_cols = {c.lower(): c for c in df.columns}
        if "username" in lower_cols: col_map[lower_cols["username"]] = "Username"
        if "password" in lower_cols: col_map[lower_cols["password"]] = "Password"
        if "role" in lower_cols: col_map[lower_cols["role"]] = "Role"
        if col_map:
            df = df.rename(columns=col_map)
        for c in ["Username", "Password", "Role"]:
            if c not in df.columns:
                df[c] = ""
        df["Username"] = df["Username"].astype(str).str.strip()
        df["Password"] = df["Password"].astype(str).str.strip()
        df["Role"] = df["Role"].astype(str).str.strip()
        return df
    except Exception as e:
        st.error(f"Unable to load user data: {e}")
        return pd.DataFrame(columns=["Username", "Password", "Role"])

def save_user(username, password, role="patient"):
    try:
        user_sheet = sheet_file.worksheet(USER_SHEET)
        user_sheet.append_row([username, password, role])
        try:
            st.cache_data.clear()
        except Exception:
            pass
        return True
    except Exception as e:
        st.error(f"Error occurred while saving new user: {e}")
        return False

users_df = load_users()

# -------------------------------
# 🔑 Session State
# -------------------------------
if "page" not in st.session_state:
    st.session_state.page = "login"
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "role" not in st.session_state:
    st.session_state.role = None
if "username" not in st.session_state:
    st.session_state.username = None

# -------------------------------
# 🔐 Authentication
# -------------------------------
def login_action():
    username = st.session_state.get("login_user", "")
    password = st.session_state.get("login_pass", "")
    df = load_users()
    if "Username" not in df.columns or "Password" not in df.columns:
        st.error("❌ User data in system is incorrect (Missing columns). Please check Google Sheet header.")
        return
    uname = str(username).strip()
    pwd = str(password).strip()
    matched = df[df["Username"].str.strip().str.lower() == uname.lower()]
    if not matched.empty and (matched["Password"].astype(str).str.strip().iloc[0] == pwd):
        st.session_state.logged_in = True
        st.session_state.username = uname
        st.session_state.role = matched.iloc[0].get("Role", "patient")
        st.session_state.page = "main"
    else:
        st.error("❌ Username or Password is incorrect")

def logout():
    for key in ["logged_in", "role", "username", "page"]:
        st.session_state.pop(key, None)
    st.session_state.page = "login"

def register_action():
    username = st.session_state.get("reg_user", "")
    password = st.session_state.get("reg_pass", "")
    confirm_password = st.session_state.get("reg_confirm", "")
    df = load_users()
    if not username or not password:
        st.warning("⚠️ Please enter Username and Password")
        return
    if password != confirm_password:
        st.warning("⚠️ Passwords do not match")
        return
    if username.strip().lower() in df["Username"].astype(str).str.strip().str.lower().values:
        st.error("🚫 This username already exists. Please choose another name")
        return
    if save_user(username.strip(), password.strip()):
        st.success("✅ Registration successful! Please login")
        try:
            st.cache_data.clear()
        except Exception:
            pass
        global users_df
        users_df = load_users()
        st.session_state.page = "login"

# -------------------------------
# 📋 Patient Data Entry
# -------------------------------
def patient_page():
    st.title("🧑‍⚕️ Patient Data Entry")
    st.markdown(f"👤 Patient Name: **{st.session_state.username}**")

    in_flex = st.number_input("IN Flex (degrees)", 0, 180, 0)
    mt_flex = st.number_input("MT Flex (degrees)", 0, 180, 0)
    ri_flex = st.number_input("RI Flex (degrees)", 0, 180, 0)
    pt_flex = st.number_input("PT Flex (degrees)", 0, 180, 0)
    th_flex = st.number_input("TH Flex (degrees)", 0, 180, 0)

    st.markdown("### 💪 Force Values for Each Part")
    in_force = st.number_input("IN Force", 0.0, 1000.0, 0.0)
    mt_force = st.number_input("MT Force", 0.0, 1000.0, 0.0)
    ri_force = st.number_input("RI Force", 0.0, 1000.0, 0.0)
    pt_force = st.number_input("PT Force", 0.0, 1000.0, 0.0)
    th_force = st.number_input("TH Force", 0.0, 1000.0, 0.0)

    pain = st.slider("Pain Scale", 0, 10, 0)
    fatigue = st.slider("Fatigue Scale", 0, 10, 0)

    if st.button("💾 Save Data"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            sheet.append_row([
                timestamp,
                st.session_state.username,
                in_flex, mt_flex, ri_flex, pt_flex, th_flex,
                in_force, mt_force, ri_force, pt_force, th_force,
                pain, fatigue
            ])
            st.success("✅ Data saved successfully!")
        except Exception as e:
            st.error(f"❌ Unable to save data: {e}")

# -------------------------------
# 👤 My Data Page (for patients)
# -------------------------------
def my_data_page():
    st.title("📊 My Data")
    try:
        df = pd.DataFrame(sheet.get_all_records())
    except Exception as e:
        st.error(f"❌ Failed to load data: {e}")
        return

    if df.empty:
        st.warning("⚠️ No data in system yet")
        return

    # Check Username column
    if "Username" not in df.columns:
        st.error("❌ Username column not found")
        return

    # Filter data for current patient
    my_username = st.session_state.username.strip().lower()
    df_user = df[df["Username"].astype(str).str.lower() == my_username]

    if df_user.empty:
        st.info("ℹ️ No data for you in the system yet")
        return

    st.success(f"✅ Found {len(df_user)} records of your data")
    st.dataframe(df_user, use_container_width=True)

# -------------------------------
# 👨‍⚕️ Doctor Dashboard
# -------------------------------
def doctor_page():
    st.title("👨‍⚕️ Doctor Dashboard")
    try:
        df = pd.DataFrame(sheet.get_all_records())
    except Exception as e:
        st.error(f"❌ Failed to load data: {e}")
        return
    if df.empty:
        st.warning("⚠️ No patient data in system yet")
        return

    flex_cols = ["IN", "MT", "RI", "PT", "TH"]
    force_cols = ["IN_Force", "MT_Force", "RI_Force", "PT_Force", "TH_Force"]
    for c in flex_cols + force_cols + ["Pain", "Fatigue"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "Timestamp" in df.columns:
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
        latest_time = df["Timestamp"].dropna().max()
    else:
        latest_time = None

    col1, col2, col3, col4 = st.columns([2,2,3,3])
    col1.metric("Number of Patients", df["Username"].nunique() if "Username" in df.columns else 0)
    col2.metric("Total Records", len(df))
    existing_flex = [c for c in flex_cols if c in df.columns]
    col3.metric("Average Flex Degrees", round(df[existing_flex].mean().mean(),2) if existing_flex else "-")
    col4.metric("Latest Record Date", latest_time.strftime("%Y-%m-%d %H:%M:%S") if pd.notna(latest_time) else "-")

    search_user = st.text_input("🔍 Search Username")
    if search_user and "Username" in df.columns:
        df = df[df["Username"].str.contains(search_user, case=False, na=False)]
    st.dataframe(df, use_container_width=True)

    if all(c in df.columns for c in flex_cols):
        fig_flex = px.line(df, x="Timestamp", y=flex_cols, title="📈 Flex Trend")
        st.plotly_chart(fig_flex, use_container_width=True)
    if all(c in df.columns for c in force_cols):
        fig_force = px.bar(df, x="Username", y=force_cols, title="💪 Force Measurements", barmode="group")
        st.plotly_chart(fig_force, use_container_width=True)
    if "Pain" in df.columns and "Fatigue" in df.columns:
        fig_pain = px.scatter(df, x="Pain", y="Fatigue", color="Username", title="❤️ Pain vs Fatigue")
        st.plotly_chart(fig_pain, use_container_width=True)

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
        if st.button("😭 Send To AI"):
            with st.spinner("AI Analyzing..."):

                summary = df_a.to_csv(index=False)
                response = client_genai.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=message
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
# 🧭 Routing
# -------------------------------
def goto_register():
    st.session_state.page = "register"

def goto_login():
    st.session_state.page = "login"

# -------------------------------
# Login/Register UI
# -------------------------------
if not st.session_state.logged_in:
    if st.session_state.page == "login":
        st.markdown("<h1 style='text-align:center;'>🔐 Login</h1>", unsafe_allow_html=True)
        st.text_input("👤 Username", key="login_user")
        st.text_input("🔑 Password", type="password", key="login_pass")
        col1,col2 = st.columns(2)
        with col1: st.button("Login", use_container_width=True, on_click=login_action)
        with col2: st.button("Register", use_container_width=True, on_click=goto_register)
    elif st.session_state.page == "register":
        st.markdown("<h1 style='text-align:center;'>🆕 Register</h1>", unsafe_allow_html=True)
        st.text_input("👤 Username", key="reg_user")
        st.text_input("🔑 Password", type="password", key="reg_pass")
        st.text_input("🔁 Confirm Password", type="password", key="reg_confirm")
        col1,col2 = st.columns(2)
        with col1: st.button("Sign Up", use_container_width=True, on_click=register_action)
        with col2: st.button("Back to Login", use_container_width=True, on_click=goto_login)
    st.stop()

# -------------------------------
# Sidebar (after login)
# -------------------------------
with st.sidebar:
    st.markdown(f"👋 Welcome, **{st.session_state.username}**")
    main_title = "👨‍⚕️ Doctor Dashboard" if st.session_state.role=="doctor" else "🧑‍⚕️ Patient Data Entry"
    st.button(main_title, use_container_width=True, on_click=lambda: st.session_state.update({"page":"main"}))
    if st.session_state.role=="patient":
        st.button("📊 View My Data", use_container_width=True, on_click=lambda: st.session_state.update({"page":"mydata"}))
    st.button("📄 AI KPI Analytics", use_container_width=True, on_click=lambda: st.session_state.update({"page":"extra"}))
    st.button("🚪 Logout", use_container_width=True, on_click=logout)

# -------------------------------
# Main Routing
# -------------------------------
if st.session_state.page == "main":
    if st.session_state.role == "doctor":
        doctor_page()
    else:
        patient_page()
elif st.session_state.page == "extra":
    extra_page()
elif st.session_state.page == "mydata":
    my_data_page()