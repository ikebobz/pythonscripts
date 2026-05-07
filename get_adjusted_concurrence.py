import pandas as pd

# 1. Load the datasets
df_regimen = pd.read_csv('regimen_counts_by_facility.csv')
df_active = pd.read_excel('facility_active_comparison.xlsx')

# 2. Merge files on Facility Name
# 'how=left' keeps all records from the active comparison file
df_merged = pd.merge(
    df_active, 
    df_regimen[['Facility Name', 'Total Clients']], 
    on='Facility Name', 
    how='left'
)

# 3. Handle missing data
# If a facility has no matching data in the first sheet, equate Total Clients to 0
df_merged['Total Clients'] = df_merged['Total Clients'].fillna(0)

# 4. Calculate Concurrence Metrics
# Original Concurrence = NDR / RADET
df_merged['original concurrence'] = df_merged['Active On NDR'] / df_merged['Active on RADET']

# Adjusted Concurrence = NDR / (RADET - Total Clients)
df_merged['adjusted concurrence'] = (
    df_merged['Active On NDR'] / (df_merged['Active on RADET'] - df_merged['Total Clients'])
)

# 5. Prepare final selection of columns
final_report = df_merged[[
    'Facility Name',
    'Active On NDR',
    'Active on RADET',
    'original concurrence',
    'adjusted concurrence'
]]

# 6. Output to Excel
final_report.to_excel('concurrence_adjustment_report_v2.xlsx', index=False)

print("Report generated: concurrence_adjustment_report_v2.xlsx")