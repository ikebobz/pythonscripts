import pandas as pd
import xml.etree.ElementTree as ET
import os
import random
import uuid
from datetime import datetime


def format_date_only(value):
    if pd.isna(value):
        return ''

    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d')

    text = str(value).strip()
    if not text or text.lower() == 'nat':
        return ''

    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%m/%d/%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(text, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue

    try:
        return pd.to_datetime(text).strftime('%Y-%m-%d')
    except Exception:
        return text

def generate_ndr_xmls_final(source_file, output_folder='generated_xmls'):
    # 1. Load Mapping Files
    try:
        util_df = pd.read_csv('utility_map.csv')
        util_df['code_description'] = util_df['code_description'].str.strip()
        state_dict = util_df[util_df['code_set_nm'] == 'STATES'].set_index('code_description')['code'].to_dict()

        reg_df = pd.read_csv('regimen_map_revised.csv')
        reg_df['description'] = reg_df['description'].str.strip()
        regimen_code_dict = reg_df.set_index('description')['code'].to_dict()
        regimen_desc_dict = reg_df.set_index('description')['code_description'].to_dict()
    except Exception as e:
        print(f"Error loading mapping files: {e}")
        return

    # 2. Load Source Data
    df = pd.read_excel(source_file)

    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    missing_regimen_log_path = os.path.join(output_folder, 'skipped_missing_regimen_code.txt')
    skipped_missing_regimen = []

    # Initialize MessageUniqueID
    current_message_id = random.randint(101, 999)
    # Current timestamp for MessageCreationDateTime
    run_timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

    print(f"Starting generation. First MessageUniqueID: {current_message_id}")

    for index, row in df.iterrows():
        try:
            # --- Transformations ---
            facility_name = str(row.get('Facility Name', ''))
            facility_id = str(row.get('DatimId', ''))
            patient_id = str(row.get('NDR Patient Identifier', ''))
            dob = format_date_only(row.get('Date of Birth (yyyy-mm-dd)', ''))
            
            sex_raw = str(row.get('Sex', ''))
            sex = sex_raw[0].upper() if sex_raw else ''
            
            state_val = str(row.get('State', '')).strip()
            state_code = state_dict.get(state_val, '')

            last_pickup = format_date_only(row.get('Last Pickup Date (yyyy-mm-dd)', ''))
            regimen_start = format_date_only(row.get('Date of Start of Current ART Regimen', ''))
            
            months_refill = row.get('Months of ARV Refill', 0)
            try:
                m_val = int(float(months_refill))
                duration = m_val * 30
                mmd_type = f"MMD{m_val}" # Prefixed
                if m_val == 6:
                    mmd_type = "MMD2"
                if m_val == 4:
                    mmd_type = "MMD3"
            except:
                duration, mmd_type = 0, ""

            raw_reg = str(row.get('Current ART Regimen', '')).strip()
            reg_code = regimen_code_dict.get(raw_reg, '')
            reg_desc = regimen_desc_dict.get(raw_reg, '')

            if not reg_code:
                skipped_missing_regimen.append((patient_id, raw_reg))
                print(f"Skipped row {index}: empty regimen code for PatientIdentifier={patient_id}")
                continue

            # --- XML Construction ---
            root = ET.Element("Container")
            
            # Message Header
            header = ET.SubElement(root, "MessageHeader")
            ET.SubElement(header, "MessageStatusCode").text = "UPDATED"
            ET.SubElement(header, "MessageCreationDateTime").text = run_timestamp
            ET.SubElement(header, "MessageSchemaVersion").text = "1.6"
            ET.SubElement(header, "MessageUniqueID").text = str(current_message_id)
            
            sending_org = ET.SubElement(header, "MessageSendingOrganization")
            ET.SubElement(sending_org, "FacilityName").text = facility_name
            ET.SubElement(sending_org, "FacilityID").text = facility_id
            ET.SubElement(sending_org, "FacilityTypeCode").text = "FAC"
            
            # Individual Report
            report = ET.SubElement(root, "IndividualReport")
            demographics = ET.SubElement(report, "PatientDemographics")
            ET.SubElement(demographics, "PatientIdentifier").text = patient_id
            
            t_fac = ET.SubElement(demographics, "TreatmentFacility")
            ET.SubElement(t_fac, "FacilityName").text = facility_name
            ET.SubElement(t_fac, "FacilityID").text = facility_id
            ET.SubElement(t_fac, "FacilityTypeCode").text = "FAC"
            
            ET.SubElement(demographics, "PatientDateOfBirth").text = dob
            ET.SubElement(demographics, "PatientSexCode").text = sex
            ET.SubElement(demographics, "StateOfNigeriaOriginCode").text = str(state_code)
            
            # Condition / Regimen
            condition = ET.SubElement(report, "Condition")
            ET.SubElement(condition, "ConditionCode").text = "86406008"
            program_area = ET.SubElement(condition, "ProgramArea")
            ET.SubElement(program_area, "ProgramAreaCode").text = "HIV"

            regimen = ET.SubElement(condition, "Regimen")
            ET.SubElement(regimen, "VisitID").text = str(uuid.uuid4())
            ET.SubElement(regimen, "VisitDate").text = last_pickup
            
            prescribed = ET.SubElement(regimen, "PrescribedRegimen")
            ET.SubElement(prescribed, "Code").text = reg_code
            ET.SubElement(prescribed, "CodeDescTxt").text = reg_desc
            
            ET.SubElement(regimen, "PrescribedRegimenTypeCode").text = "ART"
            ET.SubElement(regimen, "PrescribedRegimenDuration").text = str(duration)
            ET.SubElement(regimen, "PrescribedRegimenDispensedDate").text = last_pickup
            ET.SubElement(regimen, "DateRegimenStarted").text = regimen_start
            ET.SubElement(regimen, "MultiMonthDispensing").text = mmd_type

            # --- Save and Increment ---
            safe_id = "".join([c for c in patient_id if c.isalnum() or c in ('-', '_')]).rstrip()
            file_path = os.path.join(output_folder, f"{safe_id}.xml")
            
            tree = ET.ElementTree(root)
            try: ET.indent(tree, space="    ", level=0)
            except AttributeError: pass
            
            tree.write(file_path, encoding='UTF-8', xml_declaration=True)
            
            # Increment the ID for the next file
            current_message_id += 1

        except Exception as e:
            print(f"Error on row {index}: {e}")

    print(f"Done. Final MessageID used: {current_message_id - 1}")

    if skipped_missing_regimen:
        with open(missing_regimen_log_path, 'w', encoding='utf-8') as log_file:
            log_file.write("Skipped records with empty regimen code\n")
            log_file.write("PatientIdentifier | RawRegimen\n")
            log_file.write("-" * 60 + "\n")
            for pid, raw_reg in skipped_missing_regimen:
                log_file.write(f"{pid} | {raw_reg}\n")
        print(
            f"Logged {len(skipped_missing_regimen)} skipped record(s) to: "
            f"{missing_regimen_log_path}"
        )

generate_ndr_xmls_final('test_data.xlsx')