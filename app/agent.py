from dotenv import load_dotenv
load_dotenv()
import datetime
import urllib.request
import urllib.parse
import json
import os
import subprocess
from typing import Optional, List, Dict, Any
from zoneinfo import ZoneInfo

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.memory import VertexAiMemoryBankService
from google.genai import Client, types

from a2ui.schema.manager import A2uiSchemaManager
from a2ui.basic_catalog.provider import BasicCatalog
from .a2ui_utils import a2ui_callback

PROJECT_ID = "qwiklabs-gcp-02-86fbf5f50ecb"
GCS_BUCKET_NAME = "qwiklabs-gcp-02-86fbf5f50ecb-plant-nursery-assets"
AGENT_ENGINE_ID = "6408081310027350016"

# Instantiated memory service for Vertex AI Agent Engine Memory Bank
memory_service = VertexAiMemoryBankService(
    project=PROJECT_ID,
    location="us-east4",
    agent_engine_id=AGENT_ENGINE_ID,
)

# Build system prompt using A2uiSchemaManager version 0.8 and BasicCatalog
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description="You are a helpful Plant Nursery Assistant. You can search the plant inventory, provide plant care guides, check local weather for plant watering advice, geocode addresses into coordinates, locate nearby florists or plant nurseries, generate visual plant images, and add new plants to the store database.",
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects. "
        "Important instruction: Always remember and take note of any user allergies mentioned during conversation, "
        "and avoid recommending any plants that may trigger or affect the user's specified allergies."
    ),
    include_schema=True,
    include_examples=True,
)


