"""
Day 2: First Gemini API call.
Sends a menu-planning prompt and prints the response.
"""

import os
from dotenv import load_dotenv
from google import genai

# Load GEMINI_API_KEY from .env into environment variables
load_dotenv()

# Get the key
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("GEMINI_API_KEY not found in .env file")

# Create a client (this is how we talk to Gemini)
client = genai.Client(api_key=api_key)

# The prompt — what we ask Gemini
prompt = """You are a thoughtful family meal planner for an Indian family.

Family context:
- Husband (40s), Wife (40s) - moderate activity
- Daughter Shloka (15) - active sports player, needs higher protein
- Two grandparents (70s) - vegetarian, prefer light food

Suggest 3 South Indian breakfast options for tomorrow that work for everyone.
For each option, give:
1. Dish name
2. Brief description (one line)
3. Why it suits the family (one line)

Keep it concise."""

# Send to Gemini and get the response
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt,
)

# Print the result
print("=" * 60)
print("GEMINI MENU SUGGESTIONS")
print("=" * 60)
print(response.text)
print("=" * 60)