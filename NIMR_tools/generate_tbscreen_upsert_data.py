import pandas as pd
import json

def generate_tb_backlog_sql_v2(csv_file_path, output_sql_path):
    # Load the CSV data
    df = pd.read_excel(csv_file_path)
    
    # Filter out rows with missing IDs or Dates
    df = df.dropna(subset=['Patient ID', 'Last Pickup Date (yyyy-mm-dd)'])
    
    # Requirement 1: Ensure date and not timestamps appear
    # Convert to datetime and then to a string format YYYY-MM-DD
    df['Last Pickup Date (yyyy-mm-dd)'] = pd.to_datetime(df['Last Pickup Date (yyyy-mm-dd)']).dt.strftime('%Y-%m-%d')
    
    # Constants
    OBS_TYPE = 'Chronic Care'
    FACILITY_ID = 1759
    CREATOR = 'inject_backlogs'
    
    rows = []
    for _, row in df.iterrows():
        p_id = row['Patient ID']
        p_date = row['Last Pickup Date (yyyy-mm-dd)'] # String in YYYY-MM-DD
        
        # Construct the JSON with the date key
        data_obj = {
            "tbIptScreening": {
                "status": "No signs or symptoms of TB",
                "tbScreeningType": "Symptom screen (alone)",
                "date": p_date # Guaranteed to be date-only string
            }
        }
        # Convert dict to JSON string and escape single quotes for SQL safety
        obs_data_json = json.dumps(data_obj).replace("'", "''")
        
        # Create the VALUES tuple
        sql_row = (
            f"('{p_id}', '{OBS_TYPE}', '{p_date}', '{obs_data_json}', "
            f"{FACILITY_ID}, '{CREATOR}', '{p_date}', '{CREATOR}', '{p_date}')"
        )
        rows.append(sql_row)
    
    values_clause = ",\n        ".join(rows)
    
    # Requirement 2: Include visit_id in the insert statement
    sql_query = f"""WITH tb_backlog AS (
    SELECT * FROM (VALUES 
        {values_clause}
    ) AS t(person_uuid, observation_type, observation_date, observation_data, facility_id, creator, created_at, modifier, modified_at)
)
INSERT INTO hiv_observation (
    person_uuid, 
    type, 
    date_of_observation, 
    data, 
    facility_id, 
    created_by, 
    created_date, 
    last_modified_by, 
    last_modified_date, 
    archived, 
    visit_id,
    uuid
)
SELECT 
    person_uuid, 
    observation_type, 
    CAST(observation_date AS DATE), 
    CAST(observation_data AS JSONB), 
    CAST(facility_id AS INTEGER), 
    creator, 
    CAST(created_at AS TIMESTAMP), 
    modifier, 
    CAST(modified_at AS TIMESTAMP), 
    0, 
    gen_random_uuid(),  -- visit_id
    gen_random_uuid()   -- uuid
FROM tb_backlog;"""

    with open(output_sql_path, 'w') as f:
        f.write(sql_query)
    
    print(f"SQL query successfully generated with visit_id and JSON dates: {output_sql_path}")

# Generate the file
generate_tb_backlog_sql_v2('source.xlsx', 'generated_tb_backlog_v2.sql')