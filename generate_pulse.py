import os
import json
import re
from datetime import datetime
from dotenv import load_dotenv
from groq import Groq
import glob

# Load environment variables
load_dotenv(override=True)

class PulseGenerator:
    def __init__(self, model="llama-3.3-70b-versatile"):
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError("GROQ_API_KEY not found in .env file")
        self.client = Groq(api_key=self.api_key)
        self.model = model

    def load_grouped_reviews(self, classification_dir="data/reports"):
        """Loads the most recent grouped_reviews file."""
        # Use glob to find all grouped_reviews files
        files = glob.glob(os.path.join(classification_dir, "grouped_reviews-*.json"))
        
        if not files:
            raise FileNotFoundError(f"No grouped reviews found in {classification_dir}")
            
        # Always pick the latest by filename (which includes date)
        latest_file = sorted(files)[-1]
        print(f"Loading grouped reviews from: {latest_file}")
        
        # Extract date string for naming
        date_str = os.path.basename(latest_file).replace("grouped_reviews-", "").replace(".json", "")
        
        with open(latest_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Support both direct list of themes and encapsulated dictionary
            if isinstance(data, dict):
                return data.get("themes", []), date_str
            return data, date_str

    def prepare_input_data(self, grouped_data):
        """Extracts Top 3 themes based on review count and representative quotes."""
        # The new structure is a list of themes with their reviews directly attached
        if not isinstance(grouped_data, list):
            # Fallback if it's the old structure encapsulated
            grouped_data = grouped_data.get("themes", [])

        # Filter out 'unclassified' and count reviews
        valid_themes = []
        for theme in grouped_data:
            theme_name = theme.get("Theme name", theme.get("theme label", "Unknown"))
            if theme_name.lower() == "unclassified":
                continue
                
            reviews = theme.get("reviews", [])
            valid_themes.append({
                "name": theme_name,
                "count": len(reviews),
                "reviews": reviews
            })
            
        # Sort by count descending
        sorted_themes = sorted(valid_themes, key=lambda x: x["count"], reverse=True)
        top_3 = sorted_themes[:3]
        
        input_string = "## Theme Counts and Reviews\n\n"
        all_original_quotes = []
        
        for theme in top_3:
            # Format: ### Theme: Name (X mentions)
            theme_name = theme.get("theme label", theme.get("Theme name", theme.get("name", "Unknown Theme")))
            count = theme.get("count", len(theme.get("reviews", [])))
            
            input_string += f"### Theme: {theme_name} ({count} mentions)\n"
            
            # Use reviews field directly. Keys in JSON are lowercase per process_reviews.py
            reviews = theme.get("reviews", [])
            
            # Sort reviews by helpfulness if available, then take top 3
            sorted_reviews = sorted(reviews, key=lambda r: r.get("helpful_count", 0), reverse=True)
            top_3_reviews = sorted_reviews[:3]
            
            for i, rev in enumerate(top_3_reviews):
                text = rev.get("review_text", rev.get("Review text", ""))
                rating = rev.get("rating", rev.get("Rating", "N/A"))
                input_string += f"Review {i+1} ({rating} Stars): \"{text}\"\n"
                all_original_quotes.append(text)
                
            input_string += "\n"
            
        return input_string, all_original_quotes

    def validate_quotes(self, generated_markdown, original_quotes):
        """
        Validates that any text explicitly marked as a quote
        actually exists as a substring within the original reviews.
        Returns a boolean indicating success, and a list of invalid quotes if any.
        """
        # The new format is a bullet list where each line contains a quote and ends with " — X★ review"
        # We'll use a broader regex to capture anything inside double quotes to be safe, 
        # as the LLM might use standard quotes or blockquotes despite instructions.
        inline_quotes = re.findall(r'"([^"]{15,})"', generated_markdown) # at least 15 chars to avoid picking up single words
        
        all_extracted = inline_quotes
        
        if not all_extracted:
             # If the LLM didn't format them beautifully, we can't strictly validate without sophisticated matching, 
             # but we assume success if we can't find explicitly marked quotes.
             # However, the prompt mandates they be clearly marked.
             return True, []
             
        invalid_quotes = []
        # Normalization helper for resilient matching
        def normalize(t):
            return re.sub(r'[^a-zA-Z0-9]', '', t).lower()
            
        normalized_originals = [normalize(q) for q in original_quotes]
        
        for generated_q in all_extracted:
            norm_gen = normalize(generated_q)
            # Find if this generated snippet exists in any original quote
            is_valid = False
            for norm_orig in normalized_originals:
                # If the generated text is a substantial substring of the original
                if norm_gen in norm_orig and len(norm_gen) > 5:
                    is_valid = True
                    break
            
            # Privacy rule: [User] might be in generated quote but not original. 
            # If so, strict substring fails. We allow relaxation if [User] is present.
            if not is_valid and "[user]" in norm_gen:
                temp_gen = norm_gen.replace("[user]", "")
                for norm_orig in normalized_originals:
                     if temp_gen in norm_orig and len(temp_gen) > 5:
                         is_valid = True
                         break
                         
            if not is_valid:
                invalid_quotes.append(generated_q)
                
        return len(invalid_quotes) == 0, invalid_quotes

    def generate_pulse(self, input_data_string, original_quotes, date_str):
        """Calls Groq to generate the one-page pulse, enforcing strict rules."""
        
        # Format the date properly for the title, e.g., "8th March 2026"
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            
            def get_ordinal(n):
                if 11 <= (n % 100) <= 13:
                    return str(n) + 'th'
                return str(n) + {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
                
            formatted_date = f"{get_ordinal(date_obj.day)} {date_obj.strftime('%B %Y')}"
        except ValueError:
            formatted_date = date_str

        system_prompt = f"""You are a top-tier product communications writer for the Groww app.
Your task is to create a 'Weekly Review Pulse', a highly concise (< 250 words) one-page summary intended for Product, Growth, Support, and Leadership teams.

You will be provided with the top 3 themes from recent app reviews, along with their review counts, and representative quotes with star ratings.

**Strict Output Requirements:**
1. **Title**: Add a clear title at the top exactly like this:
   `# GROWW Weekly Review Pulse -- Week of {formatted_date}`
2. **Top Themes**: Use `## Top Themes` as the section header. Under it, clearly list the top 3 themes using a numbered list. Under each theme, add a short description based on its reviews.
   - **CRITICAL**: The description for each theme MUST use bullet points (using `-`), NOT numbers, to avoid confusion with the theme numbering itself. At the end of the theme name, clearly mention the review count using the format `(X mentions)`.
3. **What do users say**: Use `## What do users say` as the section header. Include exactly 3 real user quotes from the provided input. 
   - **CRITICAL**: Do NOT change, summarize, or alter the quotes. They must be exact substrings of the input reviews.
   - **CRITICAL FORMATTING**: Do NOT use blockquotes. Display each quote on a separate line. Add a blank line between quotes. Format the quotes as a numbered list.
   - **CRITICAL RATING**: At the end of each quote, include the star rating of that review exactly in this format: `— X★ review`. (e.g., `1. "..." — 5★ review`)
4. **Action Ideas**: Use `## Action Ideas` as the section header. Provide 3 concrete, realistic action ideas for the product team based on the insights.
5. **Privacy**: The pulse MUST NOT include any personal information. If a review contains a name or personal detail, replace it exactly with `[User]`.
6. **Length**: The entire response MUST be under 250 words.

Do NOT include any filler text. Begin immediately with the pulse report."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Here is the review data:\n\n{input_data_string}"}
        ]
        
        max_attempts = 3
        for attempt in range(max_attempts):
            print(f"Generating pulse (Attempt {attempt + 1}/{max_attempts})...")
            
            # API Call with internal retry for rate limits
            content = None
            api_retries = 0
            max_api_retries = 5
            
            while api_retries < max_api_retries:
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=0.3, # Low temp for factual quoting
                    )
                    content = response.choices[0].message.content
                    break
                except Exception as e:
                    error_msg = str(e).lower()
                    if "rate_limit_exceeded" in error_msg or "429" in error_msg or "overloaded" in error_msg:
                        wait_time = (5 * (2 ** api_retries)) + (random.random() * 5)
                        print(f"Groq API limited/overloaded. Waiting {wait_time:.2f}s...")
                        time.sleep(wait_time)
                        api_retries += 1
                    else:
                        raise e
            
            if not content:
                raise Exception("Failed to get response from Groq API after multiple retries.")
                
            # Validation check
            is_valid, invalid_q = self.validate_quotes(content, original_quotes)
            
            if is_valid:
                word_count = len(content.split())
                if word_count > 300: # giving a tiny bit of buffer over 250
                    print(f"Warning: Word count is {word_count}, which exceeds the 250 strict limit.")
                return content
            else:
                print("Validation Failed: The LLM hallucinated or modified quotes.")
                print(f"Invalid quotes detected: {invalid_q}")
                
                # Feed feedback back to LLM for next attempt
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user", 
                    "content": "You failed the validation. The following quotes you provided were NOT exact matches from the input data: " + 
                               str(invalid_q) + 
                               ". You MUST use EXACT quotes from the provided text without any modifications whatsoever (except for replacing names with [User]). Try again."
                })
                    
        raise Exception("Failed to generate a valid pulse after multiple attempts due to quote hallucinations or rate limits.")

    def save_reports(self, markdown_content, date_str):
        """Saves the generated pulse as Markdown and Plain Text."""
        reports_dir = os.path.join("data", "phase4")
        os.makedirs(reports_dir, exist_ok=True)
        
        md_path = os.path.join(reports_dir, f"pulse-{date_str}.md")
        txt_path = os.path.join(reports_dir, f"pulse-{date_str}.txt")
        
        # Save Markdown
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
            
        # Generate and save Plain Text (strip basic markdown)
        # Convert headers and blockquotes to plain text equivalents
        plain_text = re.sub(r'^#+\s+', '', markdown_content, flags=re.MULTILINE) # remove headers
        plain_text = re.sub(r'^>\s+', 'Quote: ', plain_text, flags=re.MULTILINE)  # remove blockquotes
        plain_text = re.sub(r'\*\*(.*?)\*\*', r'\1', plain_text) # remove bold
        plain_text = re.sub(r'\*(.*?)\*', r'\1', plain_text)     # remove italics
        
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(plain_text)
            
        print(f"Successfully saved reports to {md_path} and {txt_path}")
        return md_path

    def run(self):
        print("Starting Phase 4: Pulse Generation...")
        
        try:
            grouped_data, date_str = self.load_grouped_reviews()
        except FileNotFoundError as e:
            print(e)
            return
            
        print(f"Loaded grouped reviews for date: {date_str}")
        
        input_string, original_quotes = self.prepare_input_data(grouped_data)
        print("Extracted Top 3 themes and representative reviews.")
        
        pulse_content = self.generate_pulse(input_string, original_quotes, date_str)
        md_path = self.save_reports(pulse_content, date_str)
        print("Phase 4 Complete.")
        return md_path

if __name__ == "__main__":
    generator = PulseGenerator()
    generator.run()
