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
import streamlit.components.v1 as components
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

    if st.session_state.pop("to_initialize_function_csv", False):
        st.session_state.function_csv_ready = True
        st.session_state.just_imported = True
        st.rerun()

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
            try:
                user = admin_supabase.auth.admin.create_user({
                    "email": account_id,
                    "password": access_key,
                    "user_metadata": {"glcid": glc_id},
                    "options": {"email_confirm": True}
                })
                st.session_state.supabase_uid = user.user.id
            except Exception as create_err:
                if "already been registered" not in str(create_err).lower():
                    raise create_err  # Only raise if it's not a duplicate error

        st.session_state.supabase_user_checked = True

    except Exception:
        st.warning("Supabase user setup failed. Please try again later.")
        st.stop()

# === Function to update Function Health progress bar ===
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

    try:
        service = Service(ChromeDriverManager().install())
    except Exception:
        service = Service("/usr/bin/chromedriver")
        options.add_argument("--binary=/usr/bin/chromium")

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

# === Prenuvo Redaction Function ===
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
        r"\b(Male|Female|Other|Non-Binary|Transgender|Intersex)\b",
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

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Function Health", "Prenuvo", "Trudiagnostic", "Biostarks", "Interventions"])

# === Try to restore saved CSV (stateless ghost-block logic)
if not st.session_state.get("function_csv_ready"):
    try:
        bucket = user_supabase.storage.from_("data")
        function_filename = f"{username}/functionhealth.csv"
        res = bucket.download(function_filename)
        files = bucket.list(path=f"{username}/")
        in_list = any(f["name"] == "functionhealth.csv" for f in files)

        # === Ghost file — block it
        if res and len(res) > 0 and not in_list:
            st.session_state.function_csv_ready = False
        elif res and len(res) > 0:
            function_df = pd.read_csv(io.BytesIO(res))
            st.session_state.function_csv = res
            st.session_state.function_df = function_df
            st.session_state.function_csv_ready = True
        else:
            st.session_state.function_csv_ready = False
    except Exception:
        st.session_state.function_csv_ready = False


# === Try to restore saved CSV (stateless ghost-block logic)
if not st.session_state.get("csv_ready"):
    try:
        bucket = user_supabase.storage.from_("data")
        filename = f"{username}/functionhealth.csv"
        res = bucket.download(filename)
        files = bucket.list(path=f"{username}/")
        in_list = any(f["name"] == "functionhealth.csv" for f in files)

        # === Ghost file — block it
        if res and len(res) > 0 and not in_list:
            st.session_state.csv_ready = False
        elif res and len(res) > 0:
            df = pd.read_csv(io.BytesIO(res))
            st.session_state.csv = res
            st.session_state.df = df
            st.session_state.csv_ready = True
        else:
            st.session_state.csv_ready = False
    except Exception:
        st.session_state.csv_ready = False


