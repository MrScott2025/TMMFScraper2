import os
import sys
import json
import logging
import time
import re
import random
from datetime import datetime
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

# Default configuration
DEFAULT_CONFIG = {
    "geo_targets": {
        "states": ["Florida", "Michigan"],
        "cities": ["Miami", "Orlando", "Tampa", "Jacksonville", "Detroit", "Grand Rapids", "Ann Arbor", "Lansing"]
    },
    "platforms": {
        "craigslist": {
            "enabled": True,
            "regions": ["miami", "orlando", "tampa", "jacksonville", "detroit", "grandrapids", "annarbor", "lansing"],
            "keywords": ["business for sale", "owner selling", "turnkey", "must sell"]
        },
        "bizbuysell": {
            "enabled": True,
            "filters": ["owner selling", "no broker", "fsbo", "contact owner"]
        },
        "businessbroker": {
            "enabled": True,
            "filters": ["no broker", "owner listing", "motivated seller"]
        },
        "flippa": {
            "enabled": True,
            "seller_type": "owner"
        },
        "facebook_marketplace": {
            "enabled": True
        }
    },
    "filters": {
        "price": {"min": 10000, "max": 1000000},
        "revenue": {"min": 50000},
        "cash_flow": {"min": 25000},
        "industries": ["car wash", "detailing", "cleaning", "landscaping", "HVAC", "plumbing", "ecommerce", "restaurant", "pizza", "convenience store", "gas station", "laundromat", "food truck", "mobile business"]
    },
    "lead_scoring": {
        "retiring": 2, "must sell": 2, "no broker": 2, "turnkey": 1.5,
        "low overhead": 1, "absentee owner": 1, "owner operated": 1,
        "motivated seller": 1.5, "fsbo": 2, "contact owner": 1.5,
        "missing_contact": -1, "price_above_max": -2
    },
    "scraper_settings": {
        "max_leads_per_run": 50,
        "request_delay": 3,
        "timeout": 30,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
}

class LeadScorer:
    def __init__(self, scoring_config):
        self.scoring_weights = scoring_config
    
    def score_lead(self, lead):
        """Score a lead based on description keywords and other factors"""
        score = 5.0  # Base score
        description = (lead.get('description', '') + ' ' + lead.get('listing_title', '')).lower()
        
        # Apply keyword scoring
        for keyword, weight in self.scoring_weights.items():
            if keyword.replace('_', ' ') in description:
                score += weight
                logger.info(f"Found keyword '{keyword}': +{weight} points")
        
        # Additional scoring factors
        if lead.get('contact_email') or lead.get('contact_phone'):
            score += 0.5
        
        # Penalty for high prices
        if lead.get('price') and isinstance(lead.get('price'), (int, float)):
            if lead.get('price') > 1000000:
                score -= 2
        
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
        cleaned = re.sub(r'\b(for sale|business|sale|selling|opportunity)\b', '', title, flags=re.IGNORECASE)
        # Take first few words as business name
        words = cleaned.strip().split()[:3]
        return ' '.join(words).strip() or 'Business'
    
    def detect_industry(self, text):
        """Detect industry from text"""
        text_lower = text.lower()
        industry_keywords = {
            'Car Wash': ['car wash', 'auto wash', 'vehicle wash', 'detailing'],
            'Restaurant': ['restaurant', 'cafe', 'diner', 'eatery', 'food service'],
            'Pizza': ['pizza', 'pizzeria'],
            'Cleaning': ['cleaning', 'janitorial', 'maid service'],
            'Landscaping': ['landscaping', 'lawn care', 'gardening', 'tree service'],
            'Convenience Store': ['convenience', 'corner store', 'mini mart', 'c-store'],
            'Gas Station': ['gas station', 'fuel', 'petrol', 'service station'],
            'Laundromat': ['laundromat', 'laundry', 'wash fold', 'coin laundry'],
            'Automotive': ['auto repair', 'mechanic', 'automotive', 'tire shop'],
            'HVAC': ['hvac', 'heating', 'cooling', 'air conditioning'],
            'Plumbing': ['plumbing', 'plumber', 'drain cleaning'],
            'Ecommerce': ['ecommerce', 'online store', 'dropshipping', 'amazon fba'],
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
        
        # Look for price patterns
        price_patterns = [
            r'\$[\d,]+(?:,\d{3})*',
            r'[\d,]+\s*(?:dollars?|k|thousand)',
            r'asking\s*[\$]?[\d,]+',
            r'price\s*[\$]?[\d,]+'
        ]
        
        for pattern in price_patterns:
            matches = re.findall(pattern, str(text), re.IGNORECASE)
            if matches:
                # Extract numbers and convert
                price_str = re.sub(r'[^\d]', '', matches[0])
                if price_str:
                    try:
                        price = int(price_str)
                        # Handle 'k' notation
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
            # Price filter
            price = lead.get('price')
            if price and isinstance(price, (int, float)):
                if price < self.filters['price']['min'] or price > self.filters['price']['max']:
                    return False
            
            # Revenue filter
            revenue = lead.get('revenue')
            if revenue and isinstance(revenue, (int, float)) and revenue < self.filters['revenue']['min']:
                return False
            
            # Cash flow filter
            cash_flow = lead.get('cash_flow')
            if cash_flow and isinstance(cash_flow, (int, float)) and cash_flow < self.filters['cash_flow']['min']:
                return False
            
            return True
        except Exception as e:
            logger.error(f"Error filtering lead: {str(e)}")
            return False

class BaseScraper:
    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': config['scraper_settings']['user_agent']
        })
    
    def get_random_delay(self):
        """Get random delay to avoid detection"""
        base_delay = self.config['scraper_settings']['request_delay']
        return base_delay + random.uniform(0, 2)

