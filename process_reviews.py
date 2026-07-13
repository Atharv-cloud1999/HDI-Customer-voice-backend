import os
import json
import time
import random
from datetime import datetime
from dotenv import load_dotenv
from groq import Groq

# Load environment variables
load_dotenv(override=True)

class ReviewProcessor:
    def __init__(self, model="llama-3.3-70b-versatile"):
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError("GROQ_API_KEY not found in .env file")
        self.client = Groq(api_key=self.api_key)
        self.model = model

    def load_latest_reviews(self, review_dir="data/phase_1_ingestion"):
        """Loads the most recent review file from the data/reviews directory."""
        files = [f for f in os.listdir(review_dir) if f.endswith(".json")]
        if not files:
            raise FileNotFoundError("No review files found in data/reviews")
        latest_file = sorted(files)[-1]
        with open(os.path.join(review_dir, latest_file), 'r', encoding='utf-8') as f:
            return json.load(f), latest_file.split('.')[0]

    def sample_reviews(self, reviews, min_sample=100, max_sample=150):
        """Samples 100-150 reviews with a balanced distribution across ratings."""
        ratings_buckets = {1: [], 2: [], 3: [], 4: [], 5: []}
        for r in reviews:
            ratings_buckets[r['rating']].append(r)
        
        sampled = []
        per_rating = max_sample // 5
        
        for rating in range(1, 6):
            bucket = ratings_buckets[rating]
            num_to_sample = min(len(bucket), per_rating)
            sampled.extend(random.sample(bucket, num_to_sample))
            
        # If we need more to reach min_sample, fill with remaining reviews
        if len(sampled) < min_sample:
            remaining = [r for r in reviews if r not in sampled]
            num_additional = min(len(remaining), min_sample - len(sampled))
            sampled.extend(random.sample(remaining, num_additional))
            
        return sampled[:max_sample]

    def call_groq_with_retry(self, messages, response_format=None, max_retries=5):
        """Calls Groq API with exponential backoff for rate limits."""
        retries = 0
        while retries < max_retries:
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format=response_format,
                    temperature=0.1,
                )
                return response.choices[0].message.content
            except Exception as e:
                error_msg = str(e).lower()
                # Handle rate limits (often 429) or overloaded server (503)
                if "rate_limit_exceeded" in error_msg or "429" in error_msg or "overloaded" in error_msg:
                    # Adaptive wait time: 5, 10, 20, 40, 80 seconds + jitter
                    wait_time = (5 * (2 ** retries)) + (random.random() * 5)
                    print(f"Groq API limited/overloaded. Retrying in {wait_time:.2f}s (Attempt {retries + 1}/{max_retries})...")
                    time.sleep(wait_time)
                    retries += 1
                else:
                    # For other errors, raise immediately
                    raise e
        raise Exception("Max retries exceeded for Groq API after persistent rate limiting.")

    def discover_themes(self, sampled_reviews):
        """Phase 2: Extract 3-5 themes from a sample of reviews."""
        reviews_text = "\n".join([f"- [{r['review_id']}] ({r['rating']} stars): {r['review_text']}" for r in sampled_reviews])
        
        system_msg = (
            "You are a product researcher. Extract 3-5 high-level themes from these fintech app reviews. "
            "Return the themes in a valid JSON format as a list of objects. "
            "Each object must have: 'theme id' (kebab-case), 'theme label' (short name), and 'short description'. "
            "Example: [{\"theme id\": \"ui-bugs\", \"theme label\": \"UI Bugs\", \"short description\": \"Users reporting lag or glitches in the interface.\"}]"
        )
        
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"Reviews:\n{reviews_text}"}
        ]
        
        # Phase 2 requirement: Retry once if invalid JSON
        for attempt in range(2):
            try:
                content = self.call_groq_with_retry(messages, response_format={"type": "json_object"})
                # Groq returns a JSON string, we need to parse it. 
                # Note: json_object requires the word 'json' in the prompt, which we have.
                data = json.loads(content)
                # Some LLMs nest the list under a key, let's normalize
                if isinstance(data, dict):
                    for key in data:
                        if isinstance(data[key], list):
                            return data[key]
                return data
            except (json.JSONDecodeError, KeyError) as e:
                if attempt == 0:
                    print("Invalid JSON received for themes. Retrying...")
                    time.sleep(1)
                    continue
                else:
                    raise e

    def classify_reviews(self, all_reviews, themes):
        """Phase 3: Map every review to one of the discovered themes in batches of ~50."""
        classified_results = []
        batch_size = 50
        
        theme_list_str = "\n".join([f"- {t['theme id']}: {t['short description']}" for t in themes])
        
        for i in range(0, len(all_reviews), batch_size):
            batch = all_reviews[i : i + batch_size]
            print(f"Classifying batch {i // batch_size + 1} ({len(batch)} reviews)...")
            
            reviews_input = "\n".join([f"{r['review_id']}: {r['review_text']}" for r in batch])
            
            system_msg = (
                f"You are a classification assistant. Given these themes, assign each review to exactly one theme ID. "
                f"If a review doesn't fit any theme, use the ID 'unclassified'.\n\n"
                f"Themes:\n{theme_list_str}\n\n"
                "Return the results in a valid JSON format as a list of objects. "
                "Each object must have: 'review id' and 'theme id'."
            )
            
            messages = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": f"Reviews to classify:\n{reviews_input}"}
            ]
            
            try:
                content = self.call_groq_with_retry(messages, response_format={"type": "json_object"})
                batch_results = json.loads(content)
                
                # Normalize response
                if isinstance(batch_results, dict):
                    for key in batch_results:
                        if isinstance(batch_results[key], list):
                            batch_results = batch_results[key]
                            break
                            
                classified_results.extend(batch_results)
            except Exception as e:
                print(f"Error classifying batch: {e}")
                # Fallback to unclassified for this batch
                for r in batch:
                    classified_results.append({"review id": r['review_id'], "theme id": "unclassified"})
            
            # Small delay between batches to avoid rapid rate limit hits
            time.sleep(0.5)
            
        return classified_results

    def run(self):
        print("Starting Review Processing Pipeline...")
        
        # 1. Load latest reviews
        try:
            review_data, date_str = self.load_latest_reviews()
        except FileNotFoundError as e:
            print(e)
            return
            
        all_reviews = review_data['reviews']
        print(f"Loaded {len(all_reviews)} reviews from {date_str}.json")
        
        # 2. Phase 2: Theme Discovery
        print("Discovering themes...")
        sampled = self.sample_reviews(all_reviews)
        themes = self.discover_themes(sampled)
        print(f"Discovered {len(themes)} themes.")
        
        # 3. Phase 3: Review Classification
        print("Classifying all reviews...")
        classifications = self.classify_reviews(all_reviews, themes)
        
        # 4. Persistence
        output_dir = "data/reports"
        debug_dir = "data/phase_2_discovery"
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(debug_dir, exist_ok=True)
        
        # Group reviews by theme for easier consumption in Phase 4
        review_map = {r['review_id']: r for r in all_reviews}
        for theme in themes:
            theme["reviews"] = []
        
        unclassified_theme = {
            "theme id": "unclassified",
            "theme label": "Unclassified",
            "short description": "Reviews not fitting into other themes.",
            "reviews": []
        }
        
        theme_lookup = {t["theme id"]: t for t in themes}
        theme_lookup["unclassified"] = unclassified_theme
        
        for cls in classifications:
            tid = cls.get("theme id", "unclassified")
            rid = cls.get("review id")
            if rid in review_map and tid in theme_lookup:
                theme_lookup[tid]["reviews"].append(review_map[rid])

        # Save grouped results
        output_path = os.path.join(output_dir, f"grouped_reviews-{date_str}.json")
        output_payload = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "source_file": f"{date_str}.json",
                "theme_count": len(themes)
            },
            "themes": themes + [unclassified_theme]
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_payload, f, indent=4)
        print(f"Saved grouped reviews to {output_path}")
        
        # Optional: Save themes separately for debugging
        debug_themes_path = os.path.join(debug_dir, f"themes_debug-{date_str}.json")
        with open(debug_themes_path, 'w', encoding='utf-8') as f:
            json.dump(themes, f, indent=4)
        print(f"Saved debug themes to {debug_themes_path}")
        
        return output_path

if __name__ == "__main__":
    processor = ReviewProcessor()
    processor.run()
