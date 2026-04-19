import pandas as pd
import re

class DataQualityValidator:
    def __init__(self, excel_file, rules_file):
        self.df = pd.read_excel(excel_file)
        self.rules = self.load_rules(rules_file)
        self.df.columns = [clean_column_name(col) for col in self.df.columns]


    def load_rules(self, rules_file):
        rules = []
        with open(rules_file, 'r') as file:
            for line in file:
                rule = line.strip()
                if rule and not rule.startswith('#'):
                    rules.append(rule)
        return rules

    def validate(self):
        results = []
        for rule in self.rules:
            # Replace 'Null' or 'Blank' with pd.NA for proper checking
            rule_eval = rule.replace('Null', 'pd.NA').replace('Blank', 'pd.NA')
            

            try:
                for index, row in self.df.iterrows():
                    local_vars = {col: row[col] for col in self.df.columns}
                    condition_passed = eval(rule_eval, {"pd": pd}, local_vars)
                    if not condition_passed:
                        results.append((index, rule))
            except Exception as e:
                results.append((None, f"{rule} with data {str(e)}"))
        return results
    
    def find_rows_matching_rules(self):
        matches = []
        for rule in self.rules:
            # Replace 'Null'/'Blank' with pd.NA for compatibility if needed
            rule_eval = rule.replace('Null', 'pd.NA').replace('Blank', 'pd.NA')
            try:
                row_indices = []
                for index, row in self.df.iterrows():
                    local_vars = {col: row[col] for col in self.df.columns}
                    if eval(rule_eval, {"pd": pd}, local_vars):
                        row_indices.append(index + 1)
                matches.append((rule, row_indices))
            except Exception as e:
                matches.append((rule, f"Error evaluating rule: {str(e)}"))
        return matches

# Usage example:


# Replace spaces with underscores and remove invalid characters for Python identifiers
def clean_column_name(col_name):
    col_name = col_name.replace(' ', '_')
    # Remove characters other than letters, numbers, and underscores
    col_name = re.sub(r'[^a-zA-Z0-9_]', '', col_name)
    return col_name



checker = DataQualityValidator(r'C:\Users\Admin\Documents\radet\radet_test.xlsx', 'validation_rules.txt')
matches = checker.find_rows_matching_rules()
for rule, rows in matches:
     print(f"Rule: {rule}")
     if isinstance(rows, list):
         print(f"Matched rows: {rows}")
     else:
         print(f"{rows}")
