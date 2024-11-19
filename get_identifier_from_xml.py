# -*- coding: utf-8 -*-
"""
Created on Fri Nov  3 14:59:13 2023

@author: HI LEAD
"""

import glob
import os
import re

def main():
    DIR = r'C:\Users\HI LEAD\Documents\XMLs\25057_wh1fufa69db_OlojowonPrimaryHealthCentre_treatment_14112024'

    path = os.path.join(DIR, "*")
    files = glob.glob(path)
    hits = 0
    with open('data.csv','a+') as file:
      for f in files:
         fh = open(f,'r')
         fc = fh.read()
         identifier = findTXCurData(fc)
         file.write('{}\n'.format(identifier))
        
        
    '''    if "<PrescribedRegimenDispensedDate>" not in fc:
            hits = hits + 1
            print("no match found in", f)
        fh.close()
    print('Number of hits is:',hits)'''
def findTXCurData(text,flag=1):
    if flag == 1:
        m = re.findall('<PatientIdentifier>(.*?)</PatientIdentifier>',text)
        if m:
            found = m[-1]
            return found
    else:
        m = re.findall('<PatientIdentifier>(.+?)</PatientIdentifier>',text)
        if m:
            found = m[-1]
            return found
        
    

if __name__ == '__main__':
    main()