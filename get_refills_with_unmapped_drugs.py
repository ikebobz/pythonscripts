import pandas as pd

# Load the RADET dataset
file_path = r'C:\Users\Admin\Downloads\projects\python\pythonscripts\Radet_all.xlsx'
df = pd.read_excel(file_path, sheet_name='Sheet1')

# List of target regimens to filter
# (Stripping spaces ensures exact matches even with hidden formatting)
target_regimens = [
    "ABC(600mg)+3TC(300mg)+LPV/r(200/50mg)+DTG(50mg)",
    "ABC(120mg)/3TC(60mg)+DTG(50mg)",
    "Dolutegravir(5mg)",
    "Abacavir(120mg)+Lamivudine(60mg)",
    "Abacavir(60mg)+Lamivudine(30mg)"
]

# Clean the 'Current ART Regimen' column by stripping whitespace
df['Current ART Regimen'] = df['Current ART Regimen'].str.strip()

# Filter the dataframe for the specific regimens
filtered_df = df[df['Current ART Regimen'].isin(target_regimens)]

# Group by 'Facility Name' and 'Current ART Regimen' to get the counts
regimen_counts = filtered_df.groupby(['Facility Name', 'Current ART Regimen']).size().unstack(fill_value=0)

# Add a 'Total' column for each facility
regimen_counts['Total Clients'] = regimen_counts.sum(axis=1)

# Sort by total count descending
regimen_counts = regimen_counts.sort_values(by='Total Clients', ascending=False)

# Display the results
print(regimen_counts)

# Save the result to a CSV file
regimen_counts.to_csv('regimen_counts_by_facility.csv')