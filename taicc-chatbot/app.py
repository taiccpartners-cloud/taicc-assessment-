import streamlit as st
import pandas as pd
from datetime import datetime
from fpdf import FPDF
import google.generativeai as genai
import json
from io import BytesIO
from PIL import Image
import os
import requests
import streamlit.components.v1 as components
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import razorpay
import time
import matplotlib.pyplot as plt
import tempfile
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# Configure Gemini API - handle missing secrets gracefully
# Defer configuration to avoid module-level errors
GEMINI_API_KEY_CONFIGURED = False

# Razorpay Test Credentials
RAZORPAY_KEY_ID = "rzp_live_RMHZLGJemmVdVW"
RAZORPAY_KEY_SECRET = "pICu87X4NLHZtUt11cW73RBn"

# Initialize Razorpay client - handle initialization errors
razorpay_client = None
try:
    razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
except Exception as e:
    # Razorpay client initialization failed - will be handled in payment functions
    pass

def check_razorpay_payment_status(order_id):
    try:
        if razorpay_client is None:
            return False
        payments = razorpay_client.order.payments(order_id)
        for payment in payments.get('items', []):
            if payment['status'] == 'captured':  # Payment successful
                return True
        return False
    except Exception as e:
        print(f"Error checking payment status: {e}")
        return False

def create_order(amount=1):
    if razorpay_client is None:
        raise Exception("Razorpay client not initialized")
    order = razorpay_client.order.create({
        "amount": amount * 100,  # Razorpay expects paise
        "currency": "INR",
        "payment_capture": 1
    })
    return order

# -----------------------------
# --- CONFIGURATION & SETUP ---
# -----------------------------
# Streamlit page config - MUST be first Streamlit call
st.set_page_config(page_title="TAICC AI Readiness", layout="wide")

# Load questions JSON - handle file errors gracefully
try:
    file_path = os.path.join(os.path.dirname(__file__), "questions_full.json")
    with open(file_path, "r", encoding="utf-8") as f:
        questions = json.load(f)
    if not questions:
        st.error("⚠️ questions_full.json is empty or invalid.")
        st.stop()
except FileNotFoundError:
    st.error("⚠️ questions_full.json file not found.")
    st.stop()
except json.JSONDecodeError as e:
    st.error(f"⚠️ Error parsing questions_full.json: {e}")
    st.stop()
except Exception as e:
    st.error(f"⚠️ Error loading questions_full.json: {e}")
    st.stop()
#genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

# Score mapping and readiness levels
score_map = {"Not at all": 1, "Slightly": 2, "Moderately": 3, "Very": 4, "Fully": 5}
readiness_levels = [
    (0, 1.0, "Beginner"),
    (1.1, 2.0, "Emerging"),
    (2.1, 3.0, "Established"),
    (3.1, 4.0, "Advanced"),
    (4.1, 5.0, "AI Leader")
]

# Extract domains and tiers from JSON - handle empty questions gracefully
try:
    domains = list(questions.keys())
    if not domains:
        st.error("⚠️ No domains found in questions JSON.")
        st.stop()
    # Get tiers from first domain - handle empty domains
    first_domain = next(iter(questions.values()))
    if not first_domain:
        st.error("⚠️ First domain has no tiers.")
        st.stop()
    tiers = list(first_domain.keys())
    if not tiers:
        st.error("⚠️ No tiers found in questions JSON.")
        st.stop()
except (StopIteration, AttributeError, TypeError) as e:
    st.error(f"⚠️ Error extracting domains/tiers from questions JSON: {e}")
    st.stop()

