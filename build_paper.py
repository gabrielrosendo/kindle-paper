#!/usr/bin/env python3

import os
import re
import smtplib
import ssl
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from html import escape
from mimetypes import guess_type
from urllib.parse import urlparse
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


def extract_image_url(entry: dict) -> str | None:
    candidates: list[str] = []

    media_thumbnail = entry.get("media_thumbnail")
    if isinstance(media_thumbnail, list):
        for item in media_thumbnail:
            if isinstance(item, dict) and item.get("url"):
                candidates.append(item["url"])
    elif isinstance(media_thumbnail, dict) and media_thumbnail.get("url"):
        candidates.append(media_thumbnail["url"])

    if isinstance(entry.get("media_content"), list):
        for item in entry["media_content"]:
            if isinstance(item, dict) and item.get("url"):
                candidates.append(item["url"])

    for link in entry.get("links", []):
        if isinstance(link, dict):
            link_type = str(link.get("type", "")).lower()
            href = link.get("href") or link.get("url")
            if href and ("image" in link_type or href.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))):
                candidates.append(href)

    content = entry.get("content") or []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                url = item.get("url") or item.get("src")
                if url:
                    candidates.append(url)

    for html_blob in [entry.get("summary"), entry.get("description")]:
        if html_blob:
            matches = re.findall(r"https?://[^\s\"']+\.(?:jpg|jpeg|png|webp|gif)", html_blob, flags=re.IGNORECASE)
            candidates.extend(matches)

    for candidate in candidates:
        lower = candidate.lower()
        if "nytimes" in lower or lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            return candidate
    return candidates[0] if candidates else None


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
            image_url = extract_image_url(entry)
            items.append(
                {
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "source": source_name,
                    "published": published,
                    "image_url": image_url,
                }
            )

    items.sort(key=lambda item: (item["published"] or datetime(1970, 1, 1, tzinfo=EST), item["title"].lower()), reverse=True)
    return items[:6]


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower())
    return value.strip("-") or "section"


def download_image(url: str) -> bytes | None:
    if not url:
        return None
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read()
        return data if data else None
    except Exception as exc:  # pragma: no cover - image fetch is best-effort
        print(f"Warning: unable to download image: {url} ({exc})")
        return None


def render_story_card(item: dict, compact: bool = False) -> str:
    link = escape(item["link"] or "#")
    title = escape(item["title"]) 
    summary = escape(item["summary"]) if item.get("summary") else ""
    source = escape(item["source"])
    image_html = ""
    image_ref = item.get("image_ref")
    if image_ref:
        image_html = f"<img src='{image_ref}' alt='{title}' />"

    story_class = "story compact" if compact else "story"
    summary_html = f"<p>{summary}</p>" if summary else ""
    return (
        f"<div class='{story_class}'>"
        f"{image_html}"
        f"<div class='story-meta'>{source}</div>"
        f"<h3><a href='{link}'>{title}</a></h3>"
        f"{summary_html}"
        f"</div>"
    )


