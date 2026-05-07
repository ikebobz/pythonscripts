import os
import shutil
import pandas as pd

def cleanup_unlisted_files(folder_path, excel_path, column_name):
    """
    Moves files not listed in an Excel sheet to a 'not-seen' subfolder.
    """
    # 1. Load the Excel data
    try:
        df = pd.read_excel(excel_path)
        # Convert the specific column to a set for O(1) lookup speed
        # We also ensure all entries are strings for consistent comparison
        allowed_filenames = set(df[column_name].astype(str).tolist())
    except Exception as e:
        print(f"Error reading Excel file: {e}")
        return

    # 2. Create the destination folder if it doesn't exist
    target_folder = os.path.join(folder_path, "not-seen")
    if not os.path.exists(target_folder):
        os.makedirs(target_folder)

    # 3. Walk through the files
    files_moved = 0
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)

        # Skip if it's a directory (like the 'not-seen' folder itself)
        # or the Excel file we are currently reading
        if os.path.isdir(file_path) or filename == os.path.basename(excel_path):
            continue

        # 4. Check if filename exists in the Excel set
        if filename not in allowed_filenames:
            print(f"Moving: {filename}")
            shutil.move(file_path, os.path.join(target_folder, filename))
            files_moved += 1

    print(f"\nTask complete. Moved {files_moved} files to {target_folder}.")

# --- Configuration ---
FOLDER_TO_CHECK = r"C:\Users\Admin\Downloads\projects\python\pythonscripts\ndr_tools\generated_xmls"
EXCEL_FILE_PATH = r"ingested.xlsx"
COLUMN_WITH_NAMES = "FileName"  # Change this to match your Excel header

if __name__ == "__main__":
    cleanup_unlisted_files(FOLDER_TO_CHECK, EXCEL_FILE_PATH, COLUMN_WITH_NAMES)