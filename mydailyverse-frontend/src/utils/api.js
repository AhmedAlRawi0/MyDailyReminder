import axios from 'axios';

//const API_BASE_URL = process.env.REACT_APP_API_BASE_URL_LOCAL;
//const API_BASE_URL = process.env.REACT_APP_API_BASE_URL_PROD;
//const API_BASE_URL_VERSE = "http://127.0.0.1:5000";
//const API_BASE_URL_VERSE = "https://mydailyverse.onrender.com"
const API_BASE_URL_HADITH = "https://mydailyreminder-production.up.railway.app"

/**
 * @param {*} language 
 * @returns 
 */
export const fetchVerse = async (language) => {
  try {
    const response = await axios.get(`${API_BASE_URL_HADITH}/daily-verse?Language=${language}`);
    return response.data;
  } catch (err) {
    throw new Error('Failed to fetch the Verse. Please try again later.');
  }
};

/**
 * 
 * @param {*} email 
 * @returns 
 */
export const subscribeToEmails = async (email) => {
  try {
    const response = await axios.post(`${API_BASE_URL_HADITH}/subscribe`, { email });
    return response.data.message;
  } catch (err) {
    throw new Error(err.response?.data?.message || 'Failed to subscribe. Please try again.');
  }
};
