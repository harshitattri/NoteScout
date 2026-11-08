"""NoteScout: local document search with OCR and a persistent SQLite cache."""
from pathlib import Path
import json
import os
import queue
import re
import shutil
import sqlite3
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

IMAGE_TYPES = {'.jpg', '.jpeg', '.png', '.bmp'}
SUPPORTED = {'.pdf', '.pptx', '.txt'} | IMAGE_TYPES
FILE_FILTERS = {'PDF': {'.pdf'}, 'PPTX': {'.pptx'}, 'TXT': {'.txt'}, 'Photos': IMAGE_TYPES}
MAX_RESULTS = 300
CACHE_VERSION = 'notescout-extraction-1-eng-psm3'
CACHE_PATH = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.cache'))) / 'NoteScout' / 'cache.sqlite3'


def fingerprint(path):
    stat = path.stat()
    return f'{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}'


class TextCache:
    """Each worker owns its connection; only complete files are committed."""
    def __init__(self, path=CACHE_PATH):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.execute('''CREATE TABLE IF NOT EXISTS file_text (
            path TEXT NOT NULL, mode TEXT NOT NULL, stamp TEXT NOT NULL,
            sections TEXT NOT NULL, PRIMARY KEY(path, mode))''')
        self.db.commit()

    def get(self, path, mode, stamp):
        row = self.db.execute(
            'SELECT stamp, sections FROM file_text WHERE path=? AND mode=?',
            (str(path), mode)).fetchone()
        if row is None or row[0] != stamp:
            return None
        try:
            sections = json.loads(row[1])
            if not isinstance(sections, list) or any(
                not isinstance(s, list) or len(s) != 2
                or not all(isinstance(v, str) for v in s) for s in sections
            ):
                return None
            return sections
        except (ValueError, TypeError):
            return None

    def put(self, path, mode, stamp, sections):
        with self.db:
            # Discard obsolete versions of this file, including other OCR modes.
            self.db.execute('DELETE FROM file_text WHERE path=? AND stamp<>?',
                            (str(path), stamp))
            self.db.execute('INSERT OR REPLACE INTO file_text VALUES (?, ?, ?, ?)',
                            (str(path), mode, stamp, json.dumps(sections)))

    def prune(self, folder, stop):
        removed = 0
        for (stored,) in self.db.execute('SELECT DISTINCT path FROM file_text').fetchall():
            if stop.is_set():
                break
            path = Path(stored)
            if not path.is_relative_to(folder):
                continue
            try:
                path.stat()
            except FileNotFoundError:
                with self.db:
                    self.db.execute('DELETE FROM file_text WHERE path=?', (stored,))
                removed += 1
            except OSError:
                pass  # An inaccessible file is not necessarily deleted.
        return removed

    def clear(self):
        with self.db:
            self.db.execute('DELETE FROM file_text')
        self.db.execute('VACUUM')

    def close(self):
        self.db.close()


def recognise(image, stop):
    if stop.is_set():
        return ''
    import pytesseract
    from PIL import ImageOps
    bundled = Path(__file__).resolve().parent / 'tesseract' / 'tesseract.exe'
    if bundled.is_file():
        executable = str(bundled)
    elif getattr(sys, 'frozen', False):
        raise RuntimeError(
            'Bundled OCR engine is missing. Keep the complete NoteScout folder together.'
        )
    else:
        executable = shutil.which('tesseract')
        if not executable:
            candidate = Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe')
            if candidate.is_file():
                executable = str(candidate)
    if not executable:
        raise RuntimeError('Tesseract was not found. Check its installation.')
    pytesseract.pytesseract.tesseract_cmd = executable
    data_dir = Path(executable).parent / 'tessdata'
    ocr_config = '--psm 3'
    if not (data_dir / 'eng.traineddata').is_file():
        raise RuntimeError(
            f'English OCR data is missing: {data_dir / "eng.traineddata"}. '
            'Rebuild with the complete Tesseract-OCR folder.'
        )
    # Pass a raw directory through the child process environment. Quoted
    # config paths can retain literal quotes in pytesseract on Windows.
    os.environ['TESSDATA_PREFIX'] = str(data_dir)
    corrected = ImageOps.exif_transpose(image)
    try:
        rgb = corrected.convert('RGB')
        try:
            rgb.thumbnail((4000, 4000))
            return pytesseract.image_to_string(rgb, lang='eng', config=ocr_config, timeout=60)
        finally:
            rgb.close()
    finally:
        corrected.close()


