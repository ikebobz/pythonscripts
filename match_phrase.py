# -*- coding: utf-8 -*-
"""
Created on Sun Dec  1 20:23:29 2024

@author: HI LEAD
"""

# -*- coding: utf-8 -*-
"""
Created on Fri Nov  3 14:59:13 2023

@author: HI LEAD
"""

import glob
import os
import re
import numpy as np

def main():
    source = r'C:\Users\HI LEAD\Downloads\dhis_forms\pmtct_hfr.html'
    hits = 0
    out = open('match.csv','a+')
    infile = open(source,'r')
    fc = infile.read()
    uid = matchPhrase(fc)
    #out.write('{}\n'.format(uid))
        
def matchPhrase(text,flag=1):
    if flag == 1:
        m = re.findall('id="([A-Za-z0-9]+)\-[A-Za-z0-9]+',text)
        unique = np.unique(np.array(m))
        print(unique)
    
        
    

if __name__ == '__main__':
    main()