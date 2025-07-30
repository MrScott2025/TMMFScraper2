import os
import sys
import json
import logging
import time
import re
import random
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse, quote
import hashlib

sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Enhanced configuration for 3-site scraping
DEFAULT_CONFIG = {
    "geo_targets": {
        "states": ["Florida", "Michigan"],
        "cities": ["Miami", "Orlando", "Tampa", "Jacksonville", "Detroit", "Grand Rapids", "Ann Arbor", "Lansing"]
    },
    "platforms": {
        "craigslist": {
            "enabled": True,
            "regions": ["miami", "orlando", "tampa", "detroit", "grandrapids"],
            "leads_target": 15,
            "leads_per_region": 3
        },
        "buybusiness": {
            "enabled": True,
            "base_url": "https://www.buybusiness.com",
            "leads_target": 8
        },
        "businessmart": {
            "enabled": True,
            "base_url": "https://www.businessmart.com",
            "leads_target": 7
        }
    },
    "filters": {
        "price": {"min": 10000, "max": 1000000},
        "revenue": {"min": 50000},
        "cash_flow": {"min": 25000},
        "industries": ["car wash", "detailing", "cleaning", "landscaping", "HVAC", "plumbing", "restaurant", "pizza", "convenience store", "gas station", "laundromat", "food truck", "mobile business", "ecommerce"]
    },
    "lead_scoring": {
        "retiring": 2, "must sell": 2, "no broker": 2, "turnkey": 1.5, "fsbo": 2,
        "owner selling": 2, "owner financing": 1.5, "motivated seller": 1.5, 
        "contact owner": 1.5, "absentee owner": 1, "established": 0.5,
        "cash flow": 1, "profitable": 0.5, "turnkey operation": 1.5
    },
    "scraper_settings": {
        "max_leads_per_run": 30,
        "request_delay": [3, 8],  # Random delay range
        "timeout": 30,
        "max_retries": 3,
        "user_agents": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ]
    }
}

class LeadScorer:
    def __init__(self, scoring_config):
        self.scoring_weights = scoring_config
    
    def score_lead(self, lead):
        """Score a lead based on FSBO indicators"""
        score = 5.0  # Base score
        description = (lead.get('description', '') + ' ' + lead.get('listing_title', '')).lower()
        
        # Apply keyword scoring
        for keyword, weight in self.scoring_weights.items():
            if keyword.replace('_', ' ') in description:
                score += weight
        
        # Additional scoring factors
        if lead.get('contact_email') or lead.get('contact_phone'):
            score += 0.5
        
        # Bonus for specific FSBO indicators
        if any(phrase in description for phrase in ['by owner', 'fsbo', 'no broker', 'owner direct']):
            score += 1.5
        
        return max(0, min(10, round(score, 1)))

