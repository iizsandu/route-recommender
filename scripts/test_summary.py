from pymongo import MongoClient
import random

client = MongoClient("mongodb://localhost:27017/")
db = client["crime2"]

# Pull 10 records that have both structured info and article text
pipeline = [
    {"$match": {"is_crime": True, "lat": {"$ne": None}, "lng": {"$ne": None}}},
    {"$sample": {"size": 10}}
]
extracted = list(db["extracted"].aggregate(pipeline))

for rec in extracted:
    url = rec.get("url", "")
    article = db["articles2"].find_one({"url": url})
    
    print("=== STRUCTURED ===")
    print(f"crime_type: {rec.get('crime_type')}")
    print(f"location:   {rec.get('location_exact')}")
    print(f"date:       {rec.get('crime_date')}")
    print(f"\n=== ARTICLE (first 400 chars) ===")
    text = article.get("text", "") if article else ""
    print(text[:400] if text else "[NO ARTICLE FOUND]")
    print("\n" + "="*60 + "\n")
