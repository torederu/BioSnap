from flask import Flask, request, send_file, jsonify
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import pandas as pd
import time
import os

app = Flask(__name__)

def scrape_function_health(user_email, user_pass):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920x1080")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )

    driver.get("https://my.functionhealth.com/")
    driver.maximize_window()

    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "email"))
    ).send_keys(user_email)

    driver.find_element(By.ID, "password").send_keys(user_pass + Keys.RETURN)
    time.sleep(5)

    driver.get("https://my.functionhealth.com/biomarkers")

    WebDriverWait(driver, 12).until(
        EC.presence_of_element_located((By.CLASS_NAME, "biomarkerResultRow-styled__BiomarkerName-sc-3bf584b3-1"))
    )

    everything = driver.find_elements(By.XPATH, "//h4 | //div[contains(@class, 'biomarkerResult-styled__ResultContainer')]")
    data = []
    current_category = None

    for el in everything:
        tag = el.tag_name
        if tag == "h4":
            current_category = el.text.strip()
        elif tag == "div":
            try:
                name = el.find_element(By.CLASS_NAME, "biomarkerResultRow-styled__BiomarkerName-sc-3bf584b3-1").text.strip()

                status = value = units = ""

                # Get result values
                values = el.find_elements(By.CSS_SELECTOR, "[class*='biomarkerChart-styled__ResultValue']")
                texts = [v.text.strip() for v in values]
                print("Found texts:", texts)

                if len(texts) == 3:
                    status, value, units = texts
                elif len(texts) == 2:
                    status, value = texts
                elif len(texts) == 1:
                    value = texts[0]

                # Try to get the units from a separate span
                try:
                    unit_el = el.find_element(By.CSS_SELECTOR, "[class^='biomarkerChart-styled__UnitValue']")
                    units = unit_el.text.strip()
                except:
                    pass

                data.append({
                    "category": current_category,
                    "name": name,
                    "status": status,
                    "value": value,
                    "units": units
                })
            except Exception:
                continue

    driver.quit()
    return pd.DataFrame(data)

@app.route("/scrape", methods=["POST"])
def scrape():
    try:
        data = request.json
        email = data.get("email")
        password = data.get("password")
        glc_id = data.get("glc_id")

        df = scrape_function_health(email, password)
        filename = f"{glc_id}_functionhealth.csv"
        df.to_csv(filename, index=False)

        return send_file(filename, as_attachment=True)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/")
def index():
    return "Flask is running!"

if __name__ == "__main__":
    app.run(debug=True, port=5000)