def _get_gcloud_token() -> str:
    """Helper to fetch access token for Firestore API calls."""
    cmd = ["gcloud", "auth", "print-access-token"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def search_plants(category: Optional[str] = None, max_price: Optional[float] = None) -> List[Dict[str, Any]]:
    """Search for plants in the plant nursery inventory with optional filters.

    Args:
        category: Optional category filter (e.g. 'Tropical', 'Succulent', 'Tree', 'Flowering').
        max_price: Optional maximum price filter in USD.

    Returns:
        A list of matching plant dictionaries containing plant details.
    """
    token = _get_gcloud_token()
    url = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents/plants"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return [{"error": f"Failed to fetch plant inventory: {str(e)}"}]
        
    results = []
    documents = data.get("documents", [])
    for doc in documents:
        fields = doc.get("fields", {})
        plant = {
            "id": fields.get("id", {}).get("stringValue", ""),
            "name": fields.get("name", {}).get("stringValue", ""),
            "category": fields.get("category", {}).get("stringValue", ""),
            "price": float(fields.get("price", {}).get("doubleValue", 0.0) or fields.get("price", {}).get("integerValue", 0)),
            "stock": int(fields.get("stock", {}).get("integerValue", 0)),
            "care_level": fields.get("care_level", {}).get("stringValue", ""),
            "sunlight": fields.get("sunlight", {}).get("stringValue", ""),
            "description": fields.get("description", {}).get("stringValue", "")
        }
        
        if category and category.lower() not in plant["category"].lower():
            continue
        if max_price is not None and plant["price"] > max_price:
            continue
        results.append(plant)
        
    return results


def add_plant(
    plant_id: str,
    name: str,
    category: str,
    price: float,
    stock: int,
    care_level: str = "Easy",
    sunlight: str = "Indirect Light",
    description: str = ""
) -> str:
    """Add a new plant document or update an existing plant in the nursery inventory.

    Args:
        plant_id: Unique string identifier for the plant (e.g., 'pothos_golden').
        name: Display name of the plant (e.g., 'Golden Pothos').
        category: Plant category (e.g., 'Tropical', 'Succulent', 'Vining').
        price: Price of the plant in USD.
        stock: Available quantity in stock.
        care_level: Care difficulty level ('Easy', 'Moderate', 'Expert').
        sunlight: Sunlight requirement ('Low Light', 'Indirect Light', 'Full Sun').
        description: Short description of the plant.

    Returns:
        Status message confirming insertion/update.
    """
    token = _get_gcloud_token()
    url = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents/plants/{plant_id}"
    
    fields = {
        "id": {"stringValue": plant_id},
        "name": {"stringValue": name},
        "category": {"stringValue": category},
        "price": {"doubleValue": float(price)},
        "stock": {"integerValue": str(stock)},
        "care_level": {"stringValue": care_level},
        "sunlight": {"stringValue": sunlight},
        "description": {"stringValue": description}
    }
    
    body = json.dumps({"fields": fields}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="PATCH"
    )
    
    try:
        with urllib.request.urlopen(req) as resp:
            return f"Successfully added/updated plant '{name}' (ID: {plant_id}) in inventory."
    except Exception as e:
        return f"Error adding plant to inventory: {str(e)}"


def get_care_guide(plant_name: str) -> Dict[str, Any]:
    """Get detailed care instructions and tips for a specific plant.

    Args:
        plant_name: The name or type of plant (e.g., 'Monstera Deliciosa', 'Snake Plant', 'Peace Lily').

    Returns:
        A dictionary containing watering, sunlight, soil, toxicity, and care tips.
    """
    token = _get_gcloud_token()
    url = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents/plants"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            documents = data.get("documents", [])
            for doc in documents:
                fields = doc.get("fields", {})
                p_name = fields.get("name", {}).get("stringValue", "")
                if plant_name.lower() in p_name.lower():
                    return {
                        "plant": p_name,
                        "care_level": fields.get("care_level", {}).get("stringValue", "Easy"),
                        "sunlight": fields.get("sunlight", {}).get("stringValue", "Indirect Light"),
                        "watering": "Water when top 1-2 inches of soil feels dry.",
                        "pet_friendly": False if "monstera" in p_name.lower() or "peace" in p_name.lower() or "snake" in p_name.lower() else True,
                        "description": fields.get("description", {}).get("stringValue", "")
                    }
    except Exception:
        pass

    return {
        "plant": plant_name,
        "care_level": "Easy",
        "sunlight": "Medium to Bright Indirect Light",
        "watering": "Water thoroughly when top inch of soil is dry. Avoid overwatering.",
        "pet_friendly": True,
        "description": f"General care guide for {plant_name}."
    }


def get_local_weather_and_gardening_advice(latitude: float = 37.7749, longitude: float = -122.4194) -> Dict[str, Any]:
    """Get current weather conditions and temperature for a given latitude and longitude using Open-Meteo public API.

    Args:
        latitude: Latitude coordinate (default is 37.7749 for San Francisco).
        longitude: Longitude coordinate (default is -122.4194 for San Francisco).

    Returns:
        A dictionary containing temperature (°C), windspeed, and weather condition codes for outdoor plant care.
    """
    url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current_weather=true"
    req = urllib.request.Request(url, headers={"User-Agent": "PlantNurseryAgent/1.0"})
    
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            current = data.get("current_weather", {})
            temp = current.get("temperature", 20.0)
            
            advice = "Weather is mild. Suitable for standard outdoor watering."
            if temp < 10.0:
                advice = "Cold warning! Bring sensitive tropical indoor plants inside."
            elif temp > 30.0:
                advice = "Heat advisory! Increase watering frequency and protect plants from direct midday sun."
                
            return {
                "latitude": latitude,
                "longitude": longitude,
                "temperature_celsius": temp,
                "windspeed": current.get("windspeed"),
                "weathercode": current.get("weathercode"),
                "gardening_advice": advice
            }
    except Exception as e:
        return {"error": f"Failed to fetch live weather data: {str(e)}"}


def geocode_address(address: str) -> Dict[str, Any]:
    """Geocode a physical address or city name into latitude and longitude coordinates using Google Maps Geocoding API.

    Args:
        address: The location, city, or street address to geocode (e.g. '1600 Amphitheatre Pkwy, Mountain View, CA' or 'San Francisco, CA').

    Returns:
        A dictionary containing key fields: name (formatted address), address, and location (latitude, longitude).
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return {"error": "GOOGLE_MAPS_API_KEY environment variable is not configured."}
        
    encoded_addr = urllib.parse.quote(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_addr}&key={api_key}"
    req = urllib.request.Request(url)
    
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "OK" and data.get("results"):
                first_res = data["results"][0]
                formatted_addr = first_res.get("formatted_address", address)
                location = first_res.get("geometry", {}).get("location", {})
                return {
                    "name": formatted_addr,
                    "address": formatted_addr,
                    "location": {
                        "latitude": location.get("lat"),
                        "longitude": location.get("lng")
                    }
                }
            return {"error": f"Geocoding failed with status: {data.get('status')}"}
    except Exception as e:
        return {"error": f"Failed to execute geocoding request: {str(e)}"}


def search_nearby_places(latitude: float, longitude: float, place_type: str = "florist", radius_meters: float = 5000.0) -> List[Dict[str, Any]]:
    """Search for nearby places of a given type around a location using Google Places API (New).

    Args:
        latitude: Center point latitude.
        longitude: Center point longitude.
        place_type: Type of place to search for (e.g., 'florist', 'park', 'store').
        radius_meters: Search radius in meters (default 5000.0 meters).

    Returns:
        A list of place dictionaries containing key fields: name, address, and location.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return [{"error": "GOOGLE_MAPS_API_KEY environment variable is not configured."}]
        
    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location"
    }
    
    body = {
        "includedTypes": [place_type],
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": latitude,
                    "longitude": longitude
                },
                "radius": radius_meters
            }
        }
    }
    
    req_data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
    
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = []
            places = data.get("places", [])
            for place in places:
                display_name = place.get("displayName", {}).get("text", "")
                formatted_addr = place.get("formattedAddress", "")
                loc = place.get("location", {})
                results.append({
                    "name": display_name,
                    "address": formatted_addr,
                    "location": {
                        "latitude": loc.get("latitude"),
                        "longitude": loc.get("longitude")
                    }
                })
            return results
    except Exception as e:
        return [{"error": f"Failed to search nearby places: {str(e)}"}]


