# -*- coding: utf-8 -*-
"""
Created on Tue Oct 24 20:41:13 2023

@author: HI LEAD
"""

# importing the requests library
import requests
import psycopg
import pandas as pd
import json
import datetime as dt
import numpy as np

# defining the api-endpoint
API_ENDPOINT = "http://localhost:8000/data/api/patients/"
VISIT_ENDPOINT = "http://localhost:8000/data/api/visits/"
REFILL_ENDPOINT = "http://localhost:8000/data/api/refills/"

# your API key here
API_KEY = ''
data = []

TOKEN_URL = 'http://localhost:8000/api-token-auth/'
PARAMS = {'password': 'vendetta','rememberMe': True,  'username': 'hilead'  }
r1 = requests.post(TOKEN_URL, data=PARAMS)
#print(r1)
d = json.loads(r1.text)
print(d['token'])
head = {
        'Authorization': 'Token {}'.format(d['token']),
        'Content-Type':'application/json',
        'Accept':'application/json'
    }
def formatDate(date):
    #print(date)
    if  date == None:
        #print(date)
        return None
    else:
        return str(date)
        '''time_str = json.dumps(date.strftime('%Y-%m-%d  %H:%M:%S'))
        #print(time_str)
        return time_str'''

def writeCallResponse(table=None,res=None,eventtime=None,description=None):
    data = {
            "table":table,
             "state":res,
             "description":description,
             "event time":eventtime.strftime('%Y-%m-%d %H:%M:%S')
           }
    with open('log.json','a',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=4)
    

def callEndPoint(api_url,data,table=None):
    set = {'data':data}
    # sending post request and saving response as response object
    res = requests.post(url=api_url,json = set,headers=head)

    # extracting response text
    response_url = res.text
    print("The response from server is:%s" % response_url)
    writeCallResponse(table=table,res=response_url,eventtime=dt.datetime.now())


def uploadPatients(conn):
    data = []
    sql = 'select * from patient_person'
    df = pd.read_sql(sql,conn)
    for index,row in df.iterrows():
        data.append({
                   'first_name':row['first_name'],
                   'surname':row['surname'],
                   'other_name':row['other_name'],
                   'contact_point':row['contact_point'],
                   'address':row['address'],
                   'gender':row['identifier'],
                   'deceased':row['deceased'],
                   'marital_status':row['marital_status'],
                   'employment_status':row['employment_status'],
                   'organization':row['organization'],
                   'contact':row['contact'],
                   'date_of_birth':formatDate(row['date_of_birth']),
                   'date_of_registration':formatDate(row['date_of_registration']),
                   'facility_id':row['facility_id'],
                   'sex':row['sex'],
                   'hospital_number':row['hospital_number'],
                   'full_name':row['full_name'],
                   'active':row['active'],
                   'uuid':row['uuid']
                   })
    conn.commit()
    callEndPoint(API_ENDPOINT, data,table = 'patient')
def uploadVisits(conn):
    data = []
    sql = 'select * from patient_visit'
    df = pd.read_sql(sql,conn)
    for index,row in df.iterrows():
        #print(str(row['visit_end_date']))
        data.append({
                    'facility_id':row['facility_id'],
                    'archived':row['archived'],
                    'visit_start_date':formatDate(row['visit_start_date']),
                    'visit_end_date':formatDate(row['visit_end_date']),
                    'person_uuid':row['person_uuid'],
                    'uuid':row['uuid']
                    })
    conn.commit()
    callEndPoint(VISIT_ENDPOINT, data,table='visit')
def uploadPharmacies(conn):
    data = []
    sql = 'select * from hiv_art_pharmacy'
    with conn.cursor() as cur:
            cur.execute(sql)
            cur.fetchone()
            for index,row in cur:
                data.append(
                    {
                    'facility_id':row['facility_id'],
                    'archived':row['archived'],
                    'visit_date':formatDate(row['visit_date']),
                    'visit_id':row['visit_id'],
                    'person_uuid':row['person_uuid'],
                    'ard_screened':row['ard_screened'],
                    'prescription_error':row['prescription_error'],
                    'adherence':row['adherence'],
                    'mmd_type':row['mmd_type'],
                    'extra':row['extra'],
                    'next_appointment':formatDate(row['next_appointment']),
                    'refill_period':row['refill_period'],
                    'is_devolve':row['is_devolve'],
                    'adverse_drug_reactions':row['adverse_drug_reactions'],
                    'dsd_model_type':row['dsd_model_type'],
                    'refill':row['refill'],
                    'refill_type':row['refill_type'],
                    'delivery_point':row['delivery_point'],
                    'ipt':row['ipt'],
                    'ipt_type':row['ipt_type'],
                    'visit_type':row['visit_type'],
                    'dsd_model':row['dsd_model'],
                    'uuid':row['uuid']
                    }
                   )
    conn.commit()
    print('executing api call......')
    print(dt.datetime.now())
    callEndPoint(REFILL_ENDPOINT,data,table='pharmacy')
    
    
with psycopg.connect("dbname = lp_ejire user=postgres password=lamis") as conn:
    #uploadPatients(conn)
    #uploadVisits(conn)
    uploadPharmacies(conn)      
