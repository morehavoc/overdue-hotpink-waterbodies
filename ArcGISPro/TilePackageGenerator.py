import arcpy
import os
import urllib.parse
from pathlib import Path
import requests
from azure.storage.blob import BlobServiceClient
import json
import re
import random

CLAUDE_API_KEY = ""
AZURE_CONNECTION_STRING = ""
AZURE_CONTAINER_NAME = "basemaps"

# Function to sanitize names (letters, numbers, and underscores only)
def sanitize_name(name):
    # Replace any non-alphanumeric characters with underscore and convert to lowercase
    sanitized = re.sub(r'[^a-zA-Z0-9]', '_', name)
    # Remove consecutive underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove leading/trailing underscores
    sanitized = sanitized.strip('_').lower()
    return sanitized

# Get all existing names from the layer
def get_existing_names(layer):
    existing_names = set()
    with arcpy.da.SearchCursor(layer, ["Label"]) as cursor:
        for row in cursor:
            existing_names.add(row[0])
    return "\n- " + "\n- ".join(existing_names)

# Function to get new name from Claude
def get_new_name_from_claude(api_key, original_name, existing_names):
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    }
    
    data = {
        "model": "claude-3-opus-20240229",
        "max_tokens": 4000,
        "temperature": 0.6,
        "messages": [
            {
                "role": "user",
                "content": f"You are a creative naming assistant. Please provide a new, creative name for a Gulf of Mexico (GOM) location currently called '{original_name}'.  \n\nThe following names are already in use, so please provide a completely different name:{existing_names}. \n\nThe name should be unique, and related to marine/oceanic themes  It should be a pun or otherwise funny.. but not mean or rude. Provide only the new name with no additional explanation or context. Here is a random number for you: {random.random()}"
            }
        ]
    }
    
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json=data
    )
    
    if response.status_code == 200:
        return response.json()['content'][0]['text'].strip()
    else:
        print(response.text)
        raise Exception(f"Claude API error: {response.status_code}")

# Function to upload to Azure Blob Storage
def upload_to_blob_storage(connection_string, container_name, local_folder_path, blob_prefix):
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    container_client = blob_service_client.get_container_client(container_name)
    
    # Walk through the directory
    for root, dirs, files in os.walk(local_folder_path):
        for file in files:
            # Get the full path of the file
            file_path = os.path.join(root, file)
            
            # Calculate relative path from base folder
            relative_path = os.path.relpath(file_path, local_folder_path)
            
            # Create blob name by combining prefix and relative path
            blob_name = f"{blob_prefix}/{relative_path}".replace("\\", "/")
            
            # Upload the file
            print(f"Uploading {file_path} to {blob_name}")
            with open(file_path, "rb") as data:
                container_client.upload_blob(
                    name=blob_name,
                    data=data,
                    overwrite=True
                )
    
    print(f"Uploaded folder {local_folder_path} to container {container_name}")

# Set up the environment
project = arcpy.mp.ArcGISProject("CURRENT")
map = project.activeMap

# Get the layer
layer_name = "SpecialLabels"
layer = map.listLayers(layer_name)[0]

# Get the first record
with arcpy.da.SearchCursor(layer, ["OBJECTID", "LabelGroup", "Label", "SHAPE@"]) as cursor:
    for row in cursor:
        objectid, label_group, label, geometry = row
        break  # Just get the first record

# Create base output directory
base_output_dir = r"C:\Users\ChristopherMoravec\Documents\ArcGIS\Projects\LocalTileTest\ExportTileCaches"
if not os.path.exists(base_output_dir):
    os.makedirs(base_output_dir)

for i in range(1,2):
    try:
        # get existing records
        # Get new name from Claude
        existing_names = get_existing_names(layer)
        new_name = get_new_name_from_claude(CLAUDE_API_KEY, label, existing_names)

        # Create sanitized names
        safe_group = sanitize_name(label_group).upper()
        safe_label = sanitize_name(new_name)

        # Create group directory
        group_dir = os.path.join(base_output_dir, safe_group)
        if not os.path.exists(group_dir):
            os.makedirs(group_dir)

        # Set up paths
        vtpk_path = os.path.join(group_dir, f"{safe_label}.vtpk")
        extracted_path = os.path.join(group_dir)

        # Insert a new record with the new name but same geometry and other attributes
        with arcpy.da.InsertCursor(layer, ["SHAPE@", "LabelGroup", "Label"]) as insert_cursor:
            insert_cursor.insertRow([geometry, label_group, new_name])

        # Get the OBJECTID of the newly inserted record
        new_objectid = None
        with arcpy.da.SearchCursor(layer, ["OBJECTID", "Label"], f"Label = '{new_name}'") as search_cursor:
            for search_row in search_cursor:
                new_objectid = search_row[0]
                break

        if new_objectid is None:
            raise Exception("Failed to find newly inserted record")

        # Apply definition query to only show this point
        layer.definitionQuery = f"OBJECTID = {new_objectid}"

        # Create the vector tile package
        arcpy.management.CreateVectorTilePackage(
            map,
            vtpk_path,
            "ONLINE",
            None,
            "INDEXED",
            295828763.7957775, 
            564.248588, 
            None, 
            '', 
            ''
        )

        # Extract the package
        arcpy.management.ExtractPackage(
            vtpk_path,
            extracted_path,
            "CACHE", "EXPLODED", "READY_TO_SERVE_CACHE_DATASET", None
        )

        # Upload to Azure Blob Storage
        blob_name = f"{safe_group}/{safe_label}.vtiles"
        final_folder = os.path.join(extracted_path, safe_label+".vtiles")
        print(final_folder)
        upload_to_blob_storage(
            AZURE_CONNECTION_STRING,
            AZURE_CONTAINER_NAME,
            final_folder,
            blob_name
        )

        # Clean up
        os.remove(vtpk_path)

        # Clear the definition query
        layer.definitionQuery = ""

        print(f"Successfully processed {label_group} - {new_name}")

    except Exception as e:
        print(f"Error processing {label_group} - {label}: {str(e)}")
        # Clear the definition query in case of error
        layer.definitionQuery = ""

print("Processing complete!")