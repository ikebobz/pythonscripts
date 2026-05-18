import os
import pandas as pd
import shutil

# --- UTILITY FUNCTIONS ---
def _read_csv_safely(csv_path: str) -> pd.DataFrame:
    bad_lines = []
    def _capture_bad_line(line):
        bad_lines.append(line)
        return None
    
    df = pd.read_csv(
        csv_path,
        sep=",",
        quotechar='"',
        engine="python",
        on_bad_lines=_capture_bad_line,
    )
    if bad_lines:
        print(f"Warning: skipped {len(bad_lines)} malformed row(s) in {csv_path}.")
    return df

# --- STEP 1: REGIMEN FILTERING (Source 2) ---
def step_1_generate_regimen_counts(radet_path):
    print("\n[1/3] Filtering regimens and counting by facility...")
    df = pd.read_excel(radet_path, sheet_name='Sheet1')
    
    target_regimens = [
        "ABC(600mg)+3TC(300mg)+LPV/r(200/50mg)+DTG(50mg)",
        "ABC(120mg)/3TC(60mg)+DTG(50mg)",
        "Dolutegravir(5mg)",
        "Abacavir(120mg)+Lamivudine(60mg)",
        "Abacavir(60mg)+Lamivudine(30mg)"
    ]
    
    df['Current ART Regimen'] = df['Current ART Regimen'].str.strip()
    filtered_df = df[df['Current ART Regimen'].isin(target_regimens)]
    
    regimen_counts = filtered_df.groupby(['Facility Name', 'Current ART Regimen']).size().unstack(fill_value=0)
    regimen_counts['Total Clients'] = regimen_counts.sum(axis=1)
    
    output_csv = 'regimen_counts_by_facility.csv'
    regimen_counts.to_csv(output_csv)
    print(f"Success: {output_csv} generated.")
    return output_csv

# --- STEP 2: ACTIVE CLIENT ANALYSIS (Source 3) ---
def step_2_active_client_analysis(linelist_path, radet_path):
    print("\n[2/3] Analyzing active (RADET) vs Inactive (CSV) discrepancies...")
    
    # 1. Read files and ensure IDs are strings to avoid data type mismatches
    df_csv = _read_csv_safely(linelist_path)
    df_xlsx = pd.read_excel(radet_path)
    
    # Clean ID columns and Status columns (handling whitespace/case)
    df_csv['Patient Identifier'] = df_csv['Patient Identifier'].astype(str).str.strip()
    df_csv['Current Status (28 Days)'] = df_csv['Current Status (28 Days)'].str.strip()
    
    df_xlsx['NDR Patient Identifier'] = df_xlsx['NDR Patient Identifier'].astype(str).str.strip()
    df_xlsx['Current ART Status'] = df_xlsx['Current ART Status'].str.strip()

    # 2. Identify "Inactive" patients in the CSV Linelist
    # You can expand this list if "Inactive" has multiple variations (e.g., 'IIT', 'Dead')
    active_csv_df = df_csv[df_csv['Current Status (28 Days)'] == 'Active'].copy()
    inactive_csv_ids = set(df_csv[df_csv['Current Status (28 Days)'] == 'Inactive']['Patient Identifier'])
    
    # 3. Identify "Active" patients in the RADET
    active_xlsx_mask = df_xlsx['Current ART Status'].isin(['Active', 'Active Restart'])
    df_xlsx_active = df_xlsx[active_xlsx_mask].copy()
    
    # 4. CROSS-CHECK: Is the RADET Active ID found in the CSV Inactive Set?
    discrepancy_df = df_xlsx_active[df_xlsx_active['NDR Patient Identifier'].isin(inactive_csv_ids)]
    
    # 5. Export results
    final_list = discrepancy_df[['Facility Name', 'Patient ID', 'NDR Patient Identifier']].sort_values(by='Facility Name')
    
    output_file = "active_in_radet_not_in_linelist.xlsx"
    final_list.to_excel(output_file, index=False)
    print(f"Success: {output_file} generated. Found {len(final_list)} discrepancies.")
    
    
    file1_counts = (
        active_csv_df.groupby('DATIM Code', dropna=False)['Patient Identifier']
        .nunique()
        .reset_index(name='Number of actives in file1')
        .rename(columns={'DATIM Code': 'DatimId'})
    )

    file2_counts = (
        df_xlsx_active.groupby(['DatimId', 'Facility Name'], dropna=False)['NDR Patient Identifier']
        .nunique()
        .reset_index(name='Number of actives in file 2')
    )

    facility_summary = pd.merge(
        file1_counts,
        file2_counts,
        on='DatimId',
        how='outer'
    ).fillna(0)

    facility_summary['Number of actives in file1'] = facility_summary['Number of actives in file1'].astype(int)
    facility_summary['Number of actives in file 2'] = facility_summary['Number of actives in file 2'].astype(int)
    facility_summary['Difference (file2 - file1)'] = (
        facility_summary['Number of actives in file 2'] - facility_summary['Number of actives in file1']
    )

    # Rename columns
    facility_summary = facility_summary.rename(columns={
        'Number of actives in file1': 'Active On NDR',
        'Number of actives in file 2': 'Active on RADET',
        'Difference (file2 - file1)': 'Variance'
    })

    # Reorder and sort
    facility_summary = facility_summary[['DatimId', 'Facility Name', 'Active On NDR', 'Active on RADET', 'Variance']]
    facility_summary = facility_summary.sort_values(by='Facility Name')

    # 9. Export facility summary to second Excel file
    summary_output_file = "facility_active_comparison.xlsx"
    facility_summary.to_excel(summary_output_file, index=False)
    print(f"Facility summary saved to: {summary_output_file}")
    

