import pandas as pd

def extract_specific_columns(input_file, subset_file, output_file, source_sheet=0, subset_sheet="Sheet1"):
    """
    Filters an Excel file to keep only specific columns AND narrows down the rows
    to a subset of clients provided in a separate Excel file.
    """
    # The list of columns you want to keep from the source file
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
        # 1. Load the subset client list
        print(f"Reading subset client list from {subset_file} [{subset_sheet}]...")
        # Reading as string prevents dropping leading zeros if IDs look like numbers
        df_subset = pd.read_excel(subset_file, sheet_name=subset_sheet, dtype=str)
        
        # Determine which column in the subset file contains the identifiers
        # If it specifically has 'NDR Patient Identifier', we use that; otherwise, we fall back to the first column
        subset_col = "NDR Patient Identifier" if "NDR Patient Identifier" in df_subset.columns else df_subset.columns[0]
        print(f"Using column '{subset_col}' from subset file for filtering.")
        
        # Create a set of valid IDs for fast lookup, dropping any blank rows
        valid_identifiers = set(df_subset[subset_col].dropna().str.strip())
        print(f"Found {len(valid_identifiers)} unique client identifiers for filtering.")

        # 2. Load the main source Excel file
        print(f"Reading source file: {input_file}...")
        df_source = pd.read_excel(input_file, sheet_name=source_sheet)

        # 3. Verify target columns exist in the source file
        existing_cols = [col for col in target_columns if col in df_source.columns]
        missing_cols = [col for col in target_columns if col not in df_source.columns]

        if missing_cols:
            print(f"Warning: The following target columns were not found in source: {missing_cols}")

        if "NDR Patient Identifier" not in df_source.columns:
            print("Error: 'NDR Patient Identifier' column missing from source file. Cannot apply filter.")
            return

        if not existing_cols:
            print("Error: None of the target columns were found in the source file.")
            return

        # 4. Filter rows based on the NDR Patient Identifier subset
        print("Filtering rows matching the subset client list...")
        # Ensure comparison is done as stripped strings to handle formatting mismatches
        df_source["_match_id"] = df_source["NDR Patient Identifier"].astype(str).str.split('.').str[0].str.strip()
        
        df_filtered_rows = df_source[df_source["_match_id"].isin(valid_identifiers)]
        print(f"Rows matched: {len(df_filtered_rows)} out of {len(df_source)} total source rows.")

        if df_filtered_rows.empty:
            print("Warning: No matching clients were found between the subset list and the source file.")

        # 5. Filter columns to only keep target fields
        df_final = df_filtered_rows[existing_cols]

        # 6. Save to a new Excel file
        df_final.to_excel(output_file, index=False)
        print(f"Successfully saved filtered data to: {output_file}")

    except Exception as e:
        print(f"An error occurred: {e}")

# --- CONFIGURATION ---
INPUT_FILENAME = "Radet_all.xlsx" 
SUBSET_FILENAME = "target_clients.xlsx"  # <-- Change this to your client list file name
OUTPUT_FILENAME = "test_data.xlsx"

if __name__ == "__main__":
    extract_specific_columns(
        input_file=INPUT_FILENAME, 
        subset_file=SUBSET_FILENAME, 
        output_file=OUTPUT_FILENAME,
        source_sheet=0,
        subset_sheet="Sheet1"  # Explicitly targeting Sheet1
    )