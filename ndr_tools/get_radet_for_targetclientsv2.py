import pandas as pd

ART_START_COL = "ART Start Date (yyyy-mm-dd)"


def extract_specific_columns(input_file, subset_file, output_file, review_file,
                             source_sheet=0, subset_sheet="Sheet1", months_back=6):
    """
    Filters an Excel file to keep only specific columns AND narrows down the rows
    to a subset of clients provided in a separate Excel file.

    It then removes clients whose ART Start Date is earlier than `months_back` months
    ago (or is missing/unreadable). Those removed rows are written to `review_file`
    (CSV) with a 'Review Reason' column so they can be checked.
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
        ART_START_COL,
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

        if ART_START_COL not in df_source.columns:
            print(f"Error: '{ART_START_COL}' column missing from source file. Cannot apply date filter.")
            return

        if not existing_cols:
            print("Error: None of the target columns were found in the source file.")
            return

        # 4. Filter rows based on the NDR Patient Identifier subset
        print("Filtering rows matching the subset client list...")
        # Ensure comparison is done as stripped strings to handle formatting mismatches
        df_source["_match_id"] = df_source["NDR Patient Identifier"].astype(str).str.split('.').str[0].str.strip()

        df_filtered_rows = df_source[df_source["_match_id"].isin(valid_identifiers)].copy()
        print(f"Rows matched: {len(df_filtered_rows)} out of {len(df_source)} total source rows.")

        if df_filtered_rows.empty:
            print("Warning: No matching clients were found between the subset list and the source file.")

        # 5. Filter by ART Start Date: keep only clients who started within the last `months_back` months
        cutoff_date = pd.Timestamp.today().normalize() - pd.DateOffset(months=months_back)
        print(f"Applying ART start date filter: keeping clients who started on or after {cutoff_date.date()}...")

        art_start = pd.to_datetime(df_filtered_rows[ART_START_COL], errors="coerce")
        is_missing = art_start.isna()
        is_too_old = art_start < cutoff_date
        is_recent = ~is_missing & ~is_too_old

        df_keep = df_filtered_rows[is_recent]

        df_review = df_filtered_rows[~is_recent].copy()
        df_review["Review Reason"] = ""
        df_review.loc[is_too_old[~is_recent], "Review Reason"] = f"ART start date before {cutoff_date.date()}"
        df_review.loc[is_missing[~is_recent], "Review Reason"] = "ART start date missing or unreadable"

        print(f"Kept (started within last {months_back} months): {len(df_keep)}")
        print(f"Removed for review: {len(df_review)} "
              f"({int(is_too_old.sum())} older than cutoff, {int(is_missing.sum())} missing/unreadable date)")

        # 6. Save the kept clients (target columns only) to Excel
        df_keep[existing_cols].to_excel(output_file, index=False)
        print(f"Successfully saved filtered data to: {output_file}")

        # 7. Save the removed clients to CSV for review
        df_review[existing_cols + ["Review Reason"]].to_csv(review_file, index=False)
        print(f"Saved records for review to: {review_file}")

    except Exception as e:
        print(f"An error occurred: {e}")


# --- CONFIGURATION ---
INPUT_FILENAME = "Radet_all.xlsx"
SUBSET_FILENAME = "target_clients.xlsx"  # <-- Change this to your client list file name
OUTPUT_FILENAME = "test_data.xlsx"
REVIEW_FILENAME = "art_start_review.csv"  # <-- Clients removed by the ART start date filter
MONTHS_BACK = 6

if __name__ == "__main__":
    extract_specific_columns(
        input_file=INPUT_FILENAME,
        subset_file=SUBSET_FILENAME,
        output_file=OUTPUT_FILENAME,
        review_file=REVIEW_FILENAME,
        source_sheet=0,
        subset_sheet="Sheet1",  # Explicitly targeting Sheet1
        months_back=MONTHS_BACK
    )