async def generate_plant_image(
    prompt: str,
    tool_context: ToolContext
) -> Dict[str, Any]:
    """Generate an image for a plant item using gemini-3.1-flash-lite-image model, save as session artifact, and upload to public Cloud Storage.

    Args:
        prompt: Detailed description of the plant image to generate (e.g. 'A lush Monstera Deliciosa with split green leaves in a terracotta pot').
        tool_context: ADK ToolContext for saving session artifacts.

    Returns:
        A dictionary containing the image generation status, prompt, and public Cloud Storage URL.
    """
    client = Client(vertexai=True, project=PROJECT_ID, location="global")
    
    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"]
            )
        )
        
        image_bytes = None
        mime_type = "image/jpeg"
        
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    image_bytes = part.inline_data.data
                    if part.inline_data.mime_type:
                        mime_type = part.inline_data.mime_type
                    break
                    
        if not image_bytes:
            return {"error": "No image data was generated by the model."}

        # 1. Save artifact to ADK ToolContext if artifact service is active
        filename = f"plant_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        
        try:
            await tool_context.save_artifact(filename=filename, artifact=artifact_part)
        except Exception:
            pass  # Fallback gracefully if running in a test runner without initialized ArtifactService

        # 2. Upload same image bytes to public Cloud Storage bucket
        token = _get_gcloud_token()
        obj_name = f"generated_plants/{filename}"
        upload_url = f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET_NAME}/o?uploadType=media&name={obj_name}"
        
        req = urllib.request.Request(
            upload_url,
            data=image_bytes,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": mime_type
            },
            method="POST"
        )
        
        with urllib.request.urlopen(req) as resp:
            pass

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{obj_name}"
        
        return {
            "status": "success",
            "prompt": prompt,
            "artifact_filename": filename,
            "public_url": public_url
        }

    except Exception as e:
        return {"error": f"Failed to generate and upload plant image: {str(e)}"}


async def generate_plant_video(
    prompt: str,
    tool_context: ToolContext
) -> Dict[str, Any]:
    """Generate a short video for a plant item using Google's Omni model (gemini-omni-flash-preview), save as session artifact, and upload to public Cloud Storage.

    Args:
        prompt: Detailed description of the plant video to generate (e.g. 'A time-lapse video of a sunflower opening its yellow petals under morning sunlight').
        tool_context: ADK ToolContext for saving session artifacts.

    Returns:
        A dictionary containing the video generation status, prompt, artifact filename, and public Cloud Storage URL.
    """
    client = Client(vertexai=True, project=PROJECT_ID, location="global")
    
    try:
        response = client.models.generate_content(
            model="gemini-omni-flash-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["VIDEO", "TEXT"]
            )
        )
        
        video_bytes = None
        mime_type = "video/mp4"
        
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    video_bytes = part.inline_data.data
                    if part.inline_data.mime_type:
                        mime_type = part.inline_data.mime_type
                    break
                    
        if not video_bytes:
            return {"error": "No video data was generated by the model."}

        # 1. Save artifact to ADK ToolContext if artifact service is active
        ext = "mp4" if "mp4" in mime_type else "webm"
        filename = f"plant_video_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
        artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
        
        try:
            await tool_context.save_artifact(filename=filename, artifact=artifact_part)
        except Exception:
            pass  # Fallback gracefully if running in a test runner without initialized ArtifactService

        # 2. Upload same video bytes to public Cloud Storage bucket
        token = _get_gcloud_token()
        obj_name = f"generated_plant_videos/{filename}"
        upload_url = f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET_NAME}/o?uploadType=media&name={obj_name}"
        
        req = urllib.request.Request(
            upload_url,
            data=video_bytes,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": mime_type
            },
            method="POST"
        )
        
        with urllib.request.urlopen(req) as resp:
            pass

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{obj_name}"
        
        return {
            "status": "success",
            "prompt": prompt,
            "artifact_filename": filename,
            "public_url": public_url
        }

    except Exception as e:
        return {"error": f"Failed to generate and upload plant video: {str(e)}"}


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        vertexai=True,
        project=PROJECT_ID,
        location="us-east4",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    tools=[
        search_plants,
        add_plant,
        get_care_guide,
        get_local_weather_and_gardening_advice,
        geocode_address,
        search_nearby_places,
        generate_plant_image,
        generate_plant_video,
    ],
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
