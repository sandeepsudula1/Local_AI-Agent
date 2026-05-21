"""
services/academic_pdf_parser.py
================================
Advanced Layout-Aware Position-Reconstructed Parser for Structured Academic PDFs.
Supports scanned/scrambled multi-column Cumulative Marks Memos (CMMs) and Transcripts.
Directly parses semesters, subjects, credits, grades, SGPAs, CGPAs, and candidate details.
"""

from __future__ import annotations

import os
import re
import time
from typing import Optional, Dict, List, Tuple
from core.logging_config import get_logger
from configs.settings import settings

log = get_logger(__name__)

# Heuristics for structured academic detection
_ACADEMIC_KEYWORDS = [
    r"\bcgpa\b", r"\bsgpa\b", r"\bmarks\s+memo\b", r"\bgrade\s+sheet\b", 
    r"\btranscript\b", r"\bcumulative\s+marks\b", r"\bhall\s+ticket\b",
    r"\bsemester\b", r"\bsubject\s+code\b", r"\bgrade\s+points\b",
    r"\bcredits\b", r"\bpass\s+class\b", r"\bconsolidated\b"
]

# Semester Header Recognition Regex
SEM_HEADER_RE = re.compile(
    r"\b(?:semester|year|sem\b|tyear|year\s+s\b|w\s+year|v\s+year)\b",
    re.IGNORECASE
)

