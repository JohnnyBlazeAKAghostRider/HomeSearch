import requests
from bs4 import BeautifulSoup
import sqlite3
from datetime import datetime
import schedule
import time
from plyer import notification
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import os
import re

# Initial settings
BASE_URL = "https://www.xior-booking.com"
COUNTRY = "Netherlands"
CITY = "Leeuwarden"
CHECK_INTERVAL = 24 * 60 * 60  # Check daily (seconds)
CITY_ID = "20"  # ID for Leeuwarden

# Setup undetected-chromedriver
options = uc.ChromeOptions()
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu")
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

try:
    driver = uc.Chrome(options=options)
    driver.set_page_load_timeout(60)
except Exception as e:
    print(f"Error starting ChromeDriver: {e}")
    exit(1)

# Connect to database to store previous listings
def init_db():
    conn = sqlite3.connect("housing.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS listings 
                 (id TEXT PRIMARY KEY, title TEXT, start_date TEXT, link TEXT)''')
    conn.commit()
    conn.close()

# Function to analyze HTML and look for city IDs
def analyze_html():
    soup = BeautifulSoup(driver.page_source, "html.parser")
    
    # Look for script tags
    scripts = soup.find_all("script")
    city_data = None
    for script in scripts:
        if script.string and "cities" in script.string:
            print("Found script with cities data!")
            content = script.string
            # Try to extract city data with regex
            match = re.search(r'cities\s*=\s*(\[.*?\])', content, re.DOTALL)
            if match:
                city_data = match.group(1)
                print(f"Extracted city data: {city_data[:200]}...")
                
                # Try to find Leeuwarden in the data
                leeuwarden_match = re.search(r'id["\']?\s*:\s*["\']?(\d+)["\']?,.*?name["\']?\s*:\s*["\']?Leeuwarden["\']?', 
                                            city_data, re.DOTALL | re.IGNORECASE)
                if leeuwarden_match:
                    city_id = leeuwarden_match.group(1)
                    print(f"Found Leeuwarden ID: {city_id}")
                    return city_id
    
    # Look for form elements that might contain city info
    forms = soup.find_all("form")
    for form in forms:
        print(f"Found form with action: {form.get('action', 'No action')}")
        
    # Look for any city references
    city_elements = soup.find_all(string=re.compile("Leeuwarden"))
    for elem in city_elements:
        print(f"Found Leeuwarden reference: {elem.parent}")
    
    return None

# Get the list of houses with applied filters using direct URL approach
def fetch_listings():
    try:
        print("Loading main page first to analyze structure...")
        driver.get(f"{BASE_URL}/en/student-rooms")

        print("Waiting for potential Cloudflare challenge to pass...")
        max_wait_time = 60  # Maximum wait time (seconds)
        start_time = time.time()
        
        # Wait for Cloudflare to pass or for the actual page to load
        while time.time() - start_time < max_wait_time:
            page_source = driver.page_source.lower()
            if "verify you are human" in page_source or "just a moment" in page_source:
                print("Still on Cloudflare page, waiting...")
                time.sleep(5)  # Wait 5 seconds
            else:
                # Check if we've actually loaded the real page by looking for some expected content
                if "student-rooms" in page_source or "space-item" in page_source:
                    print("Cloudflare challenge passed! Real page loaded.")
                    break
                else:
                    print("Cloudflare page changed, but real page not loaded yet. Waiting...")
                    time.sleep(5)
        else:
            raise Exception("Cloudflare challenge not passed after 60 seconds or real page not loaded.")

        # Give time for page to fully load
        print("Waiting for page to fully load...")
        time.sleep(10)  # Extra time for JS to execute
        
        # Save HTML page for debugging
        with open("initial_page.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print("Initial HTML saved to initial_page.html")
        
        # Try to analyze the HTML for city structure
        city_id = analyze_html()
        if city_id:
            print(f"Using detected city ID: {city_id}")
        else:
            print(f"Using default city ID: {CITY_ID}")
            city_id = CITY_ID
        
        # Now try to navigate directly to the Leeuwarden page
        print(f"Trying direct URL approach for Leeuwarden (ID: {city_id})...")
        
        # Try multiple URL formats
        url_formats = [
            f"{BASE_URL}/en/student-rooms?city={city_id}",
            f"{BASE_URL}/en/student-rooms/{CITY.lower()}",
            f"{BASE_URL}/en/student-rooms?city_id={city_id}",
            f"{BASE_URL}/en/student-rooms/city/{city_id}",
            f"{BASE_URL}/en/student-rooms"  # Try the main page and filter later
        ]
        
        for url in url_formats:
            try:
                print(f"Trying URL: {url}")
                driver.get(url)
                
                # Wait for page to load
                time.sleep(10)
                
                # Save HTML for this attempt
                filename = f"url_attempt_{url.split('/')[-1].replace('?', '_')}.html"
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                print(f"HTML saved to {filename}")

                # Check if this URL seems to have worked
                page_source = driver.page_source.lower()
                if "leeuwarden" in page_source and "space-item" in page_source:
                    print(f"Found 'Leeuwarden' and listings in page content! This URL works.")
                    
                    # Try to find listings on this page
                    soup = BeautifulSoup(driver.page_source, "html.parser")
                    listings = soup.find_all("div", class_="space-item")
                    
                    if listings:
                        print(f"Found {len(listings)} listings with class 'space-item'")
                        return listings
                    else:
                        print("No listings found with class 'space-item', trying other methods...")
                        # Try other methods to find listings
                        potential_listings = []
                        for class_name in ["listing-item", "residence-item", "room-item", "property-item", "accommodation"]:
                            items = soup.find_all("div", class_=lambda c: c and class_name in c)
                            if items:
                                print(f"Found {len(items)} items with class containing '{class_name}'")
                                potential_listings.extend(items)
                        
                        if potential_listings:
                            unique_listings = list(dict.fromkeys(potential_listings))  # Remove duplicates
                            print(f"Found {len(unique_listings)} unique potential listings")
                            return unique_listings
                else:
                    print(f"Did not find 'Leeuwarden' and listings in page content. This URL may not be correct.")
            except Exception as e:
                print(f"Error with URL {url}: {e}")
                
        # If we've tried all URLs and found nothing, try one more approach
        print("Trying to find direct link to Leeuwarden from main page...")
        driver.get(f"{BASE_URL}/en/student-rooms")
        time.sleep(10)
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        leeuwarden_links = soup.find_all("a", href=lambda h: h and "leeuwarden" in h.lower())
        
        if leeuwarden_links:
            print(f"Found {len(leeuwarden_links)} links to Leeuwarden")
            for link in leeuwarden_links:
                href = link.get("href")
                if not href.startswith("http"):
                    href = BASE_URL + href
                
                print(f"Trying Leeuwarden link: {href}")
                driver.get(href)
                time.sleep(10)
                
                # Save HTML for this attempt
                with open("leeuwarden_link.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                print("HTML saved to leeuwarden_link.html")
                
                # Try to find listings on this page
                soup = BeautifulSoup(driver.page_source, "html.parser")
                listings = soup.find_all("div", class_="space-item")
                
                if listings:
                    print(f"Found {len(listings)} listings")
                    return listings
        
        # Fallback method
        print("Using fallback method to extract any potential listings...")
        driver.get(f"{BASE_URL}/en/student-rooms")
        time.sleep(10)
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        all_divs = soup.find_all("div")
        potential_listings = [div for div in all_divs if div.find("a") and len(div.find_all("div")) > 2]
        
        if potential_listings:
            print(f"Found {len(potential_listings)} potential listings using fallback method")
            return potential_listings[:20]  # Take first 20 to avoid too many
            
        raise Exception("Could not find any listings with any method")
            
    except Exception as e:
        with open("error_page.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print("Error HTML saved to error_page.html")
        print(f"Error fetching data: {e}")
        return []

# Get details for each listing
def fetch_details(listing_link):
    try:
        print(f"Loading details page: {listing_link}")
        driver.get(listing_link)
        
        # Wait for page to load
        time.sleep(10)

        # Save details page for debugging
        detail_filename = f"details_{listing_link.split('/')[-1].replace('?', '_')}.html"
        with open(detail_filename, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"Details saved to {detail_filename}")
        
        # Get page HTML
        soup = BeautifulSoup(driver.page_source, "html.parser")

        # Look for text about starting date (try multiple possibilities)
        start_date_str = "Unknown"
        for pattern in ["Starting date", "Start date", "Available from", "Move-in date"]:
            for tag in ["p", "div", "span", "li"]:
                elements = soup.find_all(tag, string=lambda s: s and pattern.lower() in s.lower())
                if elements:
                    date_text = elements[0].text.strip()
                    # Try to extract date using regex
                    date_match = re.search(r'(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4})', date_text)
                    if date_match:
                        start_date_str = date_match.group(1)
                        print(f"Date found: {start_date_str}")
                        return start_date_str

        print("Date not found.")
        return "Unknown"
    except Exception as e:
        print(f"Error getting details: {e}")
        return "Unknown"

# Extract information and check filters
def process_listings(listings):
    conn = sqlite3.connect("housing.db")
    c = conn.cursor()
    new_listings = []

    for listing in listings:
        try:
            # Try to find title - look for headings or elements with title-like classes
            title_element = (
                listing.find("h1") or listing.find("h2") or listing.find("h3") or 
                listing.find(class_=lambda c: c and any(t in c.lower() for t in ["title", "name", "heading"]))
            )
            
            title = "Untitled Property" if not title_element else title_element.text.strip()
            
            # Try to find link
            link_element = listing.find("a")
            if not link_element:
                print("Link not found, skipping...")
                continue
                
            link = link_element.get("href")
            if not link:
                print("Link href attribute not found, skipping...")
                continue
                
            if not link.startswith("http"):
                link = BASE_URL + link  # Complete link if relative
                
            # Extract ID from the link
            link_parts = link.split("/")
            listing_id = link_parts[-1] if link_parts else "unknown"
            
            # Get date from details page
            start_date_str = fetch_details(link)

            # Convert date to comparable format
            try:
                if start_date_str != "Unknown":
                    # Try different date formats
                    date_formats = ["%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d.%m.%Y"]
                    start_date = None
                    
                    for date_format in date_formats:
                        try:
                            start_date = datetime.strptime(start_date_str, date_format)
                            break
                        except ValueError:
                            continue
                else:
                    start_date = None
            except Exception:
                start_date = None

            # Check if this listing is new
            c.execute("SELECT * FROM listings WHERE id = ?", (listing_id,))
            if not c.fetchone():
                c.execute("INSERT INTO listings (id, title, start_date, link) VALUES (?, ?, ?, ?)",
                        (listing_id, title, start_date_str, link))
                new_listings.append({"title": title, "start_date": start_date, "link": link})
                print(f"New listing found: {title}")
        except Exception as e:
            print(f"Error processing listing: {e}")
            continue

    conn.commit()
    conn.close()
    return new_listings

# Send notification
def send_notification(listing):
    title = f"New housing found: {listing['title']}"
    start_date = listing['start_date'].strftime("%d-%m-%Y") if listing['start_date'] else "Unknown"
    message = f"Start date: {start_date}\nLink: {listing['link']}"
    
    try:
        notification.notify(
            title=title,
            message=message,
            app_name="Housing Checker",
            timeout=10
        )
        print(f"Notification sent for: {listing['title']}")
    except Exception as e:
        print(f"Error sending notification: {e}")
        print(f"New listing available: {listing['title']} - {listing['link']}")

# Check filters and send notifications
def check_and_notify():
    listings = fetch_listings()
    if not listings:
        print("No listings found or error occurred")
        return
        
    new_listings = process_listings(listings)

    august_start = datetime.strptime("01-08-2025", "%d-%m-%Y")  # August start date

    for listing in new_listings:
        if listing["start_date"]:  # If date is known
            # Check if start date is from August
            if listing["start_date"] >= august_start:
                send_notification(listing)
        else:
            # If date unknown, just notify about new listing
            send_notification(listing)

# Schedule
def job():
    print(f"Checking site at {datetime.now()}")
    try:
        check_and_notify()
    except Exception as e:
        print(f"Error in job: {e}")

# Start program
if __name__ == "__main__":
    try:
        init_db()
        # Initial run
        job()
        
        # Schedule future runs
        schedule.every(1).days.do(job)
        
        # Keep running for scheduled checks
        while True:
            schedule.run_pending()
            time.sleep(60)  # Check every minute if schedule needs to run
    finally:
        # Make sure to quit driver
        driver.quit()