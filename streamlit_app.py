import streamlit as st
import pandas as pd
import time
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import streamlit_authenticator as stauth
from yaml.loader import SafeLoader
import yaml
import os
import io
import base64
from supabase import create_client, Client
import fitz
import re
from datetime import datetime

st.set_page_config(page_title="Biometric Snapshot", layout="centered")

with open('config.yaml') as file:
    config = yaml.load(file, Loader=SafeLoader)

authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days'],
)

# Render the login widget
authenticator.login(location='main')

# Access authentication status and username
auth_status = st.session_state.get("authentication_status")
username = st.session_state.get("username")

# Handle login result
if auth_status is False:
    st.error("Username or password is incorrect.")
    st.stop()
elif auth_status is None:
    st.stop()
elif auth_status:
    col1, col2 = st.columns([4, 1])
    with col2: 
        authenticator.logout("Logout", location='main')

if auth_status:
    user_data_dir = f"data/{username}"

    if st.session_state.pop("to_initialize_csv", False):
        st.session_state.csv_ready = True
        st.session_state.just_imported = True
        st.rerun()

# SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# Secure environment values
DOMAIN = os.getenv("SNAP_DOMAIN") 
KEY_SUFFIX = os.getenv("SNAP_KEY_SUFFIX")
glc_id = st.session_state.get("username")

account_id = f"{glc_id}@{DOMAIN}"
access_key = f"{glc_id}-{KEY_SUFFIX}"

load_dotenv()
admin_supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

# Check and create Supabase user only once per session
if "supabase_user_checked" not in st.session_state:
    try:
        users = admin_supabase.auth.admin.list_users()
        user_exists = any(u.email == account_id for u in users)

        if not user_exists:
            user = admin_supabase.auth.admin.create_user({
                "email": account_id,
                "password": access_key,
                "user_metadata": {"glcid": glc_id},
                "options": {
                    "email_confirm": True
                }
            })
            st.session_state.supabase_uid = user.user.id
            st.success("Supabase user created.")

        st.session_state.supabase_user_checked = True

    except Exception as e:
        st.error(f"User lookup or creation failed: {e}")

# === Function to update progress bar ===
def update_progress(status, bar, message, percent):
    if status:
        status.write(message)
    if bar:
        bar.progress(percent)

# === Function to scrape Function Health ===
def scrape_function_health(user_email, user_pass, status=None, progress_bar=None):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920x1080")

    #service = Service(ChromeDriverManager().install())
    
    try:
        service = Service("/usr/bin/chromedriver") 
        options.add_argument(f"--binary=/usr/bin/chromium") 
    except Exception as e:
        print(f"Error setting up Selenium Service: {e}")
        raise 
        
    driver = None

    try:
        if status:
            update_progress(status, progress_bar, "Launching remote browser...", 10)

        driver = webdriver.Chrome(service=service, options=options)
        driver.get("https://my.functionhealth.com/")
        driver.maximize_window()

        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "email"))
        ).send_keys(user_email)

        if status:
            update_progress(status, progress_bar, "Accessing Function Health...", 20)

        driver.find_element(By.ID, "password").send_keys(user_pass + Keys.RETURN)
        time.sleep(5)
        if "login" in driver.current_url.lower():
            raise ValueError("Login failed — please check your Function Health credentials.")
        
        driver.get("https://my.functionhealth.com/biomarkers")

        if status:
            update_progress(status, progress_bar, "Importing biomarkers...", 30)

        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "[class^='biomarkerResultRow-styled__BiomarkerName']"))
        )

        everything = driver.find_elements(By.XPATH, "//h4 | //div[contains(@class, 'biomarkerResult-styled__ResultContainer')]")
        data = []
        current_category = None
        total = len(everything)

        for i, el in enumerate(everything):
            percent = 30 + int((i + 1) / total * 50)
            update_progress(status, progress_bar, "Importing biomarkers...", percent)

            tag = el.tag_name

            if tag == "h4":
                current_category = el.text.strip()

            elif tag == "div":
                try:
                    name = el.find_element(By.CSS_SELECTOR, "[class^='biomarkerResultRow-styled__BiomarkerName']").text.strip()
                    status_text = value = units = ""
                    values = el.find_elements(By.CSS_SELECTOR, "[class*='biomarkerChart-styled__ResultValue']")
                    texts = [v.text.strip() for v in values]

                    if len(texts) == 3:
                        status_text, value, units = texts
                    elif len(texts) == 2:
                        status_text, value = texts
                    elif len(texts) == 1:
                        value = texts[0]

                    try:
                        unit_el = el.find_element(By.CSS_SELECTOR, "[class^='biomarkerChart-styled__UnitValue']")
                        units = unit_el.text.strip()
                    except:
                        pass

                    data.append({
                        "category": current_category,
                        "name": name,
                        "status": status_text,
                        "value": value,
                        "units": units
                    })

                except Exception:
                    continue

    except Exception as e:
        print(f"An error occurred during scraping process: {type(e).__name__} — {e}")
        raise e

    finally:
        if driver:
            try:
                update_progress(status, progress_bar, "Closing remote browser...", 97)
                driver.quit()
                time.sleep(1)
            except Exception as quit_error:
                print(f"Error quitting driver: {quit_error}")

    return pd.DataFrame(data)

