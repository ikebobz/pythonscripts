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
from jproperties import Properties


ccframe = pd.read_excel("category_combinations.xlsx",sheet_name="catcom")
ccframe.set_index('catcombo',inplace=True)
deframe = pd.read_excel("data_elements_new.xlsx",sheet_name="data_elements_new")
deframe.set_index('Name',inplace=True)

#initialize endpoints
configs = Properties()
config_file = open('config.properties','rb')
configs.load(config_file)
baseurl = configs.get("baseurl").data
dhis_pass = os.environ[configs.get("dhis_pass").data]
getUrl = '{}{}'.format(baseurl,'dataSets')
postUrl = '{}{}'.format(baseurl,'dataValueSets')

#initialize dhis post parameters
#week = '2024SunW{}'.format(dt.date.today().isocalendar().week)
period = configs.get("period").data
orgUnitId = configs.get("orgUnitId").data
dataset =configs.get("dataset").data

#initialize database connection parameters
connectstring = configs.get("connectstring").data


#print(dhis_pass)



def pulldatafromdb():
    data = []
    values = {}
    header ={'Content-Type':'application/json'}
    values["dataSet"] = dataset
    values["period"] = period
    values["orgUnit"] = orgUnitId
    with psycopg.connect(connectstring) as con:
        queries = getqueries()
        for query in queries:
            deuid = query.split(":-")[0]
            print(deuid)
            sql = query.split(":-")[1]
            print(sql)
            #deuid = getdataelementid('Currently on ART (TX_CURR)')
            #sql = "select gender,age_group,count(*) from (select gender, (case when age >=1 and age <= 4 then '1-4' when age >= 5 and age <= 9 then '5-9'when age >= 10 and age <= 14 then '10-14'when age >= 15 and age <= 19 then '15-19'when age >= 20 and age <= 24 then '20-24'when age >= 25 and age <= 29 then '25-29'when age >= 30 and age <= 34 then '30-34'when age >= 35 and age <= 39 then '35-39'when age >= 40 and age <= 44 then '40-44'when age >= 45 and age <= 49 then '45-49' else '50+' end) as age_group from radet_view where currentstatus ilike 'Activ%') as s group by 1,2 order by 1 desc"
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
    res = requests.post(postUrl,auth=('ionyenuforo',dhis_pass),data=json_string,headers=header)
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
def getqueries():
    with open('queries.txt','r') as src:
        queries = src.read().split(";\n")
        for query in queries:
            print(query)
        return queries
    
    
            

def main():
    pulldatafromdb()


if __name__ == '__main__':
    main()