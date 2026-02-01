#!/usr/bin/env python3
"""
Debug the deduplication logic
"""

import re

def _extract_key_phrases(text: str) -> set:
    """
    Extract key phrases from limitation text for fuzzy matching.
    Returns a set of important keywords/phrases.
    """
    if not text:
        return set()
    
    # Uppercase for consistency
    text = text.upper()
    
    # Remove JSON artifacts
    text = re.sub(r'"[A-Z_]+"\s*:\s*(?:"[^"]*"|[\d.]+)\s*,?\s*', '', text)
    text = re.sub(r'[\{\}\[\]\(\)]', '', text)
    
    # Key aviation phrases to detect
    key_phrases = []
    
    # ELT related
    if 'ELT' in text or 'E.L.T' in text:
        key_phrases.append('ELT')
    if 'REMOVED' in text and ('CERTIFICATION' in text or '25' in text):
        key_phrases.append('ELT_REMOVED')
    if 'LIMITED TO 25' in text or '25 N.M' in text or '25 NM' in text:
        key_phrases.append('LIMITED_25NM')
    
    # Avionics related
    if 'PITOT' in text or 'STATIC' in text:
        key_phrases.append('PITOT_STATIC')
    if 'TRANSPONDER' in text:
        key_phrases.append('TRANSPONDER')
    if 'ENCODER' in text:
        key_phrases.append('ENCODER')
    if '24 MONTH' in text or '24MONTH' in text:
        key_phrases.append('24_MONTH_INSPECTION')
    if 'OVERDUE' in text:
        key_phrases.append('OVERDUE')
    if 'CONTROL ZONE' in text:
        key_phrases.append('CONTROL_ZONE')
    
    # First Aid Kit
    if 'FIRST AID' in text or 'FIRSTAID' in text:
        key_phrases.append('FIRST_AID_KIT')
    if 'SERVICEABLE' in text or 'SERVICIABLE' in text:
        key_phrases.append('SERVICEABLE')
    
    # Fire extinguisher
    if 'FIRE' in text and 'EXTINGUISHER' in text:
        key_phrases.append('FIRE_EXTINGUISHER')
    
    # If no specific phrases found, use first 50 chars normalized
    if not key_phrases:
        # Clean and take first significant words
        cleaned = re.sub(r'[^A-Z0-9\s]', '', text)
        words = cleaned.split()[:6]
        key_phrases.append('_'.join(words))
    
    return set(key_phrases)


def _normalize_limitation_text(text: str) -> str:
    """
    Normalize limitation text for deduplication.
    Creates a canonical key based on important content.
    """
    if not text:
        return ""
    
    # Uppercase
    text = text.upper()
    
    # Remove JSON-like patterns
    text = re.sub(r'"[A-Z_]+"\s*:\s*(?:"[^"]*"|[\d.]+)\s*,?\s*', '', text)
    text = re.sub(r'[\{\}\[\]\(\)]', '', text)
    
    # Remove common noise words and punctuation
    text = re.sub(r'[.,;:\'"!?]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    
    # Extract key phrases and sort them for consistent hashing
    phrases = sorted(_extract_key_phrases(text))
    
    if phrases:
        return '|'.join(phrases)
    
    # Fallback: first 60 chars of cleaned text
    return text[:60]


# Test the ELT entries
elt_texts = [
    "ELT removed for certification - limited to 25 N.M. from aerodrome",
    "E.L.T REMOVED FOR CERTIFICATION LIMITED TO 25 NM FROM AERODROME"
]

print("🔍 Debugging ELT Deduplication:")
print("=" * 50)

for i, text in enumerate(elt_texts, 1):
    print(f"\nELT Text {i}: {text}")
    phrases = _extract_key_phrases(text)
    normalized = _normalize_limitation_text(text)
    print(f"  Key phrases: {sorted(phrases)}")
    print(f"  Normalized key: '{normalized}'")

print("\n" + "=" * 50)
print("Analysis:")
key1 = _normalize_limitation_text(elt_texts[0])
key2 = _normalize_limitation_text(elt_texts[1])

if key1 == key2:
    print("✅ Keys match - deduplication should work")
else:
    print("❌ Keys don't match - deduplication won't work")
    print(f"Key 1: '{key1}'")
    print(f"Key 2: '{key2}'")

# Test fire extinguisher entries
print("\n🔍 Debugging Fire Extinguisher Deduplication:")
print("=" * 50)

fire_texts = [
    "FIRE EXTINGUISHER NOT SERVICEABLE",
    "Fire extinguisher not serviciable"
]

for i, text in enumerate(fire_texts, 1):
    print(f"\nFire Text {i}: {text}")
    phrases = _extract_key_phrases(text)
    normalized = _normalize_limitation_text(text)
    print(f"  Key phrases: {sorted(phrases)}")
    print(f"  Normalized key: '{normalized}'")

print("\n" + "=" * 50)
print("Analysis:")
key1 = _normalize_limitation_text(fire_texts[0])
key2 = _normalize_limitation_text(fire_texts[1])

if key1 == key2:
    print("✅ Keys match - deduplication should work")
else:
    print("❌ Keys don't match - deduplication won't work")
    print(f"Key 1: '{key1}'")
    print(f"Key 2: '{key2}'")