with tab1:
    st.markdown("<h1>Function Health</h1>", unsafe_allow_html=True)
    # === If deletion is in progress, stop everything else ===
    if st.session_state.get("deleting_in_progress", False):
        with st.spinner("Deleting file from database..."):
            st.session_state.pop("function_csv_ready", None)
            st.session_state.pop("function_csv", None)
            st.session_state.pop("function_df", None)
            st.session_state.pop("function_csv_filename", None)
            st.session_state.pop("function_supabase_uploaded", None)
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
    elif st.session_state.get("function_csv_ready") and "function_df" in st.session_state:
        st.dataframe(st.session_state.function_df)
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
                function_df = scrape_function_health(user_email, user_pass, status, progress_bar)
                update_progress(status, progress_bar, "Deleting Function Health credentials from memory...", 98)
                del user_email
                del user_pass
                st.session_state.pop("function_email", None)
                st.session_state.pop("function_password", None)
                time.sleep(1)
                status.empty()
                progress_bar.empty()

                function_csv_bytes = function_df.to_csv(index=False).encode()
                st.session_state.function_csv = function_csv_bytes
                st.session_state.function_df = function_df
                st.session_state.function_csv_filename = f"{username}_functionhealth.csv"
                st.session_state.function_csv_file = function_csv_bytes

                # Upload to Supabase
                function_filename = f"{username}/functionhealth.csv"
                bucket = user_supabase.storage.from_("data")

                try:
                    bucket.remove([function_filename])
                except Exception:
                    pass

                response = bucket.upload(
                    path=function_filename,
                    file=function_csv_bytes,
                    file_options={"content-type": "text/csv"}
                )

                res_data = response.__dict__
                if "error" in res_data and res_data["error"]:
                    st.error("Upload failed.")
                else:
                    st.session_state.function_supabase_uploaded = True

                st.session_state.to_initialize_function_csv = True
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

    file_list = bucket.list(path=username)
    file_exists = any(f["name"] == "redacted_prenuvo_report.pdf" for f in file_list)

    if file_exists:
        st.success("Your report was successfully redacted and saved!")
        try:
            pdf_bytes = bucket.download(filename)
            if isinstance(pdf_bytes, bytes):
                st.download_button("Download Report", pdf_bytes, file_name="redacted_prenuvo_report.pdf")
            else:
                st.error("Failed to retrieve the file. Please try again.")
        except Exception as e:
            st.error(f"Error retrieving file: {e}")

    elif "redacted_pdf_for_review" in st.session_state:
        file_bytes = st.session_state.redacted_pdf_for_review
        st.markdown("""
            <div style='font-size:17.5px; line-height:1.6; margin-top:0.5rem; margin-bottom:1.5rem;'>
            <strong>Please Review Your Redacted Report:</strong> Browse through each page to ensure sensitive information has been removed. Click "Approve Redaction" to save the file to your account.
            </div>
        """, unsafe_allow_html=True)

        base64_pdf = base64.b64encode(file_bytes).decode("utf-8")
        st.markdown(f"""
            <div style='font-size:17.5px; line-height:1.6; margin-bottom:1rem;'>
            <a href="data:application/pdf;base64,{base64_pdf}" download="redacted_prenuvo_report.pdf">Click here to download your redacted report.</a><br>
            Or scroll through the preview below to review each page.
            </div>
        """, unsafe_allow_html=True)

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page_images = [
            page.get_pixmap(dpi=150).tobytes("png")
            for page in doc
            if page.get_text().strip()
        ]
        doc.close()

        img_html_blocks = [
            f"<img src='data:image/png;base64,{base64.b64encode(img).decode()}' style='width:100%; margin-bottom: 1.5rem;'/>"
            for img in page_images
        ]

        scrollable_html = f"""
        <div style='height:650px; overflow-y:scroll; border:1px solid #ccc; padding:12px; background-color:#f9f9f9;'>
            {''.join(img_html_blocks)}
        </div>
        """

        components.html(scrollable_html, height=670, scrolling=False)

        if st.button("Approve Redaction", key="approve_redaction"):
            with st.spinner("Saving redacted file..."):
                try:
                    bucket.upload(filename, file_bytes, {"content-type": "application/pdf"})
                    st.session_state.pop("redacted_pdf_for_review", None)
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to save redacted file: {e}")

        if st.button("Report an Issue", key="report_issue"):
            st.session_state.show_report_box = True

        if st.button("Start Over", key="start_over_before_approve"):
            st.session_state.pop("redacted_pdf_for_review", None)
            st.rerun()

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

                for k in ["approved_redaction", "issue_submitted", "show_report_box"]:
                    st.session_state.pop(k, None)

                time.sleep(1.5)
                st.rerun()


