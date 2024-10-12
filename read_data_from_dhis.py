# -*- coding: utf-8 -*-
"""
Created on Sun Sep 29 19:04:41 2024

@author: HI LEAD
"""

import requests,sys
import getpass as gp
import psycopg
import pandas as pd
import json
import datetime as dt
import numpy as np
import os


ccframe = pd.read_excel("category_combinations.xlsx",sheet_name="catcom")
ccframe.set_index('catcombo',inplace=True)
deframe = pd.read_excel("data_elements_new.xlsx",sheet_name="data_elements_new")
deframe.set_index('Name',inplace=True)
baseurl = 'http://192.168.8.101:8086/dhis2/api/'
URL = 'http://192.168.8.104:8086/dhis2/api/41/auth/login'
getvals = 'http://192.168.8.104:8086/dhis2/api/dataValueSets?dataSet=Qshvdw0brBq&period=2024W39&orgUnit=UzCw35LZV95'
postval = 'http://192.168.8.104:8086/dhis2/api/dataValues?de=Vjet5qO2d5N&pe=2024SunW40&ou=UzCw35LZV95&co=ShtKZwEQsyh&value=10'
getDset = '{}{}'.format(baseurl,'dataSets')
posttoDataSet = '{}{}'.format(baseurl,'dataValueSets')
orgUnitId = 'diPCa9GUMaJ'
period = '2024SunW41'
dhis_pass = os.environ['DHIS_PASSWORD']


def interactWithDhis():
    r = requests.get(getDset,auth=('ionyenuforo','Password@2023'))
    print(r)
    datasetid = ''
    datasets = (r.json()['dataSets'])
    for obj in datasets:
        if obj['displayName'] == 'NEW_APPR_HFR':
            datasetid = obj['id']
            break
    print(dhis_pass)
    """url_datasetinfo = '{}{}{}'.format(baseurl,'dataSets/',datasetid)
    response_datasetinfo = requests.get(url_datasetinfo,auth=('ionyenuforo','Password@2023'))
    print(response_datasetinfo.json())"""
def pulldatafromdb():
    data = []
    values = {}
    header ={'Content-Type':'application/json'}
    values["dataSet"] = "Qshvdw0brBq"
    values["period"] = period
    values["orgUnit"] = orgUnitId
    with psycopg.connect('dbname=lp_camilus user=postgres password=lamis') as con:
        deuid = getdataelementid('Currently on ART (TX_CURR)')
        sql = "select gender,age_group,count(*) from (select gender, (case when age >=1 and age <= 4 then '1-4' when age >= 5 and age <= 9 then '5-9'when age >= 10 and age <= 14 then '10-14'when age >= 15 and age <= 19 then '15-19'when age >= 20 and age <= 24 then '20-24'when age >= 25 and age <= 29 then '25-29'when age >= 30 and age <= 34 then '30-34'when age >= 35 and age <= 39 then '35-39'when age >= 40 and age <= 44 then '40-44'when age >= 45 and age <= 49 then '45-49' else '50+' end) as age_group from radet_view where currentstatus ilike 'Activ%') as s group by 1,2 order by 1 desc"
        df = pd.read_sql(sql,con)
        #print(df)
        for index, row in df.iterrows():
            dict = {}
            uid = getcatcomboid(row['gender'],row['age_group'])
            dict['dataElement'] = deuid
            dict['categoryOptionCombo'] = uid
            dict['value'] = str(row['count'])
            data.append(dict)
    values['dataValues'] = data
    json_string = json.dumps(values)
    #print(json_string)
    res = requests.post(posttoDataSet,auth=('ionyenuforo',dhis_pass),data=json_string,headers=header)
    print('{}-{}'.format(res,res.text))      
        #print('The uuid for this dataelement is {}'.format(deuid))
def getcatcomboid(cat1,cat2):
    combo = '{}, {}'.format(cat2,cat1)
    #print(combo)
    uid = ccframe.loc[combo,'uid']
    #print(uid)
    return uid
def getdataelementid(name):
    getdelem = 'HFR1_{}'.format(name)
    return deframe.loc[getdelem,'ID']
    
    
            

def main():
    '''# Start a session so we can have persistant cookies
    session = requests.session()

    # This is the form data that the page sends when logging in
    login_data = {
        'username': EMAIL,
        'password': PASSWORD
    }
    headers = {'u':'ionyenuforo:Password@2023'}
    # Authenticate
    r = session.post(URL, data=login_data)

    # Try accessing a page that requires you to be logged in
    r = session.get(uploadval)
    session_cookies = session.cookies.get_dict()
    print(session_cookies['JSESSIONID'])
    print(r)'''
    #pulldatafromdb()
    interactWithDhis()

if __name__ == '__main__':
    main()