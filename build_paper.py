#!/usr/bin/env python3

import os
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from html import escape
from zoneinfo import ZoneInfo

import feedparser
from bs4 import BeautifulSoup
from ebooklib import epub

EST = ZoneInfo("America/New_York")
BOOK_TITLE = "The Morning Paper"
OUTPUT_FILENAME = "morning-paper.epub"

FEEDS = {
    "World & Politics": [
        ("World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
        ("Politics", "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml"),
    ],
    "Business & Economy": [
        ("Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ],
    "Culture & Long Reads": [
        ("Arts", "https://rss.nytimes.com/services/xml/rss/nyt/Arts.xml"),
        ("Books", "https://rss.nytimes.com/services/xml/rss/nyt/Books.xml"),
    ],
    "Technology & AI": [
        ("Technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
    ],
    "U.S. News": [
        ("U.S.", "https://rss.nytimes.com/services/xml/rss/nyt/US.xml"),
    ],
    "Opinion": [
        ("Opinion", "https://rss.nytimes.com/services/xml/rss/nyt/Opinion.xml"),
    ],
}


def clean_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = BeautifulSoup(raw_html, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def format_published(raw_entry: dict) -> datetime | None:
    published = raw_entry.get("published_parsed") or raw_entry.get("updated_parsed")
    if published:
        try:
            return datetime(*published[:6], tzinfo=EST)
        except TypeError:
            return None
    return None


def fetch_section_items(section_title: str, feed_urls: list[tuple[str, str]]) -> list[dict]:
    items: list[dict] = []
    for source_name, url in feed_urls:
        try:
            feed = feedparser.parse(url)
        except Exception as exc:  # pragma: no cover - network and feed failures are handled at runtime
            print(f"Warning: unable to load {source_name} from {url}: {exc}")
            continue

        for entry in getattr(feed, "entries", []):
            title = (entry.get("title") or "Untitled").strip()
            if not title:
                continue

            link = entry.get("link") or ""
            summary = clean_text(entry.get("summary") or entry.get("description") or "")
            published = format_published(entry)
            items.append(
                {
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "source": source_name,
                    "published": published,
                }
            )

    items.sort(key=lambda item: (item["published"] or datetime(1970, 1, 1, tzinfo=EST), item["title"].lower()), reverse=True)
    return items[:6]


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower())
    return value.strip("-") or "section"


def build_book(sections: dict[str, list[dict]]) -> epub.EpubBook:
    today = datetime.now(EST).strftime("%A, %B %d, %Y")

    book = epub.EpubBook()
    book.set_identifier("morning-paper")
    book.set_title(BOOK_TITLE)
    book.set_language("en")
    book.add_author("Morning Paper")

    cover = epub.EpubHtml(
        title="Cover",
        file_name="cover.xhtml",
        lang="en",
    )
    cover.content = (
        "<h1 style='font-size: 2.5em; text-align: center; margin-top: 3em;'>"
        f"{escape(BOOK_TITLE)}</h1>"
        f"<p style='text-align: center; font-size: 1.2em;'>{escape(today)}</p>"
    )
    book.add_item(cover)

    toc = [epub.Link("cover.xhtml", "Cover", "cover")]
    chapters = []

    for section_title, items in sections.items():
        section_slug = slugify(section_title)
        page = epub.EpubHtml(title=section_title, file_name=f"{section_slug}.xhtml", lang="en")

        html = [f"<h1>{escape(section_title)}</h1>"]
        for item in items:
            link = escape(item["link"] or "#")
            title = escape(item["title"])
            summary = escape(item["summary"])
            source = escape(item["source"])
            article_block = (
                f"<h2><a href='{link}'>{title}</a></h2>"
                f"<p><em>{source}</em></p>"
                f"<p>{summary}</p>"
            )
            if item["summary"]:
                html.append(article_block)
            else:
                html.append(f"<h2><a href='{link}'>{title}</a></h2><p><em>{source}</em></p>")

        page.content = "".join(html)
        book.add_item(page)
        toc.append(epub.Link(f"{section_slug}.xhtml", section_title, section_slug))
        chapters.append(page)

    book.toc = tuple(toc)
    book.spine = ["nav", cover] + chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    return book


def write_book(book: epub.EpubBook, output_path: str) -> str:
    epub.write_epub(output_path, book, {})
    return output_path


def send_to_kindle(path: str) -> bool:
    sender = os.environ.get("SENDER_EMAIL")
    password = os.environ.get("SMTP_PASSWORD")
    host = os.environ.get("SMTP_HOST")
    port = os.environ.get("SMTP_PORT")
    kindle_email = os.environ.get("KINDLE_EMAIL")

    if not all([sender, password, host, port, kindle_email]):
        print("Missing email configuration; skipping Kindle delivery.")
        return False

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = kindle_email
    msg["Subject"] = BOOK_TITLE
    msg.set_content("Your morning paper is attached. This was sent by the GitHub Actions Kindle automation.")

    with open(path, "rb") as attachment_file:
        attachment_data = attachment_file.read()

    msg.add_attachment(
        attachment_data,
        maintype="application",
        subtype="epub+zip",
        filename=os.path.basename(path),
    )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, int(port), context=context) as server:
        server.login(sender, password)
        server.send_message(msg)

    print(f"Sent EPUB to Kindle: {kindle_email}")
    return True


def main() -> None:
    sections: dict[str, list[dict]] = {}
    for section_title, feed_urls in FEEDS.items():
        sections[section_title] = fetch_section_items(section_title, feed_urls)

    book = build_book(sections)
    output_path = os.path.join(os.getcwd(), OUTPUT_FILENAME)
    write_book(book, output_path)
    print(f"Generated {output_path}")

    if os.environ.get("SEND_EMAIL", "false").lower() == "true":
        send_to_kindle(output_path)


if __name__ == "__main__":
    main()
