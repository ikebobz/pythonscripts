# -*- coding: utf-8 -*-
"""
Created on Tue Nov 12 05:59:47 2024

@author: HI LEAD
"""

import pandas as pd
import numpy as np
import os
import datetime as dt
import random
import string

hts_recent_rita = pd.read_excel("hts_recent_rita_rtri.xlsx",sheet_name = "hts_rita")
hts_recent_rtri = pd.read_excel("hts_recent_rita_rtri.xlsx",sheet_name = "hts_rtri")
catcombo = pd.read_excel("hts_recent_rita_rtri.xlsx",sheet_name = "catcombo")
catcombortri = pd.read_excel("hts_recent_rita_rtri.xlsx",sheet_name = "catcombortri")
insertsql = "insert into indicator(indicatorid,uid,created, lastupdated, lastupdatedby, name,shortname,description,annualized,indicatortypeid,numerator,numeratordescription,denominator,userid,sharing)VALUES({},{},current_date,current_date,216157,{},{},{},false,4675,{},{},1,216157,{});\n\n"
out = open('import.sql','w+')

short1 = 'hts_recent_rita'
short2 = 'hts_recent_rtri'
share_settings = '{"owner": "RNEoDCt5yu7","users": {},"public": "rw------","external": false,"userGroups": {}}'


def start():
    counter = 503178
    shortctr = 1
    out.write('----Building on date {}\n\n\n'.format(dt.date.today()))
    for index,row in hts_recent_rita.iterrows():
        de = row['NAME']
        id = row['ID']
        for index,r in catcombo.iterrows():
            name = "'{} {}'".format(de,r['NAME'])
            formula = "'#{{{}.{}}}'".format(id,r['ID'])
            counter = counter + 1
            shortctr = shortctr + 1
            sql = insertsql.format(counter,"'{}'".format(getuid()),name,"'{}_{}'".format(short1,shortctr),name,formula,name,"'{}'".format(share_settings))
            out.write('{}'.format(sql))
    out.write('\n--------------------BUILDING FOR RITA--------------\n')
    shortctr = 1
    for index,row in hts_recent_rtri.iterrows():
        de = row['NAME']
        id = row['ID']
        for index,r in catcombortri.iterrows():
            name = "'{} {}'".format(de,r['NAME'])
            formula = "'#{{{}.{}}}'".format(id,r['ID'])
            counter = counter + 1
            shortctr = shortctr + 1
            sql = insertsql.format(counter,"'{}'".format(getuid()),name,"'{}_{}'".format(short2,shortctr),name,formula,name,"'{}'".format(share_settings))
            out.write('{}'.format(sql))
    out.close()
def getuid():
    length = 11
    characters = string.ascii_letters + string.digits
    generated_string = ''.join(random.choice(characters) for _ in range(length))
    return generated_string

    
def main():
    start()
    

if __name__ == '__main__':
    main()

        