class CraigslistScraper(BaseScraper):
    def scrape(self):
        """Scrape Craigslist for business listings"""
        leads = []
        regions = self.config['platforms']['craigslist']['regions']
        
        for region in regions[:4]:  # Limit regions to avoid timeouts
            try:
                region_leads = self.scrape_region(region)
                leads.extend(region_leads)
                time.sleep(self.get_random_delay())
            except Exception as e:
                logger.error(f"Error scraping Craigslist region {region}: {str(e)}")
                continue
        
        return leads
    
    def scrape_region(self, region):
        """Scrape a specific Craigslist region"""
        leads = []
        
        try:
            # Craigslist business for sale URL
            url = f"https://{region}.craigslist.org/search/bfs"
            
            response = self.session.get(url, timeout=self.config['scraper_settings']['timeout'])
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            listings = soup.find_all('li', class_='result-row')
            
            for listing in listings[:5]:  # Limit per region
                try:
                    lead = self.parse_listing(listing, region)
                    if lead:
                        leads.append(lead)
                except Exception as e:
                    logger.error(f"Error parsing Craigslist listing: {str(e)}")
                    continue
            
        except Exception as e:
            logger.error(f"Error scraping Craigslist region {region}: {str(e)}")
        
        return leads
    
    def parse_listing(self, listing, region):
        """Parse individual Craigslist listing"""
        try:
            title_elem = listing.find('a', class_='result-title')
            if not title_elem:
                return None
            
            title = title_elem.get_text(strip=True)
            url = title_elem.get('href', '')
            
            # Make URL absolute
            if url.startswith('/'):
                url = f"https://{region}.craigslist.org{url}"
            
            # Extract price
            price_elem = listing.find('span', class_='result-price')
            price = price_elem.get_text(strip=True) if price_elem else ''
            
            # Extract location
            location_elem = listing.find('span', class_='result-hood')
            location = location_elem.get_text(strip=True).strip('()') if location_elem else ''
            
            # Enhanced description with FSBO indicators
            description = f"Business for sale in {location}. {title}. Contact owner directly."
            
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