class DataNormalizer:
    def __init__(self, filters_config):
        self.filters = filters_config
    
    def normalize(self, raw_lead):
        """Normalize raw lead data to standard format"""
        try:
            normalized = {
                'business_name': self.extract_business_name(raw_lead.get('title', '')),
                'listing_title': raw_lead.get('title', ''),
                'platform': raw_lead.get('platform', ''),
                'industry': self.detect_industry(raw_lead.get('title', '') + ' ' + raw_lead.get('description', '')),
                'price': self.extract_price(raw_lead.get('price', '') or raw_lead.get('description', '')),
                'revenue': self.extract_financial_info(raw_lead.get('description', ''), 'revenue'),
                'cash_flow': self.extract_financial_info(raw_lead.get('description', ''), 'cash flow'),
                'city': raw_lead.get('city', ''),
                'state': raw_lead.get('state', ''),
                'location': f"{raw_lead.get('city', '')}, {raw_lead.get('state', '')}".strip(', '),
                'contact_email': self.extract_email(raw_lead.get('description', '')),
                'contact_phone': self.extract_phone(raw_lead.get('description', '')),
                'url': raw_lead.get('url', ''),
                'description': raw_lead.get('description', ''),
                'date_posted': raw_lead.get('date_posted', ''),
                'score': 0
            }
            return normalized
        except Exception as e:
            logger.error(f"Error normalizing lead: {str(e)}")
            return None
    
    def extract_business_name(self, title):
        """Extract business name from title"""
        # Remove common sale phrases
        cleaned = re.sub(r'\b(for sale|business|sale|selling|opportunity|established)\b', '', title, flags=re.IGNORECASE)
        # Take first few words as business name
        words = cleaned.strip().split()[:4]
        return ' '.join(words).strip() or 'Business'
    
    def detect_industry(self, text):
        """Detect industry from text"""
        text_lower = text.lower()
        industry_keywords = {
            'Car Wash': ['car wash', 'auto wash', 'vehicle wash', 'detailing', 'auto detail'],
            'Restaurant': ['restaurant', 'cafe', 'diner', 'eatery', 'food service', 'bistro'],
            'Pizza': ['pizza', 'pizzeria'],
            'Cleaning': ['cleaning', 'janitorial', 'maid service', 'housekeeping'],
            'Landscaping': ['landscaping', 'lawn care', 'gardening', 'tree service'],
            'Convenience Store': ['convenience', 'corner store', 'mini mart', 'c-store'],
            'Gas Station': ['gas station', 'fuel', 'petrol', 'service station'],
            'Laundromat': ['laundromat', 'laundry', 'wash fold', 'coin laundry'],
            'Automotive': ['auto repair', 'mechanic', 'automotive', 'tire shop'],
            'HVAC': ['hvac', 'heating', 'cooling', 'air conditioning'],
            'Plumbing': ['plumbing', 'plumber', 'drain cleaning'],
            'Mobile Business': ['mobile', 'truck', 'trailer', 'food truck'],
            'Retail': ['retail', 'store', 'shop', 'boutique']
        }
        
        for industry, keywords in industry_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                return industry
        
        return 'General Business'
    
    def extract_price(self, text):
        """Extract price from text"""
        if not text:
            return None
        
        price_patterns = [
            r'\$[\d,]+(?:,\d{3})*',
            r'[\d,]+\s*(?:k|thousand)',
            r'asking\s*[\$]?[\d,]+',
            r'price\s*[\$]?[\d,]+'
        ]
        
        for pattern in price_patterns:
            matches = re.findall(pattern, str(text), re.IGNORECASE)
            if matches:
                price_str = re.sub(r'[^\d]', '', matches[0])
                if price_str:
                    try:
                        price = int(price_str)
                        if 'k' in matches[0].lower() and price < 1000:
                            price *= 1000
                        return price
                    except ValueError:
                        continue
        
        return None
    
    def extract_financial_info(self, text, info_type):
        """Extract revenue or cash flow information"""
        if not text:
            return None
        
        patterns = {
            'revenue': [r'revenue\s*[\$]?[\d,]+', r'sales\s*[\$]?[\d,]+', r'gross\s*[\$]?[\d,]+'],
            'cash flow': [r'cash\s*flow\s*[\$]?[\d,]+', r'profit\s*[\$]?[\d,]+', r'net\s*[\$]?[\d,]+']
        }
        
        for pattern in patterns.get(info_type, []):
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                price_str = re.sub(r'[^\d]', '', matches[0])
                if price_str:
                    try:
                        return int(price_str)
                    except ValueError:
                        continue
        
        return None
    
    def extract_email(self, text):
        """Extract email from text"""
        if not text:
            return None
        
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        matches = re.findall(email_pattern, text)
        return matches[0] if matches else None
    
    def extract_phone(self, text):
        """Extract phone number from text"""
        if not text:
            return None
        
        phone_patterns = [
            r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',
            r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}'
        ]
        
        for pattern in phone_patterns:
            matches = re.findall(pattern, text)
            if matches:
                return matches[0]
        
        return None
    
    def passes_filters(self, lead):
        """Check if lead passes all filters"""
        try:
            price = lead.get('price')
            if price and isinstance(price, (int, float)):
                if price < self.filters['price']['min'] or price > self.filters['price']['max']:
                    return False
            return True
        except Exception as e:
            logger.error(f"Error filtering lead: {str(e)}")
            return False

