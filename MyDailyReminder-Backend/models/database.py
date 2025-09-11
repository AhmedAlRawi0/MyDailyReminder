from pymongo import MongoClient
from pymongo.server_api import ServerApi
from pymongo.errors import DuplicateKeyError, OperationFailure
from config import MONGO_URI, HADEETH_DB_NAME, QURAAN_DB_NAME, SUBSCRIBERS_DB_NAME
from datetime import datetime
import pytz

client = MongoClient(MONGO_URI, 
                     tls=True,
                     tlsAllowInvalidCertificates=True,
                     server_api=ServerApi('1'))
try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
except Exception as e:
    print(e)

hadeeth_db = client[HADEETH_DB_NAME]
hadeeth_collection = hadeeth_db['persistence']
quraan_db = client[QURAAN_DB_NAME]
quraan_collection = quraan_db['quraan-persistence']
subscribers_db = client[SUBSCRIBERS_DB_NAME]
subscribers_collection = subscribers_db['subscribers']

# Ensure a unique index on the email field
try:
    subscribers_collection.create_index("email", unique=True)
    print("Unique index on 'email' created.")
except OperationFailure as e:
    print(f"Failed to create unique index: {e}")

# Function to initialize the hadith state of the MongoDB
def initialize_state_hadith():
    initial_state = {
        "current_index": 0,
        "last_updated": "1970-01-01",
        "last_updated_syd": "1970-01-01",
        "last_hadeeth": None,
        "last_hadeeth_fr": None
    }
    hadeeth_collection.insert_one(initial_state)
    return initial_state["current_index"], initial_state["last_updated"], initial_state["last_updated_syd"], initial_state["last_hadeeth"], initial_state["last_hadeeth_fr"]

# Function to initialize the hadith state of the MongoDB
def initialize_state_verse():
    initial_state = {
        "current_surah": 1,
        "current_verse": 1,
        "last_updated": "1970-01-01",
        "last_verse": None,
        "last_verse_fr": None,
    }
    quraan_collection.insert_one(initial_state)
    return initial_state["current_surah"], initial_state["current_verse"], initial_state["last_updated"], initial_state["last_verse"], initial_state["last_verse_fr"]

# Function to get the current hadith state from MongoDB
def get_current_hadith_state():
    doc = hadeeth_collection.find_one()
    if not doc:
        return initialize_state_hadith()
    return doc.get("current_index", 0), doc.get("last_updated", "1970-01-01"), doc.get("last_updated_syd", "1970-01-01"), doc.get("last_hadeeth", None), doc.get("last_hadeeth_fr", None)

# Function to get the current quraan state from MongoDB
def get_current_verse_state():
    doc = quraan_collection.find_one()
    if not doc:
        return initialize_state_verse()
    return doc.get("current_surah", 0), doc.get("current_verse", 0), doc.get("last_updated", "1970-01-01"), doc.get("last_verse", None), doc.get("last_verse_fr", None)

# Function to update the current hadith state in MongoDB
def update_current_hadith_state(index, hadeeth_data, hadeeth_data_fr):
    local_tz = pytz.timezone('US/Eastern')
    syd_tz = pytz.timezone('Australia/Sydney')
    hadeeth_collection.update_one(
        {},
        {
            "$set": {
                "current_index": index,
                "last_updated": datetime.now(local_tz).strftime("%Y-%m-%d"),
                "last_updated_syd": datetime.now(syd_tz).strftime("%Y-%m-%d"),
                "last_hadeeth": hadeeth_data,
                "last_hadeeth_fr": hadeeth_data_fr
            }
        },
        upsert=True
    )

# Function to retrieve subscribers from MongoDB
def get_subscribers():
    return [doc['email'] for doc in subscribers_collection.find({})]

# Function to add a subscriber to MongoDB
def add_subscriber(email):
    try:
        subscribers_collection.insert_one({"email": email})
        return {"message": "Successfully subscribed!"}
    except DuplicateKeyError:
        return {"message": "Email already subscribed!"}
    except Exception as e:
        return {"message": f"An error occurred: {e}"}
    
# Function to remove a subscriber from MongoDB
def remove_subscriber(email):
    try:
        result = subscribers_collection.delete_one({"email": email})
        if result.deleted_count > 0:
            return {"message": "Successfully unsubscribed!"}
        else:
            return {"message": "Email not found in subscription list."}
    except Exception as e:
        return {"message": f"An error occurred: {e}"}