# Domain and Tier explanations
domain_explanations = {
    "BFSI": "Banking, Financial Services, and Insurance including NBFCs, Co-op Banks, Stock Broking, and more.",
    "Manufacturing": "Industries such as Automobiles, Textiles, and Machinery.",
    "Healthcare": "Hospitals, diagnostics, health-tech platforms, and telemedicine.",
    "Hospitality": "Hotels, resorts, restaurants, and travel accommodations.",
    "Pharma": "Pharmaceutical research, biotech, and medicine production.",
    "Travel and Tourism": "Tour operators, online travel platforms, airlines, etc.",
    "Construction": "Infrastructure, civil engineering, and public works.",
    "Real Estate": "Residential and commercial property development and sales.",
    "Education & EdTech": "Schools, universities, online learning platforms.",
    "Retail & E-commerce": "Retail chains, marketplaces, and D2C brands.",
    "Logistics & Supply Chain": "Warehousing, distribution, and delivery services.",
    "Agritech": "Smart farming, agri-inputs, and precision agriculture.",
    "IT & ITES": "Software companies, IT services, and BPOs.",
    "Legal & Compliance": "Law firms, compliance tools, and contract automation.",
    "Energy & Utilities": "Power generation, oil & gas, renewables.",
    "Telecommunications": "Network providers, internet services, and 5G tech.",
    "Media & Entertainment": "Broadcasting, streaming platforms, and gaming.",
    "PropTech": "Real estate technology platforms.",
    "FMCG & Consumer Goods": "Packaged goods and fast-moving consumer brands.",
    "Public Sector": "Government departments, PSUs, and public welfare.",
    "Automotive": "OEMs, auto ancillaries, and connected vehicles.",
    "Environmental & Sustainability": "Climate tech, carbon tracking, and ESG.",
    "Smart Cities": "Urban tech, IoT infrastructure, and city planning."
}

tier_explanations = {
    "Tier 1": "Enterprise Leaders – Large organizations with significant AI investments and robust strategies.",
    "Tier 2": "Strategic Innovators – Established companies actively experimenting and implementing AI.",
    "Tier 3": "Growth Enablers – Mid-sized firms beginning structured AI adoption efforts.",
    "Tier 4": "Agile Starters – Startups or small businesses with a high willingness to explore AI.",
    "Tier 5": "Traditional Operators – Individuals or firms with minimal or no current AI engagement."
}

# -----------------------------
# --- GOOGLE SHEETS SETUP ---
# -----------------------------
# Initialize Google Sheets client - handle missing secrets gracefully
sheet = None
try:
    # Check if st.secrets exists and is accessible
    if hasattr(st, 'secrets') and st.secrets is not None:
        gcp_account = st.secrets.get("gcp_service_account")
        sheet_name = st.secrets.get("SHEET_NAME")
        if gcp_account and sheet_name:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(gcp_account, scope)
            client = gspread.authorize(creds)
            sheet = client.open(sheet_name).sheet1
except (KeyError, AttributeError, TypeError):
    # Secrets not configured - will be handled in results_screen
    sheet = None
except Exception as e:
    # Other errors - will be handled in results_screen
    sheet = None

# -----------------------------
# --- SESSION STATE SETUP ---
# -----------------------------
# Session state initialization moved to main_router to avoid module-level access

# -----------------------------
# --- PAYMENT FUNCTION ---
# -----------------------------

def navigate_to_questions():
    st.session_state.page = "questions"
    st.rerun()

def payment_screen():
    st.subheader("💳 Payment Required")
    st.write("Please complete the payment of **₹1** to continue to the assessment.")

    # Create Razorpay order once per session
    if "order_id" not in st.session_state:
        order = create_order(amount=1)
        st.session_state["order_id"] = order["id"]
        st.session_state["order_amount"] = order["amount"]

    payment_html = f"""
    <html>
    <head><script src="https://checkout.razorpay.com/v1/checkout.js"></script></head>
    <body>
    <script>
        var options = {{
            "key": "{RAZORPAY_KEY_ID}",
            "amount": "{st.session_state['order_amount']}",
            "currency": "INR",
            "name": "TAICC Partners",
            "description": "AI Readiness Assessment",
            "order_id": "{st.session_state['order_id']}",
            "theme": {{ "color": "#3399cc" }}
        }};
        var rzp1 = new Razorpay(options);
        rzp1.open();
    </script></body></html>
    """
    components.html(payment_html, height=650)

    # Initialize session flags if not present
    if "paid" not in st.session_state:
        st.session_state.paid = False

    # Poll payment status (implement your check_razorpay_payment_status function)
    if not st.session_state.paid:
        with st.spinner("Checking payment status..."):
            for _ in range(12):
                if check_razorpay_payment_status(st.session_state["order_id"]):
                    st.session_state.paid = True
                    break
                time.sleep(5)

    if st.session_state.paid:
        st.success("✅ Payment confirmed!")
        if st.button("➡️ Continue to Assessment"):
            st.session_state.page = "questions"
            st.rerun()
    else:
        st.info("Awaiting payment completion...")



