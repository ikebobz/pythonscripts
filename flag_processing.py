import pandas as pd

# ==========================
# CONFIGURATION
# ==========================
input_file = r'C:\Users\Admin\Documents\radet\Nigerian Institute of Medical Research (NIMR) Radet 2026-08-21.xlsx'    # Replace with your file
output_file = "ART_Date_Validation_Report.xlsx"

# ==========================
# READ DATA
# ==========================
df = pd.read_excel(input_file)

# ==========================
# DATE COLUMNS
# ==========================
art_start_col = "ART Start Date (yyyy-mm-dd)"

check_columns = {
    "Current ART Regimen Date Before ART Start":
        "Date of Start of Current ART Regimen",

    "VL Sample Collection Before ART Start":
        "Date of Viral Load Sample Collection (yyyy-mm-dd)",

    "VL Result Sample Date Before ART Start":
        "Date of Current ViralLoad Result Sample (yyyy-mm-dd)",

    "Current VL Date Before ART Start":
        "Date of Current Viral Load (yyyy-mm-dd)"
}

# Convert ART Start Date
df[art_start_col] = pd.to_datetime(
    df[art_start_col],
    errors="coerce"
)

# Convert validation columns to dates
for col in check_columns.values():
    df[col] = pd.to_datetime(
        df[col],
        errors="coerce"
    )

# ==========================
# FILTER TO ACTIVE CLIENTS
# ==========================
active_statuses = ["Active", "Active Restart"]

df_filtered = df[
    df["Current ART Status"].isin(active_statuses)
].copy()

# ==========================
# RUN VALIDATIONS
# ==========================
results = []

with pd.ExcelWriter(output_file, engine="openpyxl") as writer:

    for rule_name, col in check_columns.items():

        invalid_records = df_filtered[
            df_filtered[col].notna() &
            df_filtered[art_start_col].notna() &
            (df_filtered[col] < df_filtered[art_start_col])
        ].copy()

        count = len(invalid_records)

        results.append({
            "Validation Rule": rule_name,
            "Records Found": count
        })

        # Export only if violations exist
        if count > 0:
            sheet_name = f"Rule_{len(results)}"
            invalid_records.to_excel(
                writer,
                sheet_name=sheet_name,
                index=False
            )

    summary_df = pd.DataFrame(results)
    summary_df.to_excel(
        writer,
        sheet_name="Summary",
        index=False
    )



# ==========================
# DISPLAY RESULTS
# ==========================
print("\nART DATE VALIDATION SUMMARY")
print("=" * 50)
print(summary_df.to_string(index=False))
print(f"\nDetailed report saved to: {output_file}")