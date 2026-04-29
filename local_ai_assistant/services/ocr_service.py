"""
services/ocr_service.py
======================
OCR service with graceful Tesseract detection and fallback handling.

Handles:
- Tesseract-OCR path detection
- Poppler/pdf2image integration
- Fallback strategies when OCR unavailable
- Error reporting and recovery

Usage::

    from services.ocr_service import ocr_service

    if ocr_service.is_available:
        text = ocr_service.ocr_image(image)
    else:
        print(f"OCR not available: {ocr_service.unavailable_reason}")
"""

from __future__ import annotations

import os
from typing import Optional
from pathlib import Path

from core.logging_config import get_logger
from core.runtime_paths import find_tesseract, find_poppler, is_frozen

log = get_logger(__name__)


class OCRService:
    """Manages OCR operations with Tesseract."""
    
    def __init__(self):
        self.tesseract_path: Optional[str] = None
        self.poppler_available: bool = False
        self._checked = False
        self.unavailable_reason: str = ""
    
    def check_availability(self) -> None:
        """Check if Tesseract and dependencies are available."""
        if self._checked:
            return
        
        self._checked = True
        
        # Check Tesseract
        self.tesseract_path = find_tesseract()
        if not self.tesseract_path:
            self.unavailable_reason = (
                "Tesseract-OCR not found. "
                "Install from https://github.com/UB-Mannheim/tesseract-ocr-w64-setup-v5.x.exe "
                "or set TESSERACT_CMD environment variable."
            )
            log.warning(f"OCR unavailable: {self.unavailable_reason}")
            return
        
        # Check Poppler (required for pdf2image)
        self.poppler_available = find_poppler() is not None
        if not self.poppler_available:
            log.warning(
                "Poppler not found on PATH. PDF→image conversion won't work. "
                "Download from: https://github.com/oschwartz10612/poppler-windows/releases"
            )
        
        log.info(f"✓ OCR service initialized - Tesseract: {self.tesseract_path}")
    
    @property
    def is_available(self) -> bool:
        """Check if OCR is available."""
        if not self._checked:
            self.check_availability()
        return self.tesseract_path is not None
    
    @property
    def is_pdf_conversion_available(self) -> bool:
        """Check if PDF→image conversion is available."""
        if not self._checked:
            self.check_availability()
        return self.poppler_available
    
    def ocr_image(self, image_path: str, language: str = 'eng', psm: int = 6) -> str:
        """Run OCR on an image file.
        
        Parameters
        ----------
        image_path:
            Path to image file.
        language:
            Tesseract language code (e.g., 'eng', 'deu', 'fra').
        psm:
            Page segmentation mode (0-13). Default 6 = single block of text.
        
        Returns
        -------
        Extracted text or empty string if OCR failed.
        """
        if not self.is_available:
            log.warning(f"OCR unavailable: {self.unavailable_reason}")
            return ""
        
        try:
            import pytesseract
            from PIL import Image
            
            # Verify file exists
            if not os.path.exists(image_path):
                log.error(f"Image file not found: {image_path}")
                return ""
            
            # Set tesseract command
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_path
            
            # Load and OCR
            img = Image.open(image_path)
            config = f'--psm {psm}'
            text = pytesseract.image_to_string(img, lang=language, config=config)
            
            if text:
                log.debug(f"OCR extracted {len(text)} chars from {image_path}")
            return text.strip()
        
        except Exception as e:
            log.error(f"OCR failed on {image_path}: {e}")
            return ""
    
    def ocr_pil_image(self, pil_image, language: str = 'eng', psm: int = 6) -> str:
        """Run OCR on a PIL Image object.
        
        Parameters
        ----------
        pil_image:
            PIL Image object.
        language:
            Tesseract language code.
        psm:
            Page segmentation mode.
        
        Returns
        -------
        Extracted text or empty string if OCR failed.
        """
        if not self.is_available:
            return ""
        
        try:
            import pytesseract
            
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_path
            config = f'--psm {psm}'
            text = pytesseract.image_to_string(pil_image, lang=language, config=config)
            
            return text.strip()
        except Exception as e:
            log.error(f"OCR on PIL image failed: {e}")
            return ""
    
    def ocr_pdf_with_fallback(
        self,
        pdf_path: str,
        fallback_text: str = ""
    ) -> str:
        """Convert PDF to images and run OCR, with fallback.
        
        Parameters
        ----------
        pdf_path:
            Path to PDF file.
        fallback_text:
            Text to return if conversion/OCR fails.
        
        Returns
        -------
        Extracted text from PDF pages, or fallback_text if extraction failed.
        """
        if not self.is_available:
            log.warning("OCR unavailable, using fallback text")
            return fallback_text
        
        if not self.is_pdf_conversion_available:
            log.warning("PDF→image conversion unavailable")
            return fallback_text
        
        try:
            from pdf2image import convert_from_path
            from PIL import ImageEnhance
            
            # Convert PDF pages to images
            log.info(f"Converting PDF to images: {pdf_path}")
            pages = convert_from_path(pdf_path, dpi=200)
            
            all_text = []
            for page_num, page_img in enumerate(pages, start=1):
                try:
                    # Enhance contrast for better OCR
                    gray = page_img.convert("L")
                    enhanced = ImageEnhance.Contrast(gray).enhance(2.0)
                    
                    text = self.ocr_pil_image(enhanced)
                    if text:
                        all_text.append(text)
                        log.debug(f"OCR page {page_num}: {len(text)} chars")
                
                except Exception as e:
                    log.warning(f"OCR failed on page {page_num}: {e}")
            
            result = "\n\n".join(all_text).strip()
            if result:
                log.info(f"PDF OCR success: {len(result)} chars from {len(pages)} pages")
            else:
                log.warning("PDF OCR produced no text")
            
            return result if result else fallback_text
        
        except Exception as e:
            log.error(f"PDF OCR conversion failed: {e}")
            return fallback_text


# Singleton instance
_ocr_service: Optional[OCRService] = None


def get_ocr_service() -> OCRService:
    """Get or create the singleton OCR service."""
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = OCRService()
        _ocr_service.check_availability()
    return _ocr_service


# Convenience
ocr_service = get_ocr_service()
