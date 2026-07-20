import os
import requests
from dotenv import load_dotenv

load_dotenv()

AUTH_URL = os.getenv("AUTH_URL")
UPLOAD_URL = os.getenv("UPLOAD_URL")
INSTALL_URL = os.getenv("INSTALL_URL")
JAR_PATH = r"C:\Users\Admin\Downloads\installers\pre_release_18_06\casemanager-2.1.1.jar"

def get_token():
    payload = {
        "username": os.getenv("USER"),
        "password": os.getenv("PASSWORD")
    }

    if not payload["username"] or not payload["password"]:
        raise RuntimeError("USER or PASSWORD not found in environment variables")

    response = requests.post(AUTH_URL, json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"Authentication failed: {response.status_code} - {response.text}")

    token = response.json().get("id_token")
    if not token:
        raise RuntimeError("id_token not found in authentication response")

    return token

def upload_module(token):
    headers = {
        "Authorization": f"Bearer {token}"
    }

    with open(JAR_PATH, "rb") as f:
        files = {
            "file": ("DQR-2.0.1.jar", f, "application/java-archive")
        }
        response = requests.post(UPLOAD_URL, files=files, headers=headers)

    response.raise_for_status()
    return response.json()

def install_module(token, module_payload):
    headers = {
        "Authorization": f"Bearer {token}"
    }

    response = requests.post(
        INSTALL_URL,
        params={"install": "true"},
        json=module_payload,
        headers=headers
    )
    response.raise_for_status()
    return response.json()

def main():
    token = get_token()
    module_payload = upload_module(token)
    install_result = install_module(token, module_payload)
    print("Upload response:", module_payload)
    print("Install response:", install_result)

if __name__ == "__main__":
    main()