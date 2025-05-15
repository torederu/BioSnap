import streamlit as st
import pandas as pd
import requests
import time
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
import threading
from flask_backend import app
import os

def run_flask():
    app.run(host="0.0.0.0", port=5000)

threading.Thread(target=run_flask).start()

load_dotenv()

EMAIL = os.getenv("EMAIL_ADDRESS")
PASS = os.getenv("EMAIL_PASSWORD")

st.set_page_config(page_title="HealthPort", layout="centered")
tab1, tab2 = st.tabs(["Function Health", "Prenuvo"])

# Initialize session state
if 'csv_ready' not in st.session_state:
    st.session_state.csv_ready = False
    st.session_state.csv = None
    st.session_state.csv_filename = None

with tab1:
    st.markdown("<h1>Function Health</h1>", unsafe_allow_html=True)

    st.markdown("""
<div style='font-size:17.5px; line-height:1.6'>
Please enter your Function Health credentials to connect and download your data.
</div>
""", unsafe_allow_html=True)

    st.markdown("""
<div style='font-size:17.5px; line-height:1.6; margin-top:0.5rem; margin-bottom:1.5rem;'>
<strong>Your Information Stays Private:</strong> We do not store your credentials. They are used once to connect to Function Health to download your data, and then are immediately erased from memory.
</div>
""", unsafe_allow_html=True)

    if not st.session_state.csv_ready:
        user_email = st.text_input("Email")
        user_pass = st.text_input("Password", type="password")
        user_id = st.text_input("GLC ID (your data will be associated with this ID)")

        if st.button("Connect & Import Data"):
            status_text = st.empty()
        
            with st.spinner("Importing data..."):

                st.write("Data being sent:", {
                "email": user_email,
                "glc_id": user_id
                })
            
                try:
                    response = requests.post("http://127.0.0.1:5000/scrape", json={
                        "email": user_email,
                        "password": user_pass,
                        "glc_id": user_id
                    })
        
                    if response.status_code == 200:    
                        st.session_state.csv = response.content
                        st.session_state.csv_filename = f"{user_id}_functionhealth.csv"
                        st.session_state.csv_ready = True
                        st.session_state.user_id = user_id 
                        st.rerun()

                    else:
                        st.error(f"Something went wrong: {response.status_code} — {response.text}")
        
                except Exception as e:
                    st.error(f"Request failed: {type(e).__name__} — {e}")

    if st.session_state.csv_ready:
        df = pd.read_csv(pd.io.common.BytesIO(st.session_state.csv))
        st.session_state.df = df  
        st.dataframe(df)
    
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