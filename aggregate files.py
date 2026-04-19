import pandas as pd
import glob
import os
import sys

def combine_csv_files(input_directory, output_filename, include_filename_column=True):
    """
    Combines all CSV files in the specified directory into a single output file.

    It assumes all CSV files share the same header structure.

    Args:
        input_directory (str): The path to the directory containing the CSV files.
        output_filename (str): The name of the resulting combined CSV file.
        include_filename_column (bool): If True, adds a column indicating
                                        the source filename for each row.
    """
    # 1. Define the search pattern for CSV files
    search_pattern = os.path.join(input_directory, '*.csv')
    all_csv_files = glob.glob(search_pattern)

    if not all_csv_files:
        print(f"Error: No CSV files found in the directory: {input_directory}")
        return

    print(f"Found {len(all_csv_files)} CSV files to combine.")

    # 2. List to hold individual DataFrames
    all_data = []

    # 3. Iterate through all found CSV files
    for filename in all_csv_files:
        try:
            # Read the CSV file into a DataFrame
            # Using low_memory=False to prevent potential mixed-type warnings
            df = pd.read_csv(filename, low_memory=False)

            if include_filename_column:
                # Get just the base filename (e.g., 'data_01.csv')
                base_filename = os.path.basename(filename)
                # Add a column indicating the source file
                df['source_filename'] = base_filename

            # Add the DataFrame to our list
            all_data.append(df)
            print(f"Successfully processed: {base_filename}")

        except pd.errors.EmptyDataError:
            print(f"Warning: Skipping empty file: {filename}")
        except Exception as e:
            print(f"Error reading file {filename}: {e}")
            # Optionally, continue to the next file instead of stopping
            continue

    if not all_data:
        print("No data frames were successfully processed. Exiting.")
        return

    # 4. Concatenate all DataFrames in the list
    print("\nConcatenating data...")
    combined_df = pd.concat(all_data, ignore_index=True)

    # 5. Save the combined DataFrame to the output CSV file
    try:
        combined_df.to_csv(output_filename, index=False)
        print(f"\nSuccess! All data combined and saved to: {output_filename}")
        print(f"Total rows written: {len(combined_df)}")
    except Exception as e:
        print(f"Error writing output file {output_filename}: {e}")

# --- Main execution block ---
if __name__ == "__main__":
    # IMPORTANT: Change these variables to match your file structure!
    # -------------------------------------------------------------------
    
    # 1. Set the directory where your CSV files are located.
    #    Example: If your files are in a 'data' folder next to the script, use 'data'
    #    Example: If they are in the same folder as the script, use '.'
    INPUT_DIR = r'C:\Users\Admin\Documents\others\patient_details' # <--- ADJUST THIS PATH
    
    # 2. Set the name for the final combined file.
    OUTPUT_FILE = r'C:\Users\Admin\Documents\others\patient_details\combined_master_report.csv' # <--- ADJUST THIS FILENAME
    
    # 3. Set to False if you don't want an extra column showing the source file name.
    ADD_SOURCE_COLUMN = False
    
    # -------------------------------------------------------------------

    # Create a dummy folder and files for testing if they don't exist
    if not os.path.exists(INPUT_DIR):
        print(f"Creating dummy directory: {INPUT_DIR}")
        os.makedirs(INPUT_DIR)
        
        # Create 3 dummy CSV files
        pd.DataFrame({
            'ID': [1, 2],
            'Value': ['A', 'B'],
            'Quantity': [100, 200]
        }).to_csv(os.path.join(INPUT_DIR, 'file_jan.csv'), index=False)

        pd.DataFrame({
            'ID': [3, 4, 5],
            'Value': ['C', 'D', 'E'],
            'Quantity': [300, 400, 500]
        }).to_csv(os.path.join(INPUT_DIR, 'file_feb.csv'), index=False)
        
        pd.DataFrame({
            'ID': [6],
            'Value': ['F'],
            'Quantity': [600]
        }).to_csv(os.path.join(INPUT_DIR, 'file_mar.csv'), index=False)

        print("Created three dummy files (file_jan.csv, file_feb.csv, file_mar.csv) for testing.")
        print("You can now run the script or replace these with your actual files.")
        
    combine_csv_files(
        input_directory=INPUT_DIR,
        output_filename=OUTPUT_FILE,
        include_filename_column=ADD_SOURCE_COLUMN
    )
