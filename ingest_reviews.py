import json
import os
import re
from datetime import datetime, timedelta
import pandas as pd
from google_play_scraper import reviews, Sort
from langdetect import detect, DetectorFactory
import emoji

# Ensure consistent language detection results
DetectorFactory.seed = 0

def pii_filter(text):
    """Redacts emails and phone numbers from text."""
    # Email pattern
    email_pattern = r'\S+@\S+'
    # Basic phone number pattern (liberal for various formats)
    phone_pattern = r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'
    
    text = re.sub(email_pattern, '[REDACTED_EMAIL]', text)
    text = re.sub(phone_pattern, '[REDACTED_PHONE]', text)
    return text

def is_english(text):
    """Detects if the text is English."""
    try:
        return detect(text) == 'en'
    except:
        return False

def clean_review(review_text):
    """Applies cleaning rules: word count, emojis, and language."""
    # 1. Check word count (> 5 words)
    words = review_text.split()
    if len(words) < 5:
        return None, "Too short"
    
    # 2. Check for emojis
    if any(char in emoji.EMOJI_DATA for char in review_text):
        return None, "Contains emojis"
    
    # 3. Check language (English only)
    if not is_english(review_text):
        return None, "Not English"
    
    # 4. PII Redaction
    cleaned_text = pii_filter(review_text)
    
    return cleaned_text, None

def fetch_and_save_reviews(app_id='com.nextbillion.groww', weeks_requested=12, max_count=2000):
    print(f"Fetching reviews for {app_id}...")
    
    # 1. Fetch reviews
    result, continuation_token = reviews(
        app_id,
        lang='en',
        country='in',
        sort=Sort.NEWEST,
        count=max_count
    )
    
    cutoff_date = datetime.now() - timedelta(weeks=weeks_requested)
    
    # 2. Process and Clean
    processed_reviews = []
    stats = {"total": len(result), "kept": 0, "too_short": 0, "emojis": 0, "not_english": 0, "older_than_cutoff": 0}
    
    for i, r in enumerate(result):
        # Check date
        if r['at'] < cutoff_date:
            stats["older_than_cutoff"] += 1
            continue
            
        cleaned_text, failure_reason = clean_review(r['content'])
        
        if cleaned_text:
            processed_reviews.append({
                'review_id': f"rev_{i}",
                'review_text': cleaned_text,
                'rating': r['score'],
                'review_date': r['at'].isoformat(),
                'helpful_count': r['thumbsUpCount']
            })
            stats["kept"] += 1
        else:
            if failure_reason == "Too short": stats["too_short"] += 1
            elif failure_reason == "Contains emojis": stats["emojis"] += 1
            elif failure_reason == "Not English": stats["not_english"] += 1

    print(f"Stats: {stats}")
    
    # 3. Save to JSON
    today = datetime.now().strftime('%Y-%m-%d')
    output_dir = 'data/phase_1_ingestion'
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{today}.json")
    
    output_payload = {
        "metadata": {
            "scrapedAt": datetime.now().isoformat(),
            "packageId": app_id,
            "weeksRequested": weeks_requested,
            "count": len(processed_reviews),
            "filtering_stats": stats
        },
        "reviews": processed_reviews
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_payload, f, indent=4, ensure_ascii=False)
    
    print(f"Saved {len(processed_reviews)} cleaned reviews to {output_path}")
    return output_path

if __name__ == "__main__":
    fetch_and_save_reviews()
