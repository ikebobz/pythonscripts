import pandas as pd

def generate_tb_backlog_sql(csv_file_path, output_sql_path):
    # Load the CSV data
    # Note: Using the exact column names found in your file
    df = pd.read_csv(csv_file_path)
    
    # Filter out rows with missing IDs or Dates
    df = df.dropna(subset=['Patient ID', 'Last Pickup Date (yyyy-mm-dd)'])
    
    # Constants based on your template
    OBS_TYPE = 'Chronic Care'
    OBS_DATA = '{"tbIptScreening": {"status": "No signs or symptoms of TB", "tbScreeningType": "Symptom screen (alone)"}}'
    FACILITY_ID = 1759
    CREATOR = 'inject_backlogs'
    
    rows = []
    for _, row in df.iterrows():
        p_id = row['Patient ID']
        p_date = row['Last Pickup Date (yyyy-mm-dd)']
        
        # Construct the tuple string for the VALUES clause
        # Format: (uuid, type, date, data, facility, creator, created_at, modifier, modified_at)
        sql_row = (
            f"('{p_id}', '{OBS_TYPE}', '{p_date}', '{OBS_DATA}', "
            f"{FACILITY_ID}, '{CREATOR}', '{p_date}', '{CREATOR}', '{p_date}')"
        )
        rows.append(sql_row)
    
    # Join all rows with a comma and newline
    values_clause = ",\n        ".join(rows)
    
    # Full SQL Template
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
    gen_random_uuid()
FROM tb_backlog;"""

    # Write to the output file
    with open(output_sql_path, 'w') as f:
        f.write(sql_query)
    
    print(f"SQL query successfully generated and saved to {output_sql_path}")

# Run the generator
generate_tb_backlog_sql('NIMR TB Screening Gap.xlsx - NIMR TB Screening Gap.csv', 'generated_tb_backlog.sql')