def slide_text(shapes, stop):
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    parts = []
    for shape in shapes:
        if stop.is_set():
            break
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            parts.append(slide_text(shape.shapes, stop))
        elif shape.has_text_frame:
            parts.append(shape.text_frame.text)
        elif shape.has_table:
            for row in shape.table.rows:
                parts.extend(cell.text for cell in row.cells)
    return '\n'.join(parts)


def read_sections(path, force_ocr, stop, emit):
    """Yield (location, text, error). PDFium runs only in this one worker."""
    extension = path.suffix.lower()
    if extension == '.pdf':
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(str(path))
        try:
            for index in range(len(document)):
                if stop.is_set():
                    return
                location = f'Page {index + 1}'
                page = None
                try:
                    emit(('progress', f'Reading {path.name} — {location}'))
                    page = document[index]
                    text_page = page.get_textpage()
                    try:
                        text = text_page.get_text_range()
                    finally:
                        text_page.close()
                    if force_ocr or not text.strip():
                        if stop.is_set():
                            return
                        emit(('progress', f'OCR: {path.name} — {location}'))
                        width, height = page.get_size()
                        bitmap = page.render(scale=min(3.0, 4000 / max(width, height)))
                        try:
                            image = bitmap.to_pil()
                            try:
                                text += '\n' + recognise(image, stop)
                            finally:
                                image.close()
                        finally:
                            bitmap.close()
                        location += ' (OCR)'
                    yield location, text, None
                except Exception as error:
                    yield location, '', str(error)
                finally:
                    if page is not None:
                        page.close()
        finally:
            document.close()
    elif extension == '.pptx':
        from pptx import Presentation
        presentation = Presentation(str(path))
        for number, slide in enumerate(presentation.slides, 1):
            if stop.is_set():
                return
            emit(('progress', f'Reading {path.name} — slide {number}'))
            try:
                yield f'Slide {number}', slide_text(slide.shapes, stop), None
            except Exception as error:
                yield f'Slide {number}', '', str(error)
    elif extension == '.txt':
        with path.open('rb') as file:
            prefix = file.read(4)
        encoding = 'utf-8-sig'
        if prefix.startswith((b'\xff\xfe', b'\xfe\xff')):
            encoding = 'utf-16'
        with path.open('r', encoding=encoding) as file:
            for number, line in enumerate(file, 1):
                if stop.is_set():
                    return
                yield f'Line {number}', line, None
    elif extension in IMAGE_TYPES:
        from PIL import Image
        emit(('progress', f'OCR: {path.name}'))
        with Image.open(path) as image:
            text = recognise(image, stop)
        yield 'Photo (OCR)', text, None