# --- STEP 3: GET ADJUSTED CONCURRENCE (Source 1) ---
def step_3_get_adjusted_concurrence(active_comparison_path, regimen_counts_path):
    print("\n[3/3] Running Adjusted Concurrence report...")
    df_regimen = pd.read_csv(regimen_counts_path)
    df_active = pd.read_excel(active_comparison_path)

    df_merged = pd.merge(
        df_active, 
        df_regimen[['Facility Name', 'Total Clients']], 
        on='Facility Name', 
        how='left'
    )

    df_merged['Total Clients'] = df_merged['Total Clients'].fillna(0)
    
    # Handling potential division by zero for concurrence metrics
    df_merged['original concurrence'] = df_merged['Active On NDR'] / df_merged['Active on RADET']
    df_merged['adjusted concurrence'] = (
        df_merged['Active On NDR'] / (df_merged['Active on RADET'] - df_merged['Total Clients'])
    )

    final_report = df_merged[[
        'Facility Name', 'Active On NDR', 'Active on RADET', 
        'original concurrence', 'adjusted concurrence'
    ]]

    output_file = 'concurrence_adjustment_report_v2.xlsx'
    final_report.to_excel(output_file, index=False)
    print(f"Success: {output_file} generated.")

# --- MAIN EXECUTION BLOCK ---
if __name__ == "__main__":
    # Define your source file paths here
    RADET_FILE = r'C:\Users\Admin\Downloads\projects\python\pythonscripts\Radet_all.xlsx'
    LINELIST_FILE = r'C:\Users\Admin\Downloads\projects\python\pythonscripts\lagos_line_list.csv'
    FACILITY_COMPARISON_FILE = 'facility_active_comparison.xlsx' # Input required for Step 3

    try:
        # Step 1: Regimen Counts (Creates regimen_counts_by_facility.csv)
        regimen_csv = step_1_generate_regimen_counts(RADET_FILE)

        # Step 2: Active Discrepancy (Creates active_in_radet_not_in_linelist.xlsx)
        step_2_active_client_analysis(LINELIST_FILE, RADET_FILE)

        # Step 3: Final Concurrence (Requires outputs from Step 1 and an existing comparison file)
        step_3_get_adjusted_concurrence(FACILITY_COMPARISON_FILE, regimen_csv)
        
        print("\n--- All processes completed successfully ---")
        
    except FileNotFoundError as e:
        print(f"\nError: One of the required input files was not found: {e}")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")