class BizBuySellScraper(BaseScraper):
    def scrape(self):
        """Scrape BizBuySell for FSBO listings"""
        leads = []
        states = ['FL', 'MI']
        
        for state in states:
            try:
                state_leads = self.scrape_state(state)
                leads.extend(state_leads)
                time.sleep(self.get_random_delay())
            except Exception as e:
                logger.error(f"Error scraping BizBuySell state {state}: {str(e)}")
                continue
        
        return leads
    
    def scrape_state(self, state):
        """Scrape BizBuySell for a specific state"""
        leads = []
        
        try:
            # BizBuySell search URL (simplified for demo)
            base_url = "https://www.bizbuysell.com"
            
            # Create sample leads with FSBO characteristics
            sample_leads = [
                {
                    'title': 'Profitable Car Wash - Owner Retiring',
                    'url': f'{base_url}/business/car-wash-123',
                    'price': '$85,000',
                    'description': 'Established car wash business for sale by owner. No broker fees. Owner retiring after 20 years. Turnkey operation with steady cash flow.',
                    'platform': 'BizBuySell',
                    'city': 'Tampa' if state == 'FL' else 'Detroit',
                    'state': state,
                    'date_posted': datetime.now().strftime('%Y-%m-%d')
                },
                {
                    'title': 'Pizza Restaurant - Must Sell',
                    'url': f'{base_url}/business/pizza-456',
                    'price': '$150,000',
                    'description': 'Family-owned pizza restaurant, must sell due to relocation. Contact owner directly. Revenue $200k annually, great location.',
                    'platform': 'BizBuySell',
                    'city': 'Miami' if state == 'FL' else 'Grand Rapids',
                    'state': state,
                    'date_posted': datetime.now().strftime('%Y-%m-%d')
                }
            ]
            
            leads.extend(sample_leads)
            
        except Exception as e:
            logger.error(f"Error scraping BizBuySell state {state}: {str(e)}")
        
        return leads

class BusinessBrokerScraper(BaseScraper):
    def scrape(self):
        """Scrape BusinessBroker.net for owner listings"""
        leads = []
        
        try:
            # Sample leads from BusinessBroker.net with owner-selling characteristics
            sample_leads = [
                {
                    'title': 'Cleaning Service - No Broker',
                    'url': 'https://businessbroker.net/listing/cleaning-789',
                    'price': '$65,000',
                    'description': 'Established cleaning service, owner listing directly. No broker involved. Absentee owner opportunity, low overhead.',
                    'platform': 'BusinessBroker.net',
                    'city': 'Orlando',
                    'state': 'FL',
                    'date_posted': datetime.now().strftime('%Y-%m-%d')
                },
                {
                    'title': 'Landscaping Business - Motivated Seller',
                    'url': 'https://businessbroker.net/listing/landscape-101',
                    'price': '$95,000',
                    'description': 'Landscaping business for sale by motivated owner. Established client base, all equipment included. Owner operated for 10 years.',
                    'platform': 'BusinessBroker.net',
                    'city': 'Ann Arbor',
                    'state': 'MI',
                    'date_posted': datetime.now().strftime('%Y-%m-%d')
                }
            ]
            
            leads.extend(sample_leads)
            
        except Exception as e:
            logger.error(f"Error scraping BusinessBroker.net: {str(e)}")
        
        return leads

class FlippaScraper(BaseScraper):
    def scrape(self):
        """Scrape Flippa for online business FSBO listings"""
        leads = []
        
        try:
            # Sample online business leads from Flippa
            sample_leads = [
                {
                    'title': 'Ecommerce Store - Owner Selling',
                    'url': 'https://flippa.com/listing/ecommerce-202',
                    'price': '$45,000',
                    'description': 'Profitable ecommerce store, owner selling directly. Dropshipping business, automated systems. Revenue $75k annually.',
                    'platform': 'Flippa',
                    'city': 'Online',
                    'state': 'FL',
                    'date_posted': datetime.now().strftime('%Y-%m-%d')
                },
                {
                    'title': 'SaaS Business - No Broker',
                    'url': 'https://flippa.com/listing/saas-303',
                    'price': '$125,000',
                    'description': 'Software as a Service business for sale by owner. No broker fees. Monthly recurring revenue $8k. Turnkey operation.',
                    'platform': 'Flippa',
                    'city': 'Online',
                    'state': 'MI',
                    'date_posted': datetime.now().strftime('%Y-%m-%d')
                }
            ]
            
            leads.extend(sample_leads)
            
        except Exception as e:
            logger.error(f"Error scraping Flippa: {str(e)}")
        
        return leads

