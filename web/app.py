"""NoteScout Web: browser-based document search with jump-to-match previews."""
import html
from pathlib import Path
import re

import streamlit as st

from core import SUPPORTED, iter_sections, search_content
from preview import render_image_match, render_pdf_match

WINDOWS_RELEASE = "https://github.com/harshitattri/NoteScout/releases/tag/v1.0"
MAX_FILES = 12
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024
MAX_DISPLAY = 150

st.set_page_config(page_title="NoteScout Web", page_icon="🔎", layout="wide")
st.title("🔎 NoteScout Web")
st.write("Search inside your PDFs, PowerPoint slides, text files and photos.")
st.caption("Open it on your phone or computer. Upload files, find a phrase, then jump to the matching page or line.")

with st.sidebar:
    st.subheader("Two ways to use NoteScout")
    st.link_button("Get the Windows app", WINDOWS_RELEASE)
    st.caption("The Windows version searches folders you choose directly on your PC.")
    st.divider()
    st.caption("Privacy: files chosen here are uploaded to this application's host for processing. "
               "They are not intentionally written to disk by this application, but stay "
               "in session memory until the session ends. Do not upload confidential documents "
               "to hosting services you don't trust.")

with st.form("search"):
    uploads = st.file_uploader(
        "Choose documents",
        type=["pdf", "pptx", "txt", "png", "jpg", "jpeg", "bmp"],
        accept_multiple_files=True,
        help="Up to 12 files, 25 MB each, 60 MB total.",
    )
    phrase = st.text_input("Word or phrase", placeholder="e.g. Poisson distribution")
    force_ocr = st.checkbox(
        "OCR every PDF page (slower)",
        value=False,
        help="When disabled, image-only PDF pages are still processed with OCR.",
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


def show_location(hit, source, query):
    """Show the source page, slide, line or image, not just a result snippet."""
    name, content = source["name"], source["content"]
    extension = Path(name).suffix.lower()
    st.caption("Exact location: " + hit["location"])
    if extension == ".pdf":
        match = re.search(r"Page (\d+)", hit["location"])
        if match is None:
            st.warning("The page number was not available.")
            return
        (page, zoom), found = render_pdf_match(content, int(match.group(1)), query)
        if found:
            if zoom is not None:
                st.image(zoom, caption="Zoomed in on the first matching word", use_container_width=True)
            st.image(page, caption="Full page with matching text highlighted", use_container_width=True)
        else:
            st.warning("Here's the matching page, but the exact text couldn't be located visually. "
                       "This can happen with imperfect OCR.")
            st.image(page, caption=hit["location"], use_container_width=True)
    elif extension in {".png", ".jpg", ".jpeg", ".bmp"}:
        (image, zoom), found = render_image_match(content, query)
        if found:
            if zoom is not None:
                st.image(zoom, caption="Zoomed in on the matching text", use_container_width=True)
            st.image(image, caption="Image with matching words highlighted", use_container_width=True)
        else:
            st.warning("OCR found the phrase during the search, but couldn't pinpoint it in the preview.")
            st.image(image, use_container_width=True)
    else:
        index_label = hit["location"].split(" (")[0]
        for location, text in iter_sections(name, content):
            if location == index_label:
                st.markdown('<div style="padding:1rem;line-height:1.8;white-space:pre-wrap;'
                            'border:1px solid #777;border-radius:8px">' +
                            highlighted(text, query) + '</div>', unsafe_allow_html=True)
                break
        else:
            st.info("Could not reload this section. Refer to the search snippet above.")
        if extension == ".pptx":
            st.caption("This is extracted text from the matching slide, not a visual slide preview.")
    st.download_button(
        "Download original file", data=content, file_name=name,
        key=f"download_{hit['source_index']}_{hit['location']}",
    )


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
            st.error("Upload limit exceeded: 25 MB per file, 60 MB total.")
        else:
            progress = st.progress(0, text="Reading your documents...")
            sources = []
            results = []
            failures = []
            for index, up in enumerate(uploads, 1):
                source = {"name": up.name, "content": up.getvalue()}
                sources.append(source)
                try:
                    if Path(up.name).suffix.lower() not in SUPPORTED:
                        raise ValueError("Unsupported file type")
                    for hit in search_content(up.name, source["content"], phrase, force_ocr):
                        hit["source_index"] = index - 1
                        results.append(hit)
                except Exception as exc:
                    failures.append((up.name, str(exc)))
                progress.progress(index / len(uploads), text=f"Processed {index} of {len(uploads)} files")
            progress.empty()
            st.session_state["notescout_results"] = {
                "query": " ".join(phrase.split()),
                "sources": sources, "results": results, "failures": failures,
            }
            st.session_state["selected_hit"] = None

saved = st.session_state.get("notescout_results")
if saved:
    results = saved["results"]
    query = saved["query"]
    st.subheader(f"Results: {len(results)} matching sections")
    st.caption("Choose **View match in document** to see the page or image with the word highlighted. "
               "For PPTX and TXT, you'll see the exact slide or line's extracted text.")
    if not results:
        st.info("No matches found in the documents that could be read.")
    for index, hit in enumerate(results[:MAX_DISPLAY]):
        with st.container(border=True):
            st.write(f"**{hit['filename']}** · {hit['location']}")
            st.markdown('<div style="line-height:1.55">' +
                        highlighted(hit["snippet"], query) + '</div>', unsafe_allow_html=True)
            if st.button("📍 View match in document", key=f"hit_{index}"):
                st.session_state["selected_hit"] = index
            if st.session_state.get("selected_hit") == index:
                try:
                    show_location(hit, saved["sources"][hit["source_index"]], query)
                except Exception as exc:
                    st.error(f"Couldn't build a page preview: {exc}")
                    st.info("The text match above is still available.")
    if len(results) > MAX_DISPLAY:
        st.caption(f"Showing the first {MAX_DISPLAY} matches.")
    if saved["failures"]:
        st.warning(f"Could not fully process {len(saved['failures'])} files.")
        with st.expander("Show file errors"):
            for name, err in saved["failures"]:
                st.write(f"{name}: {err}")

st.divider()
st.caption("NoteScout Web — Harshit Attri · Windows desktop version available on GitHub.")
