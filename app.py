import streamlit as st
import os
import glob
from dotenv import load_dotenv

# Import phase modules - utilizing the same logic as CLI
from src.ingest_reviews import fetch_and_save_reviews
from src.process_reviews import ReviewProcessor
from src.generate_pulse import PulseGenerator
from src.generate_email import EmailGenerator

# Set page config
st.set_page_config(page_title="GROWW Insights Dashboard", page_icon="📈", layout="wide")

# Load environment variables
load_dotenv(override=True)

# Custom CSS for "Groww White & Orange" theme
st.markdown("""
    <style>
    /* Main background and text */
    .stApp {
        background-color: #ffffff;
        color: #333333;
    }
    
    /* Headers styling */
    h1, h2, h3 {
        color: #212121 !important;
        font-family: 'Inter', sans-serif;
        font-weight: 700;
    }
    
    /* Sidebar styling */
    section[data-testid="stSidebar"] {
        background-color: #f8f9fa !important;
    }
    
    /* Button primary styling - Using Orange */
    div.stButton > button:first-child {
        background-color: #ff5e00;
        color: white;
        border-radius: 8px;
        border: none;
        font-weight: 600;
        transition: all 0.3s ease;
    }
    
    div.stButton > button:hover {
        background-color: #e65500;
        color: white;
        border: none;
    }
    
    /* Input field styling */
    .stTextInput input, .stNumberInput input, .stSelectbox [data-baseweb="select"] {
        background-color: #ffffff !important;
        border: 1px solid #ddd !important;
        color: #333333 !important;
    }
    
    /* Divider custom styling */
    hr {
        border-color: #eee;
    }
    
    /* Pulse rendering section */
    .pulse-container {
        background-color: #fafafa;
        padding: 24px;
        border-radius: 12px;
        border: 1px solid #eee;
        margin-top: 10px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
    }
    </style>
""", unsafe_allow_html=True)

def run_pulse_pipeline(weeks, max_reviews):
    """Executes the backend pipeline and yields progress."""
    try:
        # Phase 1: Ingestion
        status = st.status("Phase 1: Fetching and Cleaning Reviews...")
        fetch_and_save_reviews(
            app_id='com.nextbillion.groww', 
            weeks_requested=weeks,
            max_count=max_reviews
        )
        status.update(label="Phase 1 Complete!", state="complete", expanded=False)

        # Phase 2 & 3: Selection and Classification
        status = st.status("Phase 2 & 3: Discovering Themes & Classifying Reviews...")
        processor = ReviewProcessor()
        processor.run()
        status.update(label="Phase 2 & 3 Complete!", state="complete", expanded=False)

        # Phase 4: Pulse Generation
        status = st.status("Phase 4: Generating Weekly Pulse...")
        generator = PulseGenerator()
        pulse_path = generator.run() # Generator returns the markdown path
        status.update(label="Phase 4 Complete!", state="complete", expanded=False)

        # Phase 5: Email Draft Generation
        status = st.status("Phase 5: Drafting Email...")
        # We always run in dry_run=True here just to generate the .eml for manual send/download
        email_gen = EmailGenerator(dry_run=True)
        email_gen.run()
        status.update(label="Phase 5 Complete!", state="complete", expanded=False)

        return pulse_path
    
    except Exception as e:
        st.error(f"Pipeline Error: {e}")
        return None

def main():
    st.title("📈 GROWW Weekly Review Pulse")
    st.markdown("Transforming app store reviews into actionable product insights.")

    # 1. Pipeline Configuration
    st.header("1. Pipeline Configuration")
    col_cfg1, col_cfg2 = st.columns(2)
    with col_cfg1:
        # User requested a simpler selection for weeks instead of a slider
        week_options = [8, 9, 10, 11, 12]
        weeks = st.selectbox("Select Timeframe (past weeks to analyze)", options=week_options, index=4)
        
    with col_cfg2:
        max_reviews = st.number_input("Maximum reviews to fetch", min_value=100, max_value=5000, value=1000, step=100)
    
    st.divider()

    # 2. Run Full Pipeline
    st.header("2. Insights Generation")
    # User requested a better name for the button
    if st.button("🚀 Generate Weekly Insights", type="primary", use_container_width=True):
        pulse_path = run_pulse_pipeline(weeks, max_reviews)
        if pulse_path:
            st.success("Insights generated successfully!")
            st.session_state['latest_pulse_path'] = pulse_path
        else:
            st.error("Pipeline failed to complete.")

    st.divider()

    # 3. Weekly Pulse Display
    st.header("3. Generated Weekly Pulse")
    
    # Try to load latest pulse if not in session state but exists on disk
    if 'latest_pulse_path' not in st.session_state:
        pulse_files = glob.glob("data/phase4/pulse-*.md")
        if pulse_files:
            st.session_state['latest_pulse_path'] = sorted(pulse_files)[-1]

    if 'latest_pulse_path' in st.session_state and os.path.exists(st.session_state['latest_pulse_path']):
        with open(st.session_state['latest_pulse_path'], "r", encoding="utf-8") as f:
            pulse_content = f.read()
        
        # Rendering the pulse content
        st.markdown(f'<div class="pulse-container">{pulse_content}</div>', unsafe_allow_html=True)
    else:
        st.info("No insights found. Click the button above to generate a report.")

    st.divider()

    # 4 & 5. Email Management
    col_eml1, col_eml2 = st.columns(2)
    
    with col_eml1:
        st.header("4. Download Email Draft")
        eml_path = "data/phase5/draft_email.eml"
        if os.path.exists(eml_path):
            with open(eml_path, "rb") as f:
                st.download_button(
                    label="📥 Download Email Draft (.eml)",
                    data=f,
                    file_name=os.path.basename(eml_path),
                    mime="message/rfc822",
                    use_container_width=True
                )
        else:
            st.write("No email draft available.")

    with col_eml2:
        st.header("5. Send Email")
        recipient_email = st.text_input("Recipient email address", 
                                         value=os.getenv("SMTP_RECIPIENT_EMAIL", ""),
                                         placeholder="team@example.com")
        
        if st.button("✉️ Send Email", use_container_width=True):
            if not recipient_email:
                st.warning("Please provide a recipient email.")
            else:
                with st.spinner("Sending email..."):
                    try:
                        # We trigger the EmailGenerator in non-dry-run mode for this direct action
                        email_gen = EmailGenerator(recipient_email=recipient_email, dry_run=False)
                        email_gen.run()
                        st.success(f"Email sent successfully to {recipient_email}!")
                    except Exception as e:
                        st.error(f"Failed to send email: {e}")

if __name__ == "__main__":
    main()