with tab3:
    def redact_trudiagnostic_pdf(input_path, output_path):
        doc = fitz.open(input_path)

        for i, page in enumerate(doc):
            # === Page 1 logic: redact name (above age), and demographic blocks
            if i == 0:
                body_patterns = [
                    r"Sex:\s*\w+",
                    r"Age:\s*\d+",
                    r"https?://[^\s]+",
                    r"www\.[^\s]+"
                ]

                for pattern in body_patterns:
                    matches = re.finditer(pattern, page.get_text())
                    for match in matches:
                        matched_text = match.group()
                        for rect in page.search_for(matched_text):
                            page.add_redact_annot(rect, fill=(0, 0, 0))

                text_blocks = page.get_text("blocks")
                for j, block in enumerate(text_blocks):
                    if "Age:" in block[4]:
                        if j > 0:
                            name_block = text_blocks[j - 1]
                            rect = fitz.Rect(name_block[:4])
                            page.add_redact_annot(rect, fill=(0, 0, 0))
                        break

                for block in text_blocks:
                    text = block[4]
                    if any(keyword in text for keyword in ["ID#:", "Collected:", "Reported:"]):
                        rect = fitz.Rect(block[:4])
                        page.add_redact_annot(rect, fill=(0, 0, 0))

            # === Footer cleanup on all pages
            for block in page.get_text("blocks"):
                if "PROVIDED BY:" in block[4] or "trudiagnostic.com" in block[4] or "trudiagnostic/apireports.aspx" in block[4]:
                    rect = fitz.Rect(block[:4])
                    page.add_redact_annot(rect, fill=(0, 0, 0))

            page.apply_redactions()

        doc.save(output_path)
        doc.close()

    st.markdown("<h1>Trudiagnostic</h1>", unsafe_allow_html=True)
    filename = f"{username}/redacted_trudiagnostic_report.pdf"
    bucket = user_supabase.storage.from_("data")

    file_list = bucket.list(path=username)
    file_exists = any(f["name"] == "redacted_trudiagnostic_report.pdf" for f in file_list)

    if file_exists:
        st.success("Your report was successfully redacted and saved!")
        try:
            pdf_bytes = bucket.download(filename)
            if isinstance(pdf_bytes, bytes):
                st.download_button("Download Report", pdf_bytes, file_name="redacted_trudiagnostic_report.pdf")
            else:
                st.error("Failed to retrieve the file. Please try again.")
        except Exception as e:
            st.error(f"Error retrieving file: {e}")
    elif "trudiagnostic_pdf_for_review" in st.session_state:
        file_bytes = st.session_state.trudiagnostic_pdf_for_review
        st.markdown("""
            <div style='font-size:17.5px; line-height:1.6; margin-top:0.5rem; margin-bottom:1.5rem;'>
            <strong>Please Review Your Redacted Report:</strong> Browse through each page to ensure sensitive information has been removed. Click "Approve Redaction" to save the file to your account.
            </div>
        """, unsafe_allow_html=True)

        base64_pdf = base64.b64encode(file_bytes).decode("utf-8")
        st.markdown(f"""
            <div style='font-size:17.5px; line-height:1.6; margin-bottom:1rem;'>
            <a href="data:application/pdf;base64,{base64_pdf}" download="redacted_trudiagnostic_report.pdf">Click here to download your redacted report.</a><br>
            Or scroll through the preview below to review each page.
            </div>
        """, unsafe_allow_html=True)

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page_images = [
            page.get_pixmap(dpi=150).tobytes("png")
            for page in doc
            if page.get_text().strip()
        ]
        doc.close()

        img_html_blocks = [
            f"<img src='data:image/png;base64,{base64.b64encode(img).decode()}' style='width:100%; margin-bottom: 1.5rem;'/>"
            for img in page_images
        ]

        scrollable_html = f"""
        <div style='height:650px; overflow-y:scroll; border:1px solid #ccc; padding:12px; background-color:#f9f9f9;'>
            {''.join(img_html_blocks)}
        </div>
        """

        components.html(scrollable_html, height=670, scrolling=False)

        if st.button("Approve Redaction", key="approve_trudiagnostic"):
            with st.spinner("Saving redacted file..."):
                try:
                    bucket.upload(filename, file_bytes, {"content-type": "application/pdf"})
                    st.session_state.pop("trudiagnostic_pdf_for_review", None)
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to save redacted file: {e}")

        if st.button("Report an Issue", key="report_trudiagnostic_issue"):
            st.session_state.trudiagnostic_show_report_box = True

        if st.button("Start Over", key="start_over_trudiagnostic_before_approve"):
            st.session_state.pop("trudiagnostic_pdf_for_review", None)
            st.rerun()

        if st.session_state.get("trudiagnostic_show_report_box") and not st.session_state.get("trudiagnostic_issue_submitted"):
            issue = st.text_area("Describe the issue with redaction:")
            if st.button("Submit Issue", key="submit_trudiagnostic_issue"):
                timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S-%f")
                bucket.upload(
                    f"{username}/issues/issue_{timestamp}.txt",
                    issue.encode("utf-8"),
                    {"content-type": "text/plain"}
                )
                st.session_state.trudiagnostic_issue_submitted = True
                st.session_state.pop("trudiagnostic_show_report_box", None)
                st.rerun()

        if st.session_state.get("trudiagnostic_issue_submitted"):
            st.success("Issue submitted.")
    else:
        st.markdown("<div style='font-size:17.5px; line-height:1.6'>Please upload your Trudiagnostic Provider Summary:</div>", unsafe_allow_html=True)
        st.markdown("""
        <div style='font-size:15px; line-height:1.6; margin-bottom:0.5rem; padding-left:1.5rem'>
          <ol style="margin-top: 0; margin-bottom: 0;">
            <li>Log in to <a href='https://login.trudiagnostic.com/' target='_blank'>Trudiagnostic</a></li>
            <li>Click <strong>"Reports"</strong> in the left menu bar</li>
            <li>Open the <strong>"Provider Summary Report"</strong></li>
            <li>Click <strong>"Print"</strong> in the top right corner</li>
            <li>In the print window, choose <strong>"Save as PDF"</strong> as the destination<br>
            <span style='font-size: 0.95em;'>(On Macs, select “PDF” in the dropdown menu. On Windows, choose “Microsoft Print to PDF” as your printer.)</span></li>
            <li>Save the file to your computer</li>
            <li>Upload it below</li>
          </ol>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<div style='font-size:17.5px; line-height:1.6'>We will redact sensitive information and prepare a version for your review.</div>", unsafe_allow_html=True)

        uploaded = st.file_uploader("", type="pdf", key="trudiagnostic_upload")
        if uploaded:
            with st.spinner("Redacting sensitive information..."):
                input_path = "/tmp/trudiagnostic_original.pdf"
                output_path = "/tmp/trudiagnostic_redacted.pdf"
                with open(input_path, "wb") as f:
                    f.write(uploaded.read())
                redact_trudiagnostic_pdf(input_path, output_path)
                os.remove(input_path)
                with open(output_path, "rb") as f:
                    pdf_bytes = f.read()
                os.remove(output_path)

                st.session_state.trudiagnostic_pdf_for_review = pdf_bytes
                time.sleep(1.5)
                st.rerun()


with tab4:
    st.markdown("<h1>Biostarks</h1>", unsafe_allow_html=True)

    biostarks_filename = f"{username}/biostarks.csv"
    bucket = user_supabase.storage.from_("data")

    # === Load saved CSV if available — block ghost files
    if "biostarks_df" not in st.session_state:
        try:
            biostarks_bytes = bucket.download(biostarks_filename)
            files = bucket.list(path=username)
            in_list = any(f["name"] == "biostarks.csv" for f in files)

            if biostarks_bytes and len(biostarks_bytes) > 0 and in_list:
                st.session_state.biostarks_df = pd.read_csv(io.BytesIO(biostarks_bytes))
            else:
                st.session_state.biostarks_df = pd.DataFrame(columns=["Metric", "Value"])
        except Exception:
            st.session_state.biostarks_df = pd.DataFrame(columns=["Metric", "Value"])

    # === Handle Start Over ===
    if st.session_state.get("reset_biostarks", False):
        with st.spinner("Deleting file from database..."):
            try:
                bucket.remove([biostarks_filename])
                st.session_state.biostarks_deleted = True
            except Exception as e:
                st.warning(f"Failed to delete file: {e}")
                st.session_state.biostarks_deleted = False

        for key in ["reset_biostarks", "biostarks_submitted"]:
            st.session_state.pop(key, None)

        st.session_state.biostarks_df = pd.DataFrame(columns=["Metric", "Value"])
        st.rerun()

    # === If no data yet, show form ===
    if st.session_state.biostarks_df.empty:
        st.markdown("""
        <div style='font-size:17.5px; line-height:1.6'>
        Please log in to <a href='https://results.biostarks.com/' target='_blank'>Biostarks</a> and fill in the fields below with the relevant values.<br><br>
        </div>
        """, unsafe_allow_html=True)

        with st.form("biostarks_form", border=True):

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
            input_metric("Longevity NAD+ Score", """• Log in to [results.biostarks.com](https://results.biostarks.com)  
            • Look for your **Longevity Score** (0–100)""")

            st.divider()
            input_metric("NAD+ Levels", """• Log in to [results.biostarks.com](https://results.biostarks.com)  
            • Click your **Longevity Score**  
            • Hover over the **NAD+** hexagon  
            • Value will be shown in **ug/gHb**""")

            st.divider()
            input_metric("Magnesium Levels", """• Log in to [results.biostarks.com](https://results.biostarks.com)  
            • Click your **Longevity Score**  
            • Hover over the **Mg** hexagon  
            • Value will be shown in **ug/gHb**""")

            st.divider()
            input_metric("Selenium Levels", """• Log in to [results.biostarks.com](https://results.biostarks.com)  
            • Click your **Longevity Score**  
            • Hover over the **Se** hexagon  
            • Value will be shown in **ug/gHb**""")

            st.divider()
            input_metric("Zinc Levels", """• Log in to [results.biostarks.com](https://results.biostarks.com)  
            • Click your **Longevity Score**  
            • Hover over the **Zn** hexagon  
            • Value will be shown in **ug/gHb**""")

            submitted = st.form_submit_button("Submit")

        required_keys = [
            "Longevity NAD+ Score",
            "NAD+ Levels",
            "Magnesium Levels",
            "Selenium Levels",
            "Zinc Levels",
        ]

        if submitted:
            missing = [k for k in required_keys if not st.session_state.get(k, "").strip()]
            if missing:
                st.error("Please complete all required fields before submitting.")
            else:
                biostarks_df = pd.DataFrame([
                    ["Longevity NAD+ Score", st.session_state["Longevity NAD+ Score"]],
                    ["NAD+ Levels", st.session_state["NAD+ Levels"]],
                    ["Magnesium Levels", st.session_state["Magnesium Levels"]],
                    ["Selenium Levels", st.session_state["Selenium Levels"]],
                    ["Zinc Levels", st.session_state["Zinc Levels"]],
                ], columns=["Metric", "Value"])


                with st.spinner("Saving to database..."):
                    st.session_state.biostarks_df = biostarks_df
                    biostarks_csv_bytes = biostarks_df.to_csv(index=False).encode()

                    try:
                        bucket.remove([biostarks_filename])
                    except:
                        pass

                    bucket.upload(
                        path=biostarks_filename,
                        file=biostarks_csv_bytes,
                        file_options={"content-type": "text/csv"}
                    )

                    time.sleep(1)
                    st.session_state["biostarks_submitted"] = True
                    st.rerun()

    # === If data exists, show table and start over ===
    else:
        st.dataframe(st.session_state.biostarks_df)
        st.success("Upload successful!")

        if st.button("Start Over", key="reset_biostarks"):
            st.session_state.reset_biostarks = True
            st.rerun()

# with tab5:
#     st.markdown("## Behavioral Data")
#     behavior_scores_file = f"{username}/behavioral_scores.csv"
#     try:
#         behavior_scores_bytes = user_supabase.storage.from_("data").download(behavior_scores_file)
#         if isinstance(behavior_scores_bytes, bytes):
#             behavior_scores_df = pd.read_csv(io.BytesIO(behavior_scores_bytes))
#             st.dataframe(behavior_scores_df)
#         else:
#             st.info("Please add your behavioral data.")
#     except Exception as e:
#         error_msg = str(e).lower()
#         if "not found" in error_msg or "no such file" in error_msg:
#             st.info("Please add your behavioral data.")
#         else:
#             st.warning("There was an error retrieving your behavioral data. Please contact admin.")

#     st.markdown("## Oregon Data")
#     oregon_file = f"{username}/Oregon.csv"
#     try:
#         oregon_bytes = user_supabase.storage.from_("data").download(oregon_file)
#         if isinstance(oregon_bytes, bytes):
#             oregon_df = pd.read_csv(io.BytesIO(oregon_bytes))
#             st.dataframe(oregon_df)
#         else:
#             st.info("Please add your Oregon data.")
#     except Exception as e:
#         error_msg = str(e).lower()
#         if "not found" in error_msg or "no such file" in error_msg:
#             st.info("Please add your Oregon data.")
#         else:
#             st.warning("There was an error retrieving your Oregon data. Please contact admin.")

#     st.markdown("## Function Health Data")
#     fh_file = f"{username}/functionhealth.csv"
#     bucket = user_supabase.storage.from_("data")

#     try:
#         fh_bytes = bucket.download(fh_file)
#         if isinstance(fh_bytes, bytes):
#             fh_df = pd.read_csv(io.BytesIO(fh_bytes))
#             st.dataframe(fh_df)
#         else:
#             st.info("Please add your Function Health data.")
#     except Exception as e:
#         error_msg = str(e).lower()
#         if "not found" in error_msg or "no such file" in error_msg:
#             st.info("Please import your Function Health data.")
#         else:
#             st.warning("There was an error retrieving your Function Health data. Please contact admin.")

#     st.markdown("## Prenuvo Data")
#     prenuvo_pdf_path = f"{username}/redacted_prenuvo_report.pdf"
#     try:
#         prenuvo_bytes = user_supabase.storage.from_("data").download(prenuvo_pdf_path)
#         if isinstance(prenuvo_bytes, bytes):
#             b64 = base64.b64encode(prenuvo_bytes).decode()
#             st.markdown(f"<a href='data:application/pdf;base64,{b64}' download='redacted_prenuvo_report.pdf'>Click here to download your redacted Prenuvo report.</a>", unsafe_allow_html=True)
#         else:
#             st.info("Please add your Prenuvo data.")
#     except Exception as e:
#         st.info("Please add your Prenuvo data.")

#     st.markdown("## Trudiagnostic Data")
#     trudiagnostic_pdf_path = f"{username}/redacted_trudiagnostic_report.pdf"
#     try:
#         trudiagnostic_bytes = user_supabase.storage.from_("data").download(trudiagnostic_pdf_path)
#         if isinstance(trudiagnostic_bytes, bytes):
#             b64 = base64.b64encode(trudiagnostic_bytes).decode()
#             st.markdown(f"<a href='data:application/pdf;base64,{b64}' download='redacted_trudiagnostic_report.pdf'>Click here to download your redacted Trudiagnostic report.</a>", unsafe_allow_html=True)
#         else:
#             st.info("Please add your Trudiagnostic data.")
#     except Exception as e:
#         st.info("Please add your Trudiagnostic data.")

#     st.markdown("## Biostarks")
#     biostarks_file = f"{username}/biostarks.csv"
#     try:
#         biostarks_bytes = user_supabase.storage.from_("data").download(biostarks_file)
#         if isinstance(biostarks_bytes, bytes):
#             biostarks_df = pd.read_csv(io.BytesIO(biostarks_bytes))
#             st.dataframe(biostarks_df)
#         else:
#             st.info("Please add your Biostarks data.")
#     except Exception as e:
#         error_msg = str(e).lower()
#         if "not found" in error_msg or "no such file" in error_msg:
#             st.info("Please add your Biostarks data.")
#         else:
#             st.warning("There was an error retrieving your Biostarks data. Please contact admin.")


with tab5:
    # === Try to load saved plan if not already in session state ===
    if "intervention_plan_df" not in st.session_state:
        try:
            plan_filename = f"{username}/intervention_plan.csv"
            bucket = user_supabase.storage.from_("data")

            # Step 1: List all files under this user
            metadata = bucket.list(username)
            filenames = [f["name"] for f in metadata]

            # Step 2: Only proceed if file exists
            if "intervention_plan.csv" in filenames:
                # Grab timestamp
                matching = next((f for f in metadata if f["name"] == "intervention_plan.csv"), None)
                if matching and "updated_at" in matching:
                    from dateutil import parser
                    st.session_state.intervention_plan_timestamp = parser.parse(matching["updated_at"]).strftime("%B %d, %Y")

                # Download the file
                import pandas as pd
                import io
                bytes_data = bucket.download(plan_filename)
                if isinstance(bytes_data, bytes):
                    df = pd.read_csv(io.BytesIO(bytes_data))
                    st.session_state.intervention_plan_df = df
        except:
            pass

    # === If saved plan exists, show it only ===
    if "intervention_plan_df" in st.session_state:
        timestamp = st.session_state.get("intervention_plan_timestamp")
        st.markdown(f"## Intervention Plan (Saved on {timestamp})" if timestamp else "## Intervention Plan")
        st.dataframe(st.session_state.intervention_plan_df)

    # === Otherwise, guide the user to create a new plan ===
    else:
        st.markdown("""
        ## Intervention Plan

        Use this space to design your personalized 8-week intervention plan. 
        Choose which domains you'd like to focus on, then describe the specific actions you'll commit to. 
        Your plan will be saved and viewable any time you return.
        """, unsafe_allow_html=True)

        focus_areas = [
            "Emotional Wellbeing",
            "Mental Fitness",
            "Physical Fitness",
            "Metabolic Fitness",
            "Sleep",
            "Addiction Dependency"
        ]

        examples = {
            "Emotional Wellbeing": "Example: Morning walks in nature 3x/week to discover silent spots. Weekend trips to scenic places.",
            "Mental Fitness": "Example: Daily Nuroe working memory game. Weekly cognitive training exercises.",
            "Physical Fitness": "Example: Add 1–2 60-90 minute GA1 endurance sessions per week.",
            "Metabolic Fitness": "Example: Supplement Fe, Zn, Mg, NMN+. Reduce sugar. Aim for HbA1c < 6.5.",
            "Sleep": "Example: Set 10:30 PM bedtime, limit screen use after 9 PM, take magnesium glycinate.",
            "Addiction Dependency": "Example: Reduce phone or social media use. Replace late-night scrolling with journaling. Limit alcohol to 1x/week. Track urges daily."
        }

        if "intervention_step" not in st.session_state:
            st.session_state.intervention_step = "select_areas"

        if st.session_state.intervention_step == "select_areas":
            st.markdown("### Choose your focus areas")
            with st.form("intervention_focus_area_form"):
                selected = st.multiselect("Select areas to focus on:", focus_areas, default=st.session_state.get("intervention_selected_areas", []))
                proceed = st.form_submit_button("Next")
                if proceed:
                    st.session_state.intervention_selected_areas = selected
                    st.session_state.intervention_step = "enter_plans"
                    st.rerun()

        elif st.session_state.intervention_step == "enter_plans":
            st.markdown("### Describe Your Plans")
            with st.spinner("Loading plan fields..."):
                with st.form("intervention_plan_entry_form"):
                    plans = {}
                    for area in st.session_state.intervention_selected_areas:
                        plans[area] = st.text_area(
                            f"Plan for {area}",
                            key=f"plan_{area}",
                            placeholder=examples.get(area, f"What do you want to do to improve your {area.lower()} over the next 8 weeks?")
                        )
                    submitted = st.form_submit_button("Save My Plan")

                if submitted:
                    import pandas as pd
                    import io
                    from datetime import datetime

                    plan_df = pd.DataFrame([(k, v) for k, v in plans.items()], columns=["Category", "Plan"])
                    st.session_state.intervention_plan_df = plan_df

                    # Save to Supabase
                    csv_bytes = plan_df.to_csv(index=False).encode()
                    plan_filename = f"{username}/intervention_plan.csv"
                    bucket = user_supabase.storage.from_("data")

                    try:
                        bucket.remove([plan_filename])
                    except:
                        pass

                    bucket.upload(
                        path=plan_filename,
                        file=csv_bytes,
                        file_options={"content-type": "text/csv"}
                    )

                    st.session_state.intervention_plan_timestamp = datetime.utcnow().strftime("%B %d, %Y")
                    st.rerun()