# === Redaction Function ===
def redact_prenuvo_pdf(input_path, output_path):
    doc = fitz.open(input_path)

    patient_name = None
    for i in range(min(3, len(doc))):
        text = doc[i].get_text()
        match = re.search(r"Patient:\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", text)
        if match:
            patient_name = match.group(1).strip()
            break

    patterns = [
        r"Time of scan:\s?.*",
        r"Sex:\s?.*",
        r"\b(Male|Female|Other|Non-Binary|Transgender)\b",
        r"Height:\s?.*",
        r"Weight:\s?.*",
        r"Date of Birth:\s?.*",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"Facility:\s?.*",
        r"Patient:\s?.*",
        r"Study:\s?[a-f0-9\-]{36}",
        r"REPORT RECIPIENT\(S\):\s?.*",
    ]

    if patient_name:
        escaped = re.escape(patient_name)
        patterns.append(rf"\b{escaped}\b")
        patterns.append(rf"Patient:\s*{escaped}")

    for page in doc:
        text = page.get_text()
        for pattern in patterns:
            for match in re.findall(pattern, text):
                for rect in page.search_for(match):
                    page.add_redact_annot(rect, fill=(0, 0, 0))
        page.apply_redactions()

    doc.save(output_path)
    doc.close()


# === Streamlit App ===
user_supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

if st.session_state.pop("just_deleted", False) or st.session_state.pop("just_imported", False):
    st.rerun()

tab1, tab2, tab3, tab4 = st.tabs(["Function Health", "Prenuvo", "Test Kits & Apps", "All Data"])

# === Try to restore saved CSV ===
if not st.session_state.get("csv_ready") and not st.session_state.get("skip_restore"):
    try:
        bucket = user_supabase.storage.from_("data")
        filename = f"{username}/functionhealth.csv"
        res = bucket.download(filename)

        if res and len(res) > 0:
            df = pd.read_csv(io.BytesIO(res))
            st.session_state.csv = res
            st.session_state.df = df
            st.session_state.csv_ready = True
        else:
            st.session_state.csv_ready = False
    except Exception as e:
        st.session_state.csv_ready = False

