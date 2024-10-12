# -*- coding: utf-8 -*-
"""
Created on Fri Nov  3 14:59:13 2023

@author: HI LEAD
"""

import glob
import os
import re

def main():
    DIR = r'C:\Users\HI LEAD\Downloads\installers\lplus_updates_10_07\lp_v205_2\runtime\ndr\transfer\temp\1407'

    path = os.path.join(DIR, "*")
    files = glob.glob(path)
    hits = 0
    with open('data.csv','a+') as file:
      for f in files:
         fh = open(f,'r')
         fc = fh.read()
         refill = findTXCurData(fc)
         visit = findTXCurData(fc,flag=2)
         hospital_number = findTXCurData(fc,flag=3)
         file.write('{},{},{}\n'.format(refill,visit,hospital_number))
        
        
    '''    if "<PrescribedRegimenDispensedDate>" not in fc:
            hits = hits + 1
            print("no match found in", f)
        fh.close()
    print('Number of hits is:',hits)'''
def findTXCurData(text,flag=1):
    if flag == 1:
        m = re.findall('<PrescribedRegimenDuration>(\d{2,3}?)</PrescribedRegimenDuration>',text)
        if m:
            found = m[-1]
            return found
    elif flag == 2:
        m = re.findall('<PrescribedRegimenDispensedDate>(.+?)</PrescribedRegimenDispensedDate>',text)
        if m:
            found = m[-1]
            return found
    else:
        m = re.findall('<HospitalNumber>(.+?)</HospitalNumber>',text)
        if m:
            found = m[-1]
            return found
        
    

if __name__ == '__main__':
    main()