import os
import csv
import requests
from dotenv import load_dotenv
import subprocess

load_dotenv()

AUTH_URL = os.getenv("AUTH_URL")
UPLOAD_URL = os.getenv("UPLOAD_URL")
INSTALL_URL = os.getenv("INSTALL_URL")
CSV_PATH = r"modules.csv"
#bat_path = r"migration_split.bat"
bat_path = r"consolidated_migration.bat"

def get_token():
    payload = {
        "username": os.getenv("USER"),
        "password": os.getenv("PASSWORD")
    }
    if not payload["username"] or not payload["password"]:
        raise RuntimeError("USER or PASSWORD not found in environment variables")

    response = requests.post(AUTH_URL, json=payload)
    response.raise_for_status()

    token = response.json().get("id_token")
    if not token:
        raise RuntimeError("id_token not found in authentication response")
    return token

def upload_module(token, jar_path):
    headers = {"Authorization": f"Bearer {token}"}
    jar_name = os.path.basename(jar_path)

    with open(jar_path, "rb") as f:
        files = {
            "file": (jar_name, f, "application/java-archive")
        }
        response = requests.post(UPLOAD_URL, files=files, headers=headers)

    response.raise_for_status()
    return response.json()

def install_module(token, module_payload):
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.post(
        INSTALL_URL,
        params={"install": "true"},
        json=module_payload,
        headers=headers
    )
    response.raise_for_status()
    return response.json()

def process_csv(csv_path):
    token = get_token()

    with open(csv_path, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            jar_path = row.get("filepath", "").strip()
            if not jar_path:
                print("Skipping empty filepath row")
                continue

            if not os.path.exists(jar_path):
                print(f"File not found: {jar_path}")
                continue

            try:
                print(f"Uploading: {jar_path}")
                module_payload = upload_module(token, jar_path)

                print(f"Installing: {jar_path}")
                install_result = install_module(token, module_payload)

                print("Upload response:", module_payload)
                print("Install response:", install_result)
            except Exception as e:
                print(f"Failed for {jar_path}: {e}")
    subprocess.run(bat_path, shell=True, check=True)

if __name__ == "__main__":
    process_csv(CSV_PATH)