# -----------------------------
# --- UI FUNCTIONS ---
# -----------------------------
def login_screen():
    st.image("https://i.postimg.cc/441ZWPjs/Whats-App-Image-2025-02-20-at-11-29-36.jpg", width=150)
    st.title("TAICC AI Readiness Assessment")
    st.markdown("Fill out your details to begin the assessment.")

    with st.form("user_details_form"):
        name = st.text_input("Full Name")
        company = st.text_input("Company Name")
        email = st.text_input("Email Address")
        phone = st.text_input("Phone Number")

        domain = st.selectbox("Select Your Domain", domains, format_func=lambda x: f"{x} - {domain_explanations.get(x, '')}")
        tier = st.selectbox("Select Your Tier", tiers, format_func=lambda x: f"{x} - {tier_explanations.get(x, '')}")

        submitted = st.form_submit_button("Start Assessment")

        if submitted:
            st.session_state.user_data = {
                "Name": name,
                "Company": company,
                "Email": email,
                "Phone": phone
            }
            st.session_state.selected_domain = domain
            st.session_state.selected_tier = tier
            st.session_state.page = "payment"


def question_screen():
    st.sidebar.title("TAICC")
    st.sidebar.markdown("AI Transformation Partner")
    st.title("AI Readiness Assessment")
    st.markdown("Rate your organization on these factors.")

    domain = st.session_state.selected_domain
    tier = st.session_state.selected_tier
    questions_for_tier = questions[domain][tier]

    for idx, q in enumerate(questions_for_tier):
        key = f"Q{idx}-{q}"
        val = st.radio(q, list(score_map.keys()), key=key)
        st.session_state.answers[key] = score_map[val]

    progress = int(len(st.session_state.answers) / len(questions_for_tier) * 100)
    st.progress(progress)

    if st.button("Submit"):
        st.session_state.page = "results"


def calculate_scores():
    values = list(st.session_state.answers.values())
    avg = round(sum(values) / len(values), 2)
    st.session_state.section_scores = {"Overall Score": avg}


def determine_maturity(avg):
    for low, high, label in readiness_levels:
        if low <= avg <= high:
            return label
    return "Undefined"


def generate_professional_summary():
    avg_score = list(st.session_state.section_scores.values())[0]
    maturity = determine_maturity(avg_score)
    user = st.session_state.user_data
    client_name = user.get("Name", "[Client Name]")
    company_name = user.get("Company", "[Company Name]")

    prompt = f"""
    You are a senior AI consultant preparing a comprehensive AI readiness report for a corporate client.

    Client Details:
    - Name: {client_name}
    - Company: {company_name}
    - AI Readiness Score: {avg_score} ({maturity})

    --- Report Requirements ---
    1. Executive Summary
    2. Current Maturity Level
    3. Detailed Strengths and Weaknesses Analysis
    4. Actionable Recommendations
    5. Potential Business Impact
    6. Conclusion and Call to Action

    Use a formal business tone with bullet points, tables, and clear sections. Justify an investment price of ₹199.
    """
    model = genai.GenerativeModel("gemini-2.5-flash-lite-preview-09-2025")
    response = model.generate_content(prompt)
    generated_text = response.text

    report_text = (
        f"Client: {client_name}\n"
        f"Company: {company_name}\n"
        f"Email: {user.get('Email', '')}\n"
        f"Phone: {user.get('Phone', '')}\n\n"
        f"{generated_text.strip()}"
    )
    return maturity, report_text


def safe_text(text):
    """Encode text to latin-1 compatible string by replacing unsupported chars."""
    if isinstance(text, bytes):
        text = text.decode('utf-8', errors='replace')
    return text.encode('latin-1', errors='replace').decode('latin-1')

def clean_report_text(text):
    text = re.sub(r'[\*\#\_`>~-]+', '', text)           # Remove *, #, _, `, >, ~, -
    text = re.sub(r'\[[^\]]*\]\([^\)]*\)', '', text)    # Remove markdown links [text](url)
    text = re.sub(r'\n\s*\n+', '\n\n', text)             # Reduce multiple newlines
    text = re.sub(r'^\s+|\s+$', '', text)                 # Trim leading/trailing spaces
    text = re.sub(r'\s{2,}', ' ', text)                   # Reduce multiple spaces to single

    # Add spacing before section numbers for clarity, e.g. "1. Section"
    text = re.sub(r'(\d+\.)', r'\n\n\1', text)
    return text.strip()


def generate_bar_chart(scores):
    plt.figure(figsize=(6, 3))
    plt.bar(scores.keys(), scores.values(), color='skyblue')
    plt.title("AI Scores by Section")
    plt.ylim(0, 5)
    plt.tight_layout()
    tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    plt.savefig(tmpfile.name)
    plt.close()
    return tmpfile.name