with tab1:
    st.markdown("<h1>Function Health</h1>", unsafe_allow_html=True)
    # === If deletion is in progress, stop everything else ===
    if st.session_state.get("deleting_in_progress", False):
        with st.spinner("Deleting file from database..."):
            st.session_state.pop("csv_ready", None)
            st.session_state.pop("csv", None)
            st.session_state.pop("df", None)
            st.session_state.pop("csv_filename", None)
            st.session_state.pop("supabase_uploaded", None)
            st.session_state.pop("function_email", None)
            st.session_state.pop("function_password", None)
            
            try:
                bucket = user_supabase.storage.from_("data")
                bucket.remove([f"{username}/functionhealth.csv"])

                max_attempts = 20
                file_still_exists = True

                for attempt in range(max_attempts):
                    time.sleep(3)
                    files = bucket.list(path=f"{username}/")
                    file_still_exists = any(f["name"] == "functionhealth.csv" for f in files)
                    if not file_still_exists:
                        break

                if not file_still_exists:
                    st.success("Resetting...")
                    time.sleep(1.5)
                    st.session_state.skip_restore = True
                    st.session_state.deletion_successful = True
                    st.session_state.just_deleted = True
                    st.session_state.pop("deleting_in_progress", None)
                    st.rerun()
                else:
                    st.error("File deletion timed out after 60 seconds. Please try again or check your connection.")

            except Exception as e:
                st.error(f"Something went wrong while deleting your file: {e}")

    # === If data is loaded, show it ===
    elif st.session_state.get("csv_ready") and "df" in st.session_state:
        st.dataframe(st.session_state.df)
        st.success("Import successful!")
    
        if st.button("Start Over"):
            st.session_state.deleting_in_progress = True
            st.rerun()

    # === If no data, show login form ===
    else:
        st.markdown("""
    <div style='font-size:17.5px; line-height:1.6'>
    Please enter your Function Health credentials to connect and download your data.
    </div>""", unsafe_allow_html=True)
    
        st.markdown("""
    <div style='font-size:17.5px; line-height:1.6; margin-top:0.5rem; margin-bottom:1.5rem;'>
    <strong>Your Information Stays Private:</strong> We do not store your credentials. They are used once to connect to Function Health to download your data, and then are immediately erased from memory.
    </div>""", unsafe_allow_html=True)


        with st.form("function_login_form"):
            user_email = st.text_input("Email", key="function_email")
            user_pass = st.text_input("Password", type="password", key="function_password")
            submitted = st.form_submit_button("Connect & Import Data")
        
        if submitted:
            if not user_email or not user_pass:
                st.error("Please enter email and password.")
                st.stop()
        
            st.session_state.pop("skip_restore", None)
            progress_bar = st.progress(0)
            status = st.empty()
        
            try:
                df = scrape_function_health(user_email, user_pass, status, progress_bar)
                update_progress(status, progress_bar, "Deleting Function Health credentials from memory...", 98)
                del user_email
                del user_pass
                st.session_state.pop("function_email", None)
                st.session_state.pop("function_password", None)
                time.sleep(1)
                status.empty()
                progress_bar.empty()

                csv_bytes = df.to_csv(index=False).encode()
                st.session_state.csv = csv_bytes
                st.session_state.df = df 
                st.session_state.csv_filename = f"{username}_functionhealth.csv"
                st.session_state.user_id = username
                st.session_state.csv_file = csv_bytes
        
                # Upload to Supabase
                filename = f"{username}/functionhealth.csv"
                bucket = user_supabase.storage.from_("data")
        
                try:
                    bucket.remove([filename])
                except Exception:
                    pass 
        
                response = bucket.upload(
                    path=filename,
                    file=csv_bytes,
                    file_options={"content-type": "text/csv"}
                )
        
                res_data = response.__dict__
                if "error" in res_data and res_data["error"]:
                    st.error("Upload failed.")
                else:
                    st.session_state.supabase_uploaded = True
        
                st.session_state.to_initialize_csv = True
                st.rerun()
        
            except ValueError as ve:
                progress_bar.empty()
                status.empty()
                st.error(str(ve))
        
            except Exception as e:
                st.error(f"Scraping failed: {type(e).__name__} — {e}")

