import requests
import spacy
import re
from typing import Dict, List
import here_api

nlp = spacy.load("en_core_web_sm")

# ---------------- HERE API Functions ----------------

# Geocode a given location string to latitude and longitude using HERE API
def geocode_location(location_name, api_key):
    url = "https://geocode.search.hereapi.com/v1/geocode"
    params = {
        "q": location_name,
        "apiKey": api_key
    }
    response = requests.get(url, params=params)
    items = response.json().get("items", [])
    if items:
        position = items[0].get("position", {})
        return {"lat": position.get("lat"), "lon": position.get("lng")}
    return None

# Search best matching places (e.g., Walmart, cafe) around a coordinate using HERE API
def search_best_places(query, lat, lon, api_key, limit=2):
    endpoint = "https://discover.search.hereapi.com/v1/discover"
    params = {
        "q": query,
        "at": f"{lat},{lon}",
        "limit": 10,  # fetch more for deduplication and filtering
        "apiKey": api_key
    }
    response = requests.get(endpoint, params=params)
    results = response.json().get("items", [])

    seen = set()
    best_places = []

    for place in results:
        # Only include places that are currently open
        opening_info = place.get("openingHours", [{}])[0]
        if not opening_info.get("isOpen", False):
            continue

        # Avoid duplicates based on title + address
        unique_key = place.get("title", "") + "|" + place.get("address", {}).get("label", "")
        if unique_key in seen:
            continue
        seen.add(unique_key)

        # Append filtered place
        best_places.append({
            "title": place.get("title"),
            "address": place.get("address", {}).get("label"),
            "lat": place.get("position", {}).get("lat"),
            "lon": place.get("position", {}).get("lng"),
        })

        if len(best_places) == limit:
            break

    return best_places

# ---------------- NLP Processing ----------------

# Extract named locations, excluding start/end if specified
def extract_locations(query, exclude=[]):
    doc = nlp(query)
    locations = [ent.text for ent in doc.ents if ent.label_ in ["GPE", "LOC", "FACILITY"] and ent.text not in exclude]
    return locations

# Extract intermediate stops from user phrases like "stop at", "via", etc.
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
            extracted_points = [point.strip() for point in re.split(r",|and", match)]
            waypoints.extend(extracted_points)
    return waypoints

# Detect distance-related constraints such as rest stops every X miles/km
def extract_distance_constraints(query):
    pattern = r"rest stops every (\d+ ?(?:miles|mile|km|kilometers))"
    matches = re.findall(pattern, query.lower())
    return matches

# Extract time-related constraints like arrival times or duration of stays
def extract_time_constraints(query):
    doc = nlp(query)
    times = [ent.text for ent in doc.ents if ent.label_ in ["TIME", "DATE"]]
    duration_pattern = r"(\d+\s?(?:minutes|minute|mins|min|hours|hour|hrs|hr))"
    durations = re.findall(duration_pattern, query.lower())
    return {"times": times, "durations": durations}

# Match user intent using keyword mapping (e.g., "avoid traffic" → Traffic-Aware)
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
        for keyword in keywords:
            if re.search(r'\b' + re.escape(keyword) + r'\b', query_lower):
                detected_intents.append(intent)
                break
    return detected_intents

# Extract clearly marked start and end locations ("from X to Y")
def extract_start_end_locations(query):
    start_match = re.search(r"from ([\w\s]+?) to", query.lower())
    end_match = re.search(r"to ([\w\s]+?)(,|\.| with| but| and|$)", query.lower())
    start_location = start_match.group(1).strip() if start_match else "current location"
    end_location = end_match.group(1).strip() if end_match else None
    return start_location, end_location

# Main function that ties all components together into a structured result
def structured_output(query):
    api_key = here_api.apikey
    start_location, end_location = extract_start_end_locations(query)
    start_coordinates = geocode_location(start_location, api_key)
    end_coordinates = geocode_location(end_location, api_key) if end_location else None

    locations = extract_locations(query, exclude=[start_location, end_location])
    waypoints = extract_waypoints(query)
    unique_waypoints = [w for w in waypoints if w not in locations]

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
        "time_constraints": extract_time_constraints(query)
    }

# ---------------- Test Queries ----------------

queries = [
    "Plan a trip from Dallas to Austin with a stop at a Walmart and a coffee shop.",
    "Navigate from New York to Philadelphia and avoid highways, stop at a gas station and pharmacy.",
    "Drive from San Francisco to Napa Valley with scenic views and a night stay in Sonoma."
    "Plan a long road trip from New York to Los Angeles with rest stops every 300 miles and a night stay in Chicago and Denver.",
    "Find the shortest route from my house to the airport with a quick stop at a nearby ATM.",
    "Show me a scenic drive from San Francisco to Yosemite National Park with a stop at a famous viewpoint.",
    "Navigate from Dallas to Austin avoiding tolls and highways, prefer fuel-efficient route with EV charging every 150 miles.",
    "I need to urgently reach a hospital from my office due to heavy snow and avoid traffic.",
    "Plan a trip from Seattle to Portland, include scenic views, parking availability near downtown, and rest stops every 100 miles."
]

for q in queries:
    print(f"\nQuery: {q}")
    print("Structured Output:", structured_output(q))