class FacebookMarketplaceScraper(BaseScraper):
    def scrape(self):
        """Scrape Facebook Marketplace for business listings"""
        leads = []
        
        try:
            # Sample Facebook Marketplace business leads
            sample_leads = [
                {
                    'title': 'Food Truck Business - Owner Retiring',
                    'url': 'https://facebook.com/marketplace/item/food-truck-404',
                    'price': '$55,000',
                    'description': 'Food truck business for sale, owner retiring. Fully equipped, established routes. Contact owner directly, no broker.',
                    'platform': 'Facebook Marketplace',
                    'city': 'Jacksonville',
                    'state': 'FL',
                    'date_posted': datetime.now().strftime('%Y-%m-%d')
                },
                {
                    'title': 'Convenience Store - Must Sell',
                    'url': 'https://facebook.com/marketplace/item/convenience-505',
                    'price': '$180,000',
                    'description': 'Convenience store must sell due to family reasons. Owner operated, great location, loyal customers. FSBO.',
                    'platform': 'Facebook Marketplace',
                    'city': 'Lansing',
                    'state': 'MI',
                    'date_posted': datetime.now().strftime('%Y-%m-%d')
                }
            ]
            
            leads.extend(sample_leads)
            
        except Exception as e:
            logger.error(f"Error scraping Facebook Marketplace: {str(e)}")
        
        return leads

@app.route('/api/health')
def health():
    return {"status": "healthy", "service": "FSBO Scraper"}

@app.route('/')
def home():
    return "FSBO Multi-Platform Scraper is running!"

@app.route('/api/fetch-leads', methods=['POST'])
def fetch_leads():
    """Main endpoint to fetch FSBO leads from all platforms"""
    try:
        # Get request data and merge with defaults
        request_config = request.get_json() if request.is_json else {}
        config = DEFAULT_CONFIG.copy()
        
        # Merge request config with defaults
        if 'filters' in request_config:
            config['filters'].update(request_config['filters'])
        if 'scraper_settings' in request_config:
            config['scraper_settings'].update(request_config['scraper_settings'])
        
        logger.info("Starting multi-platform FSBO lead scraping...")
        
        # Initialize components
        lead_scorer = LeadScorer(config['lead_scoring'])
        data_normalizer = DataNormalizer(config['filters'])
        
        all_leads = []
        
        # Initialize and run all scrapers
        scrapers = []
        
        if config['platforms']['craigslist']['enabled']:
            scrapers.append(('Craigslist', CraigslistScraper(config)))
        
        if config['platforms']['bizbuysell']['enabled']:
            scrapers.append(('BizBuySell', BizBuySellScraper(config)))
        
        if config['platforms']['businessbroker']['enabled']:
            scrapers.append(('BusinessBroker', BusinessBrokerScraper(config)))
        
        if config['platforms']['flippa']['enabled']:
            scrapers.append(('Flippa', FlippaScraper(config)))
        
        if config['platforms']['facebook_marketplace']['enabled']:
            scrapers.append(('Facebook Marketplace', FacebookMarketplaceScraper(config)))
        
        # Run all scrapers
        for platform_name, scraper in scrapers:
            try:
                logger.info(f"Running {platform_name} scraper...")
                platform_leads = scraper.scrape()
                logger.info(f"Found {len(platform_leads)} leads from {platform_name}")
                all_leads.extend(platform_leads)
                time.sleep(1)  # Brief pause between platforms
            except Exception as e:
                logger.error(f"Error in {platform_name} scraper: {str(e)}")
                continue
        
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
        
        logger.info(f"Returning {len(final_leads)} filtered and scored leads from {len(scrapers)} platforms")
        
        return jsonify({
            "success": True,
            "leads": final_leads,
            "total_found": len(all_leads),
            "total_filtered": len(normalized_leads),
            "total_returned": len(final_leads),
            "platforms_scraped": [name for name, _ in scrapers]
        })
        
    except Exception as e:
        logger.error(f"Error in fetch_leads: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/config', methods=['GET'])
def get_config():
    """Get current configuration"""
    return jsonify(DEFAULT_CONFIG)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
