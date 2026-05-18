import time
import re
from pathlib import Path
from urllib.parse import urlparse
import tkinter as tk
from tkinter import simpledialog
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException

# Preferred download location for this automation
DOWNLOAD_DIR = Path.home() / "Downloads" / "NDR_Exports"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads"
TARGET_HOST = "ndr.nascp.gov.ng"


def get_target_filename_from_dialog():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    raw_name = simpledialog.askstring(
        "CSV File Name",
        "Enter filename for the downloaded CSV (without extension is fine):",
        parent=root,
    )
    root.destroy()

    if not raw_name or not raw_name.strip():
        raise ValueError("No file name was supplied.")

    safe_name = re.sub(r'[<>:"/\\|?*]+', "_", raw_name.strip()).rstrip(".")
    if not safe_name:
        raise ValueError("The supplied file name is invalid after sanitization.")

    if not safe_name.lower().endswith(".csv"):
        safe_name += ".csv"

    return safe_name


def get_selection_from_dialog(title, prompt):
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    value = simpledialog.askstring(title, prompt, parent=root)
    root.destroy()
    # if not value or not value.strip():
    #     raise ValueError(f"No value was supplied for: {title}")
    return value.strip()


def click_first_available(waiter, locators):
    last_error = None
    for by, locator in locators:
        try:
            elem = waiter.until(EC.element_to_be_clickable((by, locator)))
            elem.click()
            return
        except Exception as ex:
            last_error = ex
    raise last_error if last_error else TimeoutException("No download trigger was clickable.")


def wait_for_download_completion(download_dir, before_files, timeout=30):
    print("Waiting for download to complete...")
    print(download_dir)
    print(before_files)
    end_time = time.time() + timeout
    partial_ext = {".crdownload", ".tmp", ".part"}

    while time.time() < end_time:
        current_files = {p for p in download_dir.glob("*") if p.is_file()}
        new_files = list(current_files - before_files)
        print(current_files)
        print(new_files)

        partials = [p for p in new_files if p.suffix.lower() in partial_ext]
        completed = [p for p in new_files if p.suffix.lower() not in partial_ext]

        if completed and not partials:
            return max(completed, key=lambda p: p.stat().st_mtime)

        time.sleep(1)

    raise TimeoutException("Download did not complete within the time limit.")


def apply_site_specific_download_dir(active_driver):
    """
    Use preferred folder only when current page host matches target host.
    Returns the active folder used for this download action.
    """
    print("apply_site_specific")
    current_host = (urlparse(active_driver.current_url).hostname or "").lower()
    is_target = current_host == TARGET_HOST or current_host.endswith(f".{TARGET_HOST}")
    selected_dir = DOWNLOAD_DIR if is_target else DEFAULT_DOWNLOAD_DIR
    selected_dir.mkdir(parents=True, exist_ok=True)

    # Chromium (Edge/Chrome): set download behavior at runtime.
    try:
        active_driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(selected_dir)},
        )
    except Exception:
        # If CDP call is unavailable, browser falls back to startup preferences.
        pass

    return selected_dir


# Initialize Driver (ensure you have the correct driver for your browser)
edge_options = webdriver.EdgeOptions()
edge_options.add_experimental_option(
    "prefs",
    {
        "download.default_directory": str(DEFAULT_DOWNLOAD_DIR),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    },
)
target_filename = get_target_filename_from_dialog()
selected_state = get_selection_from_dialog("State", "Enter the State name (e.g. Lagos):")
selected_lga = get_selection_from_dialog("LGA", "Enter the LGA name (e.g. Lagos Mainland):") or ""
selected_facility = get_selection_from_dialog("Facility", "Enter the Facility name (e.g. Lagos State Mainland Hospital):") or ""
driver = webdriver.Edge(options=edge_options)  # Or webdriver.Chrome(options=...)
wait = WebDriverWait(driver, 60)


try:
    

    # 1. Login
    driver.get("https://ndr.nascp.gov.ng")
    driver.find_element(By.ID, "Email").send_keys("aomoaka@ecews.org")
    driver.find_element(By.ID, "Password").send_keys("Pass1word#")
    driver.find_element(By.ID, "loginButton").click()

    # 2. Navigate to Reports
    driver.get("https://ndr.nascp.gov.ng/reporting/line-lists")
    #wait.until(EC.element_to_be_clickable((By.XPATH, "//li[3]/a/i"))).click()
    #wait.until(EC.element_to_be_clickable((By.XPATH, "//div[@id='navbarNavDropdown']/ul/li[3]/div/div[2]/div/a/span"))).click()

    # 3. Select Report and Indicator
    # Note: Standard <select> tags use the Select class
    report_dropdown = Select(wait.until(EC.presence_of_element_located((By.ID, "selectLineList"))))
    report_dropdown.select_by_visible_text("Patient Line List")

    indicator_dropdown = Select(wait.until(EC.presence_of_element_located((By.ID, "selectIndicator2"))))
    indicator_dropdown.select_by_visible_text("Ever-Enrolled")

    # 4. Handle Custom Dropdowns (Select2 Style)
    def select_custom_dropdown(input_xpath, text_to_type, selection_text):
        # Click the input field
        field = wait.until(EC.element_to_be_clickable((By.XPATH, input_xpath)))
        field.click()
        field.send_keys(text_to_type)
        
        # Wait for the result list and click the specific item
        result_xpath = f"//li[contains(@class, 'select2-results') and text()='{selection_text}']"
        result_item = wait.until(EC.element_to_be_clickable((By.XPATH, result_xpath)))
        result_item.click()
        time.sleep(1) # Small pause for AJAX/UI updates

    # Select State
    select_custom_dropdown("//div[@id='statesDiv']//input", selected_state, selected_state)
    
    # Select LGA
    
    if selected_lga:
        select_custom_dropdown("//div[@id='lgaselectLGAsDiv']//input", selected_lga, selected_lga)
    
    # Select Facility
    if selected_facility:
        select_custom_dropdown("//div[@id='selectFacilitiesDiv']//input", selected_facility, selected_facility)

    
    # 5. Load Data
    driver.find_element(By.ID, "loadData").click()
    

    # 6. Export and download CSV
    active_download_dir = apply_site_specific_download_dir(driver)
    pre_download_files = {p for p in active_download_dir.glob("*") if p.is_file()}
    # print("Initiating CSV download...")
    # click_first_available(
    #     wait,
    #     [
    #         (By.ID, "exportCsv"),
    #         (By.XPATH, "//button[contains(translate(., 'csv', 'CSV'), 'CSV')]"),
    #         (By.XPATH, "//a[contains(translate(., 'csv', 'CSV'), 'CSV')]"),
    #         (By.XPATH, "//*[@title='Export CSV' or @aria-label='Export CSV']"),
    #     ],
    # )

    downloaded_file = wait_for_download_completion(active_download_dir, pre_download_files, timeout=30)

    final_path = active_download_dir / target_filename
    if final_path.exists():
        stem, suffix = final_path.stem, final_path.suffix
        index = 1
        while final_path.exists():
            final_path = active_download_dir / f"{stem}_{index}{suffix}"
            index += 1

    downloaded_file.replace(final_path)
    print(f"CSV saved to: {final_path}")
    print("Test Completed Successfully")

except TimeoutException:
    print("Test failed: An element was not found within the time limit.")
except Exception as e:
    print(f"An error occurred: {e}")
finally:
    # Keep browser open for a few seconds to see result
    time.sleep(5)
    driver.quit()
