import pandas as pd
import os
from datetime import datetime

# Replace these with the paths to your actual Excel files
excel_a_path = r'C:\Users\Admin\Documents\flags\Central Sync_LAMISPLUS data gap.xlsx'
excel_b_path = r'C:\Users\Admin\Documents\radet\Lagos Combined RADET 14022025.xlsx'

# Read Excel A (the list of values to lookup)
df_a = pd.read_excel(excel_a_path, sheet_name='Central sync not on LAMISPLUS')
lookup_values = df_a['Patient ID'] 
# keep only rows where current status is Active or Active Restart, then get Patient ID values
df_a = df_a[df_a['Current ART Status'].isin(['Active', 'Active Restart'])]
lookup_values = df_a['Patient ID']

# Read Excel B (with columns 'patient Id' and 'hospital_number')
df_b = pd.read_excel(excel_b_path)

# Create a lookup dictionary from Excel B
lookup_dict = pd.Series(list(zip(df_b['Hospital Number'], df_b['Facility Name'])),
                        index=df_b['Patient ID']).to_dict()

# Lookup and print results
# Build an output dataframe with Patient ID + Hospital Number + Facility Name
df_out = df_a[['Patient ID']].drop_duplicates().merge(
    df_b[['Patient ID', 'Hospital Number', 'Facility Name']],
    on='Patient ID',
    how='left'
)

# Fill missing lookups and set Patient ID as the key (index)
df_out[['Hospital Number', 'Facility Name']] = df_out[['Hospital Number', 'Facility Name']].fillna('Not Found')
df_out = df_out.set_index('Patient ID')

# choose output directory: use common dir if both inputs share the same directory, otherwise use excel_a_path's directory
dir_a = os.path.dirname(excel_a_path)
dir_b = os.path.dirname(excel_b_path)
out_dir = dir_a if dir_a == dir_b else dir_a

# build output filename with timestamp to avoid accidental overwrite
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
output_path = os.path.join(out_dir, f'lookup_results_{timestamp}.xlsx')

# reset index so Patient ID becomes a column in the Excel file
df_out.reset_index().to_excel(output_path, index=False)

print(f"Saved output to: {output_path}")

