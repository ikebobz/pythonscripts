import pandas as pd

def extract_specific_columns(input_file, output_file, sheet_name=0):
    """
    Filters an Excel file to keep only specific columns.
    """
    # The list of columns you want to keep
    target_columns = [
        "Patient ID", 
        "State", 
        "Facility Name", 
        "DatimId", 
        "NDR Patient Identifier", 
        "Sex", 
        "Date of Birth (yyyy-mm-dd)", 
        "Last Pickup Date (yyyy-mm-dd)", 
        "Months of ARV Refill", 
        "Date of Start of Current ART Regimen", 
        "Current ART Regimen"
    ]

    try:
        # Load the Excel file (Sheet 1 is index 0)
        print(f"Reading {input_file}...")
        df = pd.read_excel(input_file, sheet_name=sheet_name)

        # Check which of the target columns exist in the file
        existing_cols = [col for col in target_columns if col in df.columns]
        missing_cols = [col for col in target_columns if col not in df.columns]

        if missing_cols:
            print(f"Warning: The following columns were not found: {missing_cols}")

        if not existing_cols:
            print("Error: None of the target columns were found in the file.")
            return

        # Filter the dataframe
        df_filtered = df[existing_cols]

        # Save to a new Excel file
        df_filtered.to_excel(output_file, index=False)
        print(f"Successfully saved filtered data to: {output_file}")

    except Exception as e:
        print(f"An error occurred: {e}")

# --- CONFIGURATION ---
# Change these filenames to match your local files
INPUT_FILENAME = "Active on EMR Not Active on NDR.xlsx" 
OUTPUT_FILENAME = "Filtered_Patient_Data.xlsx"

if __name__ == "__main__":
    extract_specific_columns(INPUT_FILENAME, OUTPUT_FILENAME)