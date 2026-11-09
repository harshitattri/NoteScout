"""Reusable search/extraction logic for the independently deployable web app."""
from io import BytesIO
from pathlib import Path
import re

SUPPORTED = {".pdf", ".pptx", ".txt", ".png", ".jpg", ".jpeg", ".bmp"}
MAX_PDF_PAGES = 80
MAX_SLIDES = 150


def ocr_image(image):
    import pytesseract
    from PIL import ImageOps

    fixed = ImageOps.exif_transpose(image)
    try:
        rgb = fixed.convert("RGB")
        try:
            rgb.thumbnail((3000, 3000))
            return pytesseract.image_to_string(
                rgb, lang="eng", config="--psm 3", timeout=30
            )
        finally:
            rgb.close()
    finally:
        fixed.close()


def slide_text(shapes):
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    parts = []
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            parts.append(slide_text(shape.shapes))
        elif shape.has_text_frame:
            parts.append(shape.text_frame.text)
        elif shape.has_table:
            for row in shape.table.rows:
                parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def iter_sections(filename, content, ocr_all_pdf=False):
    """Yield (location, extracted text) without persisting uploaded bytes."""
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED:
        raise ValueError("Unsupported format: " + extension)

    if extension == ".txt":
        if content.startswith((b"\xff\xfe", b"\xfe\xff")):
            decoded = content.decode("utf-16")
        else:
            decoded = content.decode("utf-8-sig")
        for number, line in enumerate(decoded.splitlines(), 1):
            yield f"Line {number}", line
        return

    if extension == ".pptx":
        from pptx import Presentation

        presentation = Presentation(BytesIO(content))
        if len(presentation.slides) > MAX_SLIDES:
            raise ValueError(f"Presentations are limited to {MAX_SLIDES} slides.")
        for number, slide in enumerate(presentation.slides, 1):
            yield f"Slide {number}", slide_text(slide.shapes)
        return

    if extension == ".pdf":
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(content)
        try:
            if len(document) > MAX_PDF_PAGES:
                raise ValueError(f"PDFs are limited to {MAX_PDF_PAGES} pages.")
            for index in range(len(document)):
                page = document[index]
                try:
                    text_page = page.get_textpage()
                    try:
                        text = text_page.get_text_range()
                    finally:
                        text_page.close()
                    location = f"Page {index + 1}"
                    if ocr_all_pdf or not text.strip():
                        width, height = page.get_size()
                        scale = min(2.5, 3000 / max(width, height, 1))
                        bitmap = page.render(scale=scale)
                        try:
                            image = bitmap.to_pil()
                            try:
                                ocr_text = ocr_image(image)
                            finally:
                                image.close()
                        finally:
                            bitmap.close()
                        text += "\n" + ocr_text
                        location += " (OCR)"
                    yield location, text
                finally:
                    page.close()
        finally:
            document.close()
        return

    from PIL import Image

    with Image.open(BytesIO(content)) as image:
        yield "Image (OCR)", ocr_image(image)


def search_content(filename, content, phrase, ocr_all_pdf=False):
    """Return page/slide/line matches with escaped rendering handled by the UI."""
    phrase = " ".join(phrase.split())
    if not phrase:
        raise ValueError("Enter a word or phrase to search.")
    pattern = re.compile(re.escape(phrase), re.IGNORECASE)
    results = []
    for location, text in iter_sections(filename, content, ocr_all_pdf):
        clean = " ".join(text.split())
        match = pattern.search(clean)
        if match:
            start = max(0, match.start() - 90)
            end = min(len(clean), match.end() + 180)
            snippet = ("…" if start else "") + clean[start:end] + ("…" if end < len(clean) else "")
            results.append({
                "filename": filename,
                "location": location,
                "snippet": snippet,
            })
            if len(results) >= 100:
                break
    return results
