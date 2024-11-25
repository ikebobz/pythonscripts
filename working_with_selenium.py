# -*- coding: utf-8 -*-
"""
Created on Fri Nov 22 12:13:50 2024

@author: HI LEAD
"""

#working with selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
import autoit as autit
import time

#initialize chrome start options
prefs = {"selectfile.last_directory":r"C:\Users\HI LEAD\Documents\XMLs"}
chrome_options = Options()
chrome_options.add_experimental_option("detach", True)
chrome_options.add_experimental_option("prefs", prefs)



driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()),options=chrome_options)

driver.get("https://ndr.phis3project.org.ng/")
email = driver.find_element(By.ID,'Email')
email.send_keys('ionyenuforo@heartlandalliancenigeria.org')
passwrd =driver.find_element(By.ID,'Password')
passwrd.send_keys('Pass1word#')
loginbtn = driver.find_element(By.ID,'loginButton')
loginbtn.send_keys(Keys.RETURN)
driver.get('https://ndr.phis3project.org.ng/uploads/new-upload')
dropzone = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID,'ndrDropzone')))
dropzone.click()
time.sleep(5)
autit.win_active('Open')
autit.control_send("Open","Edit1",r"C:\Users\HI LEAD\Downloads\treatmentxml.zip")
autit.control_send("Open","Edit1","{ENTER}")

