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
        service = Service("/usr/bin/chromedriver") 
        options.add_argument(f"--binary=/usr/bin/chromium") 
    except Exception as e:
        print(f"Error setting up Selenium Service: {e}")
        raise 

    #service = Service(ChromeDriverManager().install())

    driver = None
    
    try:
        if status:
            update_progress(status, progress_bar, "Launching virtual browser...", 10)
        
        driver = webdriver.Chrome(service=service, options=options)
        driver.get("https://my.functionhealth.com/")
        driver.maximize_window()
        
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "email"))
        ).send_keys(user_email)

        if status:
            update_progress(status, progress_bar, "Entering Function Health credentials...", 20)
        
        driver.find_element(By.ID, "password").send_keys(user_pass + Keys.RETURN)
        time.sleep(5)
            
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
                update_progress(status, progress_bar, "Deleting Function Health credentials from memory...", 95)
                user_pass = None
                user_email = None
                time.sleep(2) 
                update_progress(status, progress_bar, "Closing virtual browser...", 98)
                driver.quit()
                time.sleep(0.5) 
        
            except Exception as quit_error:
                  print(f"Error quitting driver: {quit_error}")       

    return pd.DataFrame(data)


# === Streamlit App ===
load_dotenv()
EMAIL = os.getenv("EMAIL_ADDRESS")
PASS = os.getenv("EMAIL_PASSWORD")

tab1, tab2 = st.tabs(["Function Health", "Prenuvo"])

if 'csv_ready' not in st.session_state:
    st.session_state.csv_ready = False
    st.session_state.csv = None
    st.session_state.csv_filename = None

with tab1:
    st.markdown("<h1>Function Health</h1>", unsafe_allow_html=True)
    
    if not st.session_state.csv_ready:
        st.markdown("""
    <div style='font-size:17.5px; line-height:1.6'>
    Please enter your Function Health credentials to connect and download your data.
    </div>""", unsafe_allow_html=True)
    
        st.markdown("""
    <div style='font-size:17.5px; line-height:1.6; margin-top:0.5rem; margin-bottom:1.5rem;'>
    <strong>Your Information Stays Private:</strong> We do not store your credentials. They are used once to connect to Function Health to download your data, and then are immediately erased from memory.
    </div>""", unsafe_allow_html=True)

    if not st.session_state.csv_ready:
        user_email = st.text_input("Email")
        user_pass = st.text_input("Password", type="password")
        #user_id = st.text_input("GLC ID (your data will be associated with this ID)")
        user_id = username

        if st.button("Connect & Import Data"):
            progress_bar = st.progress(0)
            status = st.empty()
            
            try:
                df = scrape_function_health(user_email, user_pass, status, progress_bar)
                csv_bytes = df.to_csv(index=False).encode()

                st.session_state.csv = csv_bytes
                st.session_state.csv_filename = f"{user_id}_functionhealth.csv"
                st.session_state.csv_ready = True
                st.session_state.user_id = user_id
                st.rerun()

            except Exception as e:
                st.error(f"Scraping failed: {type(e).__name__} — {e}")

    if st.session_state.csv_ready:
        df = pd.read_csv(pd.io.common.BytesIO(st.session_state.csv))
        st.session_state.df = df
        st.dataframe(df)
        st.success("Import successful!")

        st.text_input("Where should we send your data?", key="email_target")
        col1, col2 = st.columns([1, 1])

        with col1:
            if st.button("Email Data"):
                try:
                    msg = EmailMessage()
                    msg["Subject"] = f"Function Health Data – {st.session_state.get('user_id', 'GLCXXX')}"
                    msg["From"] = EMAIL
                    msg["To"] = st.session_state.email_target
                    msg.set_content("Attached is the Function Health data you requested.")
                    msg.add_attachment(st.session_state.csv, maintype="text", subtype="csv",
                                      filename=st.session_state.csv_filename)

                    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
                        smtp.login(EMAIL, PASS)
                        smtp.send_message(msg)

                    st.success(f"Sent {st.session_state.csv_filename} to {st.session_state.email_target}")
                except Exception as e:
                    st.error(f"Failed to send email: {type(e).__name__} — {e}")

        with col2:
            st.download_button("Download Data", st.session_state.csv, file_name=st.session_state.csv_filename)

with tab2:
    st.title("Prenuvo Data (Coming Soon)")
    st.info("This page will allow users to import Prenuvo health reports. Stay tuned!")