class AcademicPDFParser:
    """Specialized coordinate-based multi-column layout parser for academic marks memos."""

    @staticmethod
    def is_academic_pdf(path: str) -> bool:
        """Heuristically detect if a PDF is a structured academic marks memo / CMM."""
        ext = os.path.splitext(path)[1].lower()
        if ext != ".pdf":
            return False

        # Quick filename bypass
        name_lower = os.path.basename(path).lower()
        if any(w in name_lower for w in ["cmm", "marks", "memo", "transcript", "grade", "result", "academic"]):
            log.info(f"[DOC_TYPE] Filename match: {name_lower} is likely structured academic")
            return True

        # Quick pypdf check (first 2000 chars of text)
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            if not reader.pages:
                return False
            first_page_text = reader.pages[0].extract_text() or ""
            text_lower = first_page_text.lower()
            
            matches = 0
            for kw in _ACADEMIC_KEYWORDS:
                if re.search(kw, text_lower):
                    matches += 1
            
            is_academic = matches >= 3
            log.info(f"[DOC_TYPE] Keyword analysis matches={matches} for {name_lower} -> structured={is_academic}")
            return is_academic
        except Exception as e:
            log.warning(f"Error checking academic PDF heuristics: {e}")
            return False

    @classmethod
    def parse(cls, path: str) -> dict:
        """Execute hybrid layout-aware positional table reconstruction and metadata parsing."""
        t0 = time.perf_counter()
        
        result = {
            "student_name": "N/A",
            "hall_ticket_no": "N/A",
            "cgpa": "N/A",
            "total_credits": "N/A",
            "issue_date": "N/A",
            "semesters": {},
            "subjects": [],
            "raw_text": "",
            "parse_success": False
        }

        try:
            # Step 1. Get raw text to extract top-level details (CGPA, Name, HT No, Credits, Date)
            raw_text = cls._extract_raw_digital_text(path)
            
            # Step 2. Determine if PDF is scanned or digital by text length
            is_scanned = len(raw_text.strip()) < 100
            
            if is_scanned:
                log.info(f"[TABLE_EXTRACTION] Scanned PDF detected. Executing layout-aware OCR...")
                ocr_raw, left_lines, right_lines = cls._extract_ocr_layout_lines(path)
                raw_text = ocr_raw
            else:
                log.info(f"[TABLE_EXTRACTION] Digital PDF detected. Executing pdfplumber coordinates split...")
                left_lines, right_lines = cls._extract_digital_layout_lines(path)

            result["raw_text"] = raw_text
            
            # Step 3. Extract Top-Level Metadata from the full consolidated text
            cls._extract_global_metadata(raw_text, result)
            
            # Step 4. Process Left Column & Right Column lines with sequential Semester transition
            cls._process_columns(left_lines, right_lines, result)

            result["parse_success"] = len(result["subjects"]) > 0
            latency = (time.perf_counter() - t0) * 1000
            
            print(f"[DOC_TYPE] STRUCTURED_ACADEMIC")
            print(f"[TABLE_EXTRACTION] Loaded {len(result['semesters'])} semesters")
            print(f"[SUBJECT_COUNT] {len(result['subjects'])}")
            print(f"[CGPA_EXTRACT] {result['cgpa']}")
            print(f"[CREDITS_FOUND] {result['total_credits']}")
            print(f"[STRUCTURED_PARSE] Successfully completed in {latency:.2f}ms")

        except Exception as e:
            log.error(f"Error parsing academic PDF: {e}")
            result["parse_success"] = False

        return result

    @staticmethod
    def _extract_raw_digital_text(path: str) -> str:
        """Extract digital text from PDF using pypdf."""
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            full_text = ""
            for page in reader.pages:
                full_text += (page.extract_text() or "") + "\n"
            return full_text
        except Exception:
            return ""

    @classmethod
    def _extract_ocr_layout_lines(cls, path: str) -> Tuple[str, List[str], List[str]]:
        """Run Tesseract image-to-data layout-aware column splitter for scanned PDFs."""
        ocr_raw_parts = []
        left_column_lines = []
        right_column_lines = []

        try:
            from pdf2image import convert_from_path
            from PIL import ImageEnhance
            import pytesseract
            
            # Locate Tesseract Path
            tesseract_path = getattr(settings, 'tesseract_cmd', r"C:\Program Files\Tesseract-OCR\tesseract.exe")
            if not os.path.exists(tesseract_path):
                tesseract_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            pytesseract.pytesseract.tesseract_cmd = tesseract_path

            # Convert PDF pages to images
            pages = convert_from_path(path, dpi=200)
            
            for page_num, page_img in enumerate(pages, start=1):
                gray = page_img.convert("L")
                enhanced = ImageEnhance.Contrast(gray).enhance(2.0)
                
                # Get raw text
                raw_text = pytesseract.image_to_string(enhanced)
                ocr_raw_parts.append(raw_text)
                
                # Group overlapping words vertically using image_to_data coordinates
                data = pytesseract.image_to_data(enhanced, output_type=pytesseract.Output.DICT)
                
                words = []
                for i in range(len(data['level'])):
                    text = data['text'][i].strip()
                    conf = int(data['conf'][i])
                    if text and conf > 10:
                        words.append({
                            'text': text,
                            'left': data['left'][i],
                            'top': data['top'][i],
                            'width': data['width'][i],
                            'height': data['height'][i]
                        })
                
                words.sort(key=lambda w: w['top'])
                
                lines = []
                if words:
                    current_line = [words[0]]
                    for w in words[1:]:
                        # Group words with vertical offset within 12 pixels
                        if abs(w['top'] - current_line[-1]['top']) < 12:
                            current_line.append(w)
                        else:
                            lines.append(current_line)
                            current_line = [w]
                    lines.append(current_line)
                
                mid_x = enhanced.width / 2
                
                for line in lines:
                    line.sort(key=lambda w: w['left'])
                    
                    left_words = [w['text'] for w in line if w['left'] < mid_x - 30]
                    right_words = [w['text'] for w in line if w['left'] > mid_x + 30]
                    
                    l_str = " ".join(left_words).strip()
                    r_str = " ".join(right_words).strip()
                    
                    if l_str:
                        left_column_lines.append(l_str)
                    if r_str:
                        right_column_lines.append(r_str)
                        
            print(f"[OCR_WORDS] Extracted layout coordinates from {len(pages)} pages")
        except Exception as e:
            log.error(f"OCR layout splitting failed: {e}")
            
        return "\n".join(ocr_raw_parts), left_column_lines, right_column_lines

    @classmethod
    def _extract_digital_layout_lines(cls, path: str) -> Tuple[List[str], List[str]]:
        """Split columns using pdfplumber horizontal coordinates for digital PDFs."""
        left_column_lines = []
        right_column_lines = []

        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    mid_x = page.width / 2
                    words = page.extract_words()
                    
                    # Sort words by top coordinate
                    words.sort(key=lambda w: w['top'])
                    
                    lines = []
                    if words:
                        current_line = [words[0]]
                        for w in words[1:]:
                            # Group words vertically within 3 points
                            if abs(w['top'] - current_line[-1]['top']) < 3:
                                current_line.append(w)
                            else:
                                lines.append(current_line)
                                current_line = [w]
                        lines.append(current_line)
                        
                    for line in lines:
                        line.sort(key=lambda w: w['x0'])
                        
                        left_words = [w['text'] for w in line if w['x1'] < mid_x - 10]
                        right_words = [w['text'] for w in line if w['x0'] > mid_x + 10]
                        
                        l_str = " ".join(left_words).strip()
                        r_str = " ".join(right_words).strip()
                        
                        if l_str:
                            left_column_lines.append(l_str)
                        if r_str:
                            right_column_lines.append(r_str)
        except Exception as e:
            log.error(f"pdfplumber layout splitting failed: {e}")

        return left_column_lines, right_column_lines

    @classmethod
    def _extract_global_metadata(cls, text: str, result: dict) -> None:
        """Extract top-level student and overall grade performance fields from raw text."""
        # 1. Parse Student Name
        name_patterns = [
            r"(?:student\s+name|candidate's\s+name|name\s+of\s+candidate|candidate\s+name|name\s+of\s+student|name)\s*[:\-=]\s*([A-Za-z\s\.]{3,50})",
            r"name\s+of\s+the\s+candidate\s*[:\-=]?\s*([A-Za-z\s\.]{3,50})"
        ]
        for pat in name_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                result["student_name"] = m.group(1).strip().split("\n")[0].strip()
                break

        # 2. Parse Hall Ticket / Roll No
        ht_patterns = [
            r"(?:hall\s*ticket\s*no|h\.?t\.?\s*no|roll\s*no|roll\s*number|ticket\s*number|enrollment\s*no|registration\s*no)\s*[:\-=]?\s*([A-Za-z0-9\-]{5,20})"
        ]
        for pat in ht_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                result["hall_ticket_no"] = m.group(1).strip()
                break
        
        # 3. Parse CGPA
        cgpa_patterns = [
            r"(?:cgpa|c\.g\.p\.a|cumulative\s+grade\s+point\s+average|marks/cgpa|secured)\s*[:\-=]?\s*([0-9\.]+)"
        ]
        for pat in cgpa_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                result["cgpa"] = m.group(1).strip()
                break

        # 4. Parse Total Credits
        credits_patterns = [
            r"credits\s+(?:registered\s+and\s+)?secured\s+(?:are\s+)?(\d+)",
            r"total\s+credits\s*[:\-=]?\s*(\d+)"
        ]
        for pat in credits_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                result["total_credits"] = m.group(1).strip()
                break
                
        # 5. Parse Date of Issue
        date_patterns = [
            r"date\s+of\s+issue\s*[:\-=]?\s*([A-Za-z0-9\s,\.]+)"
        ]
        for pat in date_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                result["issue_date"] = m.group(1).strip().split("\n")[0].strip()
                break

    @classmethod
    def _process_columns(cls, left_lines: List[str], right_lines: List[str], result: dict) -> None:
        """Parse Left and Right columns sequentially, managing semester transitions cleanly."""
        left_sems = ["I YEAR - I SEMESTER", "II YEAR - I SEMESTER", "III YEAR - I SEMESTER", "IV YEAR - I SEMESTER"]
        right_sems = ["I YEAR - II SEMESTER", "II YEAR - II SEMESTER", "III YEAR - II SEMESTER", "IV YEAR - II SEMESTER"]

        # Parse Left Column
        left_idx = -1
        current_sem = left_sems[0]
        
        for line in left_lines:
            # Check for semester transition
            if SEM_HEADER_RE.search(line) and any(w in line.upper() for w in ["SEMESTER", "SEM", "YEAR"]):
                left_idx = min(left_idx + 1, 3)
                current_sem = left_sems[left_idx]
                if current_sem not in result["semesters"]:
                    result["semesters"][current_sem] = []
                continue
                
            sub = cls._parse_subject_line(line)
            if sub:
                if left_idx == -1:
                    left_idx = 0
                    current_sem = left_sems[0]
                    
                if current_sem not in result["semesters"]:
                    result["semesters"][current_sem] = []
                    
                sub["semester"] = current_sem
                result["subjects"].append(sub)
                result["semesters"][current_sem].append(sub)
                
        # Parse Right Column
        right_idx = -1
        current_sem = right_sems[0]
        
        for line in right_lines:
            # Check for semester transition
            if SEM_HEADER_RE.search(line) and any(w in line.upper() for w in ["SEMESTER", "SEM", "YEAR"]):
                right_idx = min(right_idx + 1, 3)
                current_sem = right_sems[right_idx]
                if current_sem not in result["semesters"]:
                    result["semesters"][current_sem] = []
                continue
                
            sub = cls._parse_subject_line(line)
            if sub:
                if right_idx == -1:
                    right_idx = 0
                    current_sem = right_sems[0]
                    
                if current_sem not in result["semesters"]:
                    result["semesters"][current_sem] = []
                    
                sub["semester"] = current_sem
                result["subjects"].append(sub)
                result["semesters"][current_sem].append(sub)

    @staticmethod
    def _parse_subject_line(line: str) -> Optional[dict]:
        """Position-reconstruct and self-heal messy subject rows from OCR output."""
        # Clean trailing borders
        line = re.sub(r'[\s\|\&\=\-\)\}\]]+$', '', line).strip()
        
        # Strip structural noise, normalize separators
        cleaned = re.sub(r'[\{\}\[\]\(\|\)\\\/\:\*]', ' ', line)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        parts = cleaned.split(' ')
        if len(parts) < 2:
            return None
            
        # Heuristic: strip watermarks/column markers OCR-ed at the very end of row
        if len(parts) >= 3 and not re.match(r'^\d+(?:\.\d+)?$', parts[-1]) and re.match(r'^\d+(?:\.\d+)?$', parts[-2]):
            parts = parts[:-1]
            
        if len(parts) < 2:
            return None
            
        credits_val = "N/A"
        grade_val = "N/A"
        points_val = "N/A"
        subject_name = ""
        
        # Primary Match: Last part is a digit/float (Credits)
        if re.match(r'^\d+(?:\.\d+)?$', parts[-1]):
            credits_val = parts[-1]
            
            # Second to last is Grade
            grade_cand = parts[-2].upper()
            grade_cand = grade_cand.replace('AT', 'A+').replace('AT+', 'A+').replace('0', 'O')
            
            if grade_cand in ['O', 'S', 'A+', 'A', 'B+', 'B', 'C', 'D', 'P', 'F', 'FAIL', 'PASS', 'F*', 'AB', '-']:
                grade_val = grade_cand
                
                # Third to last is Grade Points
                if len(parts) >= 3 and re.match(r'^\d+$', parts[-3]):
                    points_val = parts[-3]
                    subject_name = " ".join(parts[:-3])
                else:
                    subject_name = " ".join(parts[:-2])
            else:
                # Secondary Check: Grade points and letter grade are merged (e.g. 51C -> points 5, grade C)
                m = re.match(r'^(\d+)?([A-F|O|S|P][\+\-\*]?)$', parts[-2], re.IGNORECASE)
                if m:
                    points_val = m.group(1) or "N/A"
                    grade_val = m.group(2).upper().replace('0', 'O').replace('AT', 'A+')
                    subject_name = " ".join(parts[:-2])
                else:
                    pass
        
        # Secondary Regex Fallback
        if grade_val == "N/A" or credits_val == "N/A":
            m = re.search(r'([A-Za-z0-9\s\-]+?)\s+(\*|\-|\d+)?\s+([A-Z0-9\+\-\*]+)?\s+(\d+(?:\.\d+)?)$', " ".join(parts))
            if m:
                subject_name = m.group(1).strip()
                points_val = m.group(2) or "N/A"
                grade_val = m.group(3) or "N/A"
                credits_val = m.group(4) or "N/A"
            else:
                m2 = re.search(r'([A-Za-z0-9\s\-]+?)\s+([A-F|O|S|P][\+\-\*]?)\s+(\d+(?:\.\d+)?)$', " ".join(parts), re.IGNORECASE)
                if m2:
                    subject_name = m2.group(1).strip()
                    grade_val = m2.group(2).upper()
                    credits_val = m2.group(3)
                    points_val = "N/A"

        if not subject_name:
            subject_name = " ".join(parts[:-1]) if len(parts) > 1 else parts[0]
            
        # Final cleanup of code headers or leading digits
        subject_name = re.sub(r'^\d+\s*\|\s*', '', subject_name)
        subject_name = re.sub(r'^\d+\.?\s*', '', subject_name)
        subject_name = re.sub(r'^\b(?:I|II|III|IV|V)\b\s*', '', subject_name)
        subject_name = subject_name.strip()
        
        # Filter headers or noise
        if len(subject_name) < 4 or any(w in subject_name.upper() for w in ['SEMESTER', 'YEAR', 'SUBJECT TITLE', 'CGPA', 'CREDITS', 'CONSOLIDATED', 'NAME', 'TICKET', 'CLASS AWARDED']):
            return None
            
        return {
            "name": subject_name,
            "points": points_val,
            "grade": grade_val,
            "credits": credits_val
        }


