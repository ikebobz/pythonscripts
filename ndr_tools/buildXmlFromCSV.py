import pandas as pd
import xml.etree.ElementTree as ET
import os
import glob

def batch_update_and_filter_xmls(input_folder, csv_file_path, output_folder='corrected_xml'):
    # 1. Load and clean the update data from CSV
    try:
        updates_df = pd.read_csv(csv_file_path)
        updates_df['person_uuid'] = updates_df['person_uuid'].astype(str).str.strip()
        updates_df['visit_date'] = updates_df['visit_date'].astype(str).str.strip()
        updates_df['duration'] = updates_df['duration'].astype(str).str.strip()
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return

    # 2. Create output directory if it doesn't exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # 3. Get list of all XML files
    xml_files = glob.glob(os.path.join(input_folder, "*.xml"))
    if not xml_files:
        print(f"No XML files found in {input_folder}")
        return

    print(f"Scanning {len(xml_files)} files for updates...")
    updated_count = 0

    for xml_path in xml_files:
        file_name = os.path.basename(xml_path)
        tree = ET.parse(xml_path)
        root = tree.getroot()
        
        # This flag tracks if ANY change was made to the current file
        file_modified = False

        # 4. Check every update requirement against this specific file
        for _, row in updates_df.iterrows():
            target_uuid = row['person_uuid']
            target_visit_date = row['visit_date']
            new_duration = row['duration']

            reports = root.findall('.//IndividualReport')
            for report in reports:
                patient_id_elem = report.find('.//PatientIdentifier')
                
                if patient_id_elem is not None:
                    full_id = patient_id_elem.text.strip()
                    
                    # Match using the last 36 characters
                    if full_id[-36:] == target_uuid:
                        regimens = report.findall('.//Regimen')
                        for regimen in regimens:
                            vdate_elem = regimen.find('VisitDate')
                            
                            if vdate_elem is not None and vdate_elem.text.strip() == target_visit_date:
                                duration_elem = regimen.find('PrescribedRegimenDuration')
                                
                                if duration_elem is not None:
                                    # Only mark as modified if the value actually needs to change
                                    if duration_elem.text != new_duration:
                                        duration_elem.text = new_duration
                                        file_modified = True
                                else:
                                    # Create element if it didn't exist
                                    new_elem = ET.SubElement(regimen, 'PrescribedRegimenDuration')
                                    new_elem.text = new_duration
                                    file_modified = True

        # 5. Save ONLY if a modification occurred
        if file_modified:
            output_path = os.path.join(output_folder, file_name)
            tree.write(output_path, encoding='UTF-8', xml_declaration=True)
            updated_count += 1
            print(f"[UPDATED & COPIED] {file_name}")

    print(f"\nProcessing complete.")
    print(f"Total files checked: {len(xml_files)}")
    print(f"Total files updated and moved to '{output_folder}': {updated_count}")

# --- EXECUTION ---
# batch_update_and_filter_xmls('your_xml_folder', 'your_updates.csv')er containing the XMLs
batch_update_and_filter_xmls('treatmentxml_ajeromi', 'updates.csv')