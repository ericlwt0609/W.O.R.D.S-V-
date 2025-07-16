import streamlit as st
import openai, tempfile, os, requests, re
from PyPDF2 import PdfReader
from docx import Document
import pandas as pd
from pptx import Presentation
from bs4 import BeautifulSoup

openai.api_key = os.getenv("OPENAI_API_KEY")
client = openai.OpenAI()

# Session state for iterative refinement
if "sow_content" not in st.session_state:
    st.session_state.sow_content = ""

# File extractors
def extract_pdf_text(f): return "".join([p.extract_text() or "" for p in PdfReader(f).pages])[:8000]
def extract_docx_text(f): return "\n".join([p.text for p in Document(f).paragraphs])[:8000]
def extract_excel_text(f): return pd.read_excel(f, sheet_name=None).to_string()[:8000]
def extract_ppt_text(f):
    prs = Presentation(f)
    return "\n".join(s.text for slide in prs.slides for s in slide.shapes if hasattr(s, "text"))[:8000]

# Fetch example SoW from LawInsider
def fetch_lawinsider():
    r = requests.get("https://www.lawinsider.com/clause/scope-of-work")
    soup = BeautifulSoup(r.text, "html.parser")
    return [c.get_text(strip=True) for c in soup.select(".clause-body")[:5]]

# Fetch SoW from arbitrary URL (e.g. SEC doc)
def fetch_text_from_url(url):
    try:
        r = requests.get(url)
        soup = BeautifulSoup(r.text, "html.parser")
        ps = soup.find_all('p')
        return "\n".join(p.get_text(strip=True) for p in ps[:10])
    except Exception as e:
        return f"[Error extracting URL: {e}]"

# Fetch relevant SEC text by keyword search
def fetch_sec_snippets(keyword):
    search_url = f"https://www.sec.gov/cgi-bin/srch-edgar?text={keyword}"
    r = requests.get(search_url)
    soup = BeautifulSoup(r.text, "html.parser")
    hits = soup.find_all("tr", class_="blueRow")[:2]  # top 2 search hits
    results = []
    base = "https://www.sec.gov"
    for tr in hits:
        link = tr.find("a")
        if link and link.has_attr("href"):
            doc_link = base + link['href']
            doc = requests.get(doc_link)
            doc_soup = BeautifulSoup(doc.text, "html.parser")
            snippet = doc_soup.find("pre") or doc_soup.find("p")
            if snippet:
                results.append(snippet.get_text(strip=True)[:1500])
    return results

# Highlight figures for validation
def highlight_figures(text):
    return re.sub(r"\$?\d[\d,]*(\.\d+)?", r"[\g<0> – validate independently]", text)

# Generate SoW with dual business and legal perspective
def generate_sow(data, desc, examples, sec_snips, perspective):
    is_vendor = perspective == "Company is Service Provider"
    prompt = f"""
You are a dual-role expert: a contract lawyer AND a business professional for the relevant industry.

Role: {"Pro-vendor" if is_vendor else "Pro-client"} — use "Company" for Client and "Service Provider" for Vendor.

Structure:
1. Description
2. Function
3. Price
4. Dependencies
5. Milestones
6. Warranties
7. Service Levels (if applicable)
8. Others

---
User Description:
{desc}

---
Base Extract:
{data}

---
Examples:
{'\n---\n'.join(examples)} 

---
SEC Snippets:
{'\n---\n'.join(sec_snips)}

---
Highlight any figures requiring independent validation.
"""
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role":"system","content":"You are a contract lawyer AND business expert."},
            {"role":"user","content":prompt}
        ],
        temperature=0.6
    )
    return highlight_figures(resp.choices[0].message.content)

# Streamlit UI
st.title("AI Scope of Work (SoW) Generator")

file = st.file_uploader("Upload base document (PDF/DOCX/XLSX/PPTX)", type=["pdf","docx","xlsx","xls","pptx"])
desc = st.text_area("Describe the goods/services and business context")
role = st.radio("Who is Company?", ["Company is Service Provider", "Company is Client"])
kw = st.text_input("Keyword to search SEC filings (e.g. 'drone monitoring')")

use_li = st.checkbox("Include LawInsider examples", True)
if use_li:
    li = fetch_lawinsider()
    sel_li = [ex for i,ex in enumerate(li) if st.checkbox(f"Example {i+1}", value=True)]
else:
    sel_li = []

custom = st.text_area("Paste custom clauses (optional)")
url = st.text_input("Optional URL example (e.g. SEC filing)")

if st.button("Generate Scope of Work"):
    if not (file and desc): st.warning("Upload document and provide description"); st.stop()
    ext = file.name.lower()
    scanners = {"pdf":extract_pdf_text,"docx":extract_docx_text,"xlsx":extract_excel_text,
                "xls":extract_excel_text,"pptx":extract_ppt_text}
    data = scanners.get(ext.split('.')[-1], lambda f:"Unsupported file")(file)
    examples = sel_li + ([custom] if custom.strip() else []) + ([fetch_text_from_url(url)] if url.strip() else [])
    sec_snips = fetch_sec_snippets(kw) if kw.strip() else []
    sow = generate_sow(data, desc, examples, sec_snips, role)
    st.session_state.sow_content = sow
    st.subheader("Generated Scope of Work")
    st.write(sow)

# ✅ Iterative Refinement
st.header("Refine SoW")
if st.session_state.sow_content:
    st.text_area("Current SoW", st.session_state.sow_content, height=300, disabled=True)
    feedback = st.text_area("Suggest improvements")
    if st.button("Apply Refinement"):
        if feedback.strip():
            prompt = f"""
Contract lawyer + business expert: refine this Scope of Work as per user request.

Current SoW:
{st.session_state.sow_content}

User Feedback:
{feedback.strip()}
"""
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role":"system","content":"You are a contract lawyer AND business expert."},
                    {"role":"user","content":prompt}
                ],
                temperature=0.4
            )
            st.session_state.sow_content = highlight_figures(resp.choices[0].message.content)
            st.success("Refined!")
else:
    st.info("Generate a Scope of Work first.")
