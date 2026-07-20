import os
import pandas as pd


def merge_excel_files(input_folder, output_file_path):
    """Merges all Excel files in a folder with identical headers into a single file."""
    # List to hold dataframes
    all_data = []

    # Target both .xlsx and .xls files
    excel_files = [
        f
        for f in os.listdir(input_folder)
        if f.endswith((".xlsx", ".xls")) and not f.startswith("~$")
    ]

    if not excel_files:
        print(f"No Excel files found in '{input_folder}'.")
        return

    print(f"Found {len(excel_files)} files to merge...")

    # Loop through and read each file
    for file in excel_files:
        file_path = os.path.join(input_folder, file)
        try:
            # Reads the first sheet by default
            df = pd.read_excel(file_path)

            # Optional: Add a column showing which file the data came from
            df["Source_File"] = file

            all_data.append(df)
            print(f"Successfully read: {file}")
        except Exception as e:
            print(f"Error reading {file}: {e}")

    # Combine all dataframes into one
    if all_data:
        print("Merging files...")
        # ignore_index=True re-indexes the rows from 0 to total rows smoothly
        combined_df = pd.concat(all_data, ignore_index=True)

        # Save to a new Excel file
        combined_df.to_excel(output_file_path, index=False)
        print(f"\nSuccess! All files merged into: {output_file_path}")
    else:
        print("No data was successfully loaded.")


# --- CONFIGURATION ---
# Replace with the path to the folder containing your Excel files
FOLDER_PATH = r"C:\Users\Admin\Documents\radet\dropped_prints"

# Where you want to save the final merged file
OUTPUT_PATH = r"C:\Users\Admin\Documents\radet\dropped_prints\merged.xlsx"

# Run the function
if __name__ == "__main__":
    merge_excel_files(FOLDER_PATH, OUTPUT_PATH)