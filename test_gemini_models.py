"""
Quick script to test Gemini API and list available models
"""
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
print(f"API Key: {api_key[:20]}...")

client = genai.Client(api_key=api_key)

print("\n" + "=" * 70)
print("Testing Gemini API Connection")
print("=" * 70)

# Test different model names
model_names = [
    "gemini-2.0-flash-exp",
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-pro",
    "gemini-pro",
    "gemini-flash"
]

for model_name in model_names:
    try:
        print(f"\nTesting: {model_name}")
        response = client.models.generate_content(
            model=model_name,
            contents="Say 'Hello, I am working!'"
        )
        print(f"✅ SUCCESS - {model_name}")
        print(f"   Response: {response.text[:50]}...")
        break  # Stop on first success
    except Exception as e:
        print(f"❌ FAILED - {model_name}")
        print(f"   Error: {str(e)[:100]}")

print("\n" + "=" * 70)
