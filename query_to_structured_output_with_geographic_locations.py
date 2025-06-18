import requests # to make HTTP requests
import spacy # using nlp for natural language processing to extract locations and intents
import re # for regular expressions to extract patterns from the query
import folium # for creating interactive maps like route
from typing import Dict, List
from flexpolyline import decode as decode_polyline # to decode polyline strings into coordinates (lat, lang)
import here_api

nlp = spacy.load("en_core_web_sm") # load the english word model

DEFAULT_COORDINATES = {"lat": 32.7332477, "lon": -97.1117687}
DEFAULT_COUNTRY_CODE = "USA"

# ---------------- HERE API Functions ----------------

# Function to geocode a location name using HERE API
def geocode_location(location_name, api_key, country_code=None):
    url = "https://geocode.search.hereapi.com/v1/geocode"
    params = {
        "q": location_name,
        "apiKey": api_key
    }

    if country_code:
        params["in"] = f"countryCode:{country_code}"

    response = requests.get(url, params=params)
    items = response.json().get("items", [])

    if items:
        position = items[0].get("position", {})
        address = items[0].get("address", {})
        
        return {
            "lat": position.get("lat"),
            "lon": position.get("lng"),
            "countryCode": address.get("countryCode")
        }
    
    return None

# Function to search for the best places based on a query near given coordinates
def search_best_places(query, lat, lon, api_key, limit=3):
    endpoint = "https://discover.search.hereapi.com/v1/discover"
    params = {
        "q": query,
        "at": f"{lat},{lon}",
        "limit": 10,
        "apiKey": api_key
    }
    response = requests.get(endpoint, params=params)
    results = response.json().get("items", [])
    seen = set() # to track unique places
    best_places = [] # to store the best places found

    for place in results:
        # Check if the place is open
        opening_info = place.get("openingHours", [{}])[0]
        if not opening_info.get("isOpen", False):
            continue
        
        # Check if the place has a valid title and address
        unique_key = place.get("title", "") + "|" + place.get("address", {}).get("label", "")
        if unique_key in seen:
            continue

        seen.add(unique_key)

        best_places.append({
            "title": place.get("title"),
            "address": place.get("address", {}).get("label"),
            "lat": place.get("position", {}).get("lat"),
            "lon": place.get("position", {}).get("lng"),
        })

        if len(best_places) == limit:
            break

    return best_places

# Function to get route information between two coordinates using HERE API
def get_route_info(start_coords, end_coords, api_key):
    url = "https://router.hereapi.com/v8/routes"
    params = {
        "transportMode": "car",
        "origin": f"{start_coords['lat']},{start_coords['lon']}",
        "destination": f"{end_coords['lat']},{end_coords['lon']}",
        "return": "summary,polyline",
        "apiKey": api_key
    }
    response = requests.get(url, params=params)
    
    # Check if the response is successful
    if response.status_code == 200:
        data = response.json()
        
        if data.get("routes"):
            summary = data["routes"][0].get("sections", [])[0].get("summary", {})
            polyline = data["routes"][0].get("sections", [])[0].get("polyline")
            
            return {
                "distance_meters": summary.get("length"),
                "duration_seconds": summary.get("duration"),
                "polyline": polyline
            }
        
    return None

# ---------------- NLP Processing ----------------

# Function to extract locations from a query using spaCy
def extract_locations(query, exclude=[]):
    doc = nlp(query)
    return [ent.text for ent in doc.ents if ent.label_ in ["GPE", "LOC", "FACILITY"] and ent.text not in exclude]

# Function to extract waypoints from a query using regex patterns
def extract_waypoints(query):
    waypoint_patterns = [
        r"stop at ([\w\s]+)",
        r"night stay (?:at|in) ([\w\s,]+)",
        r"via ([\w\s,]+)",
        r"quick stop at ([\w\s]+)"
    ]
    waypoints = []

    for pattern in waypoint_patterns:
        matches = re.findall(pattern, query.lower())

        for match in matches:
            waypoints.extend(point.strip() for point in re.split(r",|and", match))

    return waypoints

