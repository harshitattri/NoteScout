# NoteScout Web — source implementation

This folder contains a standalone Python/Streamlit version of NoteScout, based on the document-search functionality of the Windows application.

**Provenance:** This is a newly created, independently deployable implementation. It is **not an export of the original ChatGPT Work site source** and is not automatically connected to the already-published website at https://notescout-web-harshit.cuddlypanel1.chatgpt.site.

## Features

- Upload PDFs, PPTX, TXT and common image formats in a browser.
- Search exact words or phrases, case-insensitively.
- View highlighted matches with PDF page numbers, PPTX slide numbers and TXT line numbers.
- Click **View match in document** to display the matching PDF page or image with highlighted text and a zoomed-in view; for PPTX and TXT, open the matching slide's or line's extracted text.
- OCR scanned PDF pages and images with Tesseract.
- Use on Windows, macOS, iOS or Android through a modern browser.

Unlike the desktop app, the web version cannot scan arbitrary folders on a visitor's device. Users must choose files to upload.

## Run locally

Use Python 3.11 or newer and install the Tesseract command-line engine (including English language data) in your system PATH.

From the repository root:

1. Run: python -m pip install -r web/requirements.txt
2. Run: python -m streamlit run web/app.py
3. Open the local URL printed in the terminal.

## Deploy on Streamlit Community Cloud

1. Connect your GitHub account to Streamlit Community Cloud.
2. Choose repository harshitattri/NoteScout and branch main.
3. Set the entrypoint to web/app.py.
4. Deploy. The file web/requirements.txt contains Python dependencies, and the repository-root packages.txt installs the Tesseract OCR engine.

This deployment would have a **separate address** from the existing ChatGPT Work website. Deploying it does not replace that site automatically.

## Limits and privacy

- Up to 12 files, 25 MB per file, 60 MB per search batch.
- PDFs: up to 80 pages; PowerPoint: up to 150 slides.
- Up to 100 results per input file; the interface shows the first 150 results across the batch.
- The application does not intentionally write uploaded files to disk or share them externally, but files are still transmitted to the server hosting the Streamlit app. Do not upload sensitive documents to untrusted deployments.
- Search can be slower when OCR is enabled. OCR quality depends on image clarity and language data.
- No desktop-style persistent cache in the web version.

## Desktop version

The Windows desktop app and release remain unchanged. Download it from https://github.com/harshitattri/NoteScout/releases/tag/v1.0.

Built by Harshit Attri.

## Preview behavior

The web app displays an in-browser location preview rather than opening the original PDF in an external PDF viewer. Visual position highlighting may fail on unclear scans or imperfect OCR; in that case the matching page and search snippet are still shown. PPTX and TXT matches show extracted text at the correct slide or line, not a rendered PowerPoint slide.
