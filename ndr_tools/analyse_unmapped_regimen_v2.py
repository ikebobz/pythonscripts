from pathlib import Path
import csv
from collections import defaultdict

input_file = Path(r"generated_xmls//skipped_missing_regimen_code.txt")
facility_file = Path("facility_datim.csv")
output_file = Path("regimen_count_by_datimID.csv")

with input_file.open("r", encoding="utf-8") as f:
    lines = f.readlines()[3:]  # start from the 4th line

records = []
for raw_line in lines:
    line = raw_line.strip()
    if not line or "|" not in line:
        continue

    datimID = line[:11]
    regimen = line.split("|", 1)[1].strip()

    # Find the first underscore to extract the 36-character Person ID
    if "_" in line:
        start_idx = line.find("_") + 1
        person_id = line[start_idx : start_idx + 36]
    else:
        person_id = "UNKNOWN_ID"

    records.append((datimID, person_id, regimen))

# Group data to find unique rows and count occurrences of each specific issue
# Key: (datimID, person_id, regimen) -> Value: count of occurrences
vertical_counts = defaultdict(int)
for datimID, person_id, regimen in records:
    vertical_counts[(datimID, person_id, regimen)] += 1

facility_names = {}
with facility_file.open("r", encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        code = row["code"].strip()
        name = row["name"].strip()
        facility_names[code] = name

# Write the vertically stacked data
with output_file.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["datimID", "facility_name", "person_id", "regimen", "occurrence_count"])
    
    # Sort by datimID, then person_id, then regimen for a clean, structured file
    for (datimID, person_id, regimen) in sorted(vertical_counts.keys()):
        facility_name = facility_names.get(datimID, "")
        count = vertical_counts[(datimID, person_id, regimen)]
        
        writer.writerow([
            datimID, 
            facility_name, 
            person_id, 
            regimen, 
            count
        ])

print(f"Saved: {output_file}")