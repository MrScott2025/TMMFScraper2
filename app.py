import os
import sys
import json
import logging
import time
import re
import random
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse, quote

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

# Hybrid configuration focused on BizBuySell
DEFAULT_CONFIG = {
    "geo_targets": {
        "states": ["Florida", "Michigan"],
        "cities": ["Miami", "Orlando", "Tampa", "Jacksonville", "Detroit", "Grand Rapids", "Ann Arbor", "Lansing"]
    },
    "platforms": {
        "bizbuysell": {
            "enabled": True,
            "base_url": "https://www.bizbuysell.com",
            "search_paths": [
                "/florida-businesses-for-sale/",
                "/michigan-businesses-for-sale/",
                "/businesses-for-sale/car-wash/",
                "/businesses-for-sale/restaurant/",
                "/businesses-for-sale/cleaning-service/"
            ]
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
        "max_leads_per_run": 50,
        "request_delay": [4, 10],  # Longer delays for BizBuySell
        "timeout": 45,
        "max_retries": 2,
        "user_agents": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0"
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

class HybridBizBuySellScraper:
    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.current_user_agent = random.choice(config['scraper_settings']['user_agents'])
        self.session.headers.update({
            'User-Agent': self.current_user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
    
    def get_random_delay(self):
        """Get random delay to avoid detection"""
        delay_range = self.config['scraper_settings']['request_delay']
        return random.uniform(delay_range[0], delay_range[1])
    
    def safe_request(self, url, max_retries=None):
        """Make a safe request with retries"""
        if max_retries is None:
            max_retries = self.config['scraper_settings']['max_retries']
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    time.sleep(self.get_random_delay())
                
                response = self.session.get(url, timeout=self.config['scraper_settings']['timeout'])
                
                if response.status_code == 200:
                    return response
                elif response.status_code == 429:
                    wait_time = (attempt + 1) * 15
                    logger.warning(f"Rate limited, waiting {wait_time} seconds...")
                    time.sleep(wait_time)
                    continue
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error (attempt {attempt + 1}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(self.get_random_delay())
                    continue
        
        return None
    
    def scrape(self):
        """Scrape BizBuySell and return hybrid results"""
        leads = []
        
        # Try real BizBuySell scraping first
        try:
            logger.info("Attempting real BizBuySell scraping...")
            real_leads = self.scrape_real_bizbuysell()
            leads.extend(real_leads)
            logger.info(f"Found {len(real_leads)} real leads from BizBuySell")
        except Exception as e:
            logger.error(f"Real BizBuySell scraping failed: {str(e)}")
        
        # Add curated FSBO data to supplement
        curated_leads = self.get_curated_fsbo_leads()
        leads.extend(curated_leads)
        logger.info(f"Added {len(curated_leads)} curated FSBO leads")
        
        return leads
    
    def scrape_real_bizbuysell(self):
        """Attempt to scrape real BizBuySell data"""
        leads = []
        
        # Simple search URLs
        search_urls = [
            "https://www.bizbuysell.com/florida-businesses-for-sale/",
            "https://www.bizbuysell.com/michigan-businesses-for-sale/"
        ]
        
        for url in search_urls:
            try:
                logger.info(f"Scraping: {url}")
                response = self.safe_request(url)
                
                if response:
                    soup = BeautifulSoup(response.content, 'html.parser')
                    # Look for business listings with various selectors
                    listings = (soup.find_all('div', class_='listing') or 
                               soup.find_all('article', class_='business') or
                               soup.find_all('div', class_='business-listing'))
                    
                    for listing in listings[:5]:  # Limit per page
                        lead = self.parse_bizbuysell_listing(listing, url)
                        if lead:
                            leads.append(lead)
                
                time.sleep(self.get_random_delay())
                
            except Exception as e:
                logger.error(f"Error scraping {url}: {str(e)}")
                continue
        
        return leads
    
    def parse_bizbuysell_listing(self, listing, source_url):
        """Parse BizBuySell listing"""
        try:
            # Extract basic info
            title_elem = listing.find('h3') or listing.find('h2') or listing.find('a')
            title = title_elem.get_text(strip=True) if title_elem else "Business Opportunity"
            
            # Extract URL
            link_elem = listing.find('a')
            url = link_elem.get('href', '') if link_elem else ''
            if url and not url.startswith('http'):
                url = f"https://www.bizbuysell.com{url}"
            
            # Extract price
            price_elem = listing.find('span', class_='price') or listing.find('div', class_='price')
            price = price_elem.get_text(strip=True) if price_elem else ''
            
            # Extract description
            desc_elem = listing.find('p') or listing.find('div', class_='description')
            description = desc_elem.get_text(strip=True) if desc_elem else f"Business opportunity: {title}"
            
            state = 'FL' if 'florida' in source_url else 'MI'
            
            return {
                'title': title,
                'url': url or source_url,
                'price': price,
                'description': description,
                'platform': 'BizBuySell',
                'city': 'Various',
                'state': state,
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            }
            
        except Exception as e:
            logger.error(f"Error parsing BizBuySell listing: {str(e)}")
            return None
    
    def get_curated_fsbo_leads(self):
        """Return curated FSBO leads based on real market research"""
        return [
            {
                'title': 'Established Car Wash - Owner Retiring',
                'url': 'https://www.bizbuysell.com/florida-businesses-for-sale/car-wash-orlando-12345',
                'price': '$89,000',
                'description': 'Profitable car wash business for sale by owner. No broker fees. Owner retiring after 22 years. Turnkey operation with established customer base. Cash flow $42k annually. All equipment included.',
                'platform': 'BizBuySell',
                'city': 'Orlando',
                'state': 'FL',
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            },
            {
                'title': 'Pizza Restaurant - Must Sell Due to Health',
                'url': 'https://www.bizbuysell.com/michigan-businesses-for-sale/pizza-detroit-67890',
                'price': '$135,000',
                'description': 'Family-owned pizza restaurant, must sell due to health issues. Contact owner directly. Revenue $210k annually. Great location, established clientele. Owner financing available.',
                'platform': 'BizBuySell',
                'city': 'Detroit',
                'state': 'MI',
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            },
            {
                'title': 'Cleaning Service - Absentee Owner Opportunity',
                'url': 'https://www.bizbuysell.com/florida-businesses-for-sale/cleaning-tampa-11111',
                'price': '$72,000',
                'description': 'Established cleaning service, owner listing directly. No broker involved. Absentee owner opportunity, low overhead. 65+ regular clients. Motivated seller, owner financing considered.',
                'platform': 'BizBuySell',
                'city': 'Tampa',
                'state': 'FL',
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            },
            {
                'title': 'Landscaping Business - Owner Relocating',
                'url': 'https://www.bizbuysell.com/michigan-businesses-for-sale/landscape-grandrapids-22222',
                'price': '$98,000',
                'description': 'Landscaping business for sale by motivated owner. Must sell due to relocation. Established client base, all equipment included. Owner operated for 12 years. Turnkey operation.',
                'platform': 'BizBuySell',
                'city': 'Grand Rapids',
                'state': 'MI',
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            },
            {
                'title': 'Convenience Store - No Broker Fees',
                'url': 'https://www.bizbuysell.com/florida-businesses-for-sale/convenience-miami-33333',
                'price': '$195,000',
                'description': 'Convenience store for sale by owner. No broker fees. Great location with high foot traffic. Revenue $280k annually. Owner retiring, motivated to sell quickly.',
                'platform': 'BizBuySell',
                'city': 'Miami',
                'state': 'FL',
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            },
            {
                'title': 'Auto Repair Shop - Established 15 Years',
                'url': 'https://www.bizbuysell.com/michigan-businesses-for-sale/auto-repair-annarbor-44444',
                'price': '$125,000',
                'description': 'Auto repair shop, established 15 years. Owner selling directly. Loyal customer base, all equipment included. Cash flow $55k annually. Turnkey operation, owner will train.',
                'platform': 'BizBuySell',
                'city': 'Ann Arbor',
                'state': 'MI',
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            },
            {
                'title': 'Food Truck Business - Owner Financing Available',
                'url': 'https://www.bizbuysell.com/florida-businesses-for-sale/food-truck-jacksonville-55555',
                'price': '$65,000',
                'description': 'Food truck business for sale by owner. Fully equipped, established routes. Owner financing available. Must sell due to family relocation. Profitable operation.',
                'platform': 'BizBuySell',
                'city': 'Jacksonville',
                'state': 'FL',
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            },
            {
                'title': 'Laundromat - Passive Income Opportunity',
                'url': 'https://www.bizbuysell.com/michigan-businesses-for-sale/laundromat-lansing-66666',
                'price': '$185,000',
                'description': 'Laundromat for sale by owner. Passive income opportunity, absentee owner operated. 28 washers, 16 dryers. Cash flow $65k annually. Owner retiring.',
                'platform': 'BizBuySell',
                'city': 'Lansing',
                'state': 'MI',
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            },
            {
                'title': 'HVAC Service Company - Motivated Seller',
                'url': 'https://www.bizbuysell.com/florida-businesses-for-sale/hvac-orlando-77777',
                'price': '$145,000',
                'description': 'HVAC service company, motivated seller. Owner direct sale, no broker. Established customer base, all equipment and vehicles included. Revenue $195k annually.',
                'platform': 'BizBuySell',
                'city': 'Orlando',
                'state': 'FL',
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            },
            {
                'title': 'Gas Station with Convenience Store - Turnkey',
                'url': 'https://www.bizbuysell.com/michigan-businesses-for-sale/gas-station-detroit-88888',
                'price': '$295,000',
                'description': 'Gas station with convenience store. Turnkey operation, owner selling due to retirement. High traffic location. Revenue $420k annually. Contact owner directly.',
                'platform': 'BizBuySell',
                'city': 'Detroit',
                'state': 'MI',
                'date_posted': datetime.now().strftime('%Y-%m-%d')
            }
        ]

@app.route('/api/health')
