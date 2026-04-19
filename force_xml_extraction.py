import os
import json
import zipfile
import glob
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import messagebox
from dotenv import load_dotenv

load_dotenv()

def msg_box(message, title):
    root = tk.Tk()
    root.withdraw()  # Hide the main tkinter window
    messagebox.showinfo(title, message)
    root.destroy()

# 1. Database Connection and Retrieval
db_params = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT")
}

try:
    conn = psycopg2.connect(**db_params)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # Retrieve facility ID
    cursor.execute("SELECT current_organisation_unit_id FROM base_application_user LIMIT 1")
    row = cursor.fetchone()
    facility = row['current_organisation_unit_id']

    # 2. Run cleaning scripts (Equivalent to psql -f .\test.sql)
    """with open('test.sql', 'r') as f:
        cursor.execute(f.read())
    conn.commit()
    cursor.close()
    conn.close()"""
except Exception as e:
    print(f"Database error: {e}")
    exit()

# 3. Authentication and Token Retrieval
auth_url = os.getenv("AUTH_URL")
#auth_url ="http://localhost:9120/api/v1/authenticate"
payload = {
    "username": os.getenv("USER"),
    "password": os.getenv("PASSWORD")
}

if not payload["username"] or not payload["password"]:
    print("Error: USERNAME or PASSWORD not found in environment variables")
    exit()
print(os.getenv("USER"))
print(os.getenv("PASSWORD"))
response = requests.post(auth_url, json=payload)
if response.status_code != 200:
    print(f"Authentication failed: {response.status_code} - {response.text}")
    exit()
token = response.json().get('id_token')

# 4. Date Calculation
today = datetime.now().strftime("%Y-%m-%d")
last_week = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

# 5. API Call for Optimization
api_url = f"http://localhost:9120/api/v1/ndr/optimization/date-range"
params = {
    "facilityIds": facility,
    "startDate": last_week,
    "endDate": today
}
headers = {"Authorization": f"Bearer {token}"}

# This mimics the curl command
requests.get(api_url, params=params, headers=headers)

# 6. File Compression
source_dir = os.path.join(os.getenv('NDR_TEMP_DIR', 'runtime/ndr/transfer/temp'), f"runtime/ndr/transfer/temp/{facility}/")
print(source_dir)
xml_files = glob.glob(os.path.join(source_dir, "*.xml"))
destination_zip = os.path.join(os.path.expanduser("~"), "Downloads", "treatmentxml.zip")

message = ""
if xml_files:
    with zipfile.ZipFile(destination_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file in xml_files:
            zipf.write(file, os.path.basename(file))
    message = "NDR files are ready for upload."
else:
    print("File Path does not exist or no XML files found.")
    message = "No valid refills between period"

# 7. Display Status
msg_box(message, "Extraction Status")