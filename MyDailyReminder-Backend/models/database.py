from pymongo import MongoClient
from pymongo.server_api import ServerApi
from pymongo.errors import DuplicateKeyError
from config import MONGO_URI, HADEETH_DB_NAME, QURAAN_DB_NAME, SUBSCRIBERS_DB_NAME

client = MongoClient(MONGO_URI,
                     tls=True,
                     tlsAllowInvalidCertificates=True,
                     server_api=ServerApi('1'),
                     serverSelectionTimeoutMS=2000, connectTimeoutMS=2000,
                     socketTimeoutMS=2000, timeoutMS=2000)
hadeeth_db = client[HADEETH_DB_NAME]
hadeeth_collection = hadeeth_db['persistence']
quraan_db = client[QURAAN_DB_NAME]
quraan_collection = quraan_db['quraan-persistence']
subscribers_db = client[SUBSCRIBERS_DB_NAME]
subscribers_collection = subscribers_db['subscribers']

# Function to add a subscriber to MongoDB
def add_subscriber(email):
    try:
        subscribers_collection.create_index("email", unique=True)
        subscribers_collection.insert_one({"email": email})
        return {"message": "Successfully subscribed!"}
    except DuplicateKeyError:
        return {"message": "Email already subscribed!"}
    except Exception:
        return {"message": "Database temporarily unavailable. Please retry."}

# Function to remove a subscriber from MongoDB
def remove_subscriber(email):
    try:
        result = subscribers_collection.delete_one({"email": email})
        if result.deleted_count > 0:
            return {"message": "Successfully unsubscribed!"}
        else:
            return {"message": "Email not found in subscription list."}
    except Exception:
        return {"message": "Database temporarily unavailable. Please retry."}
