"""
Utilities for persisting data to disk and loading it on startup.
"""

import json
import os
from typing import Dict, Any
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def save_to_json(data: Dict[str, Any], filename: str) -> bool:
    """
    Save dictionary data to a JSON file.
    
    Args:
        data: The dictionary to save
        filename: The filename to save to
        
    Returns:
        True if successful, False otherwise
    """
    try:
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving data to {filename}: {str(e)}")
        return False

def load_from_json(filename: str) -> Dict[str, Any]:
    """
    Load dictionary data from a JSON file.
    
    Args:
        filename: The filename to load from
        
    Returns:
        The loaded dictionary, or an empty dictionary if the file doesn't exist
    """
    if not os.path.exists(filename):
        return {}
        
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading data from {filename}: {str(e)}")
        return {}
