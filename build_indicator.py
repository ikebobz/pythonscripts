# -*- coding: utf-8 -*-
"""
Created on Tue Nov 12 05:59:47 2024

@author: HI LEAD
"""

import pandas as pd
import numpy as np

hts_recent_rita = pd.read_excel("hts_recent_rita_rtri.xlsx",sheet_name = "hts_rita")
hts_recent_rtri = pd.read_excel("hts_recent_rita_rtri.xlsx",sheet_name = "hts_rtri")
catcombo = pd.read_excel("hts_recent_rita_rtri.xlsx",sheet_name = "catcombo")
catcombortri = pd.read_excel("hts_recent_rita_rtri.xlsx",sheet_name = "catcombortri")



def start():
    for index,row in hts_recent_rita.iterrows():
        de = row['NAME']
        for index,r in catcombo.iterrows():
            formula = '{} {} = {} {}'.format(de,r['NAME'],de,r['NAME'])
            print(formula)
def main():
    start()
    

if __name__ == '__main__':
    main()

        