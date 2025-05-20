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
from supabase import create_client, Client

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

    # try:
    #     service = Service("/usr/bin/chromedriver") 
    #     options.add_argument(f"--binary=/usr/bin/chromium") 
    # except Exception as e:
    #     print(f"Error setting up Selenium Service: {e}")
    #     raise 

    service = Service(ChromeDriverManager().install())

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
        
        biomarker_divs = [el for el in everything if el.tag_name == "div"]
        total = len(biomarker_divs)

        for i, el in enumerate(biomarker_divs):
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

# === Streamlit App ===
user_supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

if st.session_state.pop("just_deleted", False) or st.session_state.pop("just_imported", False):
    st.rerun()

tab1, tab2, tab3 = st.tabs(["Function Health", "Prenuvo", "Data Profile"])

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

        # user_email = st.text_input("Email", key="function_email")
        # user_pass = st.text_input("Password", type="password", key="function_password")
        # user_id = username

        # st.session_state.pop("skip_restore", None)
        # progress_bar = st.progress(0)
        # status = st.empty()
        
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

    # === If deletion is in progress, stop everything else ===
    if st.session_state.get("prenuvo_deleting", False):
        with st.spinner("Deleting file from database..."):
            st.session_state.pop("prenuvo_ready", None)
            st.session_state.pop("prenuvo_csv", None)
            st.session_state.pop("prenuvo_df", None)
            st.session_state.pop("prenuvo_filename", None)
            st.session_state.pop("prenuvo_uploaded", None)
            st.session_state.pop("prenuvo_email", None)
            st.session_state.pop("prenuvo_password", None)

            try:
                bucket = user_supabase.storage.from_("data")
                bucket.remove([f"{username}/prenuvo.csv"])

                max_attempts = 20
                file_still_exists = True

                for attempt in range(max_attempts):
                    time.sleep(3)
                    files = bucket.list(path=f"{username}/")
                    file_still_exists = any(f["name"] == "prenuvo.csv" for f in files)
                    if not file_still_exists:
                        break

                if not file_still_exists:
                    st.success("Resetting...")
                    time.sleep(1.5)
                    st.session_state.prenuvo_skip_restore = True
                    st.session_state.prenuvo_deleted = True
                    st.session_state.pop("prenuvo_deleting", None)
                    st.rerun()
                else:
                    st.error("File deletion timed out after 60 seconds. Please try again or check your connection.")
            except Exception as e:
                st.error(f"Something went wrong while deleting your file: {e}")

    # === If data is loaded, show it ===
    elif st.session_state.get("prenuvo_ready") and "prenuvo_df" in st.session_state:
        st.dataframe(st.session_state.prenuvo_df)
        st.success("Import successful!")

        if st.button("Start Over", key="prenuvo_reset"):
            st.session_state.prenuvo_deleting = True
            st.rerun()

    # === If no data, show login form ===
    else:
        st.markdown("""
        <div style='font-size:17.5px; line-height:1.6'>
        Please enter your Prenuvo credentials to connect and download your data.
        </div>""", unsafe_allow_html=True)

        st.markdown("""
        <div style='font-size:17.5px; line-height:1.6; margin-top:0.5rem; margin-bottom:1.5rem;'>
        <strong>Your Information Stays Private:</strong> We do not store your credentials. They are used once to connect to Prenuvo to download your data, and then are immediately erased from memory.
        </div>""", unsafe_allow_html=True)

        with st.form("prenuvo_login_form"):
            user_email = st.text_input("Email", key="prenuvo_email")
            user_pass = st.text_input("Password", type="password", key="prenuvo_password")
            submitted = st.form_submit_button("Connect & Import Data")

        if submitted:
            if not user_email or not user_pass:
                st.error("Please enter email and password.")
                st.stop()

            st.session_state.pop("prenuvo_skip_restore", None)
            progress_bar = st.progress(0)
            status = st.empty()

            try:
                # === Replace this with your actual scraping logic ===
                df = scrape_prenuvo(user_email, user_pass, status, progress_bar)
                update_progress(status, progress_bar, "Deleting Prenuvo credentials from memory...", 98)

                del user_email
                del user_pass
                st.session_state.pop("prenuvo_email", None)
                st.session_state.pop("prenuvo_password", None)
                time.sleep(1)
                status.empty()
                progress_bar.empty()

                csv_bytes = df.to_csv(index=False).encode()
                st.session_state.prenuvo_csv = csv_bytes
                st.session_state.prenuvo_df = df
                st.session_state.prenuvo_filename = f"{username}_prenuvo.csv"
                st.session_state.prenuvo_file = csv_bytes

                # Upload to Supabase
                filename = f"{username}/prenuvo.csv"
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
                    st.session_state.prenuvo_uploaded = True

                st.session_state.prenuvo_ready = True
                st.rerun()

            except ValueError as ve:
                progress_bar.empty()
                status.empty()
                st.error(str(ve))

            except Exception as e:
                st.error(f"Scraping failed: {type(e).__name__} — {e}")

with tab3: 
    st.header("Submit Test Kit Results")

    testkit_filename = f"{username}/test_kits.csv"
    bucket = user_supabase.storage.from_("data")

    # === Load saved data ===
    if "test_kit_df" not in st.session_state:
        try:
            file_bytes = bucket.download(testkit_filename)
            st.session_state.test_kit_df = pd.read_csv(io.BytesIO(file_bytes))
        except Exception:
            st.session_state.test_kit_df = pd.DataFrame(columns=["Test Kit", "Metric", "Value"])

    if st.session_state.get("reset_test_kit", False):
        st.session_state.test_kit_df = pd.DataFrame(columns=["Test Kit", "Metric", "Value"])
        st.session_state.pop("reset_test_kit")
        try:
            bucket.remove([testkit_filename])
        except:
            pass
        st.success("Form has been reset.")

    # === Form ===
    with st.form("test_kit_form"):
        telomere_age = st.text_input("Trudiagnostic: Estimated Telomere Age")
        nad = st.text_input("BioStarks: NAD+ levels")
        magnesium = st.text_input("BioStarks: Magnesium levels")
        selenium = st.text_input("BioStarks: Selenium levels")
        zinc = st.text_input("BioStarks: Zinc levels")
        longevity = st.text_input("BioStarks: Longevity NAD+ Score")
        vo2max = st.text_input("Hero: VO2 Max (best result)")

        submitted = st.form_submit_button("Submit")

    if submitted:
        df = pd.DataFrame([
            ["Trudiagnostic", "Estimated Telomere Age", telomere_age],
            ["BioStarks", "NAD+ levels", nad],
            ["BioStarks", "Magnesium levels", magnesium],
            ["BioStarks", "Selenium levels", selenium],
            ["BioStarks", "Zinc levels", zinc],
            ["BioStarks", "Longevity NAD+ Score", longevity],
            ["Hero", "VO2 Max (best result)", vo2max],
        ], columns=["Test Kit", "Metric", "Value"])

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

        st.success("Test Kit data saved successfully.")
        st.rerun()

    # === Show table and reset ===
    if not st.session_state.test_kit_df.empty:
        st.subheader("Your Test Kit Results")
        st.dataframe(st.session_state.test_kit_df)

        if st.button("Start Over"):
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
    st.info("Please add your Prenuvo data.")

    st.markdown("## Test kit")
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
            st.info("Please add your Test Kit data.")
        else:
            st.warning("There was an error retrieving your Test Kit data. Please contact admin.")
