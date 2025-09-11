import os
from dotenv import load_dotenv

load_dotenv()

# MongoDB configuration
MONGO_URI = os.getenv("MONGO_URI")
HADEETH_DB_NAME = os.getenv("MONGO_HADEETH_DB_NAME")
QURAAN_DB_NAME = os.getenv("MONGO_QURAAN_DB_NAME")
SUBSCRIBERS_DB_NAME = os.getenv("MONGO_SUBSCRIBERS_DB_NAME")

# Email configuration
SMTP_SERVER= "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_ADDRESS = os.getenv('EMAIL_ADDRESS')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')