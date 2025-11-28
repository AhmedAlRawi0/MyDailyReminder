import axios from 'axios';

//const API_BASE_URL = process.env.REACT_APP_API_BASE_URL_LOCAL;
//const API_BASE_URL = process.env.REACT_APP_API_BASE_URL_PROD;
const API_BASE_URL = "https://mydailyreminder-production.up.railway.app";

/**
 * @param {*} language 
 * @returns 
 */
export const fetchHadeeth = async (language) => {
  try {
    const response = await axios.get(`${API_BASE_URL}/daily-hadeeth?Language=${language}`);
    return response.data;
  } catch (err) {
    throw new Error('Failed to fetch the Hadeeth. Please try again later.');
  }
};

/**
 * @param {*} language 
 * @returns 
 */
export const sendEmail = async (language) => {
  try {
    const response = await axios.get(`${API_BASE_URL}/send-email`);
    return response.data;
  } catch (err) {
    throw new Error('Failed to send email. Please try again later.');
  }
};

/**
 * 
 * @param {*} email 
 * @returns 
 */
export const subscribeToEmails = async (email) => {
  try {
    const response = await axios.post(`${API_BASE_URL}/subscribe`, { email });
    return response.data.message;
  } catch (err) {
    throw new Error(err.response?.data?.message || 'Failed to subscribe. Please try again.');
  }
};
