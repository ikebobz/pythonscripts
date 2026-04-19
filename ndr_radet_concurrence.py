import pandas as pd

def _read_csv_safely(csv_path: str) -> pd.DataFrame:
    bad_lines = []

    def _capture_bad_line(line):
        bad_lines.append(line)
        return None  # skip malformed line

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

def perform_active_client_analysis(csv_path, excel_path):
    # 1. Load the first file (CSV)
    df_csv = _read_csv_safely(csv_path)
    
    # 2. Load the second file (Excel .xlsx)
    df_xlsx = pd.read_excel(excel_path)
    
    # 3. Identify active clients in the first file
    active_csv_df = df_csv[df_csv['Current Status (28 Days)'] == 'Active'].copy()
    active_csv_ids = set(active_csv_df['Patient Identifier'])

    # 4. Filter active clients in the second file
    active_xlsx_mask = df_xlsx['Current ART Status'].isin(['Active', 'Active Restart'])
    df_xlsx_active = df_xlsx[active_xlsx_mask].copy()
    
    # 5. Find clients active in File 2 but NOT active in File 1
    discrepancy_df = df_xlsx_active[~df_xlsx_active['NDR Patient Identifier'].isin(active_csv_ids)]
    
    # 6. Select relevant details
    final_list = discrepancy_df[['Facility Name', 'Patient ID']].sort_values(by='Facility Name')

    # 7. Export final_list to Excel
    output_file = "active_in_radet_not_in_linelist.xlsx"
    final_list.to_excel(output_file, index=False)
    print(f"Detailed output saved to: {output_file}")
    
    # Print summary
    summary = final_list.groupby('Facility Name')['Patient ID'].count()
    print("--- Summary of Discrepancies by Facility ---")
    print(summary)

# Configuration
csv_file = 'lagos_line_list.csv'
xlsx_file = 'Radet_all.xlsx'

# Run the analysis
perform_active_client_analysis(csv_file, xlsx_file)