# PostgreSQL adapter
pip install psycopg2-binary 

# Data manipulation and Excel export
pip install pandas openpyxl


import psycopg2
import pandas as pd
from datetime import datetime
import os

# --- Configuration ---
# 1. REPLACE THESE WITH YOUR ACTUAL DATABASE CREDENTIALS
DB_CONFIG = {
    "host": "localhost", # Or your Postgres server IP/hostname
    "database": "lamisplux", 
    "user": "your_username",      # e.g., "postgres"
    "password": "your_password",  # Your actual password
    "port": "5432"               # Default PostgreSQL port
}

# 2. SQL Query to be executed
SQL_QUERY = """
    SELECT 
        DISTINCT first_name || ' ' || surname AS names, 
        enrollment_date, 
        recapture 
    FROM 
        biometric b
    INNER JOIN 
        patient_person p ON p.uuid = b.person_uuid 
    WHERE 
        b.enrollment_date > '2025-06-30'
    ORDER BY 
        names, 
        enrollment_date ASC;
"""

def export_query_to_excel(db_config, sql_query):
    """
    Connects to PostgreSQL, executes a query, and exports results to an Excel file.
    """
    conn = None
    
    # Generate a unique filename with a timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"lamisplux_biometric_export_{timestamp}.xlsx"
    
    try:
        # Establish connection
        print("Connecting to the PostgreSQL database...")
        conn = psycopg2.connect(**db_config)
        
        # Use pandas to execute the query and fetch data into a DataFrame
        print("Executing SQL query and fetching data...")
        df = pd.read_sql_query(sql_query, conn)
        
        # Export the DataFrame to an Excel file
        print(f"Exporting {len(df)} records to Excel file: {output_filename}")
        df.to_excel(output_filename, index=False, sheet_name='BiometricData')
        
        print("\n✅ Success!")
        print(f"File saved successfully at: {os.path.abspath(output_filename)}")

    except psycopg2.Error as e:
        print(f"\n❌ Database Error: {e}")
        print("Please check your DB_CONFIG credentials and ensure PostgreSQL is running.")
        
    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {e}")
        
    finally:
        # Close the connection
        if conn:
            conn.close()
            print("Connection closed.")

if __name__ == "__main__":
    export_query_to_excel(DB_CONFIG, SQL_QUERY)