def generate_pie_chart(tier_dist):
    plt.figure(figsize=(5, 5))
    plt.pie(tier_dist.values(), labels=tier_dist.keys(), autopct='%1.1f%%')
    plt.title("Tier Distribution")
    tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    plt.savefig(tmpfile.name)
    plt.close()
    return tmpfile.name

def generate_line_chart(score_trend):
    plt.figure(figsize=(6, 3))
    plt.plot(list(score_trend.keys()), list(score_trend.values()), marker='o', linestyle='-')
    plt.title("AI Readiness Score Trend")
    plt.ylim(0, 5)
    plt.tight_layout()
    tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    plt.savefig(tmpfile.name)
    plt.close()
    return tmpfile.name

def download_pdf(full_report_text, maturity, scores, tier_distribution, score_trend):
    # Extract executive summary and detailed report
    if "Executive Summary" in full_report_text:
        summary_start = full_report_text.index("Executive Summary")
        detailed_start = full_report_text.index("1.", summary_start)
        executive_summary = full_report_text[summary_start:detailed_start].strip()
        detailed_report = full_report_text[detailed_start:].strip()
    else:
        executive_summary = full_report_text[:500]
        detailed_report = full_report_text[500:]

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Logo and title block
    logo_url = "https://i.postimg.cc/441ZWPjs/Whats-App-Image-2025-02-20-at-11-29-36.jpg"
    response = requests.get(logo_url)
    logo_image = Image.open(BytesIO(response.content))
    logo_path = "temp_logo.png"
    logo_image.save(logo_path)
    pdf.image(logo_path, x=10, y=8, w=40)

    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "TAICC AI Readiness Assessment Report", ln=True, align="C")
    pdf.ln(15)

    # Watermark image
    watermark = logo_image.convert("RGBA").resize((100, 100))
    alpha = watermark.split()[3].point(lambda p: p * 0.1)
    watermark.putalpha(alpha)
    watermark.save("temp_watermark.png")
    pdf.image("temp_watermark.png", x=60, y=100, w=90)

    # User info block
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 8, "User Details:", ln=True)
    for k, v in st.session_state.user_data.items():
        pdf.cell(0, 8, f"{k}: {v}", ln=True)
    pdf.ln(5)
    pdf.cell(0, 8, f"AI Maturity Level: {maturity}", ln=True)
    pdf.ln(10)

    # Executive Summary Section
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "Executive Summary", ln=True)
    pdf.set_font("Arial", size=12)
    pdf.multi_cell(0, 8, safe_text(clean_report_text(executive_summary)))

    # Add bar chart after executive summary
    bar_chart_path = generate_bar_chart(scores)
    pdf.ln(10)
    pdf.image(bar_chart_path, x=pdf.l_margin, w=pdf.w - 2*pdf.l_margin)

    # Detailed Report Section
    pdf.ln(20)
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "Detailed Report", ln=True)
    pdf.set_font("Arial", size=12)
    pdf.multi_cell(0, 8, safe_text(clean_report_text(detailed_report)))


    # Insert pie chart after detailed report
    pie_chart_path = generate_pie_chart(tier_distribution)
    pdf.ln(10)
    pdf.image(pie_chart_path, x=pdf.l_margin, w=pdf.w - 2*pdf.l_margin)

    # Insert line chart last
    pdf.ln(20)
    line_chart_path = generate_line_chart(score_trend)
    pdf.image(line_chart_path, x=pdf.l_margin, w=pdf.w - 2*pdf.l_margin)

    # Footer
    pdf.ln(10)
    pdf.set_font("Arial", 'I', 10)
    pdf.cell(0, 10, "Report generated by TAICC AI Readiness Assessment Tool", ln=True, align="C")

    # Streamlit download button
    # pdf.output(dest="S") returns bytes/bytearray, not a string
    pdf_output = pdf.output(dest="S")
    # Convert to bytes if it's a bytearray
    if isinstance(pdf_output, bytearray):
        pdf_bytes = bytes(pdf_output)
    elif isinstance(pdf_output, bytes):
        pdf_bytes = pdf_output
    else:
        # If it's a string (shouldn't happen with dest="S"), encode it
        pdf_bytes = pdf_output.encode("latin-1")
    
    # Download button
    st.download_button(
        label="📥 Download Full Professional Report (PDF)",
        data=pdf_bytes,
        file_name=f"TAICC_AI_Readiness_Report_{st.session_state.user_data.get('Name', 'User').replace(' ', '_')}.pdf",
        mime="application/pdf"
    )
    
    # Return PDF bytes for email sending
    return pdf_bytes