def search_worker(folder, query, force_ocr, stop, emit, cache_path=CACHE_PATH,
                  extractor=read_sections, selected_types=None):
    folder = Path(folder).resolve()
    allowed = SUPPORTED if selected_types is None else SUPPORTED.intersection(selected_types)
    counts = dict(files=0, cached=0, read=0, matches=0, errors=0, empty=0, removed=0)
    started = time.monotonic()
    cache = None
    pattern = re.compile(re.escape(query), re.IGNORECASE)

    def error(label, problem):
        counts['errors'] += 1
        emit(('text', f'Could not fully process: {label}\nReason: {problem}\n\n'))

    def match_section(path, location, text):
        if not text:
            if path.suffix.lower() != '.txt':
                counts['empty'] += 1
            return
        found = pattern.search(text)
        if found:
            counts['matches'] += 1
            if counts['matches'] <= MAX_RESULTS:
                start = max(0, found.start() - 80)
                end = min(len(text), found.end() + 160)
                snippet = ('...' if start else '') + text[start:end]
                if end < len(text):
                    snippet += '...'
                emit(('result', f'{path.relative_to(folder)} | {location}', snippet, str(path)))

    try:
        try:
            cache = TextCache(cache_path)
            counts['removed'] = cache.prune(folder, stop)
        except (OSError, sqlite3.Error) as problem:
            if cache:
                cache.close()
            cache = None
            emit(('text', f'Cache unavailable; searching without it.\n{problem}\n\n'))
        for root, directories, filenames in os.walk(
            folder, followlinks=False, onerror=lambda e: error('folder', e)
        ):
            if stop.is_set():
                break
            directories[:] = sorted(d for d in directories if not (Path(root) / d).is_symlink())
            for filename in sorted(filenames):
                if stop.is_set():
                    break
                path = Path(root) / filename
                if path.suffix.lower() not in allowed or filename.startswith('~$') or path.is_symlink():
                    continue
                counts['files'] += 1
                try:
                    stamp = fingerprint(path)
                    mode = CACHE_VERSION + (':all-pdf' if force_ocr and path.suffix.lower() == '.pdf' else ':auto')
                    saved = cache.get(path, mode, stamp) if cache else None
                    if saved is not None:
                        counts['cached'] += 1
                        emit(('progress', f'Using cache: {path.name}'))
                        for location, text in saved:
                            if stop.is_set():
                                break
                            match_section(path, location, text)
                        continue
                    counts['read'] += 1
                    emit(('progress', f'Reading: {path.name}'))
                    sections = []
                    complete = True
                    stream = extractor(path, force_ocr, stop, emit)
                    try:
                        for location, text, problem in stream:
                            if stop.is_set():
                                complete = False
                                break
                            if problem:
                                complete = False
                                error(f'{path.name} | {location}', problem)
                                continue
                            clean = ' '.join(text.split())
                            sections.append((location, clean))
                            match_section(path, location, clean)
                    finally:
                        stream.close()
                    if complete and not stop.is_set():
                        if fingerprint(path) == stamp:
                            if cache:
                                cache.put(path, mode, stamp, sections)
                        else:
                            error(path.name, 'File changed during search. Search again; it was not cached.')
                except Exception as problem:
                    error(path.name, problem)
    except Exception as problem:
        error('search', problem)
    finally:
        if cache:
            cache.close()
        if not counts['matches']:
            emit(('text', 'No matches found in the text read so far.\n\n'))
        if counts['empty']:
            emit(('text', f"{counts['empty']} section(s) returned no text. They may be blank, unclear, or image-based PPTX content.\n\n"))
        if counts['matches'] > MAX_RESULTS:
            emit(('text', f'Showing the first {MAX_RESULTS} results.\n\n'))
        state = 'Stopped' if stop.is_set() else 'Finished'
        elapsed = time.monotonic() - started
        summary = (f"{state} | Files: {counts['files']} | Cached: {counts['cached']} | "
                   f"Read again: {counts['read']} | Matches: {counts['matches']} | "
                   f"Errors: {counts['errors']} | {elapsed:.1f}s")
        emit(('done', summary, counts))


def highlighted_parts(text, query):
    """Split a snippet into normal and literal, case-insensitive matching text."""
    if not query:
        yield text, False
        return
    cursor = 0
    for match in re.finditer(re.escape(query), text, re.IGNORECASE):
        if match.start() > cursor:
            yield text[cursor:match.start()], False
        yield match.group(), True
        cursor = match.end()
    if cursor < len(text):
        yield text[cursor:], False