def handle_structured_academic_qa(path: str, query: str) -> str:
    """Specialized structured QA generator for academic files."""
    import json
    import difflib
    import re
    import ollama
    from configs.llm_config import MODEL
    
    data = AcademicPDFParser.parse(path)
    query_lower = query.lower()
    
    # 1. Get History
    try:
        from memory.conversation_memory import conversation_memory
        history = conversation_memory.get_history(last_n=6)
    except Exception as e:
        log.debug(f"Failed to get conversation history: {e}")
        history = []

    # Construct the query analyzer prompt
    # Instruct it to check if it's a follow-up, and extract key fields
    analyzer_prompt = (
        "You are an academic records query analyzer. Analyze the user's query and optionally the conversation history to extract the structured intent, operation, and target.\n\n"
        "Available academic structure fields:\n"
        "- student_name (str)\n"
        "- hall_ticket_no (str)\n"
        "- cgpa (str)\n"
        "- total_credits (str)\n"
        "- issue_date (str)\n"
        "- semesters (dict mapping semester name to list of subjects)\n"
        "- subjects (list of subjects, where each subject has: name, grade, credits, points)\n\n"
        "Evaluate if the current query depends on the previous conversation history (e.g. referring to a previously mentioned subject or semester with pronouns like 'it', 'that course', 'did they pass?'). "
        "If it is an independent query (e.g. 'how many semesters mentioned', 'what is the cgpa', 'list all subjects'), set is_follow_up to false. "
        "IGNORE previous context completely if is_follow_up is false to prevent context contamination.\n\n"
        "Output EXACTLY a JSON object with the following keys. Do NOT include markdown code blocks, explanations, or extra text. Output ONLY valid JSON.\n\n"
        "JSON structure:\n"
        "{\n"
        '  "is_follow_up": true | false,\n'
        '  "intent": "count_semesters" | "count_subjects" | "get_student_metadata" | "get_grades" | "get_cgpa_or_credits" | "get_grade_distribution" | "get_issue_date" | "get_summary" | "general_qa",\n'
        '  "operation": "COUNT" | "FILTER" | "GET_VALUE" | "SUMMARIZE" | "NONE",\n'
        '  "target_entity": "semesters" | "subjects" | "cgpa" | "credits" | "student_metadata" | "issue_date" | "subject_name" | "none",\n'
        '  "subject_search_terms": [list of subject names to search for, or empty list],\n'
        '  "semester_search_terms": [list of semesters/years to search for, or empty list],\n'
        '  "grade_search_terms": [list of grades to search for, or empty list],\n'
        '  "resolved_query_context": "clear statement of the user\'s real question, resolving any pronouns from history only if is_follow_up is true"\n'
        "}\n\n"
        "--- CONVERSATION HISTORY ---\n"
    )
    # Include history turns only to help resolve follow-ups
    for turn in history:
        analyzer_prompt += f"{turn['role'].capitalize()}: {turn['content']}\n"
    
    analyzer_prompt += f"\nUser Query: {query}\nJSON query plan: "

    model_name = MODEL if MODEL else getattr(settings, 'model_name', 'qwen2.5:3b')

    plan = None
    try:
        resp = ollama.chat(
            model=model_name,
            options={"temperature": 0.0},
            messages=[{"role": "user", "content": analyzer_prompt}]
        )
        content = resp.get("message", {}).get("content", "").strip()
        
        # Clean JSON markdown blocks if any
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\n", "", content)
            content = re.sub(r"\n```$", "", content)
        content = content.strip()
        
        plan = json.loads(content)
    except Exception as e:
        log.warning(f"Failed to analyze query via LLM: {e}. Falling back to rule-based analyzer.")

    # Rule-based fallback if LLM analysis fails
    if not plan or not isinstance(plan, dict):
        plan = {
            "is_follow_up": False,
            "intent": "general_qa",
            "operation": "NONE",
            "target_entity": "none",
            "subject_search_terms": [],
            "semester_search_terms": [],
            "grade_search_terms": [],
            "resolved_query_context": f"Fallback: {query}"
        }
        
        if any(w in query_lower for w in ["semester count", "how many semester", "semesters mentioned"]):
            plan["intent"] = "count_semesters"
            plan["operation"] = "COUNT"
            plan["target_entity"] = "semesters"
            plan["resolved_query_context"] = "Count the total semesters mentioned in the document"
        elif any(w in query_lower for w in ["how many subjects", "subject count", "total subjects"]):
            plan["intent"] = "count_subjects"
            plan["operation"] = "COUNT"
            plan["target_entity"] = "subjects"
            plan["resolved_query_context"] = "Count the total subjects in the document"
        elif any(w in query_lower for w in ["cgpa", "credits", "gpa", "overall score"]):
            plan["intent"] = "get_cgpa_or_credits"
            plan["operation"] = "GET_VALUE"
            plan["target_entity"] = "cgpa"
            plan["resolved_query_context"] = "Retrieve CGPA and total credits"
        elif any(w in query_lower for w in ["date", "issue", "issued"]):
            plan["intent"] = "get_issue_date"
            plan["operation"] = "GET_VALUE"
            plan["target_entity"] = "issue_date"
            plan["resolved_query_context"] = "Retrieve issue date"
        elif any(w in query_lower for w in ["summarize", "summary", "overview"]):
            plan["intent"] = "get_summary"
            plan["operation"] = "SUMMARIZE"
            plan["target_entity"] = "subjects"
            plan["resolved_query_context"] = "Generate a consolidated academic summary"
        else:
            plan["intent"] = "get_grades"
            plan["operation"] = "FILTER"
            plan["target_entity"] = "subjects"
            plan["resolved_query_context"] = f"Retrieve subject specific details for: {query}"
            
        stopwords = {"what", "when", "where", "which", "that", "this", "have", "from", "with", "does", "about", "were", "will", "student", "subject", "semester", "grade"}
        words = re.findall(r"[a-zA-Z]{3,}", query_lower)
        terms = [w for w in words if w not in stopwords]
        if terms:
            plan["subject_search_terms"] = terms

    # Extract fields from the plan
    is_follow_up = plan.get("is_follow_up", False)
    intent = plan.get("intent", "general_qa")
    operation = plan.get("operation", "NONE")
    target_entity = plan.get("target_entity", "none")
    resolved_context = plan.get("resolved_query_context", query)

    # Calculate Ground Truth deterministically
    ground_truth_val = None
    ground_truth_label = ""

    if intent == "count_semesters" or (target_entity == "semesters" and operation == "COUNT"):
        ground_truth_val = len(data.get("semesters", {}))
        ground_truth_label = "semesters count"
    elif intent == "count_subjects" or (target_entity == "subjects" and operation == "COUNT"):
        ground_truth_val = len(data.get("subjects", []))
        ground_truth_label = "subjects count"
    elif intent == "get_cgpa_or_credits":
        ground_truth_val = f"CGPA: {data.get('cgpa')}, Credits: {data.get('total_credits')}"
        ground_truth_label = "academic stats"
    elif intent == "get_issue_date":
        ground_truth_val = data.get("issue_date")
        ground_truth_label = "issue date"
    elif intent == "get_student_metadata":
        ground_truth_val = f"Name: {data.get('student_name')}, Hall Ticket: {data.get('hall_ticket_no')}"
        ground_truth_label = "student metadata"

    # Search & Match parsed academic records for subjects/semesters
    matched_subjects = []
    matched_semesters = []

    # Fuzzy match semesters if terms are present
    if plan.get("semester_search_terms"):
        sem_mapping = {
            "I YEAR - I SEMESTER": ["1-1", "sem 1", "semester 1", "first semester", "i year i sem", "1st sem", "y1s1"],
            "I YEAR - II SEMESTER": ["1-2", "sem 2", "semester 2", "second semester", "i year ii sem", "2nd sem", "y1s2"],
            "II YEAR - I SEMESTER": ["2-1", "sem 3", "semester 3", "third semester", "ii year i sem", "3rd sem", "y2s1"],
            "II YEAR - II SEMESTER": ["2-2", "sem 4", "semester 4", "fourth semester", "ii year ii sem", "4th sem", "y2s2"],
            "III YEAR - I SEMESTER": ["3-1", "sem 5", "semester 5", "fifth semester", "iii year i sem", "5th sem", "y3s1"],
            "III YEAR - II SEMESTER": ["3-2", "sem 6", "semester 6", "sixth semester", "iii year ii sem", "6th sem", "y3s2"],
            "IV YEAR - I SEMESTER": ["4-1", "sem 7", "semester 7", "seventh semester", "iv year i sem", "7th sem", "y4s1"],
            "IV YEAR - II SEMESTER": ["4-2", "sem 8", "semester 8", "eighth semester", "iv year ii sem", "8th sem", "y4s2"],
        }
        for sem_name, synonyms in sem_mapping.items():
            if sem_name not in data.get("semesters", {}):
                continue
            for term in plan["semester_search_terms"]:
                term_lower = term.lower()
                if term_lower in sem_name.lower():
                    matched_semesters.append(sem_name)
                    break
                found_syn = False
                for syn in synonyms:
                    if term_lower in syn or syn in term_lower:
                        matched_semesters.append(sem_name)
                        found_syn = True
                        break
                if found_syn:
                    break

    # Fuzzy match subjects if terms are present
    if plan.get("subject_search_terms"):
        for sub in data.get("subjects", []):
            sub_name = sub["name"]
            sub_name_lower = sub_name.lower()
            best_score = 0.0
            
            for term in plan["subject_search_terms"]:
                term_lower = term.lower()
                if term_lower in sub_name_lower or sub_name_lower in term_lower:
                    best_score = max(best_score, 1.0)
                else:
                    sub_words = set(re.findall(r"\w+", sub_name_lower))
                    term_words = set(re.findall(r"\w+", term_lower))
                    if sub_words and term_words:
                        overlap = len(sub_words & term_words)
                        overlap_score = overlap / max(len(sub_words), len(term_words))
                        best_score = max(best_score, overlap_score)
                    char_score = difflib.SequenceMatcher(None, sub_name_lower, term_lower).ratio()
                    best_score = max(best_score, char_score)
                    
            if best_score >= 0.5:
                matched_subjects.append((sub, best_score))
        
        matched_subjects.sort(key=lambda x: -x[1])
        matched_subjects = [item[0] for item in matched_subjects]

    # Print Trace Logs exactly as requested
    print(f"\n[QUERY_INTENT] {resolved_context}")
    print(f"[STRUCTURED_OPERATION] {operation}({target_entity})")
    
    # Format Ground Truth Output log line
    if ground_truth_val is not None:
        print(f"[GROUND_TRUTH] {ground_truth_val} {ground_truth_label}")
    elif matched_subjects:
        print(f"[GROUND_TRUTH] Matched subject: {matched_subjects[0]['name']} with grade {matched_subjects[0]['grade']}")
    else:
        print("[GROUND_TRUTH] Raw transcript data fields")

    # Construct clean response context
    context = f"""--- GROUND TRUTH DATA ---
Student: {data['student_name']}
Hall Ticket: {data['hall_ticket_no']}
CGPA: {data['cgpa']}
Total Credits: {data['total_credits']}
Issue Date: {data['issue_date']}
Total Semesters Parsed: {len(data['semesters'])}
Total Subjects Parsed: {len(data['subjects'])}
"""
    if matched_subjects:
        context += "\nMatched Subject Details:\n"
        for sub in matched_subjects:
            context += f"- {sub['name']}: Grade {sub['grade']}, Credits {sub['credits']} (Semester: {sub.get('semester', 'N/A')})\n"
    if matched_semesters:
        context += "\nMatched Semester Courses:\n"
        for sem in matched_semesters:
            context += f"Semester: {sem}\n"
            for sub in data.get("semesters", {}).get(sem, []):
                context += f"  * {sub['name']}: Grade {sub['grade']}, Credits {sub['credits']}\n"
                
    if intent == "get_grade_distribution" or "grade" in query.lower():
        grade_counts = {}
        for s in data.get("subjects", []):
            g = s["grade"]
            grade_counts[g] = grade_counts.get(g, 0) + 1
        context += "\nGrade Counts:\n"
        for g, count in sorted(grade_counts.items()):
            context += f"- Grade {g}: {count} time(s)\n"

    # Construct Prompt for Response Generation
    response_prompt = (
        "You are a helpful Multi-Agent Academic Q&A Assistant. Your response must be completely fact-grounded in the provided facts.\n\n"
        "--- ABSOLUTE GROUND TRUTH FACTS ---\n"
        f"{context}\n"
        "--- RESPONSE INSTRUCTIONS ---\n"
        "1. Answer ONLY using the facts listed above. Do not speculate or introduce values not present.\n"
        "2. Direct Answer ONLY: Output a short, precise answer (1 or 2 sentences max) directly answering the query. Do NOT add speculative semester breakdowns, overall summaries, or unnecessary commentary.\n"
        "3. Strict Values: If the Ground Truth says 'Total Semesters Parsed: 8', then the answer must contain 8 semesters. Do NOT invent other counts like 6.\n\n"
        "--- CONVERSATION HISTORY ---\n"
    )
    # Only include history if the planner says it's a follow-up, to prevent context contamination
    if is_follow_up:
        for turn in history:
            response_prompt += f"{turn['role'].capitalize()}: {turn['content']}\n"
            
    response_prompt += f"\nQuestion: {query}\nAnswer: "

    answer = ""
    try:
        resp = ollama.chat(
            model=model_name,
            options={"temperature": 0.0},
            messages=[{"role": "user", "content": response_prompt}]
        )
        answer = resp.get("message", {}).get("content", "").strip()
    except Exception as e:
        log.warning(f"Ollama structured answer generation failed: {e}")

    # Grounding Validation Layer
    validation_passed = True
    validation_reason = ""

    if intent == "count_semesters" or (target_entity == "semesters" and operation == "COUNT"):
        expected = len(data.get("semesters", {}))
        numbers_found = [int(n) for n in re.findall(r"\b\d+\b", answer)]
        if expected not in numbers_found:
            validation_passed = False
            validation_reason = f"Semester count {expected} not present in generated response '{answer}'."
    elif intent == "count_subjects" or (target_entity == "subjects" and operation == "COUNT"):
        expected = len(data.get("subjects", []))
        numbers_found = [int(n) for n in re.findall(r"\b\d+\b", answer)]
        if expected not in numbers_found:
            validation_passed = False
            validation_reason = f"Subject count {expected} not present in generated response '{answer}'."
    elif intent == "get_cgpa_or_credits":
        expected_cgpa = str(data.get("cgpa"))
        expected_credits = str(data.get("total_credits"))
        if expected_cgpa not in answer or expected_credits not in answer:
            validation_passed = False
            validation_reason = f"CGPA {expected_cgpa} or Credits {expected_credits} not present in generated response."

    if validation_passed and answer:
        print("[FINAL_VALIDATION] PASSED")
        return answer
    else:
        # Rejection & Deterministic response formatting
        if not validation_passed:
            print(f"[FINAL_VALIDATION] FAILED: {validation_reason}")
        else:
            print("[FINAL_VALIDATION] FAILED: No response generated by LLM")
            
        # Hard deterministic correct formatting fallback
        if intent == "count_semesters" or (target_entity == "semesters" and operation == "COUNT"):
            return f"There are {len(data.get('semesters', {}))} semesters mentioned in the document."
        elif intent == "count_subjects" or (target_entity == "subjects" and operation == "COUNT"):
            return f"There are a total of {len(data.get('subjects', []))} subjects mentioned in the document."
        elif intent == "get_cgpa_or_credits":
            return f"The student's Cumulative GPA (CGPA) is {data.get('cgpa')} and they have earned a total of {data.get('total_credits')} credits."
        elif intent == "get_issue_date":
            return f"The document of issue date is {data.get('issue_date')}."
        elif intent == "get_student_metadata":
            return f"The student is {data.get('student_name')} with Hall Ticket No {data.get('hall_ticket_no')}."
        elif intent == "get_grades" and matched_subjects:
            sub = matched_subjects[0]
            return f"The student got a {sub['grade']} grade in {sub['name']}."
        else:
            return f"The student {data.get('student_name')} has a CGPA of {data.get('cgpa')} with {len(data.get('semesters', {}))} semesters parsed from the memo."