# Function to extract distance constraints from a query
def extract_distance_constraints(query):
    pattern = r"rest stops every (\d+ ?(?:miles|mile|km|kilometers))"
    return re.findall(pattern, query.lower())

# Function to extract time constraints from a query
def extract_time_constraints(query):
    doc = nlp(query)
    times = [ent.text for ent in doc.ents if ent.label_ in ["TIME", "DATE"]]
    durations = re.findall(r"(\d+\s?(?:minutes|minute|mins|min|hours|hour|hrs|hr))", query.lower())
    return {"times": times, "durations": durations}

# Function to extract intents from a query based on keywords
def extract_intents(query: str) -> list:
    query_lower = query.lower()
    detected_intents = []
    intent_keywords = {
        "Basic Navigation": ["navigate", "route", "direction", "way to reach", "go to"],
        "Multi-Stop": ["multi-stop", "stops at", "via", "passing through", "multiple stops", "with stops"],
        "Time-Constrained": ["arrive by", "reach by", "leave at", "depart at", "by", "before", "after", "sharp"],
        "Traffic-Aware": ["avoid traffic", "traffic-free", "least traffic", "no congestion"],
        "Scenic Routing": ["scenic", "beautiful", "picturesque", "scenery"],
        "Fuel-Efficient": ["fuel-efficient", "save fuel", "economic route"],
        "Avoiding Tolls": ["avoid tolls", "no tolls", "without toll"],
        "Avoiding Highways": ["avoid highways", "no highways", "without highways"],
        "Weather-Based": ["weather", "rain", "snow", "storm", "avoid weather"],
        "EV Charging": ["ev charging", "electric charging", "charging stations", "ev stops"],
        "Emergency Routing": ["hospital", "emergency", "urgent care", "immediately"],
        "Parking Availability": ["parking", "park near", "where can i park"],
        "Shortest": ["shortest", "quickest", "fastest"],
        "Rest Stop": ["rest stop", "break every", "rest every", "stop every"],
        "Night Stay": ["night stay", "overnight", "stay in", "stay at"]
    }
    
    for intent, keywords in intent_keywords.items():
        if any(re.search(r'\b' + re.escape(kw) + r'\b', query_lower) for kw in keywords):
            detected_intents.append(intent)
    
    return detected_intents

# Function to extract start and end locations from a query
def extract_start_end_locations(query):
    start_match = re.search(r"from ([\w\s]+?) to", query.lower())
    end_match = re.search(r"to ([\w\s]+?)(,|\.| with| but| and|$)", query.lower())
    start_location = start_match.group(1).strip() if start_match else "current location"
    end_location = end_match.group(1).strip() if end_match else None
    return start_location, end_location

# ---------------- Main Function ----------------
def structured_output(query):
    api_key = here_api.apikey

    # Extract start and end locations from the query
    start_location, end_location = extract_start_end_locations(query)

    # Geocode of start and end locations
    start_geocode = geocode_location(start_location, api_key)
    if not start_geocode:
        print(f"Could not find geolocation for start location: {start_location}, using default location.")
        start_coordinates = DEFAULT_COORDINATES
        start_country_code = DEFAULT_COUNTRY_CODE
    else:
        start_coordinates = {"lat": start_geocode["lat"], "lon": start_geocode["lon"]}
        start_country_code = start_geocode.get("countryCode")

    end_geocode = geocode_location(end_location, api_key, country_code=start_country_code) if end_location else None
    if end_location and not end_geocode:
        print(f"Could not find geolocation for end location: {end_location}, using default location.")
        end_coordinates = DEFAULT_COORDINATES
    else:
        end_coordinates = {"lat": end_geocode["lat"], "lon": end_geocode["lon"]} if end_geocode else None

    # Extract locations, constraints and waypoints from the query
    locations = extract_locations(query, exclude=[start_location, end_location])
    waypoints = extract_waypoints(query)
    unique_waypoints = [w for w in waypoints if w not in locations]

    # Route between start and end coordinates
    route_info = get_route_info(start_coordinates, end_coordinates, api_key) if start_coordinates and end_coordinates else None

    # Get coordinates for every unique stops
    enhanced_waypoints = {}
    if start_coordinates:
        for wp in unique_waypoints:
            best = search_best_places(wp, start_coordinates["lat"], start_coordinates["lon"], api_key)
            if best:
                enhanced_waypoints[wp] = best

    return {
        "intents": extract_intents(query),
        "start_location": start_location,
        "start_coordinates": start_coordinates,
        "end_location": end_location,
        "end_coordinates": end_coordinates,
        "waypoints": unique_waypoints,
        "enhanced_waypoints": enhanced_waypoints,
        "distance_constraints": extract_distance_constraints(query),
        "time_constraints": extract_time_constraints(query),
        "route_info": route_info,
    }

