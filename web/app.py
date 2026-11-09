"""NoteScout Web: browser search across documents uploaded by the user."""
import html
import re

import streamlit as st
from core import SUPPORTED, search_content

WINDOWS_RELEASE = "https://github.com/harshitattri/NoteScout/releases/tag/v1.0"
MAX_FILES = 12
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024

st.set_page_config(page_title="NoteScout Web", page_icon="🔎", layout="wide")
st.title("🔎 NoteScout Web")
st.write("Find a word or phrase across your PDFs, PowerPoint slides, text files and photos.")
st.caption("Works in your browser on a phone or computer. No Windows installation needed.")

with st.sidebar:
    st.subheader("Two ways to use NoteScout")
    st.link_button("Get the Windows app", WINDOWS_RELEASE)
    st.caption("The Windows version searches folders you choose directly on your PC.")
    st.divider()
    st.caption("Web privacy: selected files are uploaded to this app's host for processing. "
               "This implementation does not intentionally save uploaded documents to disk. "
               "Avoid uploading confidential files to hosting services you do not trust.")

with st.form("search"):
    uploads = st.file_uploader(
        "Choose documents",
        type=["pdf", "pptx", "txt", "png", "jpg", "jpeg", "bmp"],
        accept_multiple_files=True,
        help="Up to 12 files, 25 MB each, 60 MB combined.",
    )
    phrase = st.text_input("Word or phrase", placeholder="e.g. Poisson distribution")
    force_ocr = st.checkbox(
        "OCR every PDF page (slower)",
        value=False,
        help="Without this setting, OCR is attempted on PDF pages that have no extractable text.",
    )
    submitted = st.form_submit_button("Search files", type="primary", use_container_width=True)

def highlighted(text, phrase):
    pattern = re.compile(re.escape(phrase), re.IGNORECASE)
    parts = []
    pos = 0
    for match in pattern.finditer(text):
        parts.append(html.escape(text[pos:match.start()]))
        parts.append("<mark>" + html.escape(match.group(0)) + "</mark>")
        pos = match.end()
    parts.append(html.escape(text[pos:]))
    return "".join(parts)

if submitted:
    if not uploads:
        st.warning("Choose at least one document.")
    elif not phrase.strip():
        st.warning("Enter a word or phrase.")
    elif len(uploads) > MAX_FILES:
        st.error(f"Choose no more than {MAX_FILES} files.")
    else:
        sizes = [up.size for up in uploads]
        if any(size > MAX_FILE_BYTES for size in sizes) or sum(sizes) > MAX_TOTAL_BYTES:
            st.error("Upload limit exceeded: maximum 25 MB per file and 60 MB total.")
        else:
            progress = st.progress(0, text="Reading your documents...")
            results = []
            failures = []
            for index, up in enumerate(uploads, 1):
                try:
                    if not any(up.name.lower().endswith(ext) for ext in SUPPORTED):
                        raise ValueError("Unsupported file type.")
                    results.extend(search_content(up.name, up.getvalue(), phrase, force_ocr))
                except Exception as exc:
                    failures.append((up.name, str(exc)))
                progress.progress(index / len(uploads), text=f"Processed {index} / {len(uploads)} files")
            progress.empty()
            st.subheader(f"Results: {len(results)} matching sections")
            if not results:
                st.info("No matches found in the documents that could be read.")
            for hit in results[:150]:
                with st.container(border=True):
                    st.write(f"**{hit['filename']}** · {hit['location']}")
                    st.markdown(
                        '<div style="line-height:1.55">' +
                        highlighted(hit["snippet"], phrase.strip()) + '</div>',
                        unsafe_allow_html=True,
                    )
            if failures:
                st.warning(f"Could not fully process {len(failures)} file(s).")
                with st.expander("See file errors"):
                    for filename, problem in failures:
                        st.write(f"{filename}: {problem}")

st.divider()
st.caption("NoteScout Web — Harshit Attri · Open-source web implementation · "
           "Windows desktop version available on GitHub.")
