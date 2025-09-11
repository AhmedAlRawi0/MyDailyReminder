import requests
from models.database import get_current_hadith_state, get_current_verse_state, get_subscribers
from models.email import send_email
import pytz
from datetime import datetime
import json

# Load surah_names.json
with open("surah_names.json", "r") as file:
    QURAN_DATA = json.load(file)

# Function to fetch hadeeth data from the API
def fetch_hadeeth(hadeeth_id):
    url_en = f"https://hadeethenc.com/api/v1/hadeeths/one/?id={hadeeth_id}&language=en"
    url_fr = f"https://hadeethenc.com/api/v1/hadeeths/one/?id={hadeeth_id}&language=fr"
    url_ar = f"https://hadeethenc.com/api/v1/hadeeths/one/?id={hadeeth_id}&language=ar"
    response_en = requests.get(url_en)
    response_fr = requests.get(url_fr)
    response_ar = requests.get(url_ar)
    response_en_data = response_en.json()
    response_fr_data = response_fr.json()
    if response_en.status_code == 200 and response_fr.status_code == 200 and response_ar.status_code == 200:
        response_en_data["reference"] = response_ar.json().get("reference")
        response_fr_data["reference"] = response_ar.json().get("reference")
        return response_en_data, response_fr_data
    elif response_en.status_code == 200 and response_ar.status_code == 200:
        response_en_data["reference"] = response_ar.json().get("reference")
        return response_en_data
    elif response_fr.status_code == 200 and response_ar.status_code == 200:
        response_fr_data["reference"] = response_ar.json().get("reference")
        return response_fr_data
    return None

# Function to send daily hadeeth to all subscribers
def send_daily_reminder():
    subscribers = get_subscribers()  # Get all subscriber emails from the database
    if not subscribers:
        print("No subscribers to send the email to.")
        return

    # Get the current state
    current_index, last_updated, last_updated_syd, hadeeth, hadeeth_fr = get_current_hadith_state()
    current_surah, current_verse, last_updated_verse, verse, verse_fr = get_current_verse_state()

    # Get today's date in a readable format
    local_tz = pytz.timezone('US/Eastern')
    today_date = datetime.now(local_tz).strftime("%A, %B %d, %Y")

    # Email HTML content template
    for to_email in subscribers:
        #unsubscribe_link = f"http://127.0.0.1:5000/unsubscribe?email={to_email}"
        unsubscribe_link = f"https://mydailyhadith.onrender.com/unsubscribe?email={to_email}"

        email_body = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Daily Verse and Hadith</title>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Amiri&display=swap');

                /* General Styles */
                body {{
                    font-family: 'Arial', sans-serif;
                    margin: 0;
                    padding: 0;
                    background: linear-gradient(to bottom, #f7f2e9, #eae7dc);
                    color: #333;
                }}

                .container {{
                    max-width: 800px;
                    margin: 30px auto;
                    background-color: #fff;
                    border-radius: 12px;
                    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
                    padding: 20px;
                }}

                .container-gold {{
                    border: 10px solid #d4af37;
                }}

                .container-pink {{
                    border: 10px solid #9b5e5e;
                }}

                h1 {{
                    font-family: 'Amiri', serif;
                    text-align: center;
                    font-size: 28px;
                    color: #d4af37;
                    text-shadow: 1px 1px 3px rgba(0, 0, 0, 0.3);
                    margin-bottom: 10px;
                    border-bottom: 2px solid #d4af37;
                    padding-bottom: 5px;
                }}

                .h1-pink {{
                    color: #9b5e5e;
                    border-color: #9b5e5e;
                }}

                h2 {{
                    font-family: 'Amiri', serif;
                    font-size: 20px;
                    color: #2c3e50;
                    margin-bottom: 10px;
                    text-decoration: underline;
                }}

                p {{
                    font-family: 'Amiri', serif;
                    font-size: 16px;
                    line-height: 1.7;
                    color: #34495e;
                    margin-bottom: 15px;
                }}

                .date {{
                    text-align: center;
                    font-size: 14px;
                    color: #7f8c8d;
                    margin-bottom: 20px;
                    font-style: italic;
                }}

                .arabic {{
                    font-family: 'Amiri', serif;
                    font-size: 18px;
                    text-align: right;
                    direction: rtl;
                    color: #2c3e50;
                    padding: 10px;
                    margin-bottom: 15px;
                }}

                .separator {{
                    width: 100%;
                    height: 2px;
                    margin: 15px 0;
                }}

                .separator-gold {{
                    background: #d4af37;
                }}

                .separator-pink {{
                    background: #9b5e5e;
                }}

                .footer {{
                    text-align: center;
                    font-size: 12px;
                    color: #7f8c8d;
                    margin-top: 20px;
                }}

                .footer a {{
                    color: #3498db;
                    text-decoration: none;
                }}

                .footer a:hover {{
                    text-decoration: underline;
                }}
            </style>
        </head>
        <body>
            <!-- Daily Verse Section -->
            <div class="container container-pink">
                <h1 class="h1-pink">Daily Verse</h1>
                <h2 style="text-align: right; direction: rtl;">الآية:</h2>
                <p class="arabic">{verse['result']['arabic_text']}</p>
                <p class="arabic">
                    <strong>السورة:</strong> {QURAN_DATA[str(verse['result']['sura'])]["arabic"]}،{" "}
                    <strong>رقم الآية:</strong> {verse['result']['aya']}
                </p>
                <div class="separator separator-pink"></div>
                <h2>The Verse:</h2>
                <p>{verse['result']['translation']}</p>
                <p>
                    <strong>Sura:</strong> {QURAN_DATA[str(verse['result']['sura'])]["english"]},{" "}
                    <strong>Aya:</strong> {verse['result']['aya']}
                </p>
                <div class="separator separator-pink"></div>
                <h2>Le Verset:</h2>
                <p>{verse_fr['result']['translation']}</p>
                <p>
                    <strong>Sura:</strong> {QURAN_DATA[str(verse['result']['sura'])]["english"]},{" "}
                    <strong>Aya:</strong> {verse['result']['aya']}
                </p>
            </div>

            <!-- Daily Hadith Section -->
            <div class="container container-gold">
                <h1>Daily Hadith</h1>
                <h2 style="text-align: right; direction: rtl;">الحديث:</h2>
                <p class="arabic">{hadeeth['hadeeth_ar']}</p>
                <h2 style="text-align: right; direction: rtl;">الشرح:</h2>
                <p class="arabic">{hadeeth['explanation_ar']}</p>
                <div class="separator separator-gold"></div>
                <h2>The Hadith:</h2>
                <p>{hadeeth['hadeeth']}</p>
                <h2>Explanation:</h2>
                <p>{hadeeth['explanation']}</p>
                <div class="separator separator-gold"></div>
                <h2>Le Hadith:</h2>
                <p>{hadeeth_fr['hadeeth']}</p>
                <h2>Explication:</h2>
                <p>{hadeeth_fr['explanation']}</p>   
            </div>
            <div class="separator separator-gold"></div>
            <div class="footer">
                    <p>
                        © {datetime.now().year} MyDailyReminder | 
                        <a href="https://mydailyreminder.ca">Visit Website</a> | 
                        <a href="{unsubscribe_link}">Unsubscribe</a>
                    </p>
                </div>
        </body>
        </html>
        """

        send_email(to_email, f"Daily Reminder - {today_date}", email_body)