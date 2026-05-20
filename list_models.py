"""
List available Gemini models
"""
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
print(f"API Key: {api_key[:20]}...")

try:
    client = genai.Client(api_key=api_key)

    print("\n" + "=" * 70)
    print("Available Gemini Models:")
    print("=" * 70)

    # Try to list models
    models = client.models.list()

    for model in models:
        print(f"  • {model.name}")
        if hasattr(model, 'supported_generation_methods'):
            print(f"    Methods: {model.supported_generation_methods}")

    print("=" * 70)

except Exception as e:
    print(f"\n❌ Error listing models: {e}")
    print("\nTrying alternative approach...")

    # Try using the REST API directly
    import requests

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    response = requests.get(url)

    if response.status_code == 200:
        data = response.json()
        print("\n" + "=" * 70)
        print("Available Models (from REST API):")
        print("=" * 70)
        for model in data.get('models', []):
            name = model.get('name', '').replace('models/', '')
            print(f"  • {name}")
        print("=" * 70)
    else:
        print(f"Error: {response.status_code} - {response.text}")
