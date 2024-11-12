# -*- coding: utf-8 -*-
"""
Created on Sat Oct 26 08:03:19 2024

@author: HI LEAD
"""

'''with open('lamispluslite.ab','r',encoding = 'utf8') as f:
    content = f.read()
    print(content)'''
import zlib,sys,gzip
f = open('lite.ab','rb')
decompressedn = zlib.decompress(f.read(),wbits = -zlib.MAX_WBITS)
#decompressedn = gzip.open('lamispluslite.adb','rb').read()
print(decompressedn)