def send_email_with_pdf(recipient_email, pdf_bytes, recipient_name):
    """Send email with PDF attachment to the user"""
    try:
        # Get email credentials from secrets
        if not hasattr(st, 'secrets') or st.secrets is None:
            raise Exception("Email secrets not configured")
        
        sender_email = st.secrets["email"]["sender_email"]
        app_password = st.secrets["email"]["app_password"]
        
        # Create message
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = recipient_email
        msg['Subject'] = "Your AI Readiness Assessment Report - TAICC"
        
        # Email body
        body = f"""
Dear {recipient_name},

Thank you for completing the TAICC AI Readiness Assessment!

Your comprehensive AI Readiness Assessment Report is attached to this email. This report includes:

• Your AI Maturity Level Assessment
• Executive Summary
• Detailed Analysis and Recommendations
• Visual Charts and Trends
• Actionable Next Steps

We hope this report helps you understand your organization's AI readiness and plan your AI transformation journey.

If you have any questions or would like to discuss your results, please don't hesitate to reach out to us.

Best regards,
TAICC Partners Team

---
This is an automated email. Please do not reply to this email.
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach PDF
        pdf_attachment = MIMEBase('application', 'octet-stream')
        pdf_attachment.set_payload(pdf_bytes)
        encoders.encode_base64(pdf_attachment)
        pdf_attachment.add_header(
            'Content-Disposition',
            f'attachment; filename=TAICC_AI_Readiness_Report_{recipient_name.replace(" ", "_")}.pdf'
        )
        msg.attach(pdf_attachment)
        
        # Send email via Gmail SMTP
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, app_password)
        text = msg.as_string()
        server.sendmail(sender_email, recipient_email, text)
        server.quit()
        
    except KeyError as e:
        raise Exception(f"Email configuration missing: {e}")
    except smtplib.SMTPAuthenticationError:
        raise Exception("Email authentication failed. Please check email credentials.")
    except Exception as e:
        raise Exception(f"Failed to send email: {str(e)}")


def show_maturity_levels():
    st.markdown("### AI Maturity Levels Explained")
    df_levels = pd.DataFrame([
        {"Score Range": "0.0 - 1.0", "Level": "Beginner", "Description": "Just starting AI journey, minimal awareness."},
        {"Score Range": "1.1 - 2.0", "Level": "Emerging", "Description": "Early experiments, limited AI integration."},
        {"Score Range": "2.1 - 3.0", "Level": "Established", "Description": "Defined AI strategy, some successful projects."},
        {"Score Range": "3.1 - 4.0", "Level": "Advanced", "Description": "Mature AI adoption, integrated into processes."},
        {"Score Range": "4.1 - 5.0", "Level": "AI Leader", "Description": "Industry-leading AI innovation and scale."},
    ])
    st.table(df_levels)


def results_screen():
    calculate_scores()
    st.title("AI Readiness Assessment Results")
    df = pd.DataFrame(list(st.session_state.section_scores.items()), columns=["Section", "Score"])
    st.bar_chart(df.set_index("Section"))

    maturity, detailed_report = generate_professional_summary()
    st.success(f"Your AI Maturity Level: **{maturity}**")
    st.markdown(detailed_report)

    show_maturity_levels()
    time_taken = datetime.now() - st.session_state.start_time
    st.caption(f"⏱️ Time taken: {time_taken.seconds // 60} min {time_taken.seconds % 60} sec")

    # Extract scores from session_state answers grouped by category (example, adjust as you have categories)
    # Here assuming questions and answers are structured by domain, you may adapt logic as needed
    section_scores = st.session_state.section_scores  # This is your calculated overall scores dict
    
    # For tier distribution, count how many users answered selecting each tier or however tiers are tracked
    # Example from your saved session state variable or compute dummy from selected tier
    tier_distribution = {
        st.session_state.selected_tier: 1,  # Simplest case - 1 user
        # Add other tiers if needed or get statistics from your DB
    }

    # For score trend, if you have historical data, pull it from DB or session, else sample below:
    score_trend = {
        "Q1": 2.8,
        "Q2": 3.1,
        "Q3": 3.7,
        "Q4": 4.0
    }

    # Pass actual data and get PDF bytes
    pdf_bytes = download_pdf(detailed_report, maturity, section_scores, tier_distribution, score_trend)

    # -----------------------------
    # --- SEND EMAIL WITH PDF ---
    # -----------------------------
    user_email = st.session_state.user_data.get("Email", "")
    if user_email:
        try:
            send_email_with_pdf(user_email, pdf_bytes, st.session_state.user_data.get("Name", "User"))
            st.success("✅ Report has been sent to your email!")
        except Exception as e:
            st.warning(f"⚠️ Could not send email: {e}. Please use the download button above.")

    # -----------------------------
    # --- SAVE TO GOOGLE SHEETS ---
    # -----------------------------
    if sheet is not None:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            row = [
                now,
                st.session_state.user_data.get("Name", ""),
                st.session_state.user_data.get("Company", ""),
                st.session_state.user_data.get("Email", ""),
                st.session_state.user_data.get("Phone", ""),
                st.session_state.selected_domain,
                st.session_state.selected_tier,
                list(st.session_state.section_scores.values())[0],
                maturity
            ]
            sheet.append_row(row)
            st.success("✅ Results saved to Google Sheets successfully!")
        except Exception as e:
            st.error(f"❌ Could not save to Google Sheets: {e}")
    else:
        st.warning("⚠️ Google Sheets not configured. Results were not saved.")


# -----------------------------
# --- ROUTER ---
# -----------------------------
def main_router():
    # Initialize session state if not already initialized
    if "page" not in st.session_state:
        st.session_state.page = "login"
        st.session_state.answers = {}
        st.session_state.section_scores = {}
        st.session_state.user_data = {}
        st.session_state.selected_domain = ""
        st.session_state.selected_tier = ""
        st.session_state.start_time = datetime.now()
        st.session_state.paid = False
    
    # Initialize Razorpay client if not already initialized
    global razorpay_client
    if razorpay_client is None:
        try:
            razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        except Exception as e:
            # Razorpay client initialization failed - will be handled in payment functions
            pass
    
    # Initialize Google Sheets client if not already initialized
    global sheet
    if sheet is None:
        try:
            if hasattr(st, 'secrets') and st.secrets is not None:
                gcp_account = st.secrets["gcp_service_account"]
                sheet_name = st.secrets["SHEET_NAME"]
                if gcp_account and sheet_name:
                    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
                    creds = ServiceAccountCredentials.from_json_keyfile_dict(gcp_account, scope)
                    client = gspread.authorize(creds)
                    sheet = client.open(sheet_name).sheet1
        except (KeyError, AttributeError, TypeError):
            # Secrets not configured - will be handled in results_screen
            sheet = None
        except Exception as e:
            # Other errors - will be handled in results_screen
            sheet = None
    
    # Configure Gemini API key if not already configured
    global GEMINI_API_KEY_CONFIGURED
    if not GEMINI_API_KEY_CONFIGURED:
        try:
            # Safely access secrets using bracket notation with try-except
            if hasattr(st, 'secrets') and st.secrets is not None:
                try:
                    api_key = st.secrets["GEMINI_API_KEY"]
                    if api_key:
                        genai.configure(api_key=api_key)
                        GEMINI_API_KEY_CONFIGURED = True
                except KeyError:
                    # Key doesn't exist in secrets
                    GEMINI_API_KEY_CONFIGURED = False
                except AttributeError:
                    # st.secrets doesn't support bracket notation
                    GEMINI_API_KEY_CONFIGURED = False
        except (AttributeError, TypeError) as e:
            # st.secrets not available or wrong type
            GEMINI_API_KEY_CONFIGURED = False
        except Exception as e:
            # Other errors
            GEMINI_API_KEY_CONFIGURED = False
    
    # Check if Gemini API key is configured
    if not GEMINI_API_KEY_CONFIGURED:
        st.error("⚠️ **GEMINI_API_KEY not found in secrets.**")
        st.markdown("""
        **To fix this on Streamlit Cloud:**
        1. Go to your app's settings (click 'Manage app' in the lower right)
        2. Navigate to 'Secrets' tab
        3. Add the following:
        ```
        GEMINI_API_KEY = "your-api-key-here"
        ```
        4. Save and restart your app
        """)
        st.stop()
    
    page = st.session_state.get("page", "login")

    if page == "login":
        login_screen()
    elif page == "payment":
        payment_screen()
    elif page == "questions":
        question_screen()
    elif page == "results":
        results_screen()
    else:
        st.session_state.page = "login"
        st.rerun()

if __name__ == "__main__":
    main_router()