class NoteScout:
    def __init__(self, window):
        self.window = window
        self.messages = queue.Queue()
        self.stop = threading.Event()
        self.running = False
        self.links = []
        self.counter = 0
        self.active_query = ''
        window.title('NoteScout')
        window.geometry('1000x780')
        window.minsize(720, 620)
        window.protocol('WM_DELETE_WINDOW', self.close)
        main = ttk.Frame(window, padding=20)
        main.pack(fill='both', expand=True)
        ttk.Label(main, text='NoteScout', font=('Segoe UI', 26, 'bold')).pack(anchor='w')
        ttk.Label(main, text='Search PDFs, PowerPoint slides, text files and photos.').pack(anchor='w', pady=(4, 15))
        toolbar = ttk.Frame(main)
        toolbar.pack(fill='x')
        self.folder_button = ttk.Button(toolbar, text='Choose Notes Folder', command=self.choose_folder)
        self.folder_button.pack(side='left')
        self.clear_button = ttk.Button(toolbar, text='Clear Cache', command=self.clear_cache)
        self.clear_button.pack(side='left', padx=10)
        self.folder = tk.StringVar()
        ttk.Entry(main, textvariable=self.folder, state='readonly').pack(fill='x', pady=10)
        filters = ttk.LabelFrame(main, text='File types to search', padding=(10, 6))
        filters.pack(fill='x', pady=(0, 12))
        self.type_vars = {}
        self.type_buttons = []
        for column, label in enumerate(FILE_FILTERS):
            variable = tk.BooleanVar(value=True)
            button = ttk.Checkbutton(filters, text=label, variable=variable)
            button.grid(row=0, column=column, sticky='w', padx=(0, 24))
            self.type_vars[label] = variable
            self.type_buttons.append(button)
        self.force = tk.BooleanVar(value=False)
        self.ocr_check = ttk.Checkbutton(main, text='OCR every PDF page (slower; for mixed text and photos)', variable=self.force)
        self.ocr_check.pack(anchor='w', pady=(0, 10))
        ttk.Label(main, text='Search for a word or phrase:').pack(anchor='w')
        row = ttk.Frame(main)
        row.pack(fill='x', pady=(5, 12))
        self.query = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.query)
        self.entry.pack(side='left', fill='x', expand=True)
        self.entry.bind('<Return>', lambda event: self.start_search())
        self.search_button = ttk.Button(row, text='Search', command=self.start_search)
        self.search_button.pack(side='left', padx=8)
        self.stop_button = ttk.Button(row, text='Stop', command=self.stop_search, state='disabled')
        self.stop_button.pack(side='left')
        self.progress = ttk.Progressbar(main, mode='indeterminate')
        self.progress.pack(fill='x', pady=(0, 8))
        self.status = tk.StringVar(value='Choose a folder to begin. Unchanged files will reuse saved text.')
        ttk.Label(main, textvariable=self.status, wraplength=850).pack(anchor='w', pady=(0, 8))
        ttk.Label(main, text='Click a filename to open it. Matching words are highlighted in yellow.').pack(anchor='w', pady=(0, 8))
        self.results = ScrolledText(main, wrap='word', font=('Segoe UI', 11), state='disabled', cursor='arrow', padx=14, pady=12, spacing1=3, spacing3=5)
        self.results.pack(fill='both', expand=True)
        self.results.tag_configure('match', background='#FFF1A8', foreground='#172B4D')
        self.controls = (self.folder_button, self.clear_button, self.ocr_check,
                         self.entry, self.search_button, *self.type_buttons)
        window.after(100, self.poll)

    def busy(self, value):
        self.running = value
        for control in self.controls:
            control.config(state='disabled' if value else 'normal')
        if value:
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress['value'] = 0
            self.stop_button.config(state='disabled')

    def clear_results(self):
        for tag, bindings in self.links:
            for sequence, callback_id in bindings:
                self.results.tag_unbind(tag, sequence, callback_id)
            self.results.tag_delete(tag)
        self.links.clear()
        self.results.config(state='normal', cursor='arrow')
        self.results.delete('1.0', tk.END)
        self.results.config(state='disabled')

    def append(self, text):
        self.results.config(state='normal')
        self.results.insert(tk.END, text, ())
        self.results.config(state='disabled')

    def add_result(self, title, snippet, path):
        self.counter += 1
        tag = f'file_link_{self.counter}'
        self.results.tag_configure(tag, foreground='#126B78', underline=True, font=('Segoe UI', 11, 'bold'))
        bindings = []
        for event, callback in (
            ('<Button-1>', lambda event, p=path: self.open_file(p)),
            ('<Enter>', lambda event: self.results.config(cursor='hand2')),
            ('<Leave>', lambda event: self.results.config(cursor='arrow'))
        ):
            bindings.append((event, self.results.tag_bind(tag, event, callback)))
        self.links.append((tag, bindings))
        self.results.config(state='normal')
        self.results.insert(tk.END, title, (tag,))
        self.results.insert(tk.END, '\n', ())
        for text, matched in highlighted_parts(snippet, self.active_query):
            self.results.insert(tk.END, text, ('match',) if matched else ())
        self.results.insert(tk.END, '\n\n', ())
        self.results.config(state='disabled')

    def open_file(self, path):
        try:
            if not Path(path).is_file():
                raise FileNotFoundError('This file may have been moved or deleted.')
            os.startfile(str(Path(path).resolve()))
        except (OSError, AttributeError) as problem:
            messagebox.showerror('Could not open file', str(problem), parent=self.window)
        return 'break'

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self.window, title='Choose your notes folder')
        if folder:
            self.folder.set(folder)
            self.clear_results()
            self.status.set('Folder selected. Enter a word or phrase.')

    def start_search(self):
        if self.running:
            return
        folder = self.folder.get().strip()
        query = ' '.join(self.query.get().split())
        if not folder or not Path(folder).is_dir():
            messagebox.showinfo('Choose a folder', 'Select an existing notes folder first.', parent=self.window)
            return
        if not query:
            messagebox.showinfo('Enter a search', 'Type a word or phrase first.', parent=self.window)
            return
        selected = set()
        for label, variable in self.type_vars.items():
            if variable.get():
                selected.update(FILE_FILTERS[label])
        if not selected:
            messagebox.showinfo('Choose file types', 'Select at least one file type to search.', parent=self.window)
            return
        self.active_query = query
        self.clear_results()
        self.stop.clear()
        self.busy(True)
        self.stop_button.config(state='normal')
        self.status.set('Checking files and saved text...')
        threading.Thread(target=search_worker, args=(folder, query, self.force.get(), self.stop, self.messages.put), kwargs={'selected_types': selected}, daemon=True).start()

    def stop_search(self):
        self.stop.set()
        self.stop_button.config(state='disabled')
        self.status.set('Stopping after the current reading or OCR operation...')

    def clear_cache(self):
        if self.running:
            return
        if not messagebox.askyesno('Clear Cache', 'Remove all saved extracted text?\nYour original files will not be changed.\nThe next search will read them again.', parent=self.window):
            return
        self.busy(True)
        self.status.set('Clearing saved text...')
        threading.Thread(target=self.clear_worker, daemon=True).start()

    def clear_worker(self):
        cache = None
        try:
            cache = TextCache()
            cache.clear()
            self.messages.put(('cleared', 'Cache cleared. The next search will read files again.'))
        except Exception as problem:
            self.messages.put(('cleared', f'Could not clear cache: {problem}'))
        finally:
            if cache:
                cache.close()

    def poll(self):
        for _ in range(100):
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            kind = message[0]
            if kind == 'progress' and not self.stop.is_set():
                self.status.set(message[1])
            elif kind == 'result':
                self.add_result(*message[1:])
            elif kind == 'text':
                self.append(message[1])
            elif kind in ('done', 'cleared'):
                self.busy(False)
                self.status.set(message[1])
        self.window.after(100, self.poll)

    def close(self):
        self.stop.set()
        self.window.destroy()


if __name__ == '__main__':
    window = tk.Tk()
    app = NoteScout(window)
    window.mainloop()
