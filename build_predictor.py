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

data_element = pd.read_excel("format_to_import_indicator_codelist_from_datafi.xlsx",sheet_name = "predictor")
insertToExpr = "insert into expression(expressionid,description,expression,missingvaluestrategy)values({},{},{},'SKIP_IF_ALL_VALUES_MISSING');\n\n"
insToPredictor = "insert into predictor_temp(predictorid,uid,"\
"name, description, generatorexpression, generatoroutput, generatoroutputcombo,"\
    "sequentialsamplecount, annualsamplecount,"\
    "shortname)values({},{},{},{},{},{},{},5,0,{});"
out = open('importexp.sql','w+')

short1 = 'Pred_SWO'
share_settings = '{"owner": "RNEoDCt5yu7","users": {},"public": "rw------","external": false,"userGroups": {}}'

def insertIntoExpression():
    out.write('----Building insert for expression table on date {}\n\n\n'.format(dt.date.today()))
    ctr = 1
    for index,row in data_element.iterrows():
        catcomboname = row['CategoryOptionCombo']
        datauid = row['DataElementUID']
        catcombouid = row['CategoryOptionComboUID']
        if catcomboname != catcomboname:
            desc = "'Predictor_{}'".format(row['Description'])
        else:
            desc = "'Predictor_{} {}'".format(row['Description'],catcomboname)
            
        if catcombouid != catcombouid:
            expr = "'sum(#{{{}}})'".format(datauid)
        else:
            expr = "'sum(#{{{}.{}}})'".format(datauid,catcombouid)
        sql = insertToExpr.format(ctr,desc,expr)
        out.write('{}'.format(sql))
        ctr = ctr + 1

def insertToPredictor():
    out.write('----Building insert for predictor_temp table on date {}\n\n\n'.format(dt.date.today()))
    ctr = 1
    for index,row in data_element.iterrows():
        uid = "'{}'".format(getuid())
        catcomboname = row['CategoryOptionCombo']
        if catcomboname != catcomboname:
            name = "'Predictor_{}'".format(row['DataElement'])
        else:
            name = "'Predictor_{} {}'".format(row['DataElement'],catcomboname)
        datauid = row['DataElementUID']
        catcombouid = row['CategoryOptionComboUID']
        if catcombouid != catcombouid:
            expr = "'sum(#{{{}}})'".format(datauid)
        else:
            expr = "'sum(#{{{}.{}}})'".format(datauid,catcombouid)
        genoutput = "'roll_{}'".format(row['DataElement'])
        shortname = "'{}.{}'".format(short1,ctr)
        sql = insToPredictor.format(ctr,uid,name,name,expr,genoutput,"'{}'".format(catcomboname),shortname)
        out.write('{}\n'.format(sql))
        ctr = ctr + 1
    out.close()
        
    
    
        
def getuid():
    length = 11
    characters = string.ascii_letters + string.digits
    generated_string = ''.join(random.choice(characters) for _ in range(length))
    return generated_string

    
def main():
    insertIntoExpression()
    insertToPredictor()
    

if __name__ == '__main__':
    main()

        