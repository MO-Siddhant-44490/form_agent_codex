"""Deterministic golden-PDF builders: generated at test time so the repo
holds no binaries and the ground truth lives next to the assertions."""

import pymupdf


def build_pdf(lines: list[str], *, start_y: float = 72.0, leading: float = 24.0) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()  # A4-ish default
    y = start_y
    for line in lines:
        page.insert_text((72, y), line, fontsize=11)
        y += leading
    data = doc.tobytes()
    doc.close()
    return data


def build_multipage_pdf(pages: list[list[str]]) -> bytes:
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        y = 72.0
        for line in lines:
            page.insert_text((72, y), line, fontsize=11)
            y += 24.0
    data = doc.tobytes()
    doc.close()
    return data


def build_encrypted_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Secret: content")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="letmein")
    doc.close()
    return data


APPLICATION_LINES = [
    "Job Application Summary",
    "Full Name: Ada Lovelace",
    "Email: Ada@Example.test",
    "Phone Number: +1 (555) 010-2030",
    "Date of Birth: 17 Apr 1998",
    "Country: India",
    "Years of Experience: 5",
]
