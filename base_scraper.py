import requests
import time
import logging
from abc import ABC, abstractmethod
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re

class BaseScraper(ABC):
    """Base class for all platform scrapers"""
    
    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': config['scraper_settings']['user_agent']
        })
        self.request_delay = config['scraper_settings']['request_delay']
        self.timeout = config['scraper_settings']['timeout']
        self.logger = logging.getLogger(self.__class__.__name__)
        
    def make_request(self, url, **kwargs):
        """Make HTTP request with error handling and rate limiting"""
        try:
            time.sleep(self.request_delay)
            response = self.session.get(url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            self.logger.error(f"Request failed for {url}: {str(e)}")
            return None
    
    def parse_price(self, price_text):
        """Extract numeric price from text"""
        if not price_text:
            return None
        
        # Remove common currency symbols and text
        price_text = re.sub(r'[^\d,.]', '', str(price_text))
        price_text = price_text.replace(',', '')
        
        try:
            return float(price_text)
        except (ValueError, TypeError):
            return None
    
    def extract_contact_info(self, text):
        """Extract email and phone from text"""
        contact_info = {}
        
        if not text:
            return contact_info
        
        # Email regex
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(email_pattern, text)
        if emails:
            contact_info['email'] = emails[0]
        
        # Phone regex (various formats)
        phone_pattern = r'(\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})'
        phones = re.findall(phone_pattern, text)
        if phones:
            contact_info['phone'] = phones[0]
        
        return contact_info
    
    def clean_text(self, text):
        """Clean and normalize text"""
        if not text:
            return ""
        
        # Remove extra whitespace and normalize
        text = re.sub(r'\s+', ' ', str(text).strip())
        return text
    
    def extract_location(self, text):
        """Extract city and state from text"""
        if not text:
            return None, None
        
        # Look for state patterns
        states = self.config['geo_targets']['states']
        state_abbrevs = {'FL': 'Florida', 'MI': 'Michigan'}
        
        found_state = None
        for state in states:
            if state.lower() in text.lower():
                found_state = state
                break
        
        for abbrev, full_name in state_abbrevs.items():
            if abbrev in text:
                found_state = full_name
                break
        
        # Extract city (basic pattern)
        city_pattern = r'([A-Za-z\s]+),?\s*(?:FL|MI|Florida|Michigan)'
        city_match = re.search(city_pattern, text)
        city = city_match.group(1).strip() if city_match else None
        
        return city, found_state
    
    @abstractmethod
    def scrape(self):
        """Main scraping method - must be implemented by subclasses"""
        pass
    
    @abstractmethod
    def parse_listing(self, listing_element, base_url=""):
        """Parse individual listing - must be implemented by subclasses"""
        pass