# ---------------- Utility Functions ----------------

# to decode polyline strings into coordinates and retyrn a google maps url
def generate_google_maps_link(start_coords, end_coords):
    start = f"{start_coords['lat']},{start_coords['lon']}"
    end = f"{end_coords['lat']},{end_coords['lon']}"
    return f"https://www.google.com/maps/dir/?api=1&origin={start}&destination={end}&travelmode=driving"

# ---------------- Test Queries ----------------

queries = [
    "Plan a trip from Dallas to Austin with a stop at a Walmart and a coffee shop.",
    "Navigate from New York to Philadelphia and avoid highways, stop at a gas station and pharmacy.",
    "Drive from San Francisco to Napa Valley with scenic views and a night stay in Sonoma.",
    "Plan a long road trip from New York to Los Angeles with rest stops every 300 miles and a night stay in Chicago and Denver.",
    "Find the shortest route from my house to the airport with a quick stop at a nearby ATM.",
    "Show me a scenic drive from San Francisco to Yosemite National Park with a stop at a famous viewpoint.",
    "Navigate from Dallas to Austin avoiding tolls and highways, prefer fuel-efficient route with EV charging every 150 miles.",
    "I need to urgently reach a hospital from my office due to heavy snow and avoid traffic.",
    "Plan a trip from Seattle to Portland, include scenic views, parking availability near downtown, and rest stops every 100 miles."
]

for q in queries:
    print(f"\nQuery: {q}")
    result = structured_output(q)
    
    if result:
        print("Structured Output:", result)
        print("\nRoute Summary:")
        print("From:", result["start_location"], result["start_coordinates"])
        print("To:", result["end_location"], result["end_coordinates"])
        
        if result["route_info"]:
            print("Distance (km):", result["route_info"]["distance_meters"] / 1000)
            print("Estimated Time (min):", result["route_info"]["duration_seconds"] / 60)

            # Decode polyline
            decoded_points = decode_polyline(result["route_info"]["polyline"])
            print("Route Coordinates (first 5 points):", decoded_points[:5])

            # Google Maps Link
            google_maps_link = generate_google_maps_link(result["start_coordinates"], result["end_coordinates"])
            print(f"🗺️ Google Maps Link: {google_maps_link}")

            # Folium map
            m = folium.Map(location=decoded_points[0], zoom_start=7)
            folium.PolyLine(decoded_points, color="blue", weight=5).add_to(m)

            # Add Start Marker
            folium.Marker(location=[result["start_coordinates"]["lat"], result["start_coordinates"]["lon"]],
                          popup="Start", icon=folium.Icon(color='green')).add_to(m)

            # Add End Marker
            folium.Marker(location=[result["end_coordinates"]["lat"], result["end_coordinates"]["lon"]],
                          popup="End", icon=folium.Icon(color='red')).add_to(m)

            # Add Waypoints/Stops
            for place_type, places in result["enhanced_waypoints"].items():
                for place in places:
                    folium.Marker(
                        location=[place["lat"], place["lon"]],
                        popup=f"{place_type}: {place['title']}",
                        icon=folium.Icon(color='blue', icon='info-sign')
                    ).add_to(m)

        else:
            print("No route information available.")
