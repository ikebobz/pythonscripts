import pyodbc
import pandas as pd

conn = pyodbc.connect("DSN=FMPAccess;UID=admin;PWD=vendetta1983")
df = pd.read_sql('SELECT p.PepID,p.FirstName,p.Surname,p.Sex,Address FROM "PepID" p inner join "Preassess" pr on pr.PepID=p.PepID where p.PepID like ?', conn, params=["NM-25%"])
print(pd.read_sql('SELECT "genderJson", "identierJson","employmentStatusJson","addressJson" FROM "PepID" FETCH FIRST 3 ROWS ONLY', conn))
#print(df.head())