with tab2:
    st.markdown("<h1>Prenuvo</h1>", unsafe_allow_html=True)
    filename = f"{username}/redacted_prenuvo_report.pdf"
    bucket = user_supabase.storage.from_("data")

    # === Handle Start Over
    if st.session_state.get("reset_prenuvo", False):
        st.session_state.pop("reset_prenuvo", None)
        with st.spinner("Deleting file from database..."):
            try:
                bucket.remove([filename])
                st.session_state.file_deleted = True
            except:
                st.warning("File deletion failed.")
            for k in ["approved_redaction", "issue_submitted", "show_report_box", "redacted_pdf_for_review"]:
                st.session_state.pop(k, None)
            time.sleep(1.5)
            st.rerun()

    # === Try loading redacted file
    file_exists = False
    file_bytes = None
    
    # Try to load from Supabase first if it's already approved
    if st.session_state.get("approved_redaction"):
        try:
            file_bytes = bucket.download(filename)
            file_exists = isinstance(file_bytes, bytes)
        except:
            pass # File not in Supabase yet, or error downloading

    # If not from Supabase or not yet approved, check session state for the redacted file ready for review
    if not file_exists and "redacted_pdf_for_review" in st.session_state:
        file_bytes = st.session_state.redacted_pdf_for_review
        
        # If we're displaying from session state, it means it's not yet approved in Supabase
        st.session_state.approved_redaction = False 
        file_exists = True # Indicate that we have bytes to display

    if file_exists:
        # If approved_redaction not yet set and we loaded from Supabase, set it now so success message persists
        if "approved_redaction" not in st.session_state and file_exists:
            st.session_state.approved_redaction = True

        # --- Display instructions BEFORE PDF viewer if pending approval ---
        if not st.session_state.get("approved_redaction"):
            st.markdown("""
                <div style='font-size:17.5px; line-height:1.6; margin-top:0.5rem; margin-bottom:1.5rem;'>
                <strong>Please Review Your Redacted Report:</strong> Browse through each page to ensure sensitive information has been removed. Click "Approve Redaction" to save the file to your account.
                </div>
            """, unsafe_allow_html=True)

        # === PDF Viewer ===
        base64_pdf = base64.b64encode(file_bytes).decode("utf-8")
        st.markdown(
            f'<iframe src="data:application/pdf;base64,{base64_pdf}#navpanes=0" width="100%" height="800px"></iframe>',
            unsafe_allow_html=True
        )

        if st.session_state.get("approved_redaction"):
            st.success("Upload successful!")
            if st.button("Start Over", key="start_over_after_approve"):
                st.session_state.reset_prenuvo = True
                st.rerun()
        else: # This block now only contains the buttons and issue form for pending approval
            if st.button("Approve Redaction", key="approve_redaction"):
                with st.spinner("Saving redacted file..."):
                    try:
                        bucket.upload(filename, file_bytes, {"content-type": "application/pdf"})
                        st.session_state.approved_redaction = True
                        st.session_state.pop("redacted_pdf_for_review", None)
                        st.session_state.pop("issue_submitted", None)
                        st.session_state.pop("show_report_box", None)
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to save redacted file: {e}")

            if st.button("Report an Issue", key="report_issue"):
                st.session_state.show_report_box = True
                st.session_state.pop("issue_submitted", None)

            if st.button("Start Over", key="start_over_before_approve"):
                st.session_state.reset_prenuvo = True
                st.rerun()

        # Issue form
        if st.session_state.get("show_report_box") and not st.session_state.get("issue_submitted"):
            issue = st.text_area("Describe the issue with redaction:")
            if st.button("Submit Issue", key="submit_issue"):
                timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S-%f")
                bucket.upload(
                    f"{username}/issues/issue_{timestamp}.txt",
                    issue.encode("utf-8"),
                    {"content-type": "text/plain"}
                )
                st.session_state.issue_submitted = True
                st.session_state.pop("show_report_box", None)
                st.rerun()

        if st.session_state.get("issue_submitted"):
            st.success("Issue submitted.")

    else:
        # Upload instructions
        st.markdown("<div style='font-size:17.5px; line-height:1.6'>Please upload your Prenuvo Physician Report:</div>", unsafe_allow_html=True)
        st.markdown("""
        <div style='font-size:15px; line-height:1.6; margin-bottom:0.5rem; padding-left:1.5rem'>
          <ol style="margin-top: 0; margin-bottom: 0;">
            <li>Log in to <a href='https://login.prenuvo.com/' target='_blank'>Prenuvo</a></li>
            <li>Click <strong>View Official Physician Report</strong></li>
            <li>Download the PDF</li>
            <li>Upload it below</li>
          </ol>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<div style='font-size:17.5px; line-height:1.6'>We will redact sensitive information and prepare a version for your review.</div>", unsafe_allow_html=True)

        uploaded = st.file_uploader("", type="pdf")
        if uploaded:
            with st.spinner("Redacting sensitive information..."):
                input_path = "/tmp/prenuvo_original.pdf"
                output_path = "/tmp/prenuvo_redacted.pdf"
                with open(input_path, "wb") as f:
                    f.write(uploaded.read())
                redact_prenuvo_pdf(input_path, output_path)
                os.remove(input_path)
                with open(output_path, "rb") as f:
                    pdf_bytes = f.read()
                os.remove(output_path) 

                st.session_state.redacted_pdf_for_review = pdf_bytes
                
                try:
                    bucket.remove([filename])
                except:
                    pass 

                st.session_state.pop("file_deleted", None)
                st.session_state.pop("approved_redaction", None)
                st.session_state.pop("issue_submitted", None)
                st.session_state.pop("show_report_box", None)
                
                time.sleep(1.5)
                st.rerun()
with tab3:
    st.markdown("<h1>Test Kits & Apps</h1>", unsafe_allow_html=True)

    testkit_filename = f"{username}/test_kits.csv"
    bucket = user_supabase.storage.from_("data")

    # === Load saved CSV if available ===
    if "test_kit_df" not in st.session_state:
        try:
            file_bytes = bucket.download(testkit_filename)
            st.session_state.test_kit_df = pd.read_csv(io.BytesIO(file_bytes))
        except Exception:
            st.session_state.test_kit_df = pd.DataFrame(columns=["Test Kit", "Metric", "Value"])

    # === Handle Start Over ===
    if st.session_state.get("reset_test_kit", False):
        with st.spinner("Deleting file from database..."):
            try:
                bucket.remove([testkit_filename])  # no .get() needed
                st.session_state.file_deleted = True
            except Exception as e:
                st.warning(f"Failed to delete file: {e}")
                st.session_state.file_deleted = False

        # Clear relevant session state
        for key in ["reset_test_kit", "testkit_submitted"]:
            st.session_state.pop(key, None)

        st.session_state.test_kit_df = pd.DataFrame(columns=["Test Kit", "Metric", "Value"])
        st.rerun()

    # === If no data yet, show form ===
    if st.session_state.test_kit_df.empty:
        st.markdown("""
        <div style='font-size:17.5px; line-height:1.6'>
        Please complete the fields below using your latest data from each app or test kit.<br><br>
        </div>""", unsafe_allow_html=True)

        with st.form("test_kit_form", border=True):

            def input_metric(label, expander_text):
                with st.container():
                    col1, col3 = st.columns([5, 4])
                    with col1:
                        st.markdown(
                            f"<div style='font-weight:600; font-size:1.2rem; margin-bottom:0.3rem'>{label}</div>",
                            unsafe_allow_html=True
                        )
                    with col3:
                        with st.expander("Where do I find this?"):
                            st.markdown(expander_text)
                st.text_input(label, key=label, label_visibility="collapsed")

            # === Input fields ===
            input_metric("Matter Score (all time)", """1. Open the Matter App on your iPhone  
2. Click **"You"** → **"Stats"**  
3. Scroll to **Stats to Date** → **Matter Score**""")

            st.divider()
            input_metric("Matter: Number of Memories", """1. Open the Matter App  
2. Go to **You → Stats** → **Total Memories**""")

            st.divider()
            input_metric("Trudiagnostic: Estimated Telomere Age", """1. Log in to [login.trudiagnostic.com](https://login.trudiagnostic.com)  
2. Go to **My Reports** → **Telomere Report**  
3. Find **Estimated Telomere Age**""")

            st.divider()
            input_metric("BioStarks: Longevity NAD+ Score", """1. Login to [results.biostarks.com](https://results.biostarks.com)  
2. Find your **Longevity Score** (0–100)""")

            st.divider()
            input_metric("BioStarks: NAD+ Levels", """1. On the Longevity page, hover **NAD+** hexagon  
2. Read value in **ug/gHb**""")

            st.divider()
            input_metric("BioStarks: Magnesium Levels", """1. Hover **Mg** hexagon on the Longevity page  
2. Read value in **ug/gHb**""")

            st.divider()
            input_metric("BioStarks: Selenium Levels", """1. Hover **Se** hexagon  
2. Read value in **ug/gHb**""")

            st.divider()
            input_metric("BioStarks: Zinc Levels", """1. Hover **Zn** hexagon  
2. Read value in **ug/gHb**""")

            st.divider()
            input_metric("Hero: VO2 Max (best result)", "Log into the Hero App on your iPhone and look for your best recorded VO2 Max.")

            submitted = st.form_submit_button("Submit")

        required_keys = [
            "Matter Score (all time)",
            "Matter: Number of Memories",
            "Trudiagnostic: Estimated Telomere Age",
            "BioStarks: Longevity NAD+ Score",
            "BioStarks: NAD+ Levels",
            "BioStarks: Magnesium Levels",
            "BioStarks: Selenium Levels",
            "BioStarks: Zinc Levels",
            "Hero: VO2 Max (best result)",
        ]
        #required_keys = [] 

        if submitted:
            missing = [k for k in required_keys if not st.session_state.get(k, "").strip()]
            if missing:
                st.error("Please complete all required fields before submitting.")
            else:
                df = pd.DataFrame([
                    ["Matter", "Matter Score (all time)", st.session_state["Matter Score (all time)"]],
                    ["Matter", "Number of Memories", st.session_state["Matter: Number of Memories"]],
                    ["Trudiagnostic", "Estimated Telomere Age", st.session_state["Trudiagnostic: Estimated Telomere Age"]],
                    ["BioStarks", "Longevity NAD+ Score", st.session_state["BioStarks: Longevity NAD+ Score"]],
                    ["BioStarks", "NAD+ Levels", st.session_state["BioStarks: NAD+ Levels"]],
                    ["BioStarks", "Magnesium Levels", st.session_state["BioStarks: Magnesium Levels"]],
                    ["BioStarks", "Selenium Levels", st.session_state["BioStarks: Selenium Levels"]],
                    ["BioStarks", "Zinc Levels", st.session_state["BioStarks: Zinc Levels"]],
                    ["Hero", "VO2 Max (best result)", st.session_state["Hero: VO2 Max (best result)"]],
                ], columns=["Test Kit or App", "Metric", "Value"])

                with st.spinner("Saving to database..."):
                    st.session_state.test_kit_df = df
                    csv_bytes = df.to_csv(index=False).encode()

                    try:
                        bucket.remove([testkit_filename])
                    except:
                        pass

                    bucket.upload(
                        path=testkit_filename,
                        file=csv_bytes,
                        file_options={"content-type": "text/csv"}
                    )

                    time.sleep(1)
                    st.session_state["testkit_submitted"] = True
                    st.rerun()

    # === If data exists, show table and start over ===
    else:
        st.dataframe(st.session_state.test_kit_df)
        st.success("Upload successful!")

        if st.button("Start Over", key="reset_testkit"):
            st.session_state.reset_test_kit = True
            st.rerun()

with tab4:
    st.markdown("## Behavioral Data")
    behavior_file = f"{username}/behavioral_scores.csv"
    try:
        behavior_bytes = user_supabase.storage.from_("data").download(behavior_file)
        if isinstance(behavior_bytes, bytes):
            behavior_df = pd.read_csv(io.BytesIO(behavior_bytes))
            st.dataframe(behavior_df)
        else:
            st.info("Please add your behavioral data.")
    except Exception as e:
        error_msg = str(e).lower()
        if "not found" in error_msg or "no such file" in error_msg:
            st.info("Please add your behavioral data.")
        else:
            st.warning("There was an error retrieving your behavioral data. Please contact admin.")

    st.markdown("## Oregon Data")
    behavior_file = f"{username}/Oregon.csv"
    try:
        behavior_bytes = user_supabase.storage.from_("data").download(behavior_file)
        if isinstance(behavior_bytes, bytes):
            behavior_df = pd.read_csv(io.BytesIO(behavior_bytes))
            st.dataframe(behavior_df)
        else:
            st.info("Please add your behavioral data.")
    except Exception as e:
        error_msg = str(e).lower()
        if "not found" in error_msg or "no such file" in error_msg:
            st.info("Please add your behavioral data.")
        else:
            st.warning("There was an error retrieving your behavioral data. Please contact admin.")

    st.markdown("## Function Health Data")

    filename = f"{username}/functionhealth.csv"
    bucket = user_supabase.storage.from_("data")

    try:
        file_bytes = bucket.download(filename)

        if isinstance(file_bytes, bytes):
            df = pd.read_csv(io.BytesIO(file_bytes))
            st.dataframe(df)
        else:
            st.info("Please add your Function Health data.")

    except Exception as e:
        error_msg = str(e).lower()
        if "not found" in error_msg or "no such file" in error_msg:
            st.info("Please import your Function Health data.")
        else:
            st.warning("There was an error retrieving your Function Health data. Please contact admin.")

    st.markdown("## Prenuvo Data")

    prenuvo_pdf_path = f"{username}/redacted_prenuvo_report.pdf"
    try:
        file_bytes = user_supabase.storage.from_("data").download(prenuvo_pdf_path)
        if isinstance(file_bytes, bytes):
            base64_pdf = base64.b64encode(file_bytes).decode("utf-8")
            pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="600px" type="application/pdf"></iframe>'
            st.markdown(pdf_display, unsafe_allow_html=True)
        else:
            st.info("Please add your Prenuvo data.")
    except Exception as e:
        error_msg = str(e).lower()
        if "not found" in error_msg or "no such file" in error_msg:
            st.info("Please add your Prenuvo data.")
        else:
            st.info("Please add your Prenuvo data.")


    st.markdown("## Test Kit & App Data")
    testkit_file = f"{username}/test_kits.csv"
    try:
        testkit_bytes = user_supabase.storage.from_("data").download(testkit_file)
        if isinstance(testkit_bytes, bytes):
            testkit_df = pd.read_csv(io.BytesIO(testkit_bytes))
            st.dataframe(testkit_df)
        else:
            st.info("Please add your Test Kit data.")
    except Exception as e:
        error_msg = str(e).lower()
        if "not found" in error_msg or "no such file" in error_msg:
            st.info("Please add your Test Kit & App data.")
        else:
            st.warning("There was an error retrieving your Test Kit & App data. Please contact admin.")
