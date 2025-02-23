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
import math

data_element = pd.read_excel("format_to_import_indicator_codelist_from_datafi.xlsx",sheet_name = "indicator")
#catcombo = pd.read_excel("data_elements.xlsx",sheet_name = "category_combo")
insertsql = "insert into indicator(indicatorid,uid,created, lastupdated, lastupdatedby, name,shortname,description,annualized,indicatortypeid,numerator,numeratordescription,denominator,userid,sharing)VALUES({},{},current_date,current_date,216157,{},{},{},false,4675,{},{},1,216157,{});\n\n"
out = open('import.sql','w+')

short1 = 'SWO'
share_settings = '{"owner": "RNEoDCt5yu7","users": {},"public": "rw------","external": false,"userGroups": {}}'


def start():
    counter = 0
    shortctr = 1
    out.write('----Building on date {}\n\n\n'.format(dt.date.today()))
    for index,row in data_element.iterrows():
        indicator = row['Indicator']
        shortname = "'{}.{}'".format(short1,shortctr)
        datauid = row['DataElementUID']
        desc = row['Description']
        catcombouid = row['CategoryOptionComboUID']
        if catcombouid != catcombouid:
            formula = "'#{{{}}}'".format(datauid)
        else:
            formula = "'#{{{}.{}}}'".format(datauid,catcombouid)
        counter = counter + 1
        shortctr = shortctr + 1
        sql = insertsql.format(counter,"'{}'".format(getuid()),"'{}'".format(indicator),shortname,"'{}'".format(desc),formula,"'{}'".format(indicator),"'{}'".format(share_settings))
        out.write('{}'.format(sql))
        
        """for index,r in catcombo.iterrows():
            catcom = r['NAME']
            name = "'{} {}'".format(de,r['NAME'])
            formula = "'#{{{}.{}}}'".format(id,r['ID'])
            counter = counter + 1
            shortctr = shortctr + 1
            sql = insertsql.format(counter,"'{}'".format(getuid()),name,"'{}_{}'".format(short1,shortctr),name,formula,name,"'{}'".format(share_settings))
            out.write('{}'.format(sql))"""
    
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

        