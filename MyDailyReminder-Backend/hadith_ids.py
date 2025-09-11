import pandas as pd

def get_ids_list(file_path='data/Hadeeths.xlsx', skiprows=1):
    """
    Load the Excel file and return the list of IDs.

    Parameters:
    file_path (str): The path to the Excel file.
    skiprows (int): The number of rows to skip at the beginning of the file.

    Returns:
    list: A list of IDs.
    """
    # Load the data, skipping rows until the actual data begins
    df = pd.read_excel(file_path, skiprows=skiprows)

    # Extract the 'id' column
    ids = df['id']

    # Convert the 'id' column to a list and return it
    ids_list = ids.tolist()
    return ids_list
