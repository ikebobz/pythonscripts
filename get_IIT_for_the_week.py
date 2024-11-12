# -*- coding: utf-8 -*-
"""
Created on Sun Aug 25 19:28:32 2024

@author: HI LEAD
"""

import pandas as pd
import numpy as np
from datetime import timedelta,date
import datetime as dt

linelist = pd.read_excel("Patient_Line_List.xlsx",sheet_name = "Patient_Line_List")
radet = pd.read_excel("radet.xlsx",sheet_name = "radet")
ndrll = pd.read_excel("list.xlsx",sheet_name = "list")

def main():
    switch = 1
    if switch == 1:
        getVariance()
        return
    facilitycount = {}
    linelist['Days Of ARV Refill'].fillna(0,inplace = True)
    linelist['Last Drug Pickup date'].fillna('2000-01-01',inplace = True)
    for index,row in linelist.iterrows():
        lastpickup = row['Last Drug Pickup date']
        mmd = row['Days Of ARV Refill']
        facility = row['Facility']
        unique_id =  row['Hospital Number']
        if isinstance(mmd, dt.datetime):
            continue
        mmd_28 = mmd + 29
        iit_date = lastpickup + timedelta(days = mmd_28)
        if pd.Timestamp(date.today()) < iit_date < pd.Timestamp(date.today()+timedelta(days=6)):
            if facility in facilitycount.keys():
                val = facilitycount[facility]
                facilitycount[facility] = val + 1
            else:
                facilitycount[facility] = 1
    output = pd.DataFrame.from_dict(facilitycount,orient = 'index',columns = ['Number of affected clients'])
    print([output])
    output.to_excel("IIT_forecast_this_week.xlsx")

def getVariance():
    count = 0
    target = []
    interest_columns_rad = radet[['NDR Patient Identifier','Current ART Status']]
    interest_columns_rad['Current ART Status'].fillna('NA',inplace = True)
    interest_columns_list = ndrll[['Patient Identifier','Current Status (28 Days)']]
    interest_columns_list.set_index('Patient Identifier',inplace=True)
    for index,row in interest_columns_rad.iterrows():
        
        hnumber = row['NDR Patient Identifier']
        radet_status =  row['Current ART Status']
        try:
            list_status = interest_columns_list.loc[hnumber,'Current Status (28 Days)']
            if (radet_status == 'Active' or radet_status == 'Active Restart') and list_status != 'Active':
                #print('{}---------{}'.format(radet_status,list_status))
                target.append(hnumber[-36:])
                count = count + 1
        except KeyError:
            if "ctive" in radet_status:
                count = count + 1
                target.append(hnumber[-36:])
            continue
            
    print("The number of affacted clients is :",count)
    targetframe = pd.DataFrame(target,columns=['person_uuid'])
    targetframe.to_excel("variants.xlsx",sheet_name='identifiers',index=False)

        

if __name__ == '__main__':
    main()