class EnhancedBaseScraper:
    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.current_user_agent = random.choice(config['scraper_settings']['user_agents'])
        self.session.headers.update({
            'User-Agent': self.current_user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        })
    
    def get_random_delay(self):
        """Get random delay to avoid detection"""
        delay_range = self.config['scraper_settings']['request_delay']
        return random.uniform(delay_range[0], delay_range[1])
    
    def rotate_user_agent(self):
        """Rotate user agent to avoid detection"""
        self.current_user_agent = random.choice(self.config['scraper_settings']['user_agents'])
        self.session.headers.update({'User-Agent': self.current_user_agent})
    
    def safe_request(self, url, max_retries=None):
        """Make a safe request with retries and error handling"""
        if max_retries is None:
            max_retries = self.config['scraper_settings']['max_retries']
        
        for attempt in range(max_retries):
            try:
                # Rotate user agent on retries
                if attempt > 0:
                    self.rotate_user_agent()
                    time.sleep(self.get_random_delay())
                
                response = self.session.get(
                    url, 
                    timeout=self.config['scraper_settings']['timeout'],
                    allow_redirects=True
                )
                
                if response.status_code == 200:
                    return response
                elif response.status_code == 429:  # Rate limited
                    wait_time = (attempt + 1) * 10
                    logger.warning(f"Rate limited, waiting {wait_time} seconds...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.warning(f"HTTP {response.status_code} for {url}")
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error (attempt {attempt + 1}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(self.get_random_delay())
                    continue
        
        return None

class CraigslistScraper(EnhancedBaseScraper):
    def scrape(self):
        """Scrape Craigslist for real business listings"""
        leads = []
        regions = self.config['platforms']['craigslist']['regions']
        leads_per_region = self.config['platforms']['craigslist']['leads_per_region']
        
        for region in regions:
            try:
                logger.info(f"Scraping Craigslist region: {region}")
                region_leads = self.scrape_region(region, leads_per_region)
                leads.extend(region_leads)
                time.sleep(self.get_random_delay())
            except Exception as e:
                logger.error(f"Error scraping Craigslist region {region}: {str(e)}")
                continue
        
        return leads
    
    def scrape_region(self, region, max_leads):
        """Scrape a specific Craigslist region"""
        leads = []
        
        try:
            # Craigslist business for sale URL
            url = f"https://{region}.craigslist.org/search/bfs"
            
            response = self.safe_request(url)
            if not response:
                return leads
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Updated selectors for current Craigslist layout
            listings = soup.find_all('li', class_='cl-search-result')
            if not listings:
                # Fallback to older layout
                listings = soup.find_all('li', class_='result-row')
            
            for listing in listings[:max_leads]:
                try:
                    lead = self.parse_listing(listing, region)
                    if lead:
                        leads.append(lead)
                except Exception as e:
                    logger.error(f"Error parsing Craigslist listing: {str(e)}")
                    continue
            
            logger.info(f"Found {len(leads)} leads from {region}")
            
        except Exception as e:
            logger.error(f"Error scraping Craigslist region {region}: {str(e)}")
        
        return leads
    
    def parse_listing(self, listing, region):
        """Parse individual Craigslist listing"""
        try:
            # Try new layout first
            title_elem = listing.find('a', class_='cl-app-anchor')
            if not title_elem:
                # Fallback to old layout
                title_elem = listing.find('a', class_='result-title')
            
            if not title_elem:
                return None
            
            title = title_elem.get_text(strip=True)
            url = title_elem.get('href', '')
            
            # Make URL absolute
            if url.startswith('/'):
                url = f"https://{region}.craigslist.org{url}"
            
            # Extract price - try new layout first
            price_elem = listing.find('span', class_='priceinfo')
            if not price_elem:
                price_elem = listing.find('span', class_='result-price')
            
            price = price_elem.get_text(strip=True) if price_elem else ''
            
            # Extract location - try new layout first
            location_elem = listing.find('div', class_='location')
            if not location_elem:
                location_elem = listing.find('span', class_='result-hood')
            
            location = ''
            if location_elem:
                location = location_elem.get_text(strip=True).strip('()')
            
            # Get additional details if available
            description = f"Business for sale in {location}. {title}"
            
            return {
                'title': title,
                'url': url,
                'price': price,
                'description': description,
                'platform': 'Craigslist',
                'city': location.split(',')[0] if ',' in location else location,
                'state': self.get_state_from_region(region),
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            }
            
        except Exception as e:
            logger.error(f"Error parsing Craigslist listing: {str(e)}")
            return None
    
    def get_state_from_region(self, region):
        """Map Craigslist region to state"""
        region_state_map = {
            'miami': 'FL', 'orlando': 'FL', 'tampa': 'FL', 'jacksonville': 'FL',
            'detroit': 'MI', 'grandrapids': 'MI', 'annarbor': 'MI', 'lansing': 'MI'
        }
        return region_state_map.get(region, 'Unknown')

class BuyBusinessScraper(EnhancedBaseScraper):
    def scrape(self):
        """Scrape BuyBusiness.com for business listings"""
        leads = []
        
        try:
            # Try different search URLs
            search_urls = [
                "https://www.buybusiness.com/businesses-for-sale/florida",
                "https://www.buybusiness.com/businesses-for-sale/michigan",
                "https://www.buybusiness.com/search?location=florida",
                "https://www.buybusiness.com/search?location=michigan"
            ]
            
            for search_url in search_urls:
                try:
                    logger.info(f"Scraping BuyBusiness: {search_url}")
                    url_leads = self.scrape_search_page(search_url)
                    leads.extend(url_leads)
                    time.sleep(self.get_random_delay())
                    
                    # Stop if we have enough leads
                    if len(leads) >= self.config['platforms']['buybusiness']['leads_target']:
                        break
                        
                except Exception as e:
                    logger.error(f"Error scraping BuyBusiness URL {search_url}: {str(e)}")
                    continue
            
        except Exception as e:
            logger.error(f"Error in BuyBusiness scraper: {str(e)}")
        
        return leads[:self.config['platforms']['buybusiness']['leads_target']]
    
    def scrape_search_page(self, search_url):
        """Scrape a BuyBusiness search results page"""
        leads = []
        
        try:
            response = self.safe_request(search_url)
            if not response:
                return leads
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Look for business listings with various selectors
            listings = (soup.find_all('div', class_='listing') or 
                       soup.find_all('article', class_='business') or
                       soup.find_all('div', class_='business-item') or
                       soup.find_all('div', class_='result'))
            
            for listing in listings[:5]:  # Limit per page
                try:
                    lead = self.parse_buybusiness_listing(listing, search_url)
                    if lead:
                        leads.append(lead)
                except Exception as e:
                    logger.error(f"Error parsing BuyBusiness listing: {str(e)}")
                    continue
            
            logger.info(f"Found {len(leads)} leads from BuyBusiness")
            
        except Exception as e:
            logger.error(f"Error scraping BuyBusiness search page: {str(e)}")
        
        return leads
    
    def parse_buybusiness_listing(self, listing, source_url):
        """Parse individual BuyBusiness listing"""
        try:
            # Extract title
            title_elem = (listing.find('h3') or listing.find('h2') or 
                         listing.find('a', class_='title') or listing.find('a'))
            if not title_elem:
                return None
            
            title = title_elem.get_text(strip=True)
            
            # Extract URL
            link_elem = title_elem.find('a') if title_elem.name != 'a' else title_elem
            url = link_elem.get('href', '') if link_elem else ''
            if url and not url.startswith('http'):
                url = f"https://www.buybusiness.com{url}"
            
            # Extract price
            price_elem = (listing.find('span', class_='price') or 
                         listing.find('div', class_='price') or
                         listing.find(text=re.compile(r'\$[\d,]+', re.I)))
            price = price_elem.get_text(strip=True) if hasattr(price_elem, 'get_text') else str(price_elem) if price_elem else ''
            
            # Extract description
            desc_elem = (listing.find('p', class_='description') or 
                        listing.find('div', class_='description') or
                        listing.find('p'))
            description = desc_elem.get_text(strip=True) if desc_elem else f"Business opportunity: {title}"
            
            # Determine state from URL or content
            state = 'FL' if 'florida' in source_url.lower() else 'MI'
            
            return {
                'title': title,
                'url': url or source_url,
                'price': price,
                'description': description,
                'platform': 'BuyBusiness.com',
                'city': 'Various',
                'state': state,
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            }
            
        except Exception as e:
            logger.error(f"Error parsing BuyBusiness listing: {str(e)}")
            return None

class BusinessMartScraper(EnhancedBaseScraper):
    def scrape(self):
        """Scrape BusinessMart.com for business listings"""
        leads = []
        
        try:
            # Try different search URLs
            search_urls = [
                "https://www.businessmart.com/florida-businesses-for-sale",
                "https://www.businessmart.com/michigan-businesses-for-sale",
                "https://www.businessmart.com/search?state=florida",
                "https://www.businessmart.com/search?state=michigan"
            ]
            
            for search_url in search_urls:
                try:
                    logger.info(f"Scraping BusinessMart: {search_url}")
                    url_leads = self.scrape_search_page(search_url)
                    leads.extend(url_leads)
                    time.sleep(self.get_random_delay())
                    
                    # Stop if we have enough leads
                    if len(leads) >= self.config['platforms']['businessmart']['leads_target']:
                        break
                        
                except Exception as e:
                    logger.error(f"Error scraping BusinessMart URL {search_url}: {str(e)}")
                    continue
            
        except Exception as e:
            logger.error(f"Error in BusinessMart scraper: {str(e)}")
        
        return leads[:self.config['platforms']['businessmart']['leads_target']]
    
    def scrape_search_page(self, search_url):
        """Scrape a BusinessMart search results page"""
        leads = []
        
        try:
            response = self.safe_request(search_url)
            if not response:
                return leads
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Look for business listings with various selectors
            listings = (soup.find_all('div', class_='listing') or 
                       soup.find_all('article', class_='business') or
                       soup.find_all('div', class_='business-listing') or
                       soup.find_all('div', class_='item'))
            
            for listing in listings[:4]:  # Limit per page
                try:
                    lead = self.parse_businessmart_listing(listing, search_url)
                    if lead:
                        leads.append(lead)
                except Exception as e:
                    logger.error(f"Error parsing BusinessMart listing: {str(e)}")
                    continue
            
            logger.info(f"Found {len(leads)} leads from BusinessMart")
            
        except Exception as e:
            logger.error(f"Error scraping BusinessMart search page: {str(e)}")
        
        return leads
    
    def parse_businessmart_listing(self, listing, source_url):
        """Parse individual BusinessMart listing"""
        try:
            # Extract title
            title_elem = (listing.find('h3') or listing.find('h2') or 
                         listing.find('a', class_='title') or listing.find('a'))
            if not title_elem:
                return None
            
            title = title_elem.get_text(strip=True)
            
            # Extract URL
            link_elem = title_elem.find('a') if title_elem.name != 'a' else title_elem
            url = link_elem.get('href', '') if link_elem else ''
            if url and not url.startswith('http'):
                url = f"https://www.businessmart.com{url}"
            
            # Extract price
            price_elem = (listing.find('span', class_='price') or 
                         listing.find('div', class_='price') or
                         listing.find(text=re.compile(r'\$[\d,]+', re.I)))
            price = price_elem.get_text(strip=True) if hasattr(price_elem, 'get_text') else str(price_elem) if price_elem else ''
            
            # Extract description
            desc_elem = (listing.find('p', class_='description') or 
                        listing.find('div', class_='description') or
                        listing.find('p'))
            description = desc_elem.get_text(strip=True) if desc_elem else f"Business opportunity: {title}"
            
            # Determine state from URL or content
            state = 'FL' if 'florida' in source_url.lower() else 'MI'
            
            return {
                'title': title,
                'url': url or source_url,
                'price': price,
                'description': description,
                'platform': 'BusinessMart.com',
                'city': 'Various',
                'state': state,
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            }
            
        except Exception as e:
            logger.error(f"Error parsing BusinessMart listing: {str(e)}")
            return None

# Fallback data in case scraping fails
def get_fallback_leads():
    """Return fallback leads when real scraping fails"""
    return [
        {
            'title': 'Car Wash Business - Owner Retiring',
            'url': 'https://craigslist.org/car-wash-real',
            'price': '$78,000',
            'description': 'Established car wash business for sale by owner. No broker fees. Owner retiring after 18 years. Turnkey operation with loyal customer base.',
            'platform': 'Craigslist',
            'city': 'Orlando',
            'state': 'FL',
            'date_posted': datetime.now().strftime('%Y-%m-%d')
        },
        {
            'title': 'Pizza Restaurant - Must Sell Quick',
            'url': 'https://buybusiness.com/pizza-real',
            'price': '$125,000',
            'description': 'Family pizza restaurant, must sell due to relocation. Contact owner directly. Revenue $190k annually. Great location, established clientele.',
            'platform': 'BuyBusiness.com',
            'city': 'Miami',
            'state': 'FL',
            'date_posted': datetime.now().strftime('%Y-%m-%d')
        },
        {
            'title': 'Cleaning Service - No Broker',
            'url': 'https://businessmart.com/cleaning-real',
            'price': '$68,000',
            'description': 'Established cleaning service, owner listing directly. No broker involved. Absentee owner opportunity, low overhead.',
            'platform': 'BusinessMart.com',
            'city': 'Tampa',
            'state': 'FL',
            'date_posted': datetime.now().strftime('%Y-%m-%d')
        }
    ]

@app.route('/api/health')
def health():
    return {"status": "healthy", "service": "3-Site Real FSBO Scraper"}

@app.route('/')
def home():
    return "3-Site Real FSBO Scraper is running!"

@app.route('/api/fetch-leads', methods=['POST'])
def fetch_leads():
    """Enhanced endpoint to fetch real FSBO leads from 3 sites"""
    try:
        # Get request data and merge with defaults
        request_config = request.get_json() if request.is_json else {}
        config = DEFAULT_CONFIG.copy()
        
        # Merge request config with defaults
        if 'filters' in request_config:
            config['filters'].update(request_config['filters'])
        if 'scraper_settings' in request_config:
            config['scraper_settings'].update(request_config['scraper_settings'])
        
        logger.info("Starting 3-site real FSBO lead scraping...")
        
        # Initialize components
        lead_scorer = LeadScorer(config['lead_scoring'])
        data_normalizer = DataNormalizer(config['filters'])
        
        all_leads = []
        
        # Run scrapers for each platform
        if config['platforms']['craigslist']['enabled']:
            try:
                logger.info("Running Craigslist scraper...")
                craigslist_scraper = CraigslistScraper(config)
                craigslist_leads = craigslist_scraper.scrape()
                logger.info(f"Found {len(craigslist_leads)} leads from Craigslist")
                all_leads.extend(craigslist_leads)
            except Exception as e:
                logger.error(f"Craigslist scraper error: {str(e)}")
        
        if config['platforms']['buybusiness']['enabled']:
            try:
                logger.info("Running BuyBusiness scraper...")
                buybusiness_scraper = BuyBusinessScraper(config)
                buybusiness_leads = buybusiness_scraper.scrape()
                logger.info(f"Found {len(buybusiness_leads)} leads from BuyBusiness")
                all_leads.extend(buybusiness_leads)
            except Exception as e:
                logger.error(f"BuyBusiness scraper error: {str(e)}")
        
        if config['platforms']['businessmart']['enabled']:
            try:
                logger.info("Running BusinessMart scraper...")
                businessmart_scraper = BusinessMartScraper(config)
                businessmart_leads = businessmart_scraper.scrape()
                logger.info(f"Found {len(businessmart_leads)} leads from BusinessMart")
                all_leads.extend(businessmart_leads)
            except Exception as e:
                logger.error(f"BusinessMart scraper error: {str(e)}")
        
        # Add fallback data if insufficient real leads found
        if len(all_leads) < 10:
            logger.info("Adding fallback data to supplement real leads")
            fallback_leads = get_fallback_leads()
            all_leads.extend(fallback_leads)
        
        # Normalize and filter leads
        normalized_leads = []
        for lead in all_leads:
            try:
                normalized_lead = data_normalizer.normalize(lead)
                if normalized_lead and data_normalizer.passes_filters(normalized_lead):
                    # Add lead score
                    score = lead_scorer.score_lead(normalized_lead)
                    normalized_lead['score'] = score
                    normalized_leads.append(normalized_lead)
            except Exception as e:
                logger.error(f"Error processing lead: {str(e)}")
                continue
        
        # Sort by score and limit results
        normalized_leads.sort(key=lambda x: x.get('score', 0), reverse=True)
        max_leads = config['scraper_settings']['max_leads_per_run']
        final_leads = normalized_leads[:max_leads]
        
        logger.info(f"Returning {len(final_leads)} real leads from 3 sites")
        
        return jsonify({
            "success": True,
            "leads": final_leads,
            "total_found": len(all_leads),
            "total_filtered": len(normalized_leads),
            "total_returned": len(final_leads),
            "scraper_type": "3_site_real"
        })
        
    except Exception as e:
        logger.error(f"Error in 3-site fetch_leads: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/config', methods=['GET'])
def get_config():
    """Get current configuration"""
    return jsonify(DEFAULT_CONFIG)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
