import argparse
import sys
import os
from dotenv import load_dotenv

# Import phase modules
from src.ingest_reviews import fetch_and_save_reviews
from src.process_reviews import ReviewProcessor
from src.generate_pulse import PulseGenerator
from src.generate_email import EmailGenerator

# Load environment variables
load_dotenv()

def run_pipeline(args):
    """Executes the pipeline phases based on arguments."""
    
    # 1. Scrape Phase
    if args.command in ['scrape', 'all']:
        print("\n--- Phase 1: Review Ingestion & Cleaning ---")
        fetch_and_save_reviews(
            app_id='com.nextbillion.groww', 
            weeks_requested=args.weeks
        )

    # 2. Analyze & Classify Phase
    if args.command in ['analyze', 'classify', 'all']:
        print("\n--- Phase 2 & 3: Theme Discovery & Classification ---")
        processor = ReviewProcessor()
        processor.run()

    # 3. Report Phase
    if args.command in ['report', 'all']:
        print("\n--- Phase 4: Weekly Pulse Generation ---")
        generator = PulseGenerator()
        generator.run()

    # 4. Email Phase
    if args.command in ['email', 'all']:
        # If 'all' is run, we only send email if --send-email flag is present
        # If 'email' command is run directly, we always run the email generator (which handles its own send logic)
        if args.command == 'email' or args.send_email:
            print("\n--- Phase 5: Email Draft Generation & Delivery ---")
            
            # Determine recipient name and email
            # Priority: CLI argument > .env variable > Defaults
            recipient_email = args.recipient_email or os.getenv("SMTP_RECIPIENT_EMAIL")
            recipient_name = args.recipient_name or "Team"
            
            # The EmailGenerator reads from .env by default, but we can potentially subclass or modify it 
            # to accept overrides if needed. For now, we'll initialize it and it will use its internal logic.
            # However, the user specifically asked for optional recipient email and name overrides.
            
            # Let's check if EmailGenerator supports parameters in __init__
            generator = EmailGenerator(recipient_email=recipient_email, recipient_name=recipient_name)
            generator.run()

def main():
    # Base parser for shared arguments
    base_parser = argparse.ArgumentParser(add_help=False)
    base_parser.add_argument('--weeks', type=int, default=12, choices=range(8, 13), 
                            help='Number of weeks to fetch (8-12)')
    base_parser.add_argument('--send-email', action='store_true', 
                            help='Automatically send the email after reporting (only used with "all")')
    base_parser.add_argument('--recipient-email', type=str, help='Override recipient email address')
    base_parser.add_argument('--recipient-name', type=str, help='Override recipient name')

    # Main parser
    parser = argparse.ArgumentParser(description="GROWW Review Insights System - CLI Trigger")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Commands using the base_parser as parent
    subparsers.add_parser('scrape', help='Run Phase 1: Review Ingestion & Cleaning', parents=[base_parser])
    subparsers.add_parser('analyze', help='Run Phase 2: Theme Discovery', parents=[base_parser])
    subparsers.add_parser('classify', help='Run Phase 3: Review Classification', parents=[base_parser])
    subparsers.add_parser('report', help='Run Phase 4: Weekly Pulse Generation', parents=[base_parser])
    subparsers.add_parser('email', help='Run Phase 5: Email Draft Generation & Delivery', parents=[base_parser])
    subparsers.add_parser('all', help='Run the full pipeline (Phases 1-5)', parents=[base_parser])

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        run_pipeline(args)
    except Exception as e:
        print(f"\n[ERROR] Pipeline failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
