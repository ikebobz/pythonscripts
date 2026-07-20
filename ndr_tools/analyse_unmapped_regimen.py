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
    records.append((datimID, regimen))

counts = defaultdict(int)
distinct_regimens = defaultdict(set)

for datimID, regimen in records:
    counts[datimID] += 1
    distinct_regimens[datimID].add(regimen)

facility_names = {}
with facility_file.open("r", encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        code = row["code"].strip()
        name = row["name"].strip()
        facility_names[code] = name

with output_file.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["datimID", "facility_name", "regimen_count", "distinct_regimen_list"])
    for datimID in sorted(counts):
        regimen_list = sorted(distinct_regimens[datimID])
        facility_name = facility_names.get(datimID, "")
        writer.writerow([datimID, facility_name, counts[datimID], "; ".join(regimen_list)])

print(f"Saved: {output_file}")