def build_book(sections: dict[str, list[dict]]) -> epub.EpubBook:
    today = datetime.now(EST).strftime("%A, %B %d, %Y")
    book = epub.EpubBook()
    book.set_identifier("morning-paper")
    book.set_title(BOOK_TITLE)
    book.set_language("en")
    book.add_author("Morning Paper")

    top_stories = [item for section in sections.values() for item in section][:5]
    for item in top_stories:
        if item.get("image_url"):
            data = download_image(item["image_url"])
            if data:
                parsed = urlparse(item["image_url"])
                extension = os.path.splitext(parsed.path)[1].lower() or ".jpg"
                image_name = f"images/{slugify(item['title'])}-{len([i for i in top_stories if i.get('image_ref')])}{extension}"
                image_item = epub.EpubImage()
                image_item.file_name = image_name
                image_item.content = data
                image_item.media_type = guess_type(image_name)[0] or "image/jpeg"
                book.add_item(image_item)
                item["image_ref"] = image_name

    cover = epub.EpubHtml(title="Cover", file_name="cover.xhtml", lang="en")
    lead_story = top_stories[0] if top_stories else None
    secondary_stories = top_stories[1:4] if len(top_stories) > 1 else []

    lead_html = ""
    if lead_story:
        lead_image = f"<img src='{lead_story['image_ref']}' alt='{escape(lead_story['title'])}' />" if lead_story.get("image_ref") else ""
        lead_html = (
            f"<div class='lead-story'>"
            f"{lead_image}"
            f"<div class='kicker'>Lead Story</div>"
            f"<h2><a href='{escape(lead_story['link'] or '#')}'>{escape(lead_story['title'])}</a></h2>"
            f"<p>{escape(lead_story.get('summary', ''))}</p>"
            f"</div>"
        )

    sidebar_html = "".join(
        f"<div class='mini-story'><h4><a href='{escape(story['link'] or '#')}'>{escape(story['title'])}</a></h4></div>"
        for story in secondary_stories
    )

    cover.content = f"""
    <html xmlns="http://www.w3.org/1999/xhtml">
      <head>
        <title>{escape(BOOK_TITLE)}</title>
        <style>
          body {{ font-family: Georgia, serif; margin: 0; padding: 0; background: #f3efe7; color: #111; }}
          .paper {{ padding: 24px; }}
          .masthead {{ text-align: center; font-size: 2.6em; font-weight: bold; letter-spacing: 0.08em; text-transform: uppercase; padding-bottom: 8px; border-bottom: 3px solid #111; }}
          .date {{ text-align: center; font-size: 1.0em; text-transform: uppercase; letter-spacing: 0.2em; margin: 8px 0 18px 0; }}
          .grid {{ display: block; }}
          .lead-story {{ margin-bottom: 16px; padding: 12px; border: 2px solid #111; background: #fff; }}
          .lead-story img {{ width: 100%; max-height: 280px; object-fit: cover; display: block; margin-bottom: 10px; }}
          .kicker {{ font-size: 0.8em; font-weight: bold; text-transform: uppercase; letter-spacing: 0.12em; color: #444; }}
          .lead-story h2 {{ font-size: 1.8em; line-height: 1.1; margin: 8px 0 10px 0; }}
          .lead-story p {{ font-size: 1.0em; line-height: 1.5; margin: 0; }}
          .mini-story {{ padding: 12px; border-top: 1px solid #999; background: #f9f6f1; }}
          .mini-story h4 {{ margin: 0; font-size: 1.1em; line-height: 1.3; }}
          a {{ color: #0b1b2f; text-decoration: none; }}
        </style>
      </head>
      <body>
        <div class='paper'>
          <div class='masthead'>{escape(BOOK_TITLE)}</div>
          <div class='date'>{escape(today)}</div>
          <div class='grid'>
            {lead_html}
            {sidebar_html}
          </div>
        </div>
      </body>
    </html>
    """
    book.add_item(cover)

    toc = [epub.Link("cover.xhtml", "Cover", "cover")]
    chapters = []

    for section_title, items in sections.items():
        section_slug = slugify(section_title)
        page = epub.EpubHtml(title=section_title, file_name=f"{section_slug}.xhtml", lang="en")
        section_html = [
            "<html xmlns=\"http://www.w3.org/1999/xhtml\">",
            "<head>",
            "<title>" + escape(section_title) + "</title>",
            "<style>",
            "body { font-family: Georgia, serif; margin: 0; padding: 20px; background: #f8f5ee; color: #111; }",
            "h1 { font-size: 2.1em; letter-spacing: 0.06em; text-transform: uppercase; border-bottom: 3px solid #111; padding-bottom: 8px; margin-bottom: 16px; }",
            ".story { background: #fff; border: 1px solid #d6d0c4; padding: 12px; margin-bottom: 16px; }",
            ".story img { display: block; width: 100%; max-height: 240px; object-fit: cover; margin-bottom: 10px; }",
            ".story-meta { font-size: 0.72em; font-weight: bold; letter-spacing: 0.1em; text-transform: uppercase; color: #5b5a57; margin-bottom: 6px; }",
            "h3 { font-size: 1.4em; margin: 0 0 8px 0; line-height: 1.25; }",
            "p { font-size: 1.0em; line-height: 1.5; margin: 0; }",
            "a { color: #0b1b2f; text-decoration: none; }",
            "</style>",
            "</head>",
            "<body>",
            f"<h1>{escape(section_title)}</h1>",
        ]

        for item in items:
            section_html.append(render_story_card(item, compact=False))

        section_html.extend(["</body>", "</html>"])
        page.content = "".join(section_html)
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
    sender = (os.environ.get("SENDER_EMAIL") or "").strip()
    username = (os.environ.get("SMTP_USERNAME") or sender).strip()
    password = (os.environ.get("SMTP_PASSWORD") or "").strip().replace(" ", "")
    host = (os.environ.get("SMTP_HOST") or "").strip()
    port = (os.environ.get("SMTP_PORT") or "").strip()
    kindle_email = (os.environ.get("KINDLE_EMAIL") or "").strip()

    if not all([sender, username, password, host, port, kindle_email]):
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
    try:
        with smtplib.SMTP_SSL(host, int(port), context=context, timeout=30) as server:
            server.login(username, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        print(f"SMTP authentication failed. Check the Gmail app password and secret values: {exc}")
        raise
    except smtplib.SMTPException as exc:
        print(f"SMTP error while sending Kindle document: {exc}")
        raise

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
