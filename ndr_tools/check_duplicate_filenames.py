import os
from collections import defaultdict

def find_duplicate_filenames(root_directory):
    # Dictionary to store filename as key and list of paths as value
    file_map = defaultdict(list)
    
    print(f"Scanning: {root_directory}...\n")

    # Walk through all directories and subdirectories
    for root, dirs, files in os.walk(root_directory):
        for filename in files:
            # Construct the full path
            full_path = os.path.join(root, filename)
            file_map[filename].append(full_path)

    # Filter out entries that only appeared once
    duplicates = {name: paths for name, paths in file_map.items() if len(paths) > 1}

    if not duplicates:
        print("No duplicate filenames found.")
        return

    print(f"Found {len(duplicates)} duplicate filenames:\n")
    for name, paths in duplicates.items():
        print(f"File: '{name}'")
        for p in paths:
            print(f"  -> {p}")
        print("-" * 30)

# --- Configuration ---
TARGET_FOLDER = r"C:\Users\Admin\Downloads\projects\python\pythonscripts\ndr_tools\generated_xmls"

if __name__ == "__main__":
    find_duplicate_filenames(TARGET_FOLDER)