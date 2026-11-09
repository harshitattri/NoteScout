"""In-browser previews that show where a NoteScout hit occurs.

The functions use in-memory uploaded bytes. Nothing is saved to disk.
"""
from io import BytesIO
import re

from PIL import Image, ImageDraw


def _mark_image(image, boxes):
    """Add transparent highlights and return (full image, zoom around first hit)."""
    base = image.convert('RGB')
    if not boxes:
        return base, None
    tint = Image.new('RGBA', base.size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(tint, 'RGBA')
    for left, top, right, bottom in boxes[:80]:
        left, top = max(0, int(left)), max(0, int(top))
        right, bottom = min(base.width, int(right)), min(base.height, int(bottom))
        if left >= right or top >= bottom:
            continue
        pen.rectangle((left, top, right, bottom), fill=(255, 214, 0, 95),
                      outline=(245, 140, 0, 230), width=2)
    marked = Image.alpha_composite(base.convert('RGBA'), tint).convert('RGB')
    x1, y1, x2, y2 = [int(v) for v in boxes[0]]
    midx, midy = (x1 + x2) // 2, (y1 + y2) // 2
    w = min(marked.width, 900)
    h = min(marked.height, 440)
    left = max(0, min(marked.width - w, midx - w // 2))
    top = max(0, min(marked.height - h, midy - h // 2))
    return marked, marked.crop((left, top, left + w, top + h))


def _ocr_boxes(image, phrase):
    """Locate matching words in OCR text; supports multi-word phrases."""
    import pytesseract
    from pytesseract import Output

    raw = pytesseract.image_to_data(image, lang='eng', config='--psm 3',
                                     output_type=Output.DICT, timeout=30)
    words = []
    for i, value in enumerate(raw['text']):
        text = re.sub(r'\W+', '', value).casefold()
        if not text:
            continue
        words.append((text, (raw['left'][i], raw['top'][i],
                             raw['left'][i] + raw['width'][i],
                             raw['top'][i] + raw['height'][i])))
    target = [re.sub(r'\W+', '', v).casefold() for v in phrase.split()]
    target = [v for v in target if v]
    if not target:
        return []
    boxes = []
    for i in range(len(words) - len(target) + 1):
        if all((want in words[i+j][0] if len(target) == 1 else want == words[i+j][0])
               for j, want in enumerate(target)):
            boxes.extend(box for _, box in words[i:i+len(target)])
            if len(boxes) >= 80:
                break
    return boxes


def render_image_match(content, phrase):
    """Highlight OCR hits directly on an uploaded image."""
    with Image.open(BytesIO(content)) as original:
        image = original.convert('RGB')
    image.thumbnail((1800, 1800))
    boxes = _ocr_boxes(image, phrase)
    return _mark_image(image, boxes), len(boxes)


def render_pdf_match(content, page_number, phrase):
    """Render one PDF page and overlay all located text / OCR matches."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(content)
    try:
        if not 1 <= page_number <= len(document):
            raise ValueError('The requested PDF page is outside this document.')
        page = document[page_number - 1]
        try:
            width, height = page.get_size()
            scale = min(2.0, 1800 / max(width, height, 1))
            bitmap = page.render(scale=scale)
            try:
                image = bitmap.to_pil().convert('RGB')
            finally:
                bitmap.close()
            boxes = []
            tp = page.get_textpage()
            try:
                search = tp.search(phrase, match_case=False, match_whole_word=False)
                try:
                    for _ in range(40):
                        occurrence = search.get_next()
                        if occurrence is None:
                            break
                        start, count = occurrence
                        for index in range(start, start + count):
                            try:
                                left, bottom, right, top = tp.get_charbox(index)
                                if right > left and top > bottom:
                                    boxes.append((left * scale, (height - top) * scale,
                                                  right * scale, (height - bottom) * scale))
                            except Exception:
                                continue
                finally:
                    search.close()
            finally:
                tp.close()
            if not boxes:
                boxes = _ocr_boxes(image, phrase)
            return _mark_image(image, boxes), len(boxes)
        finally:
            page.close()
    finally:
        document.close()
