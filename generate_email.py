import os
import glob
import re
import smtplib
from email.message import EmailMessage
import markdown
import argparse
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class EmailGenerator:
    def __init__(self, recipient_email=None, recipient_name=None, dry_run=None):
        self.sender_email = os.getenv("SMTP_SENDER_EMAIL")
        self.sender_password = os.getenv("SMTP_SENDER_PASSWORD")
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = os.getenv("SMTP_PORT", "587")
        try:
            self.smtp_port = int(self.smtp_port)
        except ValueError:
            self.smtp_port = 587
        
        # Override with args if provided, else use .env
        self.recipient_email = recipient_email or os.getenv("SMTP_RECIPIENT_EMAIL")
        self.recipient_name = recipient_name or "Team"
        
        # Auto-send logic: If dry_run is explicitly provided, use it.
        # Otherwise, determine based on credentials.
        if dry_run is not None:
            self.dry_run = dry_run
        elif all([self.sender_email, self.sender_password, self.smtp_server, self.smtp_port, self.recipient_email]):
            self.dry_run = False
        else:
            self.dry_run = True
        
    def get_latest_pulse_file(self, phase4_dir="data/phase4"):
        """Gets the most recently generated markdown pulse file."""
        files = glob.glob(os.path.join(phase4_dir, "pulse-*.md"))
        if not files:
            raise FileNotFoundError(f"No pulse markdown file found in {phase4_dir}")
        latest_file = sorted(files)[-1]
        
        date_str = os.path.basename(latest_file).replace("pulse-", "").replace(".md", "")
        return latest_file, date_str

    def format_date_ordinal(self, date_str):
        """Formats the YYYY-MM-DD date into '8th March 2026' format."""
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            def get_ordinal(n):
                if 11 <= (n % 100) <= 13:
                    return str(n) + 'th'
                return str(n) + {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
                
            return f"{get_ordinal(date_obj.day)} {date_obj.strftime('%B %Y')}"
        except ValueError:
            return date_str

    def prepare_email_content(self, md_filepath, date_str):
        """Reads Markdown, pre-pends greeting, and generates HTML version."""
        with open(md_filepath, "r", encoding="utf-8") as f:
            md_content = f.read()

        formatted_date = self.format_date_ordinal(date_str)
        subject = f"GROWW Weekly Review Pulse -- Week of {formatted_date}"
        
        # Remove the # Title from the markdown specifically because 
        # it is redundant with the email subject, reducing clutter.
        md_content = re.sub(r'^#\s+.*?\n+', '', md_content)
        
        # Create Plain Text version (Very basic strip - keeping it mostly as Markdown since 
        # Markdown is highly readable plain-text anyway)
        plain_text_body = f"Hi {self.recipient_name},\n\n{md_content}"
        
        # Create HTML version
        html_content = markdown.markdown(md_content)
        
        # Inject CSS for bold and larger section titles
        # Standard Markdown converts ## to h2, ### to h3 etc.
        # We will style h2 and h3 tags.
        style = """
        <style>
            h2, h3 {
                font-weight: bold;
                font-size: 1.2em;
                margin-top: 20px;
                margin-bottom: 10px;
                color: #333;
            }
            p, li {
                font-size: 1em;
                line-height: 1.5;
            }
            ol li, ul li {
                margin-bottom: 10px;
            }
        </style>
        """
        
        html_body = f"<html><head>{style}</head><body><p>Hi {self.recipient_name},</p>\n{html_content}</body></html>"
        
        return subject, plain_text_body, html_body

    def send_email(self, subject, plain_text_body, html_body):
        """Dispatches the email via SMTP or saves it for Dry Run."""
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = self.sender_email
        msg['To'] = self.recipient_email
        
        # Add Plain text fallback
        msg.set_content(plain_text_body)
        
        # Add Rich HTML
        msg.add_alternative(html_body, subtype='html')
        
        # Save local drafts for record-keeping (regardless of dry_run)
        output_dir = os.path.join("data", "phase5")
        os.makedirs(output_dir, exist_ok=True)
        
        # Save raw EML
        eml_path = os.path.join(output_dir, "draft_email.eml")
        with open(eml_path, "w", encoding="utf-8") as f:
            f.write(msg.as_string())
            
        # Save HTML preview
        html_path = os.path.join(output_dir, "draft_preview.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_body)
        
        if self.dry_run:
            print(f"[DRY-RUN MODE ENABLED] Email not actually sent. Drafts saved to {eml_path} and {html_path}")
            return
            
        # Send Mode
        print(f"Connecting to {self.smtp_server}:{self.smtp_port}...")
        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()  # Secure the connection
            # SECURITY: Never log the password
            if not self.sender_email or not self.sender_password:
               raise ValueError("SMTP_SENDER_EMAIL or SMTP_SENDER_PASSWORD is not set in environment.") 
               
            server.login(self.sender_email, self.sender_password)
            server.send_message(msg)
            server.quit()
            print(f"Successfully sent out Pulse Email to {self.recipient_email}")
        except Exception as e:
            # Mask any potential credential leaks in exception messages
            error_str = str(e)
            if self.sender_password and self.sender_password in error_str:
                error_str = error_str.replace(self.sender_password, "***FILTERED***")
            print(f"Failed to send email: {error_str}")

    def run(self):
        print("Starting Phase 5: Email Draft Generation...")
        
        try:
            pulse_file, date_str = self.get_latest_pulse_file()
            print(f"Found Pulse File: {pulse_file}")
            
            subject, text_body, html_body = self.prepare_email_content(pulse_file, date_str)
            print("Successfully packaged Pulse Markdown into Email payloads.")
            
            self.send_email(subject, text_body, html_body)
            
        except Exception as e:
            print(f"Phase 5 Error: {e}")
            
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 5 - Pulse Email Generator")
    parser.add_argument("--recipient", type=str, help="Recipient Email Address")
    parser.add_argument("--name", type=str, help="Recipient Name for Greeting")
    
    args = parser.parse_args()
    
    generator = EmailGenerator(
        recipient_email=args.recipient,
        recipient_name=args.name
    )
    generator.run()
